# AeroSentinel — Autonomous Satellite Fault Recovery RL Environment

An unbreakable reinforcement learning environment where an autonomous AI flight software agent responds to live anomalies on a 3U CubeSat in Low Earth Orbit (LEO).

Unlike static coding puzzles, AeroSentinel features a **deterministic 95-minute orbital multi-physics simulation** modeling real coupled dynamics: solar irradiance and eclipse cycles, battery state-of-charge and bus voltage curves, 3-axis reaction wheel momentum and attitude precession, thermostat radiative thermal balance, and Friis RF link budget margins.

The domain is drawn from embedded avionics development at the **IIT (BHU) Rocketry Division** — modeling real flight software fault states and telemetry protocols.

---

## Security & Isolation (Bar 3: Robustness)

- **Isolated Physics Verifier**: Evaluation runs strictly in the protected harness container. The untrusted sandbox container contains zero verification or reward code.
- **Privilege Separation**: The server runs with dropped capabilities (`cap_drop: [ALL]`), and all agent Python execution (`run_python`) executes strictly under unprivileged account `sandbox-agent` (UID 1001), preventing access to server memory or `/proc/1/environ`.
- **Dynamic Secret Token Authorization**: Privileged endpoints (`/reset`, `/state`, `/fetch_configs`) require a dynamically generated 256-bit token created at container startup into a root-owned `mode 0600` volume. Unauthenticated calls receive `403 Forbidden`.
- **Dynamic Behavioural Verification**: Dynamic code testing replaces string matching; scripts such as `link_budget.py` are executed against synthesized test fixtures with varied parameters, preventing comment/substring bypasses.
- **Interactive Orbital Progression**: Agents can call `step_orbit` to advance orbital time and observe dynamic consequences in `telemetry/live_telemetry.csv` and `telemetry/current_status.json`.
- **Zero Answer Leakage**: The agent receives realistic symptoms, anomaly alerts, and flight manuals (`docs/flight_manual.md`), never hardcoded answer numbers.

---

## Tasks

| Task ID | Title | Difficulty | Coupled Physical Systems |
| :--- | :--- | :--- | :--- |
| `eps_eclipse_recovery` | EPS Solar Calibration & Eclipse Brownout | Easy | Solar generation, battery SoC, eclipse transition, bus voltage floor |
| `adcs_detumble_pointing` | ADCS Momentum Bias & Detumble | Easy | Reaction wheel bias, attitude precession, sun-vector pointing |
| `thermal_battery_preservation` | Battery Thermal Runaway Prevention | Medium | Thermostat hysteresis, internal heat dissipation, runaway cutoff |
| `rf_downlink_closure` | RF Downlink Closure & Dynamic Budget | Medium | Transmitter power, antenna directivity, Friis equation, telemetry pass |
| `safe_mode_cascade` | Multi-Subsystem Safe Mode Cascade | Medium (Seeded) | Coupled power-attitude-thermal failure requiring coordinated recovery |

**Demo Task (Run A):** `eps_eclipse_recovery`

---

## Quick Start (One Command)

From a fresh clone with Docker installed:

```bash
cp .env.example .env
docker compose up --build --abort-on-container-exit --exit-code-from harness
```

The default `MODEL=mock/solver` runs completely offline with no API key, executes the oracle reference solution (scoring 1.00), executes the no-op baseline (scoring 0.00), and completes the model run.

---

## Common Execution Modes

```bash
# 1. Run baseline gate checks across all 5 tasks (generates gate.json)
MODE=baselines docker compose up --abort-on-container-exit --exit-code-from harness

# 2. Run model across every task in turn
MODE=all docker compose up --abort-on-container-exit --exit-code-from harness

# 3. Test idle/give-up model (verifies zero undeserved reward)
MODEL=mock/idle docker compose up --abort-on-container-exit --exit-code-from harness

# 4. Run seeded variant of compound safe-mode cascade
TASK_ID=safe_mode_cascade SEED=42 docker compose up --abort-on-container-exit --exit-code-from harness

# 5. Connect real model via OpenAI-compatible endpoint
MODEL=gpt-4o API_KEY=sk-... BASE_URL=https://api.openai.com/v1 docker compose up ...
```

---

## Scoring Model (Outcome-Based)

```
reward = power_balance (0.35)
       + thermal_stability (0.25)
       + adcs_precision (0.20)
       + comms_closure (0.20)
```
- **Oracle Baseline**: Must score $\ge 0.95$ (achieves $1.00$).
- **No-Op Baseline**: Must score $\le 0.05$ (achieves $0.00$).
- If any catastrophic safety violation occurs (bus collapse below 3.10V, thermal runaway $>50^\circ\text{C}$, or unmitigated tumble), total reward is strictly zeroed.

---

## Directory Layout

```
docker-compose.yml       harness (isolated+internet) + sandbox (isolated only)
Dockerfile.sandbox       isolated workspace with hardened Python server
Dockerfile.harness       verifier, limits, telemetry logger, baseline runner
sandbox/server.py        authenticated tool server with token validation
harness/verifier.py      orbital multi-physics simulation engine
harness/tasks.py         5 flight tasks with symptom alerts & docs/flight_manual.md
harness/env.py           reset / step / score with physics evaluation
harness/runner.py        40-step limits, 600s time cap, 48K token context trimmer
harness/agents.py        OpenAI-compatible client + mock/solver + mock/idle
harness/run.py           entrypoint: oracle -> no-op -> model execution
inputs/append.txt        default appended instructions file
tests/test_physics.py    unit tests for orbital physics equations
tests/test_adversarial.py unit tests for sandbox security, tokens & limits
```
