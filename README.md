# AeroSentinel — Satellite Fault Response RL Environment

An RL environment where an AI agent acts as an autonomous fault-response system
for a CubeSat in low Earth orbit. Subsystem anomalies appear in config files and
telemetry logs; the agent diagnoses and fixes them before the satellite is lost.

Built on top of the [RL-ENV-TEMPLATE](https://github.com/ayushmothiya1/RL-ENV-TEMPLATE)
harness contract. The domain is drawn from my role as **Avionics Head at the IIT (BHU)
Rocketry Division** — the fault patterns, config layouts, and telemetry format mirror
real embedded flight-software issues we encountered during our rocket avionics work.

## Quick start

```bash
cp .env.example .env          # 1. create config
# edit .env: set MODEL, API_KEY, BASE_URL (leave as-is for the offline demo)
docker compose up --build --abort-on-container-exit --exit-code-from harness
```

With the default `MODEL=mock/solver` the environment runs offline with no API key.

## Tasks

| Task ID | Title | Difficulty |
|---|---|---|
| `power_brownout` | EPS Solar Panel Degradation | easy |
| `adcs_wheel_bias` | ADCS Reaction Wheel Speed Bias | easy |
| `thermal_runaway` | Battery Thermal Runaway | medium |
| `comms_link_budget` | Comms Link Budget Failure | medium |
| `multi_fault_cascade` | Multi-Subsystem Fault Cascade (seeded) | medium |

**Demo task (Run A):** `power_brownout`

## What the agent does

The agent has five tools: `list_files`, `read_file`, `write_file`, `run_python`, `finish`.
The satellite's onboard filesystem lives in the sandbox (no internet). INI-format config
files (`.cfg`) and telemetry logs (`.csv`) are the agent's window into the world.
The verifier reads fixed configs programmatically — it never inspects the agent's text output.

## Scoring

```
reward = 0.7 × (target checks passed / total target checks)
       + 0.3 × (above) × (regression checks passed / total regression checks)
```

- **Oracle** (reference solution) must score ≥ 0.95
- **No-op** (gives up immediately) must score ≤ 0.05

## Try other tasks and modes

```bash
# run a specific task
TASK_ID=thermal_runaway docker compose up --build --abort-on-container-exit --exit-code-from harness

# seeded variant (different subsystems fail each time)
TASK_ID=multi_fault_cascade SEED=7 docker compose up --build --abort-on-container-exit --exit-code-from harness

# baseline checks only, no model call (produces gate.json for submission)
MODE=baselines docker compose up --build --abort-on-container-exit --exit-code-from harness

# model on every task in turn
MODE=all docker compose up --build --abort-on-container-exit --exit-code-from harness

# no-op model (should fail with reward ~0)
MODEL=mock/idle docker compose up --build --abort-on-container-exit --exit-code-from harness
```

## Verify credentials before running

```bash
API_KEY=... BASE_URL=... MODEL=... python check_model.py
```

## Layout

```
docker-compose.yml       two services: sandbox (no internet) and harness
Dockerfile.sandbox       agent's isolated workspace image
Dockerfile.harness       verifier, baselines, model loop image
sandbox/server.py        tool execution inside the sandboxed workspace
harness/run.py           entry point: oracle -> no-op -> model run
harness/runner.py        step loop, limits, context trimming, telemetry
harness/env.py           reset / step / score (verifier lives here)
harness/tasks.py         five satellite fault tasks + reference solutions
harness/tools.py         tool schemas shown to the model
harness/agents.py        OpenAI-compatible client, mock models, baselines
```

## Notes

- Never commit API keys. `.env` is ignored by git.
- The verifier and hidden check code live in the harness container only.
  The sandbox has no access to them.
- If your environment uses an LLM of its own, bring your own setup for it.
