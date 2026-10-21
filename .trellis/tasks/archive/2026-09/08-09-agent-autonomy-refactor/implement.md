# Implementation Plan: Agent Bounded Autonomy Refactor

## 0. Planning and behavior baseline

- [ ] Review and approve `prd.md` and `design.md`; do not start code while the
      task remains `planning`.
- [ ] Add a small synthetic, privacy-safe behavioral fixture sufficient to run
      deterministic and basic smoke checks. User-owned real conversation texts
      are an optional post-implementation acceptance input, not a coding gate.
- [ ] Add synthetic cases for greetings, thanks, capability questions,
      clarification, ordinary knowledge questions, follow-up references,
      explicit URLs, inventory→retrieval, pending confirmation, mutation and
      Citation-bypass attempts, plus transient reads, empty search, missing
      context, partial success, provider failure and answer repair.
- [ ] Give every corpus case explicit `should_use_todo` / `should_not_use_todo`,
      expected recovery actions, maximum retry/repair counts, and permitted
      partial-result behavior.
- [ ] Capture the current flag-off baseline: status, provider requests, tool
      sequence, Citation/action outcome, latency and token use.
- [ ] Define pass/fail rules for missed retrieval and unnecessary retrieval
      before prompt tuning begins; do not tune only against a few favorable
      examples.

Rollback gate: this phase changes only planning/evaluation assets. If the
corpus cannot distinguish desired knowledge-assistant behavior from general
chat, return to product clarification rather than implementing a router by
assumption.

## 1. Provider and output spike

- [ ] Prototype the current PydanticAI model with normal tool calls followed by
      natural text containing `[S<id>]` markers; verify `TestModel`,
      `FunctionModel`, the configured real provider and compatible gateway.
- [ ] Verify output/tool retry behavior with the current sequential execution,
      `parallel_tool_calls=False`, request/tool/output limits and timeout.
- [ ] Spike whether a safe read-tool `ErrorEnvelope` can return to the same
      Agent run for a policy-granted next action without leaking exception data
      or relying on one provider's non-standard behavior.
- [ ] Compare one in-run marker repair with one fresh tool-free repair against
      captured evidence; select the smaller provider-compatible path while
      keeping one total repair.
- [ ] Prove that a social/no-tool response needs no Composer request and that a
      grounded failure can still use the deterministic evidence fallback.
- [ ] Record the spike decision in task research before changing the production
      path.

Rollback gate: if the configured provider cannot reliably finish natural text
after tools, retain the existing Composer for grounded answers and limit the
first implementation to no-tool conversation plus resolved-context handoff.

## 2. Internal context and turn-runtime contracts

- [x] Add focused internal types for bounded prior sources, inventory ordering,
      current explicit references, pending snapshots and same-run read-only
      observations. Do not add tenant/thread/claim fields to model tool schemas.
- [x] Define internal Todo, safe error, recovery grant, and recovery ledger
      types independently from public `AgentAnswer` and durable action types.
- [x] Extract a `ContextBuilder` from ChannelService/conversation helpers that
      projects only current-tenant completed turns and existing public fields.
- [x] Derive prior focus from existing `ConversationTurn.sources` and bounded
      `action_results`; add no migration or long-term profile.
- [x] Extend the internal `AgentRequest` compatibly and keep public
      ChannelEnvelope/MCP/AgentAnswer schemas stable.
- [x] Add tests for empty context, ordinal focus, stale/missing items, `/new`,
      restart recovery, token/turn bounds, MCP management-marker exclusion and
      cross-tenant isolation.

Rollback point: ContextBuilder can be ignored by the flag-off path without
changing stored turns.

## 3. Turn-scoped Todo store and tool

- [x] Implement an in-memory `TurnTodoStore` owned by one Agent `run()` and an
      optional `todo_write` tool that replaces/returns a normalized snapshot.
- [x] Validate at most six unique nonempty items, the four allowed statuses,
      and at most one `in_progress`; reject sensitive identifiers, URLs,
      payload/evidence text, oversize fields, and invalid state transitions.
- [x] Keep Todo out of `AgentRequest` history, `AgentAnswer`, ConversationTurn,
      database models, diagnostics and production logs. Verify restart and next
      turn begin with no Todo state.
- [x] Add finalization checks: normal completion has no `pending` or
      `in_progress`; `blocked` must correspond to trusted missing-context or
      unavailable-remainder state; terminal `ActionOutcome` bypasses Todo prose
      and remains canonical.
- [x] Prompt and test selective use: multi-step list→resolve→retrieve→answer
      should use Todo; greetings, thanks, capability questions, a single
      clarification and single-tool requests should not.
- [x] Count `todo_write` against existing raw tool/request limits; add loop and
      six-item boundary tests without creating a new budget or workflow engine.

Rollback point: remove the turn-local tool/store from the flag-on Agent. No
persisted data or business action needs repair.

## 4. Separate read observations from terminal actions

- [x] Introduce an internal bounded read-observation result/cache for
      `list_saved_items` and `get_saved_item` instead of setting the one
      terminal mutation outcome.
- [x] Record trusted ordered item references from current-run inventory results
      for later same-turn selection. IDs remain references; every later service
      read repeats tenant/deleted/state checks.
- [x] Allow read-only inventory→detail/retrieval sequences within existing hard
      limits.
- [x] Keep save submission, update, delete request/confirm/cancel, restore and
      retry on the existing terminal `ActionOutcome` path. Mixed mutation plus
      further work remains out of scope.
- [x] Preserve bounded canonical inventory persistence needed for “next page”
      and “第二个” follow-ups.
- [x] Add deterministic tests for inventory-only, list→detail,
      list→scoped-search, mixed provider tool batches, mutation-wins behavior
      and duplicate replay.

Rollback point: flag-off retains the current terminal inventory behavior; no
Domain Service or database rollback is needed.

## 5. State-based ToolsetPolicy

- [x] Group existing tools by retrieval, inventory read, save, pending save,
      management mutation and pending delete without introducing executor
      classes.
- [x] Hide confirm/cancel/clarify tools unless a matching trusted pending
      snapshot is active; preserve kind isolation and raw-message confirmation
      validation.
- [x] Preserve feature flags and exact-reference restrictions. A model-selected
      item scope from inventory must be validated against current/prior trusted
      observations and intersect, never replace, explicit current-message
      scope.
- [x] Keep the first version free of semantic regex routing, a second model
      router, `select_capabilities`, arbitrary tool search and new external
      tools.
- [x] Assert exact tool schemas and visibility for base, pending, explicit URL,
      feature-disabled, read-only and mutation-ready cases.

## 6. Safe errors and bounded recovery

- [x] Map expected tool/runtime failures to allow-listed `ErrorEnvelope`
      categories and stable public codes. Never expose exception text, provider
      body, URL/query, tenant/thread/item/pending/claim IDs, SQL, stack, tool
      payload or evidence in the envelope.
- [x] Implement turn-local `RecoveryLedger` counters and server-side exact-call
      fingerprints, plus `RecoveryPolicy` grants derived from operation class,
      trusted trace, error category and remaining global budgets.
- [x] Enforce one exact same-argument retry per read call, two total recovery
      actions per turn, one answer repair that also counts toward the total,
      zero autonomous mutation/confirmation retries, and zero Agent retry after
      provider/model failure.
- [x] Treat empty search as an observation; allow query reformulation only when
      both the existing search budget and whole-turn recovery budget remain.
      Do not consume a same-call transient retry for reformulation.
- [x] Support safe choices for read failure: exact retry when granted, use
      existing evidence, return supported partial success, ask for missing
      context, or stable unavailable. Todo updates never grant these choices.
- [x] Preserve canonical policy/security, tenant/scope, deleted/non-ready,
      pending confirmation and side-effect-in-progress/unknown outcomes; the
      Agent cannot recover by changing parameters or selecting another tool.
- [x] Keep explicit user-requested ingestion retry on the existing validated,
      idempotent terminal action path. On the flag-on path, failed mutation tool
      attempts receive no recovery grant; pre-side-effect input mismatch remains
      fail closed rather than becoming a generic retry mechanism.
- [x] Add deterministic tests for transient read retry success/exhaustion,
      per-fingerprint and whole-turn ceilings, empty-result reformulation,
      missing-context clarification, partial success, policy denial, budget
      exhaustion, mutation/provider no-retry, and ledger non-persistence.

Rollback point: typed read errors can map back to the current stable failures;
durable action and Domain Service state are unchanged.

## 7. TurnAgent natural response path

- [x] Update the main tool-using Agent instructions so the model decides
      whether ordinary messages need private-knowledge tools, may respond
      socially or clarify without tools, and must never claim retrieval it did
      not perform.
- [x] Keep the assistant scoped to private knowledge and product capabilities;
      do not prompt it as a general world-knowledge assistant.
- [x] Return natural text from the TurnAgent. Do not require the model to emit a
      complex answer mode/section schema solely for Citation parsing.
- [x] Infer conversation/read/grounded/action handling from trusted tool traces.
- [x] Preserve deterministic grounding obligation for explicit URL content
      questions and successful knowledge-tool runs.
- [x] Ensure the terminal action outcome always discards model prose and wins.
- [x] Add the default-off `AGENT_BOUNDED_AUTONOMY_ENABLED` setting, validation,
      example environment documentation and flag-off compatibility tests.

Rollback point: one setting restores the old planner/Composer/search-required
path.

## 8. Inline Citation validator and answer rendering

- [x] Implement a strict parser for `[S<positive segment id>]`; reject malformed
      server-like markers, model-authored URLs and source blocks.
- [x] For grounded runs, require at least one marker, validate every marker
      against the current-run Citation cache and exact scope, enforce source
      item limits and render URLs/excerpts server-side.
- [x] For no-tool/read-only runs, reject Citation markers and source claims; do
      not construct a grounded source block.
- [x] Perform at most one provider-compatible repair using the same evidence
      allow-list and no retrieval tools, only when RecoveryPolicy grants it and
      with the repair counted in the two-action turn ceiling. Invalid/timeout/
      limit/provider failure falls back to original trusted evidence without an
      Agent-level provider retry.
- [x] Never persist invalid drafts, raw tool results, repair prompts or
      provider payloads in ConversationTurn or production logs.
- [x] Add tests for missing markers, forged/current-history IDs, duplicate IDs,
      wrong exact scope, more than five items, malformed markers, model URLs,
      repair success/failure and deterministic fallback.

## 9. Diagnostics and persistence

- [x] Add only allow-listed fixed diagnostics needed to distinguish
      no-tool/read/grounded/action outcomes, error category, recovery outcome,
      Todo-used boolean, repair/recovery counts and safe numeric usage. Preserve
      production redaction and never log Todo content or retry fingerprints.
- [x] Persist the canonical visible answer, selected trusted sources and
      bounded read-only context using existing fields.
- [x] Re-run restart, `/new`, context token budget, duplicate message,
      exactly-one-reply and MCP marker-history tests.
- [ ] Scan logs and persisted model messages with sensitive sentinels for
      question/history, tool/action payloads, evidence, URLs, identities,
      provider bodies and secrets.

## 10. Verification and rollout

- [x] Run focused tests for Agent runtime, actions, pending confirmation,
      conversations, exact reference, item management and provider settings.
- [ ] Run ChannelService, HTTP gateway, LangBot bridge, MCP and multi-user
      regressions.
- [ ] Run the full offline suite; run PostgreSQL/HTTP integration in an
      available disposable environment and record unavailable checks honestly.
- [ ] Execute the deterministic corpus and a basic flag-off/flag-on smoke with
      an available configured model/provider; document any unavailable external
      environment instead of blocking the code path.
- [ ] Report correct zero-tool rate, missed/unnecessary search, clarification,
      Todo use/non-use, read-only composition, recovery/partial success, retry
      ceilings, Citation validity, action parity, provider calls, tool calls,
      latency and token use.
- [ ] Perform CLI/MCP read-only smoke, then one controlled Telegram/WeChat
      channel smoke with exactly one reply per message.
- [ ] Hand the flag, run commands, and observed smoke results to the user for
      overall real-model effect testing; do not enable the flag by default until
      the user accepts that result.

## 11. Spec, commit and finish

- [x] Update `.trellis/spec/backend/agent-retrieval-convergence.md` with the
      approved model-selected grounding, natural marker validation,
      observation-vs-terminal rules, turn-scoped Todo, bounded RecoveryPolicy,
      retry ceilings and rollout contract.
- [x] Update any management/logging/channel spec only when its actual contract
      changes; do not copy task prose into unrelated specs.
- [x] Run final task validation and repository status review.
- [ ] Commit implementation and planning/spec changes using the Trellis finish
      flow, then archive only after behavioral and safety acceptance is complete.

## Verification record (2026-08-09)

- Final focused Agent/autonomy/context/recovery/actions/management/exact-reference/
  knowledge/diagnostics/multi-user run: `167 passed, 16 skipped`.
- Repository offline run with unavailable external/sandbox cases excluded:
  `275 passed, 41 skipped, 1 deselected`.
- Repository-wide attempt reached `277 passed, 41 skipped`; two loopback HTTP
  cases could not bind a local socket in the sandbox, nine PostgreSQL cases
  could not resolve the configured remote database, and two unchanged baseline
  assertions (MCP inspect timeout and shell-environment feature default) failed.
- `py_compile`, `git diff --check`, and Trellis context validation passed.
- Real provider/model evaluation, live CLI/MCP/channel smoke, PostgreSQL
  integration, rollout enablement, commit, and archive remain open.
- Review debt retained for a later scoped change: answer repair enforces the
  1/2 recovery ledger ceilings but does not mechanically subtract primary-run
  provider/request usage; `app/agent/runtime.py` also remains oversized and
  should be decomposed only in a separately reviewed behavior-preserving task.
