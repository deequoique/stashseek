# Current retrieval and BM25 backend research

Date: 2026-08-26

## Local implementation audit

The runtime already executes two tenant-scoped candidate paths for every
`KnowledgeServices.search_segments()` call:

- `app/retrieval/search.py::vector_search` ranks `segment.embedding` by cosine
  distance through pgvector.
- `app/retrieval/search.py::bm25_search` is not Okapi BM25. English queries use
  `websearch_to_tsquery('english', ...)` plus `ts_rank_cd`; queries containing
  CJK use `pg_trgm.similarity` plus substring matching and additionally filter
  to `content_item.lang LIKE 'zh%'`.
- `app/agent/services.py::_diversify_hits` deduplicates the combined hits and
  sorts all candidates by their raw `score`. This directly compares
  `ts_rank_cd`, trigram similarity, and cosine similarity even though their
  scales are unrelated.
- Existing ingestion writes `to_tsvector('english', text)` only for non-Chinese
  segments and leaves Chinese `segment.fts` null.

The initial design already identified the correct fusion direction: use RRF
instead of weighted raw-score addition because lexical and cosine scores are
not comparable. It deferred RRF/reranking to a later phase so the first Agent
and evidence loop could ship. Relevant local sources:

- `.trellis/tasks/archive/2026-08/08-04-video-text-kb/design.md`
- `.trellis/tasks/archive/2026-08/08-06-connect-agent-embedding/prd.md`
- `.trellis/spec/backend/agent-retrieval-convergence.md`

## Deployment constraint

Production uses pooled Neon PostgreSQL for runtime and a matching direct Neon
URL for migrations. Local development uses `pgvector/pgvector:pg17` with only
`vector` and `pg_trgm` enabled by the initial migration. PostgreSQL core does
not provide Okapi BM25 ranking; `ts_rank` / `ts_rank_cd` are PostgreSQL FTS
ranking functions without BM25 corpus statistics.

## Neon official BM25 status

Neon's official documentation says:

- `pg_search` is deprecated. New Neon projects cannot enable it after
  2026-03-19 and existing installs are scheduled for removal in September 2026.
- The supported replacement is `lakebase_text`, which adds a
  `lakebase_bm25` index over a standard `tsvector` column.
- It requires PostgreSQL 16+ and a preloaded Neon library. Filtering still uses
  `@@`; ranking uses `tsvector <@> to_bm25query(...)`. The returned score is
  negative, so the best results sort ascending.
- BM25 corpus statistics are established at index build time and refreshed at
  `VACUUM`; bulk backfills therefore require an explicit index/VACUUM plan.
- The extension retains PostgreSQL text-search tokenization. It does not solve
  Chinese tokenization by itself. The application must generate a compatible
  `tsvector` representation for CJK and use the same tokenization for queries.

Primary sources:

- Neon, “The pg_search extension”:
  https://neon.com/docs/extensions/pg_search
- Neon, “Migrate from pg_search to lakebase_text”:
  https://neon.com/docs/extensions/migrate-pg-search-to-lakebase-text
- Neon, “The lakebase_text extension”:
  https://neon.com/docs/extensions/lakebase-text

## Considered options

### A. Neon `lakebase_text` for production, explicit PostgreSQL FTS fallback

Recommended. This gives the deployed Neon path real BM25 without adding an
external search service. A narrow backend contract lets local/self-hosted
PostgreSQL keep `ts_rank_cd`/trigram under the accurate name `postgres_fts`.
Both backends feed the same RRF implementation. Configuration/readiness must
make the difference explicit and must never silently downgrade a deployment
that requires BM25.

Tradeoff: local and production lexical scores/behavior differ. Release
acceptance therefore needs an isolated Neon branch test and a support matrix.

### B. Require a self-hosted BM25-capable PostgreSQL distribution everywhere

This can give local/production parity, for example by operating a ParadeDB
compatible PostgreSQL distribution. It conflicts with the current managed
Neon deployment and expands the task into database replacement, backup,
monitoring and production migration. It is not recommended for this increment.

### C. Application-side BM25 index

A Python/Rust library could maintain a separate index, but transactionally
synchronizing it with segment ingestion, deletion, restore, retry and tenant
filters would create a second source of truth. It also complicates multi-process
deployment and recovery. Not recommended while PostgreSQL remains authoritative.

### D. Keep PostgreSQL FTS and only add RRF

This is a useful low-risk intermediate and fixes the raw-score comparison bug,
but it does not satisfy the request for standard BM25. It should be a rollout
gate/fallback, not the final production target.

## Recommended implementation boundary

1. Establish the retrieval benchmark and record the current legacy baseline.
2. Rename the existing path to `postgres_fts` and introduce ranked-hit backend
   provenance.
3. Implement/test RRF against existing FTS + vector, then benchmark it before
   introducing a new index.
4. Add deterministic multilingual lexical documents and a resumable backfill.
5. Add Neon `lakebase_text` BM25 behind explicit configuration and readiness.
6. Run the same benchmark on an isolated Neon branch and switch production only
   if the PRD quality gates pass.

This sequencing isolates ranking correctness from extension/migration risk and
allows rollback without deleting user data or embeddings.
