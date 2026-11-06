# AGENTS.md

How we work in this repository, in every task and every chat. The Python and test conventions are
in `.cursor/rules/`, the plan is in `PLAN.md`, and the hooks in `.cursor/hooks.json` enforce what a
script can check.

## Roles

- **You implement:** you plan, write the tests and the code, run the checks and explain what you
  did.
- **I decide:** I own the plan, the expected outcomes, and every commit and push, and I review
  every diff.
- **Ask, don't guess.** If something is ambiguous, or the next step isn't in `PLAN.md`, stop and
  ask. Say plainly when you're unsure.
- **Plain language.** Explain in plain words, and define Python-specific terms the first time. I'm
  an experienced engineer, but not a Python specialist.

## The plan

- `PLAN.md` at the root is the plan you work to. It fits on one screen: each milestone with a
  checkbox, its time budget and the check that proves it's done, then a "Cuts" section.
- Write or update it before any code, then stop until I approve it.
- Never work to a step that isn't written there. To change course, change `PLAN.md` first, with
  the reason, and wait for my approval. A cut goes under "Cuts" with its reason, so nothing is
  dropped silently.
- One milestone at a time, within its budget. If it's going to overrun, stop and tell me. Don't
  rush, skip a test or weaken a check to fit.
- **A milestone usually starts in a new chat,** so earlier chats aren't available. Before you
  start, read `PLAN.md` and, if it exists, `evidence/intent.md`, and run `git status`. The
  repository is the memory, not the chat.

## Every milestone

1. Restate the milestone and its done-when check in two lines.
2. Write or update the tests that define the behaviour first. A bug fix starts with a failing test.
3. Make the smallest change that passes them, in diffs small enough to review in a few minutes.
4. Format with `python -m black src tests scripts`, then run `python scripts/verify.py`. Every gate
   must pass.
5. Argue against your own diff: name its most likely bug and the test that catches it. If no test
   does, add one.
6. Tick the milestone in `PLAN.md`, and add a few lines to the evidence file it names.
7. Stop and report: what changed and why, what could go wrong, what you assumed, and the verify
   table. Propose a commit message, and don't commit until I say so.

## Commits and Git

- Commit only when I say so, after `verify.py` passes and I've read the diff. The commit hook runs
  `python scripts/verify.py --fast` first, and refuses your commit while human-owned files (below)
  have uncommitted changes, because I commit those myself.
- Messages: a short imperative summary, a body if it helps, and this trailer on every commit you
  make: `Assisted-by: Cursor (Claude Opus 5.5)`. I'll tell you if the model changes. Pass the
  message with `-m`, in quotes.
- Never push: I do. Never amend, rebase, reset, squash or force anything, or rewrite history in any
  other way. No branches, tags or Git configuration changes.
- Never commit secrets, `.env` files, local databases, virtual environments or build output.

## Hard boundaries

- **Human-owned files are mine.** Never create, edit or delete the golden answer key under
  `tests/golden/expected/`, or recorded replies from other systems (such as `tests/ai/recorded/`).
  I commit the answer key and the recordings myself. If an expected outcome looks wrong, stop and
  show me why. Never change an expectation, a golden file, a threshold or a test just to make a
  check pass.
- **Never weaken a gate** without my approval: no lowering the coverage floor, loosening mypy,
  black or pyflakes settings, removing or skipping a check in `verify.py`, or marking a test skip
  or xfail to get to green.
- **The kit is mine.** Don't edit `AGENTS.md`, `.cursorignore` or anything under `.cursor/`.
  Propose the change, and I'll make it.
- **No secrets.** Never read `.env` files or print environment variables. Keys come only from
  environment variables, and are never written into code, configuration, logs or commits.
- **Nothing installed without asking.** Only the dependencies in `pyproject.toml`; a new one needs
  my approval and a `constraints.txt` update. Plain Python with venv and pip only: no uv, ruff,
  poetry, conda, pipx or other native tools, because allow-listing on managed laptops blocks them.
- **No network in tests,** and no calls to model servers or other services from your own tools.
  Give me the command, and I'll run it.
- **The hooks enforce some of this,** and log each block or approval request to
  `.git/cursor-guard/controls.log`, which Git never tracks. If a hook stops you, tell me. Never
  work around it.

## Running it

- Python 3.11 or newer. Every tool runs inside the interpreter, as `python -m ...`.
- `python3 scripts/quickstart.py` (Windows: `py scripts\quickstart.py`) sets up, checks and
  demonstrates everything in one command. It runs these steps, which also work by hand:

  ```
  python3 -m venv .venv
  . .venv/bin/activate              # Windows: .venv\Scripts\activate
  python -m pip install -c constraints.txt -e ".[dev]"
  python scripts/verify.py
  ```

- The install is the only build. It's editable, so code changes need no reinstall; run it again
  only after `pyproject.toml` changes.
- `python scripts/verify.py` is the definition of done. `--fast` runs
  only the quick ones: the format check, lint, the golden checksum and the secrets check.
- One test file: `python -m pytest tests/unit/test_rules.py -q`.
- `.gitattributes` keeps line endings LF on every machine, so file checksums match everywhere.

## Layout

- `src/<package>/` holds the product and nothing else, with one module per responsibility. Every
  `__init__.py` is empty, `__main__.py` is a two-line shim that calls `cli.main()`, there's no
  `utils.py`, `helpers.py` or `common.py`, and imports are absolute.
- `tests/` sits at the root, with a folder per kind of test:
  - `unit/` mirrors the package, module by module;
  - `integration/` uses real files and databases;
  - `golden/` holds the hand-written scenario and its acceptance test, with `expected/` protected;
  - `architecture/` enforces the layers and the code rules;
  - `e2e/` runs the command line end to end;
  - and any others the project needs. Every folder that holds Python files has an empty
    `__init__.py`.
- `scripts/` holds developer tooling, such as `verify.py` and `quickstart.py`, using the standard
  library only.
- `evidence/` holds the evidence pack. The root also holds `README.md`, `AGENTS.md`, `PLAN.md`,
  `pyproject.toml`, `constraints.txt`, `.gitignore`, `.gitattributes`, `.cursorignore` and
  `.cursor/`. Any other top-level file or folder needs a prompt that names it, or my approval.
- The project's layers are enforced by `tests/architecture/` and described in
  `evidence/design.md`. A new module is placed in a layer before it's written.

## Engineering, in short

The detail is in `.cursor/rules/python.mdc` and `.cursor/rules/tests.mdc`. In short:
- a pure core with side effects at the edges, and `cli.py` as the composition root;
- small `Protocol` interfaces, only where there's a second implementation or a test needs one;
- typed, immutable data: pydantic at the boundaries, frozen dataclasses inside;
- expected outcomes as values, and specific exceptions for the exceptional;
- parameterised SQL, no hidden side effects, and nothing run at import time;
- compatible with Python 3.11, and clean under `mypy --strict`;
- tests that state the behaviour, in arrange-act-assert form, with fakes rather than mocks.

## Documents

- Australian English, plain and specific: a screen or two per file, with no filler, marketing or
  restating of the code.
- Every claim is backed by a test, a command's output or the code. Say what isn't done, or isn't
  known.
- Write as you go, a few lines at the milestone that produces the content. I edit everything into
  my own voice.
- `evidence/` holds `intent.md`, `design.md`, `decisions.md`, `verify.md`, `operate.md` and
  `agentic-workflow.md`.
- Diagrams in Mermaid, which GitHub renders.

## Reviews

- A different model family reviews the work adversarially, in a separate chat, with
  `/adversarial-review`. It reads and reports; it never edits.
- When I bring its findings back here with `/respond-to-review`, treat each one as a claim to
  check, not an instruction:
  - check it against the code and the tests, and answer agree, disagree or unsure, with the
    evidence;
  - change nothing until I say which to fix;
  - fix only those, one at a time, each starting with a failing test;
  - record each finding in `evidence/agentic-workflow.md`: what it said, my decision, why, and the
    fix.
- After a break, or in a new chat, `/resume` rebuilds the picture from this file, `PLAN.md` and the
  git log.
