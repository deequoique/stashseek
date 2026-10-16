# Design: semantic-first chunking with lower/upper bounds and cue overlap

## Boundary

- Changes:
  - `app/ingest/chunker.py` (algorithm)
  - `app/ingest/tasks.py` (semantic embedder budget guard only)
  - `app/agent/runtime_state.py` and `app/agent/answer_pipeline.py` (excerpt cap)
  - `tests/test_chunker.py`, `tests/test_tasks.py` (expectations)
  - `docs/explanation/ingestion-and-retrieval.md`
- Unchanged:
  - DB schema (`segment.boundary_kind` is free `Text`; labels stay
    `chapter|gap|punct|semantic|hard_cut`)
  - connectors
  - retrieval and search
  - existing production segments (no backfill, C5)

## Public contract of `chunk()`

Signature unchanged:
`chunk(cues, *, lang, chapters=None, semantic_embedder=None) -> list[Chunk]`.

Guarantees after the change:

1. Every chunk spans ≤120 s (unchanged ceiling).
2. Except for a whole-transcript chunk shorter than the lower bound, every
   chunk has ≥ `LOWER` units:
   - EN 80 words, CJK 130 chars (`_units`)
   - this includes overlap cues
3. Every chunk has ≤ `UPPER` units (EN 200 / CJK 330), unless a single cue
   alone exceeds it.
4. Chunks are contiguous cue ranges. Chunk *i+1* starts `k` cues before the end
   of chunk *i*, where `k = max(1, round(0.15 * cues_in_chunk_i))`, capped so
   that the next chunk still advances by ≥1 new cue. The final chunk covers
   the last cue.
5. Chapters: a chapter ≤180 s stays one `chapter` chunk (unchanged). Longer
   chapters recurse into the algorithm below.

## Algorithm

Inputs per call: `cues`, `lang`, and optionally the per-cue embeddings.

1. **Candidate cut points.** For each index `j` (cut before cue `j`,
   `1 ≤ j < n`):
   - `gap[j]` = pause `cues[j].start - cues[j-1].end` (gap boundary when ≥2.0 s,
     as today).
   - `sent[j]` = `cues[j-1]` ends with sentence punctuation (regex as today).
   - `sim[j]` = cosine(emb[j-1], emb[j]) when embeddings exist.
   - `depth[j]` = TextTiling depth: `(peak_left - sim[j]) + (peak_right - sim[j])`,
     where `peak_left` / `peak_right` are the nearest local maxima of `sim`
     on each side. Without embeddings, `depth` is undefined.
2. **Greedy window walk.** From `start`, extend `end` cue by cue until the chunk
   `[start, end)` reaches `LOWER` units. The window `W` is every `j > start`
   where `[start, j)` stays within all three limits: ≥ `LOWER` units,
   ≤ `UPPER` units, ≤120 s.
3. **Pick the cut in `W`.** The ranking key is lexicographic.
   - With embeddings:
     1. `depth[j]`, deepest first (the user's rule C2: deepest semantic dip)
     2. tie-break: `j` is a sentence end or gap
     3. earlier `j`

     To avoid mid-sentence cuts, sentence-end/gap candidates are preferred
     whenever their depth is ≥ 50% of the deepest depth in `W`.
   - Without embeddings:
     1. largest gap ≥2 s
     2. else the sentence end closest to `(LOWER+UPPER)/2`
     3. else the end of `W`
   - Label the chunk by the winning signal (`semantic` / `gap` / `punct`).
   - If `W` is empty because one cue range jumps past both limits before
     reaching `LOWER`, cut at the last cue that keeps ≤120 s and label
     `hard_cut`.
4. **Advance with overlap.** The next `start = cut - k` (rule 4). Repeat until
   all cues are consumed.
5. **Tail merge.** If the final chunk has < `LOWER` units and merging it into
   the previous chunk keeps ≤ `UPPER` units and ≤120 s, merge it. Otherwise
   keep it.

`semantic_boundary_indices` stays exported for compatibility, but is no longer
used by `chunk()`. `_hard_cut` stays as the fallback building block.
`_valid_signal_chunks`, `_split_at` and the `duration / 180` required-count
gate are removed from the main path. They are what caused split-at-every-
boundary.

## Semantic embedder usage and cost guard (`app/ingest/tasks.py`)

- Semantic-first (C4): when a semantic embedder is passed, embed every cue.
  This is the only new cost. The >512-cue browser-capture rule stays as it is.
- The preflight `chunk()` call (no embedder) keeps working. It is
  deterministic on gap/punct and yields a comparable chunk count for the
  `ingest_max_segments_per_item` check.
- Budget guard: `ingest_max_embedding_chars_per_item` defaults to 2,000,000;
  text is capped at 1,000,000.
  - Cue embedding plus overlapped chunk text is ≈2.15× transcript chars.
  - Before calling the semantic embedder, if
    `cue_chars + 1.2 * cue_chars > remaining_embedding_chars`, skip semantic
    boundaries and chunk on gap/punct. This is graceful degradation instead of
    `IngestLimitExceeded`.
  - The cost guard value is not changed.

## Excerpt cap (C3)

- `COMPOSER_EVIDENCE_EXCERPT_CHARS = 1200` (`app/agent/runtime_state.py:32`).
- `_section_text_stream` currently hard-codes `excerpt_chars=360`
  (`app/agent/answer_pipeline.py:652`). It should use the same constant.
- No other consumer truncates segment text for the model. Tool results already
  carry the full excerpt.

## Compatibility and risks

- Existing production segments keep their old shape (C5). New and old items
  coexist; retrieval code is agnostic to chunk size.
- The `get_neighbors` / `open_at` semantics are unchanged. Overlap means
  adjacent segments share 1+ cues, so neighbor expansion returns some repeated
  text. This is acceptable and already true for hard cuts.
- Ingest latency and embedding cost rise for punctuated tracks (every cue now
  embedded). This is user-acknowledged (C4).
- `tests/test_tasks.py` asserts the semantic embedder call shape for
  ≤/>512 cues. Those expectations hold: one per-cue call, then one chunk call.
  Tests asserting split-at-every-punct counts must be rewritten to the new
  contract, not deleted.

## Rollback

The change is a pure code revert of `chunker.py` plus the constant. It needs no
data migration, and segments already written with the new shape remain valid.
