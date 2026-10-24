"""Physical Validation Across All 5 Tasks.

Ensures:
1. Every reference solution scores >= 0.95 across all 5 tasks.
2. Every no-op baseline scores <= 0.05 across all 5 tasks.
3. Behavioral verifier correctly evaluates dynamic link budget scripts.
4. Process exits with code 0 on success, code 1 on failure.
"""
import configparser
import sys
from pathlib import Path

# Add project root to sys.path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from harness.tasks import TASKS, build_task
from harness.verifier import simulate_orbit
from harness.env import _verify_link_budget_behaviour


def run_physics_validation():
    print("=== Physical Validation Across All 5 Tasks ===", flush=True)
    all_pass = True

    for tid in TASKS:
        t = build_task(tid, 0)

        # ------------------------------------------------------------- 1. No-op
        eps = configparser.ConfigParser()
        eps.read_string(t.files.get("subsystems/eps.cfg", ""))
        adcs = configparser.ConfigParser()
        adcs.read_string(t.files.get("subsystems/adcs.cfg", ""))
        thm = configparser.ConfigParser()
        thm.read_string(t.files.get("subsystems/thermal.cfg", ""))
        com = configparser.ConfigParser()
        com.read_string(t.files.get("subsystems/comms.cfg", ""))

        script_ok = _verify_link_budget_behaviour(t.files)
        p_loss = float(com["comms"].get("path_loss_db", 141.5))
        if not script_ok and tid in ("rf_downlink_closure", "safe_mode_cascade") and "scripts/link_budget.py" in t.files:
            p_loss = 175.0

        res_noop = simulate_orbit(
            solar_efficiency=float(eps["power"]["solar_efficiency"]),
            undervoltage_threshold=float(eps["power"]["undervoltage_threshold"]),
            wheel_bias_rpm=float(adcs["attitude"]["wheel_bias_rpm"]),
            pointing_deadband_deg=float(adcs["attitude"]["pointing_deadband_deg"]),
            control_gain=float(adcs["attitude"]["control_gain"]),
            heater_setpoint_c=float(thm["thermal"]["battery_heater_setpoint"]),
            heater_hysteresis_c=float(thm["thermal"]["heater_hysteresis_c"]),
            thermal_cutoff_c=float(thm["thermal"]["thermal_cutoff_temp"]),
            tx_power_dbm=float(com["comms"]["tx_power_dbm"]),
            antenna_gain_dbi=float(com["comms"]["antenna_gain_dbi"]),
            path_loss_db=p_loss,
            initial_soc_pct=t.initial_soc_pct,
            initial_temp_c=t.initial_temp_c,
            initial_pointing_error_deg=t.initial_pointing_error_deg,
            task_id=tid,
            script_verified=script_ok,
        )

        # ------------------------------------------------------------- 2. Reference
        merged = dict(t.files)
        merged.update(t.reference)
        eps.read_string(merged.get("subsystems/eps.cfg", ""))
        adcs.read_string(merged.get("subsystems/adcs.cfg", ""))
        thm.read_string(merged.get("subsystems/thermal.cfg", ""))
        com.read_string(merged.get("subsystems/comms.cfg", ""))

        ref_script_ok = _verify_link_budget_behaviour(merged)
        ref_ploss = float(com["comms"].get("path_loss_db", 141.5))
        if not ref_script_ok and tid in ("rf_downlink_closure", "safe_mode_cascade"):
            ref_ploss = 175.0

        res_ref = simulate_orbit(
            solar_efficiency=float(eps["power"]["solar_efficiency"]),
            undervoltage_threshold=float(eps["power"]["undervoltage_threshold"]),
            wheel_bias_rpm=float(adcs["attitude"]["wheel_bias_rpm"]),
            pointing_deadband_deg=float(adcs["attitude"]["pointing_deadband_deg"]),
            control_gain=float(adcs["attitude"]["control_gain"]),
            heater_setpoint_c=float(thm["thermal"]["battery_heater_setpoint"]),
            heater_hysteresis_c=float(thm["thermal"]["heater_hysteresis_c"]),
            thermal_cutoff_c=float(thm["thermal"]["thermal_cutoff_temp"]),
            tx_power_dbm=float(com["comms"]["tx_power_dbm"]),
            antenna_gain_dbi=float(com["comms"]["antenna_gain_dbi"]),
            path_loss_db=ref_ploss,
            initial_soc_pct=t.initial_soc_pct,
            initial_temp_c=t.initial_temp_c,
            initial_pointing_error_deg=t.initial_pointing_error_deg,
            task_id=tid,
            script_verified=ref_script_ok,
        )

        ref_ok = res_ref.reward["total"] >= 0.95
        noop_ok = res_noop.reward["total"] <= 0.05
        all_pass = all_pass and ref_ok and noop_ok

        print(
            f"{tid:<30} | Ref: {res_ref.reward['total']:.2f} ({'PASS' if ref_ok else 'FAIL'}) "
            f"| No-op: {res_noop.reward['total']:.2f} ({'PASS' if noop_ok else 'FAIL'})",
            flush=True,
        )

    print(f"\nOverall Physical Validation Result: {'ALL PASS' if all_pass else 'FAILED'}", flush=True)
    if not all_pass:
        print("ERROR: Baseline physics check failed!", file=sys.stderr, flush=True)
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    run_physics_validation()
