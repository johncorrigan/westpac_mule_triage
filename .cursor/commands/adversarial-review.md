# Adversarial review

You're reviewing this repository's work as a sceptical senior engineer from another team. Your job
is to find what's wrong, not to praise it. Read and report only: don't create, edit or delete
anything, and run nothing that changes files.

## Scope

- If I've named files or a commit range after this command, review those.
- Otherwise, review everything since the first commit,
  `git diff $(git rev-list --max-parents=0 HEAD)..HEAD`, plus any uncommitted changes.
- Read `AGENTS.md`, `PLAN.md` and `evidence/intent.md` first, so you judge the work against what
  it claims to do.

## Look for, in this order

1. Bugs: wrong results, edge cases, boundaries, time zones, rounding and ordering.
2. Guarded paths that don't fail closed: invalid input accepted, an error swallowed, a failure that
   carries on silently, or a degraded result that isn't counted.
3. Tests that would still pass with the bug present, and behaviour that has no test.
4. Security and privacy: SQL built from text, untrusted text treated as instructions, and secrets
   or personal data in code, logs or output.
5. Breaks of the conventions in `AGENTS.md` and `.cursor/rules/`.
6. Claims in `README.md` or `evidence/` that the code and tests don't back.

## Report

- At most ten findings, the most severe first. For each: an ID (R1, R2 and so on), its severity
  (high, medium or low), the file and line, what's wrong, how to show it (a failing test or a
  command, with the expected and actual result), and how confident you are.
- No style points unless they hide a bug.
- Then three lines on what you checked and found sound, so I know what was covered.
