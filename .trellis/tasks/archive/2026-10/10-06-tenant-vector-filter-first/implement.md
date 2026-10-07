# Implement: filter-first vector search, drop global HNSW

Implementer: `trellis-implement` sub-agent, model **sonnet**. Checker:
`trellis-check`. Never block a single tool call for more than ~5 minutes; run
anything slow in the background and poll.

## Ordered checklist

1. `app/retrieval/search.py`: add `filter_first_vector_rank` and rewrite
   `vector_search` per `design.md`. Keep `bm25_search`, `Hit` and `_hits`
   unchanged.
2. `tests/test_vector_search_filter_first.py`. Use compiled-SQL assertions and a
   fake session; no live DB needed. Cover:
   - query-1 predicates, including `platform`, empty `platform_ids` → no items,
     and `item_id`;
   - empty tenant → `[]` with no second query;
   - query 2 uses `= ANY(:ids)` plus `embedding IS NOT NULL` inside a
     `MATERIALIZED` CTE, ordered by distance with `LIMIT k`;
   - hydration re-applies tenant/lifecycle predicates and preserves rank order
     and scores;
   - helper `k < 1` → `[]`.
3. `app/models.py`: remove only the two HNSW `Index(...)` declarations (~:717
   media, ~:1052 segment).
4. New migration `migrations/versions/<rev>_drop_segment_embedding_hnsw.py`
   (`down_revision = "b8c9d0e1f2a3"`) per `design.md`.
5. Edit the WIP media migration `c9d0e1f2a3b4_media_asset_embeddings.py`:
   `down_revision` → `<rev>`; delete its HNSW `op.create_index` block only.
   Verify `alembic heads` shows exactly one head.
6. Spec, per R8:
   - `.trellis/spec/backend/agent-retrieval-convergence.md`: the vector-search
     contract;
   - `.trellis/spec/backend/database-guidelines.md`: a new scenario, "Vector
     indexes in a multi-tenant table", in the 7-section format;
   - `.trellis/tasks/09-09-media-asset-vector-index/prd.md`: append the media
     requirement ("media search must call `filter_first_vector_rank` restricted
     by `app_user_id` + embedding-space columns; no global ANN index").
7. Test-branch procedure (`design.md` §Test-branch procedure, R9). Use the direct
   test URL with the host assertion. Report each step's result.
8. RQ1 rerun on the test branch; report dense candidate counts and the scope-B
   baseline against 2026-10-06.

## Validation

```bash
.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_vector_search_filter_first.py tests/test_knowledge_services.py tests/test_provider_and_explicit_user.py tests/test_meeting_gold*.py
.venv/bin/alembic heads        # exactly one
.venv/bin/python -m pytest -q -p no:cacheprovider --continue-on-collection-errors   # background; compare with baseline (32 failed / 1 collection error pre-existing)
.venv/bin/python -m evals.meeting_gold rq1 --output data/meeting_gold/runs/rq1-20261006
```

## Safety

- Never connect to the production DSN in this task.
- Test-branch DDL only via the direct test URL after asserting the host.
- No git commit/add/stash/checkout/reset.
- No production migration.
