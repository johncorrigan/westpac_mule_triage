# Intent

What mule_triage is for, who it serves, and how it's judged. How it's built is in
[`design.md`](design.md), and how it's checked is in [`verify.md`](verify.md).

## The problem

A money mule is an account that receives illicit funds and quickly passes them on, often for
someone else. A bank flags possible mules for its financial-crime investigators, who decide what
happens to each one, and must be able to show an auditor later why. Four things make that hard:

- **Alerts that can't explain themselves.** An investigator needs the facts behind a flag, with
  where each came from and when it was known, not a score.
- **Missing data that clears an account silently.** If "unknown" is treated as "fine", an account
  with no profile slips through.
- **AI that decides, or invents.** A model can save an investigator typing, but it can state a
  wrong amount, obey text planted in a payment reference, or recommend an action.
- **A record no one can trust.** A decision that can be edited later, or made on facts that have
  since changed, makes the audit trail worthless.

mule_triage is a thin slice through that problem: one day of synthetic payments in, and a
prioritised, explainable queue out, with a person making every decision.

## Users

- **Financial-crime investigators**, who work through possible money-mule accounts and decide
  what happens to each one.
- **Their team leads**, who set the queue's priorities, check the investigators' dispositions
  and answer for them in an audit.

## Outcome

A prioritised, explainable alert queue for one day of synthetic payments:

- every account gets exactly one outcome: ALERT, with a priority and its reasons; NEEDS_REVIEW,
  when the known signals don't alert but an Unknown one could; or NO_ALERT;
- every case comes with an evidence pack in Markdown and JSON, in which every fact has an ID, a
  source and its times;
- every case has a narrative the reviewer can check, in which every sentence cites the facts it
  rests on: the model's draft if the validator accepts it, otherwise a template built from the
  facts, with the validator's reasons. With the recorded model, both golden alerts get the
  template ([`verify.md`](verify.md));
- the tool never takes an action on an account. A person decides, and their disposition is
  recorded in an audit table that refuses updates and deletes.

## How AI is used

**In building it.** A coding agent, Cursor running Claude Opus 5.5, wrote the plan, the code,
the tests and the drafts of these documents, to [`PLAN.md`](../PLAN.md) and
[`AGENTS.md`](../AGENTS.md). A separate planning chat checked each milestone's prompt against the
code, and at milestone R a separate chat reviewed the whole repository adversarially. I own the
plan, the expected outcomes and every commit, and I review every diff. The golden answer key and
the recorded model replies are mine. Hooks block the agents' direct edits to them, but can't see
through variables, wildcards or code that builds a path; the golden checksum and my own commits
cover those routes. The detail is in [`agentic-workflow.md`](agentic-workflow.md).

**Inside it.** A small local model, run through Ollama, drafts the case narrative, and nothing
else: it never decides an outcome or a priority, and never sees them. A deterministic validator
checks that every sentence cites real fact IDs, and that its numbers and dates appear in those
facts. Any failure falls back to a template that states the reason, and an unreachable model
degrades the run rather than failing it. By default the tool replays committed recordings, so no
test and no demonstration needs a model.

## Assumptions

- The data is synthetic, with pseudonymous IDs and no names.
- One run covers one day of payments and account profiles, as JSON Lines.
- One person runs it on their own machine, with a local SQLite database.
- Users run Python 3.13 or newer. The code targets 3.11 as its floor, and `mypy` checks it
  against 3.11, but only Python 3.14.7 has run it ([`verify.md`](verify.md), known gaps).
- The Ollama endpoint is local. The tool accepts a remote one, even over plain HTTP, which would
  send the case's facts off the machine.

## Constraints

- Plain Python 3.11 or newer, with venv and pip only; pydantic is the only runtime dependency.
- No network in tests, and the model runs locally.
- Three hours of build time, to the milestones in [`PLAN.md`](../PLAN.md).
- Managed laptops, which block tools such as uv, ruff, poetry and conda.

## Out of scope

- Real customer data or any production use.
- Taking any action on an account, such as freezing it or reporting it.
- CI and CD: cut for this build ([`PLAN.md`](../PLAN.md), Cuts). [`verify.md`](verify.md) says
  where each check would run.
- A user interface beyond the command line, and access control for more than one user.
- The data generator (G) and the threaded concurrency test (T), both optional in `PLAN.md`, and
  neither built.

## Success criteria

| Criterion | Result | Evidence |
| --- | --- | --- |
| `python scripts/verify.py` is green, with every gate on | Met: 407 tests at 96 % coverage, every gate passing, in a Git checkout and in a copy without Git | [`verify.md`](verify.md) |
| The golden acceptance test passes against my answer key | Met since M4 | `tests/golden/test_golden.py` |
| Every case's evidence pack meets M5a's and M5b's checks | Met: every fact has an ID, a source and its times; the counterparty cap; byte-identical JSON; untrusted text as inline code; the facts hash, audit and stale-version checks | `tests/unit/test_evidence.py` and `test_report.py`; `tests/integration/test_audit.py`, `test_pipeline.py` and `test_review.py` |
| Every narrative meets M6's checks | Met: the replay gates pass, every bad reply falls back with its reason, and an unreachable model degrades the run | `tests/golden/test_recordings.py`, `tests/integration/test_narrative_run.py` |
| At least one guarded failure, rejection, degrade or abstain path works | Met: all four, through the real command line | [`operate.md`](operate.md), the [README](../README.md#try-it-one-day-end-to-end) |

No criterion scores the rules or the AI narrative against a threshold: both evaluation metrics
were cut ([`PLAN.md`](../PLAN.md), Cuts), because the only labelled data is the golden day.

## The golden scenario

One day, 2 November 2026, judged at midnight UTC: 35 payment lines and 39 profiles in
`tests/golden/`, with my answer key in `tests/golden/expected/outcomes.toml`. The key, the golden
README and both data files are checksummed. `tests/golden/test_golden.py` compares a run with the
key, and has passed since M4.

- **Covered:** every outcome, with ALERT for ACC-101 and ACC-106, NEEDS_REVIEW for ACC-105 and
  NO_ALERT for the rest; the one Unknown reason, `PROFILE_MISSING`; an INCOMPLETE alert; HIGH
  priority, once raised by an Unknown; a deliberate miss and two decoys; and an invalid record, a
  duplicate and an instruction injected into a payment reference.
- **Not covered:** a MEDIUM alert; a record that arrives after the as-of time or falls outside
  the 24-hour window, so the knowledge-time filtering isn't tested end to end; a Conflict; and
  more than one day.
