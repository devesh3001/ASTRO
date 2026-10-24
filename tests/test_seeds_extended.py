"""Extended Seed & Reproducibility Validation (100 task-seed combinations).

Verifies across all 5 tasks and 20 distinct seeds (0-19):
1. Deterministic reset: identical files and initial state for same (task_id, seed).
2. Reference solution scores >= 0.95 across all 100 combinations.
3. No-op baseline scores <= 0.05 across all 100 combinations.
"""
import configparser
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from harness.tasks import TASKS, build_task
from harness.verifier import simulate_orbit
from harness.env import _verify_link_budget_behaviour


class TestExtendedSeeds(unittest.TestCase):
    def test_all_100_combinations(self):
        print("\n=== Running Extended Seed Validation (5 Tasks x 20 Seeds = 100 Runs) ===", flush=True)
        total_runs = 0
        all_passed = True

        for tid in TASKS:
            task_ref_passes = 0
            task_noop_passes = 0
            for seed in range(20):
                total_runs += 1
                t1 = build_task(tid, seed)
                t2 = build_task(tid, seed)

                # 1. Deterministic reset verification
                self.assertEqual(t1.files, t2.files, f"Non-deterministic files for {tid} seed {seed}")
                self.assertEqual(t1.reference, t2.reference, f"Non-deterministic ref for {tid} seed {seed}")

                # 2. No-op check
                eps_no = configparser.ConfigParser(); eps_no.read_string(t1.files.get("subsystems/eps.cfg", ""))
                adcs_no = configparser.ConfigParser(); adcs_no.read_string(t1.files.get("subsystems/adcs.cfg", ""))
                thm_no = configparser.ConfigParser(); thm_no.read_string(t1.files.get("subsystems/thermal.cfg", ""))
                com_no = configparser.ConfigParser(); com_no.read_string(t1.files.get("subsystems/comms.cfg", ""))

                no_script_ok = _verify_link_budget_behaviour(t1.files)
                no_ploss = float(com_no["comms"].get("path_loss_db", 141.5))
                if not no_script_ok and tid in ("rf_downlink_closure", "safe_mode_cascade") and "scripts/link_budget.py" in t1.files:
                    no_ploss = 175.0

                res_noop = simulate_orbit(
                    solar_efficiency=float(eps_no["power"]["solar_efficiency"]),
                    undervoltage_threshold=float(eps_no["power"]["undervoltage_threshold"]),
                    wheel_bias_rpm=float(adcs_no["attitude"]["wheel_bias_rpm"]),
                    pointing_deadband_deg=float(adcs_no["attitude"]["pointing_deadband_deg"]),
                    control_gain=float(adcs_no["attitude"]["control_gain"]),
                    heater_setpoint_c=float(thm_no["thermal"]["battery_heater_setpoint"]),
                    heater_hysteresis_c=float(thm_no["thermal"]["heater_hysteresis_c"]),
                    thermal_cutoff_c=float(thm_no["thermal"]["thermal_cutoff_temp"]),
                    tx_power_dbm=float(com_no["comms"]["tx_power_dbm"]),
                    antenna_gain_dbi=float(com_no["comms"]["antenna_gain_dbi"]),
                    path_loss_db=no_ploss,
                    initial_soc_pct=t1.initial_soc_pct,
                    initial_temp_c=t1.initial_temp_c,
                    initial_pointing_error_deg=t1.initial_pointing_error_deg,
                    task_id=tid,
                    script_verified=no_script_ok,
                )
                self.assertLessEqual(res_noop.reward["total"], 0.05, f"No-op failed for {tid} seed {seed}: {res_noop.reward['total']}")
                task_noop_passes += 1

                # 3. Reference check
                merged = dict(t1.files)
                merged.update(t1.reference)
                eps_ref = configparser.ConfigParser(); eps_ref.read_string(merged.get("subsystems/eps.cfg", ""))
                adcs_ref = configparser.ConfigParser(); adcs_ref.read_string(merged.get("subsystems/adcs.cfg", ""))
                thm_ref = configparser.ConfigParser(); thm_ref.read_string(merged.get("subsystems/thermal.cfg", ""))
                com_ref = configparser.ConfigParser(); com_ref.read_string(merged.get("subsystems/comms.cfg", ""))

                ref_script_ok = _verify_link_budget_behaviour(merged)
                ref_ploss = float(com_ref["comms"].get("path_loss_db", 141.5))
                if not ref_script_ok and tid in ("rf_downlink_closure", "safe_mode_cascade"):
                    ref_ploss = 175.0

                res_ref = simulate_orbit(
                    solar_efficiency=float(eps_ref["power"]["solar_efficiency"]),
                    undervoltage_threshold=float(eps_ref["power"]["undervoltage_threshold"]),
                    wheel_bias_rpm=float(adcs_ref["attitude"]["wheel_bias_rpm"]),
                    pointing_deadband_deg=float(adcs_ref["attitude"]["pointing_deadband_deg"]),
                    control_gain=float(adcs_ref["attitude"]["control_gain"]),
                    heater_setpoint_c=float(thm_ref["thermal"]["battery_heater_setpoint"]),
                    heater_hysteresis_c=float(thm_ref["thermal"]["heater_hysteresis_c"]),
                    thermal_cutoff_c=float(thm_ref["thermal"]["thermal_cutoff_temp"]),
                    tx_power_dbm=float(com_ref["comms"]["tx_power_dbm"]),
                    antenna_gain_dbi=float(com_ref["comms"]["antenna_gain_dbi"]),
                    path_loss_db=ref_ploss,
                    initial_soc_pct=t1.initial_soc_pct,
                    initial_temp_c=t1.initial_temp_c,
                    initial_pointing_error_deg=t1.initial_pointing_error_deg,
                    task_id=tid,
                    script_verified=ref_script_ok,
                )
                self.assertGreaterEqual(res_ref.reward["total"], 0.95, f"Ref failed for {tid} seed {seed}: {res_ref.reward['total']}")
                task_ref_passes += 1

            print(f"  {tid:<30}: 20/20 Ref PASS (>=0.95), 20/20 No-op PASS (<=0.05)", flush=True)

        print(f"\nAll 100/100 task-seed combinations successfully verified!", flush=True)


if __name__ == "__main__":
    unittest.main()
