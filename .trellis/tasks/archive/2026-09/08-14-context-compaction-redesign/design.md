# Context Compaction Redesign

## 1. Problem and Design Principles

The current runtime has two unrelated bounds: conversation history is truncated
to a recent window before a run, while Composer evidence is compressed only
after output-length exhaustion. The redesign introduces two explicit layers:

1. durable cross-request compaction for old canonical conversation turns; and
2. deterministic in-run projection of redundant tool results before each model
   request.

Compaction reduces model-visible context only. It never mutates server-owned
authorization, tenant scope, pending actions, Citation caches, recovery grants,
or action outcomes. Recent user/assistant semantics take priority over tool
payload detail.

## 2. Boundaries

### 2.1 Durable conversation layer

`ChannelService` obtains a bounded history projection before constructing
`AgentRequest`. A new conversation-compaction service owns checkpoint reads,
summary generation, compare-and-swap persistence, and fallback. The existing
`load_message_history()` remains the deterministic flag-off/failure path.

Only completed, non-MCP-management turns from the same tenant-bound thread may
enter a summary. Canonical turns already exclude planner drafts and tool
payloads.

### 2.2 In-run tool layer

The primary PydanticAI Agent receives a request-local `ProcessHistory`
capability. Before each provider request, the processor estimates the actual
message projection. When the soft threshold is exceeded, it replaces eligible
`ToolReturnPart` content with server-generated bounded projections while
preserving tool-call IDs, pairing, ordering, status, and required state.

The processor does not rewrite the original runtime state or persisted
history. Composer remains tool-free and keeps its independent Citation
compression behavior.

## 3. Durable Data Model

Add one optional row per thread:

```text
conversation_compaction
  thread_id                 PK, FK conversation_thread.id ON DELETE CASCADE
  summary                   JSONB, validated structured summary
  covered_through_turn_id   BIGINT, FK conversation_turn.id
  source_turn_count         INTEGER
  format_version            INTEGER
  revision                  INTEGER, optimistic concurrency token
  created_at                timestamptz
  updated_at                timestamptz
```

The structured summary contains bounded fields for goals/constraints,
confirmed facts, important item references, completed outcomes, unresolved
questions, and recent focus. It contains no raw tool payload, provider error,
secret, authorization grant, or current Citation allow-list.

`covered_through_turn_id` is the checkpoint: every summarized turn for that
thread is at or before the checkpoint, and later raw turns remain eligible for
the recent window or the next incremental summary. Old threads need no
backfill; their first over-budget request creates the row lazily.

The Alembic change must preserve the repository's single migration head.

## 4. Durable Compaction Flow

1. In a short read transaction, load the current checkpoint and completed
   turns in stable `(created_at, id)` order.
2. Build the normal recent raw window. Compaction is skipped when it fits below
   the configured trigger.
3. Select a contiguous prefix after the checkpoint for summarization, while
   reserving the configured number/token target of newest raw turns.
4. Close the transaction. Generate a typed summary from the previous summary
   plus only the selected canonical user/assistant turns, under a separate
   bounded request/output/time budget.
5. Validate the structure and enforce field/character limits. Summary content
   is data, never instructions or authorization state.
6. In a new short transaction, insert the initial row or update with a
   compare-and-swap on `revision` and the observed checkpoint. Never hold a
   database lock during a model request.
7. On conflict, discard the stale candidate and load the winner. On any model,
   validation, timeout, or database failure, keep the prior row unchanged and
   use the deterministic recent-window fallback.
8. Render model history as one server-labeled earlier-conversation summary,
   followed by recent canonical turns in chronological order. The current user
   message remains the run prompt and is never summarized before it completes.

Checkpoint advancement is monotonic and only covers a contiguous prefix.
Idempotent replay does not create a new completed turn and therefore cannot
advance the checkpoint twice. `/new` creates a different thread, so no summary
is inherited.

## 5. Tool Projection Policy

The processor is deterministic and idempotent. It changes the content of an
eligible tool result rather than removing one side of a tool exchange.

Priority order under pressure:

1. Collapse superseded duplicate read results and older todo snapshots to a
   fixed placeholder with safe status/count metadata.
2. Project retrieval evidence to the current server allow-list identifiers,
   titles/timestamps when needed, and shorter excerpts; keep the newest useful
   evidence for answer generation.
3. Project inventory/detail pages to identifiers and bounded canonical labels
   needed by current-run follow-ups.
4. Retain the latest expected error envelope and recovery action required for
   a legal retry; older resolved error details may become fixed placeholders.

Never project away:

- tool-call ID/name and the matching result part;
- pending decision snapshots and terminal mutation/action outcomes;
- exact-reference scope needed by current-message URL handling;
- the current Citation allow-list or evidence required by the next answer;
- the active recovery grant/fingerprint semantics;
- unfinished todo dependencies;
- protocol-required deferred or retry parts.

If no safe projector exists for a tool, keep the original bounded result. A
generic character slice is not an acceptable fallback because it can corrupt
typed JSON or remove security-relevant fields.

## 6. Budgets and Configuration

Introduce an independent rollout flag and bounded tuning values for:

- enabling layered context compaction;
- soft trigger and post-compaction target;
- number/token target of recent raw turns;
- summary provider output cap and timeout;
- maximum structured-summary field sizes.

Settings validation requires positive finite values, target below trigger, and
summary timeout below the outer gateway reserve. Existing
`CONTEXT_MAX_TURNS`/`CONTEXT_TOKEN_BUDGET` remain the flag-off and failure
fallback. Existing Agent request/tool/output/wall-clock limits remain hard
ceilings.

Provider usage fields may be recorded as safe numeric observations and used to
calibrate estimation, but provider-neutral serialized-size estimation remains
the admission mechanism until equivalent usage semantics are proven across
providers.

## 7. Summary Trust Contract

The summarizer is tool-free and returns a typed schema. Its input contains only
the prior validated summary and canonical visible turns. The validator rejects
unknown fields, overlong values, instruction-like wrapper leakage, raw URLs or
secret-like fields where forbidden, and malformed item references.

The summary is conversational memory, not evidence. Historical item/segment
references may guide a new tenant-scoped read, but they never join the current
run Citation allow-list and never authorize mutation, confirmation, retry, or
scope expansion.

## 8. Diagnostics and Privacy

Use distinct fixed stages, for example:

- `history_compaction_triggered`
- `history_compaction_completed`
- `history_compaction_fallback`
- `tool_context_projected`

Events contain only safe numeric counts, before/after estimates, fixed reason,
format version, and conflict/fallback category. They must not contain summary
text, history, tool payloads, URLs, IDs, excerpts, prompts, or model output.
The existing `context_compressed` event remains reserved for Composer Citation
compression.

## 9. Compatibility, Rollout, and Rollback

- Flag off: use the existing history loader and unchanged Agent behavior.
- Flag on with no compaction row: lazily compact only after the trigger.
- Summary unavailable: fall back to the existing recent-window selection.
- Tool projection unavailable or unsafe: retain the original already-bounded
  tool result and allow hard safety limits to govern the run.
- Rollback: disable the flag. The additive table may remain unused; canonical
  turns stay authoritative and are never deleted or rewritten.

## 10. Important Trade-offs

- Synchronous lazy summary generation adds bounded latency to the first request
  that crosses a checkpoint. It avoids introducing a new background queue and
  always has a deterministic fallback.
- A dedicated row is clearer than a synthetic ConversationTurn and preserves
  audit, replay, sidebar, export, and message-id semantics.
- Per-tool typed projectors require more explicit code than generic truncation,
  but they preserve security and protocol correctness.
