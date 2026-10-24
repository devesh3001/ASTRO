# Team Astro: ten-minute presentation and defense

Use `submission_bundle/deck.pdf` for the required PDF presentation. Use the editable deck's speaker notes for pacing and this guide for rehearsal. Measured results belong to the code fingerprint shown in the validation record. Never substitute older successful logs after a runtime change.

## Talk sequence

| Slide | Time | What to explain |
|---|---:|---|
| 1. AeroSentinel | 0:00-0:35 | We evaluate an agent's recovery decisions against protected mission history. We build the environment; judges supply the model. |
| 2. Environment contract | 0:35-1:25 | State, observations, actions, time costs and independent outcome checks. |
| 3. Task progression | 1:25-2:20 | Five guided repairs, one radio-planning task, and two tasks where timing matters. |
| 4. Observation scheduling | 2:20-3:25 | Show the constant-duty contradiction and a feasible scheduled exposure. Explain why copying nominal values is insufficient. |
| 5. Reward and protected history | 3:25-4:25 | Show component weights, no-op normalization and mandatory checks. A correct final file cannot undo a missed window. |
| 6. Isolation | 4:25-5:25 | Distinguish harness, agent and isolated evaluator; explain token protection, worker cleanup and remaining kernel trust. |
| 7. Executed failure probes | 5:25-6:30 | Show a fabricated telemetry attempt and a missed-window attempt. Both fail; partial credit is not full success. |
| 8. Reproducibility evidence | 6:30-7:35 | Explain the seed sweep, cold build, current fingerprints and reference versus real-model evidence. |
| 9. Model results | 7:35-8:45 | Report actual outcomes, seeds, reply counts and durations, including any failed missions. Do not hide failures. |
| 10. Demo and limitations | 8:45-10:00 | Show the demo logs and final verifier checks. State simplifications and the limits of the evidence. |

The live PS slot has presentations and runs in parallel. Do not wait for a run to finish before beginning the talk. Keep the demo configuration ready and preserve time for six minutes of questions.

## Demo procedure

Set `MODEL`, `API_KEY` and `BASE_URL` locally from the organizer email. Use `TASK_ID=eps_eclipse_recovery`, `SEED=0`, `MODE=full` and an empty `APPEND_FILE` for a Run A-style validation. Keep `inputs/append.txt` absent because that compatibility filename auto-loads when present. The tracked example is `inputs/append.example.txt`. Never show `.env` or a full Compose configuration containing secrets on screen.

```sh
docker compose up --build --abort-on-container-exit --exit-code-from harness
```

Show the oracle/no-op gate, readable per-reply telemetry, final reward and named verifier checks. For failure evidence, open a previously executed defense-probe log. Explain that replaying a script is an adversarial demonstration, not a model run.

## Likely questions

**Where is the reinforcement learning?** This is an environment with state, observations, tools, transitions and reward. We do not train a policy. The PS explicitly says judges bring the agent and no training is required.

**Are the tasks just changing constants from a manual?** The five introductory recovery families are guided repairs. The planning task balances throughput, link margin and energy. The early-contact task loses data if repaired too late. The observation task requires switching control over time; the constant-duty inequality proves a static setting cannot meet its objectives. We report shortcut results rather than claiming all tasks are equally deep.

**Is the satellite physics realistic?** It is a declared benchmark approximation. We implement energy integration, scalar pointing, lumped temperature, a bandwidth-aware link budget and bounded data/exposure. We do not claim flight validation, three-axis rigid-body dynamics or a radiation model. The equations and inert legacy fields are documented in `model-assumptions.md`.

**How can a no-op score zero if some subsystems are healthy?** We measure improvement beyond the initial projected outcome. Healthy starting components do not generate free progress. Catastrophic failure gives zero, and every required condition must hold for success. The raw components and initial baseline remain in the logs so the normalization is inspectable.

**Could a high score compensate for a failed mission condition?** No. Every required check is separate from the numeric sum. A failed check caps normalized reward below 0.95, and termination records failure. The near-target regression exercises this boundary.

**Can the agent edit telemetry or mission.json to pass?** It can edit workspace copies, but grading reads protected simulation state and mission facts in the harness. The forged-telemetry probe is executed and logged. Protected hardware changes are rejected.

**Does the reference policy get answers?** Yes. It is a privileged feasibility baseline. It uses the same public tools and action-time costs, but is not evidence of an agent discovering the solution. Actual model runs are separate and labeled by deployment and seed.

**Must the agent follow your exact repair or schedule?** No. The verifier checks outcomes. Tests accept another controller and another feasible duty schedule. It does not compare the final file text with a reference answer.

**What prevents changing settings after an accident?** Catastrophic events latch, and exposure/data are accumulated through actual history. Final repair cannot recover an earlier missed window or erase a safety event.

**How is the verifier protected?** The agent runs only in the sandbox as UID 1001. The verifier is in the harness, which is not mounted into the agent workspace. Behavioral fixtures use UID 1002. Control endpoints require a token hidden from workers. Root filesystem, network, storage, processes, CPU and outputs are bounded. This relies on Docker and the host kernel; it is not a VM escape-proof claim.

**What happens to malformed replies and timeouts?** Received malformed replies consume a step and receive feedback. Extra calls are rejected individually. Tool failures return errors so the episode can continue. Provider transport failure and final termination are recorded honestly. There is no provider-specific grading branch.

**Will it work with the undisclosed model?** The interface and error-handling tests support portability; they do not prove every unknown model will succeed. Report exactly which deployment was tested on the final source. An honestly scored model failure is acceptable under the PS.

**Why are there old model logs?** Historical runs remain historical. Packaging requires matching source fingerprints and rejects stale evidence. Only current-code runs belong in the final evidence archive.

**What is the most serious remaining weakness?** Finite adversarial coverage and simplified dynamics. The tasks still include guided introductory repairs. We have measured defenses against specified attacks, not a proof that every exploit is impossible.

**Is the submission fully compliant?** Technical evidence is checked separately from administrative requirements. Public visibility was an explicit user decision and does not satisfy the PS's private-repository instruction. Exact team-name spelling, organizer access and form upload must be settled before claiming complete compliance.

## Statements to avoid

Do not say "unbreakable," "production-ready satellite software," "all models pass," "zero answer leakage," or "100% PS compliant." Do not count the oracle, offline solver or scripted attack as a genuine model run. Do not present a model's success on one seed as general performance across all seeds.

## Rehearsal checklist

Every presenter should be able to trace one task from `build_task` through an action, state transition, raw reward, normalization and final check. Be able to explain one accepted alternative and one rejected exploit without reading source on stage. Know which figures came from scripted policies and which came from the organizer's model. Check the local key is hidden before sharing the screen.
