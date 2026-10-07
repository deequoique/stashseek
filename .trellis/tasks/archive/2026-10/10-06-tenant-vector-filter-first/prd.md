# 修复租户内向量检索截断（segment 与媒体向量）

## Goal

Vector search must return the requested number of nearest neighbours from the
caller's own data, whatever the size of other tenants or the total table. The
tenant filter is applied **before** ranking, never after an approximate global
scan. The global HNSW structure that causes the truncation is removed.

## Background (confirmed 2026-10-06)

### The defect

- `segment.embedding` has a global HNSW index `ix_segment_embedding_hnsw`
  (m=16, ef_construction=64).
  - It was created in the bootstrap migration
    `migrations/versions/6df2e721d7b2_init_schema.py` (commit `b544400`,
    2026-08-04) and is also declared in the ORM at `app/models.py:1052`.
  - It was chosen over IVFFlat for a single-user "personal library"
    (`.trellis/tasks/archive/2026-08/08-04-video-text-kb/design.md:143`).
  - Multi-tenant filtering was never considered.
- `vector_search` (`app/retrieval/search.py:50-83`) runs
  `WHERE user_id=… [AND item_id=…] ORDER BY embedding <=> q LIMIT k`.
- When the planner picks HNSW, pgvector collects only `hnsw.ef_search`
  (default 40) global candidates, then post-filters by tenant. Results are
  silently truncated.

### Production (read-only checks, 2026-10-06)

- pgvector 0.8.0, PostgreSQL 17; Alembic at `b8c9d0e1f2a3`; no media tables.
- 60,918 segments across 17 tenants. Per-tenant segments: median 325, p90
  10,228, max 18,900.
- Small and median tenants get an exact plan and full results. The largest
  tenant (31.7% of rows) gets the HNSW plan and received 12–39 of 50 rows in
  five random queries.
- Index costs:
  - HNSW size is 335 MB (segment heap 19 MB; total with TOAST and indexes
    858 MB).
  - Insert costs ~2.3 ms/row with the index vs 0.04 ms without (test branch).

### Test branch

RQ1 library: 35 meetings in one tenant, 5.6% of rows.

- 25/30 tenant-wide queries got fewer than 10 of the 50 requested rows; 2 got 0.
- An exact scan returns 50/50, with a top-10 identical to the exact ranking.
- Exact-sort cost by number of rows: 5k 39 ms, 10k 74 ms, 20k 120 ms,
  40k 390 ms.
- A naive filter-first CTE joined through `content_item` still seq-scans all of
  `segment`. Filtering by the tenant's item IDs can use
  `uq_segment_item_id_seq (item_id, seq)`.

### Call sites

- `KnowledgeServices.search_segments` (`app/agent/services.py:236-263`),
  tenant-wide and item-scoped.
- `app/cli.py:147` (`search`).
- Tests that monkeypatch or call `vector_search`: `tests/test_knowledge_services.py`,
  `tests/test_multiuser_integration.py:1181-1260`,
  `tests/test_provider_and_explicit_user.py:143`.
- Lexical `bm25_search` (GIN plus filter) is unaffected.

### Media vectors (uncommitted WIP of `09-09-*`)

- Migration `c9d0e1f2a3b4_media_asset_embeddings.py`:
  - untracked; `down_revision` `b8c9d0e1f2a3`;
  - creates `media_asset` and `media_embedding` (`Vector(1024)`, `app_user_id`,
    btree `ix_media_embedding_user_space`) plus HNSW
    `ix_media_embedding_cosine_hnsw`;
  - its `downgrade()` is a deliberate no-op.
- The ORM declares the media HNSW at `app/models.py:717`.
- There is no SQL media search yet: `app/retrieval/media.py` defines only the
  `MediaRepository` protocol.
- The owning task `09-09-media-asset-vector-index` is in `planning`.
- The Neon test branch is already at `c9d0e1f2a3b4`, with media tables and the
  media HNSW present.

### Migration rules

`.trellis/spec/backend/database-guidelines.md`:

- exactly one Alembic head;
- migrations run via the direct (non-pooler) URL in a one-shot unit, followed
  by `alembic upgrade head`, `alembic current` and `alembic check`.

Pooled runtime connections are PgBouncer transaction mode.

## Decisions (user, 2026-10-06)

- V1 Filter-first via the tenant's item IDs. Resolve the tenant's eligible item
  IDs first, then rank only `segment.item_id = ANY(:ids)` exactly. A
  denormalized `segment.user_id` is out of scope.
- V2 Drop the global segment HNSW `ix_segment_embedding_hnsw` with a new Alembic
  migration. Rationale:
  - after V1 nothing uses it;
  - a global graph with a tenant post-filter is the wrong long-term structure;
  - it costs 335 MB and ~2.3 ms per inserted segment.
- V2a Future large-tenant path, not built now: when a tenant approaches ~50k
  segments or latency exceeds target, benchmark Neon `lakebase_vector`
  (`lakebase_ann` with `SET LOCAL lakebase_ann.prefilter = on`; PostgreSQL ≥16)
  using the RQ1 harness, falling back to per-tenant partitioning.
- V3 Media handled together, without implementing media search SQL:
  - V3.1 Edit the uncommitted media migration: re-parent it after the new drop
    migration, and remove its HNSW index (plus the ORM declaration at
    `app/models.py:717`). The fix then ships independently of the media
    feature, and the media feature never ships the same defect.
  - V3.2 Provide a reusable filter-first exact vector ranking helper. Segment
    search uses it now; media search must use it when implemented.
  - V3.3 Record the contract in spec and as a requirement in
    `09-09-media-asset-vector-index/prd.md`.
  - V3.4 The test branch's media test tables are dropped and rebuilt through
    the corrected chain. They hold test data only.

## Requirements

- R1 Segment vector search, tenant-wide and item-scoped, ranks exactly over the
  tenant's own rows:
  1. Query 1 resolves eligible item IDs (`user_id`, not deleted, not archived,
     `state='ready'`, plus optional `platform` / `platform_ids` / `item_id`).
  2. Query 2 ranks segments with `item_id = ANY(:ids)` and
     `embedding IS NOT NULL`, ordered by cosine distance, with `LIMIT k`.
  3. Query 2 re-applies the same tenant/lifecycle predicates via the join, as
     defense in depth against races.
- R2 Cost scales with the tenant's own data. The ID filter uses the existing
  `(item_id, seq)` btree, and there is no seq scan of the whole table by
  construction.
- R3 Contract unchanged: the `vector_search(...)` signature, the `Hit` shape,
  score = 1 − cosine distance, and descending order.
- R4 No eligible items → `[]` with no vector query issued.
- R5 A reusable filter-first exact vector ranking helper (V3.2) is used by
  `vector_search`. Its docstring and spec state that media search must use it.
- R6 A new Alembic migration (down_revision `b8c9d0e1f2a3`) drops
  `ix_segment_embedding_hnsw`:
  - `DROP INDEX CONCURRENTLY IF EXISTS` in an autocommit block;
  - downgrade: `CREATE INDEX CONCURRENTLY IF NOT EXISTS` with the original
    parameters;
  - `app/models.py:1052` declaration removed;
  - exactly one head.
- R7 Media WIP edits (V3.1): `c9d0e1f2a3b4.down_revision` = the new revision.
  Its HNSW `create_index` and the ORM declaration at `app/models.py:717` are
  removed. Nothing else in the media migration changes.
- R8 Spec:
  - add a "vector search must filter tenant before ranking; no global ANN index
    with tenant post-filter" contract (retrieval spec plus database
    guidelines);
  - add the media requirement to `09-09-media-asset-vector-index/prd.md`.
- R9 Test branch (V3.4):
  1. Stamp `b8c9d0e1f2a3`.
  2. Drop the media test objects created by `c9d0e1f2a3b4`.
  3. `alembic upgrade head` via the test branch direct URL, then
     `alembic current`, `alembic check`.
  4. Exercise the new migration downgrade → upgrade once.

  Production is not migrated in this task. Deployment needs separate explicit
  approval.

## Acceptance Criteria

- [ ] Unit tests cover:
  - query-1 predicates (including `platform`, `platform_ids` and `item_id`)
  - empty tenant → `[]` with no vector query
  - query-2 restricted to the resolved IDs with tenant predicates re-applied
  - `Hit` shape and score unchanged
- [ ] `tests/test_knowledge_services.py`, `tests/test_provider_and_explicit_user.py`,
      the retrieval tests and RQ1 harness tests pass. The full suite shows no
      new failures against the recorded baseline.
- [ ] Alembic has exactly one head. Model metadata no longer declares either
      HNSW index.
- [ ] On the test branch:
  - `upgrade head`, `current` and `check` succeed;
  - the new migration's downgrade → upgrade succeeds;
  - neither HNSW index exists afterwards; media tables exist.
- [ ] RQ1 rerun on the test branch (`python -m evals.meeting_gold rq1`, library
      reused):
  - R2 equivalence 30×2 passes;
  - scope-B dense candidate counts are 50/50 for all 30 cases;
  - the scope-B baseline metrics are reported against the 2026-10-06 values.
- [ ] Spec and the media task PRD are updated per R8.

## Out of Scope

- Production migration/deploy (separate approval).
- Media search SQL implementation (owned by `09-09-media-asset-vector-index`).
- `lakebase_vector`, partitioning, `halfvec`, iterative scan.
- Lexical / fusion / diversification changes (RQ1 follow-up).
- Committing unrelated WIP.

## Addendum (user decision, 2026-10-07, during release)

- R6 revised: migration `d04fdae36884` uses plain transactional
  `DROP INDEX IF EXISTS` and, in downgrade, `CREATE INDEX IF NOT EXISTS` with
  `SET LOCAL maintenance_work_mem = '512MB'`. It no longer uses CONCURRENTLY
  or `autocommit_block`.
  - The concurrent form broke `tests/test_migration_roundtrip_postgres.py`,
    which drives Alembic on an externally managed connection.
  - The deploy procedure already stops writes before the one-shot migration
    unit, so the brief exclusive lock of a plain drop is harmless.
  - A downgrade rebuild (~1 min) must run in the same write-stopped window.
