# Team ASTRO submission handoff

The code and evidence can be prepared mechanically. Organizer invitations are sent and naming/deadline are confirmed. Invitation acceptance, acceptance of the public-visibility exception, uploading and team eligibility remain human checks. This handoff records those remaining actions without changing the user's explicit decision to keep the repository public. It is not a completed submission receipt.

## Keep code and the reference archive distinct

| Item | Purpose | Final check |
|---|---|---|
| GitHub `devesh3001/ASTRO`, branch `main` | Executable source the judges clone and run | Record the final main commit and its CI result; use the README Compose command from a clean clone |
| `ASTRO.zip` | Reference deliverables: gates, genuine model logs, sample trajectories, PDFs and checksums | Upload the reviewed current archive named exactly `ASTRO.zip`; do not substitute a GitHub source-code ZIP |
| `submission_bundle/validation-evidence.json` | Maps measured outcomes to source/build fingerprints and run IDs | Confirm contract v3 and matching archive fingerprints; historical runs cannot certify changed runtime code |
| Local ignored `.env` | Runtime credentials used on the team's computer | Keep private; never include it in Git, the ZIP, a form attachment, a screenshot or this checklist |

The reference ZIP does not replace the executable main branch. Conversely, a repository link does not replace the reference ZIP when the form requests that upload. A documentation-only commit does not change runtime fingerprints, but record the actual final main commit after all approved updates.

## Human actions still open

| Action | Current state | Concrete completion evidence |
|---|---|---|
| Retain requested public visibility | **User decision recorded: keep public.** This remains a PS exception to the private-repository rule | Record any organizer acceptance if obtained. Do not silently relabel public visibility as compliance or change it without the user's instruction |
| Confirm registered-name capitalization | **Confirmed:** registered team, repository and archive use **ASTRO** | Exact name applied with user authorization |
| Obtain exact organizer GitHub username | **Verified:** `ayushmothiya1` and `rahulgpw`; write-access invitations sent with user approval | Both invitations are pending acceptance; public read access is available immediately |
| Obtain submission form URL | **Confirmed:** https://forms.gle/uddpuA4yLu5SZ2wW9 | The user will fill identity fields and upload personally |
| Confirm final deadline in IST | **Confirmed:** 2026-10-25 10:15 IST (UTC+05:30), from organizer submission email | Only commits and the latest form submission before this cutoff count |
| Complete human eligibility/originality attestation | **Not attested here** | Team members confirm the competition rules and can explain the implementation; commit times alone do not prove eligibility |
| Upload the reference archive and repository link | **User will submit personally:** https://forms.gle/uddpuA4yLu5SZ2wW9 | Form receipt/confirmation identifier showing the intended repository and current `ASTRO.zip` |
| Complete a timed presentation rehearsal | Pack and answer key are available; **not claimed complete** | Human-filled practice log in `docs/rehearsal-pack.md`, including corrections and duration |

No new API key is needed for these administrative steps. Do not paste, echo or request the existing key to complete this handoff. Use the existing ignored local configuration only when an authorized live run is needed. This checklist does not authorize additional paid model episodes or send invitations/messages automatically.

## Final local verification

After documents and the archive are finalized, use the existing audit instead of rerunning every simulation or making more model calls:

```sh
python scripts/audit_submission.py --team-name ASTRO --github
```

If the exact organizer username has been obtained, pass it with `--organizer EXACT_USERNAME`. The audit does not grant access or send an invitation. Confirm access separately through GitHub. A `NOT_COMPLETE` result is expected while administrative items remain unresolved; do not remove those checks merely to obtain a green status.

Before uploading, a human should check:

- The selected archive is the latest reviewed **`ASTRO.zip`**, rather than a historical backup with the same visible filename.
- The archive checksum manifest and validation record agree with current source/build fingerprints; all included files verify.
- The design PDF has one or two pages and the deck is the current 16-page version (13 talk slides and three reference slides).
- The evidence includes all-task gates, at least three genuine complete model runs, and honestly labeled success/failure samples. Zero-reply transport attempts are diagnostic only.
- The actual four current organizer-model runs show EPS, RF and science success plus observation failure. Scripts and local fake-endpoint tests are not additional real model runs.
- The Run A configuration names EPS recovery, seed 0, empty `APPEND_FILE`, and no compatibility `inputs/append.txt` file. The tracked `append.example.txt` is a sample, not the undisclosed judge file.
- The final `main` revision is pushed, the expected CI run has completed, and the repository URL is correct.
- No credential is visible in an attachment, terminal capture, screen share or form field.

This local audit provides evidence checks; it does not submit the form, attest the active Docker daemon, establish team authorship, or resolve organizer policy decisions.

## Receipt record to fill after actual completion

| Field | Human entry |
|---|---|
| Final repository URL and main commit | |
| Final CI URL and conclusion | |
| Archive filename and SHA-256 | |
| Organizer GitHub username and confirmed access | |
| Name-case acceptance source | |
| Public-visibility exception acceptance, if obtained | |
| Official submission form URL | |
| Exact deadline, IST, and official source | |
| Upload date/time, IST | |
| Form confirmation/receipt | |
| Team member who verified attachments | |

Keep receipt evidence private if it contains personal information. Leave fields blank until verified; a prepared artifact is not proof that the corresponding human action occurred.

## Confirmed organizer instructions

Team name: **ASTRO**. Team code: **team_036**. Repository: https://github.com/devesh3001/ASTRO. Upload **ASTRO.zip** through the official form before **25 October 2026, 10:15 AM IST**. The latest submission before the cutoff counts. No form has been submitted by this agent.
