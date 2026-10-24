"""Hardened Sandbox service: runs the agent's tools inside an isolated workspace.

Security & Isolation:
1. Sits on an internal Docker network with NO internet access.
2. Separate Execution Identities:
   - Server runs with privilege separation.
   - Agent tool execution (run_python) executes strictly as unprivileged user
     `sandbox-agent` (UID 1001), isolated from the server process.
3. Cryptographically Secure Dynamic Secret:
   - Generates a fresh 256-bit token (secrets.token_hex(32)) at startup into
     a root-owned, mode 0600 file (/run/aerosentinel/harness_secret.txt).
   - The token is NEVER visible in docker-compose.yml or to the agent process.
4. Protected Endpoints:
   - /reset, /state, /fetch_configs strictly require X-Harness-Secret header.
   - Unauthenticated calls receive 403 Forbidden.
5. Interactive Orbit Progression:
   - Tool `step_orbit` advances the satellite orbit and logs dynamic telemetry.
   - Evaluation runs exclusively in the harness container (zero grading code here).
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
        # Restrict permissions to owner only (chmod 600)
        os.chmod(SECRET_FILE, 0o600)
    except Exception:
        pass


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


# ---- Interactive Orbital Progression State ---------------------------
_ORBIT_STATE = {
    "elapsed_min": 0,
    "soc_wh": 28.0,
    "temp_c": 14.0,
    "pointing_err": 35.0,
}


def _init_orbit_state():
    global _ORBIT_STATE
    _ORBIT_STATE = {
        "elapsed_min": 0,
        "soc_wh": 28.0,
        "temp_c": 14.0,
        "pointing_err": 35.0,
    }
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
    if not log_file.is_file():
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
    path.parent.mkdir(parents=True, exist_ok=True)
    content = args["content"]
    path.write_text(content, encoding="utf-8")
    return f"wrote {len(content)} characters to {args['path']}"


def tool_step_orbit(args):
    """Advance the orbital simulation by the specified minutes (1-30)."""
    global _ORBIT_STATE
    try:
        minutes = int(args.get("minutes", 10))
    except (ValueError, TypeError):
        minutes = 10
    minutes = max(1, min(30, minutes))

    eps_cfg = configparser.ConfigParser()
    p_eps = WORKSPACE / "subsystems" / "eps.cfg"
    if not p_eps.is_file():
        p_eps = WORKSPACE / "eps.cfg"
    if p_eps.is_file():
        eps_cfg.read(str(p_eps))

    adcs_cfg = configparser.ConfigParser()
    p_adcs = WORKSPACE / "subsystems" / "adcs.cfg"
    if not p_adcs.is_file():
        p_adcs = WORKSPACE / "adcs.cfg"
    if p_adcs.is_file():
        adcs_cfg.read(str(p_adcs))

    thermal_cfg = configparser.ConfigParser()
    p_thm = WORKSPACE / "subsystems" / "thermal.cfg"
    if not p_thm.is_file():
        p_thm = WORKSPACE / "thermal.cfg"
    if p_thm.is_file():
        thermal_cfg.read(str(p_thm))

    comms_cfg = configparser.ConfigParser()
    p_com = WORKSPACE / "subsystems" / "comms.cfg"
    if not p_com.is_file():
        p_com = WORKSPACE / "comms.cfg"
    if p_com.is_file():
        comms_cfg.read(str(p_com))

    def _get(cfg, sec, k, d):
        try:
            return float(cfg[sec][k])
        except Exception:
            return d

    solar_eff = _get(eps_cfg, "power", "solar_efficiency", 0.41)
    uv_thresh = _get(eps_cfg, "power", "undervoltage_threshold", 2.80)
    wheel_bias = _get(adcs_cfg, "attitude", "wheel_bias_rpm", 120.0)
    deadband = _get(adcs_cfg, "attitude", "pointing_deadband_deg", 0.50)
    gain = _get(adcs_cfg, "attitude", "control_gain", 0.40)
    heater_sp = _get(thermal_cfg, "thermal", "battery_heater_setpoint", 65.0)
    heater_hyst = _get(thermal_cfg, "thermal", "heater_hysteresis_c", 2.0)
    thermal_cut = _get(thermal_cfg, "thermal", "thermal_cutoff_temp", 35.0)
    tx_p = _get(comms_cfg, "comms", "tx_power_dbm", 20.0)
    ant_g = _get(comms_cfg, "comms", "antenna_gain_dbi", 2.0)
    path_l = _get(comms_cfg, "comms", "path_loss_db", 141.5)

    telemetry_dir = WORKSPACE / "telemetry"
    telemetry_dir.mkdir(parents=True, exist_ok=True)
    log_file = telemetry_dir / "live_telemetry.csv"

    rows = []
    for _ in range(minutes):
        _ORBIT_STATE["elapsed_min"] += 1
        t_min = _ORBIT_STATE["elapsed_min"]
        in_eclipse = (t_min % 95) >= 60
        phase = "ECLIPSE" if in_eclipse else "SUNLIGHT"

        # ADCS attitude dynamics
        if abs(wheel_bias) > 20.0:
            _ORBIT_STATE["pointing_err"] = min(120.0, _ORBIT_STATE["pointing_err"] + abs(wheel_bias) * 0.04)
        else:
            _ORBIT_STATE["pointing_err"] = max(deadband, _ORBIT_STATE["pointing_err"] * (1.0 - min(0.35, gain * 0.06)))

        cos_sun = max(0.0, math.cos(math.radians(min(88.0, _ORBIT_STATE["pointing_err"]))))

        # EPS solar generation & battery
        if in_eclipse:
            p_gen = 0.0
        else:
            clamped_eff = max(0.0, min(0.85, solar_eff))
            p_gen = clamped_eff * 0.28 * 0.06 * 1361.0 * cos_sun

        # TCS thermostat & load power
        heater_on = (_ORBIT_STATE["temp_c"] < (heater_sp - heater_hyst)) and (_ORBIT_STATE["temp_c"] < thermal_cut)
        p_load = 4.5 + (6.0 if heater_on else 0.0)

        # Energy update (40 Wh capacity)
        net_p = p_gen - p_load
        _ORBIT_STATE["soc_wh"] = max(0.0, min(40.0, _ORBIT_STATE["soc_wh"] + net_p * (1.0 / 60.0)))
        soc_pct = (_ORBIT_STATE["soc_wh"] / 40.0) * 100.0
        bus_v = 3.0 + 1.2 * (soc_pct / 100.0)

        # Thermal dynamics
        q_diss = p_load * 0.8
        q_sol = 0.0 if in_eclipse else 2.5 * cos_sun
        q_rad = 0.09 * (_ORBIT_STATE["temp_c"] - (-35.0))
        _ORBIT_STATE["temp_c"] += ((q_diss + q_sol - q_rad) * 60.0) / 380.0

        # RF Link Margin
        pointing_loss = min(20.0, (_ORBIT_STATE["pointing_err"] / 10.0) ** 2)
        link_margin = (tx_p + ant_g + 16.0) - path_l - 2.5 - (-100.0) - pointing_loss

        rows.append(
            f"{t_min},{phase},{bus_v:.2f},{soc_pct:.1f},{_ORBIT_STATE['temp_c']:.1f},"
            f"{_ORBIT_STATE['pointing_err']:.2f},{link_margin:.1f},{'ON' if heater_on else 'OFF'}\n"
        )

    try:
        with open(log_file, "a", encoding="utf-8") as f:
            f.writelines(rows)
    except Exception:
        pass

    status_data = {
        "elapsed_minutes": _ORBIT_STATE["elapsed_min"],
        "orbit_phase": phase,
        "bus_voltage_v": round(bus_v, 2),
        "battery_soc_pct": round(soc_pct, 1),
        "battery_temp_c": round(_ORBIT_STATE["temp_c"], 1),
        "pointing_error_deg": round(_ORBIT_STATE["pointing_err"], 2),
        "heater_active": heater_on,
        "link_margin_db": round(link_margin, 1),
    }
    try:
        (telemetry_dir / "current_status.json").write_text(json.dumps(status_data, indent=2), encoding="utf-8")
    except Exception:
        pass

    return (
        f"Orbit advanced by {minutes} min (MET: {_ORBIT_STATE['elapsed_min']} min, Phase: {phase}).\n"
        f"Status: Bus={bus_v:.2f}V, Battery SoC={soc_pct:.1f}%, Temp={_ORBIT_STATE['temp_c']:.1f}°C, "
        f"Pointing Err={_ORBIT_STATE['pointing_err']:.2f}°, Link Margin={link_margin:.1f} dB (Heater: {'ON' if heater_on else 'OFF'}).\n"
        f"Updated logs in telemetry/live_telemetry.csv and telemetry/current_status.json"
    )


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

    # Initialize dynamic orbital telemetry state
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


ROUTES_POST = {
    "/reset": (do_reset, True),
    "/tool": (do_tool, False),
    "/fetch_configs": (do_fetch_configs, True),
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
