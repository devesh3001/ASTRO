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
| `rf_energy_tradeoff` | Hard | Deliver a seeded payload within a short contact and RF energy allocation |
| `early_pass_recovery` | Hard | Recover pointing and radio settings before an early contact is lost |
| `observation_window_scheduling` | Hard | Schedule instrument exposure within a window and energy allocation |

**Run A demo:** `eps_eclipse_recovery`, seed 0, with `APPEND_FILE=`. The sample is named `inputs/append.example.txt` so a fresh clone adds no extra prompt. For Run B, put the judge's file in `inputs/` and set its exact path.

The flight manual provides nominal operational guidance. `docs/mission.json` reports instance-specific success targets and fixed hardware properties. Seeds change faults, energy requirements and hardware/link profiles. The hard mission also varies contact timing, duration, payload and radio energy allocation. Higher baud rates cost link margin; higher transmitter power costs energy. Nominal settings and maximum settings both fail its constraints. Agent edits cannot change protected mission metadata.

## Modes

Set these values in `.env` (or use shell environment overrides):

- `MODE=full`: baselines then model for `TASK_ID`.
- `MODE=baselines`: baselines for all eight tasks.
- `MODE=all`: all-task baselines then a model episode for each task.
- `MODEL=mock/idle`: offline agent that immediately finishes, useful for failure-path checks.

`APPEND_FILE` names a UTF-8 file inside `inputs/`. Its decoded text is appended without trimming or newline rewriting and recorded with its hash. An explicitly named missing, unreadable or blank file stops the run. For compatibility, an existing `inputs/append.txt` is also loaded when the setting is empty. Keep that filename absent for Run A.

## State, actions and scoring

State persists within an episode: elapsed orbital time, battery energy, temperature, scalar pointing error, downlinked data, payload energy and accumulated observation exposure. Tools are `list_files`, `read_file`, `write_file`, `run_python`, `step_orbit` (1-30 minutes), and `finish`. Ordinary successful tool execution consumes 30 simulated seconds; the mission window is 95 simulated minutes. Initial telemetry is generated from the actual seeded simulator state. `read_file` accepts character `offset` and `limit` (up to 3500) and reports the next offset for long files, so the complete manual is accessible without custom code.

The raw score combines power (0.35), thermal behavior (0.25), pointing (0.20) and communications (0.20). On observation missions these four weights are multiplied by 0.8, and exposure completion contributes the remaining 0.20. Every required check still has to pass. Reported reward measures progress beyond the no-op world:

```text
reward = clamp((raw_score - initial_raw_score) / (1 - initial_raw_score), 0, 1)
```

Logs include both raw components and normalized progress. Every required mission check must pass; a nearly met battery target cannot be offset by another score component. Failed required checks cap normalized reward below the success threshold. Final diagnostics name failed checks and report delivered data and RF energy use. Catastrophic hardware safety events remain latched and yield zero. Success targets and hardware facts are protected; increasing an agent deadband cannot relax scoring. Configuration validation runs even when orbital time has ended. Reference >= 0.95 and no-op <= 0.05 are checked automatically.

The model is deliberately approximate: linear SoC/voltage, scalar attitude response, linearized heat loss and a bandwidth-aware link-margin model. Integration splits at sunlight and contact boundaries, so tool timing cannot create extra contact time. It does not claim three-axis rigid-body dynamics or flight certification.

## Isolation and limits

- Verifier and reward logic are in the harness container; no verifier code is copied into the sandbox.
- Every tool/control endpoint requires the dynamically rotated harness token.
- Agent code runs as UID 1001; evaluation scripts run as UID 1002. Identity lookup failures never fall back to root.
- Worker processes, including detached descendants, are killed after every command and before reset.
- The sandbox root filesystem is read-only. Workspace and temporary mounts have storage/inode bounds; workers have file, CPU, memory and output limits. The container enforces a 128-process/thread cgroup limit, avoiding UID-wide process limits that can break on shared CI hosts.
- Protected file access rejects symlinks, hard links and special files. The sandbox network has no external route.
- Execution ends at 40 model replies or 600 seconds, whichever comes first. Each reply allows 10 tools; each complete tool operation has a 60-second deadline. Final verification has a separate 120-second budget. Baselines share 120 seconds per task.
- Provider calls run in a cancellable process without hidden SDK retries. At most two explicit, logged retries per episode handle transient connection, timeout, rate-limit or server failures within the same 600-second budget; authentication and malformed requests stop immediately. Failed requests that receive no reply do not consume an agent step. Received malformed replies count once and get corrective feedback. Context trimming conservatively bounds request size while retaining valid tool/result groups.

Docker and the host kernel remain part of the trust boundary. This is not a VM-level security guarantee. A failed task is an expected outcome when the verifier scores it honestly.

## Validation

With Python dependencies installed, run:

```sh
python tests/test_physics.py
python -m unittest tests.test_adversarial tests.test_hardening tests.test_packaging tests.test_mission_planning tests.test_temporal_missions tests.test_failure_feedback tests.test_seeds_extended
```

The seed suite checks actual reset hashes and both baselines across 160 task/seed combinations (eight tasks, seeds 0-19). Planning regressions additionally check 100 separate seeds (100-199) against nominal and maximum-radio shortcuts, exact contact capacity, alternative plans, and protected requirements. For Linux UID/network/resource tests:

```sh
docker compose up -d sandbox
docker compose run --rm -v "${PWD}/tests:/app/tests:ro" -e FAIL_ON_UNAVAILABLE=1 harness python -m unittest tests.test_docker_security
docker compose down
```

The runtime is pinned by image digest and Linux x86-64 wheel hashes in `requirements.lock`. To measure an uncached build without clearing other projects' caches:

```sh
python scripts/check_cold_build.py
```

Compare four scripted policies through the HTTP environment on seeds separate from the integration suite:

```sh
python scripts/benchmark_policies.py
```

The report is written to `logs/benchmarks/policy-ablation.json`. It labels the reference policy as privileged and never calls a model API. It measures shortcut resistance, not language-model ability.

CI runs integration regressions, this policy comparison, all-task gates, an offline solver run and live sandbox checks, and uploads sanitized logs. Offline solver results validate plumbing; they are not real-model capability evidence.

## Present and defend the project

Read [the presentation guide](docs/presentation-defense.md) for the ten-minute talk, demo procedure, likely questions and explicit limitations. [Model assumptions](docs/model-assumptions.md) explains the equations and controls. [The PS checklist](docs/ps-checklist.md) separates tested requirements from administrative items.

The first five tasks are guided recovery exercises. The three hard tasks test radio planning, an irreversible contact deadline, and observation scheduling. This is an environment for evaluating agents; no policy is trained here. The reference policy is privileged validation code, never a model-performance claim.

## Logs and submission

Each run writes `gate.json`, JSONL records, a unique run identifier and a source fingerprint manifest under `logs/`. Model runs also write a summary and episode termination, verifier reasons and diagnostics. Credentials are never part of the manifest.

The registered team name is **Astro**. The reviewed two-page design document and ten-slide deck are tracked in `submission_bundle/`. Validate those PDFs and build the reference archive with:

```sh
python scripts/build_submission_docs.py --team-name Astro
```

Use `--docs-only` to validate the reviewed PDFs without building an archive. Packaging preserves those documents; it does not regenerate them from an older template. Packaging requires current-code all-task gates, at least three genuine model runs, and success/failure trajectories. It validates episode records, matching source fingerprints, summaries and credential scans. It refuses to relabel historical logs or manufacture missing evidence. Zero-reply provider failures are preserved separately as diagnostic attempts and never count toward the three complete runs. A failure sample may be an executed scripted adversarial regression, explicitly identified in failure-provenance.json; such a sample never counts as one of the three genuine model runs.

The output is `Astro.zip`, containing `baseline-results/`, `test-run-logs/`, `sample-trajectories/`, `design-doc.pdf`, `deck.pdf` and a checksum manifest. Existing historical runs may be kept locally but do not certify changed code. Confirm the repository name, private visibility and organiser access against the submission instructions before uploading.
