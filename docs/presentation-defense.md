# Team ASTRO: ten-minute presentation and defense

Use `submission_bundle/deck.pdf` for the required PDF presentation. Use the editable deck's speaker notes for pacing and this guide for rehearsal. Every measured result belongs to the source and runtime-contract hashes in its validation record. After a runtime change, earlier successful logs remain historical. Report current-source evidence explicitly, including missing or failed evidence.

## Talk sequence

| Slide and actual title | Stopwatch | What to explain |
|---|---:|---|
| 1. AeroSentinel | 0:00-0:35 | Introduce an evaluation environment for satellite recovery and planning. Judges supply the agent; no policy is trained. |
| 2. The problem: a repair can arrive too late | 0:35-1:20 | Explain why time, energy and storage make the order of decisions matter. |
| 3. How an episode works | 1:20-2:15 | Explain observation, tools and protected evaluation. Distinguish simulated mission time from the execution budget. |
| 4. Nine tasks, with increasing planning depth | 2:15-3:05 | Identify the five guided repairs and four harder planning missions, with seeded faults and three difficulty levels. |
| 5. The planning test: collect, transmit, repeat | 3:05-4:20 | Trace science seed 101 through two acquisitions and contacts. Explain the finite buffer and constant-duty contradiction. |
| 6. What earns a successful score | 4:20-5:20 | Explain clamped no-op normalization and mandatory checks. Healthy subsystems cannot compensate for a missed quota. |
| 7. The agent cannot award itself success | 5:20-6:20 | Explain independent verification and six scripted failure probes. State the remaining Docker/kernel trust. |
| 8. Validation on the final source | 6:20-7:25 | Distinguish 150 regressions, 12 real-container isolation checks, nine gates and 474 stress expectations: 80 feasible plans and 394 correctly rejected choices. |
| 9. What the organizer model actually achieved | 7:25-8:40 | Report four genuine gpt-6-luna runs, three successes and one observation planning failure. Do not generalize a success rate from four attempts. |
| 10. Demo: EPS eclipse recovery | 8:40-10:00 | Show Run A final mission checks, or clearly label the saved trajectory if using it. State scientific and validation limits. Finish within ten minutes. |

The live PS slot has presentations and runs in parallel. Begin the talk while the run executes. Keep the demo configuration ready and preserve the allotted question time.

## Demo procedure

Set `MODEL`, `API_KEY` and `BASE_URL` locally from the organizer email. Use `TASK_ID=eps_eclipse_recovery`, `SEED=0`, `MODE=full` and an empty `APPEND_FILE` for Run A-style validation. Keep `inputs/append.txt` absent because that compatibility filename auto-loads when present. The tracked example is `inputs/append.example.txt`. Never show `.env` or a full Compose configuration containing secrets.

```sh
docker compose up --build --abort-on-container-exit --exit-code-from harness
```

Show the oracle/no-op gate, per-reply telemetry, final reward and named verifier checks. For the hard science mission, use a recorded current-source trajectory to explain acquisition, storage and delivery without spending the whole talk waiting for a second run. If the trajectory is scripted, label it as a privileged feasibility or adversarial demonstration, never a model discovery.

## Evidence to prepare

Use the final validation record for exact test totals, seeds, run IDs, durations and CI links. The talk must distinguish local deterministic regressions, live Docker isolation checks, privileged reference/scripted policies, local synthetic provider responses and actual model episodes. Present current-source failures alongside successes. A zero-reply transport attempt is diagnostic evidence and does not count toward three complete genuine model runs. If fresh evidence is incomplete, state the gap rather than substituting older eight-task figures.

## Likely questions

**Where is the reinforcement learning?** This is an environment with observations, actions, transitions and reward. We do not train a policy. The PS says judges bring the agent and no training is required.

**Are the tasks just changing constants from a manual?** Five introductory families are guided repairs. Four hard families add radio-energy tradeoffs, an irreversible early contact, timed observation and coupled science acquisition/storage/downlink. We report the task mix honestly rather than claiming equal difficulty.

**Why is the science mission deeper?** The agent must acquire each window's quota, preserve finite storage, deliver only acquired data during contacts and obey two energy allocations. Missing the first contact can overflow the buffer during the second acquisition. A constant duty needs at least 0.90 for exposure but is limited to roughly 0.101-0.152 by total energy. Correct final constants alone cannot solve it.

**Can one long exposure make up for a missed acquisition?** No. Each protected window has an independent check. Aggregate science or exposure cannot replace a missing window. Generated data must equal buffered plus delivered plus dropped data, and success permits no loss.

**Can a contact transmit data that does not exist?** No. Delivered science is bounded by both physical channel capacity and current protected buffer occupancy. A later repair cannot reconstruct missed acquisition or dropped science.

**Is the satellite physics realistic?** It is a declared benchmark approximation: energy integration, scalar pointing, lumped temperature, a simplified link budget and continuous data volumes. It does not model rigid-body attitude, packet queues or flight validation. Exponential scalar damping and a fixed global 30-second grid with event splits make permitted time groupings consistent. See `model-assumptions.md`.

**How can a no-op score zero if some subsystems are healthy?** Reward measures progress beyond the initial projected outcome. Raw components and the baseline are logged. Every required condition must hold for success, and catastrophic failure gives zero.

**Could high scores hide a failed condition?** No. The verifier checks required conditions separately from the weighted sum. A failure cannot earn a success score even if other components are strong.

**Can edits to telemetry or mission.json pass?** Grading reads protected simulation state and mission facts, not workspace copies. Executed probes test this. Hardware parameter violations and catastrophic events are latched.

**Does the reference policy get answers?** Yes. It is privileged feasibility code using the same public tools and action-time costs. It does not measure model discovery. Actual model runs are labeled separately by deployment, seed and matching provenance.

**Must the agent follow your exact plan?** No. Checks evaluate outcomes and invariants. Regressions accept feasible alternatives. They do not compare file text to the reference solution.

**Can a script recognize your hidden tests?** The evaluator includes the exact live communications schema plus perturbed full-schema cases. Its cache includes script, communications content and seed, so editing the configuration triggers another check. This closes specified shortcuts; finite behavioral fixtures do not prove arbitrary malicious code is impossible.

**What if a configuration value is malformed?** Mandatory missing or non-numeric fields are rejected rather than silently replaced with nominal values. Documented optional absent-field defaults remain supported. Final validation still runs after orbital time ends.

**How is the verifier protected?** It runs in the harness, outside the agent workspace. The agent uses UID 1001 and behavioral evaluation uses UID 1002. Authenticated controls, read-only filesystem, network isolation, storage/process/CPU/output bounds and worker cleanup provide measured defenses. Docker and the host kernel remain trusted.

**What happens to malformed replies and network failures?** Received malformed replies consume a step and receive corrective feedback. Malformed tool calls do not execute. Provider bodies are read with a size bound, and the subprocess is cancellable. At most two logged transient retries share the same episode deadline. Authentication or malformed request failures stop immediately; transport failure and termination remain in the evidence.

**Do fake-provider tests prove portability?** They exercise real local HTTP and subprocess paths using deliberately synthetic replies and errors. They validate a protocol contract, not an unknown provider's behavior or a model's task performance. Report only genuinely tested deployments as model evidence.

**What does the v3 hash prove?** It binds source plus expected Docker, Compose and dependency inputs, with copies baked into the harness image. It is not active-daemon attestation and cannot prove that effective mounts or network settings match the file. Separate live tests supply runtime evidence.

**Why keep older logs?** They show history without pretending to certify changed code. Packaging requires complete current-source episodes and contract v3; historical successes are not relabeled.

**What remains weak?** Finite adversarial coverage, simplified dynamics and unproven performance with an undisclosed model. The task mix still includes guided repairs. Neither test success nor a presentation rating guarantees a judge's score.

**Is the submission fully compliant?** Public visibility is an explicit user decision and conflicts with the PS private-repository instruction. Repository and archive both use exact name ASTRO. Organizer invitations await acceptance. The user will submit the official form before 25 October 2026, 10:15 AM IST. Team eligibility remains a human attestation. Technical evidence cannot substitute for them.

## Current-source live examples

These four gpt-6-luna attempts use the v3 source/build hashes in `submission_bundle/validation-evidence.json`. They are a small measured sample, not a general success-rate estimate. Each episode received actual model replies. No provider failures occurred in this batch.

| Mission | Seed | Append | Outcome | Replies | Harness seconds |
|---|---:|---|---|---:|---:|
| EPS demo | 0 | None | Success, 1.0 | 11 | 49.440 |
| Radio energy | 101 | Sample | Success, 1.0 | 11 | 58.971 |
| Science store and forward | 101 | Sample | Success, 1.0 | 28 | 193.683 |
| Observation scheduling | 101 | Sample | Failure, 0.8 | 12 | 94.388 |

**Explain the science success.** Run `20261024T210515-082614Z-a0efc859` selected 38,400 baud and 32.5 dBm, a valid alternative to the reference's 32.7 dBm. It collected 270 seconds in the first window and 240 in the second. The first contact drained 513 kB before a further 456 kB was collected. Actual delivery reached 969 kB against the 923.4 kB target, with zero loss. Peak storage was 513/598.5 kB, payload energy 1.00/1.08 Wh and radio energy 1.540875/1.625752 Wh. Tool telemetry confirms actual delivery. These are measured counters; the model's printed hypothesis contains arithmetic errors and is not authoritative.

**Explain the observation failure.** Run `20261024T210831-941520Z-b1981481` had a window at mission elapsed time 1,200–2,100 seconds and needed 810 seconds of exposure. The agent spent 0.5 Wh before disabling the initially active payload at MET 300. It re-enabled at MET 1,380, leaving only 720 useful seconds, then left it active until MET 2,340. Its 1,260 active seconds consumed 2.10 Wh against 1.80 Wh. The verifier correctly failed both exposure and energy, while healthy subsystem components earned 0.8. Its own final diagnostic reported both failed requirements. This is a real planning failure, with no provider or tool error.

Both final histories were independently replayed from their logged actions; counters and checks matched within 1e-5. Simulated mission time is distinct from UTC reply timestamps and the 600-second harness budget. The sample append is only a smoke test; the judge's unknown append remains untested.

## Statements to avoid

Do not say "unbreakable," "production-ready satellite software," "all models pass," "zero answer leakage," "10/10 guaranteed," or "100% PS compliant." Do not count reference policies, offline solvers, scripted attacks or synthetic endpoint tests as genuine model runs. A success on one deployment and seed does not establish general performance.

## Rehearsal checklist

Every presenter should trace one task through reset, action-time cost, transition, raw reward, normalization and named final checks. Explain one accepted alternative and one rejected exploit. Derive the science conservation and static-duty inequalities without reading code. Identify which numbers are current-source measurements and which are historical or synthetic. Hide credentials before sharing the screen.
