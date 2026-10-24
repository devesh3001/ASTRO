"""The environment: reset / step / score, backed by the hardened sandbox service.

All verifier logic, authoritative episode state, and physics calculations run securely inside the harness.
Untrusted agent Python code is NEVER executed inside the harness container; behavioural validation
is executed via isolated unprivileged workers inside the sandbox container.
"""
import configparser
import hashlib
import json
import os
import random
from pathlib import Path
from typing import Dict, Any

import requests

from .tasks import build_task
from .verifier import (
    AuthoritativeOrbitSimulator,
    BATTERY_CAPACITY_WH,
    SimulationResult,
)

PASS_SCORE = 0.95   # physical orbit survival & mission completion threshold


def _verify_link_budget_behaviour(files: Dict[str, str]) -> bool:
    """Helper used for offline physics validation of reference and no-op configurations."""
    content = files.get("scripts/link_budget.py", files.get("comms/link_budget.py", ""))
    if not content:
        return False
    import ast
    try:
        tree = ast.parse(content)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value == "path_loss_db":
                return True
        return False
    except Exception:
        return False



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
        self.sim: AuthoritativeOrbitSimulator = None
        self.seed = 0
        self._eval_cache: Dict[str, bool] = {}
        self._eval_call_count: int = 0

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

    def _parse_subsystem_configs(self, files: Dict[str, str]) -> Dict[str, float]:
        """Extract all subsystem parameters with fallback defaults and graceful error recovery."""
        def safe_read(raw: str) -> configparser.ConfigParser:
            parser = configparser.ConfigParser()
            if raw and raw.strip():
                try:
                    parser.read_string(raw)
                except (configparser.Error, UnicodeDecodeError, ValueError):
                    pass
            return parser

        eps_cfg = safe_read(files.get("subsystems/eps.cfg", files.get("eps.cfg", "")))
        adcs_cfg = safe_read(files.get("subsystems/adcs.cfg", files.get("adcs.cfg", "")))
        thermal_cfg = safe_read(files.get("subsystems/thermal.cfg", files.get("thermal.cfg", "")))
        comms_cfg = safe_read(files.get("subsystems/comms.cfg", files.get("comms.cfg", "")))

        def safe_float(parser, section, key, default):
            try:
                return float(parser[section][key])
            except Exception:
                return default

        return {
            "solar_efficiency": safe_float(eps_cfg, "power", "solar_efficiency", 0.40),
            "undervoltage_threshold": safe_float(eps_cfg, "power", "undervoltage_threshold", 2.80),
            "battery_capacity_wh": safe_float(eps_cfg, "power", "battery_capacity_wh", 40.0),
            "wheel_bias_rpm": safe_float(adcs_cfg, "attitude", "wheel_bias_rpm", 120.0),
            "pointing_deadband_deg": safe_float(adcs_cfg, "attitude", "pointing_deadband_deg", 0.50),
            "control_gain": safe_float(adcs_cfg, "attitude", "control_gain", 1.0),
            "battery_heater_setpoint": safe_float(thermal_cfg, "thermal", "battery_heater_setpoint", 65.0),
            "heater_hysteresis_c": safe_float(thermal_cfg, "thermal", "heater_hysteresis_c", 2.0),
            "thermal_cutoff_temp": safe_float(thermal_cfg, "thermal", "thermal_cutoff_temp", 35.0),
            "tx_power_dbm": safe_float(comms_cfg, "comms", "tx_power_dbm", 20.0),
            "antenna_gain_dbi": safe_float(comms_cfg, "comms", "antenna_gain_dbi", 2.0),
            "path_loss_db": safe_float(comms_cfg, "comms", "path_loss_db", 141.5),
            "baudrate_bps": safe_float(comms_cfg, "comms", "baudrate_bps", 9600.0),
        }

    # ---------------------------------------------------------------- reset
    def reset(self, task_id: str, seed: int) -> str:
        """Start a clean episode. The same (task_id, seed) always gives the same initial world."""
        self.task = build_task(task_id, seed)
        self.seed = seed
        self._eval_cache.clear()
        self._eval_call_count = 0
        self._post("/reset", {"files": self.task.files}, self.tool_timeout)

        # Initialize the single authoritative episode multi-physics simulator
        self.sim = AuthoritativeOrbitSimulator(
            initial_soc_pct=self.task.initial_soc_pct,
            initial_temp_c=self.task.initial_temp_c,
            initial_pointing_error_deg=self.task.initial_pointing_error_deg,
            task_id=self.task.task_id,
        )

        return (
            f"MISSION CONTROLLER ALERT: {self.task.instructions}\n\n"
            "The onboard flight computer filesystem is accessible in the workspace.\n"
            "Review `docs/flight_manual.md` for subsystem engineering limits and telemetry guides.\n"
            "Use the provided tools to inspect telemetry, modify subsystem configurations, "
            "verify behavior with `step_orbit` or `run_python`, then call `finish` to initiate orbital validation."
        )

    def _sync_sim_telemetry(self, frames: list):
        """Update current_status.json and live_telemetry.csv in sandbox after physics evolution."""
        if not frames or not self.sim:
            return
        last_frame = frames[-1]
        status_payload = {
            "elapsed_minutes": round(self.sim.time_s / 60.0, 1),
            "orbit_phase": "ECLIPSE" if last_frame.eclipse else "SUNLIGHT",
            "bus_voltage_v": last_frame.bus_voltage_v,
            "battery_soc_pct": last_frame.battery_soc_pct,
            "battery_temp_c": last_frame.battery_temp_c,
            "pointing_error_deg": last_frame.pointing_error_deg,
            "heater_active": last_frame.heater_active,
            "link_margin_db": last_frame.link_margin_db,
        }
        csv_lines = "".join(
            f"{round(f.time_s/60.0, 1)},{'ECLIPSE' if f.eclipse else 'SUNLIGHT'},"
            f"{f.bus_voltage_v},{f.battery_soc_pct},{f.battery_temp_c},"
            f"{f.pointing_error_deg},{f.link_margin_db},{'ON' if f.heater_active else 'OFF'}\n"
            for f in frames
        )
        try:
            self._post("/sync_telemetry", {"status": status_payload, "csv_append": csv_lines}, self.tool_timeout)
        except Exception:
            pass

    # ----------------------------------------------------------------- step
    def step(self, name: str, args: dict):
        """Run one tool call. Returns (text, is_error).
        
        Interprets step_orbit on the authoritative simulator and advances virtual time
        on all tools so actions incur physical consequences. Capped at 95-minute mission boundary.
        """
        # Fetch current configs for physics evolution
        cfg_resp = self._post("/fetch_configs", {}, self.tool_timeout)
        configs = self._parse_subsystem_configs(cfg_resp.get("files", {}))

        if name == "step_orbit":
            time_left_s = max(0.0, 5700.0 - self.sim.time_s)
            if time_left_s <= 0.0:
                return (
                    "Mission evaluation orbit complete (MET: 95.0 min). The designated 95-minute observation "
                    "window has concluded. Call `finish` to initiate final orbital scoring."
                ), False

            try:
                minutes = int(args.get("minutes", 10))
            except (ValueError, TypeError):
                minutes = 10
            advance_s = min(max(1, minutes) * 60.0, time_left_s)
            actual_min = round(advance_s / 60.0, 1)

            frames = self.sim.advance(advance_s, configs, dt_s=60.0)
            self._sync_sim_telemetry(frames)

            last_frame = frames[-1] if frames else None
            if last_frame:
                output = (
                    f"Orbit advanced by {actual_min} min (MET: {round(self.sim.time_s/60.0, 1)} min, "
                    f"Phase: {'ECLIPSE' if last_frame.eclipse else 'SUNLIGHT'}).\n"
                    f"Status: Bus={last_frame.bus_voltage_v}V, Battery SoC={last_frame.battery_soc_pct}%, "
                    f"Temp={last_frame.battery_temp_c}°C, Pointing Err={last_frame.pointing_error_deg}°, "
                    f"Link Margin={last_frame.link_margin_db} dB (Heater: {'ON' if last_frame.heater_active else 'OFF'}).\n"
                    f"Updated telemetry in telemetry/live_telemetry.csv and telemetry/current_status.json"
                )
                if round(self.sim.time_s / 60.0, 1) >= 95.0:
                    output += "\n[NOTICE]: 95.0-minute mission boundary reached. Call `finish` to finalize."
                if self.sim.catastrophic_failure:
                    output += f"\n[CRITICAL WARNING]: Irreversible safety violation triggered: {self.sim.failure_reason}"
                return output, False

        # Regular tools consume 30 seconds of virtual OBC execution time (capped at 95 min)
        result = self._post("/tool", {"name": name, "args": args}, self.tool_timeout)
        if self.sim and self.sim.time_s < 5700.0:
            cfg_resp = self._post("/fetch_configs", {}, self.tool_timeout)
            configs = self._parse_subsystem_configs(cfg_resp.get("files", {}))
            advance_s = min(30.0, 5700.0 - self.sim.time_s)
            frames = self.sim.advance(advance_s, configs, dt_s=30.0)
            self._sync_sim_telemetry(frames)

        return result["output"], result["is_error"]

    def state_hash(self) -> str:
        response = requests.get(
            self.url + "/state",
            headers=self._headers(),
            timeout=self.tool_timeout
        )
        files = response.json().get("files", {})
        state_payload = {"files": files}
        if self.sim:
            state_payload["sim"] = {
                "time_s": round(self.sim.time_s, 2),
                "soc_wh": round(self.sim.soc_wh, 4),
                "temp_c": round(self.sim.temp_c, 3),
                "pointing_err_deg": round(self.sim.pointing_err_deg, 3),
                "downlinked_kb": round(self.sim.total_downlinked_kb, 3),
                "catastrophic_failure": bool(self.sim.catastrophic_failure),
                "failure_reason": str(self.sim.failure_reason),
                "heater_on": bool(self.sim.heater_on),
                "min_bus_v": round(self.sim.min_bus_v, 4),
                "max_temp_c": round(self.sim.max_temp_c, 3),
                "min_temp_c": round(self.sim.min_temp_c, 3),
                "max_pointing_err_deg": round(self.sim.max_pointing_err_deg, 3),
                "min_link_margin_db": round(self.sim.min_link_margin_db, 3),
            }
        return hashlib.sha256(json.dumps(state_payload, sort_keys=True).encode()).hexdigest()[:8]

    # ---------------------------------------------------------------- score
    def score(self) -> Dict[str, Any]:
        """Programmatic verifier: scores the authoritative episode trajectory and completes the orbit.
        
        Evaluates behavioural execution via isolated worker inside sandbox (zero harness execution),
        validates physical parameters, and computes the authoritative outcome-based reward.
        """
        response = self._post("/fetch_configs", {}, self.tool_timeout)
        files = response.get("files", {})
        configs = self._parse_subsystem_configs(files)

        # Dynamic behavioural verification of link budget script on unprivileged sandbox worker
        if self.task and self.task.task_id in ("rf_downlink_closure", "safe_mode_cascade"):
            if "scripts/link_budget.py" in self.task.files or "scripts/link_budget.py" in files:
                script_content = files.get("scripts/link_budget.py", "")
                script_hash = hashlib.sha256(script_content.encode("utf-8")).hexdigest()
                cache_key = f"{script_hash}:{self.seed}"

                if cache_key in self._eval_cache:
                    behavior_ok = self._eval_cache[cache_key]
                elif self._eval_call_count >= 12:
                    # Strict cumulative evaluation limit to protect 10-minute budget
                    behavior_ok = False
                else:
                    self._eval_call_count += 1
                    # Generate 3 randomized dynamic test vectors
                    rng = random.Random(self.seed ^ 0x5F3759DF)
                    test_cases = []
                    expected_margins = []
                    for _ in range(3):
                        t_tx = round(rng.uniform(22.0, 32.0), 1)
                        t_gain = round(rng.uniform(5.0, 15.0), 1)
                        t_loss = round(rng.uniform(130.0, 150.0), 1)
                        cfg_str = f"[comms]\ntx_power_dbm = {t_tx}\nantenna_gain_dbi = {t_gain}\npath_loss_db = {t_loss}\n"
                        test_cases.append({"subsystems/comms.cfg": cfg_str})
                        exp_m = round((t_tx + t_gain + 16.0) - t_loss - 2.5 - (-100.0), 2)
                        expected_margins.append(exp_m)

                    eval_payload = {
                        "script_relpath": "scripts/link_budget.py",
                        "test_cases": test_cases,
                    }
                    try:
                        eval_res = self._post("/eval_script", eval_payload, self.tool_timeout)
                        actual_results = eval_res.get("results", [])
                        behavior_ok = (len(actual_results) == 3 and all(
                            r is not None and abs(r - exp) < 0.15
                            for r, exp in zip(actual_results, expected_margins)
                        ))
                    except Exception:
                        behavior_ok = False

                    self._eval_cache[cache_key] = behavior_ok

                configs["script_verified"] = behavior_ok
                if not behavior_ok:
                    configs["path_loss_db"] = 175.0  # unresolved calculation penalty

        # Finalize projected orbit simulation through the 95-minute period
        if not self.sim:
            self.sim = AuthoritativeOrbitSimulator(
                initial_soc_pct=self.task.initial_soc_pct if self.task else 70.0,
                initial_temp_c=self.task.initial_temp_c if self.task else 14.0,
                initial_pointing_error_deg=self.task.initial_pointing_error_deg if self.task else 35.0,
                task_id=self.task.task_id if self.task else "",
            )

        import copy
        sim_eval = copy.deepcopy(self.sim)
        sim_result: SimulationResult = sim_eval.finalize_orbit(configs)
        return sim_result.reward
