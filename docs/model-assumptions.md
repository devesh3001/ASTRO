# Model assumptions and controls

AeroSentinel is a deterministic environment for evaluating agents that inspect, repair and schedule operations. It is not validated satellite flight software. The first five task families are guided recovery exercises; four hard families add radio, deadline, observation and coupled science planning. Scientific claims are limited to the implemented equations and checks.

## State transition

An episode covers 5,700 simulated seconds. Sunlight ends at 3,600 seconds. Contact and acquisition windows come from the seeded, protected mission. Completed dispatched ordinary tool attempts consume 30 simulated seconds, including tool errors; schema-rejected calls do not execute or advance time; `step_orbit` advances 1-30 minutes. Integration follows a fixed global 30-second grid, split at protected event boundaries. Allowed ways of grouping the same elapsed time use the same integration steps. Energy, exposure and delivery accumulate from the actual trajectory rather than being reconstructed from the final configuration.

| Quantity | Implemented model | Limitation |
|---|---|---|
| Solar generation | `calibration * 0.28 * 0.06 * 1361 * cos(pointing)` W in sunlight; zero in eclipse | Fixed orbit and array; no orbital propagator or degradation dynamics |
| Battery | Integrate generation minus load in Wh; clamp to 0-40 Wh | No cell electrochemistry, charger current regulator, efficiency or aging model |
| Bus voltage | `3.0 + 1.2 * SoC_fraction` V | Linear benchmark relation, not a measured discharge curve |
| Attitude | Scalar drift for large bias; otherwise `error *= (1-min(0.35, gain*0.06))**(dt/60)`, floored at deadband | Exponential retention calibrated to one-minute damping; no rigid-body or three-axis wheel dynamics |
| Temperature | Integrate internal heat, sunlight heat and linear heat loss with a lumped heat capacity | No spatial thermal model or fourth-power radiation law |
| Radio margin | TX + antenna + ground gain - path loss - system loss - sensitivity - bandwidth penalty - pointing loss | Fixed mission propagation profile; simplified packet-loss threshold |
| Channel capacity | Baud/8, multiplied by 0.85 packet efficiency and contact duration | Half capacity at 0-3 dB; full at >=3 dB; no detailed coding/channel simulation |
| Observation | Duty cycle times seconds inside an acquisition window | Averaged instrument duty; exposure is a benchmark work metric |
| Science storage | Window exposure times generation rate enters a finite buffer; overflow is permanently dropped | Continuous-volume data, no individual files, packet queues, compression or prioritization |
| Science delivery | Minimum of buffered data and usable contact capacity | Cannot transmit before acquisition; protected accounting, not agent-reported transfer totals |

Radio bandwidth penalty is `10*log10(baud/9600)` dB. The transmitter bus load is `8 + 2*10**(tx_dbm/10)/1000` W during contact. A science/observation payload draws 6 W times duty continuously, including outside useful acquisition windows. HTTP body streaming is bounded buffering, not model token streaming.

`max_charge_rate_a`, `bus_voltage_nominal`, actuator torque/frequency and thermal polling fields are descriptive legacy fields. They do not affect state or score. The manual identifies them explicitly. Missing or malformed mandatory configuration fields are recorded as errors and cannot silently become nominal defaults. The documented optional payload duty and baud rate retain their declared absent-field defaults; malformed supplied values are rejected.

## Coupled science mission

`science_store_forward` has two disjoint acquisition windows and two later ground contacts. Seeds vary timing, duration, generation rate, propagation conditions and energy allocations. Each acquisition lasts 240-360 seconds and requires at least 90% useful exposure. Generated data enters storage with capacity `1.05 * science_rate * max(window_duration)`. The first contact clears space before the second acquisition; missing it causes irreversible overflow because both required acquisitions together exceed one buffer.

Every transition preserves, within numerical tolerance:

```text
generated_science = buffered_science + delivered_science + dropped_science
0 <= buffered_science <= buffer_capacity
delivered_in_contact <= available_buffer and usable_radio_capacity
```

Success requires each window quota, the science delivery target, no lost science, conservation, payload and RF energy allocations, and the ordinary hardware safety checks. Exposure in the second window cannot replace a missed first-window quota. Final configuration edits cannot restore dropped science or create past observations. Tests also accept feasible alternatives to the reference schedule.

## Why static duty cannot solve scheduling

For a 600-second single observation window, the required useful exposure is 540 seconds. Constant duty must be at least `540/600 = 0.90`. The 1.2 Wh allocation over the full 5,700-second episode permits at most `1.2/(6*5700/3600) = 0.1263`. These cannot both hold. Enabling near the window and disabling afterward can satisfy them.

The coupled science mission has the same contradiction for every seed. Each window requires duty at least 0.90 if held constant. Its payload allocation is `1.2 * 6 * sum(window_durations) / 3600` Wh, so constant duty must be at most `1.2 * sum(window_durations) / 5700`. Total window duration is 480-720 seconds, giving an upper bound of roughly 0.101-0.152. This is strictly below 0.90. Timing is therefore necessary even with a correct radio configuration. The finite buffer and contact ordering add a second planning constraint.

## Protected outcomes and behavioral checks

The harness owns hardware facts, mission windows, energy allocations, storage capacity and success thresholds. The agent can read a copy in `docs/mission.json`, but editing that copy cannot redefine success. Telemetry is an observation generated from protected state, not a grading input. Unsafe hardware changes and catastrophic events remain latched.

The ordinary recovery weights are 0.35 power, 0.25 temperature, 0.20 attitude and 0.20 communications. The single-window observation mission scales these by 0.8 and adds 0.20 exposure completion. The science mission scales them by 0.6, adds 0.20 average per-window completion and 0.20 science delivery. Every required check must pass independently; normalized progress below full success cannot compensate for a failed condition.

When a task includes the link-budget script, an isolated evaluator checks its behavior on the exact live communications configuration and seven perturbed full-schema cases, including the documented optional-baud default. Cache keys include script content, communications configuration and seed. Editing the configuration triggers another evaluation. This addresses specific fixture-recognition and stale-cache attacks; finite fixtures do not prove every malicious program has been excluded.

## Scope of evidence and provenance

Reference runs prove selected generated instances are solvable through public tools. Scripted shortcuts test particular failure modes. Local fake-endpoint tests exercise the real provider subprocess and HTTP contract with synthetic bodies, including malformed replies and bounded oversized bodies. These are not genuine model runs and do not establish cross-provider model success.

Actual model results must be identified by deployment, seed, complete raw episode records and matching current provenance. Contract v3 binds harness/sandbox source and the expected Dockerfiles, Compose, `.dockerignore` and dependency build inputs. The harness retains copies of those non-secret inputs so host and built-image hashes agree across LF/CRLF checkouts. This is an expected-build record, not attestation of the active daemon, effective container mounts or networking. Runtime isolation needs separate live checks.

Earlier source versions remain historical. An eight-task run cannot certify the current nine-task environment or changed provider code. No finite suite proves that every reward exploit, container escape or provider incompatibility has been eliminated.
