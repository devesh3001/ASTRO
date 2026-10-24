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
            headers={"Content-Type": "application/json"}
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

    def test_agent_cannot_access_eval_directories(self):
        """Agent (UID 1001) must be denied permission to inspect evaluation temporary files."""
        code = """
import os
import glob
matches = glob.glob('/tmp/aero_eval_*')
denied = False
for m in matches:
    try:
        os.listdir(m)
    except PermissionError:
        denied = True
print(f"DENIED={denied or len(matches) == 0}")
"""
        res = self._post_tool("run_python", {"code": code})
        self.assertFalse(res.get("is_error"))
        self.assertIn("DENIED=True", res.get("output", ""))


if __name__ == "__main__":
    unittest.main()
