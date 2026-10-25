# Team ASTRO rehearsal pack

This is a practice exercise and answer key, not evidence that the team has rehearsed or mastered the project. Use the 16-slide `submission_bundle/deck.pdf` and its editable speaker notes. The measured examples below come from the four genuine organizer-model runs in `submission_bundle/validation-evidence.json`, on the runtime validated at commit `eb49105`. Check matching source/build hashes if the runtime changes.

## Ten-minute run-through

Assign one speaker, one timer and one questioner. Begin the talk while the Run A demonstration starts, as allowed by the PS. Use recorded evidence when practicing; this exercise does not require another paid model run. Hide `.env` and any window containing credentials.

| Slide | Time | Opening explanation |
|---|---:|---|
| 1. AeroSentinel | 0:00-0:25 | We are Team ASTRO. |
| 2. The nine tasks | 0:25-1:05 | These are all nine tasks. |
| 3. Guided tasks: constraints and difficulty | 1:05-1:40 | For the first five tasks, the challenge is diagnosing and repairing a configuration while meeting every protected condition. |
| 4. Hard tasks: constraints and planning challenge | 1:40-2:20 | The harder tasks introduce consequences that a final configuration cannot undo. |
| 5. Architecture and complete run workflow | 2:20-3:10 | One Compose command starts a harness and an isolated sandbox. |
| 6. What the agent sees and can do | 3:10-3:50 | The observation includes a flight manual, instance-specific mission targets, subsystem configuration and current telemetry. |
| 7. Worked example: EPS recovery, seed 0 | 3:50-4:30 | Here is the actual unappended demo run. |
| 8. Worked example: science store and forward | 4:30-5:20 | This is the actual successful science seed one hundred and one run. |
| 9. Programmatic reward and final verification | 5:20-6:10 | The verifier uses programmatic checks only. |
| 10. Isolation, errors and attempted shortcuts | 6:10-6:50 | The verifier runs outside the agent workspace. |
| 11. Reproduce a run from a fresh clone | 6:50-7:45 | A judge can clone main, copy the environment example, select the task and seed, and run this one Compose command. |
| 12. Measured results and preserved evidence | 7:45-8:40 | The current runtime has one hundred and fifty Linux regression tests, twelve real-container isolation checks and all nine reference/no-op gates passing. |
| 13. Live Run A, Run B and the judge view | 8:40-10:00 | Run A uses our named EPS demo with seed zero and no append. |

Practice log, to be completed by a human after an actual rehearsal:

| Field | Human entry |
|---|---|
| Speaker / timer / questioner | |
| Rehearsal date and start time, IST | |
| Talk duration | |
| Question session duration | |
| One incorrect explanation caught | |
| One correction to practice next | |

## Exercise 1: trace the science mission

Use run `20261024T210515-082614Z-a0efc859`, task `science_store_forward`, seed 101. Read the mission fields and selected raw tool results before opening the answer key. Do not treat an agent's printed engineering hypothesis as authoritative; compare it with executed settings and protected telemetry.

| Protected fact | Seed 101 value |
|---|---:|
| Acquisition window 1 | 1,140-1,440 s; requires 270 s exposure |
| Contact 1 | 1,740-1,980 s |
| Acquisition window 2 | 2,940-3,180 s; requires 216 s exposure |
| Contact 2 | 4,440-4,680 s |
| Science generation | 1.9 kB per useful exposure second |
| Buffer capacity | 598.5 kB |
| Required delivered science | 923.4 kB |
| Payload / RF energy allocations | 1.08 Wh / 1.625752 Wh |

Without executing any new episode:

1. Trace the executed radio setting and payload duty switches. Which first-window seconds were missed, and did that violate the quota?
2. At the returned 54-minute telemetry snapshot, show how generated, buffered and delivered science balance before contact 2.
3. Reconcile final 969 kB delivery with the two measured exposures. Check both quotas, peak buffer, loss and energy allocations.
4. If the same acquisitions occurred but contact 1 delivered nothing, calculate the minimum overflow before contact 2. Explain why final-file repair cannot recover it.
5. Derive the seed-specific constant-duty contradiction.

### Short answer key

- The actual radio write selects **32.5 dBm and 38,400 bps**. The earlier model hypothesis mentions other numbers; the executed configuration and verifier measurements govern the result.
- The payload is enabled at mission time 19.5 minutes, 30 seconds after window 1 begins. It earns **270 seconds**, exactly the first quota. The payload is switched off at 25 minutes. For window 2 it is enabled at 49 minutes and switched off at 53.5 minutes, earning **240 useful seconds** against the 216-second quota. Extra on-time outside a window consumes energy but earns no useful exposure.
- The returned 54-minute snapshot reports **969 generated = 456 buffered + 513 delivered + 0 dropped** kB. Contact 1 cleared the first acquisition before the next one arrived.
- Final generation and delivery are `1.9 * (270 + 240) = 969` kB; buffer and loss are both zero. Peak buffer **513 kB < 598.5 kB**. Measured payload energy **1.0 Wh < 1.08 Wh**, RF energy **1.54087 Wh < 1.625752 Wh**, and delivered science **969 kB > 923.4 kB**. The verifier reports success, reward 1.0, in 28 replies and 193.683 harness seconds.
- With those same 969 kB acquisitions and no first-contact drain, overflow would be at least `969 - 598.5 = 370.5` kB before contact 2. At most 598.5 kB remains available, below the delivery target. This is a counterfactual calculation, not another measured run.
- A constant duty must be at least **0.90** to meet both window quotas. Its whole-episode energy is `6 * 5700 / 3600 * duty = 9.5 * duty` Wh, so the 1.08 Wh allocation allows duty at most **0.1137**. These conditions cannot both hold.

## Exercise 2: explain an honest failure

Use observation run `20261024T210831-941520Z-b1981481`, seed 101. Its window is 1,200-2,100 seconds; required useful exposure is 810 seconds, and the payload energy limit is 1.8 Wh. The run records 720 useful seconds, 2.1 Wh and reward 0.8.

Explain, in 30 seconds, why reward 0.8 cannot be presented as success. Name the independent failed checks and one better scheduling principle without claiming that you executed an improved model run.

**Answer key:** useful exposure falls short by **90 seconds** and energy exceeds its allocation by **0.3 Wh**. The failed checks are `observation_target` and `payload_energy_allocation`. At 6 W, 2.1 Wh corresponds to 1,260 equivalent on-seconds, of which only 720 were useful; on-time outside the window wastes energy. A plan should enable near the protected start, count action-time costs, obtain at least the quota and disable promptly. A scheduled alternative is a proposed plan until executed and verified. Correct rejection demonstrates verifier behavior; it does not turn this attempt into agent success.

## Exercise 3: distinguish the four real runs

| Actual task | Outcome / reward | Replies | Harness seconds |
|---|---|---:|---:|
| EPS recovery, seed 0, no append | Success / 1.0 | 11 | 49.440 |
| RF energy tradeoff, seed 101, sample append | Success / 1.0 | 11 | 58.971 |
| Science store-and-forward, seed 101, sample append | Success / 1.0 | 28 | 193.683 |
| Observation scheduling, seed 101, sample append | Failure / 0.8 | 12 | 94.388 |

Say: **three of these four selected attempts succeeded on one tested deployment.** Do not turn this small selected set into a general success-rate guarantee, claim the sample append is the unknown judge append, or count scripts/fake endpoints as more models. Harness elapsed times are not independent external wall-clock certification; the validation record documents the host/VM clock limitation.

## Six-minute question drill

Give each question one minute: answer in 45 seconds, then allow a 15-second follow-up. The questioner should interrupt vague claims and ask for a concrete check or record.

| Question | Minimum defensible answer |
|---|---|
| Why is science more than editing constants? | Two window quotas, finite storage, contact ordering and two energy limits; derive one inequality and trace one inventory snapshot. |
| Can a large reward hide a missed mission condition? | No; mandatory checks gate success. Use the actual 720/810-second and 2.1/1.8-Wh observation failure. |
| Can an agent write fake telemetry or recognize script fixtures? | Protected state supplies grading. Executed probes test specified attacks; live-schema and config-sensitive script checks address measured shortcuts. Finite tests are not a proof against all attacks. |
| Why do extra reads not change numerical damping? | Elapsed time still costs resources, but the fixed global 30-second event-split grid and exponential retention make allowed groupings of the same elapsed time consistent. |
| What proves unknown-model or container robustness? | Synthetic local HTTP tests check the protocol; live container tests check selected boundaries; real model evidence covers one deployment. Docker/kernel trust and unknown-model performance remain limits. |
| Is the project submission complete? | Technical evidence and human administration are separate. The repository remains public by explicit choice; name-case acceptance, organizer access, exact deadline, form/upload and eligibility remain to confirm. |

## Human mastery self-assessment

Score each item **0** for incorrect or unable to explain, **1** for correct with prompts, **2** for correct unaided with a concrete example. This is a practice rubric, not an official judging score.

| Skill | Score 0-2 |
|---|---:|
| Explain state, action costs and termination | |
| Trace both science windows and contacts accurately | |
| Derive conservation and static-duty inequalities | |
| Explain normalization and the real observation failure | |
| Distinguish harness, agent, evaluator and trust limits | |
| Separate real, scripted, synthetic and historical evidence | |
| Locate a supporting run/check without exposing credentials | |
| Finish the talk and answer questions within the practice slots | |

A useful self-assessed practice target is **14/16 with no zero in the first six items**. Any credential exposure, invented result, or claim that a failed required check passed requires correction and another practice attempt regardless of the total. Leave the practice log blank until humans actually complete the exercise.


## Spoken script for the current deck

# ASTRO presenter script

Present slides 1-13 in ten minutes. Slides 14-16 are reference material for Q&A and reproduction. These are suggested spoken words, not a claim that a human has rehearsed.

## Slide 1: AeroSentinel

Time: 0:00-0:25

We are Team ASTRO. AeroSentinel is a reproducible environment for evaluating tool-using agents on simplified satellite recovery and mission planning. We provide the world, tools, verifier and logs. Judges provide the model. Our submission has nine task families across three difficulty levels. We will explain one complete recovery, a harder planning mission, and the exact steps to reproduce both. We do not train a policy or claim flight certification.

## Slide 2: The nine tasks

Time: 0:25-1:05

These are all nine tasks. The first five cover power recovery, detumbling and pointing, battery temperature, radio closure, and a two-fault cascade. The four hard tasks are radio-energy tradeoff, early-pass recovery, observation-window scheduling, and science store-and-forward. The table states exactly what the agent must accomplish in each. Every task uses the same six tools and a seeded reset. The verifier checks the mission outcome, not whether the final file matches one preferred answer. Next we explain the four harder workflows.

## Slide 3: Guided tasks: constraints and difficulty

Time: 1:05-1:40

For the first five tasks, the challenge is diagnosing and repairing a configuration while meeting every protected condition. Power has a fixed battery capacity and a seeded reserve target. Pointing has a protected target, so increasing the agent deadband cannot relax grading. Thermal checks the trajectory as well as the final temperature. Radio scripts must calculate correctly for the live configuration and varied schemas. The cascade requires two faults to be repaired without creating a safety failure. These are guided repairs, and we do not present them as equally deep as the planning tasks.

## Slide 4: Hard tasks: constraints and planning challenge

Time: 1:40-2:20

The harder tasks introduce consequences that a final configuration cannot undo. Radio energy trades rate and power against an energy budget. Early recovery requires repair before a short contact closes. Observation scheduling requires useful exposure during a window while limiting payload energy. Science store-and-forward adds two acquisition windows, two contacts and finite storage. Seeds vary faults, hardware and timing. In our separate scripted comparison, nominal and maximum-radio shortcuts each fail all twenty hard cases. The privileged reference demonstrates that valid plans exist.

## Slide 5: Architecture and complete run workflow

Time: 2:20-3:10

One Compose command starts a harness and an isolated sandbox. The harness owns the verifier, protected mission state and model client. The sandbox executes the agent tools and has no outbound route. For the selected task and seed, the reference and no-op each receive a fresh reset. The reference must score at least point nine five and the no-op at most point zero five. If either fails, no model is called. Otherwise the model gets another fresh reset, receives observations, calls tools, sees results, and repeats until termination.

## Slide 6: What the agent sees and can do

Time: 3:10-3:50

The observation includes a flight manual, instance-specific mission targets, subsystem configuration and current telemetry. There are six schema-defined tools. The model can list files, read file ranges, replace a file, execute Python, advance the orbit and finish. Completed ordinary tool attempts consume thirty simulated seconds, including execution errors. Invalid schemas do not execute or advance time. Orbit steps advance one to thirty minutes. The world retains energy, temperature, pointing and data history. Editing telemetry cannot rewrite that protected history.

## Slide 7: Worked example: EPS recovery, seed 0

Time: 3:50-4:30

Here is the actual unappended demo run. The initial power configuration had solar calibration point four one and an unsafe undervoltage threshold of two point eight volts. The model inspected the manual, mission and telemetry, then repaired those settings to point seven two and three point two volts. It checked the changing world and called finish. The final verifier reported a battery reserve of eighty-seven point seven seven eight percent and all required checks passed. The complete genuine model episode took eleven replies and scored one point zero.

## Slide 8: Worked example: science store and forward

Time: 4:30-5:20

This is the actual successful science seed one hundred and one run. The model collected two hundred and seventy useful seconds in the first window, then the first contact delivered five hundred and thirteen kilobytes. That drain freed space for the second acquisition. The second window added two hundred and forty seconds and four hundred and fifty-six kilobytes, which the second contact delivered. Total delivery was nine hundred and sixty-nine kilobytes with zero loss. Every exposure quota and both energy budgets must pass. Missing the first drain can cause irreversible buffer overflow.

## Slide 9: Programmatic reward and final verification

Time: 5:20-6:10

The verifier uses programmatic checks only. The standard raw score weights power, temperature, pointing and communications. Observation and science add their respective exposure and delivery terms. We normalize progress against the seeded no-op outcome and clamp it between zero and one. Partial credit is allowed, but every mandatory condition must pass to reach the success threshold. An observation run can therefore earn point eight and still correctly fail. Invalid configurations score zero, catastrophic events latch, and final scoring cannot restore data or exposure that the mission already lost.

## Slide 10: Isolation, errors and attempted shortcuts

Time: 6:10-6:50

The verifier runs outside the agent workspace. Tool controls require authentication, workers use a non-root identity, the root filesystem is read-only, and the agent sandbox cannot reach the internet. Execution has process, time, storage and output limits, with descendant cleanup. Malformed replies receive feedback and consume a step. Invalid tool schemas do not execute. Provider failures have bounded, logged retries inside the same deadline. Six deliberate scripted attacks stay below success, including forged telemetry and forged science inventory. Docker and the host kernel remain trusted, so this is finite evidence rather than a universal security proof.

## Slide 11: Reproduce a run from a fresh clone

Time: 6:50-7:45

A judge can clone main, copy the environment example, select the task and seed, and run this one Compose command. The default mock solver needs no API key and checks the offline pipeline. For the genuine demo, use the organizer model, endpoint and privately supplied key. Keep append empty and keep the compatibility append dot text file absent for Run A. The automatic gates run first, then the model, and a new log directory records the outcome. Use baselines mode to check every task without a model request.

## Slide 12: Measured results and preserved evidence

Time: 7:45-8:40

The current runtime has one hundred and fifty Linux regression tests, twelve real-container isolation checks and all nine reference/no-op gates passing. The separate mission stress report meets four hundred and seventy-four expectations: eighty feasible plans succeed and three hundred and ninety-four invalid choices are correctly rejected. These are not extra real model runs. We have four genuine organizer-model episodes: EPS, radio energy and science succeed, while observation correctly fails at point eight. Four attempts are a small sample, not a general success-rate estimate. Full raw replies, tool actions, errors, state hashes and final checks are preserved.

## Slide 13: Live Run A, Run B and the judge view

Time: 8:40-10:00

Run A uses our named EPS demo with seed zero and no append. Run B uses the judge-selected task, undisclosed capable model and exact append file through the same interface. Both runs start in parallel with the talk. Once a run starts we do not intervene. The terminal shows each reply, tools, reward, state hash and errors. The recorded termination says success, failure, step cap, time cap or crash. Our key claim is reproducible, correctly scored behavior, including honest failure. We can use the reference slides for task IDs, source files, log interpretation and known limitations.
