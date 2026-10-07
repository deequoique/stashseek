# 会议问答质量：RQ1 前置修复与检索研究

## Goal

Fix the production defects found in the 2026-10-06 production-path rerun of
the QMSum specific-30 golden set. Then run the RQ1 retrieval-quality study on
the fixed system, so the study measures what production actually does.

## Background (2026-10-06 production-path rerun)

- Harness: real `create_item` + `process_item` ingest of a YouTube json3
  caption track. Web `agent.stream()`, fresh thread, one process per case.
- Result: 18/30 answered. 12 failures, 11 of them with gold evidence in the
  retrieved pool:
  - 6: stream guard aborted on a whitespace-only delta.
  - 3: stream plan cited more than 8 segments (0 retries).
  - 3: composer fallback failed on too_many_segments / duplicate_citation.
- Production chunking of the 20 meetings: 10,161 segments, median 12 words,
  32% ≤5 words, zero hard cuts.
- An embedding call raised an unwrapped `http.client.IncompleteRead`. In the
  worker this does not crash the process. It does permanently fail the item
  (`ingestion_failed`), as every embedding failure does, because embedding
  errors are never retried.

## Child Tasks

| Child | Deliverable | Order |
|---|---|---|
| `10-06-fix-chunk-merge` | Boundary signals become candidate cut points; chunks merge toward the documented target | Must land before RQ1 |
| `10-06-fix-answer-fail-closed` | Whitespace-only stream deltas never abort an answer. Too-many / duplicate citations no longer fail the answer | Independent of RQ1; parallel |
| `10-06-fix-embed-transient-errors` | Embedding failures are classified; transient ones are retried per batch inside the embedder | Independent; parallel |
| `10-06-rq1-retrieval-quality` | RQ1 study and deck material | After fix-chunk-merge |

## Cross-Child Requirements

- Code for every child is written by a `trellis-implement` sub-agent running
  model **sonnet**. Review and checks are done by `trellis-check`.
- Each fix child ships its own regression tests. Each is verifiable
  independently with `uv run pytest` on the touched test modules.
- The RQ1 index is built only after `fix-chunk-merge` is merged into the
  working tree.
- Not in scope:
  - item 5: retrieval-agent budget (skipped calls counted, thinking on, discarded primary answer).
  - item 6: AND query / raw-score fusion / diversification. These are RQ1 factors and are not fixed first.
  - item 7: possible async-generator leak across turns. Investigate separately.
  - item 8: speaker labels.

## Acceptance Criteria (cross-child)

- [ ] All three fix children are checked and their tests pass.
- [ ] The production-path rerun of the specific-30 set (same harness) is
      repeated after the fixes. It reports answer success, failure categories
      and chunk statistics alongside the 2026-10-06 numbers.
- [ ] RQ1 deliverables are produced on the post-fix index.
