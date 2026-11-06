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
