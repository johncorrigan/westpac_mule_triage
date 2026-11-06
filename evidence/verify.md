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
7. a smoke run of `python -m mule_triage --help`.

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
- **The architecture rules read code as written,** so they miss what only shows when it runs:
  imports made through `importlib`, the clock or `eval` reached through `getattr` or `builtins`,
  `from os import system`, and SQL assembled without an uppercase SQL keyword in the literal
  text.
