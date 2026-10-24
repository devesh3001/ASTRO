"""Synthetic fixtures only: never used as submission evidence."""
import json
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from harness.provenance import CONTRACT_FILES, CONTRACT_VERSION
from harness.provenance import contract_fingerprint as runtime_fingerprint
from harness.provenance import fingerprints as source_fingerprints
from harness.tasks import TASKS
from scripts.package_submission import assemble, collect_evidence


class TestEvidence(unittest.TestCase):
    def setUp(self):
        for name, value in (("fingerprints", ("h", "s")), ("contract_fingerprint", "c")):
            active = patch("scripts.package_submission." + name, return_value=value)
            active.start()
            self.addCleanup(active.stop)

    def fixture(self, logs, name, termination="success", mode="full"):
        directory = logs / name
        directory.mkdir()
        def save(filename, data):
            (directory / filename).write_text(json.dumps(data), encoding="utf-8")
        save("manifest.json", {"contract_version": CONTRACT_VERSION, "harness_sha256": "h",
                               "sandbox_sha256": "s", "runtime_contract_sha256": "c"})
        save("gate.json", {"passed": True, "tasks": [
            {"task": task, "oracle": 1., "noop": 0.} for task in TASKS]})
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

    def mutate(self, directory, filename, **values):
        path = directory / filename
        record = json.loads(path.read_text(encoding="utf-8"))
        record.update(values)
        path.write_text(json.dumps(record), encoding="utf-8")

    def populate(self, logs):
        return [self.fixture(logs, str(i), "failure" if i == 0 else "success") for i in range(3)]

    def test_scripted_failure_is_labelled_and_never_counts_as_real(self):
        with tempfile.TemporaryDirectory() as folder:
            logs = Path(folder)
            for i in range(3):
                self.fixture(logs, str(i), "success")
            directory = self.fixture(logs, "attack", "failure")
            self.mutate(directory, "manifest.json", evidence_role="adversarial_regression")
            self.mutate(directory, "summary.json", model="mock/adversarial", model_called=False)
            _, runs, _, failure = collect_evidence(logs)
            self.assertEqual(len(runs), 3)
            self.assertNotIn(directory, runs)
            self.assertEqual(failure.parent, directory)

    def test_all_mode_episodes_and_three_distinct_runs(self):
        with tempfile.TemporaryDirectory() as folder:
            logs = Path(folder)
            for i in range(3):
                self.fixture(logs, str(i), "failure" if i == 0 else "success", "all")
            _, runs, success, failure = collect_evidence(logs)
            self.assertEqual(len(runs), 3)
            self.assertTrue(success.is_file() and failure.is_file())

    def test_missing_episode_never_counts_as_a_genuine_run(self):
        with tempfile.TemporaryDirectory() as folder:
            logs = Path(folder)
            self.populate(logs)
            (logs / "2/episode.jsonl").unlink()
            with self.assertRaisesRegex(ValueError, "2/3"):
                collect_evidence(logs)

    def test_duplicate_task_gates_are_not_full_coverage(self):
        with tempfile.TemporaryDirectory() as folder:
            logs = Path(folder)
            for directory in self.populate(logs):
                self.mutate(directory, "gate.json", tasks=[
                    {"task": next(iter(TASKS)), "oracle": 1., "noop": 0.}] * len(TASKS))
            with self.assertRaisesRegex(ValueError, "gate missing"):
                collect_evidence(logs)

    def test_stale_evidence_cannot_certify_changed_code(self):
        with tempfile.TemporaryDirectory() as folder:
            logs = Path(folder)
            self.populate(logs)
            with patch("scripts.package_submission.fingerprints", return_value=("new", "s")):
                with self.assertRaisesRegex(ValueError, "gate missing"):
                    collect_evidence(logs)

    def test_runtime_contract_change_rejects_matching_python_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            logs = Path(folder)
            self.populate(logs)
            with patch("scripts.package_submission.contract_fingerprint", return_value="changed"):
                with self.assertRaisesRegex(ValueError, "gate missing"):
                    collect_evidence(logs)

    def test_v2_and_missing_contract_are_not_current_evidence(self):
        for legacy in (True, False):
            with self.subTest(legacy=legacy), tempfile.TemporaryDirectory() as folder:
                logs = Path(folder)
                for directory in self.populate(logs):
                    path = directory / "manifest.json"
                    manifest = json.loads(path.read_text())
                    if legacy:
                        manifest["contract_version"] = 2
                    else:
                        manifest.pop("runtime_contract_sha256")
                    path.write_text(json.dumps(manifest))
                with self.assertRaisesRegex(ValueError, "gate missing"):
                    collect_evidence(logs)

    def test_raw_replies_and_summary_must_match(self):
        for invalid in ("raw_missing", "bad_step", "ending_count", "reward_mismatch"):
            with self.subTest(invalid=invalid), tempfile.TemporaryDirectory() as folder:
                logs = Path(folder)
                directory = self.populate(logs)[2]
                path = directory / "episode.jsonl"
                records = [json.loads(line) for line in path.read_text().splitlines()]
                if invalid == "raw_missing":
                    records[0].pop("model_output_raw")
                elif invalid == "bad_step":
                    records[0]["step"] = 2
                elif invalid == "ending_count":
                    records.append(records[-1])
                else:
                    records[-1]["reward"] = {"total": 0.}
                path.write_text("\n".join(json.dumps(r) for r in records))
                with self.assertRaisesRegex(ValueError, "2/3"):
                    collect_evidence(logs)

    def test_zero_reply_provider_attempt_is_diagnostic_only(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            logs = root / "logs"
            logs.mkdir()
            self.populate(logs)
            diagnostic = self.fixture(logs, "zero", "crash")
            self.mutate(diagnostic, "summary.json", steps=0)
            (diagnostic / "episode.jsonl").write_text(json.dumps({"event": "model_call_failed"}) + "\n" +
                json.dumps({"event": "episode_end", "steps": 0, "termination": "crash", "reward": {"total": 0.}}))
            stale = self.fixture(logs, "stale-zero", "crash")
            self.mutate(stale, "summary.json", steps=0)
            self.mutate(stale, "manifest.json", runtime_contract_sha256="old")
            (stale / "episode.jsonl").write_bytes((diagnostic / "episode.jsonl").read_bytes())
            bundle = root / "submission_bundle"
            bundle.mkdir()
            for name in ("deck.pdf", "design-doc.pdf"):
                (bundle / name).write_bytes(b"%PDF-synthetic-test-only")
            output = root / "Astro.zip"
            with patch("scripts.package_submission.ROOT", root):
                assemble("Astro", logs, output)
            with zipfile.ZipFile(output) as archive:
                record = json.loads(archive.read("evidence-manifest.json"))
                self.assertEqual(len(record["qualifying_model_run_ids"]), 3)
                self.assertEqual(record["diagnostic_provider_attempt_ids"], ["zero"])
                self.assertEqual(record["contract_version"], CONTRACT_VERSION)
                self.assertEqual(record["runtime_contract_sha256"], "c")


class TestRuntimeContract(unittest.TestCase):
    def fixture(self, root):
        for name in CONTRACT_FILES:
            (root / name).write_bytes((name + "\nexample build input\n").encode())
        (root / "harness").mkdir()
        (root / "harness/run.py").write_bytes(b"# harness\n")
        (root / "sandbox").mkdir()
        for name in ("server.py", "runtime.py"):
            (root / "sandbox" / name).write_bytes(b"# sandbox\n")

    def test_every_security_and_build_input_changes_contract(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.fixture(root)
            expected = runtime_fingerprint(root)
            for name in CONTRACT_FILES:
                with self.subTest(name=name):
                    path = root / name
                    original = path.read_bytes()
                    path.write_bytes(original + b"changed\n")
                    self.assertNotEqual(runtime_fingerprint(root), expected)
                    path.write_bytes(original)

    def test_lf_crlf_and_baked_contract_agree(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.fixture(root)
            expected, sources = runtime_fingerprint(root), source_fingerprints(root)
            for path in root.rglob("*"):
                if path.is_file():
                    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
            self.assertEqual(runtime_fingerprint(root), expected)
            self.assertEqual(source_fingerprints(root), sources)
            baked = root / "build-contract"
            baked.mkdir()
            for name in CONTRACT_FILES:
                shutil.move(root / name, baked / name)
            self.assertEqual(runtime_fingerprint(root), expected)

    def test_credentials_and_model_settings_are_not_build_inputs(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.fixture(root)
            expected = runtime_fingerprint(root)
            (root / ".env").write_text("API_KEY=synthetic-secret\nMODEL=opaque-deployment-identifier\n")
            self.assertEqual(runtime_fingerprint(root), expected)
            self.assertNotIn(".env", CONTRACT_FILES)

    def test_missing_required_build_input_fails_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.fixture(root)
            (root / "docker-compose.yml").unlink()
            with self.assertRaises(FileNotFoundError):
                runtime_fingerprint(root)


if __name__ == "__main__":
    unittest.main()
