# Implement: answer-stage fail-closed fixes

Implementer: `trellis-implement` sub-agent, model **sonnet**. Checker:
`trellis-check`.

## Ordered checklist

1. Stream guard whitespace fix (`design.md` §A) in
   `app/agent/answer_pipeline.py`.
2. Add `normalize_section_citations` and `NormalizationReport` (pure, module
   level) to `app/agent/answer_pipeline.py`, with unit tests:
   - no-op under the cap
   - in-section duplicate removal
   - a cross-section duplicate kept and counted once
   - round-robin with 3 sections × 5 IDs, cap 8 → each section ≥1 and
     distinct = 8
   - 8 sections × 3 IDs → one each
   - order preservation
3. Wire it into `validate_stream_plan` and `validate_draft` /
   `_draft_failure_reason` per `design.md` §B. Validators return the
   normalized model objects.
4. De-duplicate the final citation lists in `recover_answer` and
   `stream_answer` result assembly. Markers per section stay intact.
5. `app/diagnostics.py`: add `"citation_normalized"` to `_STAGES`. Emit per
   `design.md` §C.
6. Rewrite the affected `tests/test_agent_runtime.py` cases (around lines
   840-880 and 1052) to the new contract. Add end-to-end pipeline tests with a
   `FunctionModel` / stub covering:
   - stream plan citing 12 IDs → grounded answer with 8, plus one
     `citation_normalized` event
   - composer draft with a duplicate ID across sections → grounded answer with
     de-duplicated sources
   - stream section deltas `["文本", "\n\n", "更多文本"]` → completes
   - `["文本", "http://x"]` → still aborts
7. Update `.trellis/spec/backend/agent-retrieval-convergence.md` in Phase 3.3.

## Validation

```bash
.venv/bin/python -m pytest -q tests/test_agent_runtime.py tests/test_trusted_response_boundary.py
.venv/bin/python -m pytest -q -k "stream or composer or citation or diagnostics"
.venv/bin/python -m pytest -q
```

The full-suite run must not regress against the pre-change baseline. Record
pre-existing failures first.

## Rollback points

`app/agent/answer_pipeline.py` and `app/diagnostics.py`. Both revert cleanly
with no data impact.
