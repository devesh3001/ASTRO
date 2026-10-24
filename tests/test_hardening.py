"""Regressions for failures reproduced in the repository review."""
import json
import time
import unittest
import importlib.util
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from contextlib import contextmanager
from unittest.mock import patch
from harness.agents import OpenAIClient, Reply, ToolCall
from harness.runner import run_episode, trim_history, estimate_tokens, Limits
from harness.tasks import build_task
from harness.verifier import AuthoritativeOrbitSimulator
from harness.tools import check_args
from tests.support import environment


class Log:
    def __init__(self):
        self.records = []

    def write(self, record):
        self.records.append(record)


class FakeEnv:
    task = None
    deadline = None
    last_verifier = {}

    def reset(self, *args):
        return "test task"

    def state_hash(self):
        return "hash"

    def step(self, *args):
        raise TimeoutError("simulated tool timeout")

    def score(self):
        return {"total": 0.0}


class Client:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.inputs = []

    def start(self, task):
        pass

    def complete(self, messages, tools, timeout):
        self.inputs.append(messages)
        return next(self.replies)


def reply(*calls):
    return Reply(None, [ToolCall(str(i), name, json.dumps(args)) for i, (name, args) in enumerate(calls)], raw="{}")


class TestRunner(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec("openai"), "provider dependency not installed")
    def test_provider_process_preserves_received_malformed_body(self):
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                data = b'{"choices":[]}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = OpenAIClient("unknown-model", "test-only", f"http://127.0.0.1:{server.server_address[1]}/v1")
            result = client.complete([{"role": "user", "content": "test"}], [], 15)
            self.assertEqual(result.raw, '{"choices":[]}')
            self.assertIsNotNone(result.error)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_timeout_returns_feedback_and_episode_end(self):
        log = Log()
        client = Client([reply(("read_file", {"path": "x"})), reply(("finish", {}))])
        result = run_episode(FakeEnv(), "task", 0, client, "", Limits(), log)
        self.assertEqual(result["steps"], 2)
        self.assertEqual(result["termination"], "failure")
        self.assertIn("TimeoutError", client.inputs[1][-1]["content"])
        self.assertEqual(log.records[-1]["event"], "episode_end")

    def test_malformed_reply_counts_one_step_and_continues(self):
        log = Log()
        client = Client([OpenAIClient.normalize('{"choices":[]}'), reply(("finish", {}))])
        result = run_episode(FakeEnv(), "task", 0, client, "", Limits(), log)
        self.assertEqual(result["steps"], 2)
        self.assertEqual(log.records[0]["errors"][0]["kind"], "unreadable_reply")

    def test_reply_tool_limit_and_finish_matching_results(self):
        log = Log()
        result = run_episode(FakeEnv(), "task", 0,
            Client([reply(*[("list_files", {}) for _ in range(12)])]), "", Limits(max_steps=1), log)
        self.assertEqual(result["steps"], 1)
        self.assertEqual(len(log.records[0]["tool_result"]), 12)
        self.assertEqual(sum(e["kind"] == "too_many_tool_calls" for e in log.records[0]["errors"]), 2)
        self.assertEqual(result["termination"], "step_cap")

    def test_step_cap_and_append_exactly_preserved(self):
        log = Log()
        appended = "Judge instructions\r\n  preserve spaces\n"
        client = Client([Reply("thinking", raw="{}") for _ in range(40)])
        result = run_episode(FakeEnv(), "task", 0, client, appended, Limits(), log)
        self.assertEqual(result["steps"], 40)
        self.assertEqual(result["termination"], "step_cap")
        self.assertTrue(client.inputs[0][0]["content"].endswith(appended))

    def test_long_assistant_text_and_nonascii_fit(self):
        messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "t"},
                    {"role": "assistant", "content": "語" * 240000}]
        trim_history(messages, 48000)
        self.assertLessEqual(estimate_tokens(messages), 48000)

    def test_oversize_immutable_prompt_is_explicit(self):
        with self.assertRaises(ValueError):
            trim_history([{"role": "system", "content": "x" * 10000}], 100)

    def test_invalid_argument_types_do_not_crash(self):
        self.assertIsNotNone(ToolCall("x", "read_file", {"path": "x"}).parse()[1])
        self.assertIsNotNone(check_args("step_orbit", {"minutes": True}))
        self.assertIsNotNone(check_args("step_orbit", {"minutes": 31}))

    def test_model_transport_timeout_is_bounded(self):
        import subprocess
        client = OpenAIClient("model", "test-only")
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("test", 0.01)):
            with self.assertRaises(TimeoutError):
                client.complete([], [], 0.01)


class TestIntegrity(unittest.TestCase):
    def test_adcs_relaxed_tolerance_cannot_get_full_reward(self):
        with environment() as env:
            env.reset("adcs_detumble_pointing", 0)
            content = env.task.reference["subsystems/adcs.cfg"].replace("0.05", "15.0")
            env.step("write_file", {"path": "subsystems/adcs.cfg", "content": content})
            self.assertLess(env.score()["total"], 0.95)

    def test_invalid_final_config_at_orbit_boundary(self):
        with environment() as env:
            env.reset("eps_eclipse_recovery", 0)
            content = env.task.reference["subsystems/eps.cfg"]
            env.step("write_file", {"path": "subsystems/eps.cfg", "content": content})
            for _ in range(4):
                env.step("step_orbit", {"minutes": 30})
            env.step("write_file", {"path": "subsystems/eps.cfg", "content": content.replace("0.72", "nan")})
            self.assertEqual(env.score()["total"], 0)
            self.assertIn("NaN", env.last_verifier["failure_reason"])

    def test_eps_threshold_only_is_not_solar_recovery(self):
        with environment() as env:
            env.reset("eps_eclipse_recovery", 0)
            content = env.task.files["subsystems/eps.cfg"].replace("2.80", "3.20")
            env.step("write_file", {"path": "subsystems/eps.cfg", "content": content})
            self.assertLess(env.score()["total"], 0.95)

    def test_reset_telemetry_matches_ground_truth(self):
        with environment() as env:
            env.reset("thermal_battery_preservation", 0)
            text, error = env._post("/tool", {"name": "read_file", "args": {"path": "telemetry/current_status.json"}}, 60).values()
            status = json.loads(text)
            self.assertEqual(status["battery_temp_c"], env.task.initial_temp_c)
            self.assertEqual(status["battery_soc_pct"], env.task.initial_soc_pct)
            self.assertEqual(status["pointing_error_deg"], env.task.initial_pointing_error_deg)

    def test_hardware_properties_cannot_be_edited(self):
        with environment() as env:
            env.reset("rf_downlink_closure", 0)
            content = env.task.reference["subsystems/comms.cfg"]
            content = content.replace(str(env.task.mission["path_loss_db"]), "100.0")
            env.step("write_file", {"path": "subsystems/comms.cfg", "content": content})
            self.assertEqual(env.score()["total"], 0)

    def test_hardware_cutoff_does_not_depend_on_software_threshold(self):
        sim = AuthoritativeOrbitSimulator(initial_soc_pct=5, initial_pointing_error_deg=0)
        sim.advance(60, {"solar_efficiency": 0, "undervoltage_threshold": 2.4, "wheel_bias_rpm": 0})
        self.assertTrue(sim.catastrophic_failure)

    def test_whole_operation_uses_remaining_deadline(self):
        with environment() as env:
            env.deadline = time.monotonic() - 1
            with self.assertRaises(TimeoutError):
                env.reset("eps_eclipse_recovery", 0)

    def test_late_valid_script_is_not_rejected_by_edit_count(self):
        with environment() as env:
            env.reset("rf_downlink_closure", 0)
            env._eval_call_count = 12
            for path, content in env.task.reference.items():
                env.step("write_file", {"path": path, "content": content})
            self.assertGreaterEqual(env.score()["total"], 0.95)


if __name__ == "__main__":
    unittest.main()
