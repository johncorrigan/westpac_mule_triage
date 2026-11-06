# Agentic workflow

First draft, written at M1. How the agent and I worked, and every review finding with my
decision. The rules are in `AGENTS.md`.

## Reviews

### Plan review (before M1, by the building agent)

The agent reviewed its own draft of `PLAN.md`, against `AGENTS.md` and the brief. I accepted
fixes that added the missing checks (the evidence pack, one transaction per run, the gate list,
scenario coverage), tied each cut to the check it changes, and added "needs from me" notes. I
then switched the plan from milestone list r11 to r12, and retired CI.

### M1 part 1 review (by the building agent)

| ID | Finding | Decision | Fix |
| --- | --- | --- | --- |
| R1 | The secrets check missed credentials written as JSON keys | Fix | The pattern allows a quote after the key; a JSON-key test failed first |
| R2 | A failing black gate didn't name the file | Fix | Dropped `--quiet` |
| R3 | Nothing tested `verify.py`'s own checks | Fix | The secrets check takes the folder to scan; seven tests |
| R4 | Nothing stopped a local database being committed | Fix | `*.db`, `*.sqlite` and `*.sqlite3` ignored |
| R5 | Only direct dependencies were checked, and not where the package loads from | Fix | Every pin is compared; the package must load from this `src/`; two tests failed first |
| R6 | The README claimed features that don't exist yet | Fix | Worded as intent |
| R7 | Nothing runs the guard's own tests without CI | Noted | None: the kit is mine |
| R8 | The golden checksum skips if the answer key folder is missing | Noted | Planned for M2 |

### M1 part 2 review (by the building agent)

| ID | Finding | Decision | Fix |
| --- | --- | --- | --- |
| R1 | The matching-key fixture hashed bytes but wrote text, so it would fail on Windows | Fix | Fixture files written as bytes; not run on Windows |
| R2 | The wall-clock rule missed `time.time_ns()` and `datetime.now` passed by name | Fix | Every reference is checked; both cases failed first |
| R3 | SQL built in a variable, then passed to `execute()`, got through | Fix | Built text with an SQL keyword is flagged anywhere; failed first |
| R4 | `os.system()` and `os.popen()` weren't flagged | Fix | Added to the unsafe calls; both failed first |
| R5 | `intent.md` overstated what the hooks stop | Fix | Reworded |
| R6 | `verify.md` didn't list the rules' blind spots | Fix | Listed |
| R7 | The `__main__.py` check accepted any second line | Fix | Exact match; failed first |
| R8 | Nothing enforced "standard library only" for `scripts/` | Fix | New test; failed first |

The reasons for each decision are mine to add.

### M2 (by me)

At M2, I had a separate planning chat (Claude Opus 5.5, not the builder agent) draft the golden README and answer key from rules I agreed. I checked every case and committed both by hand.

### M2 review (by the building agent)

| ID | Finding | Decision | Fix |
| --- | --- | --- | --- |
| R1 | The checksum gate didn't require the README or data files to be listed, so deleting a line from `SHA256SUMS` removed their protection | Fix | Every file in `tests/golden/` except Python code must be listed; two cases failed first |
| R2 | Nothing checksums `tests/golden/test_golden.py`, so its comparison could be weakened unnoticed | Open: mine to decide | None |
| R3 | The as-of time is fixed in the test, not in the answer key | Open: mine to decide | None |
| R4 | The strict contracts reject every valid line if ingest parses it into a dictionary first | Fix | The rule is in `design.md`; the strict-mode tests already fail if strict mode is loosened |
| R5 | The contracts accept empty IDs, self-payments, a knowledge time before the event time, and amounts beyond SQLite's integer | Open: suggested for M3 | None |
| R6 | `RunResult` keeps counts and IDs apart, so they can disagree; a rejected line has no line number or reason | Open: suggested for M3 | None |
| R7 | An `Alert` can be incomplete without `PROFILE_MISSING`, and `Unknown` accepts any reason code | Open: suggested for M4 | None |
| R8 | The scenario has no MEDIUM alert, no late or out-of-window record, and no Conflict | Open: mine to decide | None |
| R9 | `Status.SKIP` was dead code; a linked `expected/` would move the checksum's root; `Conflict` can hold one value; Python 3.11 hasn't run the slotted generic dataclasses | Fix, for `SKIP` only | `SKIP` removed; the rest noted |

The reasons for each decision are mine to add.

### M3 self-review (by the building agent)

| ID | Finding | Decision | Fix |
| --- | --- | --- | --- |
| R1 | A line holding a number of more than 4,300 digits made `json.loads` raise `ValueError`, which crashed the run instead of rejecting the line | Fix | `ValueError` is caught; the test failed first |
| R2 | An amount of 2^63 cents or more passed ingest, then crashed the store | Fix | `amount_cents` is capped at 2^63 - 1, SQLite's largest integer; the contract and store tests failed first. Closes that part of M2's R5 |
| R3 | Reconciliation compares totals, so a lost line and a line recorded twice cancel out | Open | None |
| R4 | The dropping fake loses a record before the real store sees it, and the part-way failure comes after the whole save | Open | None |
| R5 | `ROLLBACK` after SQLite has rolled back by itself would hide the real error (not verified) | Open | None |
| R6 | On a re-run every record is a duplicate, so signals built from the run's accepted records would clear every account | Fix | Noted in `PLAN.md`, M4 |
| R7 | The run ledger doesn't record which files a run read | Open | None |
| R8 | `decisions.md` said primary keys make a re-run add nothing; the store's look-up does | Fix | Reworded |
| R9 | A corrected profile is rejected for ever; no schema version; `run` ignores `settings.database` when given a store; one ingest line uncovered | Open | None |

The reasons for each decision are mine to add.

### M4 self-review (by the building agent)

| ID | Finding | Decision | Fix |
| --- | --- | --- | --- |
| R1 | The rules version didn't fix the thresholds: a file with `min_senders = 50` still loaded as `v1`, so `decisions.md` overclaimed replay | Fix | Each run stores a SHA-256 of the validated settings; the test failed first; claim reworded |
| R2 | `alert_at = 4` loaded, so with three signals nothing could alert and the run still succeeded | Fix | Counts above the number of signals are refused with exit code 2; two cases failed first |
| R3 | An account ID holding a newline or control character was accepted, and the summary prints flagged IDs as they are | Fix | Account IDs must be up to 64 letters, digits or `_ . : -`; 18 cases failed first. Closes the empty-ID part of M2's R5 |
| R4 | A missing input file crashed with a traceback and exit code 1 | Fix | A one-line message and exit code 2; the end-to-end test failed first |
| R5 | A re-run adds a second case per account, each at version 1 | Fix, at M4, keyed by the exact as-of time rather than the day, because the signals depend on it | A case per account and as-of time; a changed result raises the version and records the run, and a drop to NO_ALERT keeps the row; six integration tests failed first. Facts hash, reopening and audit events left for M5 |
| R6 | The re-run test would pass if both runs returned nothing | Fix | It also checks the flagged accounts |
| R7 | Money out before money in counts as pass-through, and an account paying itself counts as its own sender and pass-through; the README doesn't decide either | Open: mine to decide | None |
| R8 | No test for the rule that only an ALERT row in `cases` has a priority; `--out` is accepted but unused; `min_ratio = 1` gives the error `is_instance_of`; each run loads every stored payment; `design.md` still says the pipeline raises `NotImplementedError` | Open | None |

The reasons for each decision are mine to add.

### M5a self-review (by the building agent)

| ID | Finding | Decision | Fix |
| --- | --- | --- | --- |
| R1 | Money out before any came in breached pass-through, because a negative span counts as "at most 12 hours" | Fix | The span must be 0 or more, and the pack shows "from 0 to 12"; the test failed first; no golden outcome changed. Closes that part of M4's R7 |
| R2 | The facts hash covered labels and rounded values, so rewording a label would raise every case's version, stale every disposition and, at M6, break every recording | Fix | The hash covers exact values and codes only; two tests failed first |
| R3 | References are copied in full, and free text may hold a name | Keep in full, as quoted data | The risk is noted in `design.md` |
| R4 | Rounding a ratio down misleads when the threshold has more than two decimal places | Open | None |
| R5 | The threshold wording in `evidence.py` is separate from the comparisons in `signals.py`, and no test ties them together | Open | None |
| R6 | Pack file names collide for account IDs that differ only in case (macOS, Windows), `:` is invalid on Windows, and as-of times within one second share a name | Open | None |
| R7 | Packs are written after the commit: a failed write exits 2 without saying the run was stored, and two concurrent runs can leave a file behind the database | Open | None |
| R8 | Refusing an old database switches it to WAL first | Open | None |
| R9 | No test ties the hash stored on the case to the pack file's `facts_sha256` | Open | None |
| R10 | Untested: a payment to itself, and an empty ID, which renders as literal backticks | Open | None |

### M5b self-review (by the building agent)

| ID | Finding | Decision | Fix |
| --- | --- | --- | --- |
| R1 | `show` rebuilt the pack with the rules it was given, and warned only if the facts differed, so a different `alert_at` showed another outcome than the stored case with no warning | Fix | The view must match the stored outcome, priority, INCOMPLETE flag, reasons and facts hash; the test failed first |
| R2 | A closed case can be disposed of again at its current version, which records a second disposition and raises the version | Open: mine to decide | None |
| R3 | Anyone who can write to the database file can drop the triggers | Open | Noted in `design.md` |
| R4 | Two reviewers are tested one after the other, not concurrently (that's T) | Open | None |

### M5b review (by the building agent, at my request)

| ID | Finding | Decision | Fix |
| --- | --- | --- | --- |
| R1 | `design.md` said the console output is only IDs, codes and counts, but `show` prints whole packs, references included | Fix | Reworded |
| R2 | No test checked that a change to an open case is audited as `CASE_CHANGED`, so labelling every change as reopened would pass | Fix | New test; it failed when the label was broken on purpose |
| R3 | A locked or unreadable database crashed every command with a traceback and exit code 1 | Fix | A one-line message saying nothing was changed, and exit code 6; three cases failed first |
| R4 | A disposition's audit record didn't name the facts it was made on | Fix | Its payload holds the case's facts hash; the test failed first |
| R5 | `--version` accepts 0 or a negative number | Open | None |
| R6 | Pack files show the version they were written at, and no status, so they go stale after a disposition | Open | None |
| R7 | `show` exits 0 even when its pack differs from the stored case; no test for a disposition that fails part-way; `StoredCase.outcome` isn't typed; `dispose` prints `+00:00` where the packs print `Z` | Open | None |
| R8 | The reviewer ID is whatever the user types, and nothing records who viewed a case | Open: out of scope in `intent.md` | None |

Found at M5a before the review, and fixed: a database made before M5a crashed the run on the missing `facts_sha256` column, so the schema now has a version and an older database is refused; and the end-to-end test wrote packs into the repository's `out/`.

The reasons for each decision are mine to add.
