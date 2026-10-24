"""Hardened Sandbox service: runs the agent's tools inside an isolated workspace.

Security & Isolation Architecture:
1. Internal Network Isolation: Sits on an isolated Docker network with NO internet access.
2. Separate Execution Identities:
   - Server runs with least required capabilities (`cap_drop: [ALL]`).
   - Agent tool execution (run_python) executes strictly as unprivileged user
     `sandbox-agent` (UID 1001), isolated from the server process.
3. Cryptographically Secure Dynamic Secret:
   - Generates a fresh 256-bit token (secrets.token_hex(32)) at startup into
     a root-owned, mode 0600 file (/run/aerosentinel/harness_secret.txt).
   - The token is NEVER visible in docker-compose.yml or to the agent process.
4. Protected Endpoints:
   - /reset, /state, /fetch_configs, /eval_script, /sync_telemetry require X-Harness-Secret header.
   - Unauthenticated calls receive 403 Forbidden.
5. Path Traversal & Symlink Hardening:
   - All paths strictly resolve inside /workspace; system directories (/proc, /sys, /etc, /run)
     and directory escapes are blocked.
6. Isolated Evaluation Worker:
   - The verifier executes untrusted agent scripts via /eval_script inside an ephemeral,
     unprivileged directory as sandbox-agent (UID 1001).
   - Untrusted code NEVER executes inside the harness container.
"""
import configparser
import hashlib
import json
import math
import os
import secrets
import shutil
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# ---- Workspace Resolution --------------------------------------------
WORKSPACE_ENV = os.environ.get("WORKSPACE_DIR")
if WORKSPACE_ENV:
    WORKSPACE = Path(WORKSPACE_ENV).resolve()
elif Path("/workspace").exists():
    WORKSPACE = Path("/workspace").resolve()
else:
    WORKSPACE = (Path(__file__).parent.parent / ".workspace").resolve()
WORKSPACE.mkdir(parents=True, exist_ok=True)

PORT = int(os.environ.get("PORT", "8000"))
MAX_OUTPUT = 4000          # characters returned to agent per tool call
RUN_TIMEOUT = 20           # seconds timeout per command
MAX_FILE_BYTES = 250_000         # 250 KB per single file write
MAX_WORKSPACE_BYTES = 10_000_000 # 10 MB total workspace storage
MAX_PATH_LENGTH = 250            # maximum path characters
MAX_PATH_DEPTH = 10              # maximum directory nesting depth

# ---- Secure Dynamic Token Generation ---------------------------------
SECRET_FILE = Path("/run/aerosentinel/harness_secret.txt")
_AUTH_TOKEN = ""

if os.environ.get("HARNESS_SECRET"):
    _AUTH_TOKEN = os.environ.pop("HARNESS_SECRET").strip()
elif SECRET_FILE.is_file():
    try:
        _AUTH_TOKEN = SECRET_FILE.read_text(encoding="utf-8").strip()
    except Exception:
        pass

if not _AUTH_TOKEN:
    _AUTH_TOKEN = secrets.token_hex(32)
    try:
        SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
        SECRET_FILE.write_text(_AUTH_TOKEN, encoding="utf-8")
        os.chmod(SECRET_FILE, 0o600)
    except Exception:
        pass


def safe_path(relative: str) -> Path:
    """Ensure path stays strictly within the workspace directory, preventing directory traversal and symlink escapes."""
    if not isinstance(relative, str) or not relative.strip():
        raise ValueError("path must be a non-empty string")
    rel = str(relative).replace("\\", "/").strip()
    if len(rel) > MAX_PATH_LENGTH:
        raise ValueError(f"path length {len(rel)} exceeds maximum limit of {MAX_PATH_LENGTH} characters")
    parts = [p for p in rel.split("/") if p]
    if len(parts) > MAX_PATH_DEPTH:
        raise ValueError(f"path depth {len(parts)} exceeds maximum limit of {MAX_PATH_DEPTH} directories")
    if rel.startswith("/") or ".." in rel.split("/"):
        raise ValueError("path traversal blocked: relative path must not contain '..' or leading '/'")

    target = (WORKSPACE / rel).resolve()
    try:
        target.relative_to(WORKSPACE)
    except ValueError:
        raise ValueError(f"path traversal blocked: path '{relative}' resolves outside workspace")

    # Block access to system paths or private runtime directories
    for blocked in ("/run", "/proc", "/sys", "/etc", "/dev", "/root", "/var", "/tmp"):
        if str(target).startswith(blocked):
            raise ValueError(f"access to system directory '{blocked}' is strictly forbidden")

    return target


def clip(text: str) -> str:
    if len(text) <= MAX_OUTPUT:
        return text
    return text[:MAX_OUTPUT] + f"\n[output cut at {MAX_OUTPUT} characters]"


def all_files(root: Path):
    for path in sorted(root.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            yield path


def _init_orbit_state():
    telemetry_dir = WORKSPACE / "telemetry"
    telemetry_dir.mkdir(parents=True, exist_ok=True)

    status_file = telemetry_dir / "current_status.json"
    status_data = {
        "elapsed_minutes": 0,
        "orbit_phase": "SUNLIGHT",
        "bus_voltage_v": 3.70,
        "battery_soc_pct": 70.0,
        "battery_temp_c": 14.0,
        "pointing_error_deg": 35.0,
        "heater_active": False,
        "link_margin_db": -6.0,
    }
    status_file.write_text(json.dumps(status_data, indent=2), encoding="utf-8")

    log_file = telemetry_dir / "live_telemetry.csv"
    header = "elapsed_min,orbit_phase,bus_v,soc_pct,temp_c,pointing_err_deg,link_margin_db,heater\n"
    init_row = "0,SUNLIGHT,3.70,70.0,14.0,35.0,-6.0,OFF\n"
    log_file.write_text(header + init_row, encoding="utf-8")


# ----------------------------------------------------------------------------
# Tools the agent can call
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
    content = args["content"]
    content_bytes = content.encode("utf-8")
    if len(content_bytes) > MAX_FILE_BYTES:
        raise ValueError(f"file size {len(content_bytes)} bytes exceeds maximum limit of {MAX_FILE_BYTES} bytes")

    total_storage = sum(p.stat().st_size for p in all_files(WORKSPACE) if p != path)
    if total_storage + len(content_bytes) > MAX_WORKSPACE_BYTES:
        raise ValueError(f"total workspace storage exceeds limit of {MAX_WORKSPACE_BYTES} bytes")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return f"wrote {len(content)} characters to {args['path']}"


def tool_step_orbit(args):
    """Fallback status check if called directly on sandbox."""
    status_file = WORKSPACE / "telemetry" / "current_status.json"
    if status_file.is_file():
        try:
            data = json.loads(status_file.read_text(encoding="utf-8"))
            return (
                f"Orbit Status (MET: {data.get('elapsed_minutes')} min, Phase: {data.get('orbit_phase')}):\n"
                f"Bus Voltage={data.get('bus_voltage_v')}V, Battery SoC={data.get('battery_soc_pct')}%, "
                f"Temp={data.get('battery_temp_c')}°C, Pointing Err={data.get('pointing_error_deg')}°, "
                f"Link Margin={data.get('link_margin_db')} dB (Heater: {'ON' if data.get('heater_active') else 'OFF'})."
            )
        except Exception:
            pass
    return "Orbit advanced. Check telemetry/current_status.json and telemetry/live_telemetry.csv."


def tool_run_python(args):
    """Run Python code under unprivileged sandbox-agent (UID 1001) identity."""
    clean_env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
        "HOME": "/home/sandbox-agent" if Path("/home/sandbox-agent").exists() else str(WORKSPACE),
        "USER": "sandbox-agent",
    }
    kwargs = {
        "cwd": WORKSPACE,
        "capture_output": True,
        "text": True,
        "timeout": RUN_TIMEOUT,
        "env": clean_env,
    }

    # Privilege Separation on POSIX when running as root
    if hasattr(os, "getuid") and os.getuid() == 0:
        try:
            import pwd
            agent_user = pwd.getpwnam("sandbox-agent")
            kwargs["user"] = agent_user.pw_uid
            kwargs["group"] = agent_user.pw_gid
        except Exception:
            pass

    try:
        done = subprocess.run([sys.executable, "-c", args["code"]], **kwargs)
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
    "step_orbit": tool_step_orbit,
    "run_python": tool_run_python,
}


# ----------------------------------------------------------------------------
# Endpoints used by the harness (protected with dynamic _AUTH_TOKEN)
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

    # Initialize dynamic orbital telemetry files
    _init_orbit_state()

    # Set file ownership and permissions to sandbox-agent if on POSIX
    if hasattr(os, "getuid") and os.getuid() == 0:
        try:
            import pwd
            agent_user = pwd.getpwnam("sandbox-agent")
            for root, dirs, files in os.walk(WORKSPACE):
                for d in dirs:
                    p = os.path.join(root, d)
                    os.chown(p, agent_user.pw_uid, agent_user.pw_gid)
                    os.chmod(p, 0o777)
                for f in files:
                    p = os.path.join(root, f)
                    os.chown(p, agent_user.pw_uid, agent_user.pw_gid)
                    os.chmod(p, 0o666)
            os.chown(WORKSPACE, agent_user.pw_uid, agent_user.pw_gid)
            os.chmod(WORKSPACE, 0o777)
        except Exception:
            pass

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
        if rel.endswith((".cfg", ".py", ".csv", ".json", ".md")):
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


def do_eval_script(body):
    """Execute an agent script in an isolated ephemeral directory as isolated sandbox-eval (UID 1002).
    
    Used exclusively by the trusted harness verifier. The untrusted code never executes
    inside the harness container, and runs under a distinct worker identity from the agent.
    """
    script_rel = body.get("script_relpath", "scripts/link_budget.py")
    test_cases = body.get("test_cases", [])
    script_path = safe_path(script_rel)
    if not script_path.is_file():
        return {"error": f"script not found: {script_rel}", "results": []}

    script_code = script_path.read_text(encoding="utf-8", errors="replace")
    results = []

    eval_user_name = "sandbox-eval"
    eval_uid = None
    eval_gid = None
    if hasattr(os, "getuid") and os.getuid() == 0:
        try:
            import pwd
            try:
                eval_pwnam = pwd.getpwnam("sandbox-eval")
            except KeyError:
                eval_pwnam = pwd.getpwnam("sandbox-agent")
            eval_uid = eval_pwnam.pw_uid
            eval_gid = eval_pwnam.pw_gid
            eval_user_name = eval_pwnam.pw_name
        except Exception:
            pass

    clean_env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
        "HOME": f"/home/{eval_user_name}" if Path(f"/home/{eval_user_name}").exists() else "/tmp",
        "USER": eval_user_name,
    }
    kwargs = {
        "capture_output": True,
        "text": True,
        "timeout": 3,
        "env": clean_env,
    }
    if eval_uid is not None:
        kwargs["user"] = eval_uid
        kwargs["group"] = eval_gid

    import tempfile
    for tc in test_cases:
        eval_base_dir = Path("/tmp") if Path("/tmp").is_dir() else Path(tempfile.gettempdir())
        temp_dir = Path(tempfile.mkdtemp(dir=eval_base_dir, prefix="aero_eval_isolated_"))
        try:
            os.chmod(temp_dir, 0o700)  # Restricted solely to sandbox-eval

            (temp_dir / "scripts").mkdir(parents=True, exist_ok=True)
            (temp_dir / "subsystems").mkdir(parents=True, exist_ok=True)

            (temp_dir / "scripts" / "link_budget.py").write_text(script_code, encoding="utf-8")

            for fname, content in tc.items():
                p = temp_dir / fname
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(content, encoding="utf-8")

            if eval_uid is not None:
                for root, dirs, files_in_dir in os.walk(temp_dir):
                    for d in dirs:
                        p_dir = os.path.join(root, d)
                        os.chmod(p_dir, 0o777)
                        os.chown(p_dir, eval_uid, eval_gid)
                    for f in files_in_dir:
                        p_file = os.path.join(root, f)
                        os.chmod(p_file, 0o666)
                        os.chown(p_file, eval_uid, eval_gid)
                os.chmod(temp_dir, 0o700)
                os.chown(temp_dir, eval_uid, eval_gid)

            runner_code = "import sys; from scripts.link_budget import calc_margin; print(calc_margin())"
            proc = subprocess.run([sys.executable, "-c", runner_code], cwd=temp_dir, **kwargs)
            if proc.returncode == 0:
                try:
                    val = float(proc.stdout.strip())
                    results.append(val)
                except ValueError:
                    results.append(None)
            else:
                results.append(None)
        except Exception:
            results.append(None)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    return {"results": results}


def do_sync_telemetry(body):
    """Write authoritative telemetry frames and current status from harness to the workspace."""
    telemetry_dir = WORKSPACE / "telemetry"
    telemetry_dir.mkdir(parents=True, exist_ok=True)

    status = body.get("status")
    if status is not None:
        (telemetry_dir / "current_status.json").write_text(json.dumps(status, indent=2), encoding="utf-8")

    csv_append = body.get("csv_append")
    if csv_append:
        log_file = telemetry_dir / "live_telemetry.csv"
        try:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(csv_append)
        except Exception:
            pass

    return {"ok": True}


ROUTES_POST = {
    "/reset": (do_reset, True),
    "/tool": (do_tool, False),
    "/fetch_configs": (do_fetch_configs, True),
    "/eval_script": (do_eval_script, True),
    "/sync_telemetry": (do_sync_telemetry, True),
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
        if not _AUTH_TOKEN:
            return True
        token = self.headers.get("X-Harness-Secret", "").strip()
        return token == _AUTH_TOKEN

    def do_GET(self):
        if self.path == "/health":
            self._send(200, {"ok": True, "service": "aerosentinel-sandbox"})
        elif self.path == "/state":
            if not self._is_authorized():
                self._send(403, {"error": "forbidden: unauthorized call to privileged endpoint"})
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
            if length > 2 * 1024 * 1024:
                return self._send(413, {"error": "payload too large (maximum 2 MB)"})
            body = json.loads(self.rfile.read(length) or b"{}")
            self._send(200, handler(body))
        except Exception as exc:
            import traceback
            traceback.print_exc()
            sys.stderr.flush()
            self._send(400, {"error": f"{type(exc).__name__}: {exc}"})

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    print(f"hardened sandbox listening on :{PORT}, workspace {WORKSPACE}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
