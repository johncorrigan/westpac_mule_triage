# Agentic workflow

How mule_triage was built with AI agents: which tools did what, how the work was split, the
prompts that steered it, where I stepped in, and how I checked what came back. Every review
finding is recorded at the end, with my decision.

**In short.** AI agents wrote almost all of this repository: the plan, the code, the tests, the
scripts and the drafts of these documents. Before any code, I set the rules they work under and
the hooks that enforce them. I steered each milestone with a written prompt, which a planning
chat checked against the code first. I owned the expected outcomes and the model recordings, read
every diff and decided every commit. Of the 25 commits up to M8's tally, 17 carry an
`Assisted-by: Cursor` trailer, and one more says so in its body (the tally is at the end).

## The AI tools, and what each did

| Tool and model | Role | What it did |
| --- | --- | --- |
| **Cursor agent, Claude Opus 5.5:** the builder | Implementation | Wrote `PLAN.md` from my milestone list, then the code, tests, scripts and the drafts of `evidence/`, one milestone per chat. Ran `verify.py`, argued against its own diff, and from M1 to M6 reviewed its own work adversarially when I asked. Proposed each milestone's commit message. At M8 it checked every claim in the documents, tallied the commits and the guard log, and rewrote the documents for submission |
| **A planning chat in Cursor, Claude Opus 5.5:** separate from the builder | Planning and prompt review | From M3 on, reviewed each milestone's prompt against the code before I pasted it. Drafted the golden README and answer key from rules I agreed, checked the golden data the builder generated, set out the steps for recording with Ollama, and wrote the prompt for S, the subpackage refactor. I decided which of its changes to use |
| **A review chat in Cursor, Claude Opus 5.5:** `/adversarial-review` | Independent review | At R, reviewed the whole repository read-only and reported eight findings (below). Then turned my answers into the `/respond-to-review` brief I gave the builder |
| **Two further Cursor chats** | Cleaning up the plan | One explained the two TBD success criteria in `intent.md` and the options for each, and made the cuts I chose. The other reworded those cuts in the documents' style |
| **Ollama, `granite3.3:8b`** | Inside the product | Drafts each alert's narrative, and nothing else. I ran it and recorded its two golden replies myself; replay mode reads them |
| **Ollama, `llama3.1:8b`** | A comparison at M6 | I tried it when both granite drafts failed the validator. One of its drafts passed, but held factual errors, so I kept granite's real replies |

None of these is the control around them. That's the hooks in `.cursor/hooks.json`, which
run `.cursor/hooks/guard.py` on every shell command, tool call and file read (below).

## How the work was split

- **A plan before any code.** The builder wrote [`PLAN.md`](../PLAN.md) from my milestone list,
  one line per milestone with its time budget and the check that proves it done, then the
  optional items, the cut order and a "Cuts" section. Every change of course went into the plan
  first, with its reason.
- **One milestone per chat,** so the repository carries the memory, not the chat.
  [`AGENTS.md`](../AGENTS.md) tells each new chat to read `PLAN.md` and run `git status` first.
  The larger milestones went in parts: M1 and M2 in two, M5 as M5a and M5b, and M6 in three.
- **Steps only I could do,** marked "Needs from me" in the plan: approving the dev-tool pins,
  writing the answer key, recording the model and running the review.
- **The same close to every prompt,** then a quick adversarial self-review, then my decision on
  each finding (the tables below).

Apart from the planning chat, the build ran over 16 chats in Cursor, in this order:

| Chat | Work |
| --- | --- |
| Before the build | A commit command for the set-up commits |
| The plan and M1 | `PLAN.md` from list r11, then r12; the skeleton, `verify.py` and `quickstart.py`; the architecture tests; the first evidence drafts |
| M2 | The golden data, generated from my README; the contracts; the acceptance test, marked as an expected failure |
| M4, first start | Read the code and was set aside, because M3 hadn't run |
| M3 | The store, ingest and reconciliation |
| M4 | Signals, rules v1 and outcomes |
| M5a and M5b | The evidence pack, reports and facts hash; then the audit table, dispositions, `show` and `dispose` |
| M6, parts 1 to 3 | The prompt, validator and template; the model clients and degrade mode; the recordings and the live evaluation |
| M7, first start | Read the code and was set aside, so S could run first |
| S | The subpackages, with no logic change |
| M7 | The run summary, JSON logs, smoke test, `operate.md` and the deliberate bugs |
| Two chats on the TBDs | The two evaluation metrics, cut at my choice |
| R, the review | `/adversarial-review`, read-only |
| R, the fix | `/respond-to-review`: finding R1 |
| M8 | The documents; then P, the secrets gate without Git |

## Representative prompts

Verbatim excerpts, trimmed where marked.

**Setting the terms, at the start of the build:**

> We're starting a timed build: three hours of build time, in sessions with breaks. You
> implement; I steer, review every diff and decide every commit. How we work is in AGENTS.md and
> .cursor/rules/. Read them now, and follow them throughout. […] Your first task: write PLAN.md
> at the root from the milestone list below. […] Write no code. Stop when it's written, so I can
> review it.

**The close of every milestone prompt:**

> When it's done: run python scripts/verify.py and show me its table, then tick M2 in PLAN.md.
> Stop and tell me, in plain language, what changed and why, what could go wrong (above all, the
> most likely bug in this diff and the test that catches it), and anything you assumed or weren't
> sure about. Then propose a commit message ending with the Assisted-by trailer from AGENTS.md.
> Don't commit until I say so.

followed, once the report was in, by:

> Please perform a very quick adversarial review of your work here looking for gaps,
> improvements, etc.

**Keeping the oracle mine (M2):**

> Don't write anything under tests/golden/expected/: that's mine. Stop after the summary, with
> no commit. […] If anything in my answer key looks wrong or ambiguous, stop and tell me. Never
> change it.

**Writing safety into the specification (M3):**

> Each rejection has its line number, the record ID if json.loads can find one, and a reason built
> from the field and the error type, such as "event_time: timezone_aware", never from pydantic's
> message, which quotes the rejected value and could hold a name.

**Never bending the answer to fit (M4):**

> The golden acceptance tests must now pass […]. If they don't, show me which accounts differ and
> why: fix code that doesn't follow the README, but never change the README, the rules file, the
> data or the answer key to fit.

**The AI boundary (M6):**

> The model drafts; people decide. […] A reply passes only if it's well-formed JSON within the
> limits; every sentence cites at least one fact in this case's pack; every number, amount and
> date in a sentence appears in the facts it cites […]; and there's no decision or recommendation
> language […].

> An unreachable or slow model means template narratives, counted as AI_UNAVAILABLE, and exit code
> 4. AI never blocks the pipeline, and never disappears silently. […] Don't call Ollama yourself.

**Reporting a result rather than fixing it (M6, part 3):**

> If a recorded reply fails the validator, leave it alone: a rejected draft with its template
> fallback is a result to report, not something to fix.

**Proposing before restructuring (S):**

> First, propose and stop. […] If a different shape is better, or the refactor isn't worth its
> risk now, say so. […] move each file with git mv and change nothing in it but its imports, so
> the diff shows renames.

**Treating a review as claims, not instructions (R):**

> Check the claim against the code before fixing it. […] Scope fits R's budget, so no PLAN.md
> change. Fix these two things only […]. Out of scope, so record these as open parts of R1 […].
> Deferred, decided by me: dispose should refuse --narrative ACCEPTED or EDITED when the case's
> current version has no AI draft […].

## Where I intervened

- **The wrong plan.** I'd given the builder an outdated larger milestone list, rather than a smaller list
  cut back from it. It rebuilt `PLAN.md`, carrying over the fixes from its review of the larger list. I then 
  added a cut order that, in my words to the builder, came from "another agent that helped build the milestones".
- **No CI in the plan.** "There will be NO CI during this build": it's a cut, and
  `verify.md` says where each check would run.
- **Python 3.11 as the floor,** with users assumed on 3.13 or newer, and `StrEnum` rather than
  the `Enum` the builder had chosen for older Pythons.
- **The answer key.** I wrote it, from a planning-chat draft I checked, and committed it myself.
  When the builder said M2 couldn't be ticked without each alert's priority, I added them and
  committed them myself.
- **One case per account and exact as-of time.** I proposed it at M4, because the signals depend
  on the exact time; it became the M4 self-review's R5.
- **Splitting M5** into M5a and M5b, with budgets I set and committed myself.
- **Recording the model.** My first live run gave AI_UNAVAILABLE. Not allowed to call Ollama, the
  builder worked from the code to the likely cause: the 60-second timeout, on a first call that
  reloads the model with an 8,192-token context. I recorded again with `--ai-timeout 300`; the
  calls took 69.57 and 52.94 seconds.
- **Not editing the recordings.** Both drafts failed the validator. The builder advised against
  editing them by hand, because a recording claims what the model said. I tried a `llama` model
  for comparison, kept granite's real replies, rejected as they are, and planned a better prompt
  as optional item N.
- **Two metrics cut.** Both TBD success criteria, the rules evaluation and the AI narrative
  evaluation, went under "Cuts" in `PLAN.md`, with my reasons.
- **R1's scope.** I chose to fix storing and showing the narrative within R's budget, record the
  rest as open, and defer tightening `dispose`.
- **The documents.** At M8 I had the builder check every claim against the code, the tests and a
  fresh `verify.py` run, then rewrite the README and evidence for submission.
- **A ZIP download, not only a clone.** The secrets gate listed its files with `git ls-files`,
  so a ZIP download failed it, and quickstart stopped there. I asked for the project to run
  without Git, as item P in `PLAN.md`. The gate still uses Git when it can; otherwise it walks
  the folder and applies the root `.gitignore` itself, so nothing is skipped. Three tests failed
  first; in this checkout both ways list the same 99 files; and `verify.py` passed in a copy
  with no `.git`.

## How I reviewed the output

- **Rules before code.** [`AGENTS.md`](../AGENTS.md) sets the roles, the plan discipline, the
  steps of every milestone and the hard boundaries. `.cursor/rules/python.mdc` and `tests.mdc`
  set the engineering and test conventions.
- **Hooks that enforce what a script can check.** The guard blocks any change to the answer key,
  the recordings, the kit or Git's own files, any read of a `.env` file, pushing, rewriting
  history, branch, tag and configuration changes, `sudo`, and tools other than plain Python and
  pip. It asks me before any install or network call. On a commit, it requires an `Assisted-by`
  trailer, runs `verify.py --fast`, and refuses while the answer key or recordings have
  uncommitted changes. It reads text, so it can't see through variables or code that builds a
  path; the golden checksum and my own commits cover those routes.
- **An oracle the agents can't touch.** The golden answer key is mine, checksummed by a gate that
  fails if it, the golden README or either data file changes. The recordings are mine too.
- **One definition of done.** Every milestone ended with `python scripts/verify.py` green. No gate
  was loosened: the coverage floor, `mypy --strict` and black's target are as M1 set them, and no
  test is marked skip or expected-failure.
- **A report, then a review, then my decision.** Each milestone ended with the gate table, the
  builder's own "most likely bug and the test that catches it", and, from M1 to M6, a quick
  adversarial self-review. I read the diff and chose which findings to fix; each fix started with
  a failing test.
- **Tests that are shown to fail.** At M7, two deliberate bugs each turned `verify.py` red
  ([`verify.md`](verify.md)).
- **A second pair of eyes.** At R, a separate chat reviewed the whole repository adversarially,
  and the builder checked each finding against the code before fixing it.
- **A second reader for every prompt.** From M3 on, the planning chat checked each prompt against
  the code before I sent it.

## Reviews

Each table gives the finding, my decision and the fix. "Failed first" means a test that showed
the problem was written and seen to fail before the fix.

### Plan review (before M1, by the building agent)

The agent reviewed its own draft of `PLAN.md`, against `AGENTS.md` and the brief. I accepted
fixes that added the missing checks (the evidence pack, one transaction per run, the gate list,
scenario coverage), tied each cut to the check it changes, and added "needs from me" notes. I
then switched the plan from milestone list r11 to r12, and retired CI.

### M1 part 1 review (by the building agent)

| ID | Finding | Decision | Fix |
| --- | --- | --- | --- |
| R1 | The secrets check missed credentials written as JSON keys | Fix | The pattern allows a quote after the key; a JSON-key test failed first |
| R2 | A failing black gate didn't name the file | Fix | Dropped `--quiet` |
| R3 | Nothing tested `verify.py`'s own checks | Fix | The secrets check takes the folder to scan; seven tests |
| R4 | Nothing stopped a local database being committed | Fix | `*.db`, `*.sqlite` and `*.sqlite3` ignored |
| R5 | Only direct dependencies were checked, and not where the package loads from | Fix | Every pin is compared; the package must load from this `src/`; two tests failed first |
| R6 | The README claimed features that don't exist yet | Fix | Worded as intent |
| R7 | Nothing runs the guard's own tests without CI | Noted | None: the kit is mine. I run them by hand (see the tally) |
| R8 | The golden checksum skips if the answer key folder is missing | Noted | Closed at M2, when the gate became required |

### M1 part 2 review (by the building agent)

| ID | Finding | Decision | Fix |
| --- | --- | --- | --- |
| R1 | The matching-key fixture hashed bytes but wrote text, so it would fail on Windows | Fix | Fixture files written as bytes; not run on Windows |
| R2 | The wall-clock rule missed `time.time_ns()` and `datetime.now` passed by name | Fix | Every reference is checked; both cases failed first |
| R3 | SQL built in a variable, then passed to `execute()`, got through | Fix | Built text with an SQL keyword is flagged anywhere; failed first |
| R4 | `os.system()` and `os.popen()` weren't flagged | Fix | Added to the unsafe calls; both failed first |
| R5 | `intent.md` overstated what the hooks stop | Fix | Reworded |
| R6 | `verify.md` didn't list the rules' blind spots | Fix | Listed |
| R7 | The `__main__.py` check accepted any second line | Fix | Exact match; failed first |
| R8 | Nothing enforced "standard library only" for `scripts/` | Fix | New test; failed first |

### M2 (by me, with the planning chat)

The planning chat drafted the golden README and answer key from rules I agreed. I checked every
case, and committed both by hand, then the alert priorities.

### M2 review (by the building agent)

| ID | Finding | Decision | Fix |
| --- | --- | --- | --- |
| R1 | The checksum gate didn't require the README or data files to be listed, so deleting a line from `SHA256SUMS` removed their protection | Fix | Every file in `tests/golden/` except Python code must be listed; two cases failed first |
| R2 | Nothing checksums `tests/golden/test_golden.py`, so its comparison could be weakened unnoticed | Open: mine to decide | None |
| R3 | The as-of time is fixed in the test, not in the answer key | Open: mine to decide | None |
| R4 | The strict contracts reject every valid line if ingest parses it into a dictionary first | Fix | The rule is in `design.md`; the strict-mode tests already fail if strict mode is loosened |
| R5 | The contracts accept empty IDs, self-payments, a knowledge time before the event time, and amounts beyond SQLite's integer | Open: suggested for M3 | Closed in part at M3 (the amount) and M4 (empty account IDs) |
| R6 | `RunResult` keeps counts and IDs apart, so they can disagree; a rejected line has no line number or reason | Open: suggested for M3 | Each rejection got its line number and reason at M3 |
| R7 | An `Alert` can be incomplete without `PROFILE_MISSING`, and `Unknown` accepts any reason code | Open: suggested for M4 | None |
| R8 | The scenario has no MEDIUM alert, no late or out-of-window record, and no Conflict | Open: mine to decide | Recorded under "Not covered" in `intent.md` |
| R9 | `Status.SKIP` was dead code; a linked `expected/` would move the checksum's root; `Conflict` can hold one value; Python 3.11 hasn't run the slotted generic dataclasses | Fix, for `SKIP` only | `SKIP` removed; the rest noted |

### M3 self-review (by the building agent)

| ID | Finding | Decision | Fix |
| --- | --- | --- | --- |
| R1 | A line holding a number of more than 4,300 digits made `json.loads` raise `ValueError`, which crashed the run instead of rejecting the line | Fix | `ValueError` is caught; the test failed first |
| R2 | An amount of 2^63 cents or more passed ingest, then crashed the store | Fix | `amount_cents` is capped at 2^63 - 1, SQLite's largest integer; the contract and store tests failed first. Closes that part of M2's R5 |
| R3 | Reconciliation compares totals, so a lost line and a line recorded twice cancel out | Open | None |
| R4 | The dropping fake loses a record before the real store sees it, and the part-way failure comes after the whole save | Open | None |
| R5 | `ROLLBACK` after SQLite has rolled back by itself would hide the real error (not verified) | Open | None |
| R6 | On a re-run every record is a duplicate, so signals built from the run's accepted records would clear every account | Fix | Noted in `PLAN.md`, M4 |
| R7 | The run ledger doesn't record which files a run read | Open | None |
| R8 | `decisions.md` said primary keys make a re-run add nothing; the store's look-up does | Fix | Reworded |
| R9 | A corrected profile is rejected for ever; no schema version; `run` ignores `settings.database` when given a store; one ingest line uncovered | Open | The schema version came at M5a |

### M4 self-review (by the building agent)

| ID | Finding | Decision | Fix |
| --- | --- | --- | --- |
| R1 | The rules version didn't fix the thresholds: a file with `min_senders = 50` still loaded as `v1`, so `decisions.md` overclaimed replay | Fix | Each run stores a SHA-256 of the validated settings; the test failed first; claim reworded |
| R2 | `alert_at = 4` loaded, so with three signals nothing could alert and the run still succeeded | Fix | Counts above the number of signals are refused with exit code 2; two cases failed first |
| R3 | An account ID holding a newline or control character was accepted, and the summary prints flagged IDs as they are | Fix | Account IDs must be up to 64 letters, digits or `_ . : -`; 18 cases failed first. Closes the empty-ID part of M2's R5 |
| R4 | A missing input file crashed with a traceback and exit code 1 | Fix | A one-line message and exit code 2; the end-to-end test failed first |
| R5 | A re-run adds a second case per account, each at version 1 | Fix, as I proposed: keyed by the exact as-of time rather than the day, because the signals depend on it | A case per account and as-of time; a changed result raises the version and records the run, and a drop to NO_ALERT keeps the row; six integration tests failed first. Facts hash, reopening and audit events left for M5 |
| R6 | The re-run test would pass if both runs returned nothing | Fix | It also checks the flagged accounts |
| R7 | Money out before money in counts as pass-through, and an account paying itself counts as its own sender and pass-through; the README doesn't decide either | Open: mine to decide | The negative-span part closed at M5a |
| R8 | No test for the rule that only an ALERT row in `cases` has a priority; `--out` is accepted but unused; `min_ratio = 1` gives the error `is_instance_of`; each run loads every stored payment; `design.md` still says the pipeline raises `NotImplementedError` | Open | `--out` is used from M5a; `design.md` reworded at M8 |

### M5a self-review (by the building agent)

| ID | Finding | Decision | Fix |
| --- | --- | --- | --- |
| R1 | Money out before any came in breached pass-through, because a negative span counts as "at most 12 hours" | Fix | The span must be 0 or more, and the pack shows "from 0 to 12"; the test failed first; no golden outcome changed. Closes that part of M4's R7 |
| R2 | The facts hash covered labels and rounded values, so rewording a label would raise every case's version, stale every disposition and, at M6, break every recording | Fix | The hash covers exact values and codes only; two tests failed first |
| R3 | References are copied in full, and free text may hold a name | Keep in full, as quoted data | The risk is noted in `design.md` |
| R4 | Rounding a ratio down misleads when the threshold has more than two decimal places | Open | None |
| R5 | The threshold wording in `evidence.py` is separate from the comparisons in `detection/signals.py`, and no test ties them together | Open | None |
| R6 | Pack file names collide for account IDs that differ only in case (macOS, Windows), `:` is invalid on Windows, and as-of times within one second share a name | Open | None |
| R7 | Packs are written after the commit: a failed write exits 2 without saying the run was stored, and two concurrent runs can leave a file behind the database | Open | None |
| R8 | Refusing an old database switches it to WAL first | Open | None |
| R9 | No test ties the hash stored on the case to the pack file's `facts_sha256` | Open | None |
| R10 | Untested: a payment to itself, and an empty ID, which renders as literal backticks | Open | None |

### M5b self-review (by the building agent)

| ID | Finding | Decision | Fix |
| --- | --- | --- | --- |
| R1 | `show` rebuilt the pack with the rules it was given, and warned only if the facts differed, so a different `alert_at` showed another outcome than the stored case with no warning | Fix | The view must match the stored outcome, priority, INCOMPLETE flag, reasons and facts hash; the test failed first |
| R2 | A closed case can be disposed of again at its current version, which records a second disposition and raises the version | Open: mine to decide | None |
| R3 | Anyone who can write to the database file can drop the triggers | Open | Noted in `design.md` |
| R4 | Two reviewers are tested one after the other, not concurrently (that's T) | Open | T wasn't built |

### M5b review (by the building agent, at my request)

| ID | Finding | Decision | Fix |
| --- | --- | --- | --- |
| R1 | `design.md` said the console output is only IDs, codes and counts, but `show` prints whole packs, references included | Fix | Reworded |
| R2 | No test checked that a change to an open case is audited as `CASE_CHANGED`, so labelling every change as reopened would pass | Fix | New test; it failed when the label was broken on purpose |
| R3 | A locked or unreadable database crashed every command with a traceback and exit code 1 | Fix | A one-line message saying nothing was changed, and exit code 6; three cases failed first |
| R4 | A disposition's audit record didn't name the facts it was made on | Fix | Its payload holds the case's facts hash; the test failed first |
| R5 | `--version` accepts 0 or a negative number | Open | None |
| R6 | Pack files show the version they were written at, and no status, so they go stale after a disposition | Open | None |
| R7 | `show` exits 0 even when its pack differs from the stored case; no test for a disposition that fails part-way; `StoredCase.outcome` isn't typed; `dispose` prints `+00:00` where the packs print `Z` | Open | None |
| R8 | The reviewer ID is whatever the user types, and nothing records who viewed a case | Open: out of scope in `intent.md` | None |

Found at M5a before the review, and fixed: a database made before M5a crashed the run on the
missing `facts_sha256` column, so the schema now has a version and an older database is refused;
and the end-to-end test wrote packs into the repository's `out/`.

### M6 part 1 review (by the building agent, at my request)

Ten hostile drafts, run in memory against ACC-101's golden pack, all passed the validator.

| ID | Finding | Decision | Fix |
| --- | --- | --- | --- |
| R1 | A synonym obeys the injection: "appears genuine and safe" passes | Open | Recorded as a known limit in `decisions.md` |
| R2 | A soft hyphen, a zero-width space, full-width letters or an underscore hide a decision word | Fix | Text is normalised (NFKC, invisible characters removed) and `_` is a break for the word check; four cases failed first |
| R3 | A sentence can name an account or transaction its cited facts don't show, such as the wrong payer | Fix | New reason `UNSUPPORTED_ID`; the case's own account is always allowed; two cases failed first |
| R4 | A sentence can repeat a payment reference, such as the injected instruction | Fix | New reason `REPEATS_REFERENCE`, for three words in a row from any reference; two cases failed first |
| R5 | Citing every fact lets a sentence use any number in the pack | Open | Recorded as a known limit |
| R6 | A payment's knowledge time can stand in for its event time | Open | Recorded as a known limit |
| R7 | A claim without an ID, number or date can't be checked | Open | Recorded as a known limit |
| R8 | No cap on prompt size, so a small model's context could drop the instructions; no cap on the reply's size; accepted text isn't yet rendered as untrusted | Open: for M6 parts 2 and 3 | The reply is capped at 1 MB in part 2, and `show` renders the narrative as inline code from R |

Found before the review, and fixed: an account ID of digits alone, such as `950`, was taken out
of a sentence as an ID, so it could hide an invented $950; the test failed first.

### M6 part 2 review (by the building agent, at my request)

| ID | Finding | Decision | Fix |
| --- | --- | --- | --- |
| R1 | Rewording the prompt, or changing its schema or options, under the same version would replay old replies as if they answered it; a probe confirmed the key didn't change | Fix | A test pins a fingerprint of the instructions, schema and options to `narrative-v1`; it failed first, and changing only the seed fails it |
| R2 | An unavailable model's reason held the error's text, which can quote the server's reply, so injected text could reach the reviewer and the audit; a probe confirmed it | Fix | A fixed message for each kind of failure; two cases failed first |
| R3 | One reply that wasn't Ollama's shape, or was over 1 MB, made the model unavailable for the rest of the run and exited 4 | Fix | Such a reply is REJECTED for its case alone; two cases failed first |
| R4 | Replay through the command line wasn't tested, since the end-to-end test uses `--ai off` | Fix | New test; it fails when the replay client's arguments are swapped |
| R5 | A re-run never re-drafts, and a crash between the two transactions leaves a case without a narrative | Open | Recorded as a known limit in `decisions.md` |
| R6 | No cap on the reply's length, so Ollama keeps generating after a timeout | Open | Recorded as a known limit |
| R7 | The completeness test checks only that recordings exist | Open | Closed in part 3: `tests/golden/test_recordings.py` pins each recording's verdict |
| R8 | A NO_RECORDING reason names the recordings folder, which can be a local path; the prompt's size is unchecked; the endpoint may be remote plain HTTP | Open | Recorded as known limits, and the endpoint as an assumption |

Found while fixing, and fixed: the stub server's half-second shutdown poll made the client tests
take about 4.5 seconds; they now take under one.

### R: the adversarial review (Claude Opus 5.5, in a separate chat)

Run with `/adversarial-review` over `src/`, `tests/`, `scripts/`, `rules/`, the README and
`evidence/`, read-only, by the same model family as the builder. It reported eight findings, each
with where, what's wrong, how to show it and the test that would catch it, and confirmed R2 and
R3 with probes in memory. The builder checked R1 against the code before fixing it: a golden replay,
then `show ACC-105`, printed no note and no sentence, and the NARRATIVE payload held
`"reply":null`. At M8, R2, R3 and R8 were confirmed again in memory.

| ID | Finding | Decision | Fix |
| --- | --- | --- | --- |
| R1 (high) | The reviewer never reads a narrative. The template's note and sentences, and an accepted draft's normalised sentences, were built but never stored or shown; the NARRATIVE event kept only the raw reply, against `validator.normalised`'s docstring; the packs and `show` had no narrative section; so `intent.md`'s "checked first-draft narrative" wasn't backed | Agreed. Fix storing and showing it, within R's budget, so no `PLAN.md` change | The NARRATIVE payload also holds `note` and `sentences`: an accepted draft's normalised text, or the template's. No schema change, and nothing new in the facts hash, so the recordings still match. `show` has a `## Narrative` section from the latest NARRATIVE event at the case's current version, naming its source (model and digest, or the template's note and reasons), with all its text as inline code; with no event at that version it says so. Five tests failed first; a sixth, added at self-review, shows an event written before this fix gives no narrative rather than an error |
| R1, open part | The pack files under `--out` have no narrative, and on a re-run an unchanged case would need it read from its stored event | Open: out of R's scope | None |
| R1, open part | `intent.md` claims every alert has a checked first-draft narrative | Fix at M8 | Reworded: an alert gets a checked draft or the template, and the golden alerts get the template |
| R1, open part | `dispose` accepts `--narrative ACCEPTED` or `EDITED` when the case's current version has no AI draft, and doesn't record which NARRATIVE event the reviewer saw | Deferred: it should refuse those two values in that case, and record the event | None |
| R2 (medium) | The validator accepts an account or transaction ID that isn't in the pack when its digits match a cited number or it has none, such as "from ACC-900" citing a $900.00 payment | Open | Recorded as a known limit in `decisions.md` and the README |
| R3 (medium) | The decision-word list misses other forms of its own words, such as "freezing" and "escalating", and accusing words such as "scam" and "laundering" | Open | Recorded as a known limit in `decisions.md` and the README |
| R4 (low to medium) | Renaming the rules version alone changes every facts hash, reopening every case and losing every recording; changing `max_counterparties` changes the pack but no version | Open | None |
| R5 (low) | Pass-through counts money sent out before any came in, so an account that passed on $1 can breach; the README allows either reading | Open: mine to decide | None |
| R6 (low) | Pack file names collide on a case-insensitive file system, as on this Mac (M5a's R6) | Open | None |
| R7 (low) | `README.md`, `design.md` and `intent.md` made claims the code contradicts | Fix at M8 | Every claim checked against the code, the tests and `verify.py`, and the documents rewritten |
| R8 (low) | A blank line, such as an extra newline at the end of a file, counts as a rejected record | Open | None |

## Tally (M8)

Counted from `git log`, and from `.git/cursor-guard/controls.log`, which Git doesn't track.

**Commits:** 25 up to and including the commit of this tally. 17 carry an `Assisted-by:`
trailer that Git reads: 16 say `Cursor (Claude Opus 5.5)`, and the commit of R's fix says only
`Cursor`. 8 have none: the three set-up commits before the plan, the golden answer key and its
priorities, the M5 plan split, the recorded replies, and the subpackage move, S, whose body has
"Me and Assisted-by: Cursor (Claude Opus 5.5)" inside a sentence, so Git doesn't read it as a
trailer.

**Guard log:** seven entries, four approval requests and three blocks. None was an attempt to
change a protected file.

| Rule | Decision | Count | What it was |
| --- | --- | --- | --- |
| `install` | ask | 3 | At M1, the agent's first install, unpinned so `pip freeze` could write `constraints.txt`, then the README's four steps run by hand; at M8, an `rg` search of a past chat for that install command. The log keeps the request, not my answer; all three ran |
| `network` | ask | 1 | At M8, a `git clone` of this repository into a temporary folder, to test the README's commands from a clean checkout. It copied local files and fetched nothing |
| `recordings` | deny | 2 | At M6, a read-only Python check of the recorded replies; at M8, a script reading a past chat whose text held the same path. The guard treats any `python` command that names the path as a possible write. Neither agent went round it |
| `kit` | deny | 1 | At M8, `sed -n` printing lines of `.cursor/hooks/guard.py`; the guard treats `sed` as a possible writer |

No other rule fired: no commit was refused for its trailer, its checks or uncommitted
human-owned files.

**The guard's own tests:** `python3 .cursor/hooks/guard_tests.py`, run by me at M8, because the
guard stops the agent running them: `CASES: 113  FAILURES: 0`.
