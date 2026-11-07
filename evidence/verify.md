# Verification

First draft, written at M1. This build has no CI: the CI and CD path is retired (`PLAN.md`,
Cuts). So this says what runs today, where each check would run in a full delivery, and what
nothing runs.

## What runs today

`python scripts/verify.py` runs every gate, in order, and prints a pass/fail table:

1. black, the formatting check;
2. pyflakes, the lint;
3. mypy, in strict mode, over `src/`, `tests/` and `scripts/`, targeting Python 3.11;
4. pytest over every test folder, with an 80% coverage floor on the package;
5. the golden checksum of my answer key, the golden README and both data files, listed in
   `tests/golden/expected/SHA256SUMS` by their paths in `tests/golden/`. A missing or changed
   file fails it, as do a file in `expected/` that isn't listed, a listed path outside
   `tests/golden/` and a missing answer key;
6. a secrets check over every file Git tracks or would track;
7. a smoke run, from M7, of `python -m mule_triage run` on the golden data in replay mode, into
   a new temporary database and folder. It checks for exit code 0, the summary's payment counts
   against the answer key's `[ingest]` section, a new case for each account the key doesn't
   give its default outcome, and one Markdown and one JSON pack for each. Until M7 it ran
   `--help`. `tests/e2e/test_run_command.py` runs the same check under pytest.

Before any of them, it checks that every direct dependency is installed, that nothing installed
differs from its pin in `constraints.txt`, and that the package loads from this checkout. `--fast` runs only gates 1, 2, 5 and 6, in under a second,
and the commit hook runs it before every agent commit. `python3 scripts/quickstart.py` sets up
`.venv` and runs the full set.

Result at the end of M1, on macOS with Python 3.14.7:

```text
Gate             Result  Seconds  Detail
---------------  ------  -------  ------
black            PASS        0.3  19 files would be left unchanged.
pyflakes         PASS        0.1
mypy             PASS        0.4  Success: no issues found in 19 source files
pytest           PASS        0.9  71 passed in 0.24s
golden checksum  SKIP        0.0  tests/golden/expected/ doesn't exist yet
secrets          PASS        0.1  39 files checked
smoke            PASS        0.1  python -m mule_triage --help

verify: passed, with 1 gate(s) skipped
```

## Live evaluation of the narrative (M6)

A smoke test of real output, not a measured rate: only the two golden alerts, ACC-101 and
ACC-106, get drafts, and ACC-105, as NEEDS_REVIEW, gets the template without asking the model.
The AI narrative metric in `intent.md` is still TBD.

- **Model:** `granite3.3:8b`, digest
  `fd429f23b90980ed1bef53b990894e7b0199331f6ae90c5650240a7d5b70f1f7`, with prompt `narrative-v1`,
  temperature 0, seed 42 and `num_ctx` 8192. I recorded both replies with `--ai live --record`,
  read them and committed them (`tests/ai/recorded/`).
- **Acceptance:** 0 of 2 drafts. By sentence, 4 of 11 have no problem: 2 of ACC-101's 6, and 2
  of ACC-106's 5. Both cases get the template with the validator's reasons, and the run exits 0.
- **Rejection reasons:** 10 `UNSUPPORTED_DATE` and 4 `UNSUPPORTED_NUMBER`, 5 and 2 for each case;
  no other reason. Four are real errors: ACC-101's "$4500" received (the facts give seven
  payments of 900.00) and its payments out "between 09:00 and 11:30" (13:00 and 14:00); and
  ACC-106's "received 90%" (it sent on 0.90) and "$600" sent to ACC-351 (2700.00). The rest are
  right values in another form: "95%" for 0.95, and a date or time without the full timestamp
  the facts show. Nothing flagged ACC-106's sentence 4, which lists four of its five payers.
- **To repeat it:** `python -m mule_triage run tests/golden/payments.jsonl
  tests/golden/profiles.jsonl --as-of 2026-11-03T00:00:00Z --db <new file> --out <folder>`
  replays the recordings and prints `REJECTED: 2`; each reason is in the `NARRATIVE` audit
  event. `tests/golden/test_recordings.py` pins the reason counts, so a new recording or a
  changed validator must update this section.

## Testing the tests (M7)

Each deliberate bug was applied on its own to a clean tree at commit `0faead0`, before M7's
changes; `verify.py` failed, and after the revert `git diff` was empty.

1. **An Unknown account age is treated as an old account:** in `detection/rules.py`,
   `outcome_for` stopped counting an Unknown signal (`unknowns += 1` removed), so a missing
   profile counted as not breaching. 18 tests failed, among them the golden acceptance test
   (ACC-105 dropped to NO_ALERT and ACC-106 to MEDIUM, no longer INCOMPLETE), `test_rules.py`'s
   "one breach and one Unknown" and "two breach and one Unknown" cases, `test_evidence.py`'s
   missing-profile test, the narrator's NEEDS_REVIEW test and the end-to-end run. Applied again
   to M7's code, it failed 22 tests and the new smoke gate too: 2 cases and 4 packs, not 3 and 6.
2. **The validator stops checking citations:** in `narrative/validator.py`, the checks for a
   sentence citing no fact and for a fact outside the pack were removed, and unknown fact IDs
   skipped. 3 tests failed, all in `tests/unit/narrative/test_validator.py`:
   `test_rejects_a_sentence_that_cites_no_fact`,
   `test_rejects_a_sentence_that_cites_a_fact_outside_this_pack` and
   `test_a_problem_never_quotes_the_models_free_text`. Nothing beyond the validator's own unit
   tests would notice: the golden recordings cite real facts, so their replay test still passed.

Both gave the same table, apart from the seconds:

```text
Gate             Result  Seconds  Detail
---------------  ------  -------  ------
black            PASS        0.9  65 files would be left unchanged.
pyflakes         PASS        0.3
mypy             PASS        0.3  Success: no issues found in 65 source files
pytest           FAIL        3.7  exit code 1
golden checksum  PASS        0.0  4 files match SHA256SUMS
secrets          PASS        0.1  95 files checked
smoke            PASS        0.2  python -m mule_triage --help

verify: FAILED (pytest)
```

Result at the end of M7, on macOS with Python 3.14.7:

```text
Gate             Result  Seconds  Detail
---------------  ------  -------  ------
black            PASS        0.3  69 files would be left unchanged.
pyflakes         PASS        0.2
mypy             PASS        0.3  Success: no issues found in 69 source files
pytest           PASS        4.9  399 passed in 4.47s
golden checksum  PASS        0.0  4 files match SHA256SUMS
secrets          PASS        0.1  99 files checked
smoke            PASS        0.2  golden replay: the answer key's payment counts, a pack per case

verify: every gate passed
```

## Where each check would run

| Stage | What would run there |
| --- | --- |
| Locally | `python scripts/verify.py`, with `--fast` before every agent commit |
| Pull request | the same command on clean Linux and Windows runners, on Python 3.11 and the latest release, plus a secrets scanner over the full Git history |
| Delivery pipeline | one artefact, built once, with a dependency scan; deployed to a test environment and smoke-tested there with the golden scenario; promoted with an approval; rolled back by pinning the rules, prompt and model versions |

Only the first row exists in this build.

## Known gaps

- **What no CI leaves unrun:** Python 3.11 itself, since only 3.14 has run these gates; Windows;
  and a clean checkout on every push. The pins in `constraints.txt` are proven only on macOS
  with Python 3.14.
- **No secrets scan of the history:** the secrets check reads the current files, not past
  commits, and uses a handful of patterns rather than a full scanner.
- **The version checks at the top of `verify.py` and `quickstart.py`** are skipped by mypy and
  run by no test. They were checked by hand with Apple's Python 3.9.6.
- **The guard's own tests,** `.cursor/hooks/guard_tests.py`, are run by nothing now that CI is
  retired.
- **The end-to-end log test never sees a `model_call` record,** which is logged only for a live
  reply, and neither replay nor an unreachable model gives one. Its fields are pinned in memory
  instead, by `tests/integration/test_narrative_run.py`.
- **The architecture rules read code as written,** so they miss what only shows when it runs:
  imports made through `importlib`, the clock or `eval` reached through `getattr` or `builtins`,
  `from os import system`, and SQL assembled without an uppercase SQL keyword in the literal
  text.
