# Team Astro rehearsal pack

This is a practice exercise and answer key, not evidence that the team has rehearsed or mastered the project. Use the ten-slide `submission_bundle/deck.pdf` and its editable speaker notes. The measured examples below come from the four genuine organizer-model runs in `submission_bundle/validation-evidence.json`, on the runtime validated at commit `eb49105`. Check matching source/build hashes if the runtime changes.

## Ten-minute run-through

Assign one speaker, one timer and one questioner. Begin the talk while the Run A demonstration starts, as allowed by the PS. Use recorded evidence when practicing; this exercise does not require another paid model run. Hide `.env` and any window containing credentials.

| Slide and actual title | Stopwatch | Required explanation and practice check |
|---|---:|---|
| 1. AeroSentinel | 0:00-0:35 | State the project purpose in one sentence: an evaluation environment where agents repair and schedule a simplified satellite mission. Judges supply the agent; no policy is trained here. |
| 2. Each action changes a persistent mission | 0:35-1:25 | Name one observation, one action and one protected outcome. Explain 30-second ordinary tool costs, 40 replies, 10 tools per reply and the real-time budget. |
| 3. Nine tasks, four planning challenges | 1:25-2:15 | Identify five guided repairs and four hard missions. Give one different constraint for RF tradeoff, early contact, observation and science storage. |
| 4. Two science windows, one finite buffer | 2:15-3:30 | Trace science seed 101 using the worksheet below. Explain why the first contact matters and why constant duty cannot satisfy the energy/exposure constraints. |
| 5. Reward follows verified mission history | 3:30-4:30 | Write the normalization equation. Explain why observation reward 0.8 is still failure, and why one window cannot compensate for another. |
| 6. Separate processes protect scoring and mission facts | 4:30-5:30 | Distinguish harness, UID 1001 agent and UID 1002 evaluator. Name one live-tested boundary and the remaining Docker/kernel trust. |
| 7. Six executed probes fail the success checks | 5:30-6:30 | Choose a forged-inventory or missed-contact probe. Explain the attempted shortcut, the protected check and the measured failure. Label it scripted. |
| 8. Reproducibility and shortcut comparison | 6:30-7:35 | Distinguish 150 Linux regressions, 180 task/seed pairs and 12 live isolation checks. Explain the separate 474-case mission stress report (80 feasible plans, 394 correctly rejected decisions) and 13 synthetic interface tests over 32 local HTTP requests; the 13 tests are included in the 150 total, not additional model runs. Explain v3 expected-build provenance without claiming active-daemon attestation. |
| 9. Four organizer-model validation attempts | 7:35-8:45 | Report all four outcomes, including the observation failure. Explain that local fake-endpoint tests are synthetic conformance checks, not additional real-provider successes. |
| 10. Demo and presentation defense | 8:45-10:00 | Show Run A's final checks, summarize the scientific limitations and state unresolved submission requirements. Finish within ten minutes. |

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
