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
