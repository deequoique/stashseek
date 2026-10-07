# Design: no fail-closed on server-resolvable answer violations

## Boundary

- Changes:
  - `app/agent/answer_pipeline.py`: stream guard, plan validator, draft
    validator / `_draft_failure_reason`, result assembly in `recover_answer`
    and `stream_answer`
  - `app/diagnostics.py`: one new stage
  - tests
  - `.trellis/spec/backend/agent-retrieval-convergence.md`: the contract
    changes intentionally
- Unchanged:
  - retrieval
  - the primary agent
  - `validate_natural_answer` itself
  - unknown-ID, too-many-items and missing-scope-item handling
  - URL/HTML/marker/source-block rejection

## A. Stream guard and whitespace (R1)

`_StreamingTextGuard._has_forbidden_text(value)` returns `False` when
`value.strip() == ""`. Otherwise it delegates to `validate_natural_answer`
exactly as today.

- `feed()`: a whitespace-only candidate is not forbidden. It flows through the
  normal hold-back logic and becomes part of `_parts`, preserving Markdown
  paragraph breaks.
- `flush()`: same rule for the tail.
- The final `validate_natural_answer(guard.text)` after a section's stream
  still enforces "not empty" on the assembled text. A section that streamed
  only whitespace still fails, which is correct.

## B. Citation normalization (R2, decision F1)

One pure helper is shared by both paths:

```python
def normalize_section_citations(
    sections: Sequence[Sequence[int]],  # per grounded section, model order
    cap: int,                          # COMPRESSED_EVIDENCE_LIMIT (8)
) -> tuple[list[list[int]], NormalizationReport]:
    ...
```

1. Within a section, drop repeated IDs and keep the first occurrence.
2. Across sections, duplicates are allowed. The cap counts distinct IDs over
   the whole answer.
3. If distinct IDs exceed `cap`, round-robin:
   - Pass `p = 0, 1, 2, …` takes each grounded section's `p`-th ID in section
     order.
   - An already-kept ID is kept again at no cost to the cap.
   - A new ID is kept only while distinct < `cap`.
   - Section citation lists keep their original relative order.
4. Grounded sections are ≤8 by schema (`app/agent/types.py`), so every
   grounded section keeps ≥1 ID.
5. Return `NormalizationReport(deduplicated: bool, clamped: bool,
   kept_distinct: int, dropped_distinct: int)`.

Applied in:

- **Stream plan** (`build_stream_plan` → `validate_stream_plan`):
  1. First reject unknown IDs (unchanged).
  2. Then normalize, and return a *new* `AnswerStreamPlan` with the normalized
     `citation_ids`.
  3. Then run the items/scope checks on the normalized selection.

  Section text is generated afterwards from the locked citations, so the stream
  path stays exact.
- **Composer** (`build_composer` → `validate_draft` / `_draft_failure_reason`):
  1. Check unsafe text, missing citation and unknown citation (unchanged).
  2. Normalize, and return a new `AnswerDraft` with the normalized IDs.
  3. Run the items/scope checks on the normalized selection.

  `duplicate_citation` and `too_many_segments` are no longer failure reasons
  produced by validation. Their guidance strings stay in
  `_COMPOSER_FAILURE_GUIDANCE` for compatibility.
- **Result assembly**: wherever the code later re-derives `selected` from
  section IDs (`recover_answer` after a successful run, and `stream_answer`
  building `selected`), de-duplicate by segment ID for the citations list.
  Sections keep their own (possibly shared) IDs for marker rendering.

## C. Diagnostics (R3)

- Add stage `"citation_normalized"` to `_STAGES` in `app/diagnostics.py`.
- Emit it once per normalized plan or draft:
  - `agent_phase="answer"`
  - `failure_reason` = `"too_many_segments"` if clamped, else `"duplicate_citation"`
    (both already allow-listed)
  - `result_count` = kept distinct
  - `retry_count` = dropped distinct

  No IDs or text are logged.

## Contract and spec change

`agent-retrieval-convergence.md` currently states that a too-many-segments
selection is rejected and consumes an attempt. It will instead say:

- Duplicates and over-cap selections are normalized server-side by section
  round-robin, and a diagnostic event records it.
- On the composer path, a dropped citation's sentence is attributed to the
  section's remaining citations (accepted risk).

Done in Phase 3.3 (spec update) of this child.

## Risks

- `tests/test_agent_runtime.py` cases around lines 840-880 and 1052 assert the
  old failure/retry behavior. They must be rewritten to assert normalization,
  not deleted.
- Media citations (`media_citation_ids`) share the cap today. The round-robin
  applies only to segment IDs; any existing media rules stay as they are.
  Mixed media+segment over-cap keeps the current failure, since it is out of
  scope and has no observed failures.

## Rollback

Code-only revert of `answer_pipeline.py` and `diagnostics.py`. The new stage
string is additive.
