"""Record an executed scoring attack, explicitly labelled as scripted, not a real model."""
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from harness.agents import Reply, ToolCall
from harness.env import Env
from harness.runner import JsonlLog, Limits, run_episode
from harness.run import run_gate
from package_submission import fingerprints


class Adversary:
    def start(self, task):
        self.content = task.reference["subsystems/adcs.cfg"].replace("0.05", "15.0")
        self.turn = 0

    def complete(self, messages, tools, timeout):
        self.turn += 1
        name, args = ("write_file", {"path": "subsystems/adcs.cfg", "content": self.content}) if self.turn == 1 else ("finish", {})
        raw = json.dumps({"scripted_adversary": "relax-pointing-target", "turn": self.turn})
        return Reply(None, [ToolCall(f"attack-{self.turn}", name, json.dumps(args))], raw)


def main():
    name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S-%fZ") + "-adversarial-" + uuid.uuid4().hex[:8]
    out = ROOT / "logs" / name
    out.mkdir(parents=True)
    harness, sandbox = fingerprints()
    model = "mock/adcs-relaxation-regression"
    (out / "manifest.json").write_text(json.dumps({"contract_version": 2,
        "harness_sha256": harness, "sandbox_sha256": sandbox, "model": model, "seed": 0,
        "evidence_role": "adversarial_regression", "description": "Executed scripted attack; not a real-model run"}, indent=2))
    env = Env("http://sandbox:8000")
    passed, rows = run_gate(env, ["adcs_detumble_pointing"], 0, out / "gate.jsonl")
    (out / "gate.json").write_text(json.dumps({"passed": passed, "tasks": rows, "seed": 0}, indent=2))
    log = JsonlLog(out / "episode.jsonl")
    try:
        result = run_episode(env, "adcs_detumble_pointing", 0, Adversary(), "", Limits(), log)
    finally:
        log.close()
    assert result["termination"] == "failure" and result["reward"]["total"] < .95, result
    (out / "summary.json").write_text(json.dumps({"model": model, "model_called": False,
        "agent_called": True, "task": "adcs_detumble_pointing", **result}, indent=2))
    print("Scoring attack correctly rejected; scripted failure recorded in", out)


if __name__ == "__main__":
    main()
