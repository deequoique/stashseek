# Implement: semantic-first chunking

Implementer: `trellis-implement` sub-agent, model **sonnet**. Checker:
`trellis-check`.

## Ordered checklist

1. `app/ingest/chunker.py`
   - Add module constants:
     - `LOWER_UNITS = {"en": 80, "zh": 130}`
     - `UPPER_UNITS = {"en": 200, "zh": 330}`
     - `MAX_CHUNK_SECONDS = 120`
     - `OVERLAP_RATIO = 0.15`

     Language selection follows `_units` (`zh*` → CJK).
   - Implement the candidate scoring (`gap`, `sent`, `sim`, TextTiling `depth`)
     and the greedy window walk with overlap and tail merge, per
     `design.md` §Algorithm.
   - Keep `Chunk`, `semantic_boundary_indices`, `_hard_cut`, `_units`. Remove
     the split-at-every-boundary main path (`_valid_signal_chunks`, `_split_at`,
     the `duration/180` gate) only if nothing else imports them; grep first.
   - Chapters: unchanged behavior (≤180 s → one `chapter` chunk; longer →
     recurse).
2. `app/ingest/tasks.py`: the semantic budget guard (`design.md` §Semantic
   embedder usage). Do not change the >512-cue browser-capture rule, the
   preflight call, or the error mapping.
3. `app/agent/runtime_state.py`: set `COMPOSER_EVIDENCE_EXCERPT_CHARS = 1200`.
   `app/agent/answer_pipeline.py:652`: replace the literal `360` with the
   constant.
4. `tests/test_chunker.py`: rewrite the boundary tests to the new contract and
   add:
   - dense-punctuation EN track (sentence-per-cue): every chunk ≥80 words,
     except a short whole transcript; ≤200 words; ≤120 s.
   - semantic deepest-dip choice: synthetic embeddings with one deep dip inside
     the window → the cut lands there, `boundary_kind == "semantic"`.
   - sentence-end preference within 50% depth.
   - gap preferred without embeddings.
   - a long span with no boundary → `hard_cut`, ≤120 s.
   - overlap: consecutive chunks share `max(1, round(0.15·n))` whole cues and
     always advance.
   - tail merge within limits, and tail kept when a merge would exceed them.
   - CJK units (130/330).
   - chapters unchanged.
5. `tests/test_tasks.py`: keep the semantic call-shape assertions (one per-cue
   call + one chunk call; >512-cue capture skips per-cue). Add a test for the
   budget guard: when the budget cannot cover cues plus overlapped chunks, the
   semantic embedder is not called and ingest still completes.
6. Update `docs/explanation/ingestion-and-retrieval.md` "分块和 embedding" to
   describe: semantic-first, the lower/upper bounds, the window deepest dip,
   overlap, and the 120 s ceiling.

## Validation

```bash
.venv/bin/python -m pytest -q tests/test_chunker.py tests/test_tasks.py
.venv/bin/python -m pytest -q tests/test_agent_runtime.py tests/test_trusted_response_boundary.py
.venv/bin/python -m pytest -q
```

The full-suite run must not regress relative to the pre-change baseline.
Record any pre-existing failures before starting.

Offline size check (no provider calls; gap/punct path). Run the production
`chunk()` without an embedder over caption tracks built from the 20 QMSum
meetings (≤12 words/cue, new cue per speaker turn, 2.5 w/s) and report the
median, p10 and p90 words, and max seconds. Expected: median 80–200 words,
max ≤120 s. The semantic-path distribution is measured later by the RQ1
production-path ingest.

## Risky files / rollback points

- `app/ingest/chunker.py` is the single point of behavior change; revert the
  file to roll back.
- `tests/test_tasks.py` encodes ingestion call shapes; do not weaken the
  >512-cue cost assertion.
