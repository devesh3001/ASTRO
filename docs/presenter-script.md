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
