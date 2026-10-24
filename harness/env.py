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
import time
import copy
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Any

import requests

from .tasks import build_task
from .verifier import (
    AuthoritativeOrbitSimulator,
    BATTERY_CAPACITY_WH,
    SimulationResult,
    link_margin_db,
    validate_subsystem_configs,
)

PASS_SCORE = 0.95   # physical orbit survival & mission completion threshold


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
        self.deadline = None
        self.baseline = None
        self.last_verifier = {}

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.secret:
            headers["X-Harness-Secret"] = self.secret
        return headers

    @contextmanager
    def budget(self, seconds):
        previous = self.deadline
        self.deadline = min(previous or float("inf"), time.monotonic() + seconds)
        try:
            yield
        finally:
            self.deadline = previous

    def _remaining(self):
        remaining = (self.deadline or (time.monotonic() + 60)) - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("operation deadline exceeded")
        return remaining

    def _request(self, method, path, payload=None):
        remaining = self._remaining()
        with requests.request(method, self.url + path, json=payload, headers=self._headers(),
                              stream=True, timeout=(min(5, remaining), min(self.tool_timeout, remaining))) as response:
            response.raise_for_status()
            chunks, size = [], 0
            for chunk in response.iter_content(8192):
                self._remaining()
                size += len(chunk)
                if size > 2_000_000:
                    raise ValueError("sandbox response exceeds size limit")
                chunks.append(chunk)
            self._remaining()
            return json.loads(b"".join(chunks))

    def _post(self, path, payload, timeout):
        with self.budget(timeout):
            return self._request("POST", path, payload)

    def _parse_subsystem_configs(self, files: Dict[str, str]) -> Dict[str, float]:
        """Parse declared controls; mandatory malformed values cannot silently become nominal."""
        config_errors = []
        def safe_read(raw: str) -> configparser.ConfigParser:
            parser = configparser.ConfigParser()
            if raw and raw.strip():
                try:
                    parser.read_string(raw)
                except (configparser.Error, UnicodeDecodeError, ValueError):
                    config_errors.append("Malformed subsystem configuration")
            return parser

        eps_cfg = safe_read(files.get("subsystems/eps.cfg", files.get("eps.cfg", "")))
        adcs_cfg = safe_read(files.get("subsystems/adcs.cfg", files.get("adcs.cfg", "")))
        thermal_cfg = safe_read(files.get("subsystems/thermal.cfg", files.get("thermal.cfg", "")))
        comms_cfg = safe_read(files.get("subsystems/comms.cfg", files.get("comms.cfg", "")))

        def safe_float(parser, section, key, default):
            try:
                return float(parser[section][key])
            except Exception:
                if key not in ("payload_duty_cycle", "baudrate_bps") or parser.has_option(section,key):
                    config_errors.append(f"Missing or non-numeric {section}.{key}")
                return default

        parsed = {
            "solar_efficiency": safe_float(eps_cfg, "power", "solar_efficiency", 0.40),
            "payload_duty_cycle": safe_float(eps_cfg, "power", "payload_duty_cycle", 0.0),
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
            "frequency_mhz": safe_float(comms_cfg, "comms", "frequency_mhz", 437.5),
        }
        parsed["_config_errors"] = config_errors
        return parsed

    # ---------------------------------------------------------------- reset
    def reset(self, task_id: str, seed: int) -> str:
        """Start a clean episode. The same (task_id, seed) always gives the same initial world."""
        self.task = build_task(task_id, seed)
        self.seed = seed
        self._eval_cache.clear()
        self._eval_call_count = 0
        self.baseline = None

        # Initialize the single authoritative episode multi-physics simulator
        self.sim = AuthoritativeOrbitSimulator(
            initial_soc_pct=self.task.initial_soc_pct,
            initial_temp_c=self.task.initial_temp_c,
            initial_pointing_error_deg=self.task.initial_pointing_error_deg,
            task_id=self.task.task_id,
            mission=self.task.mission,
        )
        configs = self._parse_subsystem_configs(self.task.files)
        status = self._initial_status(configs)
        self._post("/reset", {"files": self.task.files, "status": status}, self.tool_timeout)
        self.baseline = self.score()["raw_components"]

        return (
            f"MISSION CONTROLLER ALERT: {self.task.instructions}\n\n"
            "The onboard flight computer filesystem is accessible in the workspace.\n"
            "Review `docs/flight_manual.md` for subsystem engineering limits and telemetry guides.\n"
            "Use the provided tools to inspect telemetry, modify subsystem configurations, "
            "verify behavior with `step_orbit` or `run_python`, then call `finish` to initiate orbital validation."
        )

    def _science_status(self, sim=None):
        sim = sim or self.sim
        return {"science_buffer_kb":round(sim.science_buffer_kb,6),
                "science_generated_kb":round(sim.science_generated_kb,6),
                "science_downlinked_kb":round(sim.science_downlinked_kb,6),
                "science_dropped_kb":round(sim.science_dropped_kb,6),
                "science_peak_buffer_kb":round(sim.science_peak_buffer_kb,6),
                "science_window_exposure_s":[round(v,6) for v in sim.science_window_exposure_s]}

    def _initial_status(self, configs):
        error = self.sim.pointing_err_deg
        return {"elapsed_minutes": 0.0, "orbit_phase": "SUNLIGHT",
                "bus_voltage_v": round(3.0 + 1.2 * self.sim.soc_wh / BATTERY_CAPACITY_WH, 2),
                "battery_soc_pct": round(self.sim.soc_wh / BATTERY_CAPACITY_WH * 100, 1),
                "battery_temp_c": self.sim.temp_c, "pointing_error_deg": error,
                "heater_active": False,
                "pass_active": False, "data_downlinked_kb": 0.0, "tx_energy_wh": 0.0,
                "observation_s": 0.0, "payload_energy_wh": 0.0,
                "link_margin_db": round(link_margin_db(configs, error), 1), **self._science_status()}


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
            "pass_active": last_frame.pass_active,
            "data_downlinked_kb": last_frame.data_downlinked_kb,
            "tx_energy_wh": round(self.sim.tx_energy_wh, 5),
            "observation_s": round(self.sim.observation_s, 3),
            "payload_energy_wh": round(self.sim.payload_energy_wh, 5),
            **self._science_status(),
        }
        csv_lines = "".join(
            f"{round(f.time_s/60.0, 1)},{'ECLIPSE' if f.eclipse else 'SUNLIGHT'},"
            f"{f.bus_voltage_v},{f.battery_soc_pct},{f.battery_temp_c},"
            f"{f.pointing_error_deg},{f.link_margin_db},{'ON' if f.heater_active else 'OFF'}\n"
            for f in frames
        )
        self._post("/sync_telemetry", {"status": status_payload, "csv_append": csv_lines}, self.tool_timeout)

    # ----------------------------------------------------------------- step
    def step(self, name: str, args: dict):
        with self.budget(self.tool_timeout):
            return self._step(name, args)

    def _step(self, name: str, args: dict):
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
            if not 1 <= minutes <= 30:
                return "error: minutes must be between 1 and 30", True
            advance_s = min(minutes * 60.0, time_left_s)
            actual_min = round(advance_s / 60.0, 1)

            frames = self.sim.advance(advance_s, configs, dt_s=60.0)
            self._sync_sim_telemetry(frames)

            last_frame = frames[-1] if frames else None
            if last_frame:
                output = (
                    f"Orbit advanced by {actual_min} min (MET: {round(self.sim.time_s/60.0, 1)} min, "
                    f"Phase: {'ECLIPSE' if last_frame.eclipse else 'SUNLIGHT'}).\n"
                    f"Status: Bus={last_frame.bus_voltage_v}V, Battery SoC={last_frame.battery_soc_pct}%, "
                    f"Temp={last_frame.battery_temp_c} C, Pointing Err={last_frame.pointing_error_deg} degrees, "
                    f"Link Margin={last_frame.link_margin_db} dB (Heater: {'ON' if last_frame.heater_active else 'OFF'}).\n"
                    f"Updated telemetry in telemetry/live_telemetry.csv and telemetry/current_status.json"
                )
                if round(self.sim.time_s / 60.0, 1) >= 95.0:
                    output += "\n[NOTICE]: 95.0-minute mission boundary reached. Call `finish` to finalize."
                if self.sim.catastrophic_failure:
                    output += f"\n[CRITICAL WARNING]: Irreversible safety violation triggered: {self.sim.failure_reason}"
                return output, False
            return "error: orbit cannot advance: " + (self.sim.failure_reason or "invalid simulation configuration"), True

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
        with self.budget(self.tool_timeout):
            response = self._request("GET", "/state")
        files = response.get("files", {})
        state_payload = {"files": files, "mission": self.task.mission if self.task else {}}
        if self.sim:
            state_payload["sim"] = {
                **self._science_status(),
                "time_s": round(self.sim.time_s, 2),
                "soc_wh": round(self.sim.soc_wh, 4),
                "temp_c": round(self.sim.temp_c, 3),
                "pointing_err_deg": round(self.sim.pointing_err_deg, 3),
                "downlinked_kb": round(self.sim.total_downlinked_kb, 3),
                "tx_energy_wh": round(self.sim.tx_energy_wh, 6),
                "observation_s": round(self.sim.observation_s, 6),
                "payload_energy_wh": round(self.sim.payload_energy_wh, 6),
                "catastrophic_failure": bool(self.sim.catastrophic_failure),
                "failure_reason": str(self.sim.failure_reason),
                "heater_on": bool(self.sim.heater_on),
                "min_bus_v": round(self.sim.min_bus_v, 4),
                "max_temp_c": round(self.sim.max_temp_c, 3),
                "min_temp_c": round(self.sim.min_temp_c, 3),
                "max_pointing_err_deg": round(self.sim.max_pointing_err_deg, 3),
                "min_link_margin_db": round(self.sim.min_link_margin_db, 3),
            }
        return hashlib.sha256(json.dumps(state_payload, sort_keys=True).encode()).hexdigest()

    # ---------------------------------------------------------------- score
    def score(self) -> Dict[str, Any]:
        with self.budget(self.verify_timeout):
            return self._score()

    def _score(self) -> Dict[str, Any]:
        """Programmatic verifier: scores the authoritative episode trajectory and completes the orbit.
        
        Evaluates behavioural execution via isolated worker inside sandbox (zero harness execution),
        validates physical parameters, and computes the authoritative outcome-based reward.
        """
        response = self._post("/fetch_configs", {}, self.tool_timeout)
        files = response.get("files", {})
        configs = self._parse_subsystem_configs(files)

        # Dynamic behavioural verification of link budget script on unprivileged sandbox worker
        if (self.task and "scripts/link_budget.py" in self.task.files
                and not configs.get("_config_errors") and validate_subsystem_configs(configs)[0]):
            if "scripts/link_budget.py" in self.task.files or "scripts/link_budget.py" in files:
                script_content = files.get("scripts/link_budget.py", "")
                script_hash = hashlib.sha256(script_content.encode("utf-8")).hexdigest()
                cache_key = f"{script_hash}:{hashlib.sha256(files.get('subsystems/comms.cfg', '').encode()).hexdigest()}:{self.seed}"

                if cache_key in self._eval_cache:
                    behavior_ok = self._eval_cache[cache_key]
                else:
                    self._eval_call_count += 1
                    # Test the exact live configuration plus seven full-schema
                    # variants. Optional hardware fields must not identify a fixture.
                    rng = random.Random(self.seed ^ 0x5F3759DF)
                    actual_cfg = files.get("subsystems/comms.cfg", "")
                    test_cases = [{"subsystems/comms.cfg": actual_cfg}]
                    expected_margins = [round(link_margin_db(configs), 2)]
                    for index in range(7):
                        parser = configparser.ConfigParser(interpolation=None)
                        try:
                            parser.read_string(actual_cfg)
                        except configparser.Error:
                            parser = configparser.ConfigParser(interpolation=None)
                        if not parser.has_section("comms"):
                            parser.add_section("comms")
                        values = {"tx_power_dbm": round(rng.uniform(22, 32), 1),
                                  "antenna_gain_dbi": round(rng.uniform(5, 15), 1),
                                  "path_loss_db": round(rng.uniform(130, 150), 1),
                                  "baudrate_bps": rng.choice([4800, 9600, 19200, 38400])}
                        for key, value in values.items():
                            parser.set("comms", key, str(value))
                        # Exercise the documented absent-baud default as well.
                        if index == 6:
                            parser.remove_option("comms", "baudrate_bps")
                            values["baudrate_bps"] = 9600
                        import io
                        text = io.StringIO()
                        parser.write(text)
                        test_cases.append({"subsystems/comms.cfg": text.getvalue()})
                        expected_margins.append(round(link_margin_db(values), 2))

                    eval_payload = {
                        "script_relpath": "scripts/link_budget.py",
                        "test_cases": test_cases,
                    }
                    try:
                        eval_res = self._post("/eval_script", eval_payload, self.tool_timeout)
                        actual_results = eval_res.get("results", [])
                        behavior_ok = (len(actual_results) == len(test_cases) and all(
                            r is not None and abs(r - exp) < 0.15
                            for r, exp in zip(actual_results, expected_margins)
                        ))
                    except (requests.RequestException, TimeoutError):
                        raise

                    self._eval_cache[cache_key] = behavior_ok

                configs["script_verified"] = behavior_ok


        # Finalize projected orbit simulation through the 95-minute period
        if not self.sim:
            self.sim = AuthoritativeOrbitSimulator(
                initial_soc_pct=self.task.initial_soc_pct if self.task else 70.0,
                initial_temp_c=self.task.initial_temp_c if self.task else 14.0,
                initial_pointing_error_deg=self.task.initial_pointing_error_deg if self.task else 35.0,
                task_id=self.task.task_id if self.task else "",
            )

        sim_eval = copy.deepcopy(self.sim)
        sim_result: SimulationResult = sim_eval.finalize_orbit(configs)
        raw = sim_result.reward
        baseline_total = self.baseline["total"] if self.baseline else 0.0
        progress = max(0.0, min(1.0, (raw["total"] - baseline_total) / max(0.0001, 1.0 - baseline_total)))
        if not sim_result.passed:
            progress = min(progress, 0.94)
        keys = sorted((set(raw) | set(self.baseline or {})) - {"total"})
        gains = {key: max(0.0, raw.get(key, 0) - (self.baseline or {}).get(key, 0)) for key in keys}
        gain_sum = sum(gains.values())
        result = {key: round(progress * gains[key] / gain_sum, 6) if gain_sum else 0.0 for key in keys}
        result.update(total=round(progress, 6), raw_components=raw, baseline_total=baseline_total)
        self.last_verifier = {"passed": sim_result.passed and progress >= PASS_SCORE, "failure_reason": sim_result.failure_reason,
                              "checks": sim_result.checks,
                              **self._science_status(sim_eval),
                              "tx_energy_wh": round(sim_eval.tx_energy_wh, 5),
                              "observation_s": round(sim_eval.observation_s, 3),
                              "payload_energy_wh": round(sim_eval.payload_energy_wh, 5),
                              "projected": True, "final_soc_pct": round(sim_eval.soc_wh / BATTERY_CAPACITY_WH * 100, 3),
                              "final_pointing_deg": round(sim_eval.pointing_err_deg, 3),
                              "downlinked_kb": round(sim_eval.total_downlinked_kb, 3)}
        return result
