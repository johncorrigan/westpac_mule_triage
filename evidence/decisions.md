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
  Unknown never lowers an alert's priority (the golden test, `tests/unit/test_rules.py`).
- **Cost:** rules this simple can be gamed. ACC-102 stays one sender and 0.05 of a ratio under the
  thresholds and isn't flagged, by design of the scenario. NEEDS_REVIEW adds work for
  investigators, one case per account without a profile that has one breach. Thresholds are
  judgement, not tuned on data: the rules evaluation metric in `intent.md` is still TBD.
