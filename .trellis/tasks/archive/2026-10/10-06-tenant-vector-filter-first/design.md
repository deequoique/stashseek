# Design: filter-first exact vector search; drop global HNSW

## Boundary

| Area | Change |
|---|---|
| `app/retrieval/search.py` | New helper `filter_first_vector_rank`; `vector_search` rewritten on top of it (signature unchanged). |
| `app/models.py` | Remove the `Index("ix_segment_embedding_hnsw", …)` (~:1052) and `Index("ix_media_embedding_cosine_hnsw", …)` (~:717) declarations only. |
| `migrations/versions/<new>_drop_segment_embedding_hnsw.py` | New revision, down_revision `b8c9d0e1f2a3`. |
| `migrations/versions/c9d0e1f2a3b4_media_asset_embeddings.py` (untracked WIP) | `down_revision` → new revision; delete its HNSW `op.create_index` block. Nothing else. |
| Tests | New `tests/test_vector_search_filter_first.py`; keep existing call-site tests green. |
| Spec / other task | `.trellis/spec/backend/agent-retrieval-convergence.md`, `.trellis/spec/backend/database-guidelines.md`, `.trellis/tasks/09-09-media-asset-vector-index/prd.md` (requirement line). |

Unchanged: `KnowledgeServices`, `bm25_search`, `_diversify_hits`, CLI, the agent,
media service and protocol code, `lakebase_*` (not installed).

## Helper contract (`app/retrieval/search.py`)

```python
def filter_first_vector_rank(
    db,
    candidates: Select,          # SELECT <id_col>, <embedding_col> ... already restricted to ONE tenant
    *,
    id_column: str,              # label of the id column in `candidates`
    embedding_column: str,       # label of the vector column in `candidates`
    query_vector: Sequence[float],
    k: int,
) -> list[tuple[int, float]]:    # (id, 1 - cosine_distance), best first, len <= k
```

- The helper wraps `candidates` in `WITH tenant_vectors AS MATERIALIZED (…)` via
  SQLAlchemy `.cte(...).prefix_with("MATERIALIZED")`. It then runs
  `SELECT id, 1 - (embedding <=> :q) AS score FROM tenant_vectors ORDER BY embedding <=> :q LIMIT :k`.
  - The materialized CTE is an optimization fence: Postgres must evaluate the
    tenant-restricted candidate set first.
  - An approximate index on the base table can never be used for the ranking,
    even if someone re-adds one later.
- `k < 1` → `[]` without a query.
- The docstring states the rule: callers must restrict `candidates` to a single
  tenant through an indexed predicate. Media search (`09-09-*`) must use this
  helper with `media_embedding.app_user_id = :tenant` plus the embedding-space
  columns, which hit `ix_media_embedding_user_space`.

## `vector_search` (signature and `Hit` output unchanged)

1. **Query 1 (eligible items).**

   ```python
   select(ContentItem.id).where(
       ContentItem.user_id == user_id,
       ContentItem.deleted_at.is_(None),
       ContentItem.archived_at.is_(None),
       ContentItem.state == "ready",
       [platform], [platform_ids / platform_id via in_ or false()], [item_id],
   )
   ```

   This reuses the existing predicate logic verbatim, including the
   empty-`platform_ids` → `false()` behavior. No IDs → return `[]` and issue
   no vector query (R4).
2. **Query 2 (rank).**
   - Candidates are
     `select(Segment.id.label("id"), Segment.embedding.label("embedding")).where(Segment.item_id == any_(array(ids)), Segment.embedding.isnot(None))`.
   - Bind IDs as one array parameter (`= ANY(:ids)`) so the plan stays
     stable for large ID lists, and the filter uses `uq_segment_item_id_seq`
     (R2).
   - Call `filter_first_vector_rank(..., k=k)`.
3. **Hydrate.**
   - Query `select(Segment, ContentItem).join(ContentItem).where(Segment.id.in_(ranked_ids), <all tenant/lifecycle predicates again>)`.
     Re-applying the predicates is defense in depth against an item being
     deleted, archived or reassigned between the queries (R1.3).
   - Build `Hit`s in ranked order with `score` from step 2, dropping any ID
     that no longer hydrates.
   - Return a `list[Hit]` identical in shape to today's.

Complexity: queries 1 and 3 are index lookups. Query 2 is O(tenant segments)
distance computations: about 120 ms at 20k segments and 390 ms at 40k on the
test branch. The production max is 18.9k.

## Migration `<new>_drop_segment_embedding_hnsw`

```python
revision = "<generated 12-hex>"
down_revision = "b8c9d0e1f2a3"

def upgrade():
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_segment_embedding_hnsw")

def downgrade():
    with op.get_context().autocommit_block():
        op.execute("SET maintenance_work_mem = '512MB'")
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_segment_embedding_hnsw "
            "ON segment USING hnsw (embedding vector_cosine_ops) "
            "WITH (m = 16, ef_construction = 64)"
        )
```

- `CONCURRENTLY` cannot run in a transaction, hence the autocommit block. It
  needs the **direct** URL, which is already the migration contract.
- `IF [NOT] EXISTS` keeps the migration idempotent if a drop was done by hand.
- Downgrade rebuild time: ~1 min at 61k rows with 512 MB `maintenance_work_mem`
  (measured 32 s at 41k). Writes are not blocked.

## Media WIP edit (V3.1)

- In `c9d0e1f2a3b4_media_asset_embeddings.py`, set
  `down_revision = "<new revision>"` and delete the
  `op.create_index("ix_media_embedding_cosine_hnsw", …)` block.
- Its no-op `downgrade()` stays. The single head is `c9d0e1f2a3b4` in the WIP
  tree and `<new>` in the committed tree.

## Test-branch procedure (R9; destructive only to test media tables)

Use the direct URL from `.env.neon-test` (host without `-pooler`). Assert the
test host ≠ production host before connecting. Never print a DSN.

1. `alembic stamp b8c9d0e1f2a3`. The branch currently records `c9d0e1f2a3b4`,
   which is no longer reachable on the old path.
2. `DROP TABLE IF EXISTS media_embedding, media_asset CASCADE`. These are the
   objects created by `c9d0e1f2a3b4` and hold test data only.
3. `alembic upgrade <new>` drops the segment HNSW.
   `alembic downgrade b8c9d0e1f2a3` recreates it. `alembic upgrade <new>`
   drops it again. This is the roundtrip check for the new migration.
4. `alembic upgrade head` creates the media tables without HNSW. Then run
   `alembic current` and `alembic check` (the models must match the DB).
5. Verify `select indexname from pg_indexes where indexdef ilike '%hnsw%'`
   returns nothing.

The full-chain downgrade through `c9d0e1f2a3b4` is not exercised: its
`downgrade()` is a deliberate no-op owned by the media task.

## Verification on data

Re-run `python -m evals.meeting_gold rq1 --output data/meeting_gold/runs/rq1-20261006`.
The library is reused, so this makes only DB reads plus cached query
embeddings. Expected:

- the R2 equivalence still passes, since the harness and production both call
  `vector_search`;
- scope-B dense candidate counts are 50/50;
- report the new scope-B baseline against the 2026-10-06 run.

## Risks

- `app/models.py` and the media migration carry unrelated WIP. Edits must be
  surgical to the two `Index(...)` declarations and the two migration lines.
- Planner variance on tiny test tables: correctness never depends on the plan,
  because the materialized CTE plus the exact sort guarantee exact results.
  Speed claims are verified on the test branch only.
- Production rollout (later, separate approval) must run the migration via the
  direct URL. The `DROP INDEX CONCURRENTLY` takes seconds.

## Rollback

- Code: revert `search.py` / `models.py`.
- Schema: `alembic downgrade b8c9d0e1f2a3` recreates the index concurrently.
