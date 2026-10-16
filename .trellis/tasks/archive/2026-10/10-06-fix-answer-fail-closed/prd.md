# 修复回答阶段 fail-closed：流式空白片段与引用超限/重复

Parent: `10-06-meeting-qa-quality`. Independent of RQ1; can run in parallel
with `10-06-fix-chunk-merge`.

## Goal

Answers whose evidence was retrieved successfully must not be thrown away for
reasons the server can resolve deterministically. Grounding stays strict: only
current-run allow-listed citations, and no model-authored URLs, markers or
source blocks.

## Confirmed Facts (2026-10-06 production-path rerun: 12/30 failed)

- **Bug A — whitespace delta aborts the stream (6/30).**
  - Code: `_StreamingTextGuard.feed`/`flush` (`app/agent/answer_pipeline.py:537-558`)
    calls `validate_natural_answer` on every `tail + delta` candidate. That
    function rejects whitespace-only text ("answer text must not be empty",
    `app/agent/answer_validation.py`), so a streamed `"\n\n"` or `" "` delta
    raises `NaturalAnswerValidationError("unsafe streamed text")`.
  - Effect: the section is aborted (`provider_failure`) and the whole answer
    becomes `answer_unavailable`.
  - Reproduced: `feed("B 认为实验设置")` then `feed("\n\n")` raises.
  - The old smoke harness used non-stream `agent.run()` and never hit this.
    The web default is `agent.stream()`.
- **Bug B — stream plan over-cites with no retry (3/30).**
  - Code: the `build_stream_plan` validator (`app/agent/answer_pipeline.py:~160-185`)
    raises `ModelRetry` with `retries={"output": 0}`, so a plan citing more than
    8 segments fails the answer immediately.
  - Section text is written later, only from the citations locked by the plan.
- **Bug C — composer fallback fails closed (3/30).**
  - Code: the non-stream composer path (`recover_answer`,
    `app/agent/answer_pipeline.py:~872-1000`). `_draft_failure_reason` rejects
    `too_many_segments` and `duplicate_citation`. Three fresh attempts with the
    same candidates fail the same way.
  - Section text is written together with its `citation_ids`.
- Schemas (`app/agent/types.py`): plans and drafts each have ≤8 sections and
  ≤8 `citation_ids` per section. The answer-wide cap is 8 distinct segments
  (`COMPRESSED_EVIDENCE_LIMIT`) across ≤5 items.

## Requirements

- R1 (Bug A) Whitespace-only stream deltas are buffered as text, never treated
  as forbidden. All other guard rules are unchanged: URLs, HTML, citation-like
  markers, media markers, source blocks, and the tail hold-back for split
  prefixes. The final non-empty check still runs on the assembled section text.
- R2 (Bugs B/C, user decision F1, 2026-10-06) Server-side normalization
  replaces fail-closed in both the stream-plan and the composer paths:
  - Duplicates are allowed. The same segment may support several sections. The
    8-segment cap counts distinct segments, and the source list stays
    de-duplicated.
  - Over-cap selections are clamped by **section round-robin**:
    - Pass 1 keeps each grounded section's first citation, pass 2 its second,
      and so on, until 8 distinct segments are kept.
    - Grounded sections are ≤8, so every grounded section keeps ≥1 citation.
    - Dropped IDs are removed from their sections.
  - Accepted residual risk (composer path only): text there is written together
    with its citations, so a sentence whose citation was dropped is attributed
    to the section's remaining citations. The stream path is exact, because
    section text is generated after the plan is locked.
  - Unknown citation IDs, too_many_items and missing_scope_item keep their
    current fail-closed behavior.
- R3 Diagnostics record when normalization changed the model's selection
  (a counted event, no model prose).

## Acceptance Criteria

- [ ] Regression tests:
  - whitespace-only deltas, alone and between text, stream to completion
  - forbidden-content deltas still abort
- [ ] Regression tests: a plan / draft with >8 or duplicate citations produces
      a grounded answer with ≤8 distinct segments, every grounded section keeping ≥1 citation (round-robin), plus one `citation_normalized` event.
- [ ] Existing answer-pipeline and streaming tests pass.

