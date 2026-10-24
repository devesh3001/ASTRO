"""Synthetic fixtures only: never used as submission evidence."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from scripts.package_submission import collect_evidence
from harness.tasks import TASKS


class TestEvidence(unittest.TestCase):
    @patch("scripts.package_submission.fingerprints", return_value=("h", "s"))
    def test_scripted_failure_is_labelled_and_never_counts_as_real(self, _):
        with tempfile.TemporaryDirectory() as folder:
            logs = Path(folder)
            for i in range(3):
                self.fixture(logs, str(i), "success")
            directory = self.fixture(logs, "attack", "failure")
            manifest = json.loads((directory / "manifest.json").read_text())
            manifest["evidence_role"] = "adversarial_regression"
            (directory / "manifest.json").write_text(json.dumps(manifest))
            summary = json.loads((directory / "summary.json").read_text())
            summary.update(model="mock/adversarial", model_called=False)
            (directory / "summary.json").write_text(json.dumps(summary))
            _, runs, _, failure = collect_evidence(logs)
            self.assertEqual(len(runs), 3)
            self.assertNotIn(directory, runs)
            self.assertEqual(failure.parent, directory)

    def fixture(self, logs, name, termination="success", mode="full"):
        directory = logs / name
        directory.mkdir()
        def save(filename, data):
            (directory / filename).write_text(json.dumps(data), encoding="utf-8")
        save("manifest.json", {"contract_version": 2, "harness_sha256": "h", "sandbox_sha256": "s"})
        save("gate.json", {"passed": True, "tasks": [{"task": task, "oracle": 1., "noop": 0.} for task in TASKS]})
        task = next(iter(TASKS))
        reward = {"total": 1.0 if termination == "success" else 0.0}
        summary = {"model": "synthetic-test-fixture", "model_called": True,
                   "termination": termination, "reward": reward}
        if mode == "all":
            summary = {"model": "synthetic-test-fixture", "model_called": True,
                       "tasks": {task: {"termination": termination, "reward": reward}}}
        save("summary.json", summary)
        records = [{"agent": "model", "step": 1, "model_output_raw": "{}"},
                   {"agent": "model", "event": "episode_end", "steps": 1, "task": task,
                    "termination": termination, "reward": reward}]
        path = directory / (f"episode-{task}.jsonl" if mode == "all" else "episode.jsonl")
        path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
        return directory

    @patch("scripts.package_submission.fingerprints", return_value=("h", "s"))
    def test_all_mode_episodes_and_three_distinct_runs(self, _):
        with tempfile.TemporaryDirectory() as folder:
            logs = Path(folder)
            for i in range(3):
                self.fixture(logs, str(i), "failure" if i == 0 else "success", "all")
            _, runs, success, failure = collect_evidence(logs)
            self.assertEqual(len(runs), 3)
            self.assertTrue(success.is_file() and failure.is_file())

    @patch("scripts.package_submission.fingerprints", return_value=("h", "s"))
    def test_missing_episode_never_counts_as_a_genuine_run(self, _):
        with tempfile.TemporaryDirectory() as folder:
            logs = Path(folder)
            for i in range(3):
                self.fixture(logs, str(i), "failure" if i == 0 else "success")
            (logs / "2/episode.jsonl").unlink()
            with self.assertRaisesRegex(ValueError, "2/3"):
                collect_evidence(logs)

    @patch("scripts.package_submission.fingerprints", return_value=("h", "s"))
    def test_duplicate_task_gates_are_not_full_coverage(self, _):
        with tempfile.TemporaryDirectory() as folder:
            logs = Path(folder)
            for i in range(3):
                directory = self.fixture(logs, str(i), "failure" if i == 0 else "success")
                (directory / "gate.json").write_text(json.dumps({"passed": True, "tasks": [
                    {"task": next(iter(TASKS)), "oracle": 1., "noop": 0.}] * 5}))
            with self.assertRaisesRegex(ValueError, "gate missing"):
                collect_evidence(logs)

    @patch("scripts.package_submission.fingerprints", return_value=("new", "s"))
    def test_stale_evidence_cannot_certify_changed_code(self, _):
        with tempfile.TemporaryDirectory() as folder:
            logs = Path(folder)
            for i in range(3):
                self.fixture(logs, str(i), "failure" if i == 0 else "success")
            with self.assertRaisesRegex(ValueError, "gate missing"):
                collect_evidence(logs)


if __name__ == "__main__":
    unittest.main()
