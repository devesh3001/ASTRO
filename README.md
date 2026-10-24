# AeroSentinel - Team Astro

A deterministic, simplified CubeSat fault-recovery environment for tool-using language models. Agents repair software configurations; a separate programmatic verifier evaluates mission outcomes. This is an evaluation simulator, not validated flight software.

## Run from a fresh clone

Copy `.env.example` to `.env`, fill in model credentials if using a real model, and run:

```sh
docker compose up --build --abort-on-container-exit --exit-code-from harness
```

The default `MODEL=mock/solver` runs offline. Both baselines must pass before a model is called. Judges change only `MODEL`, `API_KEY`, `BASE_URL`, `TASK_ID`, optional `SEED`, and `APPEND_FILE`. No provider/model is special-cased.

| Task | Difficulty | Goal |
|---|---|---|
| `eps_eclipse_recovery` | Easy | Recover generation and meet the instance's eclipse-energy target |
| `adcs_detumble_pointing` | Easy | Remove momentum bias and meet a protected pointing requirement |
| `thermal_battery_preservation` | Medium | Recover safe battery temperature regulation |
| `rf_downlink_closure` | Medium | Restore transmitter settings and repair the dynamic RF calculation |
| `safe_mode_cascade` | Medium | Recover two seeded subsystem faults without an irreversible safety event |

**Run A demo:** `eps_eclipse_recovery`.

The flight manual provides nominal operational guidance. `docs/mission.json` reports instance-specific success targets and fixed hardware properties. Seeds change faults, energy requirements and hardware/link profiles. Agent edits cannot change protected mission metadata.

## Modes

Set these values in `.env` (or use shell environment overrides):

- `MODE=full`: baselines then model for `TASK_ID`.
- `MODE=baselines`: baselines for all five tasks.
- `MODE=all`: all-task baselines then a model episode for each task.
- `MODEL=mock/idle`: offline agent that immediately finishes, useful for failure-path checks.

`APPEND_FILE` names a UTF-8 file inside `inputs/`. Its decoded text is appended without trimming or newline rewriting and recorded with its hash. An explicitly named missing, unreadable or blank file stops the run.

## State, actions and scoring

State persists within an episode: elapsed orbital time, battery energy, temperature, scalar pointing error and downlinked data. Tools are `list_files`, `read_file`, `write_file`, `run_python`, `step_orbit` (1-30 minutes), and `finish`. Ordinary successful tool execution consumes 30 simulated seconds; the mission window is 95 simulated minutes. Initial telemetry is generated from the actual seeded simulator state.

The raw score combines power (0.35), thermal behavior (0.25), pointing (0.20) and communications (0.20). Reported reward measures progress beyond the no-op world:

```text
reward = clamp((raw_score - initial_raw_score) / (1 - initial_raw_score), 0, 1)
```

Logs include both raw components and normalized progress. Catastrophic hardware safety events remain latched and yield zero. Success targets and hardware facts are protected; increasing an agent deadband cannot relax scoring. Configuration validation runs even when orbital time has ended. Reference >= 0.95 and no-op <= 0.05 are checked automatically.

The model is deliberately approximate: linear SoC/voltage, scalar attitude response, linearized heat loss and a baud-limited link-margin model. It does not claim three-axis rigid-body dynamics or flight certification.

## Isolation and limits

- Verifier and reward logic are in the harness container; no verifier code is copied into the sandbox.
- Every tool/control endpoint requires the dynamically rotated harness token.
- Agent code runs as UID 1001; evaluation scripts run as UID 1002. Identity lookup failures never fall back to root.
- Worker processes, including detached descendants, are killed after every command and before reset.
- The sandbox root filesystem is read-only. Workspace and temporary mounts have storage/inode bounds; workers have file, CPU, process, memory and output limits.
- Protected file access rejects symlinks, hard links and special files. The sandbox network has no external route.
- Execution ends at 40 model replies or 600 seconds, whichever comes first. Each reply allows 10 tools; each complete tool operation has a 60-second deadline. Final verification has a separate 120-second budget. Baselines share 120 seconds per task.
- Provider calls run in a cancellable process without hidden SDK retries. Received malformed replies count once and get corrective feedback. Context trimming conservatively bounds request size while retaining valid tool/result groups.

Docker and the host kernel remain part of the trust boundary. This is not a VM-level security guarantee. A failed task is an expected outcome when the verifier scores it honestly.

## Validation

With Python dependencies installed, run:

```sh
python tests/test_physics.py
python -m unittest tests.test_adversarial tests.test_hardening tests.test_seeds_extended
```

The seed suite checks actual reset hashes and both baselines across 100 task/seed combinations. For Linux UID/network/resource tests:

```sh
docker compose up -d sandbox
docker compose run --rm -v "${PWD}/tests:/app/tests:ro" -e FAIL_ON_UNAVAILABLE=1 harness python -m unittest tests.test_docker_security
docker compose down
```

The runtime is pinned by image digest and Linux x86-64 wheel hashes in `requirements.lock`. To measure an uncached build without clearing other projects' caches:

```sh
python scripts/check_cold_build.py
```

CI runs integration regressions, all-task gates, an offline solver run and live sandbox checks, and uploads sanitized logs. Offline solver results validate plumbing; they are not real-model capability evidence.

## Logs and submission

Each run writes `gate.json`, JSONL records, a unique run identifier and a source fingerprint manifest under `logs/`. Model runs also write a summary and episode termination, verifier reasons and diagnostics. Credentials are never part of the manifest.

The registered team name is **Astro**. Generate the two-page design document, ten-slide deck and one archive with:

```sh
python scripts/build_submission_docs.py --team-name Astro
```

Document generation requires `reportlab==4.4.9`. Use `--docs-only` to regenerate PDFs without an archive. Packaging requires current-code all-task gates, at least three genuine model runs, and success/failure trajectories. It validates episode records, matching source fingerprints, summaries and credential scans. It refuses to relabel historical logs or manufacture missing evidence. A failure sample may be an executed scripted adversarial regression, explicitly identified in failure-provenance.json; such a sample never counts as one of the three genuine model runs.

The output is `Astro.zip`, containing `baseline-results/`, `test-run-logs/`, `sample-trajectories/`, `design-doc.pdf`, `deck.pdf` and a checksum manifest. Existing historical runs may be kept locally but do not certify changed code. Confirm the repository name, private visibility and organiser access against the submission instructions before uploading.
