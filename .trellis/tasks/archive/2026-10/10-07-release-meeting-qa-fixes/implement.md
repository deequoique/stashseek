# Implement: release meeting-QA fixes

Implementer: `trellis-implement` sub-agent, model **sonnet**, working ONLY inside
the release worktree. Checker: `trellis-check`.

## Safety

- Never modify, stash, reset or check out anything in the main checkout
  `/Volumes/PeeB/projects/notebook-agent` (the `dev` working tree with WIP). Read
  it only, as the source of the fixes.
- Every pytest / script / Alembic call uses `DATABASE_URL` = the Neon **test**
  branch: assert the host, never print a DSN. The production `.env` must never
  be used for DB access.
- No git commit until the user confirms the commit plan. No push, merge, deploy
  or production migration by the sub-agent at all.

## Steps

1. `git worktree add ../notebook-agent-release -b release/meeting-qa-fixes origin/main`
   (main session does this).
2. In the worktree, run the baseline: full test suite on untouched `origin/main`
   (test-branch DB) and record failures.
3. Port each fix group (PRD table order) by reading the task artifacts plus the
   dev working-tree versions of the files, and applying only the fix hunks to
   main's files. Adapt WIP dependencies (R2) and note every adaptation. After
   each group, run its targeted tests.
4. Copy the new files verbatim where they have no WIP dependency:
   - `evals/meeting_gold/*` new modules and tests;
   - `tests/fixtures/meeting_gold/*`;
   - the migration `d04fdae36884`;
   - new spec and report files.
   Keep `README` / `__main__` edits for evals.
5. Run the full suite in the worktree and compare with step 2. Check
   `alembic heads`.
6. Schema check on an isolated target: a temporary empty database on the Neon
   test branch (`CREATE DATABASE`, dropped afterwards), created by the main session.
   Run `alembic upgrade head` / `current` / `check` from main's head.
7. Production-path 30-case rerun with the harness pointed at the worktree code
   (ROOT = worktree; reuse test-branch items 99–118 via the post-fix
   `ingest.json`). Compare with `05-budget-final-thinking-on-20261007`.
8. Write `research/release-verification.md` (baseline vs release test results,
   adaptations, rerun table) and propose the commit plan (messages + files per
   commit). Stop and report.

## Rollback

Delete the worktree and branch. Nothing outside it changes.
