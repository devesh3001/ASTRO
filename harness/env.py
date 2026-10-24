"""The environment: reset / step / score, backed by the hardened sandbox service.

All verifier logic and physics calculations run securely inside the harness.
The agent cannot inspect, modify, or intercept grading logic.
"""
import configparser
import hashlib
import json
import os
from typing import Dict, Any

import requests

from .tasks import build_task
from .verifier import simulate_orbit

PASS_SCORE = 0.95   # physical orbit survival & mission completion threshold


class Env:
    def __init__(self, sandbox_url: str, tool_timeout: int = 60, verify_timeout: int = 120):
        self.url = sandbox_url.rstrip("/")
        self.tool_timeout = tool_timeout
        self.verify_timeout = verify_timeout
        self.secret = os.environ.get("HARNESS_SECRET", "").strip()
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
            "verify behavior, then call `finish` to initiate orbital validation."
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
        path_loss_db = safe_float(comms_cfg, "comms", "path_loss_db", 35.0)

        # Check Python link budget script fix if comms task
        script_content = files.get("scripts/link_budget.py", files.get("comms/link_budget.py", ""))
        script_fixed = ("cfg['comms']['path_loss_db']" in script_content or 
                        'cfg["comms"]["path_loss_db"]' in script_content or
                        "path_loss_db" in script_content)

        # In task 4 (rf_downlink_closure), if link_budget.py wasn't fixed, penalize path loss
        effective_path_loss = path_loss_db
        if self.task and self.task.task_id == "rf_downlink_closure" and not script_fixed:
            effective_path_loss = 120.0  # unresolved hardcoded penalty

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
            initial_temp_c=self.task.initial_temp_c if self.task else 12.0,
            initial_pointing_error_deg=self.task.initial_pointing_error_deg if self.task else 40.0,
            task_id=self.task.task_id if self.task else "",
        )

        return sim_result.reward
