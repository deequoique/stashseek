# 发布：把会议问答修复单独移植到 main 并上线

## Goal

Ship only the completed meeting-QA fixes to production. None of the unrelated
in-progress work in the `dev` working tree may ship with them: web frontend
removal, multimodal/WeMM, extension changes, the media migration
`c9d0e1f2a3b4`, and others.

## Background (confirmed 2026-10-07)

- Production deploys only a full SHA of current `origin/main`
  (`docs/how-to/deploy-production.md` §2). Migrations run as a one-shot unit
  via the direct URL (`upgrade head` / `current` / `check`).
- `origin/main` and `origin/dev` have identical `app/` and `migrations/`.
  Their committed differences are tests, evals and Trellis files only. The
  release branch therefore starts from `origin/main`.
- The `dev` working tree has 267 changed paths (70 deleted, 106 modified, 91
  untracked). Pre-existing WIP shares files with the fixes: `app/config.py`,
  `app/models.py`, `app/ingest/embed.py`, `app/agent/{orchestrator,runtime_state,types,answer_pipeline,agent_builder}.py`,
  `app/agent/agent_tools/policy.py`, `.env.example`,
  `docs/reference/configuration.md`, and several test files. Some fix code is
  written against WIP structures (e.g. media-citation handling in answer
  normalization).
- Fixes to ship, as defined by their task artifacts:

  | Task | Location | Fix |
  |---|---|---|
  | `10-06-fix-chunk-merge` | archived | semantic-first chunking, budget guard, excerpt cap 1200 |
  | `10-06-fix-answer-fail-closed` | archived | whitespace stream guard, citation normalization, `types.py` / `response.py` relaxations, `citation_normalized` stage |
  | `10-06-fix-embed-transient-errors` | archived | classified per-batch retry |
  | `10-06-rq1-retrieval-quality` | archived | `evals/meeting_gold` RQ1 + agent-smoke harness, tests, fixture |
  | `10-06-tenant-vector-filter-first` | active | `filter_first_vector_rank`, `vector_search` rewrite, migration `d04fdae36884` (down_revision `b8c9d0e1f2a3`, the main head), model HNSW declaration removed (segment only — the media model does not exist on main) |
  | `10-07-retrieval-agent-budget` | active | batch retrieval within budgets, post-search 「检索完成」 prompt, `stage_usage` diagnostics, thinking unchanged, `AGENT_OUTPUT_TOKEN_LIMIT` default 3000 |

  Also: specs (`ingestion-chunking-embedding.md` new; `agent-retrieval-convergence.md`,
  `database-guidelines.md`, `browser-companion-capture.md`, spec `index.md`),
  `docs/explanation/ingestion-and-retrieval.md`, the configuration doc line, the
  `.env.example` line, and `evals/meeting_gold/reports/2026-10-specific30-quality.md`.

## Requirements

- R1 Create an isolated git worktree on a new branch `release/meeting-qa-fixes`
  from `origin/main`. The `dev` working tree is never modified, stashed or
  reset.
- R2 Port only the fixes listed above, applied to main's versions of each
  file. Where fix code depends on WIP-only structures (media citations,
  multimodal deps, WeMM config), adapt it to main's code without importing the
  WIP. Each adaptation is recorded in the commit message and the release notes.
- R3 Commits are grouped by fix, in the order of the table above, with specs,
  docs and tests in the same commit as their code. A final commit adds the
  Trellis task records and the report. The commit plan is shown to the user
  for one-shot confirmation before any commit.
- R4 Verification on the release worktree, always with `DATABASE_URL` set to the
  Neon test branch:
  - full test suite: no new failures vs a baseline run of untouched
    `origin/main` in the same environment;
  - exactly one Alembic head (`d04fdae36884`);
  - on a temporary empty database on the Neon test branch (from base, through
    main's chain to `d04fdae36884`): `alembic upgrade head` / `current` / `check` succeed;
  - production-path 30-case rerun against the release code (~250 LLM calls)
    within noise of the final dev run: 30/30 answered, comparable pool gold
    recall and latency.
- R5 Ship: push the branch, merge to `main` (via PR), push, then deploy the
  `origin/main` SHA with `deploy <sha>`. **Each outward step (push, merge,
  deploy, production migration) requires explicit user confirmation at that
  moment.**
- R6 Post-deploy checks:
  - production read-only probe: the largest tenant gets 50/50 vector
    candidates; `ix_segment_embedding_hnsw` absent; Alembic at `d04fdae36884`;
  - service readiness per the deploy doc.

## Acceptance Criteria

- [ ] The release branch contains only fix-related changes. `git diff
      origin/main..release/meeting-qa-fixes --stat` lists no WIP-only paths.
- [ ] R4 verification passes, with results recorded in this task's `research/`.
- [ ] The user has confirmed the commit plan and each outward step.
- [ ] Production is deployed and R6 checks pass.

## Out of Scope

- Committing or shipping any WIP.
- Merging `main` back into `dev`. This is a later step for whoever owns the WIP;
  the release notes flag the expected conflicts.
- Diversification/OR/RRF retrieval changes.
