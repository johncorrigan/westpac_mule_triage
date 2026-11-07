# Plan: mule_triage (milestone list)

Three hours of build time; breaks don't count. One milestone at a time, ticked only when its check passes. No times are recorded: each milestone's budget runs from the first prompt of its chat, and I keep the overall build clock. Evidence files are in `evidence/`. TBD marks a value I set before M1, and "Needs from me" marks a step only I can do.

- [x] **M1** (0:00-0:15, 15 min) Skeleton and gates. **Done when** `verify.py` is green on an empty package with every gate on (black format check, pyflakes, `mypy --strict`, pytest with a coverage floor of 80 %, golden checksum, secrets check). The checksum gate is proven against a test fixture; from M2, a missing or changed answer key turns it red. **Needs from me:** approval of the dev tools and their `constraints.txt` pins. Evidence: `verify.md`, `design.md`.
- [x] **M2** (0:15-0:40, 25 min) Golden scenario and contracts. **Done when** my answer key, the golden README and both data files are checksummed, by a checksum gate that accepts files under `tests/golden/` and refuses any path outside it (added at M2: the gate accepted only files inside `expected/`, so it couldn't fix the data); the scenario has at least one case per outcome, per UNKNOWN reason and for INCOMPLETE; a contract test rejects name fields; and the golden acceptance test fails as expected (xfail). **Needs from me:** my answer key under `tests/golden/expected/`, written and committed by me. Evidence: `intent.md`, `design.md`.
- [x] **M3** (0:40-1:00, 20 min) Store, ingest and reconciliation. **Done when** rejected payments and profiles carry reasons, a re-run adds nothing, and an unbalanced run fails and leaves the database unchanged. Evidence: `design.md`.
- [x] **M4** (1:00-1:30, 30 min) Signals, rules and outcomes. **Done when** the golden acceptance test passes with its xfail marker removed. Signals read every payment stored and known by the as-of time, never only the run's accepted records (added at M3's self-review: on a re-run every record is a duplicate, so the run's own list is empty). A case is kept per account and exact as-of time; a re-run raises its version and records the run only when its outcome, priority, reasons or INCOMPLETE flag change, and a case that drops to NO_ALERT keeps its row (added at M4's self-review: a case per run duplicated the queue on every re-run, and a re-run could never make a disposition stale). Evidence: `decisions.md`.

Break.

- [x] **M5a** (1:30-1:40, 10 min) Evidence pack (with the counterparty cap), reports and the facts hash. **Done when** every case, including NEEDS_REVIEW, has a Markdown and a JSON pack under `--out` in which every fact has an ID, a source and its times; the pack lists at most 5 counterparties and says how many it left out (ACC-101: "5 of 9 shown; 4 left out"); the same data gives byte-identical JSON; untrusted text renders as inline code; and a case stores its facts' hash, so a re-run of the same files changes no case and one more payment into ACC-101 raises its version. Evidence: `design.md`.
- [x] **M5b** (1:40-1:50, 10 min) Audit, dispositions and the show command. **Done when** the audit table refuses UPDATE, DELETE and INSERT OR REPLACE on a real SQLite file; every case creation and change is an audit event with its run; `dispose` takes the version the reviewer saw, with one of ESCALATED, FALSE_POSITIVE, INSUFFICIENT_EVIDENCE or NO_FURTHER_ACTION, and a stale one is refused as a result value; a disposed case that changes reopens, with its earlier disposition still in the audit table; and `show` displays one case with its version. Also the architecture, data flow, privacy and production notes in `design.md`. Evidence: `design.md`.
- [x] **M6** (1:50-2:20, 30 min) AI case narrative. **Done when** the replay gates pass, every bad reply falls back with its reason, and an unreachable model degrades the run. **Needs from me:** the recordings under `tests/ai/recorded/`, made with Ollama from the command I'm given, and committed by me. Evidence: `decisions.md`, `operate.md`.
- [ ] **M7** (2:20-2:30, 10 min) Run summary and smoke test, with the JSON log format. **Done when** the end-to-end smoke test passes and checks the run summary's counts, a test shows each log record is one line of JSON, and two deliberate bugs each turn `verify.py` red. Evidence: `verify.md`, `operate.md`.
- [ ] **R** (2:30-2:40, 10 min) Full-diff review by a different model family, then fixes for the findings I accept. **Done when** every finding is answered and recorded in `evidence/agentic-workflow.md`, and `verify.py` is green. **Needs from me:** the review, run with `/adversarial-review` in a separate chat, and my decision on each finding. Evidence: `agentic-workflow.md`.
- [ ] **M8** (2:40-2:50, 10 min) Documents. **Done when** every evidence file is edited and the final `verify.py` is green. Evidence: all six files, and `README.md`.

Buffer (2:50-3:00, 10 min), for overruns only.

If time allows, only once M1-M7 are committed and never after 2:30, in this order. They use only time saved before 2:30.

- [ ] **T** The threaded concurrency test. **Done when** exactly one of two concurrent reviewers wins. Evidence: `design.md`.
- [ ] **G** The generator on one seed. **Done when** the same seed gives the same output, and a full run on that output completes. Evidence: `verify.md`.
- [x] **S** (10 min) Subpackages, with no logic change (added before M7, and run then as an exception to the order above, so M7's loggers get their final names and R reviews the final layout): `ingest` and `store` move into `records/`, `signals` and `rules` into `detection/`, and `review` and `audit` into `decision/`; their unit tests move with them, and the layer map names each subpackage. **Done when** every moved file shows in `git diff -M` as a rename whose only changes are imports; the test count and the coverage figure equal those of the run before the move; the golden checksum passes and `tests/ai/recorded/` is unchanged; the replay and completeness tests pass; and `python -m mule_triage --help` and `python scripts/quickstart.py` run. Evidence: `design.md`.
- [ ] **N** Prompt `narrative-v2` (added at M6: both `granite3.3:8b` drafts for `narrative-v1` fail the validator, and a `llama` draft passed it with factual errors). **Done when** both golden alerts' recorded drafts pass the validator, a person finds no factual error in either, and the accepted-but-wrong `llama` draft is a failing hand-written test that the validator or the prompt now handles. Evidence: `decisions.md`, `agentic-workflow.md`.

From 2:30, no new features: only fixes for review findings I accept. From 2:40, documents only.

If behind, cut in this order. Each cut is recorded under Cuts and changes only its own milestone's check, as shown:
1. The counterparty cap (M5): the pack lists every counterparty, and M5's check drops the cap and the left-out count.
2. The JSON log format (M7): plain log lines will do, and M7's check drops the JSON log test.
3. Testing the tests (M7): M7's check drops the two deliberate bugs.

The review can shrink to five minutes, with its findings recorded rather than fixed, but it isn't cut. If it shrinks, R's check becomes: every finding is answered and recorded as open, and `verify.py` is green.

## Cuts

- CI (M1): optional, nice to have, and not coming in this build, so M1's check no longer includes it. Reason: my decision; `verify.py`, run locally, is the definition of done.
