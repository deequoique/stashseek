# 修复分块：语义优先切分 + 下限合并 + 块间重叠

Parent: `10-06-meeting-qa-quality`. Must be implemented and checked before
`10-06-rq1-retrieval-quality` builds its index.

## Goal

Production chunks of spoken transcripts should be retrieval-sized units, not
single sentences. Semantic (and, as fallback, gap or punctuation) boundaries
choose *where* to cut. A lower bound prevents fragments, and adjacent chunks
overlap.

## Background (confirmed, 2026-10-06)

- `chunk()` (`app/ingest/chunker.py:97-136`) has this priority: chapters →
  gap/punct boundaries → semantic local minima → `_hard_cut`.
  - `_hard_cut` targets 60 s or 170 English words / 280 CJK chars, with 15%
    overlap and a 120 s ceiling.
  - `_valid_signal_chunks` splits at *every* boundary and only rejects the
    whole set if a piece exceeds 120 s. There is no lower bound and no merge.
  - The `duration / 180` boundary count is a minimum, not a maximum.
- Measured on 20 QMSum meetings ingested via `process_item` from a caption
  track (≤12 words/cue, new cue per speaker turn): 10,161 segments, median 12
  words, 32% ≤5 words, 0 hard cuts.
  - AMI/ICSI take the punct path.
  - Committee meetings take punct+semantic, median 24 words.
- The semantic path embeds every cue. It is enabled for server-fetched items
  and for browser captures with ≤512 cues (`app/ingest/tasks.py:409-416`).
- Embedding budget: `ingest_max_embedding_chars_per_item` = 2,000,000, while
  text is capped at 1,000,000 (`app/config.py:389-397`).
- The answer stage shows the model only the first 360 chars of each segment:
  - `COMPOSER_EVIDENCE_EXCERPT_CHARS`, `app/agent/runtime_state.py:32`
  - a literal `360` at `app/agent/answer_pipeline.py:652`
- Citation links jump to `segment.start_sec` (`app/browser_capture.py:254`).
- `segment.boundary_kind` is free `Text`, so no migration is needed.
- `tests/test_chunker.py` encodes split-at-every-boundary behavior.
  `docs/explanation/ingestion-and-retrieval.md` describes chunking.

## Decisions (user, 2026-10-06)

- C1 Semantics first; merge fragments up to a lower bound; overlap adjacent
  chunks. The fix is "raise the lower bound", not "cut at a fixed target".
- C2 After reaching the lower bound, cut at the **deepest semantic-similarity
  dip** between the lower and upper bound, not at the first boundary.
- C3 Parameters:

  | Parameter | English | CJK | Notes |
  |---|---|---|---|
  | Lower bound | 80 words | 130 chars | ≈30 s |
  | Upper bound | 200 words | 330 chars | Never more than 120 s |
  | Overlap | trailing ≈15% of the previous chunk's cues | same | Whole cues, at least 1 cue |
  | Composer/planner excerpt cap | 1,200 chars (was 360) | same | — |

- C4 Accepted cost: every cue is embedded whenever the semantic embedder is
  available. The >512-cue browser-capture guard is unchanged.
- C5 New ingests only. No backfill of existing production items. A later,
  separate task may add an operator re-chunk command that also handles stale
  segment IDs.

## Requirements

- R1 Semantic dips are the primary cut criterion. Gap and punctuation
  boundaries are the fallback when no embeddings exist (the >512-cue capture
  path, the preflight call, or the budget guard in R5).
- R2 No cut before the lower bound. Within the window
  [lower, min(upper, 120 s)], cut at the deepest TextTiling-style similarity
  dip (C2).
  - **Refinement, pending approval:** when a sentence-end or gap position has
    ≥50% of the window's deepest depth, prefer it. This avoids cutting
    mid-sentence, which keeps evidence sentences whole inside one chunk.
  - Without embeddings: the largest gap ≥2 s, else the sentence end nearest
    the window middle.
  - No candidate at all → the existing hard cut.
- R3 Tail merge: a final piece below the lower bound merges into the previous
  chunk while the result stays ≤ upper and ≤120 s.
- R4 Overlap: each next chunk starts `max(1, round(0.15 × cues))` whole cues
  before the previous chunk's end, and always advances by ≥1 cue.
- R5 Budget guard: if per-cue embedding plus overlapped chunk embedding cannot
  fit `ingest_max_embedding_chars_per_item`, skip semantic boundaries and
  chunk on gap/punct instead of failing the ingest.
- R6 Excerpt cap 1,200 (C3), applied both to the constant and to the literal
  at `answer_pipeline.py:652`.
- R7 Unchanged contracts: chapters (≤180 s → one chunk), the 120 s ceiling,
  language units, the >512-cue capture rule, the preflight segment-limit
  check, and `boundary_kind` labels.
- R8 Tests and `docs/explanation/ingestion-and-retrieval.md` describe the new
  behavior.

## Acceptance Criteria

- [ ] `tests/test_chunker.py` covers:
  - a dense-punctuation track merges to ≥ lower bound
  - deepest-dip choice
  - the sentence-end preference rule
  - gap preference without embeddings
  - hard-cut fallback ≤120 s
  - overlap of whole cues with guaranteed advance
  - tail merge and no-merge
  - CJK bounds
  - chapters unchanged
- [ ] `tests/test_tasks.py` keeps the semantic call-shape assertions (≤512 / >512
      cues) and adds a budget-guard test. The full suite does not regress.
- [ ] Offline check on caption tracks of the 20 meetings (no embedder): median
      chunk words between 80 and 200, and max duration ≤120 s.
- [ ] Docs updated.

## Out of Scope

- Speaker labels.
- Backfilling or re-chunking existing items (C5).
- Retrieval or fusion changes (RQ1 factors).
