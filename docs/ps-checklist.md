# Problem-statement checklist

This checklist follows the supplied RL hackathon problem statement. Implemented behavior and passing tests support specific requirements; they do not establish eligibility, a perfect rating or complete compliance. Read exact measured results from the current final validation record. Earlier eight-task results cannot certify the nine-task source.

| PS requirement | Implementation and evidence to check |
|---|---|
| Clear instructions and explicit success criteria | Nine task builders and protected mission metadata; science windows, storage and delivery targets are explicit |
| Observations, named tools and argument schemas | Files, live telemetry, paginated reads and schemas in `harness/tools.py`; science inventory and per-window exposure are observable |
| Persistent state and seeded reset | Protected simulation state; all-task seed sweep checks repeated reset hashes |
| Consistent state transition | Fixed global 30-second grid split at protected events; exponential scalar damping; grouping-consistency regressions |
| Termination and partial reward | Success/failure/cap/crash records, raw components, baseline normalization and named failed checks |
| At least five tasks, two difficulty levels | Nine task families across three levels: five guided repairs and four hard missions |
| Meaningful action consequences | Contact deadlines, exposure quotas, finite-buffer loss and two energy allocations; static-duty impossibility and data-conservation tests |
| Named realistic GPT-Luna demo | `eps_eclipse_recovery`, seed 0; inspect final current-source model evidence rather than an older success |
| Reference >=0.95 and no-op <=0.05 | Automatic per-task gates; current all-task gate required by packager |
| Failed gate prevents model call | Enforced in `harness/run.py`; final CI must exercise all-task gates |
| Verifier and hidden tests inaccessible | Separate harness; agent/evaluation UIDs, authenticated controls and live Docker tests |
| Behavioral script verification | Exact live communications schema plus perturbed full-schema cases; cache includes script, communications content and seed |
| Invalid configuration cannot silently pass | Missing or malformed mandatory values are errors; declared optional absent-field defaults are explicit; final checks run after orbital time ends |
| No external network from agent tools | Internal sandbox network; independently executed outbound-network probe |
| One Compose command from fresh clone | README command and pinned image/wheel dependencies; inspect current final CI result |
| Build <=180 seconds | Isolated empty-cache builder check; report the current run's measured result |
| Both baselines <=120 seconds on chosen task | Shared task-level deadline; gate logs record elapsed time |
| 40 replies, 10 calls/reply, 600 seconds | Runner limits and regressions; malformed received replies consume one step |
| Tool limit 60 seconds, timeout error and continuation | Operation budget and feedback; cancellable provider process; bounded streaming HTTP body reads |
| Final scoring <=120 seconds | Separate verification budget and bounded isolated behavioral fixtures |
| 64K context support | Bounded tool output and conservative context trimming; immutable-prompt overflow is explicit |
| Same interface for every model | Configured compatible API client and opaque deployment names; no model-specific grading. Local synthetic HTTP conformance tests are not cross-provider model-success evidence |
| Exact judge append file | UTF-8 text preserved and hashed; named missing/blank file stops the run. Keep compatibility `inputs/append.txt` absent for Run A |
| Complete structured telemetry | Raw replies, actions, results, errors, state hash, reward, token usage, latency and final reason |
| All-task gate and >=3 genuine complete runs in zip | Packager checks matching source and runtime-contract v3, raw episodes and summaries. Stale logs are rejected; zero-reply transport attempts are diagnostic only |
| Success and failure samples | Actual current-source success plus honest failure; executed scripted failures are labeled and never count as genuine model runs |
| Source and build provenance | v3 covers harness/sandbox source and expected Dockerfiles, Compose, `.dockerignore` and dependencies; LF/CRLF-normalized host/baked hashes. This is not attestation of active daemon configuration |
| Design 1-2 pages; deck PDF | Final documents must match nine-task code and measured evidence; check page count, text bounds and visual layout |
| No credentials in Git, logs or archive | Ignored local `.env`, blank example, credential scans and archive checksums |
| Main is the judge branch | `main` is the intended/default judge branch; verify actual checkout and `devesh3001/ASTRO` remote before final upload |
| Private repository | **Unmet by explicit user choice:** repository remains public |
| Repository and archive exactly match team name | **Pass:** registered `ASTRO`, repository `ASTRO`, archive `ASTRO.zip` |
| Organizer invited before deadline | **Invitations sent, acceptance pending:** `ayushmothiya1` and `rahulgpw`, with explicitly approved write access |
| Form upload and deadline | **Deadline confirmed:** 25 October 2026, 10:15 AM IST. [Official form](https://forms.gle/uddpuA4yLu5SZ2wW9). The team will submit itself; no upload performed here |
| Code timing, originality and team understanding | Team attestation required; commit times alone cannot establish eligibility or authorship |

Run the complete local regression command, build and current-source evidence workflow before packaging:

```sh
python tests/test_physics.py
python -m unittest tests.test_adversarial tests.test_hardening tests.test_packaging tests.test_mission_planning tests.test_temporal_missions tests.test_failure_feedback tests.test_provider_contract tests.test_consistency tests.test_science_mission tests.test_seeds_extended tests.test_judge_contract tests.test_mission_stress tests.test_supplemental_evidence
```

After genuine evidence and reviewed documents are ready, run the read-only audit:

```sh
python scripts/audit_submission.py --team-name ASTRO --github
```

Use the final suite's actual counts rather than a hardcoded earlier total. Synthetic endpoint checks validate local behavior; privileged reference plans validate feasibility. Neither supplies one of the three genuine model runs. The audit deliberately reports `NOT_COMPLETE` while technical evidence or administrative requirements remain open. Public visibility is not treated as satisfying the private-repository instruction; the user's explicit preference controls visibility.


## Supplemental final checks

Run `python scripts/check_runb_contract.py` and `python scripts/stress_missions.py` to regenerate source-bound offline reports. They strengthen measured interface and mission coverage without evaluating the undisclosed judge model or actual unknown append. Packaging rejects inconsistent labels/counts, changed validation code or HTTP fixtures, and reports that differ from the reviewed summary.

Practice with `docs/rehearsal-pack.md`; complete its human entries after an actual rehearsal. Follow `docs/submission-handoff.md` to record organizer access, accepted spelling/visibility and submission confirmation. Prepared materials do not establish that those human actions have occurred.
