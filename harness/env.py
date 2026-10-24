"""The environment: reset / step / score, backed by the hardened sandbox service.

All verifier logic and physics calculations run securely inside the harness.
The agent cannot inspect, modify, or intercept grading logic.
"""
import configparser
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, Any

import requests

from .tasks import build_task
from .verifier import simulate_orbit

PASS_SCORE = 0.95   # physical orbit survival & mission completion threshold


def _verify_link_budget_behaviour(files: Dict[str, str]) -> bool:
    """Dynamic behavioural verifier: executes link_budget.py against synthesized test configs.
    
    Verifies that the script genuinely imports and reads `path_loss_db` from `subsystems/comms.cfg`
    and computes the Friis link margin dynamically, preventing simple comment or string loopholes.
    """
    script_content = files.get("scripts/link_budget.py", files.get("comms/link_budget.py", ""))
    if not script_content:
        return False

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / "subsystems").mkdir(parents=True, exist_ok=True)
        (root / "scripts").mkdir(parents=True, exist_ok=True)
        (root / "scripts" / "link_budget.py").write_text(script_content, encoding="utf-8")

        cmd = [sys.executable, "-c", "import sys; from scripts.link_budget import calc_margin; print(calc_margin())"]

        # Test Vector 1: Nominal orbital parameters
        # tx=30.0, gain=8.0, path_loss=141.5 -> Expected margin = 10.0 dB
        cfg1 = "[comms]\ntx_power_dbm = 30.0\nantenna_gain_dbi = 8.0\npath_loss_db = 141.5\n"
        (root / "subsystems" / "comms.cfg").write_text(cfg1, encoding="utf-8")
        try:
            p1 = subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=5)
            if p1.returncode != 0:
                return False
            m1 = float(p1.stdout.strip())
            if abs(m1 - 10.0) > 0.15:
                return False
        except Exception:
            return False

        # Test Vector 2: Dynamic variation test
        # tx=25.0, gain=6.0, path_loss=135.0 -> Expected margin = (25 + 6 + 16) - 135 - 2.5 + 100 = 9.5 dB
        cfg2 = "[comms]\ntx_power_dbm = 25.0\nantenna_gain_dbi = 6.0\npath_loss_db = 135.0\n"
        (root / "subsystems" / "comms.cfg").write_text(cfg2, encoding="utf-8")
        try:
            p2 = subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=5)
            if p2.returncode != 0:
                return False
            m2 = float(p2.stdout.strip())
            if abs(m2 - 9.5) > 0.15:
                return False
        except Exception:
            return False

        return True


class Env:
    def __init__(self, sandbox_url: str, tool_timeout: int = 60, verify_timeout: int = 120):
        self.url = sandbox_url.rstrip("/")
        self.tool_timeout = tool_timeout
        self.verify_timeout = verify_timeout
        self.secret = os.environ.get("HARNESS_SECRET", "").strip()

        # Dynamic secret retrieval from shared volume if unset
        if not self.secret:
            secret_file = Path("/run/aerosentinel/harness_secret.txt")
            if secret_file.is_file():
                try:
                    self.secret = secret_file.read_text(encoding="utf-8").strip()
                except Exception:
                    pass

        self.task = None

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.secret:
            headers["X-Harness-Secret"] = self.secret
        return headers

    def _post(self, path: str, payload: dict, timeout: int):
        response = requests.post(
            self.url + path,
            json=payload,
            headers=self._headers(),
            timeout=timeout
        )
        if not response.ok:
            print(f"DEBUG HTTP ERROR: POST {path} returned {response.status_code}: {response.text}", flush=True)
        response.raise_for_status()
        return response.json()

    # ---------------------------------------------------------------- reset
    def reset(self, task_id: str, seed: int) -> str:
        """Start a clean episode. The same (task_id, seed) always gives the same initial world."""
        self.task = build_task(task_id, seed)
        self._post("/reset", {"files": self.task.files}, self.tool_timeout)
        return (
            f"MISSION CONTROLLER ALERT: {self.task.instructions}\n\n"
            "The onboard flight computer filesystem is accessible in the workspace.\n"
            "Review `docs/flight_manual.md` for subsystem engineering limits and telemetry guides.\n"
            "Use the provided tools to inspect telemetry, modify subsystem configurations, "
            "verify behavior with `step_orbit` or `run_python`, then call `finish` to initiate orbital validation."
        )

    # ----------------------------------------------------------------- step
    def step(self, name: str, args: dict):
        """Run one tool call. Returns (text, is_error)."""
        result = self._post("/tool", {"name": name, "args": args}, self.tool_timeout)
        return result["output"], result["is_error"]

    def state_hash(self) -> str:
        response = requests.get(
            self.url + "/state",
            headers=self._headers(),
            timeout=self.tool_timeout
        )
        files = response.json().get("files", {})
        return hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()[:8]

    # ---------------------------------------------------------------- score
    def score(self) -> Dict[str, Any]:
        """Programmatic verifier: runs isolated orbital physics simulation on harness.
        
        Reads the satellite configuration files from the sandbox, validates engineering
        invariants, and runs a 95-minute LEO orbit simulation to test actual survival.
        """
        response = self._post("/fetch_configs", {}, self.tool_timeout)
        files = response.get("files", {})

        # Parse configurations with safe defaults
        eps_cfg = configparser.ConfigParser()
        eps_cfg.read_string(files.get("subsystems/eps.cfg", files.get("eps.cfg", "")))

        adcs_cfg = configparser.ConfigParser()
        adcs_cfg.read_string(files.get("subsystems/adcs.cfg", files.get("adcs.cfg", "")))

        thermal_cfg = configparser.ConfigParser()
        thermal_cfg.read_string(files.get("subsystems/thermal.cfg", files.get("thermal.cfg", "")))

        comms_cfg = configparser.ConfigParser()
        comms_cfg.read_string(files.get("subsystems/comms.cfg", files.get("comms.cfg", "")))

        def safe_float(parser, section, key, default):
            try:
                return float(parser[section][key])
            except Exception:
                return default

        solar_efficiency = safe_float(eps_cfg, "power", "solar_efficiency", 0.40)
        undervoltage_threshold = safe_float(eps_cfg, "power", "undervoltage_threshold", 2.80)
        wheel_bias_rpm = safe_float(adcs_cfg, "attitude", "wheel_bias_rpm", 120.0)
        pointing_deadband_deg = safe_float(adcs_cfg, "attitude", "pointing_deadband_deg", 0.50)
        control_gain = safe_float(adcs_cfg, "attitude", "control_gain", 1.0)
        heater_setpoint_c = safe_float(thermal_cfg, "thermal", "battery_heater_setpoint", 65.0)
        heater_hysteresis_c = safe_float(thermal_cfg, "thermal", "heater_hysteresis_c", 2.0)
        thermal_cutoff_c = safe_float(thermal_cfg, "thermal", "thermal_cutoff_temp", 35.0)
        tx_power_dbm = safe_float(comms_cfg, "comms", "tx_power_dbm", 20.0)
        antenna_gain_dbi = safe_float(comms_cfg, "comms", "antenna_gain_dbi", 2.0)
        path_loss_db = safe_float(comms_cfg, "comms", "path_loss_db", 141.5)

        # Dynamic behavioural verification of link budget script
        script_behavior_ok = _verify_link_budget_behaviour(files)

        # In tasks involving comms, if link_budget.py fails dynamic behavioural testing, penalize path loss
        effective_path_loss = path_loss_db
        if self.task and self.task.task_id in ("rf_downlink_closure", "safe_mode_cascade"):
            if "scripts/link_budget.py" in self.task.files or "scripts/link_budget.py" in files:
                if not script_behavior_ok:
                    effective_path_loss = 175.0  # unresolved link calculation penalty

        # Run orbital physics simulation
        sim_result = simulate_orbit(
            solar_efficiency=solar_efficiency,
            undervoltage_threshold=undervoltage_threshold,
            wheel_bias_rpm=wheel_bias_rpm,
            pointing_deadband_deg=pointing_deadband_deg,
            control_gain=control_gain,
            heater_setpoint_c=heater_setpoint_c,
            heater_hysteresis_c=heater_hysteresis_c,
            thermal_cutoff_c=thermal_cutoff_c,
            tx_power_dbm=tx_power_dbm,
            antenna_gain_dbi=antenna_gain_dbi,
            path_loss_db=effective_path_loss,
            initial_soc_pct=self.task.initial_soc_pct if self.task else 70.0,
            initial_temp_c=self.task.initial_temp_c if self.task else 14.0,
            initial_pointing_error_deg=self.task.initial_pointing_error_deg if self.task else 35.0,
            task_id=self.task.task_id if self.task else "",
        )

        return sim_result.reward
