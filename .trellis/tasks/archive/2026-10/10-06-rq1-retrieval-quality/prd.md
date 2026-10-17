# RQ1 检索质量：混合检索在长会议转录上的证据召回

Parent: `10-06-meeting-qa-quality`. **Ordering:** starts only after
`10-06-fix-chunk-merge` is implemented and checked (D6). The answer-stage and
embedding fix children are independent of RQ1 metrics.

## Goal

Answer RQ1 with a reproducible, production-faithful, paired retrieval study:

> How well does hybrid lexical–dense retrieval recover annotated evidence in
> long spoken transcripts, and how much do fusion and diversification choices
> matter?

The output feeds a 5-slide deck: setup table, baseline table (with the
theoretical ceiling), ablation table (scope A/B), root-cause attribution chart,
and 3 typical failure cases.

## Background (confirmed, 2026-10-06)

- **Data.** `data/meeting_gold/final` (QMSum + ExplainMeetSum). The val split has
  35 meetings. The 30 selected `specific` cases (10 per domain) span 20 of them.
  28 are scorable under the `ces` policy: `qmsum:Bed015:specific:2` is
  answer_only and `qmsum:IS1006c:specific:8` is partial. The case list equals
  the 30 case IDs of `/private/tmp/meeting-agent-real-20261006-specific30`.
- **Production retrieval** is `KnowledgeServices.search_segments`
  (`app/agent/services.py:200-305`):
  - lexical `bm25_search` (`app/retrieval/search.py:86-119`), which is
    `ts_rank_cd` over `websearch_to_tsquery`, i.e. AND semantics;
  - plus `vector_search` (cosine).
  - Each path fetches `max(20, limit*5)` = 50 candidates. The lists are
    concatenated and ranked by raw score in `_diversify_hits`
    (`app/agent/services.py:78-107`). That function reserves the best segment of
    up to 5 items, then fills to `limit` = 10.
- With the AND query, all 30 raw questions get 0 lexical hits, on sentence-level
  and ~150-word chunks alike.
- `_diversify_hits` is order-preserving when every hit comes from one item.
  Diversification is therefore only observable in scope B.
- **Vector index.** `segment.embedding` has a global HNSW index
  (`ix_segment_embedding_hnsw`, cosine), and the test branch runs pgvector
  0.8.6. A query scoped to one item plans an exact sort via
  `uq_segment_item_id_seq`. A tenant-wide query may plan an approximate HNSW
  scan with post-filtering. Production semantics include whichever plan
  Postgres chooses.
- **Why the status-table baseline cannot be reused.** The figures (Hit@5 8/28;
  Recall@1/3/5 = 2.69/5.19/6.77%; MRR 23.83%) came from the old smoke runner,
  which:
  - indexed one segment per gold sentence;
  - forced `item_id`, so it is actually scope A, not 全库;
  - pooled agent-rewritten queries with neighbor expansion.

  The figures are recomputed under this protocol.
- The earlier in-session offline ablation (Python BM25, 170-word hard cut,
  scope A only) is directional evidence only.

## Decisions

- D1 Query = the raw QMSum question text. No LLM; every setting is
  deterministic and strictly paired.
- D2 Scope B library = all 35 val meetings in one test tenant (15 distractor
  meetings). Scope A = the same index searched with `item_id` = the case's
  meeting.
- D3 Harness code lives in `evals/meeting_gold/`, reproducible from one command.
- D4 The index is built through the production ingest path (`create_item` +
  `process_item`, production `chunk()` with its semantic embedder, production
  embedding and FTS) on the Neon test branch. The transcript enters as a
  YouTube json3 caption track:
  - ≤12 words per cue
  - a new cue at each speaker turn
  - 2.5 words/s
  - 0.3 s pause between turns
  - dataset annotation tokens like `{disfmarker}` removed
- D5 Lexical query semantics are a factor:
  - AND = production `websearch_to_tsquery`
  - OR = the same `plainto_tsquery` lexemes joined with `|`
- D6 RQ1 runs on the post-fix chunker from `10-06-fix-chunk-merge`.
  "Current chunking" in baseline and ceiling means the fixed production
  chunker.

## Requirements

- **R1 Settings.** Paired, all 30 cases, top-10 per setting (production
  `limit`):

  | Retrieval method | Variants |
  |---|---|
  | ① lexical only | AND, OR |
  | ② dense only | — |
  | ③ raw-score merge (current) | AND = production, OR |
  | ④a RRF (k=60) | AND, OR |
  | ④b min-max normalized sum (α=0.5) | AND, OR |

  - Every method runs with diversification on and off, in scope A and scope B.
  - In scope A, diversification on/off must yield identical rankings
    (asserted), and the table reports one column.
  - Candidate pools match production: 50 lexical and 50 dense.
- **R2 Production equivalence.** Setting ③-AND with diversification on must
  equal `KnowledgeServices.search_segments(question, limit=10[, item_id])`
  exactly, per case and scope (asserted).
- **R3 Ceiling.** For each case and each k ∈ {1,3,5,10}, report the maximum
  fraction of gold evidence sentences that any k post-fix chunks of the
  case's meeting fully contain (exact search over gold-bearing chunks),
  plus the ceiling Hit@k.
- **R4 Metrics.** A sentence counts as covered by a chunk only if the chunk fully
  contains it, i.e. every word of the sentence lies inside the chunk's cue range.
  For k ∈ {1,3,5,10}:
  - Hit@k: ≥1 gold sentence covered by the top-k chunks.
  - Recall@k: the fraction of gold sentences covered by the top-k chunks.
  - MRR@10: the rank of the first chunk covering a gold sentence.
  - nDCG@10 with binary chunk gain.
  - Scope B only: meeting-level Hit@k, i.e. the case's meeting appears among
    the top-k chunks' items.

  Gold sets and eligibility come from the existing scorer (`_validate_case`,
  policy `ces`).
- **R5 Statistics.** Each setting is compared with the baseline, paired by case:
  - Hit@k: exact McNemar on discordant pairs.
  - Recall@5 and RR: per-case win/loss/tie.
  - 95% CIs: Wilson for proportions, paired bootstrap (10,000 resamples,
    fixed seed) for mean differences.
  - The setup table states n = 28 and the resulting minimum detectable effect.
- **R6 Deliverables** (`data/meeting_gold/runs/rq1-<date>/`):
  - `setup.md`: data scale, index stats (segments, median/p10/p90 words),
    definitions of Hit vs Recall.
  - `baseline.md` + CSV: production setting in scopes A and B, with the ceiling.
  - `ablation.md` + CSV: every setting × scope, with Δ vs baseline and CIs.
  - `attribution.svg` + data: one-factor-at-a-time Δ from the scope-B baseline
    (AND→OR, raw→RRF, raw→min-max, diversify on→off), the best cumulative path,
    and the remaining gap to the ceiling.
  - `failure_cases.md`: 3 cases chosen by a written rule (largest
    ceiling−baseline gap, one per domain). Each shows the query, gold evidence
    sentences, top-5 returned chunks (text, score, meeting, rank) and a
    diagnosis.
  - `per_case.jsonl` and `run_manifest.json`: dataset/selection hashes,
    git SHA, chunker parameters, EXPLAIN plan type per scope, query embedding
    cache.
- **R7 ANN diagnostic.** For scope B, also compute the exact dense ranking
  (sequential scan, same filters) and report how often the production plan's
  dense top-10 differs from it, plus the metric delta. This is a diagnostic,
  not an ablation factor.
- **R8 End-to-end harness.** Ship the production-path agent smoke (ingest
  reuse, web `agent.stream`, fresh thread, one process per case, failure
  categories, chunk-level citation metrics) in `evals/meeting_gold/`. The
  parent's post-fix rerun uses it.

## Acceptance Criteria

- [ ] `python -m evals.meeting_gold rq1 --output data/meeting_gold/runs/rq1-<date>`
      rebuilds or reuses the 35-meeting index idempotently and regenerates
      every R6 artifact from the same selection.
- [ ] The R2 equivalence assertion passes for all 30 cases in both scopes.
- [ ] Scope-A diversification on/off equivalence is asserted.
- [ ] Every table reports n, metric definitions and CIs. Hit and Recall are
      defined distinctly, as in R4.
- [ ] Unit tests cover:
  - full-containment mapping, including a sentence split across cues and a
    chunk overlap
  - each fusion method on a toy ranking
  - exact ceiling on a toy instance
  - McNemar, Wilson and bootstrap on fixed inputs
- [ ] The R8 harness reproduces the 2026-10-06 failure categorization when run
      against the pre-fix code path. A spot check on the existing scratch
      results is sufficient.

## Out of Scope

- The optional comparison on the 20 real-video human gold samples (17 with
  evidence). Deferred to a follow-up.
- Chunking variants beyond the fixed production chunker. The ceiling is the
  only segmentation signal in RQ1.
- Agent query rewriting, neighbor expansion and answer quality.
- Changing production retrieval. RQ1 measures; fixes follow from its results.
