"""Satellite fault-response tasks for AeroSentinel.

Each task simulates a CubeSat subsystem failure. The agent reads onboard
telemetry and config files, diagnoses the fault, and applies corrective fixes.
Tasks are reproducible: the same (task_id, seed) always gives the same world.

Five tasks across two difficulty levels:
  easy   : power_brownout, adcs_wheel_bias
  medium : thermal_runaway, comms_link_budget, multi_fault_cascade
"""
import random
import textwrap
from dataclasses import dataclass


# ---- data containers ------------------------------------------------

@dataclass
class Case:
    group: str     # "target": fails before fix.  "regression": must keep passing.
    code: str      # runs in sandbox; must set a variable called `result`
    expect: object # compared in harness only; never sent to sandbox


@dataclass
class TaskInstance:
    task_id: str
    title: str
    difficulty: str
    instructions: str
    files: dict    # starting workspace files the agent can see
    cases: list    # hidden checks (list of Case)
    reference: dict  # files that fully solve the task; used by the oracle


def _case(group, code, expect):
    """Convenience wrapper — strips leading indentation from check code."""
    return Case(group, textwrap.dedent(code).strip(), expect)


# ---- 1. power_brownout (easy) ----------------------------------------

_EPS_CFG_BUGGY = """\
[power]
solar_efficiency = 0.41
battery_capacity_wh = 10.0
undervoltage_threshold = 2.8
max_charge_rate_a = 1.2
bus_voltage_nominal = 3.7
"""

_EPS_CFG_FIXED = """\
[power]
solar_efficiency = 0.72
battery_capacity_wh = 10.0
undervoltage_threshold = 3.2
max_charge_rate_a = 1.2
bus_voltage_nominal = 3.7
"""


def power_brownout_task(seed):
    return TaskInstance(
        task_id="power_brownout",
        title="EPS Solar Panel Degradation",
        difficulty="easy",
        instructions=(
            "The Electrical Power Subsystem (EPS) is reporting a brownout. "
            "Read `eps.cfg` and fix two miscalibrated parameters: "
            "`solar_efficiency` should be 0.72 (currently wrong) and "
            "`undervoltage_threshold` should be 3.2 V (currently wrong). "
            "All other parameters must stay unchanged."
        ),
        files={"eps.cfg": _EPS_CFG_BUGGY},
        cases=[
            _case("target", """
                import configparser
                cfg = configparser.ConfigParser()
                cfg.read('eps.cfg')
                p = cfg['power']
                result = (round(float(p['solar_efficiency']), 2) == 0.72 and
                          round(float(p['undervoltage_threshold']), 2) == 3.2)
            """, True),
            _case("regression", """
                import configparser
                cfg = configparser.ConfigParser()
                cfg.read('eps.cfg')
                p = cfg['power']
                result = (round(float(p['battery_capacity_wh']), 1) == 10.0 and
                          round(float(p['max_charge_rate_a']), 1) == 1.2 and
                          round(float(p['bus_voltage_nominal']), 1) == 3.7)
            """, True),
        ],
        reference={"eps.cfg": _EPS_CFG_FIXED},
    )


# ---- 2. adcs_wheel_bias (easy) ---------------------------------------

_ADCS_CFG_BUGGY = """\
[attitude]
wheel_bias_rpm = 120
pointing_deadband_deg = 0.5
max_torque_nm = 0.002
control_frequency_hz = 10
"""

_ADCS_CFG_FIXED = """\
[attitude]
wheel_bias_rpm = 0
pointing_deadband_deg = 0.05
max_torque_nm = 0.002
control_frequency_hz = 10
"""


def adcs_wheel_bias_task(seed):
    return TaskInstance(
        task_id="adcs_wheel_bias",
        title="ADCS Reaction Wheel Speed Bias",
        difficulty="easy",
        instructions=(
            "The Attitude Determination and Control System (ADCS) is drifting off-axis. "
            "Read `adcs.cfg` and correct two parameters: "
            "`wheel_bias_rpm` must be 0 (no residual bias) and "
            "`pointing_deadband_deg` must be 0.05 degrees. "
            "Do not modify any other parameters."
        ),
        files={"adcs.cfg": _ADCS_CFG_BUGGY},
        cases=[
            _case("target", """
                import configparser
                cfg = configparser.ConfigParser()
                cfg.read('adcs.cfg')
                a = cfg['attitude']
                result = (round(float(a['wheel_bias_rpm'])) == 0 and
                          round(float(a['pointing_deadband_deg']), 2) == 0.05)
            """, True),
            _case("regression", """
                import configparser
                cfg = configparser.ConfigParser()
                cfg.read('adcs.cfg')
                a = cfg['attitude']
                result = (round(float(a['max_torque_nm']), 3) == 0.002 and
                          round(float(a['control_frequency_hz'])) == 10)
            """, True),
        ],
        reference={"adcs.cfg": _ADCS_CFG_FIXED},
    )


# ---- 3. thermal_runaway (medium) -------------------------------------

_THERMAL_CFG_BUGGY = """\
[thermal]
battery_heater_setpoint = 65.0
thermal_cutoff_temp = 35.0
heater_hysteresis_c = 2.0
sensor_poll_interval_s = 30
"""

_THERMAL_CFG_FIXED = """\
[thermal]
battery_heater_setpoint = 15.0
thermal_cutoff_temp = 45.0
heater_hysteresis_c = 2.0
sensor_poll_interval_s = 30
"""

# short telemetry dump so the agent can observe the runaway trend
_THERMAL_LOG = """\
timestamp,zone,temp_c,heater_state
2026-10-23T08:00:00Z,battery,62.1,ON
2026-10-23T08:05:00Z,battery,63.8,ON
2026-10-23T08:10:00Z,battery,65.2,ON
2026-10-23T08:15:00Z,battery,66.1,ON
2026-10-23T08:20:00Z,battery,67.3,ON
"""


def thermal_runaway_task(seed):
    return TaskInstance(
        task_id="thermal_runaway",
        title="Battery Thermal Runaway",
        difficulty="medium",
        instructions=(
            "CRITICAL: The battery thermal zone is in runaway. "
            "Read `telemetry/thermal_log.csv` to see the temperature trend, "
            "then fix `thermal.cfg`. Two parameters are wrong: "
            "`battery_heater_setpoint` should be 15.0 C (not 65.0) and "
            "`thermal_cutoff_temp` should be 45.0 C (currently 35.0, triggers too early). "
            "Leave all other parameters unchanged."
        ),
        files={
            "thermal.cfg": _THERMAL_CFG_BUGGY,
            "telemetry/thermal_log.csv": _THERMAL_LOG,
        },
        cases=[
            _case("target", """
                import configparser
                cfg = configparser.ConfigParser()
                cfg.read('thermal.cfg')
                t = cfg['thermal']
                sp = float(t['battery_heater_setpoint'])
                co = float(t['thermal_cutoff_temp'])
                result = (10.0 <= sp <= 25.0 and round(co, 1) == 45.0)
            """, True),
            _case("regression", """
                import configparser
                cfg = configparser.ConfigParser()
                cfg.read('thermal.cfg')
                t = cfg['thermal']
                result = (round(float(t['heater_hysteresis_c']), 1) == 2.0 and
                          round(float(t['sensor_poll_interval_s'])) == 30)
            """, True),
        ],
        reference={
            "thermal.cfg": _THERMAL_CFG_FIXED,
            "telemetry/thermal_log.csv": _THERMAL_LOG,
        },
    )


# ---- 4. comms_link_budget (medium) -----------------------------------

_COMMS_CFG_BUGGY = """\
[comms]
tx_power_dbm = 20
antenna_gain_dbi = 2
path_loss_db = 35
frequency_mhz = 437.5
baudrate_bps = 9600
"""

_COMMS_CFG_FIXED = """\
[comms]
tx_power_dbm = 30
antenna_gain_dbi = 12
path_loss_db = 35
frequency_mhz = 437.5
baudrate_bps = 9600
"""

# NOTE: hardcoded path_loss is the bug — a test value that was never cleaned up
_LINK_BUDGET_BUGGY = (
    '"""Link budget calculator for the UHF downlink."""\n'
    'import configparser\n'
    '\n'
    '\n'
    'def calc_margin():\n'
    '    """Return the RF link margin in dB."""\n'
    '    cfg = configparser.ConfigParser()\n'
    "    cfg.read('comms.cfg')\n"
    "    tx_power = float(cfg['comms']['tx_power_dbm'])\n"
    "    gain = float(cfg['comms']['antenna_gain_dbi'])\n"
    '    path_loss = 120  # TODO: was hardcoded during a bench test, forgot to fix it\n'
    '    margin = tx_power + gain - path_loss\n'
    '    return round(margin, 2)\n'
    '\n'
    '\n'
    "if __name__ == '__main__':\n"
    '    print(f"Link margin: {calc_margin()} dB")\n'
)

_LINK_BUDGET_FIXED = (
    '"""Link budget calculator for the UHF downlink."""\n'
    'import configparser\n'
    '\n'
    '\n'
    'def calc_margin():\n'
    '    """Return the RF link margin in dB."""\n'
    '    cfg = configparser.ConfigParser()\n'
    "    cfg.read('comms.cfg')\n"
    "    tx_power = float(cfg['comms']['tx_power_dbm'])\n"
    "    gain = float(cfg['comms']['antenna_gain_dbi'])\n"
    "    path_loss = float(cfg['comms']['path_loss_db'])\n"
    '    margin = tx_power + gain - path_loss\n'
    '    return round(margin, 2)\n'
    '\n'
    '\n'
    "if __name__ == '__main__':\n"
    '    print(f"Link margin: {calc_margin()} dB")\n'
)


def comms_link_budget_task(seed):
    return TaskInstance(
        task_id="comms_link_budget",
        title="Comms Link Budget Failure",
        difficulty="medium",
        instructions=(
            "The UHF downlink is failing. There are two problems to fix: "
            "(1) In `comms.cfg`, `tx_power_dbm` should be 30 dBm and "
            "`antenna_gain_dbi` should be 12 dBi. "
            "(2) In `comms/link_budget.py`, `path_loss` is hardcoded to 120 — "
            "fix it to read `path_loss_db` from `comms.cfg` instead. "
            "After both fixes, running `comms/link_budget.py` should print a positive margin."
        ),
        files={
            "comms.cfg": _COMMS_CFG_BUGGY,
            "comms/link_budget.py": _LINK_BUDGET_BUGGY,
        },
        cases=[
            _case("target", """
                import configparser
                cfg = configparser.ConfigParser()
                cfg.read('comms.cfg')
                c = cfg['comms']
                result = (round(float(c['tx_power_dbm'])) == 30 and
                          round(float(c['antenna_gain_dbi'])) == 12)
            """, True),
            _case("target", """
                import sys
                sys.path.insert(0, '.')
                from comms.link_budget import calc_margin
                result = calc_margin() == 7.0
            """, True),
            _case("regression", """
                import configparser
                cfg = configparser.ConfigParser()
                cfg.read('comms.cfg')
                c = cfg['comms']
                result = (round(float(c['path_loss_db'])) == 35 and
                          round(float(c['frequency_mhz']), 1) == 437.5 and
                          round(float(c['baudrate_bps'])) == 9600)
            """, True),
        ],
        reference={
            "comms.cfg": _COMMS_CFG_FIXED,
            "comms/link_budget.py": _LINK_BUDGET_FIXED,
        },
    )


# ---- 5. multi_fault_cascade (medium, seeded) -------------------------

# nominal values for all four subsystems
_NOMINAL = {
    "eps": {
        "solar_efficiency": "0.72",
        "battery_capacity_wh": "10.0",
        "undervoltage_threshold": "3.2",
        "max_charge_rate_a": "1.2",
        "bus_voltage_nominal": "3.7",
    },
    "adcs": {
        "wheel_bias_rpm": "0",
        "pointing_deadband_deg": "0.05",
        "max_torque_nm": "0.002",
        "control_frequency_hz": "10",
    },
    "thermal": {
        "battery_heater_setpoint": "15.0",
        "thermal_cutoff_temp": "45.0",
        "heater_hysteresis_c": "2.0",
        "sensor_poll_interval_s": "30",
    },
    "comms": {
        "tx_power_dbm": "30",
        "antenna_gain_dbi": "12",
        "path_loss_db": "35",
        "frequency_mhz": "437.5",
        "baudrate_bps": "9600",
    },
}

# pool of faults that can be injected per subsystem
_FAULT_POOL = {
    "eps":     [("solar_efficiency", "0.35"), ("solar_efficiency", "0.41"),
                ("undervoltage_threshold", "2.6")],
    "adcs":    [("wheel_bias_rpm", "80"), ("wheel_bias_rpm", "120"),
                ("pointing_deadband_deg", "0.8")],
    "thermal": [("battery_heater_setpoint", "55.0"), ("battery_heater_setpoint", "65.0"),
                ("thermal_cutoff_temp", "30.0")],
    "comms":   [("tx_power_dbm", "15"), ("antenna_gain_dbi", "4"),
                ("tx_power_dbm", "18")],
}

_SECTION = {"eps": "power", "adcs": "attitude", "thermal": "thermal", "comms": "comms"}


def _make_cfg(subsystem, overrides=None):
    """Build an INI config string for one subsystem, optionally with faulted values."""
    section = _SECTION[subsystem]
    vals = dict(_NOMINAL[subsystem])
    if overrides:
        vals.update(overrides)
    lines = [f"[{section}]"]
    for k, v in vals.items():
        lines.append(f"{k} = {v}")
    return "\n".join(lines) + "\n"


def _check_code(subsystem):
    """Return hidden check code that asserts a subsystem is at nominal values."""
    section = _SECTION[subsystem]
    fname = f"{subsystem}.cfg"
    # we check every key individually — verbose but leaves no ambiguity
    checks = " and ".join(
        f"cfg['{section}']['{k}'].strip() == '{v}'"
        for k, v in _NOMINAL[subsystem].items()
    )
    return textwrap.dedent(f"""
        import configparser
        cfg = configparser.ConfigParser()
        cfg.read('{fname}')
        result = {checks}
    """).strip()


def multi_fault_cascade_task(seed):
    rng = random.Random(seed)
    # pick exactly 2 subsystems to inject faults into
    failed = rng.sample(["eps", "adcs", "thermal", "comms"], 2)

    files = {}
    fault_lines = []
    for sub in ["eps", "adcs", "thermal", "comms"]:
        if sub in failed:
            fault_key, fault_val = rng.choice(_FAULT_POOL[sub])
            files[f"{sub}.cfg"] = _make_cfg(sub, {fault_key: fault_val})
            fault_lines.append(f"  - {sub}.cfg has at least one wrong parameter.")
        else:
            files[f"{sub}.cfg"] = _make_cfg(sub)

    instructions = (
        "Multiple subsystems are reporting anomalies. Inspect all four config files "
        "(eps.cfg, adcs.cfg, thermal.cfg, comms.cfg) and fix every faulted parameter.\n"
        + "\n".join(fault_lines)
    )

    # reference fixes every subsystem to nominal (safe even for the ones already correct)
    reference = {f"{sub}.cfg": _make_cfg(sub) for sub in ["eps", "adcs", "thermal", "comms"]}

    cases = []
    for sub in ["eps", "adcs", "thermal", "comms"]:
        group = "target" if sub in failed else "regression"
        cases.append(_case(group, _check_code(sub), True))

    return TaskInstance(
        task_id="multi_fault_cascade",
        title="Multi-Subsystem Fault Cascade (seeded)",
        difficulty="medium",
        instructions=instructions,
        files=files,
        cases=cases,
        reference=reference,
    )


# ---- registry --------------------------------------------------------

TASKS = {
    "power_brownout":      power_brownout_task,
    "adcs_wheel_bias":     adcs_wheel_bias_task,
    "thermal_runaway":     thermal_runaway_task,
    "comms_link_budget":   comms_link_budget_task,
    "multi_fault_cascade": multi_fault_cascade_task,
}
DEMO_TASK = "power_brownout"


def build_task(task_id, seed):
    if task_id not in TASKS:
        raise KeyError(f"unknown task '{task_id}'. Available: {', '.join(TASKS)}")
    return TASKS[task_id](seed)
