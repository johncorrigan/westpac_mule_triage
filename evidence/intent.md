# Intent

First draft, written at M1. What mule_triage is for, who it serves and how we'll judge it. How
it's built is in `design.md`; how it's checked is in `verify.md`.

## Users

- **Financial-crime investigators**, who work through possible money-mule accounts and decide
  what happens to each one.
- **Their team leads**, who set the queue's priorities, check the investigators' dispositions
  and answer for them in an audit.

## Outcome

A prioritised, explainable alert queue for one day of synthetic payments:

- every account gets exactly one outcome: ALERT, with a priority and its reasons; NEEDS_REVIEW,
  when the known signals don't alert but an UNKNOWN one could; or NO_ALERT;
- every alert comes with trustworthy evidence: a pack in Markdown and JSON in which every fact
  has an ID, a source and its times;
- every alert has a checked first-draft narrative, in which every sentence cites the facts it
  rests on;
- the tool never takes an action on an account. A person decides, and their disposition is
  recorded in an audit table that refuses updates and deletes.

## How AI is used

**In building it.** A coding agent (Cursor, with Claude Opus 5.5) writes the plan, tests, code
and documents to `PLAN.md` and `AGENTS.md`. I own the plan, the expected outcomes and every
commit, and I review every diff. The golden answer key and the recorded model replies are mine.
Hooks block the agent's direct edits to them, but can't see through variables, wildcards or code
that builds a path; the golden checksum and my own commits cover those routes. A model from a different family reviews the full diff
adversarially before the documents are finished (milestone R).

**Inside it.** A small local model, run through Ollama, drafts the case narrative, and nothing
else: it never decides an outcome or a priority. A deterministic validator checks that every
sentence cites real fact IDs. Any failure falls back to a template that states the reason, and
an unreachable model degrades the run rather than failing it. By default the tool replays
committed recordings, so no test needs a model.

## Assumptions

- The data is synthetic, with pseudonymous IDs and no names.
- One run covers one day of payments and account profiles, as JSON Lines.
- One person runs it on their own machine, with a local SQLite database.
- Users run Python 3.13 or newer; the supported floor is 3.11.

## Constraints

- Plain Python 3.11 or newer, with venv and pip only; pydantic is the only runtime dependency.
- No network in tests, and the model runs locally.
- Three hours of build time, to the milestones in `PLAN.md`.
- Managed laptops, which block tools such as uv, ruff, poetry and conda.

## Out of scope

- Real customer data or any production use.
- Taking any action on an account, such as freezing it or reporting it.
- CI and CD: retired for this build (`PLAN.md`, Cuts). `verify.md` says where each check would
  run.
- A user interface beyond the command line, and access control for more than one user.
- The data generator and the threaded concurrency test, unless time allows (`PLAN.md`, T and G).

## Success criteria

- `python scripts/verify.py` is green, with every gate on.
- The golden acceptance test passes against my answer key.
- Every alert's evidence pack and narrative meet the checks in `PLAN.md`, M5 and M6.

No criterion scores the rules or the AI narrative against a threshold: both evaluation metrics
were cut (`PLAN.md`, Cuts).

## The golden scenario

Written at M2. One day, 2 November 2026, judged at midnight UTC: 35 payment lines and 39
profiles in `tests/golden/`, with my answer key in `expected/outcomes.toml`. The key, the README
and both data files are checksummed. `tests/golden/test_golden.py` compares a run with the key,
and is an expected failure until M4.

- **Covered:** every outcome, with ALERT for ACC-101 and ACC-106, NEEDS_REVIEW for ACC-105 and
  NO_ALERT for the rest; the one Unknown reason, `PROFILE_MISSING`; an INCOMPLETE alert; HIGH
  priority, once raised by an Unknown; a deliberate miss and two decoys; and an invalid record, a
  duplicate and an instruction injected into a payment reference.
- **Not covered:** a MEDIUM alert; a record that arrives after the as-of time or falls outside
  the 24-hour window, so the knowledge-time filtering isn't tested end to end; a Conflict; and
  more than one day.
