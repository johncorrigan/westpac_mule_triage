# Operating it

First draft, written at M6. How to run the narrative's model and what its failures look like.
The run summary and the log format come at M7.

## The narrative's model

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
- **An unavailable model:** every remaining alert gets the template, the summary counts
  `AI_UNAVAILABLE`, a warning says the run is stored, and the exit code is 4. The reason, such as
  "didn't answer in time" or "refused the connection", is in each case's `NARRATIVE` audit
  event, not on the console.
- **A rejected draft or a missing recording** is counted as `REJECTED` or `NO_RECORDING`, gives
  the template with its reasons, and exits 0.
