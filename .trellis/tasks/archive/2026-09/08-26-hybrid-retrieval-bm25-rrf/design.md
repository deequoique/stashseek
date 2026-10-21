# Hybrid Retrieval BM25 + RRF Design

## 1. Boundary and invariants

The change stays inside ingestion lexical-document generation, retrieval
backends, candidate fusion, evaluation, schema/configuration and deployment
readiness. Agent tool schemas, retrieval call budgets, query embedding,
Citation authorization and Composer behavior remain unchanged.

```text
query
  ├─ query embedding ──> tenant-scoped pgvector top-K ─┐
  └─ lexical tokens ───> configured lexical top-K ─────┤
                                                       v
                                                RRF by rank
                                                       v
                                          item diversification
                                                       v
                                  tenant-scoped hydration/Citations
```

Hard invariants:

- server-owned tenant/item/reference scope is applied independently to every
  retrieval query and again during hydration;
- each backend is bounded to `min(50, max(20, limit * 5))` candidates;
- fusion cannot increase public limit 10, source-item limit 5 or Composer
  evidence limits;
- embedding is still mandatory for a hybrid search; no lexical-only success on
  embedding failure;
- raw backend scores are diagnostic/internal only and never compared across
  backends.

## 2. Ranked hit contract

Extend the internal hit representation conceptually as follows:

```python
@dataclass(frozen=True)
class RankedHit:
    item_id: int
    segment_id: int
    backend: Literal["vector", "postgres_fts", "lakebase_bm25"]
    backend_rank: int               # one-based within that backend
    backend_score: float | None     # never used across backends
    fused_score: float | None = None
```

Search backends return deterministic rank order with segment ID as the final
tie-break. Tenant and lifecycle predicates remain SQL-level filters, not
post-filtering. The lexical contract receives a normalized query and scope;
models/users never supply backend, tenant or ranking parameters.

## 3. Lexical document and tokenization

Retain a single searchable `tsvector` representation per segment but make its
generation language-aware and reusable by ingestion and backfill:

- English/Latin prose uses the existing `english` text-search configuration.
- CJK text is converted by a deterministic application tokenizer into bounded
  CJK bigrams plus preserved ASCII/code tokens, then parsed with `simple` so
  segment and query lexemes are identical.
- Code-switch input produces both the applicable Latin lexemes and CJK
  lexemes. Query language must not become an item-language authorization/filter.
- Empty/over-short CJK input has an explicit rule and cannot expand into an
  unbounded wildcard scan. Substring/trigram may remain only as the named
  `postgres_fts` fallback behavior where benchmarked.

The exact token contract is versioned, for example `lexical_document_version=2`,
so readiness can detect incomplete backfill. Token output is bounded by segment
text limits and is never logged.

## 4. Lexical backends

### 4.1 `postgres_fts`

The portable fallback uses standard PostgreSQL GIN filtering and
`ts_rank_cd`/trigram where needed. It is accurately named and remains available
for local or self-hosted profiles that explicitly select it. It provides
backend ranks but makes no BM25 claim.

### 4.2 `lakebase_bm25`

The production target uses Neon `lakebase_text`:

```sql
CREATE EXTENSION IF NOT EXISTS lakebase_text;
CREATE INDEX ... ON segment USING lakebase_bm25 (lexical_fts)
  WITH (default_limit = 50);

SELECT ...,
       lexical_fts <@>
         to_bm25query(<query_tsvector>, '<index_name>'::regclass) AS score
FROM segment JOIN content_item ...
WHERE <tenant/lifecycle/scope predicates>
  AND lexical_fts @@ <query_tsquery>
ORDER BY score ASC, segment.id ASC
LIMIT :candidate_limit;
```

The concrete SQL must be proven with `EXPLAIN` on an isolated Neon branch,
including strict tenant/item filters. `lakebase_text` prefilter behavior must
be measured rather than enabled blindly. Index name is application-owned and
constant, never user/model input.

Configuration is explicit:

```text
LEXICAL_SEARCH_BACKEND=postgres_fts | lakebase_bm25
RETRIEVAL_FUSION_MODE=legacy | rrf
```

`lakebase_bm25` requires extension, index and lexical-document version at
readiness. Missing requirements stop admission with a safe configuration
category. Runtime query failure remains `retrieval_unavailable`.

## 5. RRF and diversification

For each backend's one-based rank:

```python
fused_score[segment_id] += weight[backend] / (rrf_k + backend_rank)
```

Initial weights are equal. Candidate `rrf_k` values must be selected from a
small predeclared set using the fixed benchmark; the chosen constant is then a
server-owned code/config default with a regression fixture. Tie-break order is:

1. fused score descending;
2. number of contributing backends descending;
3. best backend rank ascending;
4. segment ID ascending.

Item diversification runs only after fusion. It preserves the existing policy:
rank item groups by their best fused hit, reserve one representative from at
most five items, then fill remaining slots from those item groups. Hydration
preserves this fused order. Citation retrieval score, if retained, is the fused
score and is never the maximum of lexical and cosine values.

## 6. Benchmark design

Add a model-free retrieval evaluator adjacent to the existing natural-language
quality code. It consumes stable Gold Evidence and calls each retrieval variant
with a deterministic query embedding fixture or the configured opt-in embedder,
depending on command mode.

Variants:

- current legacy merge (baseline only);
- lexical backend only;
- vector only;
- FTS + vector RRF;
- BM25 + vector RRF on Neon.

Reports contain dataset/config versions, variant, counts, ranks and aggregate
metrics, not query or evidence content. Evaluation includes overall and named
subgroups. A fixed tuning split selects RRF parameters; a separate fixed holdout
decides the release gate to avoid choosing and evaluating on the same queries.

No-evidence evaluation must use a bounded relevance threshold or explicit
candidate admission rule if the current vector path otherwise always returns a
nearest neighbor. That threshold/admission policy is measured and, if a product
change is required, documented within this task rather than silently added.

## 7. Migration and rollout

1. Ship benchmark and record legacy results without runtime change.
2. Ship accurate naming/ranked-hit/RRF code with `legacy` still selected.
3. Backfill versioned multilingual lexical documents in bounded resumable
   batches; new ingestion dual-writes the new representation.
4. On an isolated Neon branch, enable `lakebase_text`, create the BM25 index
   after backfill, `VACUUM`, verify `EXPLAIN` and run the holdout benchmark.
5. Deploy application support with `postgres_fts`/legacy still active; readiness
   observes BM25 capability without switching traffic.
6. Switch to RRF, then `lakebase_bm25`, as separate reversible configuration
   changes after quality gates pass.
7. Remove legacy naming/compatibility only after an observation window; retain
   FTS fallback as a supported explicit profile.

Rollback switches backend/fusion configuration first. New columns/indexes stay
in place until a later reviewed cleanup migration; rollback never drops
segments, embeddings or source objects.

## 8. Failure and privacy behavior

| Condition | Behavior |
| --- | --- |
| embedding unavailable | existing `embedding_unavailable`; no lexical answer |
| configured BM25 extension/index absent at startup | readiness/configuration failure |
| lexical/vector SQL fails at runtime | `retrieval_unavailable` |
| both searches succeed with zero admitted hits | `no_evidence` |
| backfill incomplete | BM25 readiness fails; old backend remains selectable |
| evaluation fixture cannot resolve stable evidence | explicit skip/unscorable, not zero |

Diagnostics may emit fixed backend/fusion mode, candidate counts, durations and
safe failure categories. Query text, lexemes, hits, segment/item IDs, excerpts,
URLs and raw scores remain excluded from production logs.

## 9. Alternatives rejected

- Directly sort `ts_rank_cd`, trigram and cosine scores: invalid cross-scale
  comparison and the current defect.
- New Neon `pg_search`: unavailable for new projects and scheduled for removal.
- External Elasticsearch/OpenSearch: duplicates the source of truth and adds
  ingestion/deletion synchronization beyond this task.
- Application-local BM25 index: difficult to keep transactional across workers,
  tenants, deletion/restore and multi-process deployment.
- Cross-encoder in this task: adds a third ranking model before the lexical and
  fusion baseline is measurable.

## 10. Open decision

Implementation starts only after confirming whether production may depend on
Neon `lakebase_text` while local/self-hosted PostgreSQL remains on an explicit
`postgres_fts` fallback. Requiring identical BM25 everywhere needs a different
database distribution and a broader deployment design.
