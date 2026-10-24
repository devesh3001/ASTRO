"""Hardened Sandbox service: runs the agent's tools inside an isolated workspace.

Security & Isolation:
1. Sits on an internal Docker network with NO internet access.
2. Privileged operations (/reset, /state, /fetch_configs) require a secure transport
   authorization token (HARNESS_SECRET) via the X-Harness-Secret header.
3. Untrusted agent code execution (run_python) strips all secrets from child environment.
4. The /verify endpoint is REMOVED: evaluation and physics simulation run exclusively
   inside the harness container, completely out of reach of the agent.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

WORKSPACE_ENV = os.environ.get("WORKSPACE_DIR")
if WORKSPACE_ENV:
    WORKSPACE = Path(WORKSPACE_ENV).resolve()
elif Path("/workspace").exists():
    WORKSPACE = Path("/workspace").resolve()
else:
    WORKSPACE = (Path(__file__).parent.parent / ".workspace").resolve()
WORKSPACE.mkdir(parents=True, exist_ok=True)
PORT = int(os.environ.get("PORT", "8000"))
HARNESS_SECRET = os.environ.get("HARNESS_SECRET", "").strip()
MAX_OUTPUT = 4000          # characters returned to the agent per tool call
RUN_TIMEOUT = 20           # seconds for one run_python call


def safe_path(relative: str) -> Path:
    """Ensure path stays strictly within the workspace directory."""
    path = (WORKSPACE / relative).resolve()
    if path != WORKSPACE and WORKSPACE not in path.parents:
        raise ValueError("path traversal blocked: path must stay inside workspace")
    return path


def clip(text: str) -> str:
    if len(text) <= MAX_OUTPUT:
        return text
    return text[:MAX_OUTPUT] + f"\n[output cut at {MAX_OUTPUT} characters]"


def all_files(root: Path):
    for path in sorted(root.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            yield path


# ----------------------------------------------------------------------------
# tools the agent can call
# ----------------------------------------------------------------------------
def tool_list_files(args):
    files = [str(p.relative_to(WORKSPACE)).replace("\\", "/") for p in all_files(WORKSPACE)]
    return "\n".join(files) if files else "(no files)"


def tool_read_file(args):
    path = safe_path(args["path"])
    if not path.is_file():
        raise FileNotFoundError(f"no such file: {args['path']}")
    return clip(path.read_text(encoding="utf-8", errors="replace"))


def tool_write_file(args):
    path = safe_path(args["path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    content = args["content"]
    path.write_text(content, encoding="utf-8")
    return f"wrote {len(content)} characters to {args['path']}"


def tool_run_python(args):
    """Run Python code in a scrubbed subprocess environment (no secrets, no harness access)."""
    clean_env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
        "HOME": "/home/sandbox",
        "USER": "sandbox",
    }
    try:
        done = subprocess.run(
            [sys.executable, "-c", args["code"]],
            cwd=WORKSPACE,
            capture_output=True,
            text=True,
            timeout=RUN_TIMEOUT,
            env=clean_env,
        )
    except subprocess.TimeoutExpired:
        return f"error: execution exceeded {RUN_TIMEOUT} seconds limit and was terminated"
    except Exception as exc:
        return f"error: execution failed ({type(exc).__name__}: {exc})"

    output = (done.stdout + done.stderr).strip() or "(no output)"
    return clip(f"{output}\n[exit code {done.returncode}]")


TOOLS = {
    "list_files": tool_list_files,
    "read_file": tool_read_file,
    "write_file": tool_write_file,
    "run_python": tool_run_python,
}


# ----------------------------------------------------------------------------
# endpoints used by the harness (protected with HARNESS_SECRET)
# ----------------------------------------------------------------------------
def do_reset(body):
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    for child in WORKSPACE.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
    for name, content in body["files"].items():
        path = safe_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return {"ok": True}


def do_tool(body):
    name, args = body.get("name"), body.get("args", {})
    if name not in TOOLS:
        return {"output": f"error: unknown tool '{name}'", "is_error": True}
    try:
        output = TOOLS[name](args)
        return {"output": output, "is_error": False}
    except Exception as exc:
        return {"output": f"error: {exc}", "is_error": True}


def do_fetch_configs(_body=None):
    """Retrieve workspace config files for protected harness-side physics simulation."""
    configs = {}
    for p in all_files(WORKSPACE):
        rel = str(p.relative_to(WORKSPACE)).replace("\\", "/")
        if rel.endswith((".cfg", ".py", ".csv", ".json")):
            try:
                configs[rel] = p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                pass
    return {"files": configs}


def do_state(_body=None):
    files = {
        str(p.relative_to(WORKSPACE)).replace("\\", "/"): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in all_files(WORKSPACE)
    }
    return {"files": files}


ROUTES_POST = {
    "/reset": (do_reset, True),             # (handler, requires_auth)
    "/tool": (do_tool, False),              # agent tool dispatch (safe)
    "/fetch_configs": (do_fetch_configs, True), # harness state extraction (requires_auth)
}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, payload):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _is_authorized(self) -> bool:
        """Verify request comes from harness using shared secret token."""
        if not HARNESS_SECRET:
            return True  # no secret configured
        token = self.headers.get("X-Harness-Secret", "").strip()
        return token == HARNESS_SECRET

    def do_GET(self):
        if self.path == "/health":
            self._send(200, {"ok": True, "service": "aerosentinel-sandbox"})
        elif self.path == "/state":
            if not self._is_authorized():
                self._send(403, {"error": "forbidden: invalid or missing harness authorization token"})
                return
            self._send(200, do_state())
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        route = ROUTES_POST.get(self.path)
        if route is None:
            return self._send(404, {"error": "endpoint not found"})
        handler, requires_auth = route
        if requires_auth and not self._is_authorized():
            return self._send(403, {"error": "forbidden: unauthorized call to privileged endpoint"})
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            self._send(200, handler(body))
        except Exception as exc:
            self._send(400, {"error": f"{type(exc).__name__}: {exc}"})

    def log_message(self, *args):
        pass  # keep logs clean


if __name__ == "__main__":
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    print(f"hardened sandbox listening on :{PORT}, workspace {WORKSPACE}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
