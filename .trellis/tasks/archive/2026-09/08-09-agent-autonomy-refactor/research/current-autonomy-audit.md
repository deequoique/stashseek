# Current Agent Autonomy Audit

## Scope

This audit records the current execution shape and the constraints that the
bounded-autonomy refactor must preserve. It is based on the repository state on
2026-08-09 and the completed retrieval-convergence, management, and exact-video
incident work.

## Current runtime shape

```text
ChannelService
  -> trusted AgentRequest + bounded PydanticAI history
  -> KnowledgeAgent.run
       -> deterministic bare-URL pre-route
       -> one retrieval/action Agent with dynamically prepared tools
       -> terminal AgentActionRuntime outcome, or
       -> Citation cache
       -> separate tool-free Composer
       -> structured AnswerDraft validation / evidence fallback
  -> completed ConversationTurn persistence
```

The implementation registers up to eighteen tools in `app/agent/runtime.py`:
four retrieval tools, five save/pending tools, and nine optional item-management
tools. Visibility is partly dynamic, but instructions, preparation callbacks,
retrieval reservations, action state, Composer behavior, and fallbacks remain
concentrated in the same runtime module.

## Why the current behavior feels rigid

1. Every non-action outcome is rejected unless `search_segments` ran at least
   once. This converts greetings, capability questions, acknowledgements, and
   clarification into retrieval work or `search_required`.
2. The retrieval/action Agent sees conversation history, but its final semantic
   interpretation is discarded. The Composer sees the current raw question and
   bounded Citations, so a resolved follow-up such as “这个呢？” loses important
   context between stages.
3. `AgentActionRuntime` retains only one terminal outcome. Read-only inventory
   calls use that same terminal channel, preventing a natural same-turn
   inventory-to-retrieval sequence.
4. Tool convergence is expressed through fixed 5/2/3 counters and prepare
   callbacks. These are necessary hard ceilings, but they currently also stand
   in for semantic sufficiency.
5. Action prose is canonical and safe but intentionally fixed. The system does
   not distinguish safe conversational freedom from authoritative action
   rendering.
6. Deterministic tests emphasize isolation, limits, confirmation, and failure
   mapping. The original manual acceptance checklist still contains uncompleted
   real-model context-follow-up and multi-tool cases, so there is no stable
   behavioral baseline for “less rigid.”

## Current failure handling and recovery gap

The runtime has important failure mapping and fallback behavior, but almost no
Agent-level recovery mechanism:

- retrieval-phase timeout, usage-limit, embedding/retrieval unavailable,
  provider HTTP failure, not-found, and unexpected exceptions are mapped to
  stable public failures; if Citations already exist, timeout/limit can proceed
  to answer composition;
- the Composer validates structured Citation IDs, may repair an invalid draft,
  may compress evidence once for an output-length failure, and otherwise uses a
  deterministic evidence fallback;
- save input mismatch uses framework `ModelRetry` to force the exact current-
  message URL set; this protects one tool contract rather than providing a
  general recovery policy;
- duplicate message replay, pending confirmation replay, delete effect claims,
  and explicit failed-ingestion retry are durable business mechanisms outside
  the Agent loop.

What is missing is a safe typed error returned to the reasoning loop, a policy
that states which next actions are legal for each error class, and a shared
ledger that stops retries across tools/answer repair. Today most read failures
abort immediately; the model cannot choose a permitted partial result or one
bounded retry. Conversely, adding generic retries would be unsafe because
mutation/effect/provider failures have different semantics.

The refactor must retain the Composer repair/evidence fallback behavior until a
provider-compatible replacement is proven, and must not replace pending replay,
effect recovery, or canonical action retry with Agent reasoning.

## Safety controls that must not move into the model

- channel identity resolution and tenant injection;
- model-invisible tenant/thread/pending/claim identifiers;
- exact current-message URL scope and deleted/non-ready gates;
- Citation allow-list construction from current-run tool results;
- duplicate message replay and action idempotency;
- save/delete durable confirmation, one-time codes, TTL, anchors, and delete
  effect claims;
- request, tool, retrieval-stage, output-token, timeout, and result-size limits;
- canonical mutation outcome and production log redaction.

These controls were added after observed failures. In particular, an explicit
video B previously retrieved and described video A from stale history and
nearest-neighbor results. Prompt wording and session reset were insufficient;
server-owned exact-reference enforcement was required.

## Autonomy that can move back to the model

- whether an ordinary non-URL message needs private-knowledge retrieval;
- whether to answer socially, explain capabilities, or ask a clarification;
- search query formulation and whether more context is useful within hard
  limits;
- which trusted inventory result a phrase such as “第二个” refers to;
- read-only tool ordering within one bounded turn;
- natural answer wording and placement of current-run Citation markers.

## Selected direction

Use one primary PydanticAI Turn Agent. Do not add a separate semantic router or
capability executor graph in the first iteration. The model decides whether to
call knowledge tools. The server infers the validation path from actual tool
execution:

```text
terminal mutation outcome -> canonical action answer
knowledge search ran       -> natural text requiring current-run [S…] markers
read-only tools ran         -> bounded tool-supported natural answer
no tools ran                -> conversation or clarification
```

The grounded answer may be natural Markdown. A complex structured
`AnswerDraft.sections` schema is not required for tool calling or Citation.
The validator extracts inline markers, checks them against the current-run
Citation cache and scope, and renders the source block itself.

For genuinely dependent multi-step requests, add one optional turn-scoped
`todo_write` tool backed by an in-memory `TurnTodoStore`. It has at most six
`pending`/`in_progress`/`completed`/`blocked` entries and one `in_progress`
entry. It is model working memory only: no persistence, no production logging,
no authorization meaning, and no cross-turn task engine. Simple conversation
and single-tool requests should not use it.

Add a safe `ErrorEnvelope`, deterministic `RecoveryPolicy`, and in-memory
`RecoveryLedger`. The policy determines legal recovery choices, then the model
chooses among them. The agreed limits are one exact-call retry per read tool,
two recovery actions for the whole turn, one answer repair, zero autonomous
mutation retry, and zero Agent retry after provider/model failure. Answer repair
also counts toward the two-action turn limit, and all recovery shares the
existing request/tool/retrieval/timeout budgets.

Empty search is an observation rather than an exception and may trigger one of
the bounded query reformulations. Missing context leads to clarification and a
blocked Todo item. Policy/security failures and side effects in progress or
unknown stop on canonical server outcomes. Partial success preserves already
trusted observations/evidence and clearly identifies the unavailable remainder.

## Primary product trade-off

Once no-tool conversation is allowed, the server can no longer mechanically
prove that every semantic knowledge question was classified correctly without
reintroducing a blanket search rule or a separate classifier. The first
iteration accepts model-selected grounding as a quality decision while keeping
authorization and data access deterministic:

- a no-tool turn has no new knowledge-base evidence and cannot cross tenant;
- explicit URL questions retain server-owned scope obligations;
- a turn that activates or uses knowledge retrieval cannot finish without
  valid current-run evidence;
- a behavioral corpus measures missed-search and unnecessary-search rates;
- rollout remains flag-controlled until the real-model result is acceptable.

This is a reliability trade-off, not permission to relax tenant, destructive
action, or Citation-origin boundaries.

## Open implementation questions for the first spike

1. Whether the current provider set reliably supports a tool-using Agent that
   returns natural text with inline markers after multiple tool calls.
2. Whether grounded output validation should retry inside the same Agent run or
   perform one fresh, tool-free repair against the same evidence cache.
3. How to represent read-only observations separately from terminal
   `ActionOutcome` without changing public `AgentAnswer` or mutation behavior.
4. How much structured prior-turn focus can be derived from existing
   `sources`/`action_results` without adding a database column.
5. Whether state-based tool pruning is sufficient after the main behavior
   change, or a later lazy toolset-selection mechanism is justified by the
   behavioral corpus.
6. Whether PydanticAI's current tool-return and retry APIs can expose a safe
   `ErrorEnvelope` plus server-issued recovery choices without treating the
   envelope as an unhandled exception or adding provider-specific behavior.
7. Whether a full-snapshot `todo_write` schema yields reliable status
   convergence with the configured models while remaining absent from simple
   cases and within the current raw tool-call budget.

The implementation plan treats these as bounded spikes with rollback gates,
not as authorization to broaden the task into a framework migration.
