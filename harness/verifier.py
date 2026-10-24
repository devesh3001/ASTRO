"""Deterministic CubeSat Orbital Multi-Physics Simulation & Authoritative State Verifier.

This module models an autonomous 3U CubeSat in Low Earth Orbit (LEO, 500 km, 95-minute period).
It provides a single authoritative episode state tracking:
  1. Electrical Power Subsystem (EPS): Solar generation (GaAs cells), battery Coulombic SoC,
     bus voltage curve, and eclipse survival.
  2. Attitude Determination & Control System (ADCS): Momentum bias, precession rate,
     and proportional attitude damping towards deadband.
  3. Thermal Control Subsystem (TCS): Linearized thermal loss to an effective environment, internal dissipation,
     and thermostat hysteresis regulation.
  4. Communications Subsystem (COMMS): Friis link budget, baud-rate bounded throughput,
     and ground station pass closure.

Unified Episode State Guarantee:
Any irreversible catastrophic violation (e.g. bus collapse <= 3.10V, thermal runaway > 50 C,
freezing < -15 C) is permanently latched for the entire episode and cannot be erased by
modifying configuration files after the fact.

Physical Channel Law Guarantee:
Downlink data throughput is strictly calculated from configured baud rate and packet efficiency;
no 8-minute ground pass can ever downlink more data than the physical channel capacity (<= 489.6 kB at 9600 bps with 85% packet efficiency).
"""
import math
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Any

def clamp(value):
    return max(0.0, min(1.0, value))

# ---- 1. Physical Constants & Orbital Parameters -----------------------

ORBIT_PERIOD_S = 5700.0         # 95 minutes LEO orbit at 500 km
SUNLIGHT_PERIOD_S = 3600.0      # 60 minutes sunlight
ECLIPSE_PERIOD_S = 2100.0       # 35 minutes eclipse
SOLAR_FLUX_W_M2 = 1361.0        # AM0 solar irradiance at 1 AU
SOLAR_ARRAY_AREA_M2 = 0.06      # 3U deployable solar array area (0.06 m2)
CELL_CONVERSION_EFF = 0.28      # Triple-junction GaAs cell nominal efficiency (28%)
BATTERY_CAPACITY_WH = 40.0      # 3S2P Li-ion battery pack (40.0 Wh nominal)
NOMINAL_BUS_V = 3.7             # 1S equivalent cell voltage nominal
CRITICAL_UNDERVOLTAGE_V = 3.10  # Hardware cutoff voltage (below this = irreversible bus collapse)
BASE_LOAD_POWER_W = 4.5         # OBC + ADCS + sensors baseline power draw
HEATER_POWER_W = 6.0            # Battery survival heater power
TX_RF_LOAD_POWER_W = 8.0        # RF transmitter power during pass
DT_DEFAULT_S = 60.0
GROUND_PASS_START_S = 4200.0    # 8-minute ground station pass
GROUND_PASS_DURATION_S = 480.0

# RF Link Budget Physical Constants (UHF 437.5 MHz at 500 km LEO slant range)
GROUND_STATION_RX_GAIN_DBI = 16.0   # UHF crossed-Yagi ground station array
SYSTEM_LOSSES_DB = 2.5              # Atmospheric, polarization, and cable insertion losses
GROUND_RX_SENSITIVITY_DBM = -100.0  # 9600 bps GFSK demodulator sensitivity threshold
PACKET_EFFICIENCY = 0.85            # 85% data payload after AX.25/CCSDS sync, header, CRC, FEC


@dataclass
class TelemetryFrame:
    time_s: float
    eclipse: bool
    bus_voltage_v: float
    battery_soc_pct: float
    battery_temp_c: float
    pointing_error_deg: float
    link_margin_db: float
    heater_active: bool
    pass_active: bool
    data_downlinked_kb: float


@dataclass
class SimulationResult:
    passed: bool
    reward: Dict[str, float]
    frames: List[TelemetryFrame] = field(default_factory=list)
    failure_reason: str = ""
    checks: Dict[str, bool] = field(default_factory=dict)


# ---- 2. Physical Bounds Validation ------------------------------------

PHYSICAL_BOUNDS = {
    "solar_efficiency": (0.0, 1.0),
    "undervoltage_threshold": (2.4, 4.2),
    "battery_capacity_wh": (30.0, 50.0),
    "wheel_bias_rpm": (-5000.0, 5000.0),
    "pointing_deadband_deg": (0.01, 15.0),
    "control_gain": (0.01, 20.0),
    "battery_heater_setpoint": (-30.0, 85.0),
    "heater_hysteresis_c": (0.2, 15.0),
    "thermal_cutoff_temp": (15.0, 85.0),
    "tx_power_dbm": (-10.0, 36.0),
    "antenna_gain_dbi": (-10.0, 25.0),
    "path_loss_db": (100.0, 200.0),
    "frequency_mhz": (400.0, 450.0),
    "baudrate_bps": (1200.0, 38400.0),
}


def validate_subsystem_configs(configs: Dict[str, Any]) -> Tuple[bool, str]:
    """Strict physical bounds checking. Rejects NaN, Inf, and impossible engineering parameters."""
    for key, (low, high) in PHYSICAL_BOUNDS.items():
        if key in configs:
            val = configs[key]
            if not isinstance(val, (int, float)):
                return False, f"Non-numerical parameter for {key}: {val}"
            if math.isnan(val) or math.isinf(val):
                return False, f"Invalid NaN/Inf numerical value for {key}"
            if val < low or val > high:
                return False, f"Parameter {key}={val} outside physical limits [{low}, {high}]"
    return True, ""


def link_margin_db(configs, pointing_error_deg=0.0):
    """Bandwidth-aware RF margin; the script reports the zero-pointing-loss value."""
    return (configs.get("tx_power_dbm", 20.0) + configs.get("antenna_gain_dbi", 2.0)
            + GROUND_STATION_RX_GAIN_DBI - configs.get("path_loss_db", 141.5)
            - SYSTEM_LOSSES_DB - GROUND_RX_SENSITIVITY_DBM
            - 10 * math.log10(configs.get("baudrate_bps", 9600.0) / 9600.0)
            - min(20.0, (pointing_error_deg / 10.0) ** 2))


# ---- 3. Authoritative Episode Multi-Physics Engine -------------------

class AuthoritativeOrbitSimulator:
    """The authoritative physical ground truth for a satellite episode.
    
    Persists continuous virtual time, battery energy, thermal equilibrium, attitude,
    and downlink data across the entire episode. Catastrophic events are permanently latched.
    """

    def __init__(
        self,
        initial_soc_pct: float = 70.0,
        initial_temp_c: float = 14.0,
        initial_pointing_error_deg: float = 35.0,
        task_id: str = "",
        mission: dict = None,
    ):
        self.task_id = task_id
        self.mission = dict(mission or {})
        self.time_s: float = 0.0
        self.soc_wh: float = max(0.0, min(BATTERY_CAPACITY_WH, (initial_soc_pct / 100.0) * BATTERY_CAPACITY_WH))
        self.temp_c: float = initial_temp_c
        self.pointing_err_deg: float = initial_pointing_error_deg
        self.wheel_bias_rpm: float = 0.0
        self.heater_on: bool = False
        self.total_downlinked_kb: float = 0.0
        self.tx_energy_wh: float = 0.0

        # Trajectory extremes
        self.min_bus_v: float = 3.0 + 1.2 * (self.soc_wh / BATTERY_CAPACITY_WH)
        self.max_temp_c: float = initial_temp_c
        self.min_temp_c: float = initial_temp_c
        self.max_pointing_err_deg: float = initial_pointing_error_deg
        self.min_link_margin_db: float = 999.0

        # Irreversible safety event tracking
        self.catastrophic_failure: bool = False
        self.failure_reason: str = ""
        self.frames: List[TelemetryFrame] = []

    def _validate(self, configs):
        valid, reason = validate_subsystem_configs(configs)
        for key in ("path_loss_db", "antenna_gain_dbi", "battery_capacity_wh", "frequency_mhz"):
            if key in self.mission and configs.get(key) != self.mission[key]:
                return False, f"Protected hardware parameter changed: {key}"
        allowed = self.mission.get("allowed_baudrates_bps")
        if allowed and configs.get("baudrate_bps", 9600.0) not in allowed:
            return False, "Baud rate is not supported by the protected radio profile"
        return valid, reason

    def advance(self, duration_s: float, configs: Dict[str, Any], dt_s: float = 60.0) -> List[TelemetryFrame]:
        """Advance authoritative orbit simulation by duration_s using current configs."""
        if duration_s <= 0:
            return []

        # Validate physical limits
        valid, reason = self._validate(configs)
        if not valid and not self.catastrophic_failure:
            self.catastrophic_failure = True
            self.failure_reason = f"Rejected unphysical configuration: {reason}"
        if not valid:
            return []

        solar_eff = configs.get("solar_efficiency", 0.40)
        uv_thresh = configs.get("undervoltage_threshold", 2.80)
        wheel_bias = configs.get("wheel_bias_rpm", 120.0)
        deadband = configs.get("pointing_deadband_deg", 0.50)
        gain = configs.get("control_gain", 1.0)
        heater_sp = configs.get("battery_heater_setpoint", 65.0)
        heater_hyst = configs.get("heater_hysteresis_c", 2.0)
        thermal_cut = configs.get("thermal_cutoff_temp", 35.0)
        tx_power = configs.get("tx_power_dbm", 20.0)
        baudrate = configs.get("baudrate_bps", 9600.0)

        # Physical RF channel byte capacity per second: R_eff = (baudrate / 8) * packet_efficiency
        effective_kb_per_sec = ((baudrate / 8.0) * PACKET_EFFICIENCY) / 1000.0

        pass_start = self.mission.get("ground_pass_start_s", GROUND_PASS_START_S)
        pass_end = pass_start + self.mission.get("ground_pass_duration_s", GROUND_PASS_DURATION_S)
        end_s = min(ORBIT_PERIOD_S, self.time_s + duration_s)
        new_frames = []

        while self.time_s < end_s - 1e-9:
            t = self.time_s
            # Split at physical events, so partial tool steps cannot create extra contact time.
            boundaries = [b for b in (SUNLIGHT_PERIOD_S, pass_start, pass_end, end_s) if b > t + 1e-9]
            actual_dt = min(dt_s, min(boundaries) - t)
            in_eclipse = t >= SUNLIGHT_PERIOD_S
            in_ground_pass = pass_start <= t < pass_end

            # --------------------------------------------------------- 1. ADCS
            if abs(wheel_bias) > 20.0:
                self.pointing_err_deg = min(120.0, self.pointing_err_deg + abs(wheel_bias) * 0.04 * (actual_dt / 60.0))
            else:
                damping = min(0.35, gain * 0.06) * (actual_dt / 60.0)
                self.pointing_err_deg = max(deadband, self.pointing_err_deg * (1.0 - damping))

            self.max_pointing_err_deg = max(self.max_pointing_err_deg, self.pointing_err_deg)
            cos_sun = max(0.0, math.cos(math.radians(min(88.0, self.pointing_err_deg))))

            # --------------------------------------------------------- 2. EPS
            if in_eclipse:
                p_gen = 0.0
            else:
                clamped_eff = max(0.0, min(0.85, solar_eff))
                p_gen = clamped_eff * CELL_CONVERSION_EFF * SOLAR_ARRAY_AREA_M2 * SOLAR_FLUX_W_M2 * cos_sun

            # --------------------------------------------------------- 3. TCS
            if self.temp_c < (heater_sp - heater_hyst) and self.temp_c < thermal_cut:
                self.heater_on = True
            elif self.temp_c >= heater_sp or self.temp_c >= thermal_cut:
                self.heater_on = False

            p_load = BASE_LOAD_POWER_W
            if self.heater_on:
                p_load += HEATER_POWER_W
            if in_ground_pass:
                p_rf_w = (10.0 ** (tx_power / 10.0)) / 1000.0
                tx_load = TX_RF_LOAD_POWER_W + p_rf_w * 2.0
                p_load += tx_load
                self.tx_energy_wh += tx_load * actual_dt / 3600.0

            # Battery Energy Update
            net_power = p_gen - p_load
            self.soc_wh = max(0.0, min(BATTERY_CAPACITY_WH, self.soc_wh + (net_power * (actual_dt / 3600.0))))
            soc_pct = (self.soc_wh / BATTERY_CAPACITY_WH) * 100.0
            bus_v = 3.0 + 1.2 * (soc_pct / 100.0)
            self.min_bus_v = min(self.min_bus_v, bus_v)

            # Undervoltage Check
            if bus_v <= CRITICAL_UNDERVOLTAGE_V and not self.catastrophic_failure:
                self.catastrophic_failure = True
                self.failure_reason = f"Catastrophic bus collapse ({bus_v:.2f}V <= {CRITICAL_UNDERVOLTAGE_V:.2f}V)"

            # Thermal Dynamics
            q_dissipation = p_load * 0.8
            q_solar = 0.0 if in_eclipse else 2.5 * cos_sun
            q_radiated = 0.09 * (self.temp_c - (-35.0))
            net_heat = q_dissipation + q_solar - q_radiated
            self.temp_c += (net_heat * actual_dt) / 380.0
            self.max_temp_c = max(self.max_temp_c, self.temp_c)
            self.min_temp_c = min(self.min_temp_c, self.temp_c)

            if not self.catastrophic_failure:
                if self.temp_c > 50.0:
                    self.catastrophic_failure = True
                    self.failure_reason = f"Battery thermal runaway ({self.temp_c:.1f}C > 50C)"
                elif self.temp_c < -15.0:
                    self.catastrophic_failure = True
                    self.failure_reason = f"Battery freezing ({self.temp_c:.1f}C < -15C)"
                elif self.pointing_err_deg >= 120.0:
                    self.catastrophic_failure = True
                    self.failure_reason = f"Irreversible attitude tumble (pointing error {self.pointing_err_deg:.1f} deg)"

            # --------------------------------------------------------- 4. COMMS
            link_margin = link_margin_db(configs, self.pointing_err_deg)

            if in_ground_pass:
                self.min_link_margin_db = min(self.min_link_margin_db, link_margin)
                if link_margin >= 3.0 and bus_v >= uv_thresh:
                    # Full data throughput bounded by baud rate
                    self.total_downlinked_kb += effective_kb_per_sec * actual_dt
                elif link_margin >= 0.0 and bus_v >= uv_thresh:
                    # Marginal link: 50% packet error drop
                    self.total_downlinked_kb += (effective_kb_per_sec * 0.5) * actual_dt

            self.time_s += actual_dt
            frame = TelemetryFrame(
                time_s=round(self.time_s, 1),
                eclipse=in_eclipse,
                bus_voltage_v=round(bus_v, 2),
                battery_soc_pct=round(soc_pct, 1),
                battery_temp_c=round(self.temp_c, 1),
                pointing_error_deg=round(self.pointing_err_deg, 2),
                link_margin_db=round(link_margin, 1),
                heater_active=self.heater_on,
                pass_active=in_ground_pass,
                data_downlinked_kb=round(self.total_downlinked_kb, 1),
            )
            self.frames.append(frame)
            new_frames.append(frame)

        return new_frames

    def finalize_orbit(self, configs: Dict[str, Any]) -> SimulationResult:
        """Simulate through the remainder of the 95-minute orbit and score the authoritative trajectory."""
        if self.time_s < ORBIT_PERIOD_S - 1e-6:
            remaining_s = ORBIT_PERIOD_S - self.time_s
            self.advance(remaining_s, configs, dt_s=DT_DEFAULT_S)
        self.time_s = ORBIT_PERIOD_S

        # Validate even when no simulation time remains. Hardware facts are immutable.
        valid, reason = self._validate(configs)
        if not valid:
            return SimulationResult(False, {"power_balance": 0.0, "thermal_stability": 0.0,
                "adcs_precision": 0.0, "comms_closure": 0.0, "total": 0.0}, self.frames, reason)

        # If an irreversible catastrophic failure occurred at ANY point in the episode, reward = 0.0
        if self.catastrophic_failure:
            return SimulationResult(
                passed=False,
                reward={"power_balance": 0.0, "thermal_stability": 0.0, "adcs_precision": 0.0, "comms_closure": 0.0, "total": 0.0},
                frames=self.frames,
                failure_reason=self.failure_reason,
            )

        # ------------------------------------------------------------- Scoring Criteria
        uv_thresh = configs.get("undervoltage_threshold", 2.80)
        wheel_bias = configs.get("wheel_bias_rpm", 120.0)
        deadband = configs.get("pointing_deadband_deg", 0.50)
        final_soc_pct = (self.soc_wh / BATTERY_CAPACITY_WH) * 100.0

        # Mission targets are protected; agent tolerances cannot redefine success.
        target_soc = self.mission.get("min_final_soc_pct", 85.0 if self.task_id == "eps_eclipse_recovery" else 50.0)
        power_score = 0.0
        if self.min_bus_v >= uv_thresh and uv_thresh >= 3.15:
            floor_soc = 75.0 if self.task_id == "eps_eclipse_recovery" else 35.0
            power_score = 0.35 * clamp((final_soc_pct - floor_soc) / (target_soc - floor_soc))

        thermal_score = 0.0
        if self.min_temp_c >= 0.0:
            thermal_score = 0.25 * clamp((48.0 - max(self.max_temp_c, self.temp_c + 10.0)) / 6.0)
        elif self.min_temp_c >= -10.0 and self.max_temp_c <= 48.0:
            thermal_score = 0.12

        target_pointing = self.mission.get("required_pointing_deg", 0.15)
        adcs_score = 0.0
        if self.pointing_err_deg <= target_pointing and abs(wheel_bias) < 5.0:
            adcs_score = 0.20
        elif self.pointing_err_deg <= 10.0:
            adcs_score = 0.10 * clamp((10.0 - self.pointing_err_deg) / (10.0 - target_pointing))

        # Communications credit is bounded by physical payload delivery, not script output.
        # Verify script correctness independently of accumulated communication credit (Item 1)
        script_verified = configs.get("script_verified", True)
        comms_score = 0.0
        target_data = self.mission.get("required_downlink_kb", 350.0)
        if not script_verified:
            comms_score = 0.0
            self.total_downlinked_kb = 0.0
        elif self.min_link_margin_db >= 3.0 and self.total_downlinked_kb >= target_data:
            comms_score = 0.20
        elif self.min_link_margin_db >= 0.0 and self.total_downlinked_kb >= min(100.0, target_data):
            comms_score = 0.10

        energy_ok = self.tx_energy_wh <= self.mission.get("max_tx_energy_wh", float("inf")) + 1e-9
        if not energy_ok:
            power_score = 0.0
        checks = {
            "battery_reserve": final_soc_pct >= target_soc and self.min_bus_v >= uv_thresh and uv_thresh >= 3.15,
            "thermal_limits": (self.min_temp_c >= self.mission.get("min_battery_temp_c", 0)
                               and self.max_temp_c <= self.mission.get("max_battery_temp_c", 42)
                               and self.temp_c <= self.mission.get("max_final_temp_c", 32)),
            "pointing_target": self.pointing_err_deg <= target_pointing and abs(wheel_bias) < 5,
            "payload_target": self.total_downlinked_kb >= target_data,
            "contact_link_margin": self.min_link_margin_db >= 3,
            "link_script_behavior": bool(script_verified),
            "rf_energy_allocation": energy_ok,
        }
        total_reward = round(power_score + thermal_score + adcs_score + comms_score, 4)
        passed = total_reward >= 0.95 and all(checks.values())

        return SimulationResult(
            passed=passed,
            reward={
                "power_balance": round(power_score, 4),
                "thermal_stability": round(thermal_score, 4),
                "adcs_precision": round(adcs_score, 4),
                "comms_closure": round(comms_score, 4),
                "total": total_reward,
            },
            frames=self.frames,
            failure_reason="; ".join(name for name, ok in checks.items() if not ok),
            checks=checks,
        )


# Backward-compatible function wrapper for unit testing
def simulate_orbit(
    solar_efficiency: float,
    undervoltage_threshold: float,
    wheel_bias_rpm: float,
    pointing_deadband_deg: float,
    control_gain: float,
    heater_setpoint_c: float,
    heater_hysteresis_c: float,
    thermal_cutoff_c: float,
    tx_power_dbm: float,
    antenna_gain_dbi: float,
    path_loss_db: float,
    initial_soc_pct: float = 70.0,
    initial_temp_c: float = 14.0,
    initial_pointing_error_deg: float = 30.0,
    task_id: str = "",
    script_verified: bool = True,
) -> SimulationResult:
    configs = {
        "solar_efficiency": solar_efficiency,
        "undervoltage_threshold": undervoltage_threshold,
        "wheel_bias_rpm": wheel_bias_rpm,
        "pointing_deadband_deg": pointing_deadband_deg,
        "control_gain": control_gain,
        "battery_heater_setpoint": heater_setpoint_c,
        "heater_hysteresis_c": heater_hysteresis_c,
        "thermal_cutoff_temp": thermal_cutoff_c,
        "tx_power_dbm": tx_power_dbm,
        "antenna_gain_dbi": antenna_gain_dbi,
        "path_loss_db": path_loss_db,
        "script_verified": script_verified,
    }
    sim = AuthoritativeOrbitSimulator(
        initial_soc_pct=initial_soc_pct,
        initial_temp_c=initial_temp_c,
        initial_pointing_error_deg=initial_pointing_error_deg,
        task_id=task_id,
    )
    return sim.finalize_orbit(configs)
