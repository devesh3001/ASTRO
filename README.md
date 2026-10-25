# AeroSentinel - Team ASTRO

A deterministic, simplified CubeSat recovery and mission-scheduling environment for tool-using language models. Agents repair software configurations; a separate programmatic verifier evaluates mission outcomes. This is an evaluation simulator, not validated flight software.

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
| `science_store_forward` | Hard | Acquire science in two windows, manage finite storage and deliver it over two contacts within energy allocations |

**Run A demo:** `eps_eclipse_recovery`, seed 0, with `APPEND_FILE=`. The sample is named `inputs/append.example.txt` so a fresh clone adds no extra prompt. For Run B, put the judge's file in `inputs/` and set its exact path.

The flight manual provides nominal operational guidance. `docs/mission.json` reports instance-specific success targets and fixed hardware properties. Seeds change faults, energy requirements and hardware/link profiles. The four hard families vary contact timing, exposure windows, payload, storage and energy allocations. Higher baud rates cost link margin; higher transmitter power costs energy. Nominal settings and maximum settings both fail its constraints. Agent edits cannot change protected mission metadata.

## Modes

Set these values in `.env` (or use shell environment overrides):

- `MODE=full`: baselines then model for `TASK_ID`.
- `MODE=baselines`: baselines for all nine tasks.
- `MODE=all`: all-task baselines then a model episode for each task.
- `MODEL=mock/idle`: offline agent that immediately finishes, useful for failure-path checks.

`APPEND_FILE` names a UTF-8 file inside `inputs/`. Its decoded text is appended without trimming or newline rewriting and recorded with its hash. An explicitly named missing, unreadable or blank file stops the run. For compatibility, an existing `inputs/append.txt` is also loaded when the setting is empty. Keep that filename absent for Run A.

## State, actions and scoring

State persists within an episode: elapsed orbital time, battery energy, temperature, scalar pointing error, downlinked data, payload energy and accumulated exposure. The science mission also tracks generated, buffered, delivered and permanently dropped science, peak storage occupancy and exposure for each window. Tools are `list_files`, `read_file`, `write_file`, `run_python`, `step_orbit` (1-30 minutes), and `finish`. Completed dispatched ordinary tool attempts consume 30 simulated seconds, including tool errors; schema-rejected calls do not execute or advance time; the mission window is 95 simulated minutes. Initial telemetry is generated from the actual seeded simulator state. `read_file` accepts character `offset` and `limit` (up to 3500) and reports the next offset for long files, so the complete manual is accessible without custom code.

The raw score combines power (0.35), thermal behavior (0.25), pointing (0.20) and communications (0.20). On the single-window observation mission these four weights are multiplied by 0.8, with 0.20 for exposure. On the coupled science mission they are multiplied by 0.6, with 0.20 for average per-window exposure completion and 0.20 for science delivery. Each science window has its own mandatory exposure check; extra exposure in another window cannot compensate. Every required check still has to pass. Reported reward measures progress beyond the no-op world:

```text
reward = clamp((raw_score - initial_raw_score) / (1 - initial_raw_score), 0, 1)
```

Logs include both raw components and normalized progress. Every required mission check must pass; a nearly met battery target cannot be offset by another score component. Failed required checks cap normalized reward below the success threshold. Final diagnostics name failed checks and report delivered data and RF energy use. Catastrophic hardware safety events remain latched and yield zero. Success targets and hardware facts are protected; increasing an agent deadband cannot relax scoring. Configuration validation runs even when orbital time has ended. Reference >= 0.95 and no-op <= 0.05 are checked automatically.

The model is deliberately approximate: linear SoC/voltage, scalar attitude response, linearized heat loss and a bandwidth-aware link-margin model. Integration uses a fixed global 30-second grid split at sunlight, contact and acquisition boundaries. Exponential scalar damping preserves the declared one-minute retention, so grouping the same elapsed time into different allowed tool calls does not change the physical trajectory. It does not claim three-axis rigid-body dynamics or flight certification.

## Isolation and limits

- Verifier and reward logic are in the harness container; no verifier code is copied into the sandbox.
- Every tool/control endpoint requires the dynamically rotated harness token.
- Agent code runs as UID 1001; evaluation scripts run as UID 1002. Identity lookup failures never fall back to root.
- Worker processes, including detached descendants, are killed after every command and before reset.
- The sandbox root filesystem is read-only. Workspace and temporary mounts have storage/inode bounds; workers have file, CPU, memory and output limits. The container enforces a 128-process/thread cgroup limit, avoiding UID-wide process limits that can break on shared CI hosts.
- Protected file access rejects symlinks, hard links and special files. The sandbox network has no external route.
- Execution ends at 40 model replies or 600 seconds, whichever comes first. Each reply allows 10 tools; each complete tool operation has a 60-second deadline. Final verification has a separate 120-second budget. Baselines share 120 seconds per task.
- Provider calls run in a cancellable process without hidden SDK retries. HTTP response bodies are streamed into bounded buffers before JSON decoding; this is a memory bound, not token streaming to the agent. At most two explicit, logged retries per episode handle transient connection, timeout, rate-limit or server failures within the same 600-second budget; authentication and malformed requests stop immediately. Failed requests that receive no reply do not consume an agent step. Received malformed replies count once and get corrective feedback. Context trimming conservatively bounds request size while retaining valid tool/result groups.

Docker and the host kernel remain part of the trust boundary. This is not a VM-level security guarantee. A failed task is an expected outcome when the verifier scores it honestly.

## Validation

With Python dependencies installed, run:

```sh
python tests/test_physics.py
python -m unittest tests.test_adversarial tests.test_hardening tests.test_packaging tests.test_mission_planning tests.test_temporal_missions tests.test_failure_feedback tests.test_provider_contract tests.test_consistency tests.test_science_mission tests.test_seeds_extended tests.test_judge_contract tests.test_mission_stress tests.test_supplemental_evidence
```

The seed suite checks actual reset hashes and both baselines for every registered task on seeds 0-19. Planning and science regressions additionally use separate seeds to check static-duty impossibility, nominal and maximum-radio shortcuts, buffer conservation, irreversible loss, per-window quotas, alternative schedules and protected requirements. Consistency regressions check identical physical histories across allowed tool-time groupings, strict mandatory configuration parsing and full live-schema script evaluation. Script checks use the current communications configuration plus perturbed full-schema cases; results are cached by script, communications configuration and seed, and configuration changes trigger re-evaluation. The provider contract suite sends actual HTTP requests only to local synthetic endpoints. It tests reply normalization, error classification and bounded response handling; it is not evidence of real model capability or cross-provider success. For Linux UID/network/resource tests:

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

Run the supplemental Run B checks without a provider key:

```sh
python scripts/check_runb_contract.py
python scripts/stress_missions.py
```

The first runs the actual SDK subprocess and episode runner against local synthetic HTTP fixtures. It checks exact Unicode/CRLF append text, opaque deployment names, malformed-reply recovery, signed metadata, retries and cancellation. It does not test the judge's undisclosed model or instruction obedience. The second uses independent plans derived only from public mission inputs across held-out generated seeds and corner profiles. It tests near-boundary energy/exposure choices, irreversible loss and alternative controllers through the simulator and a private local HTTP environment. It does not add task families or certify production isolation. Both reports explicitly record zero real-model calls and zero external-provider requests.

Reports are written to `logs/benchmarks/runb-contract.json` and `mission-stress.json`. Their runtime/build hashes, validation-code hashes, HTTP-fixture hash and actual case counts must match before packaging. Correctly rejected bad decisions are passing stress expectations, not successful mission episodes.

CI runs integration regressions, this policy comparison, all-task gates, an offline solver run and live sandbox checks, and uploads sanitized logs. Offline solver results validate plumbing; they are not real-model capability evidence.

## Present and defend the project

Use [the timed rehearsal and answer key](docs/rehearsal-pack.md) to practice the talk and six-minute Q&A; it includes a human-filled mastery rubric. Use [the submission handoff](docs/submission-handoff.md) to record organizer access and the actual submission receipt. Read [the presentation guide](docs/presentation-defense.md) for the ten-minute talk, demo procedure, likely questions and explicit limitations. [Model assumptions](docs/model-assumptions.md) explains the equations and controls. [The PS checklist](docs/ps-checklist.md) separates tested requirements from administrative items.

The first five tasks are guided recovery exercises. The four hard tasks test radio planning, an irreversible contact deadline, observation scheduling, and coupled acquisition/storage/downlink planning. In the science mission, the first contact must free buffer space before the second acquisition; contacts cannot deliver data that has not been acquired. Generated science always equals buffered plus delivered plus dropped science. Every window quota, both energy allocations and the no-loss requirement must pass. A constant payload duty cannot meet both the exposure and full-episode energy constraints; see the proof in the model assumptions. This is an environment for evaluating agents; no policy is trained here. The reference policy is privileged validation code, never a model-performance claim.

## Logs and submission

Each run writes `gate.json`, JSONL records, a unique run identifier and a source fingerprint manifest under `logs/`. Model runs also write a summary and episode termination, verifier reasons and diagnostics. Credentials are never part of the manifest. Contract v3 records the harness hash, running sandbox source hash and `runtime_contract_sha256`. The latter covers both Dockerfiles, Compose, `.dockerignore` and dependency files, using normalized line endings and copies baked into the harness image. It identifies the expected build contract; it does not attest the active Docker daemon, effective mounts or network policy.

The registered team name is **ASTRO**. The reviewed two-page design document and ten-slide deck are tracked in `submission_bundle/`. Validate those PDFs and build the reference archive with:

```sh
python scripts/build_submission_docs.py --team-name ASTRO
```

Use `--docs-only` to validate the reviewed PDFs without building an archive. Packaging preserves those documents; it does not regenerate them from an older template. Packaging requires current-code all-task gates, at least three genuine model runs, and success/failure trajectories. It validates episode records, matching source and runtime-contract fingerprints, summaries and credential scans. Version 2 manifests and incomplete runtime-contract hashes cannot certify the current source. Reviewed validation summaries and referenced defense probes must also match contract v3. It refuses to relabel historical logs or manufacture missing evidence. Zero-reply provider failures are preserved separately as diagnostic attempts and never count toward the three complete runs. A failure sample may be an executed scripted adversarial regression, explicitly identified in failure-provenance.json; such a sample never counts as one of the three genuine model runs.

The output is `ASTRO.zip`, containing `baseline-results/`, `test-run-logs/`, `sample-trajectories/`, `design-doc.pdf`, `deck.pdf` and a checksum manifest. Current supplemental reports are included under `baseline-results/`, with rehearsal/handoff notes under `presentation-support/`. Existing historical runs may be kept locally but do not certify changed code. Read current run IDs and measured results from the final validation record; earlier eight-task results do not establish nine-task or updated-provider performance. The repository remains public at the user's explicit request, which does not meet the PS private-repository requirement. Repository and archive names match **ASTRO**. Write-access invitations to **ayushmothiya1** and **rahulgpw** have been sent and await acceptance. The organizer deadline is **25 October 2026, 10:15 AM IST**; only earlier commits and the latest earlier form submission count. The team will complete the form itself; eligibility and actual submission remain human checks.
