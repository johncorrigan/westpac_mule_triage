# Operating it

Written at M6 and M7. How to run mule_triage, what it reports, and what to do when it fails.

## Running it

From the repository root, with `.venv` activated:

```bash
python -m mule_triage run tests/golden/payments.jsonl tests/golden/profiles.jsonl \
  --as-of 2026-11-03T00:00:00Z --db out/mule_triage.db --out out/run
python -m mule_triage show ACC-101 --as-of 2026-11-03T00:00:00Z --db out/mule_triage.db
python -m mule_triage dispose ACC-101 --as-of 2026-11-03T00:00:00Z --db out/mule_triage.db \
  --version 1 --reason ESCALATED --reviewer R-1 --narrative DISCARDED
```

`--rules` defaults to `rules/rules-v1.toml`, `--db` to `out/mule_triage.db`, `--out` to `out/run`
and `--ai` to `replay`. `python3 scripts/quickstart.py` runs the checks, then replays the golden
day into an emptied `out/demo/` and shows ACC-101, so a second quickstart shows the same run:
the same summary bytes (`tests/scripts/test_quickstart.py`), and by hand, the same output but
for the audit history's times.

## The run summary and the logs

- **The run summary** goes to stdout, and the same summary, as JSON, to
  `<--out>/run_summary.json`: the run ID, as-of time, rules version, prompt version and AI mode;
  received, accepted, rejected and duplicates for payments and for profiles; each outcome's
  count, the INCOMPLETE alerts and each flagged account; cases created, changed (a reopened
  case counts as changed) and unchanged; the narratives drafted (the model answered), accepted,
  and fallen back to the template by reason, and AI_UNAVAILABLE; and the exit code with its
  meaning. It holds no times or durations, so the same files into a new database give the same
  bytes (`tests/integration/test_run_summary.py`). A run that fails writes no summary.
- **The logs** go to stderr, one JSON object per line, each with its time, level, logger, event
  and run ID: `run_stored` (the ingest, outcome and case counts, once the run commits),
  `model_call` (each live call's outcome and seconds), `narratives_stored`, `reports_written`
  and `run_finished` (the exit code, its meaning and the run's seconds: INFO for 0, WARNING
  for 4, ERROR otherwise, with a null run ID when nothing was stored). They hold counts and
  codes, never an account ID, a transaction ID or a reference (`tests/e2e/test_run_command.py`).
  A run that fails first prints one plain line starting `mule_triage:`, for the person at the
  terminal, so a log shipper keeps only the lines that parse as JSON.
- **Per-case detail,** such as why a narrative fell back, is in the audit table, which `show`
  prints.

## Configuration and secrets

Everything is set on the command line and in the rules file, and checked before any input is
read: a bad setting exits 2 and stores nothing. The tool reads no environment variables. The
slice needs no secrets, because the model runs locally without credentials. In production the
model endpoint's and the database's credentials would come from a vault, be read once into the
settings object at start-up, and never reach a log, a summary or the repository; `verify.py`'s
secrets gate checks the repository only.

## Degrade and roll back

- **Degrade:** an unavailable model never fails a run. Each remaining alert gets the template,
  the run is stored and the exit code is 4. `--ai off` turns drafting off on purpose, and exits 0.
- **Roll back by pinning versions.** Each run stores its rules version and a hash of the
  validated settings, so rolling back the rules means running with the earlier file. The prompt
  version is fixed in the code (`narrative-v1`), so rolling back the prompt means installing the
  earlier release; the summary and every `NARRATIVE` audit event record it, and replay finds a
  recording only under the prompt version and model it was made with. `--model` pins the model,
  and each live draft records its digest. A rules change that alters a case's facts raises its
  version, so a reviewer can't dispose of it on what they saw before.

## The AI modes and what to watch

- **Modes:** `--ai replay`, the default, reads `tests/ai/recorded/`; `--ai off` drafts nothing;
  `--ai live` calls Ollama at `--endpoint`, by default `http://localhost:11434`, for `--model`,
  by default `granite3.3:8b`.
- **Recording:** with Ollama running and the model pulled, from the repository root:

  ```bash
  rm -rf out/record
  python -m mule_triage run tests/golden/payments.jsonl tests/golden/profiles.jsonl \
    --as-of 2026-11-03T00:00:00Z --db out/record/mule_triage.db --out out/record/packs \
    --ai live --record --ai-timeout 300
  ```

  The database must be new, because a narrative is drafted only when a case opens or changes.
  Record again whenever the prompt version, the model or a case's facts change; a test fails
  if the prompt's wording, schema or options change under the same version.
- **What to watch:** `ai_unavailable` above 0 (exit 4); `accepted` falling against `drafted`,
  which points to a model or prompt change; `NO_RECORDING` in replay, which means the recordings
  are out of date; and `model_call` seconds nearing `--ai-timeout`.

## From local to cloud

Not built, and not tested.

| Here | In the cloud |
| --- | --- |
| A person runs it | A scheduler runs it once a day's files land, with the as-of time set by the schedule |
| JSON lines on stderr | Central logs, searched by run ID |
| The exit code and the summary on screen | Alerts on the run summary: a non-zero exit code, `ai_unavailable` above 0, rejected lines above a limit, or no summary by a deadline |
| A SQLite file | A managed database, with the audit table's append-only rule enforced by its grants as well as triggers |
| Ollama on the laptop | A private model endpoint in the same region, its credentials from a vault |

## Failure handling

| Failure | How it shows | What the system does | What the operator does |
| --- | --- | --- | --- |
| An input file is missing or unreadable | Exit 2, `mule_triage: <path>: ...`; `run_finished` with a null run ID | Stores nothing | Fix the path and run again |
| A bad rules file or AI setting | Exit 2, naming the setting | Refuses before reading any input | Fix the file or the flag |
| An invalid line, such as a time without a timezone | `rejected` in the summary; the line and reason in the `rejections` table | Rejects that line and keeps the rest | Check the count. A corrected record needs a new ID: one that differs from the stored record under the same ID is rejected as `changed_since_stored` |
| A duplicate line | `duplicates` in the summary | Stores it once | Nothing, unless the count is unexpected |
| The run doesn't reconcile | Exit 3, `nothing was stored: ...` | Rolls the whole run back | Treat it as a defect and report the message; the database is as it was |
| The database is locked or unreadable | Exit 6, `... nothing was changed ...` | Waits up to 5 seconds, then rolls back the open transaction | Wait for the other writer, or use another `--db`. If `run_stored` was logged, the run is stored despite the message, and its cases have no narrative event |
| The model is unreachable, too slow or not pulled | Exit 4; AI_UNAVAILABLE in the summary; `run_finished` at WARNING | Gives the remaining alerts the template and stores the run | Start Ollama or pull the model. A re-run doesn't redraft: a case gets a new draft only when it changes |
| A draft fails the validator | `REJECTED` in the summary; the reasons in the `NARRATIVE` audit event | Gives that case the template, with the reasons | The reviewer writes the narrative; watch `accepted` against `drafted` |
| No recording, in replay | `NO_RECORDING` in the summary; exit 0 | Gives the template | Record again, as above |
| A pack or the summary can't be written | Exit 2, naming the file; `run_stored` logged | The run is stored; packs may be missing | Fix the folder and run again: the re-run changes no case and rewrites every pack |
| A disposition on an old version | Exit 5, `re-read the case` | Records nothing | Read the case with `show`, then dispose again |
| An unexpected error | Exit 1 and a traceback; `run_finished` at ERROR | Rolls back any open transaction | Report it with the traceback |
