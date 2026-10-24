"""Deterministic CubeSat Orbital Multi-Physics Simulation & Verifier.

This module models an autonomous 3U CubeSat in Low Earth Orbit (LEO, ~500 km).
It evaluates the physical outcome of the agent's repairs across an orbital cycle
(95 minutes = 5700 seconds), testing:
  1. Electrical Power Subsystem (EPS): Solar generation, battery SoC, bus voltage, eclipse survival.
  2. Attitude Determination & Control System (ADCS): Detumble, reaction wheel bias, pointing error.
  3. Thermal Control Subsystem (TCS): Radiative cooling, internal dissipation, heater regulation.
  4. Communications Subsystem (COMMS): Friis link budget, EIRP, ground station pass closure.

The verifier executes strictly in the protected harness container — NEVER inside the untrusted sandbox.
"""
import math
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

# ---- 1. Physical Constants & Orbital Parameters -----------------------

ORBIT_PERIOD_S = 5700.0         # ~95 minutes LEO orbit at 500 km
SUNLIGHT_PERIOD_S = 3600.0      # ~60 minutes sunlight
ECLIPSE_PERIOD_S = 2100.0       # ~35 minutes eclipse
SOLAR_FLUX_W_M2 = 1361.0        # AM0 solar irradiance at 1 AU
SOLAR_ARRAY_AREA_M2 = 0.025     # 3U deployable solar array area (~0.025 m2 effective)
BATTERY_CAPACITY_WH = 30.0      # 3S2P Li-ion battery pack (30 Wh nominal)
NOMINAL_BUS_V = 3.7             # 1S equivalent cell voltage nominal
CRITICAL_UNDERVOLTAGE_V = 3.10  # Hardware cutoff voltage (below this = bus collapse)
BASE_LOAD_POWER_W = 4.5         # OBC + ADCS + sensors baseline power draw
HEATER_POWER_W = 6.0            # Battery survival heater power
TX_RF_LOAD_POWER_W = 8.0        # RF transmitter power during pass
SIM_TIMESTEPS = 95              # 1 step = 60 seconds (full 95-minute orbit)
DT_S = 60.0
GROUND_PASS_START_S = 4200.0    # 8-minute ground station pass
GROUND_PASS_DURATION_S = 480.0


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


# ---- 2. Physics Simulation Engine ------------------------------------

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
    initial_soc_pct: float = 60.0,
    initial_temp_c: float = 14.0,
    initial_pointing_error_deg: float = 30.0,
    task_id: str = "",
) -> SimulationResult:
    """Run a deterministic 95-minute orbital physics simulation.
    
    Returns programmatic scoring components:
      - power_balance (0.35): Battery survived eclipse, bus voltage stayed healthy.
      - thermal_stability (0.25): Battery maintained within safe operational bounds [5C, 30C].
      - adcs_precision (0.20): Pointing error eliminated and kept within deadband.
      - comms_closure (0.20): Positive link margin and complete telemetry downlink during ground pass.
    """
    soc_wh = (initial_soc_pct / 100.0) * BATTERY_CAPACITY_WH
    temp_c = initial_temp_c
    pointing_err = initial_pointing_error_deg
    wheel_rpm = wheel_bias_rpm
    heater_on = False
    total_downlinked_kb = 0.0

    frames: List[TelemetryFrame] = []
    min_bus_v = 4.2
    max_temp = -100.0
    min_temp = 100.0
    max_pointing_err = 0.0
    min_link_margin = 999.0
    catastrophic_failure = False
    failure_reason = ""

    for step in range(SIM_TIMESTEPS):
        t = step * DT_S
        in_eclipse = (t % ORBIT_PERIOD_S) >= SUNLIGHT_PERIOD_S
        in_ground_pass = (t >= GROUND_PASS_START_S) and (t < GROUND_PASS_START_S + GROUND_PASS_DURATION_S)

        # ------------------------------------------------------------- A. ADCS
        # Wheel bias causes continuous attitude tumble
        if abs(wheel_rpm) > 20.0:
            pointing_err = min(120.0, pointing_err + abs(wheel_rpm) * 0.04)
        else:
            # Proportional damping towards deadband
            pointing_err = max(pointing_deadband_deg, pointing_err * (1.0 - min(0.35, control_gain * 0.06)))

        # Solar angle: cos(theta)
        cos_sun = max(0.0, math.cos(math.radians(min(88.0, pointing_err))))

        # ------------------------------------------------------------- B. EPS
        if in_eclipse:
            p_gen = 0.0
        else:
            # P_gen = eta * Area * Flux * cos(pointing)
            clamped_eff = max(0.0, min(0.85, solar_efficiency))
            p_gen = clamped_eff * SOLAR_ARRAY_AREA_M2 * SOLAR_FLUX_W_M2 * cos_sun

        # ------------------------------------------------------------- C. TCS
        # Thermostat logic
        if temp_c < (heater_setpoint_c - heater_hysteresis_c) and temp_c < thermal_cutoff_c:
            heater_on = True
        elif temp_c >= heater_setpoint_c or temp_c >= thermal_cutoff_c:
            heater_on = False

        # Load Power Calculation
        p_load = BASE_LOAD_POWER_W
        if heater_on:
            p_load += HEATER_POWER_W
        if in_ground_pass:
            # RF PA load
            p_rf_w = (10.0 ** (tx_power_dbm / 10.0)) / 1000.0
            p_load += TX_RF_LOAD_POWER_W + p_rf_w * 2.0

        # Battery Energy Update
        net_power = p_gen - p_load
        soc_wh = max(0.0, min(BATTERY_CAPACITY_WH, soc_wh + (net_power * (DT_S / 3600.0))))
        soc_pct = (soc_wh / BATTERY_CAPACITY_WH) * 100.0

        # Bus Voltage Model
        bus_v = 3.0 + 1.2 * (soc_pct / 100.0)
        min_bus_v = min(min_bus_v, bus_v)

        # Undervoltage check
        if bus_v < undervoltage_threshold:
            if bus_v <= CRITICAL_UNDERVOLTAGE_V or undervoltage_threshold < 3.0:
                catastrophic_failure = True
                failure_reason = f"Catastrophic bus collapse ({bus_v:.2f}V <= {CRITICAL_UNDERVOLTAGE_V:.2f}V)"
                break

        # Thermal Dynamics
        q_dissipation = p_load * 0.8
        q_solar = (0.0 if in_eclipse else 2.5 * cos_sun)
        q_radiated = 0.09 * (temp_c - (-35.0))
        net_heat = q_dissipation + q_solar - q_radiated
        temp_c += (net_heat * DT_S) / 380.0
        max_temp = max(max_temp, temp_c)
        min_temp = min(min_temp, temp_c)

        if temp_c > 50.0:
            catastrophic_failure = True
            failure_reason = f"Battery thermal runaway ({temp_c:.1f}C > 50C)"
            break
        if temp_c < -15.0:
            catastrophic_failure = True
            failure_reason = f"Battery freezing ({temp_c:.1f}C < -15C)"
            break

        # ------------------------------------------------------------- D. COMMS
        # Directivity loss if pointing error is high during pass
        antenna_pointing_loss = min(20.0, (pointing_err / 10.0) ** 2)
        link_margin = (tx_power_dbm + antenna_gain_dbi + 26.0) - path_loss_db - 3.0 - (-110.0) - 100.0 - antenna_pointing_loss
        if in_ground_pass:
            min_link_margin = min(min_link_margin, link_margin)
            if link_margin >= 3.0 and bus_v >= undervoltage_threshold:
                total_downlinked_kb += 128.0 * (DT_S / 8.0)

        max_pointing_err = max(max_pointing_err, pointing_err)
        frames.append(TelemetryFrame(
            time_s=t,
            eclipse=in_eclipse,
            bus_voltage_v=round(bus_v, 2),
            battery_soc_pct=round(soc_pct, 1),
            battery_temp_c=round(temp_c, 1),
            pointing_error_deg=round(pointing_err, 2),
            link_margin_db=round(link_margin, 1),
            heater_active=heater_on,
            pass_active=in_ground_pass,
            data_downlinked_kb=round(total_downlinked_kb, 1),
        ))

    # If catastrophic failure occurred, reward is zeroed
    if catastrophic_failure:
        return SimulationResult(
            passed=False,
            reward={"power_balance": 0.0, "thermal_stability": 0.0, "adcs_precision": 0.0, "comms_closure": 0.0, "total": 0.0},
            frames=frames,
            failure_reason=failure_reason,
        )

    # Scoring Criteria
    # 1. Power Balance (0.35)
    power_score = 0.0
    if min_bus_v >= undervoltage_threshold and undervoltage_threshold >= 3.15:
        if soc_pct >= 50.0:
            power_score = 0.35
        elif soc_pct >= 35.0:
            power_score = 0.20

    # 2. Thermal Stability (0.25)
    thermal_score = 0.0
    final_temp = frames[-1].battery_temp_c if frames else temp_c
    if min_temp >= 0.0 and max_temp <= 42.0 and final_temp <= 32.0:
        # Prevented runaway and stabilized within nominal operational range
        thermal_score = 0.25
    elif min_temp >= -10.0 and max_temp <= 48.0:
        thermal_score = 0.12

    # 3. ADCS Precision (0.20)
    adcs_score = 0.0
    final_pointing = frames[-1].pointing_error_deg if frames else 90.0
    if final_pointing <= pointing_deadband_deg + 0.1 and abs(wheel_rpm) < 5.0:
        adcs_score = 0.20
    elif final_pointing <= 10.0:
        adcs_score = 0.10

    # 4. Comms Closure (0.20)
    comms_score = 0.0
    if min_link_margin >= 3.0 and total_downlinked_kb >= 4000.0:
        comms_score = 0.20
    elif min_link_margin >= 0.0 and total_downlinked_kb > 1000.0:
        comms_score = 0.10

    # Task-Specific Normalization & Multiplier
    # For targeted single-subsystem tasks, the remaining subsystems are nominally configured,
    # but the targeted subsystem must be solved or the total is penalized to ensure no-op <= 0.05!
    if task_id == "eps_eclipse_recovery":
        # Target is power: if power_score is 0, overall is 0!
        if power_score < 0.30:
            return SimulationResult(False, {"power_balance": 0.0, "thermal_stability": 0.0, "adcs_precision": 0.0, "comms_closure": 0.0, "total": 0.0}, frames, "Power balance unverified")
    elif task_id == "adcs_detumble_pointing":
        if adcs_score < 0.15:
            return SimulationResult(False, {"power_balance": 0.0, "thermal_stability": 0.0, "adcs_precision": 0.0, "comms_closure": 0.0, "total": 0.0}, frames, "ADCS pointing unverified")
    elif task_id == "thermal_battery_preservation":
        if thermal_score < 0.20:
            return SimulationResult(False, {"power_balance": 0.0, "thermal_stability": 0.0, "adcs_precision": 0.0, "comms_closure": 0.0, "total": 0.0}, frames, "Thermal regulation unverified")
    elif task_id == "rf_downlink_closure":
        if comms_score < 0.15:
            return SimulationResult(False, {"power_balance": 0.0, "thermal_stability": 0.0, "adcs_precision": 0.0, "comms_closure": 0.0, "total": 0.0}, frames, "Comms downlink unverified")
    elif task_id == "safe_mode_cascade":
        if power_score < 0.30 or adcs_score < 0.15 or thermal_score < 0.20 or comms_score < 0.15:
            # Multi-fault cascade requires all to hold
            return SimulationResult(False, {"power_balance": power_score, "thermal_stability": thermal_score, "adcs_precision": adcs_score, "comms_closure": comms_score, "total": 0.0}, frames, "Cascade recovery incomplete")

    total_reward = round(power_score + thermal_score + adcs_score + comms_score, 4)
    passed = total_reward >= 0.95

    return SimulationResult(
        passed=passed,
        reward={
            "power_balance": round(power_score, 4),
            "thermal_stability": round(thermal_score, 4),
            "adcs_precision": round(adcs_score, 4),
            "comms_closure": round(comms_score, 4),
            "total": total_reward,
        },
        frames=frames,
        failure_reason="",
    )
