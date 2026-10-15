# Branch and Code-Authoring Workflow

> Set by the project owner on 2026-10-15. It applies to every development task in this repository.

## Branches

- **Develop everything on `dev`.** Write and verify the change there; only then does it go to `main`.
- **Never develop or commit on `main`.** Every push to `main` starts the production deploy pipeline. After the `Production` environment gate is approved, it deploys to the OVH VPS. See `../backend/deployment-lifecycle.md`.
- **Keep local `main` current with fast-forwards only:**

  ```bash
  git fetch origin main:main
  ```

- **Merge into `main` only when the user explicitly asks**, through a PR or the method the user names.
- **Cut new development branches and worktrees from `dev`, never from `main`.** A `main`-based worktree lacks the commits and files that exist only on `dev`.
- **Commit only the current task's changes.** When the working tree holds unrelated uncommitted work, stage the task's own files or hunks explicitly. Never use `git add -A` or `git commit -a`.

## Code authoring

- **Sonnet sub-agents write the concrete code.** Dispatch `trellis-implement` with `model: sonnet`. Do the same for `trellis-check` when it edits code.
- **The main session handles everything else:**
  - planning (`prd.md`, `design.md`, `implement.md`);
  - splitting the work;
  - reviewing sub-agent diffs;
  - running verification;
  - committing on `dev`.
- **Every dispatch prompt must:**
  - start with `Active task: <task path>`;
  - say that tests must point `DATABASE_URL` at the `.env.neon-test` branch, because a bare `pytest` reaches the production database;
  - say that sub-agents never commit.

## Common mistakes

| Mistake | Correct approach |
| --- | --- |
| A new session's worktree is cut from `main` and lacks `dev`-only code | Cut it from `dev`; commit the needed pending work on `dev` first |
| `git add -A` sweeps another task's uncommitted edits into the commit | Stage only this task's files and hunks |
| Editing `main` directly to ship something faster | Change and verify it on `dev`, then let the user decide when to merge |
