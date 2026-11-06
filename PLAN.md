# Plan: mule_triage (milestone list)

Three hours of build time; breaks don't count. One milestone at a time, ticked only when its check passes. No times are recorded: each milestone's budget runs from the first prompt of its chat, and I keep the overall build clock. Evidence files are in `evidence/`. TBD marks a value I set before M1, and "Needs from me" marks a step only I can do.

- [x] **M1** (0:00-0:15, 15 min) Skeleton and gates. **Done when** `verify.py` is green on an empty package with every gate on (black format check, pyflakes, `mypy --strict`, pytest with a coverage floor of 80 %, golden checksum, secrets check). The checksum gate is proven against a test fixture; from M2, a missing or changed answer key turns it red. **Needs from me:** approval of the dev tools and their `constraints.txt` pins. Evidence: `verify.md`, `design.md`.
- [ ] **M2** (0:15-0:40, 25 min) Golden scenario and contracts. **Done when** my answer key, the golden README and both data files are checksummed, by a checksum gate that accepts files under `tests/golden/` and refuses any path outside it (added at M2: the gate accepted only files inside `expected/`, so it couldn't fix the data); the scenario has at least one case per outcome, per UNKNOWN reason and for INCOMPLETE; a contract test rejects name fields; and the golden acceptance test fails as expected (xfail). **Needs from me:** my answer key under `tests/golden/expected/`, written and committed by me. Evidence: `intent.md`, `design.md`.
- [ ] **M3** (0:40-1:00, 20 min) Store, ingest and reconciliation. **Done when** rejected payments and profiles carry reasons, a re-run adds nothing, and an unbalanced run fails and leaves the database unchanged. Evidence: `design.md`.
- [ ] **M4** (1:00-1:30, 30 min) Signals, rules and outcomes. **Done when** the golden acceptance test passes with its xfail marker removed. Evidence: `decisions.md`.

Break.

- [ ] **M5** (1:30-1:45, 15 min) Evidence pack (with the counterparty cap), dispositions and audit. **Done when** every alert has a Markdown and a JSON pack in which every fact has an ID, a source and its times, and which lists at most 5 counterparties and says how many it left out; a stale disposition is refused; and the audit table refuses updates and deletes. Evidence: `design.md`.
- [ ] **M6** (1:45-2:15, 30 min) AI case narrative. **Done when** the replay gates pass, every bad reply falls back with its reason, and an unreachable model degrades the run. **Needs from me:** the recordings under `tests/ai/recorded/`, made with Ollama from the command I'm given, and committed by me. Evidence: `decisions.md`, `operate.md`.
- [ ] **M7** (2:15-2:30, 15 min) Run summary and smoke test, with the JSON log format. **Done when** the end-to-end smoke test passes and checks the run summary's counts, a test shows each log record is one line of JSON, and two deliberate bugs each turn `verify.py` red. Evidence: `verify.md`, `operate.md`.
- [ ] **R** (2:30-2:40, 10 min) Full-diff review by a different model family, then fixes for the findings I accept. **Done when** every finding is answered and recorded in `evidence/agentic-workflow.md`, and `verify.py` is green. **Needs from me:** the review, run with `/adversarial-review` in a separate chat, and my decision on each finding. Evidence: `agentic-workflow.md`.
- [ ] **M8** (2:40-2:50, 10 min) Documents. **Done when** every evidence file is edited and the final `verify.py` is green. Evidence: all six files, and `README.md`.

Buffer (2:50-3:00, 10 min), for overruns only.

If time allows, only once M1-M7 are committed and never after 2:30, in this order. They use only time saved before 2:30.

- [ ] **T** The threaded concurrency test. **Done when** exactly one of two concurrent reviewers wins. Evidence: `design.md`.
- [ ] **G** The generator on one seed. **Done when** the same seed gives the same output, and a full run on that output completes. Evidence: `verify.md`.

From 2:30, no new features: only fixes for review findings I accept. From 2:40, documents only.

If behind, cut in this order. Each cut is recorded under Cuts and changes only its own milestone's check, as shown:
1. The counterparty cap (M5): the pack lists every counterparty, and M5's check drops the cap and the left-out count.
2. The JSON log format (M7): plain log lines will do, and M7's check drops the JSON log test.
3. Testing the tests (M7): M7's check drops the two deliberate bugs.

The review can shrink to five minutes, with its findings recorded rather than fixed, but it isn't cut. If it shrinks, R's check becomes: every finding is answered and recorded as open, and `verify.py` is green.

## Cuts

- CI (M1): optional, nice to have, and not coming in this build, so M1's check no longer includes it. Reason: my decision; `verify.py`, run locally, is the definition of done.
