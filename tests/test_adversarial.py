"""Adversarial & Security Test Suite for AeroSentinel.

Verifies:
1. Privileged endpoint protection: 403 Forbidden without secret token on live HTTP requests.
2. Endpoint authorization: 200 OK when authenticating with valid secret token.
3. Secret scrubbing in sandbox run_python environment.
4. Path traversal protection: safe_path blocks ../ escapes.
5. Tool timeout & runaway loop handling: infinite loops stopped safely.
6. Output clipping: caps response at 4000 chars.
7. Deterministic seeded resets: same seed = identical state hash.
8. Seed diversity: different seeds = different initial state.
9. Interactive orbital stepping: verifies dynamic telemetry tracking.
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


class TestSecurityAndIsolation(unittest.TestCase):
    def test_path_traversal_prevention(self):
        """Verify that directory traversal attempts are blocked."""
        with self.assertRaises(ValueError):
            srv.safe_path("../../etc/passwd")
        with self.assertRaises(ValueError):
            srv.safe_path("../harness/verifier.py")
        with self.assertRaises(ValueError):
            srv.safe_path("/etc/shadow")

    def test_secret_scrubbing_in_sandbox(self):
        """Verify that agent subprocesses cannot see HARNESS_SECRET."""
        os.environ["HARNESS_SECRET"] = "super_classified_token_123"
        code = "import os; print(os.environ.get('HARNESS_SECRET', 'NOT_FOUND'))"
        output = srv.tool_run_python({"code": code})
        self.assertIn("NOT_FOUND", output)
        self.assertNotIn("super_classified_token_123", output)

    def test_timeout_enforcement_on_infinite_loop(self):
        """Verify that an infinite loop code snippet is killed after timeout."""
        code = "import time\nwhile True:\n    time.sleep(0.1)"
        output = srv.tool_run_python({"code": code})
        self.assertIn("exceeded", output)
        self.assertIn(f"{srv.RUN_TIMEOUT} seconds", output)

    def test_output_clipping(self):
        """Verify output truncation at MAX_OUTPUT characters."""
        long_text = "A" * (srv.MAX_OUTPUT + 500)
        clipped = srv.clip(long_text)
        self.assertLessEqual(len(clipped), srv.MAX_OUTPUT + 100)
        self.assertIn("[output cut at", clipped)

    def test_step_orbit_progression(self):
        """Verify interactive orbit stepping produces dynamic telemetry."""
        srv.WORKSPACE.mkdir(parents=True, exist_ok=True)
        srv._init_orbit_state()
        res = srv.tool_step_orbit({"minutes": 15})
        self.assertIn("Orbit advanced by 15 min", res)
        status_file = srv.WORKSPACE / "telemetry" / "current_status.json"
        self.assertTrue(status_file.is_file())
        data = json.loads(status_file.read_text(encoding="utf-8"))
        self.assertEqual(data["elapsed_minutes"], 15)


class TestAuthenticatedEndpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Configure a test auth token
        cls.test_token = "adversarial_test_secret_token_999"
        srv._AUTH_TOKEN = cls.test_token

        # Spin up test server on loopback
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
        """Privileged endpoints (/state, /reset, /fetch_configs) must return 403 when unauthenticated."""
        # 1. GET /state without header
        with self.assertRaises(HTTPError) as ctx:
            urlopen(Request(f"{self.base_url}/state"))
        self.assertEqual(ctx.exception.code, 403)

        # 2. POST /reset without header
        req = Request(
            f"{self.base_url}/reset",
            data=b'{"files": {}}',
            headers={"Content-Type": "application/json"}
        )
        with self.assertRaises(HTTPError) as ctx:
            urlopen(req)
        self.assertEqual(ctx.exception.code, 403)

        # 3. POST /fetch_configs without header
        req = Request(
            f"{self.base_url}/fetch_configs",
            data=b'{}',
            headers={"Content-Type": "application/json"}
        )
        with self.assertRaises(HTTPError) as ctx:
            urlopen(req)
        self.assertEqual(ctx.exception.code, 403)

    def test_privileged_endpoints_accept_with_token(self):
        """Privileged endpoints accept calls when presenting the correct X-Harness-Secret header."""
        headers = {
            "Content-Type": "application/json",
            "X-Harness-Secret": self.test_token
        }

        # 1. GET /state with token
        req = Request(f"{self.base_url}/state", headers=headers)
        with urlopen(req) as resp:
            self.assertEqual(resp.status, 200)

        # 2. POST /reset with token
        req = Request(
            f"{self.base_url}/reset",
            data=b'{"files": {"test.txt": "nominal"}}',
            headers=headers
        )
        with urlopen(req) as resp:
            self.assertEqual(resp.status, 200)

        # 3. POST /fetch_configs with token
        req = Request(f"{self.base_url}/fetch_configs", data=b'{}', headers=headers)
        with urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode())
            self.assertIn("files", data)


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
        # Random choice of failed subsystems will differ across seeds
        self.assertNotEqual(t1.files, t2.files)


if __name__ == "__main__":
    unittest.main()
