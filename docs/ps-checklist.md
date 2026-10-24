# Problem-statement checklist

This checklist follows the supplied RL hackathon problem statement. An implementation or a passing test is evidence for a specific requirement, not a guarantee of finalist eligibility.

| PS requirement | Status and evidence |
|---|---|
| Clear instructions and explicit success criteria | Implemented in eight task builders and protected mission metadata |
| Observations, named tools and argument schemas | Files, live telemetry, paginated reads and schemas in `harness/tools.py` |
| Persistent state and seeded reset | Protected simulation state; seed sweep checks actual repeated reset hashes |
| Termination and partial reward | Success/failure/cap/crash records, component scores and named failed checks |
| At least five tasks, two difficulty levels | Eight task families across three levels |
| Named realistic GPT-Luna demo | `eps_eclipse_recovery`, seed 0; current-code evidence must be read from final validation records |
| Reference >=0.95 and no-op <=0.05 | Automatic per-task gates; all-task gate is required by the packager |
| Failed gate prevents model call | Enforced in `harness/run.py`; CI runs all-task gates |
| Verifier and hidden tests inaccessible | Separate harness; agent/evaluation UIDs and authenticated control endpoints; live Docker tests |
| No external network from agent tools | Internal sandbox network; live outbound-network probe |
| One Compose command from fresh clone | README command and pinned image/wheel dependencies; final CI exercises it |
| Build <=180 seconds | Isolated empty-cache builder check in CI; use the current run's measured result |
| Both baselines <=120 seconds on chosen task | Shared task-level deadline; gate logs record elapsed time |
| 40 replies, 10 calls/reply, 600 seconds | Runner limits and regression tests; received unreadable replies consume one step |
| Tool limit 60 seconds, timeout error and continuation | Operation budget and feedback; provider subprocess cancellation; HTTP wait respects remaining tool budget |
| Final scoring <=120 seconds | Separate verification budget; bounded isolated script fixtures |
| 64K context support | Bounded tool output and conservative context trimming; immutable prompt overflow is explicit |
| Same interface for every model | Configured OpenAI-compatible client; no model-name-specific grading or solver logic |
| Exact judge append file | UTF-8 content preserved and logged; named missing/blank file stops the run |
| Complete structured telemetry | Inputs, raw replies, actions, results, errors, state hash, reward, token usage, latency and final reason |
| All-task gate and >=3 genuine runs in zip | Packager checks current source fingerprints and complete episodes; stale evidence is rejected |
| Success and failure samples | Real success plus honest failure; scripted adversarial samples are labeled and never count as genuine runs |
| Design 1-2 pages; deck PDF | Final PDFs are checked for page count, text bounds and visual layout |
| No credentials in Git, logs or archive | Ignored local `.env`, blank example, credential scans and checksum validation |
| Main is the judge branch | `main` is the GitHub default; actual checkout remote is `devesh3001/astro` |
| Private repository | **Unmet by explicit user choice:** repository remains public |
| Repository and archive exactly match team name | **Review:** registered spelling is `Astro`, repository is `astro`, archive is `Astro.zip`; confirm whether case-only difference is accepted |
| Organizer invited before deadline | **Unconfirmed:** organizer GitHub username has not been supplied |
| Form upload and deadline | **Unconfirmed:** submission-form email/link and precise deadline have not been supplied; no upload has been performed |
| Code timing, originality and team understanding | Team attestation required; commit times cannot establish these alone |

Run the read-only audit after packaging:

```sh
python scripts/audit_submission.py --team-name Astro --github
```

The report deliberately says `NOT_COMPLETE` while administrative items remain open. Public visibility is not silently treated as satisfying the PS's private-repository instruction. The user's later preference controls repository visibility.
