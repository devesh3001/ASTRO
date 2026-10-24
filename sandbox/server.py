"""Authenticated sandbox service with bounded, separate agent and evaluation workers.

The root service uses explicit capabilities for identity switching, file ownership
and worker cleanup. The protected verifier runs in the other container.
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
import threading
from sandbox import runtime
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
_AUTH_TOKEN = os.environ.pop("HARNESS_SECRET", "").strip() or secrets.token_hex(32)
_LOCK = threading.RLock()

def publish_secret():
    SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
    SECRET_FILE.write_text(_AUTH_TOKEN, encoding="utf-8")
    os.chmod(SECRET_FILE, 0o600)


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


def all_files(root):
    yield from runtime.files(root)


def tool_list_files(args):
    files = [str(p.relative_to(WORKSPACE)).replace("\\", "/") for p in all_files(WORKSPACE)]
    return "\n".join(files) if files else "(no files)"


def tool_read_file(args):
    return clip(runtime.read(WORKSPACE, args["path"]).decode("utf-8", errors="replace"))


def tool_write_file(args):
    runtime.check_storage(WORKSPACE)
    content = args["content"].encode("utf-8")
    path = safe_path(args["path"])
    old_size = path.stat().st_size if path.exists() else 0
    if sum(p.stat().st_size for p in all_files(WORKSPACE)) - old_size + len(content) > MAX_WORKSPACE_BYTES:
        raise ValueError("total workspace storage exceeds limit")
    runtime.write(WORKSPACE, args["path"], content)
    return f"wrote {len(content)} bytes to {args['path']}"


def tool_run_python(args):
    try:
        code, output = runtime.execute(args["code"], WORKSPACE, seconds=RUN_TIMEOUT, workspace=WORKSPACE)
        return clip(("error: " if code else "") + (output.strip() or "(no output)") + f"\n[exit code {code}]")
    except Exception as exc:
        return f"error: {type(exc).__name__}: {exc}"


TOOLS = {
    "list_files": tool_list_files,
    "read_file": tool_read_file,
    "write_file": tool_write_file,
    "run_python": tool_run_python,
}


# ----------------------------------------------------------------------------
# Endpoints used by the harness (protected with dynamic _AUTH_TOKEN)
# ----------------------------------------------------------------------------
def do_reset(body):
    runtime.kill_workers()
    for child in WORKSPACE.iterdir():
        if child.is_symlink() or not child.is_dir():
            child.unlink()
        else:
            shutil.rmtree(child)
    if os.name == "posix" and os.getuid() == 0:
        os.chown(WORKSPACE, *runtime.identity("sandbox-agent"))
        os.chmod(WORKSPACE, 0o755)
        worker_uids = {runtime.identity(n)[0] for n in ("sandbox-agent", "sandbox-eval")}
        for child in Path("/tmp").iterdir():
            if child.lstat().st_uid in worker_uids:
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child)
                else:
                    child.unlink()
    for name, content in body["files"].items():
        runtime.write(WORKSPACE, name, content.encode("utf-8"))
    if body.get("status") is not None:
        do_sync_telemetry({"status": body["status"], "replace": True})
    runtime.check_storage(WORKSPACE)
    return {"ok": True}


def do_tool(body):
    name, args = body.get("name"), body.get("args", {})
    if name not in TOOLS:
        return {"output": f"error: unknown tool '{name}'", "is_error": True}
    try:
        output = TOOLS[name](args)
        return {"output": output, "is_error": output.startswith("error:")}
    except Exception as exc:
        return {"output": f"error: {exc}", "is_error": True}


def do_fetch_configs(_body=None):
    runtime.check_storage(WORKSPACE)
    files = {}
    for name in [f"subsystems/{n}.cfg" for n in ("eps", "adcs", "thermal", "comms")] + ["scripts/link_budget.py"]:
        try:
            files[name] = runtime.read(WORKSPACE, name).decode("utf-8", errors="replace")
        except FileNotFoundError:
            pass
    return {"files": files}


def do_state(_body=None):
    runtime.check_storage(WORKSPACE)
    return {"files": {p.relative_to(WORKSPACE).as_posix(): hashlib.sha256(
        runtime.read(WORKSPACE, p.relative_to(WORKSPACE).as_posix())).hexdigest() for p in all_files(WORKSPACE)}}


def do_eval_script(body):
    script = runtime.read(WORKSPACE, body.get("script_relpath", "scripts/link_budget.py"))
    cases = body.get("test_cases", [])
    if not isinstance(cases, list) or len(cases) > 8:
        raise ValueError("at most eight evaluation cases allowed")
    uid, gid = runtime.identity("sandbox-eval")
    import tempfile
    results = []
    for case in cases:
        with tempfile.TemporaryDirectory(prefix="aero_eval_") as directory:
            root = Path(directory)
            os.chmod(root, 0o700)
            (root / "scripts").mkdir()
            (root / "subsystems").mkdir()
            (root / "scripts/link_budget.py").write_bytes(script)
            for name, content in case.items():
                if name != "subsystems/comms.cfg":
                    raise ValueError("unexpected fixture path")
                (root / name).write_text(content, encoding="utf-8")
            if uid is not None:
                for path in [root, *root.rglob("*")]:
                    os.chown(path, uid, gid)
                    os.chmod(path, 0o700 if path.is_dir() else 0o600)
            try:
                code, output = runtime.execute("from scripts.link_budget import calc_margin; print(calc_margin())",
                                               root, "sandbox-eval", seconds=3)
                value = float(output.strip()) if code == 0 else None
                results.append(value if value is not None and math.isfinite(value) else None)
            except Exception:
                results.append(None)
    return {"results": results}


def do_sync_telemetry(body):
    if body.get("status") is not None:
        status = body["status"]
        runtime.write(WORKSPACE, "telemetry/current_status.json", json.dumps(status, indent=2).encode())
        if body.get("replace"):
            header = "elapsed_min,orbit_phase,bus_v,soc_pct,temp_c,pointing_err_deg,link_margin_db,heater\n"
            row = f"0,SUNLIGHT,{status['bus_voltage_v']},{status['battery_soc_pct']},{status['battery_temp_c']},{status['pointing_error_deg']},{status['link_margin_db']},OFF\n"
            runtime.write(WORKSPACE, "telemetry/live_telemetry.csv", (header + row).encode())
    if body.get("csv_append"):
        runtime.write(WORKSPACE, "telemetry/live_telemetry.csv", body["csv_append"].encode(), append=True)
    return {"ok": True}


ROUTES_POST = {
    "/reset": (do_reset, True),
    "/tool": (do_tool, True),
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
        token = self.headers.get("X-Harness-Secret", "").strip()
        return bool(_AUTH_TOKEN) and secrets.compare_digest(token, _AUTH_TOKEN)

    def do_GET(self):
        if self.path == "/health":
            self._send(200, {"ok": True, "service": "aerosentinel-sandbox", "build_sha256": hashlib.sha256((Path(__file__).read_bytes() + Path(runtime.__file__).read_bytes()).replace(b"\r\n", b"\n")).hexdigest()})
        elif self.path == "/state":
            if not self._is_authorized():
                self._send(403, {"error": "forbidden: unauthorized call to privileged endpoint"})
                return
            try:
                with _LOCK:
                    self._send(200, do_state())
            except Exception as exc:
                self._send(400, {"error": str(exc)})
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
            self.connection.settimeout(5)
            length = int(self.headers.get("Content-Length", 0))
            if length < 0 or length > 2 * 1024 * 1024:
                return self._send(413, {"error": "payload too large (maximum 2 MB)"})
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise ValueError("body must be an object")
            with _LOCK:
                self._send(200, handler(body))
        except Exception as exc:
            import traceback
            traceback.print_exc()
            sys.stderr.flush()
            self._send(400, {"error": f"{type(exc).__name__}: {exc}"})

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    runtime.identity("sandbox-agent")
    runtime.identity("sandbox-eval")
    publish_secret()
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    print(f"hardened sandbox listening on :{PORT}, workspace {WORKSPACE}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
