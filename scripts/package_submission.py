"""Validate reviewed PDFs and create one source-matched evidence archive."""
import argparse
import hashlib
import json
import math
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from harness.tasks import TASKS
from harness.provenance import CONTRACT_VERSION, contract_fingerprint as _contract_fingerprint, fingerprints as _fingerprints


def fingerprints():
    return _fingerprints(ROOT)


def contract_fingerprint():
    return _contract_fingerprint(ROOT)


def provenance_matches(record, source=None, contract=None):
    """All current evidence must name the source and expected build contract."""
    harness, sandbox = fingerprints() if source is None else source
    contract = contract_fingerprint() if contract is None else contract
    return (isinstance(record, dict) and record.get("contract_version") == CONTRACT_VERSION
            and record.get("harness_sha256") == harness
            and record.get("sandbox_sha256") == sandbox
            and record.get("runtime_contract_sha256") == contract)


def json_file(path):
    return json.loads(path.read_text(encoding="utf-8"))


def collect_evidence(logs):
    source, contract = fingerprints(), contract_fingerprint()
    gates, runs, successes, failures = [], [], [], []
    for directory in sorted(logs.iterdir() if logs.is_dir() else [], reverse=True):
        if not directory.is_dir():
            continue
        try:
            manifest = json_file(directory / "manifest.json")
            if not provenance_matches(manifest, source, contract):
                continue
            gate_path = directory / "gate.json"
            gate = json_file(gate_path)
            rows = gate.get("tasks", [])
            if (gate.get("passed") is True and len(rows) == len(TASKS)
                    and {r["task"] for r in rows} == set(TASKS)
                    and all(math.isfinite(r["oracle"]) and r["oracle"] >= .95
                            and math.isfinite(r["noop"]) and r["noop"] <= .05 for r in rows)):
                gates.append(directory)
            summary_path = directory / "summary.json"
            if not summary_path.is_file():
                continue
            summary = json_file(summary_path)
            genuine = summary.get("model_called") and not str(summary.get("model", "mock/")).startswith("mock/")
            adversarial = (manifest.get("evidence_role") == "adversarial_regression"
                           and str(summary.get("model", "")).startswith("mock/"))
            if not (genuine or adversarial):
                continue
            episodes = []
            for path in sorted(directory.glob("episode*.jsonl")):
                records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
                replies = [r for r in records if r.get("agent") == "model" and "step" in r]
                endings = [r for r in records if r.get("event") == "episode_end"]
                if (not replies or [r["step"] for r in replies] != list(range(1, len(replies) + 1))
                        or len(endings) != 1 or endings[0].get("steps") != len(replies)
                        or any(r.get("model_output_raw") is None for r in replies)):
                    continue
                end = endings[0]
                task_summary = summary.get("tasks", {}).get(end.get("task"), summary)
                if task_summary.get("termination") != end.get("termination") or task_summary.get("reward") != end.get("reward"):
                    continue
                episodes.append(path)
                total = end.get("reward", {}).get("total")
                if genuine and end.get("termination") == "success" and isinstance(total, (float, int)) and total >= .95:
                    successes.append(path)
                elif end.get("termination") in ("failure", "crash", "time_cap", "step_cap"):
                    failures.append(path)
            if episodes and genuine:
                runs.append(directory)
        except (ValueError, KeyError, TypeError, OSError):
            continue
    if not gates:
        raise ValueError("Current-code all-task baseline gate missing. Run MODE=baselines first.")
    if len(runs) < 3 or not successes or not failures:
        raise ValueError(f"Current-code evidence incomplete: {len(runs)}/3 genuine model runs, "
                         f"success={bool(successes)}, failure={bool(failures)}. Historical logs are preserved but not relabeled.")
    return gates[0], runs, successes[0], failures[0]


def validate_documents(team):
    """Package the reviewed tracked PDFs without overwriting them with stale templates."""
    if team != 'Astro':
        raise ValueError('Reviewed documents identify Team Astro; update them before packaging another team.')
    for name in ('design-doc.pdf','deck.pdf'):
        path = ROOT / 'submission_bundle' / name
        if not path.is_file() or not path.read_bytes().startswith(b'%PDF-'):
            raise ValueError(f'Missing or invalid reviewed PDF: {name}')


def assemble(team, logs, output):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", team):
        raise ValueError("team name must be a simple filename")
    if output.name != f"{team}.zip":
        raise ValueError("archive filename must match the registered team name")
    gate, runs, success, failure = collect_evidence(logs)
    selected = {}
    def include(path, name):
        selected[name] = path.read_bytes()
    for path in gate.glob("gate.*"):
        include(path, "baseline-results/" + path.name)
    include(gate / "manifest.json", "baseline-results/manifest.json")
    for index, directory in enumerate(runs, 1):
        for path in directory.iterdir():
            if path.is_file() and path.suffix in (".json", ".jsonl", ".txt"):
                include(path, f"test-run-logs/run{index}/" + path.name)
    # Preserve zero-reply transport failures without counting them as complete runs.
    diagnostic_attempts = []
    for directory in sorted(logs.iterdir()):
        if directory in runs or not directory.is_dir():
            continue
        try:
            manifest = json_file(directory / "manifest.json")
            summary = json_file(directory / "summary.json")
            if not provenance_matches(manifest):
                continue
            if not (summary.get("model_called") and not str(summary.get("model", "mock/")).startswith("mock/")
                    and summary.get("termination") == "crash" and summary.get("steps") == 0):
                continue
            records = [json.loads(line) for line in (directory/"episode.jsonl").read_text().splitlines() if line.strip()]
            if not any(r.get("event") == "model_call_failed" for r in records):
                continue
            diagnostic_attempts.append(directory.name)
            for path in directory.iterdir():
                if path.is_file() and path.suffix in (".json", ".jsonl", ".txt"):
                    include(path, f"test-run-logs/diagnostic-attempts/{directory.name}/" + path.name)
        except (OSError, ValueError):
            continue
    reviewed_evidence = ROOT / "submission_bundle/validation-evidence.json"
    if reviewed_evidence.is_file():
        record = json_file(reviewed_evidence)
        if not provenance_matches(record):
            raise ValueError("Reviewed evidence summary belongs to older runtime source")
        include(reviewed_evidence, "baseline-results/validation-evidence.json")
        for probe in record.get("defense_probes", {}).get("results", []):
            directory = logs / probe["run_id"]
            if directory.parent != logs or not directory.is_dir():
                raise ValueError("Missing executed defense-probe evidence")
            if not provenance_matches(json_file(directory / "manifest.json")):
                raise ValueError("Executed defense-probe evidence belongs to an older runtime contract")
            for name in ("manifest.json", "episode.jsonl", "summary.json"):
                include(directory/name, f"sample-trajectories/defense-probes/{directory.name}/"+name)
    include(success, "sample-trajectories/success.jsonl")
    include(failure, "sample-trajectories/failure.jsonl")
    include(failure.parent / "manifest.json", "sample-trajectories/failure-provenance.json")
    for name in ("design-doc.pdf", "deck.pdf"):
        include(ROOT / "submission_bundle" / name, name)
    secrets = []
    if (ROOT / ".env").is_file():
        for line in (ROOT / ".env").read_text(encoding="utf-8-sig").splitlines():
            key, _, value = line.partition("=")
            if key.strip() in ("API_KEY", "HARNESS_SECRET") and len(value.strip()) > 8:
                secrets.append(value.strip().strip("\"'").encode())
    for name, data in selected.items():
        if any(secret in data for secret in secrets) or (not name.endswith(".pdf") and re.search(rb"sk-[A-Za-z0-9_-]{16,}", data)):
            raise ValueError(f"Potential credential detected in {name}; archive not written")
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(selected.items()):
            archive.writestr(name, data)
        archive.writestr("evidence-manifest.json", json.dumps({"team": team, "contract_version": CONTRACT_VERSION,
                          "harness_sha256": fingerprints()[0], "sandbox_sha256": fingerprints()[1],
                          "runtime_contract_sha256": contract_fingerprint(),
                          "qualifying_model_run_ids": [p.name for p in runs],
                          "diagnostic_provider_attempt_ids": diagnostic_attempts, "files": {n: hashlib.sha256(d).hexdigest() for n, d in selected.items()}}, indent=2))
    print(f"Validated archive: {output}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--team-name", default="Astro")
    parser.add_argument("--logs", type=Path, default=ROOT / "logs")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--docs-only", action="store_true")
    args = parser.parse_args()
    validate_documents(args.team_name)
    if not args.docs_only:
        assemble(args.team_name, args.logs, args.output or ROOT / f"{args.team_name}.zip")


if __name__ == "__main__":
    main()
