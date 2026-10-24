"""Live Docker Security & Isolation Validation Suite.

Verifies against a live or running Docker sandbox container:
1. Process identity: tool executions run under UID 1001 (sandbox-agent), not UID 0 (root).
2. Secret protection: HARNESS_SECRET is not visible in environment or process table.
3. Protected mount isolation: /run/aerosentinel cannot be read or inspected by UID 1001.
4. Path traversal & symlink breakout protection: safe_path rejects escapes.
5. Network containment: sandbox container has zero outbound internet access.
6. Execution timeouts: runaway loops killed within configured timeout.
"""
import json
import os
import sys
import unittest
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class TestDockerSecurity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sandbox_url = os.environ.get("SANDBOX_URL", "http://localhost:8000").rstrip("/")
        cls.token = os.environ.get("HARNESS_SECRET", "").strip()
        secret = Path("/run/aerosentinel/harness_secret.txt")
        if not cls.token and secret.is_file():
            cls.token = secret.read_text().strip()
        # Verify sandbox is reachable
        try:
            req = Request(f"{cls.sandbox_url}/health")
            with urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read().decode())
                cls.sandbox_active = data.get("ok", False)
        except Exception:
            cls.sandbox_active = False

        if not cls.sandbox_active and (os.environ.get("CI") or os.environ.get("FAIL_ON_UNAVAILABLE") == "1"):
            raise RuntimeError(f"FATAL: Docker sandbox not reachable at {cls.sandbox_url}; failing security suite.")

    def setUp(self):
        if not self.sandbox_active:
            if os.environ.get("CI") or os.environ.get("FAIL_ON_UNAVAILABLE") == "1":
                self.fail(f"Docker sandbox not reachable at {self.sandbox_url}")
            self.skipTest(f"Docker sandbox not reachable at {self.sandbox_url}; skipping live container test.")

    def _post_tool(self, name: str, args: dict):
        req = Request(
            f"{self.sandbox_url}/tool",
            data=json.dumps({"name": name, "args": args}).encode(),
            headers={"Content-Type": "application/json", "X-Harness-Secret": self.token}
        )
        with urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode())

    def test_container_process_identity(self):
        """Tool subprocesses must execute as unprivileged UID 1001 (sandbox-agent)."""
        code = "import os; print(f'UID={os.getuid() if hasattr(os, \"getuid\") else 1001}')"
        res = self._post_tool("run_python", {"code": code})
        self.assertFalse(res.get("is_error"))
        # In Linux container, UID must be 1001
        output = res.get("output", "")
        if "UID=" in output:
            self.assertIn("UID=1001", output)

    def test_container_secret_protection(self):
        """HARNESS_SECRET must never be present in the agent execution environment."""
        code = "import os; print('SECRET=' + str(os.environ.get('HARNESS_SECRET', 'NONE')))"
        res = self._post_tool("run_python", {"code": code})
        self.assertFalse(res.get("is_error"))
        self.assertIn("SECRET=NONE", res.get("output", ""))

    def test_container_protected_mount_access_denied(self):
        """Agent code must not be able to read /run/aerosentinel or harness secret token."""
        code = """
import os
try:
    with open('/run/aerosentinel/harness_secret.txt', 'r') as f:
        print('LEAKED=' + f.read().strip())
except Exception as e:
    print('DENIED=' + type(e).__name__)
"""
        res = self._post_tool("run_python", {"code": code})
        self.assertFalse(res.get("is_error"))
        output = res.get("output", "")
        self.assertNotIn("LEAKED=", output)
        self.assertIn("DENIED=", output)

    def test_container_outbound_network_blocked(self):
        """Agent workspace must have no outbound internet connection."""
        code = """
import urllib.request
try:
    urllib.request.urlopen('http://1.1.1.1', timeout=2)
    print('NET=OPEN')
except Exception as e:
    print('NET=BLOCKED')
"""
        res = self._post_tool("run_python", {"code": code})
        self.assertFalse(res.get("is_error"))
        self.assertIn("NET=BLOCKED", res.get("output", ""))

    def test_container_filesystem_traversal_blocked(self):
        """Path traversal ../ must be rejected by safe_path in sandbox."""
        res = self._post_tool("read_file", {"path": "../../etc/passwd"})
        self.assertTrue(res.get("is_error"))
        out = res.get("output", "").lower()
        self.assertTrue("traversal" in out or "escapes" in out)

    def test_eval_worker_separate_uid(self):
        """Evaluation worker must run as separate UID 1002 sandbox-eval, distinct from agent UID 1001."""
        code = """
import os
import pwd
eval_user = pwd.getpwnam("sandbox-eval")
print(f"EVAL_UID={eval_user.pw_uid}")
"""
        res = self._post_tool("run_python", {"code": code})
        self.assertFalse(res.get("is_error"))
        self.assertIn("EVAL_UID=1002", res.get("output", ""))

    def environment(self):
        from harness.env import Env
        env = Env(self.sandbox_url)
        env.secret = self.token
        return env

    def test_eval_worker_actual_identity_and_secret_denial(self):
        env = self.environment()
        script = "def calc_margin():\n import os\n try:\n  open('/run/aerosentinel/harness_secret.txt').read()\n  return -1\n except PermissionError:\n  return os.getuid()\n"
        env._post("/reset", {"files": {"scripts/link_budget.py": script}}, 60)
        result = env._post("/eval_script", {"script_relpath": "scripts/link_budget.py", "test_cases": [{}]}, 60)
        self.assertEqual(result["results"], [1002])
        result = self._post_tool("run_python", {"code": "import glob; print(len(glob.glob('/tmp/aero_eval_*')))"})
        self.assertIn("0", result["output"])

    def test_background_process_cannot_contaminate_reset(self):
        import time
        env = self.environment()
        env.reset("eps_eclipse_recovery", 0)
        child = "import time; from pathlib import Path; time.sleep(2); Path('/workspace/episode_leak.txt').write_text('leak')"
        code = "import subprocess,sys; subprocess.Popen([sys.executable,'-c'," + repr(child) + "],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True); print('spawned')"
        result = self._post_tool("run_python", {"code": code})
        self.assertFalse(result["is_error"], result)
        env.reset("eps_eclipse_recovery", 0)
        first = env.state_hash()
        time.sleep(3)
        self.assertEqual(first, env.state_hash())
        self.assertTrue(self._post_tool("read_file", {"path": "episode_leak.txt"})["is_error"])

    def test_python_cannot_bypass_file_limit(self):
        env = self.environment()
        env.reset("eps_eclipse_recovery", 0)
        result = self._post_tool("run_python", {"code": "from pathlib import Path; Path('huge.bin').write_bytes(b'x'*251000)"})
        self.assertTrue(result["is_error"], result)
        env.reset("eps_eclipse_recovery", 0)

    def test_output_flood_is_bounded(self):
        result = self._post_tool("run_python", {"code": "while True: print('X'*4000)"})
        self.assertTrue(result["is_error"], result)
        self.assertLess(len(result["output"]), 4200)

    def test_agent_cannot_call_tool_endpoint_directly(self):
        code = "import urllib.request, urllib.error; req=urllib.request.Request('http://localhost:8000/tool',data=b'{}');\ntry: urllib.request.urlopen(req)\nexcept urllib.error.HTTPError as e: print(e.code)"
        result = self._post_tool("run_python", {"code": code})
        self.assertIn("403", result["output"])


if __name__ == "__main__":
    unittest.main()
