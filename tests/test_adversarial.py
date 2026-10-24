"""Adversarial & Security Test Suite for AeroSentinel.

Verifies:
1. Privileged endpoint protection: 403 Forbidden without secret token on all protected endpoints.
2. Endpoint authorization: 200 OK when authenticating with valid secret token.
3. Secret scrubbing in sandbox run_python environment.
4. Path traversal protection: safe_path blocks ../ escapes and symlink breakouts.
5. Tool timeout & runaway loop handling: infinite loops stopped safely.
6. Output clipping: caps response at MAX_OUTPUT characters.
7. Physical bounds checking: rejects NaN, Inf, and impossible engineering parameters.
8. Authoritative failure latching: catastrophic events cannot be undone.
9. Interactive orbital stepping: dynamic state progression and pointing convergence.
10. Dynamic isolated script evaluation: behavioural verification without harness execution.
11. Deterministic seeded resets and seed variety.
"""
import hashlib
import json
import os
import sys
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

# Add project root to sys.path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import sandbox.server as srv
from harness.tasks import TASKS, build_task
from harness.verifier import (
    validate_subsystem_configs,
    AuthoritativeOrbitSimulator,
)


class TestSecurityAndIsolation(unittest.TestCase):
    def test_path_traversal_prevention(self):
        """Verify that directory traversal attempts are blocked."""
        with self.assertRaises(ValueError):
            srv.safe_path("../../etc/passwd")
        with self.assertRaises(ValueError):
            srv.safe_path("../harness/verifier.py")
        with self.assertRaises(ValueError):
            srv.safe_path("/etc/shadow")

        # Test symlink breakout rejection if OS allows symlink creation
        srv.WORKSPACE.mkdir(parents=True, exist_ok=True)
        symlink_path = srv.WORKSPACE / "escape_symlink"
        try:
            if symlink_path.is_symlink() or symlink_path.exists():
                symlink_path.unlink()
            symlink_path.symlink_to(ROOT)
            with self.assertRaises(ValueError):
                srv.safe_path("escape_symlink/harness/verifier.py")
        except (OSError, NotImplementedError):
            pass  # Windows non-elevated symlinks may not be supported
        finally:
            if symlink_path.is_symlink():
                symlink_path.unlink()

    def test_secret_scrubbing_in_sandbox(self):
        """Verify that agent subprocesses cannot see HARNESS_SECRET."""
        os.environ["HARNESS_SECRET"] = "super_classified_token_123"
        code = "import os; print(os.environ.get('HARNESS_SECRET', 'NOT_FOUND'))"
        output = srv.tool_run_python({"code": code})
        self.assertIn("NOT_FOUND", output)
        self.assertNotIn("super_classified_token_123", output)

    def test_timeout_enforcement_on_infinite_loop(self):
        """Verify that an infinite loop code snippet is killed after timeout."""
        old_timeout = srv.RUN_TIMEOUT
        try:
            srv.RUN_TIMEOUT = 1
            code = "import time\nwhile True:\n    time.sleep(0.05)"
            output = srv.tool_run_python({"code": code})
            self.assertIn("exceeded", output)
            self.assertIn("1 seconds", output)
        finally:
            srv.RUN_TIMEOUT = old_timeout

    def test_output_clipping(self):
        """Verify output truncation at MAX_OUTPUT characters."""
        long_text = "A" * (srv.MAX_OUTPUT + 500)
        clipped = srv.clip(long_text)
        self.assertLessEqual(len(clipped), srv.MAX_OUTPUT + 100)
        self.assertIn("[output cut at", clipped)

    def test_physical_bounds_validation(self):
        """Verify that impossible, infinite, or NaN parameters are rejected."""
        nominal = {
            "solar_efficiency": 0.72,
            "undervoltage_threshold": 3.2,
            "battery_capacity_wh": 40.0,
            "wheel_bias_rpm": 0.0,
            "pointing_deadband_deg": 0.05,
            "control_gain": 1.0,
            "battery_heater_setpoint": 15.0,
            "heater_hysteresis_c": 2.0,
            "thermal_cutoff_temp": 45.0,
            "tx_power_dbm": 30.0,
            "antenna_gain_dbi": 12.0,
            "path_loss_db": 141.5,
            "baudrate_bps": 9600.0,
        }
        ok, reason = validate_subsystem_configs(nominal)
        self.assertTrue(ok, f"Expected nominal to pass: {reason}")

        # NaN rejection
        ok, reason = validate_subsystem_configs({**nominal, "solar_efficiency": float("nan")})
        self.assertFalse(ok)
        self.assertIn("NaN/Inf", reason)

        # Inf rejection
        ok, reason = validate_subsystem_configs({**nominal, "tx_power_dbm": float("inf")})
        self.assertFalse(ok)
        self.assertIn("NaN/Inf", reason)

        # Solar efficiency > 1.0
        ok, reason = validate_subsystem_configs({**nominal, "solar_efficiency": 1.5})
        self.assertFalse(ok)
        self.assertIn("outside physical limits", reason)

        # Negative battery capacity
        ok, reason = validate_subsystem_configs({**nominal, "battery_capacity_wh": -5.0})
        self.assertFalse(ok)
        self.assertIn("outside physical limits", reason)

        # Absurd baud rate
        ok, reason = validate_subsystem_configs({**nominal, "baudrate_bps": 1000000.0})
        self.assertFalse(ok)
        self.assertIn("outside physical limits", reason)

    def test_catastrophic_failure_latching(self):
        """Verify that catastrophic failure (e.g. battery collapse) cannot be undone."""
        sim = AuthoritativeOrbitSimulator(
            initial_soc_pct=5.0,
            initial_temp_c=14.0,
            initial_pointing_error_deg=35.0,
            task_id="eps_eclipse_recovery",
        )
        buggy_cfg = {
            "solar_efficiency": 0.0,
            "undervoltage_threshold": 3.4,
            "battery_capacity_wh": 40.0,
            "wheel_bias_rpm": 120.0,
            "pointing_deadband_deg": 0.5,
            "control_gain": 1.0,
            "battery_heater_setpoint": 15.0,
            "heater_hysteresis_c": 2.0,
            "thermal_cutoff_temp": 45.0,
            "tx_power_dbm": 20.0,
            "antenna_gain_dbi": 2.0,
            "path_loss_db": 141.5,
            "baudrate_bps": 9600.0,
        }
        sim.advance(30 * 60.0, buggy_cfg)
        self.assertTrue(sim.catastrophic_failure)

        # Trying to apply reference configs afterward must NOT erase catastrophic failure
        perfect_cfg = dict(buggy_cfg)
        perfect_cfg["solar_efficiency"] = 0.72
        perfect_cfg["wheel_bias_rpm"] = 0.0
        perfect_cfg["pointing_deadband_deg"] = 0.05
        sim.advance(10 * 60.0, perfect_cfg)
        self.assertTrue(sim.catastrophic_failure)

        res = sim.finalize_orbit(perfect_cfg)
        self.assertEqual(res.reward["total"], 0.0)

    def test_interactive_orbit_stepping(self):
        """Verify AuthoritativeOrbitSimulator advances virtual time and converges pointing."""
        sim = AuthoritativeOrbitSimulator(
            initial_soc_pct=70.0,
            initial_temp_c=14.0,
            initial_pointing_error_deg=35.0,
            task_id="eps_eclipse_recovery",
        )
        nominal_cfg = {
            "solar_efficiency": 0.72,
            "undervoltage_threshold": 3.2,
            "battery_capacity_wh": 40.0,
            "wheel_bias_rpm": 0.0,
            "pointing_deadband_deg": 0.05,
            "control_gain": 2.5,
            "battery_heater_setpoint": 15.0,
            "heater_hysteresis_c": 2.0,
            "thermal_cutoff_temp": 45.0,
            "tx_power_dbm": 30.0,
            "antenna_gain_dbi": 12.0,
            "path_loss_db": 141.5,
            "baudrate_bps": 9600.0,
        }
        frames = sim.advance(15 * 60.0, nominal_cfg, dt_s=60.0)
        self.assertEqual(len(frames), 15)
        self.assertEqual(round(sim.time_s / 60.0, 1), 15.0)
        self.assertLess(sim.pointing_err_deg, 35.0)


class TestAuthenticatedEndpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.test_token = "adversarial_test_secret_token_999"
        srv._AUTH_TOKEN = cls.test_token

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
        cls.port = cls.server.server_address[1]
        cls.base_url = f"http://127.0.0.1:{cls.port}"
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()
        time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_health_endpoint_public(self):
        """Health check endpoint should be public without authorization."""
        req = Request(f"{self.base_url}/health")
        with urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode())
            self.assertTrue(data.get("ok"))

    def test_privileged_endpoints_reject_without_token(self):
        """Privileged endpoints must return 403 when unauthenticated."""
        endpoints = [
            ("GET", "/state", None),
            ("POST", "/reset", b'{"files": {}}'),
            ("POST", "/tool", b'{"name":"list_files","args":{}}'),
            ("POST", "/fetch_configs", b'{}'),
            ("POST", "/eval_script", b'{"script_relpath": "scripts/link_budget.py", "test_cases": []}'),
            ("POST", "/sync_telemetry", b'{"status": {}, "csv_append": ""}'),
        ]
        for method, path, data in endpoints:
            req = Request(
                f"{self.base_url}{path}",
                data=data,
                headers={"Content-Type": "application/json"} if data else {}
            )
            req.get_method = lambda m=method: m
            with self.assertRaises(HTTPError) as ctx:
                urlopen(req)
            self.assertEqual(ctx.exception.code, 403, f"Expected 403 for {path}")

    def test_privileged_endpoints_accept_with_token(self):
        """Privileged endpoints accept calls when presenting the correct X-Harness-Secret header."""
        headers = {
            "Content-Type": "application/json",
            "X-Harness-Secret": self.test_token
        }

        # 1. GET /state
        req = Request(f"{self.base_url}/state", headers=headers)
        with urlopen(req) as resp:
            self.assertEqual(resp.status, 200)

        # 2. POST /reset
        req = Request(
            f"{self.base_url}/reset",
            data=b'{"files": {"scripts/link_budget.py": "def calc_margin(): return 5.5\\n"}}',
            headers=headers
        )
        with urlopen(req) as resp:
            self.assertEqual(resp.status, 200)

        # 3. POST /fetch_configs
        req = Request(f"{self.base_url}/fetch_configs", data=b'{}', headers=headers)
        with urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode())
            self.assertIn("files", data)

        # 4. POST /sync_telemetry
        sync_payload = json.dumps({"status": {"elapsed_minutes": 15}, "csv_append": "15,SUNLIGHT\n"}).encode()
        req = Request(f"{self.base_url}/sync_telemetry", data=sync_payload, headers=headers)
        with urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode())
            self.assertTrue(data.get("ok"))

        # 5. POST /eval_script
        eval_payload = json.dumps({
            "script_relpath": "scripts/link_budget.py",
            "test_cases": [{"subsystems/comms.cfg": "[comms]\n"}]
        }).encode()
        req = Request(f"{self.base_url}/eval_script", data=eval_payload, headers=headers)
        with urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode())
            self.assertIn("results", data)
            self.assertEqual(data["results"], [5.5])


class TestTaskSeedsAndReproducibility(unittest.TestCase):
    def test_seeded_reproducibility(self):
        """Same seed must produce bit-for-bit identical task files and state hash."""
        t1 = build_task("safe_mode_cascade", seed=42)
        t2 = build_task("safe_mode_cascade", seed=42)
        self.assertEqual(t1.files, t2.files)
        self.assertEqual(t1.reference, t2.reference)

    def test_seed_variety(self):
        """Different seeds should generate distinct failure variants."""
        t1 = build_task("safe_mode_cascade", seed=42)
        t2 = build_task("safe_mode_cascade", seed=99)
        self.assertNotEqual(t1.files, t2.files)


class TestAdversarialScoringAndIsolation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.test_token = "adversarial_scoring_test_secret"
        srv._AUTH_TOKEN = cls.test_token
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
        cls.port = cls.server.server_address[1]
        cls.base_url = f"http://127.0.0.1:{cls.port}"
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()
        time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_rf_reward_bypass_prevention(self):
        """Verify that rf_downlink_closure CANNOT receive credit if link_budget.py is unverified,
        even if the agent steps through the entire orbit accumulating ground-pass communication."""
        sim = AuthoritativeOrbitSimulator(
            initial_soc_pct=70.0,
            initial_temp_c=14.0,
            initial_pointing_error_deg=5.0,
            task_id="rf_downlink_closure",
        )
        nominal_cfg = {
            "solar_efficiency": 0.72,
            "undervoltage_threshold": 3.20,
            "battery_capacity_wh": 40.0,
            "wheel_bias_rpm": 0.0,
            "pointing_deadband_deg": 0.05,
            "control_gain": 2.0,
            "battery_heater_setpoint": 15.0,
            "heater_hysteresis_c": 2.0,
            "thermal_cutoff_temp": 45.0,
            "tx_power_dbm": 30.0,
            "antenna_gain_dbi": 8.0,
            "path_loss_db": 141.5,
            "baudrate_bps": 9600.0,
        }
        # Step through the entire 95-minute orbit (including ground pass)
        sim.advance(5700.0, nominal_cfg, dt_s=60.0)
        self.assertGreaterEqual(sim.total_downlinked_kb, 350.0)

        # Finalize with script_verified=False (unverified / broken script)
        res = sim.finalize_orbit({**nominal_cfg, "script_verified": False})
        self.assertFalse(res.passed)
        self.assertEqual(res.reward["comms_closure"], 0.0)
        self.assertLess(res.reward["total"], 0.95)

    def test_forged_telemetry_ignored_by_verifier(self):
        """Verify that forging telemetry files in the workspace does not alter verifier score."""
        from harness.env import Env
        os.environ["HARNESS_SECRET"] = self.test_token
        env = Env(self.base_url)
        env.reset("eps_eclipse_recovery", 0)

        # Forge status and CSV pretending everything is 100% nominal
        fake_status = {
            "elapsed_minutes": 95.0,
            "bus_voltage_v": 4.20,
            "battery_soc_pct": 100.0,
            "battery_temp_c": 20.0,
            "pointing_error_deg": 0.0,
            "heater_active": False,
            "link_margin_db": 10.0,
        }
        fake_file = srv.WORKSPACE / "telemetry" / "current_status.json"
        fake_file.write_text(json.dumps(fake_status), encoding="utf-8")

        # Score before applying actual EPS fixes must still be 0.00
        score = env.score()
        self.assertEqual(score["total"], 0.0)

    def test_eval_script_leaves_no_artifacts_in_workspace(self):
        """Verify that temporary evaluation directories are located outside the agent's workspace."""
        headers = {
            "Content-Type": "application/json",
            "X-Harness-Secret": self.test_token,
        }
        req_reset = Request(
            f"{self.base_url}/reset",
            data=b'{"files": {"scripts/link_budget.py": "def calc_margin(): return 7.0\\n"}}',
            headers=headers,
        )
        urlopen(req_reset)

        eval_payload = json.dumps({
            "script_relpath": "scripts/link_budget.py",
            "test_cases": [{"subsystems/comms.cfg": "[comms]\n"}]
        }).encode()
        req_eval = Request(f"{self.base_url}/eval_script", data=eval_payload, headers=headers)
        urlopen(req_eval)

        # Check workspace contents: no .eval_* or aero_eval_* directories
        workspace_items = [p.name for p in srv.WORKSPACE.iterdir()]
        for item in workspace_items:
            self.assertFalse(item.startswith(".eval_"))
            self.assertFalse(item.startswith("aero_eval_"))

    def test_state_hash_tracks_physical_sim_state(self):
        """Verify that state_hash changes when simulation advances even with identical files."""
        from harness.env import Env
        os.environ["HARNESS_SECRET"] = self.test_token
        env = Env(self.base_url)
        env.reset("eps_eclipse_recovery", 0)

        hash1 = env.state_hash()
        env.step("step_orbit", {"minutes": 10})
        hash2 = env.state_hash()
        self.assertNotEqual(hash1, hash2, "State hash must reflect physical state changes")

    def test_orbital_mission_cap_enforced(self):
        """Verify that orbital stepping is capped at the 95-minute mission boundary."""
        from harness.env import Env
        os.environ["HARNESS_SECRET"] = self.test_token
        env = Env(self.base_url)
        env.reset("eps_eclipse_recovery", 0)

        # Step 90 minutes
        for _ in range(3):
            out1, err1 = env.step("step_orbit", {"minutes": 30})
        self.assertFalse(err1)

        # Attempt to step 30 more minutes (should cap at 95.0 total)
        out2, err2 = env.step("step_orbit", {"minutes": 30})
        self.assertFalse(err2)
        self.assertIn("95.0 min", out2)
        self.assertEqual(round(env.sim.time_s / 60.0, 1), 95.0)

        # Subsequent step_orbit should report orbit already complete
        out3, err3 = env.step("step_orbit", {"minutes": 10})
        self.assertIn("Mission evaluation orbit complete", out3)

    def test_malformed_ini_configuration_handling(self):
        """Verify that malformed INI files do not crash the verifier or environment."""
        from harness.env import Env
        os.environ["HARNESS_SECRET"] = self.test_token
        env = Env(self.base_url)
        env.reset("eps_eclipse_recovery", 0)

        # Write corrupted INI syntax to eps.cfg
        corrupted_content = "[unclosed_section\nsolar_efficiency = corrupt\n::bad_syntax::"
        env.step("write_file", {"path": "subsystems/eps.cfg", "content": corrupted_content})

        # Scoring must not crash with an uncaught exception
        score = env.score()
        self.assertIsInstance(score, dict)
        self.assertEqual(score["total"], 0.0)

    def test_alternative_valid_solutions_accepted(self):
        """Verify that an alternative physically valid configuration scores >= 0.95."""
        from harness.verifier import simulate_orbit
        res = simulate_orbit(
            solar_efficiency=0.75,
            undervoltage_threshold=3.25,
            wheel_bias_rpm=0.0,
            pointing_deadband_deg=0.04,
            control_gain=2.2,
            heater_setpoint_c=16.0,
            heater_hysteresis_c=2.0,
            thermal_cutoff_c=44.0,
            tx_power_dbm=30.0,
            antenna_gain_dbi=8.0,
            path_loss_db=141.5,
            initial_soc_pct=70.0,
            initial_temp_c=14.0,
            initial_pointing_error_deg=35.0,
            task_id="eps_eclipse_recovery",
            script_verified=True,
        )
        self.assertTrue(res.passed)
        self.assertGreaterEqual(res.reward["total"], 0.95)

    def test_file_size_and_workspace_limits(self):
        """Verify that file writes exceeding 250 KB or path depth > 10 are rejected."""
        # Oversized file
        oversized = "X" * (srv.MAX_FILE_BYTES + 1000)
        with self.assertRaises(ValueError):
            srv.tool_write_file({"path": "huge.bin", "content": oversized})

        # Excessive path depth
        deep_path = "a/b/c/d/e/f/g/h/i/j/k/l/m/file.txt"
        with self.assertRaises(ValueError):
            srv.safe_path(deep_path)

        # Excessive path length
        long_path = "a/" * 130 + "test.txt"
        with self.assertRaises(ValueError):
            srv.safe_path(long_path)

    def test_malformed_tool_requests(self):
        """Verify that unknown tools and invalid argument formats return error without crashing."""
        # Unknown tool
        res1 = srv.do_tool({"name": "nonexistent_tool", "args": {}})
        self.assertTrue(res1["is_error"])
        self.assertIn("unknown tool", res1["output"])

        # Invalid arguments for read_file
        res2 = srv.do_tool({"name": "read_file", "args": {}})
        self.assertTrue(res2["is_error"])

    def test_script_evaluation_caching(self):
        """Verify that repeated evaluation of the same script content utilizes the cache."""
        from harness.env import Env
        os.environ["HARNESS_SECRET"] = self.test_token
        env = Env(self.base_url)
        env.reset("rf_downlink_closure", 0)

        # First score performs verification
        env.score()
        cache_len_1 = len(env._eval_cache)
        calls_1 = env._eval_call_count

        # Second score on same state must reuse cache without extra sandbox calls
        env.score()
        cache_len_2 = len(env._eval_cache)
        calls_2 = env._eval_call_count

        self.assertEqual(cache_len_1, cache_len_2)
        self.assertEqual(calls_1, calls_2)


if __name__ == "__main__":
    unittest.main()
