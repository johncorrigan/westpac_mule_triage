# Decisions

Each entry gives the decision, why, and what it costs. Written at the milestone that makes it.

## SQLite over loose files (M3)

- **Decision:** one SQLite database, through the standard library's `sqlite3`, rather than JSON
  or CSV files written per run. No new dependency.
- **Why:** a run is written in one transaction, so a run that fails part-way or doesn't balance
  leaves the database exactly as it was (`tests/integration/test_pipeline.py`). Payments and
  profiles are keyed by their IDs, and the store looks each record up before writing it, so a
  re-run adds nothing (the golden second ingest). The reconciliation counts stored rows with SQL
  rather than trusting counters in memory. Loose files
  can be left half-written, and give no way to make a check and a write succeed or fail together.
- **Cost:** one writer at a time, eased by WAL mode and a 5-second busy timeout, and a file that
  people can't read directly. Not yet tested: the busy timeout, and WAL on a network drive, where
  SQLite may silently keep its old journal mode.

## Rules for detection, and NEEDS_REVIEW instead of a silent clear (M4)

- **Decision:** outcomes come from three deterministic signals and fixed thresholds in
  `rules/rules-v1.toml`, never from a model. An account whose known signals don't alert, but
  would if its Unknown signals breached, gets NEEDS_REVIEW rather than NO_ALERT.
- **Why:** every outcome can be explained by its reason codes, and replayed from the stored
  payments, the as-of time and a rules file whose settings match the run's record. Each run stores
  the rules version and a SHA-256 checksum of the validated settings, so two files with the same
  version and different thresholds can be told apart (`tests/integration/test_pipeline.py`). The
  rules file refuses a missing, invalid or unknown setting, or an alert count above the number of
  signals, so a typo stops the run (exit code 2) rather than falling back to a default or never
  alerting (`tests/unit/test_cli.py`). Missing data is a value, not a zero: ACC-105 has no
  profile and would alert if it were new, so it goes to a person instead of being cleared, and an
  Unknown never lowers an alert's priority (the golden test, `tests/unit/detection/test_rules.py`).
- **Cost:** rules this simple can be gamed. ACC-102 stays one sender and 0.05 of a ratio under the
  thresholds and isn't flagged, by design of the scenario. NEEDS_REVIEW adds work for
  investigators, one case per account without a profile that has one breach. Thresholds are
  judgement, not tuned on data: the rules evaluation metric in `intent.md` is still TBD.

## AI drafts, people decide (M6)

- **Decision:** the model only drafts a narrative for an ALERT case. It never sets an
  outcome, a priority or a disposition, and never sees them. Rules decide the outcome, and the
  investigator decides the disposition. A NEEDS_REVIEW case gets no AI draft at all.
- **Why:** an outcome must be explainable and repeatable from the stored records, which a
  model's output isn't. The draft saves the reviewer typing, and the validator, the model
  label and the audit event make it checkable. Replay, the default, makes every test and demo
  repeatable without a model; a missing recording is counted, never silent.
- **Cost:** the model can't help with the hardest cases, the NEEDS_REVIEW ones. An unavailable
  model, a missing recording or a rejected draft leaves the reviewer a template to finish by
  hand. Recordings must be made again whenever the prompt version, the model or a case's facts
  change.
- **Known limits (M6 part 2 self-review):** a narrative is drafted only when a case opens or
  changes, so a model that comes back later doesn't replace a template, and a crash between
  the run's commit and its narrative events leaves those cases without one; nothing caps the
  reply's length (`num_predict`), so Ollama keeps generating after the client gives up; the
  completeness test checks only that recordings exist, not that they pass the validator; a
  NO_RECORDING reason names the recordings folder, which can be a local path; and the prompt's
  size isn't checked against the context.

## A strict validator and a template, not trust in the model (M6)

- **Decision:** the model's draft is used only if `narrative/validator.py` accepts it;
  otherwise, and always for NEEDS_REVIEW, the reviewer gets a template built from the facts,
  with "no AI draft: write this one manually" and the reasons. The prompt (`narrative-v1`)
  gives the model only this case's facts, as JSON lines in a `<facts>` block with `<` and `>`
  escaped, so a reference can't close the block (`tests/unit/narrative/test_prompt.py`).
- **Why:** each check is a value comparison a reviewer can repeat. Every sentence must cite
  facts in the pack; the IDs it names must be in those facts, its numbers must appear in them
  by value, and its dates exactly. Whole-word decision terms, such as "legitimate", reject it,
  after look-alike and invisible characters are normalised, and so do three words in a row
  from any payment reference. A draft that obeys the golden injection is rejected
  (`tests/unit/narrative/test_validator.py`). A reference never supports a number, because the payer
  wrote it.
- **Cost:** some true sentences fail: a total such as $6,300.00, "09:00" or "2 November 2026",
  a listed word used innocently, such as "closed", and a common phrase that a reference
  happens to hold, such as "this account is" from the golden injection.
- **Known limits, not checked (M6 self-review):** a synonym of a decision word, such as
  "genuine" or "safe"; Cyrillic or other look-alike letters, which NFKC doesn't map; a
  sentence that cites every fact can use any number in the pack; a payment's knowledge time
  can stand in for its event time; spelled-out numbers; whether a number plays the role the
  sentence gives it; and any claim without an ID, number or date, such as "moved the money
  overseas". The reviewer reads the draft beside the pack.
