# Design: RQ1 production-faithful retrieval study

## Modules (all under `evals/meeting_gold/`, no production code changes)

| Module | Responsibility |
|---|---|
| `production_path.py` | Caption-track builder (D4); golden connector; in-memory object store; retrying ingest embedder wrapper (harness-only retry, recorded in the manifest); `ingest_library()` (35 meetings → one tenant via `create_item` + `process_item`); chunk↔sentence containment map; idempotent library manifest. |
| `retrieval_study.py` | Settings matrix, fusion/diversify implementations built on production primitives, scope A/B execution, R2/R7 checks, metrics, ceiling. |
| `stats.py` | Exact McNemar, Wilson CI, paired bootstrap, win/loss/tie. |
| `report.py` | Markdown/CSV tables, `attribution.svg` (hand-written SVG, no plotting dependency), failure-case rendering. |
| `agent_smoke.py` | R8 end-to-end harness (ported from the session scratch runner), one subprocess per case. |
| `__main__.py` | New subcommands `rq1` and `agent-smoke`. |

## Library build (`production_path.ingest_library`)

1. Load the bundle. Meetings = the 35 val meeting IDs. Case list = the 30 fixed
   IDs, stored as a constant plus a hash.
2. For each meeting, build the json3 caption track and per-word sentence
   provenance:
   - cue `c` holds the words `w`, each with its `sentence_id`;
   - a sentence's word span is recorded as (first cue, last cue).
3. Create one `AppUser` for the library and run `create_item` + `process_item`
   with the production worker embedder (wrapped in harness retries; production
   retry behavior arrives with `fix-embed-transient-errors`). Use the production
   settings from `.env`, except `DATABASE_URL`, which must resolve to the Neon
   test host. The host is asserted as in the existing smoke harness; the
   production host is refused.
4. Map each segment's `[start_sec, end_sec]` back to a cue range and assert that
   the text equals the joined cue texts.
   - **Full containment**: sentence `s` ⊂ chunk iff all of its cues lie in the
     chunk's cue range.
   - Partial containment is recorded for diagnostics only.
5. The manifest (`library.json` in the run dir) records:
   - meeting → item_id, tenant user_id
   - chunker parameter fingerprint
   - git SHA, dataset hash
   - per-segment containment

   The build is idempotent: an existing manifest is reused when the DB rows
   still match (segment IDs, text hash). Otherwise a fresh tenant is ingested
   and the stale manifest is renamed.

## Retrieval settings (`retrieval_study.py`)

Per case and scope, with the query embedded once (cached in the run dir):

- `lex_and = bm25_search(db, q, user_id, k=50[, item_id])`, production function.
- `lex_or` = the same SQL with `to_tsquery('english', <lexemes joined by ' | '>)`,
  where the lexemes come from `plainto_tsquery('english', q)`. Implemented
  locally, mirroring `bm25_search` filters exactly.
- `dense = vector_search(db, qvec, user_id, k=50[, item_id])`, production
  function and production plan.
- `dense_exact`: the same filters with `SET LOCAL enable_indexscan = off` and
  `SET LOCAL enable_bitmapscan = off` in a read-only transaction. Used only
  for R7.

Fusion, all yielding a scored list per unique segment:

| Method | Rule |
|---|---|
| ① lexical | the lexical list |
| ② dense | the dense list |
| ③ raw merge | concatenate lexical + dense hits (production semantics; `_diversify_hits` keeps the max score per segment) |
| ④a RRF | `Σ 1/(60 + rank)` over the lists in which the segment appears |
| ④b min-max | each list min-max normalized to [0,1] (an absent segment scores 0); `0.5·lex + 0.5·dense` |

Diversification:

- **on**: apply the production `_diversify_hits` to the fused list (as `Hit`
  objects with the fused score) with `limit=10`.
- **off**: sort by fused score and take the top 10.

Scope A passes `item_id`; scope B omits it.

Checks:

- **R2**: `③-AND + div-on == KnowledgeServices.search_segments(q, limit=10[, item_id])`,
  compared as an ordered segment-ID list. The service is called with a cached
  embedder that returns the same query vector, so no embedding drift is
  possible.
- **Scope-A invariance**: div-on == div-off for every method.
- Record `EXPLAIN` node types for the dense query in each scope (`hnsw` vs
  `sort`).

## Metrics

For each ranked top-10 list `L` and gold set `G` (scorer `_validate_case`,
`ces`):

- `covered(k)` = ∪ fully-contained sentences of `L[:k]`, intersected with `G`.
- `Hit@k = [covered(k) ≠ ∅]`.
- `Recall@k = |covered(k)| / |G|`.
- `RR` = 1/rank of the first chunk that fully contains some `g ∈ G` (0 if none
  in the top 10). MRR is the mean of RR.
- `nDCG@10`: binary gain per chunk.
- Scope B: `MeetingHit@k = [case item ∈ items(L[:k])]`.
- **Ceiling@k**: the exact maximum coverage over subsets of size ≤k of the
  meeting's chunks that contain ≥1 gold sentence. Enumerate combinations; with
  the fixed chunker, gold-bearing chunks per case are expected to be ≤ ~15, so
  C(15,10) is trivial. If more than 25, fall back to greedy and flag the
  value as a lower bound in the table.
- Ineligible cases are listed in the setup table and excluded from macro
  averages (n = 28).

## Statistics (`stats.py`)

| Measure | Method |
|---|---|
| Hit@k, setting vs baseline | exact two-sided McNemar: binomial test on discordant counts b, c with p=0.5 |
| Proportions | Wilson 95% CI |
| Mean metric differences | paired bootstrap, 10,000 resamples of cases, seed 20261006, percentile CI |
| Recall@5 and RR | per-case win/loss/tie |

The setup table includes a minimum detectable effect note: n = 28 paired cases
and the discordance needed for p < 0.05.

## Attribution chart

Baseline = scope B, ③-AND, div-on (= production).

- **Bars** (Δ Recall@5 and Δ Hit@5, with bootstrap CIs):
  - one-factor changes: AND→OR (③-OR), raw→RRF (④a-AND), raw→min-max
    (④b-AND), div on→off (③-AND-off)
  - the best combined setting
  - scope B→A (the known-meeting gain)
- **Line**: the ceiling Recall@5.
- **Format**: hand-written SVG, with the same data in CSV.

## Failure cases

Rule: per domain (Academic, Product, Committee), pick the eligible case with
the largest `Ceiling Recall@5 − baseline Recall@5`. Ties go to the lower
baseline Hit@5, then case_id. The text for each case is rendered from stored
data:

- query
- gold sentences, with speaker
- top-5 returned chunks: rank, score, meeting, first 300 chars
- which gold chunks ranked where in each list

The diagnosis paragraph is written by the implementer after reading the data,
labelled as analysis.

## Safety and cost

- No LLM calls in `rq1`.
- Embedding calls:
  - ingest of 35 meetings: every cue plus chunks, ≈ 35 × (1.2k cues + ~300
    chunks)
  - 30 queries
- All DB access goes to the Neon test branch. Study queries run in read-only
  transactions. Ingest writes only to a fresh test tenant.

## Compatibility

New modules and CLI subcommands only. The existing `select` / `score`
behavior and artifact validation are untouched. Run artifacts live under the
existing `data/meeting_gold/runs/`, which is outside the validated artifact
inventory. `data/` is gitignored.
