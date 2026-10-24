"""Satellite fault-response tasks for AeroSentinel with realistic physics grounding.

Key improvements:
- Zero answer leakage: Task descriptions report symptoms, alarms, and flight telemetry,
  NOT the solution parameter values.
- Engineering manual provided: `docs/flight_manual.md` details nominal operational
  bounds, equations, and diagnostic guidelines.
- Dynamic seed variety: Seeds alter orbital initial conditions, telemetry noise,
  and fault profiles while maintaining guaranteed solvability for reference solutions.
- Five tasks across two difficulty levels:
  * easy   : eps_eclipse_recovery, adcs_detumble_pointing
  * medium : thermal_battery_preservation, rf_downlink_closure, safe_mode_cascade
"""
import random
from dataclasses import dataclass
from typing import Dict, List


# ---- 1. Data Containers ----------------------------------------------

@dataclass
class TaskInstance:
    task_id: str
    title: str
    difficulty: str
    instructions: str
    files: Dict[str, str]       # workspace files provided to the agent
    reference: Dict[str, str]   # corrected files that achieve full mission success
    initial_soc_pct: float = 75.0
    initial_temp_c: float = 14.0
    initial_pointing_error_deg: float = 35.0


# ---- 2. CubeSat Flight Manual (docs/flight_manual.md) ------------------

FLIGHT_MANUAL_MD = """\
# AEROSENTINEL-3U CUBESAT FLIGHT SOFTWARE MANUAL
**Document Ref:** AS3U-FSW-OPS-v2.5
**Orbit Specification:** 500 km Circular LEO, Period = 95 min (60 min sun, 35 min eclipse)

---

## 1. Electrical Power Subsystem (EPS)
- **Solar Array:** Triple-junction GaAs deployable panels (Total Area = 0.06 m², nominal cell efficiency = 28%).
  - Maximum Power Point Tracking (MPPT) software calibration: `solar_efficiency = 0.72` (Nominal operational band: 0.70 – 0.75).
  - Software calibration below 0.50 causes acute solar power deficit, leading to battery brownout during eclipse.
  - Solar generation formula: `P_gen = solar_efficiency * 0.28 * 0.06 * 1361.0 * cos(theta_sun)` (Watts).
- **Battery Pack:** 3S2P Li-ion (Nominal voltage 3.7V equivalent, 40.0 Wh capacity).
  - Hardware critical collapse floor: 3.10 V.
  - Required software undervoltage cutoff: `undervoltage_threshold = 3.20` V. Setting this below 3.00 V risks unrecoverable bus collapse during peak eclipse load.
  - Maximum safe charge current: `max_charge_rate_a = 1.2` A.

## 2. Attitude Determination & Control System (ADCS)
- **Actuators:** 3-axis reaction wheel assembly + magnetic torquers.
  - Nominal steady-state wheel bias: `wheel_bias_rpm = 0` (Residual momentum bias causes continuous attitude precession).
  - Fine pointing deadband: `pointing_deadband_deg = 0.05` degrees.
  - Control loop proportional gain: `control_gain = 2.0` (Gains below 1.0 result in underdamped sluggish pointing).
  - Nominal 3-axis pointing requirement: <= 5.0 degrees off-sun during generation phase.

## 3. Thermal Control Subsystem (TCS)
- **Operational Temperature Limits:**
  - Li-ion battery pack safe operating range: 0°C to +35°C.
  - Critical thermal runaway threshold: > 50°C (irreversible cell venting/degradation).
  - Freezing floor: < -15°C (electrolyte freezing).
- **Thermostat Parameters:**
  - Nominal battery heater setpoint: `battery_heater_setpoint = 15.0` °C.
  - Thermostat hysteresis: `heater_hysteresis_c = 2.0` °C (Turns ON at setpoint - hysteresis, OFF at setpoint).
  - Thermal safety upper cutoff: `thermal_cutoff_temp = 45.0` °C (Emergency override to prevent runaway).

## 4. Communications Subsystem (COMMS)
- **Transceiver:** UHF direct-to-ground downlink at 437.5 MHz (500 km LEO orbit).
  - Nominal RF output power: `tx_power_dbm = 30.0` dBm (1.0 Watt). Operational band: 28.0 to 32.0 dBm.
  - High-gain patch antenna: `antenna_gain_dbi = 8.0` dBi. Operational band: 6.0 to 10.0 dBi.
  - 500 km LEO slant-range Free Space Path Loss (FSPL): `path_loss_db = 141.5` dB.
  - Ground Station Parameters: Receiver Gain `G_rx = 16.0` dBi, System Losses `L_sys = 2.5` dB, Demodulator Sensitivity `P_sens = -100.0` dBm.
  - Friis Link Margin Equation:
    `Margin (dB) = (tx_power_dbm + antenna_gain_dbi + 16.0) - path_loss_db - 2.5 - (-100.0)`
  - Required operational link margin for telemetry packet lock: >= 3.0 dB.
  - Link budget scripts must dynamically query `path_loss_db` from `subsystems/comms.cfg`.

---

## 5. Multi-Subsystem Telemetry Diagnostics & Fault Signatures
When diagnosing anomalies from `telemetry/live_telemetry.csv` and `telemetry/current_status.json`, cross-correlate telemetry columns before modifying configurations:

1. **Low Bus Voltage / Rapid Eclipse Drain:**
   - *Check ADCS first:* If `pointing_err_deg` > 20°, off-sun tumble is causing panel cosine illumination loss, starving generation. Neutralize reaction wheel momentum bias (`wheel_bias_rpm = 0`) and tighten deadband.
   - *Check EPS MPPT:* If pointing is nominal (<= 5°) but `bus_v` steadily degrades during sunlight, MPPT `solar_efficiency` has drifted below nominal (0.70 - 0.75).
   - *Check TCS Heater Load:* If `heater = ON` continuously even when core temp > 20°C, stuck heater consumes 2.5W baseline load, accelerating battery depletion.

2. **Battery Temperature Elevation / Thermal Warning:**
   - Inspect `battery_heater_setpoint` vs `live_telemetry.csv` temp trend. If setpoint exceeds 30°C, the thermostat will drive cells toward the 50°C catastrophic runaway limit. Reconfigure setpoint to 15.0°C and cutoff to 45.0°C.

3. **Loss of Telemetry / Blackout During Pass:**
   - Verify link margin in `telemetry/current_status.json`. If negative or below 3.0 dB, check transceiver TX power (must be >= 28.0 dBm) and run `scripts/link_budget.py` to ensure it dynamically queries `path_loss_db` rather than using stale bench-test constants.
"""


# ---- 3. Nominal Subsystem Configurations ------------------------------

_EPS_CFG_NOMINAL = """\
[power]
solar_efficiency = 0.72
battery_capacity_wh = 40.0
undervoltage_threshold = 3.20
max_charge_rate_a = 1.2
bus_voltage_nominal = 3.7
"""

_ADCS_CFG_NOMINAL = """\
[attitude]
wheel_bias_rpm = 0
pointing_deadband_deg = 0.05
control_gain = 2.0
max_torque_nm = 0.002
control_frequency_hz = 10
"""

_THERMAL_CFG_NOMINAL = """\
[thermal]
battery_heater_setpoint = 15.0
thermal_cutoff_temp = 45.0
heater_hysteresis_c = 2.0
sensor_poll_interval_s = 30
"""

_COMMS_CFG_NOMINAL = """\
[comms]
tx_power_dbm = 30.0
antenna_gain_dbi = 8.0
path_loss_db = 141.5
frequency_mhz = 437.5
baudrate_bps = 9600
"""

_LINK_BUDGET_SCRIPT_FIXED = '''\
"""Dynamic Link Budget Calculator for AeroSentinel UHF/S-band."""
import configparser
from pathlib import Path


def calc_margin():
    """Compute RF link margin using configured transceiver parameters."""
    cfg = configparser.ConfigParser()
    path = Path("subsystems/comms.cfg")
    if not path.is_file():
        path = Path("comms.cfg")
    cfg.read(str(path))

    tx_power = float(cfg['comms']['tx_power_dbm'])
    gain = float(cfg['comms']['antenna_gain_dbi'])
    path_loss = float(cfg['comms']['path_loss_db'])

    # Friis Link Equation: Margin (dB) = P_tx + G_tx + G_rx - Path_Loss - L_sys - P_sens
    # Ground Station: G_rx = 16.0 dBi, P_sens = -100.0 dBm, L_sys = 2.5 dB
    margin = (tx_power + gain + 16.0) - path_loss - 2.5 - (-100.0)
    return round(margin, 2)


if __name__ == "__main__":
    margin = calc_margin()
    print(f"Computed Link Margin: {margin:.2f} dB (Nominal required >= 3.0 dB)")
'''

_LINK_BUDGET_SCRIPT_BUGGY = '''\
"""Dynamic Link Budget Calculator for AeroSentinel UHF/S-band."""
import configparser
from pathlib import Path


def calc_margin():
    """Compute RF link margin using configured transceiver parameters."""
    cfg = configparser.ConfigParser()
    path = Path("subsystems/comms.cfg")
    if not path.is_file():
        path = Path("comms.cfg")
    cfg.read(str(path))

    tx_power = float(cfg['comms']['tx_power_dbm'])
    gain = float(cfg['comms']['antenna_gain_dbi'])
    # BUG: hardcoded bench test value instead of querying config
    path_loss = 120.0
    margin = (tx_power + gain + 16.0) - path_loss - 2.5 - (-100.0)
    return round(margin, 2)


if __name__ == "__main__":
    margin = calc_margin()
    print(f"Computed Link Margin: {margin:.2f} dB (Nominal required >= 3.0 dB)")
'''


# ---- 4. Task 1: EPS Eclipse Recovery (Easy) ---------------------------

def eps_eclipse_recovery_task(seed: int) -> TaskInstance:
    rng = random.Random(seed)
    noise_soc = round(rng.uniform(-2.0, 2.0), 1)

    buggy_eps = """\
[power]
solar_efficiency = 0.41
battery_capacity_wh = 40.0
undervoltage_threshold = 2.80
max_charge_rate_a = 1.2
bus_voltage_nominal = 3.7
"""
    telemetry_csv = f"""\
timestamp,orbit_phase,bus_voltage_v,battery_soc_pct,array_current_a
2026-10-24T00:10:00Z,SUNLIGHT,3.68,{68.0 + noise_soc},0.48
2026-10-24T00:30:00Z,SUNLIGHT,3.72,{72.0 + noise_soc},0.51
2026-10-24T00:50:00Z,SUNLIGHT,3.74,{74.5 + noise_soc},0.50
2026-10-24T01:05:00Z,ECLIPSE_ENTRY,3.52,{65.0 + noise_soc},0.00
2026-10-24T01:20:00Z,ECLIPSE_DEEP,3.22,{42.0 + noise_soc},0.00
2026-10-24T01:30:00Z,ECLIPSE_EXIT,2.98,{18.0 + noise_soc},0.00
2026-10-24T01:32:00Z,FAULT_COLLAPSE,2.82,8.0,0.00
"""

    instructions = (
        "ANOMALY WARNING [EPS-PWR-302]: Satellite telemetry indicates battery depletion during "
        "the 35-minute orbital eclipse, triggering bus voltage collapse below 3.0V. "
        "Review `docs/flight_manual.md` for nominal solar array efficiency and safe undervoltage limits. "
        "Inspect `telemetry/eps_orbit_log.csv` and reconfigure `subsystems/eps.cfg` so the satellite "
        "retains sufficient power to survive eclipse transitions without voltage brownout."
    )

    files = {
        "docs/flight_manual.md": FLIGHT_MANUAL_MD,
        "subsystems/eps.cfg": buggy_eps,
        "subsystems/adcs.cfg": _ADCS_CFG_NOMINAL,
        "subsystems/thermal.cfg": _THERMAL_CFG_NOMINAL,
        "subsystems/comms.cfg": _COMMS_CFG_NOMINAL,
        "telemetry/eps_orbit_log.csv": telemetry_csv,
    }

    reference = {
        "subsystems/eps.cfg": _EPS_CFG_NOMINAL,
    }

    return TaskInstance(
        task_id="eps_eclipse_recovery",
        title="EPS Solar Degradation & Eclipse Brownout",
        difficulty="easy",
        instructions=instructions,
        files=files,
        reference=reference,
        initial_soc_pct=70.0,
        initial_temp_c=12.0,
        initial_pointing_error_deg=5.0,
    )


# ---- 5. Task 2: ADCS Detumble & Sun-Pointing (Easy) -------------------

def adcs_detumble_pointing_task(seed: int) -> TaskInstance:
    rng = random.Random(seed)
    bias_val = rng.choice([120, 140, 160])

    buggy_adcs = f"""\
[attitude]
wheel_bias_rpm = {bias_val}
pointing_deadband_deg = 0.50
control_gain = 0.40
max_torque_nm = 0.002
control_frequency_hz = 10
"""
    adcs_log = """\
timestamp,pointing_error_deg,wheel_rpm_x,wheel_rpm_y,wheel_rpm_z,rate_deg_s
2026-10-24T02:00:00Z,42.5,138.2,1.2,-0.5,3.2
2026-10-24T02:15:00Z,46.8,139.5,0.8,-0.9,3.1
2026-10-24T02:30:00Z,51.2,141.0,1.5,-0.4,3.4
2026-10-24T02:45:00Z,55.0,138.8,1.1,-0.7,3.0
"""

    instructions = (
        "ANOMALY WARNING [ADCS-ATT-108]: Attitude determination detects severe continuous pointing "
        "precession. The satellite is tumbling at >3 deg/s and cannot stabilize solar array sun-pointing. "
        "Inspect `telemetry/adcs_orbit_log.csv` and consult `docs/flight_manual.md` for reaction wheel "
        "bias and control loop settings. Update `subsystems/adcs.cfg` to eliminate momentum bias and "
        "achieve steady 3-axis sun-pointing within nominal deadband."
    )

    files = {
        "docs/flight_manual.md": FLIGHT_MANUAL_MD,
        "subsystems/eps.cfg": _EPS_CFG_NOMINAL,
        "subsystems/adcs.cfg": buggy_adcs,
        "subsystems/thermal.cfg": _THERMAL_CFG_NOMINAL,
        "subsystems/comms.cfg": _COMMS_CFG_NOMINAL,
        "telemetry/adcs_orbit_log.csv": adcs_log,
    }

    reference = {
        "subsystems/adcs.cfg": _ADCS_CFG_NOMINAL,
    }

    return TaskInstance(
        task_id="adcs_detumble_pointing",
        title="ADCS Momentum Bias & Detumble",
        difficulty="easy",
        instructions=instructions,
        files=files,
        reference=reference,
        initial_soc_pct=80.0,
        initial_temp_c=12.0,
        initial_pointing_error_deg=45.0,
    )


# ---- 6. Task 3: Thermal Battery Preservation (Medium) -----------------

def thermal_battery_preservation_task(seed: int) -> TaskInstance:
    rng = random.Random(seed)
    sp_val = rng.choice([55.0, 60.0, 65.0])
    noise_temp = round(rng.uniform(-1.5, 1.5), 1)

    buggy_thermal = f"""\
[thermal]
battery_heater_setpoint = {sp_val}
thermal_cutoff_temp = 35.0
heater_hysteresis_c = 2.0
sensor_poll_interval_s = 30
"""
    thermal_log = f"""\
timestamp,zone,temp_c,heater_status,bus_current_a
2026-10-24T04:00:00Z,battery_bay,{34.2 + noise_temp:.1f},ACTIVE,1.8
2026-10-24T04:15:00Z,battery_bay,{39.8 + noise_temp:.1f},ACTIVE,1.8
2026-10-24T04:30:00Z,battery_bay,{44.5 + noise_temp:.1f},ACTIVE,1.8
2026-10-24T04:45:00Z,battery_bay,{48.1 + noise_temp:.1f},ACTIVE,1.9
2026-10-24T04:55:00Z,battery_bay,{51.4 + noise_temp:.1f},OVERHEAT_ALARM,1.9
"""

    instructions = (
        "CRITICAL ALERT [TCS-THM-501]: Battery thermal zone reports rapid uncontrolled temperature rise "
        "exceeding safe limits (approaching 50°C runaway threshold) due to an erroneous heater setpoint. "
        "Review `telemetry/thermal_log.csv` and `docs/flight_manual.md` for thermal constraints. "
        "Reconfigure `subsystems/thermal.cfg` with nominal setpoint and emergency cutoff temperatures "
        "to prevent catastrophic battery thermal failure."
    )

    files = {
        "docs/flight_manual.md": FLIGHT_MANUAL_MD,
        "subsystems/eps.cfg": _EPS_CFG_NOMINAL,
        "subsystems/adcs.cfg": _ADCS_CFG_NOMINAL,
        "subsystems/thermal.cfg": buggy_thermal,
        "subsystems/comms.cfg": _COMMS_CFG_NOMINAL,
        "telemetry/thermal_log.csv": thermal_log,
    }

    reference = {
        "subsystems/thermal.cfg": _THERMAL_CFG_NOMINAL,
    }

    return TaskInstance(
        task_id="thermal_battery_preservation",
        title="Battery Thermal Runaway Prevention",
        difficulty="medium",
        instructions=instructions,
        files=files,
        reference=reference,
        initial_soc_pct=75.0,
        initial_temp_c=round(36.0 + noise_temp, 1),
        initial_pointing_error_deg=5.0,
    )


# ---- 7. Task 4: RF Downlink Closure (Medium) --------------------------

def rf_downlink_closure_task(seed: int) -> TaskInstance:
    rng = random.Random(seed)
    tx_val = rng.choice([16.0, 18.0, 20.0])
    gain_val = rng.choice([1.5, 2.0, 2.5])

    buggy_comms = f"""\
[comms]
tx_power_dbm = {tx_val}
antenna_gain_dbi = {gain_val}
path_loss_db = 141.5
frequency_mhz = 437.5
baudrate_bps = 9600
"""

    instructions = (
        "COMMUNICATION FAILURE [COMMS-RF-404]: Ground station pass failed to achieve carrier lock; "
        "zero telemetry packets were downlinked. "
        "Two faults must be corrected: "
        "(1) In `subsystems/comms.cfg`, transmitter power and antenna gain are set below required orbital link levels. "
        "(2) In `scripts/link_budget.py`, the path loss calculation is hardcoded to a legacy test constant instead "
        "of reading `path_loss_db` from `subsystems/comms.cfg`. "
        "Refer to `docs/flight_manual.md` for link margin specifications, repair both files, and confirm "
        "that running `python scripts/link_budget.py` computes an operational margin >= 3.0 dB."
    )

    files = {
        "docs/flight_manual.md": FLIGHT_MANUAL_MD,
        "subsystems/eps.cfg": _EPS_CFG_NOMINAL,
        "subsystems/adcs.cfg": _ADCS_CFG_NOMINAL,
        "subsystems/thermal.cfg": _THERMAL_CFG_NOMINAL,
        "subsystems/comms.cfg": buggy_comms,
        "scripts/link_budget.py": _LINK_BUDGET_SCRIPT_BUGGY,
    }

    reference = {
        "subsystems/comms.cfg": _COMMS_CFG_NOMINAL,
        "scripts/link_budget.py": _LINK_BUDGET_SCRIPT_FIXED,
    }

    return TaskInstance(
        task_id="rf_downlink_closure",
        title="RF Downlink Closure & Link Budget",
        difficulty="medium",
        instructions=instructions,
        files=files,
        reference=reference,
        initial_soc_pct=80.0,
        initial_temp_c=12.0,
        initial_pointing_error_deg=5.0,
    )


# ---- 8. Task 5: Safe Mode Cascade (Hard, Seeded) ----------------------

def safe_mode_cascade_task(seed: int) -> TaskInstance:
    rng = random.Random(seed)

    # Randomly select 2 distinct subsystems to fault
    subsystems = ["eps", "adcs", "thermal", "comms"]
    failed_subsystems = rng.sample(subsystems, 2)

    files = {
        "docs/flight_manual.md": FLIGHT_MANUAL_MD,
        "subsystems/eps.cfg": _EPS_CFG_NOMINAL,
        "subsystems/adcs.cfg": _ADCS_CFG_NOMINAL,
        "subsystems/thermal.cfg": _THERMAL_CFG_NOMINAL,
        "subsystems/comms.cfg": _COMMS_CFG_NOMINAL,
        "scripts/link_budget.py": _LINK_BUDGET_SCRIPT_FIXED,
    }

    alert_msgs = []
    if "eps" in failed_subsystems:
        eff = rng.choice([0.35, 0.42])
        files["subsystems/eps.cfg"] = f"""\
[power]
solar_efficiency = {eff}
battery_capacity_wh = 40.0
undervoltage_threshold = 2.80
max_charge_rate_a = 1.2
bus_voltage_nominal = 3.7
"""
        alert_msgs.append("- [EPS]: Solar generation degraded; bus voltage dropping sharply during load.")

    if "adcs" in failed_subsystems:
        bias = rng.choice([95, 130])
        files["subsystems/adcs.cfg"] = f"""\
[attitude]
wheel_bias_rpm = {bias}
pointing_deadband_deg = 0.40
control_gain = 0.50
max_torque_nm = 0.002
control_frequency_hz = 10
"""
        alert_msgs.append("- [ADCS]: Reaction wheel momentum bias detected; satellite off-axis from sun vector.")

    if "thermal" in failed_subsystems:
        sp = rng.choice([58.0, 64.0])
        files["subsystems/thermal.cfg"] = f"""\
[thermal]
battery_heater_setpoint = {sp}
thermal_cutoff_temp = 32.0
heater_hysteresis_c = 2.0
sensor_poll_interval_s = 30
"""
        alert_msgs.append("- [TCS]: Battery heater operating abnormally; runaway thermal trend logged.")

    if "comms" in failed_subsystems:
        tx_p = rng.choice([18.0, 22.0])
        files["subsystems/comms.cfg"] = f"""\
[comms]
tx_power_dbm = {tx_p}
antenna_gain_dbi = 3.0
path_loss_db = 141.5
frequency_mhz = 437.5
baudrate_bps = 9600
"""
        files["scripts/link_budget.py"] = _LINK_BUDGET_SCRIPT_BUGGY
        alert_msgs.append("- [COMMS]: Downlink signal strength deficient; RF budget calculations failing.")

    instructions = (
        "EMERGENCY CASCADE [SAFE-MODE-CASCADE]: Spacecraft has entered autonomous safe mode following "
        "compound multi-subsystem anomalies across the bus:\n"
        + "\n".join(alert_msgs) + "\n\n"
        "Review `docs/flight_manual.md` for standard operating specifications across all four subsystems. "
        "Inspect all configuration files in `subsystems/`, diagnose and repair all faulted subsystems, "
        "and restore the satellite to nominal orbital mission capability."
    )

    reference = {
        "subsystems/eps.cfg": _EPS_CFG_NOMINAL,
        "subsystems/adcs.cfg": _ADCS_CFG_NOMINAL,
        "subsystems/thermal.cfg": _THERMAL_CFG_NOMINAL,
        "subsystems/comms.cfg": _COMMS_CFG_NOMINAL,
        "scripts/link_budget.py": _LINK_BUDGET_SCRIPT_FIXED,
    }

    return TaskInstance(
        task_id="safe_mode_cascade",
        title="Multi-Subsystem Safe Mode Cascade (Seeded)",
        difficulty="medium",
        instructions=instructions,
        files=files,
        reference=reference,
        initial_soc_pct=50.0,
        initial_temp_c=18.0,
        initial_pointing_error_deg=35.0,
    )


# ---- 9. Registry & Selector -------------------------------------------

TASKS = {
    "eps_eclipse_recovery": eps_eclipse_recovery_task,
    "adcs_detumble_pointing": adcs_detumble_pointing_task,
    "thermal_battery_preservation": thermal_battery_preservation_task,
    "rf_downlink_closure": rf_downlink_closure_task,
    "safe_mode_cascade": safe_mode_cascade_task,
}

DEMO_TASK = "eps_eclipse_recovery"


def build_task(task_id: str, seed: int) -> TaskInstance:
    if task_id not in TASKS:
        raise KeyError(f"unknown task '{task_id}'. Available: {', '.join(TASKS)}")
    return TASKS[task_id](seed)
