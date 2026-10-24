"""Generate accurate PDFs and one evidence-validated team archive."""
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


def fingerprints():
    harness = hashlib.sha256(b"".join(p.name.encode() + p.read_bytes().replace(b"\r\n", b"\n")
                                   for p in sorted((ROOT / "harness").glob("*.py")))).hexdigest()
    sandbox = hashlib.sha256(b"".join((ROOT / "sandbox" / name).read_bytes() for name in
                                   ("server.py", "runtime.py")).replace(b"\r\n", b"\n")).hexdigest()
    return harness, sandbox


def json_file(path):
    return json.loads(path.read_text(encoding="utf-8"))


def collect_evidence(logs):
    harness, sandbox = fingerprints()
    gates, runs, successes, failures = [], [], [], []
    for directory in sorted(logs.iterdir() if logs.is_dir() else [], reverse=True):
        if not directory.is_dir():
            continue
        try:
            manifest = json_file(directory / "manifest.json")
            if (manifest.get("contract_version") != 2 or manifest.get("harness_sha256") != harness
                    or manifest.get("sandbox_sha256") != sandbox):
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
    return gates[0], runs[:3], successes[0], failures[0]


def generate_pdfs(team):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak
    from reportlab.pdfgen import canvas
    from reportlab.lib.utils import simpleSplit
    folder = ROOT / "submission_bundle"
    folder.mkdir(exist_ok=True)
    styles = getSampleStyleSheet()
    story = []
    def paragraph(title, body):
        story.extend([Paragraph(title, styles["Heading2"]), Paragraph(body, styles["BodyText"]), Spacer(1, 7)])
    story.append(Paragraph(f"AeroSentinel | Team {team}", styles["Title"]))
    paragraph("Environment and tasks", "A deterministic, simplified CubeSat fault-recovery environment. Six tasks across three difficulty levels cover electrical power, pointing control, thermal regulation, downlink software, a seeded two-subsystem cascade and hard RF contact planning. The demo is eps_eclipse_recovery. State includes battery energy, temperature, scalar pointing error, elapsed time and downloaded data. No agent training is required.")
    paragraph("Actions and observations", "Agents inspect files, edit software controls, run bounded Python and advance orbital time by 1-30 minutes. Ordinary actions consume 30 simulated seconds; the mission lasts 95 simulated minutes. Reset telemetry comes from the protected simulator. Mission targets and fixed hardware are exposed in docs/mission.json for planning; editing that file cannot change protected requirements.")
    paragraph("Reward design", "The raw mission score combines power (0.35), thermal behavior (0.25), pointing (0.20) and communications (0.20). The reported reward is max(0, (raw - initial raw) / (1 - initial raw)), capped at one. This measures progress over the no-op state. Every required check must pass; failed requirements cap normalized reward below success. Diagnostics identify failed checks and report RF energy use. Hardware safety failures are permanently latched and score zero. The pointing target is protected; final configuration validation runs even at the mission boundary.")
    paragraph("Seeded variation", "Seeds vary faults, propagation loss, antenna hardware and EPS energy targets. The hard contact mission varies pass timing, payload and an RF energy allocation: nominal and maximum radio settings both fail, while instance-specific feasible plans pass. Reference configurations retain the instance's actual hardware values. The manual supplies operational guidance and nominal software values; this is intentionally a guided recovery environment, not a claim of zero answer leakage.")
    paragraph("Physics assumptions", "The model uses a scalar pointing approximation, linear state-of-charge voltage, linearized thermal loss, a bandwidth-aware link-margin equation and baud-limited data transfer. Integration splits at contact and sunlight boundaries; RF bus energy is integrated over the actual pass. It is useful for controlled evaluation, not validated flight software or a high-fidelity spacecraft simulator.")
    story.append(PageBreak())
    paragraph("Isolation and reset", "Verifier and reward code run only in the harness container. Every tool endpoint requires the harness token. Agent and evaluation code use separate UIDs (1001 and 1002). All worker processes, including detached descendants, are killed after execution and before reset. The sandbox has no external route, a read-only root, bounded workspace/tmp mounts and process, CPU, memory, file and output limits. Protected file access rejects symlinks and hard links.")
    paragraph("Harness contract", "One Docker Compose command runs reference and no-op checks before the model. Baselines have a combined 120-second per-task budget. Model execution allows 40 replies or 600 seconds; each reply allows 10 tools and each tool operation is bounded to 60 seconds. A cancellable provider process bounds API calls. Final verification has its own 120-second limit. Model, key and endpoint use the same interface for all providers. Judge append text is preserved and logged.")
    paragraph("Telemetry and reproducibility", "JSONL records contain model inputs, raw response text, parsed actions, tool results, errors, reward and state hash. Final records include termination and verifier diagnostics. Manifests fingerprint the harness and sandbox source. Packaging accepts only evidence matching the current code, with complete reply sequences and consistent summaries. No credentials belong in logs or archives.")
    paragraph("Known limitations and tests", "The physics, task generator and reward targets remain simplified. UID isolation is not a VM boundary and still relies on Docker and the host kernel. Tests cover baseline thresholds, 120 task/seed combinations plus 100 separate shortcut checks, malformed replies, context pressure, timeout recovery, hardware edits, post-orbit invalid configurations, and live container process/resource isolation. Real-provider results and clean-build timing must be taken from supplied logs; no fixed performance claims are embedded here.")
    paragraph("Submission", f"Submit {team}.zip with all-task baseline results, at least three genuine model runs, success/failure trajectories and these two PDFs. The GitHub repository should match the registered team name and organiser visibility/access requirements. Historical logs remain historical; the packager refuses to manufacture missing evidence.")
    SimpleDocTemplate(str(folder / "design-doc.pdf"), pagesize=letter, topMargin=32, bottomMargin=32).build(story)
    slides = [
        (f"AeroSentinel | {team}", ["Deterministic satellite fault-recovery environment", "Six tasks; three difficulty levels; separate verifier", "Demo task: eps_eclipse_recovery"]),
        ("What the agent must do", ["Inspect symptoms and live telemetry", "Repair software controls while preserving hardware facts", "Recover power, temperature, pointing and downlink outcomes"]),
        ("State and actions", ["Battery energy, temperature, scalar pointing, data and orbital time", "Read/write files, run bounded Python, advance orbit, finish", "Every reset reconstructs the seeded world"]),
        ("Simplified coupled model", ["95-minute orbit with sunlight, eclipse and a ground pass", "Energy balance and linear voltage; scalar pointing response", "Linearized heat loss and baud-limited communications", "Explicit approximation, not a flight-certification claim"]),
        ("Task depth and variation", ["EPS, ADCS, thermal, RF software and two-fault cascade", "Seeded hardware, contact windows, payload and RF energy", "Hard contact planning needs rate/power/energy tradeoffs"]),
        ("Reward integrity", ["Fixed success targets cannot be relaxed by agent settings", "Progress is normalized against the initial no-op outcome", "Catastrophic failures remain latched", "Final configurations are checked even after virtual time ends"]),
        ("Runtime isolation", ["Verifier in harness; untrusted workers in sandbox", "Separate agent/evaluation UIDs; authenticated endpoints", "Read-only root, bounded storage, CPU, processes and output", "Detached workers killed before the next episode"]),
        ("Unknown-model resilience", ["Same OpenAI-compatible endpoint contract for every model", "Malformed replies receive feedback and consume one step", "40 replies, 10 calls per reply, 600-second execution cap", "Conservative context trimming preserves valid tool groups"]),
        ("Evidence and regression tests", ["Present the generated gate.json and sanitized model logs", "Regression cases reproduce the scoring and reset defects", "Source fingerprints prevent stale evidence being relabeled", "A failed task is acceptable when correctly scored and explained"]),
        ("Live demonstration and limitations", ["Run the demo; show telemetry, score components and termination", "Show the adversarial tests and a genuine failure trajectory", "Known limits: simplified physics and kernel/container reliance", "Submission: one Astro.zip with current-code evidence"]),
    ]
    pdf = canvas.Canvas(str(folder / "deck.pdf"), pagesize=landscape(letter))
    width, height = landscape(letter)
    for index, (title, bullets) in enumerate(slides, 1):
        pdf.setFillColor(colors.HexColor("#101c30")); pdf.rect(0, 0, width, height, fill=1, stroke=0)
        pdf.setFillColor(colors.white); pdf.setFont("Helvetica-Bold", 26)
        pdf.drawString(44, height - 65, title)
        pdf.setFont("Helvetica", 18); y = height - 140
        for bullet in bullets:
            for line_index, line in enumerate(simpleSplit(bullet, "Helvetica", 18, width - 130)):
                pdf.drawString(54 if line_index == 0 else 74, y, ("- " if line_index == 0 else "") + line)
                y -= 27
            y -= 25
        pdf.setFont("Helvetica", 10); pdf.drawString(44, 26, f"AeroSentinel | Team {team} | {index}/10")
        pdf.showPage()
    pdf.save()


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
        archive.writestr("evidence-manifest.json", json.dumps({"team": team, "harness_sha256": fingerprints()[0],
                          "sandbox_sha256": fingerprints()[1], "files": {n: hashlib.sha256(d).hexdigest() for n, d in selected.items()}}, indent=2))
    print(f"Validated archive: {output}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--team-name", default="Astro")
    parser.add_argument("--logs", type=Path, default=ROOT / "logs")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--docs-only", action="store_true")
    args = parser.parse_args()
    generate_pdfs(args.team_name)
    if not args.docs_only:
        assemble(args.team_name, args.logs, args.output or ROOT / f"{args.team_name}.zip")


if __name__ == "__main__":
    main()
