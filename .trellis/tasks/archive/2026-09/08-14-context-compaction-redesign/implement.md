# Implementation Plan

## 1. Data and configuration

- [ ] Add the `ConversationCompaction` SQLAlchemy model with one row per thread,
      structured JSON summary, monotonic checkpoint, revision, counts, version,
      and timestamps.
- [ ] Add an additive Alembic migration and update migration round-trip/head
      tests; do not backfill or rewrite existing turns.
- [ ] Add the rollout flag and validated trigger/target/recent-window/summary
      budgets to Settings and environment documentation.

## 2. Structured summary boundary

- [ ] Define strict Pydantic summary input/output types with field and total
      size bounds and a versioned renderer for model history.
- [ ] Build a tool-free summarizer with explicit provider max tokens, request
      limit, timeout, and no autonomous retry beyond the defined budget.
- [ ] Reject unsafe or malformed summaries and ensure summary references cannot
      enter current-run Citation or action authorization state.

## 3. Checkpoint service

- [ ] Implement stable contiguous-prefix selection after the current checkpoint
      while reserving recent raw turns.
- [ ] Generate outside database transactions, then persist with revision and
      checkpoint compare-and-swap.
- [ ] Handle first insert races, stale writers, restart, replay, `/new`, and
      summary failure by loading the winning row or using the existing bounded
      history fallback.
- [ ] Integrate the service into ChannelService before AgentRequest creation
      without changing tenant/thread lookup or duplicate-message behavior.

## 4. In-run tool history projection

- [ ] Add a request-local PydanticAI history processor/capability that estimates
      the messages immediately before every primary-Agent provider request.
- [ ] Implement typed projectors for retrieval, inventory/detail, todo, and
      expected error/recovery results; preserve unknown or security-sensitive
      tool results unchanged.
- [ ] Preserve every tool-call/result pair and the latest state required for
      citations, exact scope, pending actions, recovery, and unfinished work.
- [ ] Prove projection is deterministic, idempotent, and does not mutate
      server-owned AgentDeps/action state or persisted history.

## 5. Diagnostics and documentation

- [ ] Add distinct privacy-safe history/tool-compaction diagnostic stages with
      fixed categories and numeric counts only.
- [ ] Update configuration, deployment, and context troubleshooting docs while
      keeping Composer Citation compression terminology separate.
- [ ] Update the backend runtime spec after implementation is verified.

## 6. Validation

- [ ] Unit-test trigger/target estimation, contiguous checkpoint selection,
      structured rendering, summary bounds, and every tool projector.
- [ ] Test tool protocol validity after projection, including batched calls,
      retries, invalid citations, pending confirmations, terminal mutations,
      todo state, and unknown tool types.
- [ ] Test initial compaction, incremental advancement, CAS conflicts, summary
      timeout/invalid output/database failure fallback, restart, replay, tenant
      isolation, same external conversation label, and `/new` isolation.
- [ ] Test flag-off compatibility and prove no summarizer call below threshold.
- [ ] Test that historical references cannot authorize a mutation or become a
      current-run citation without fresh retrieval.
- [ ] Scan production diagnostics for summary/history/tool/prompt/URL/ID/content
      sentinels.
- [ ] Run focused suites for conversations, Agent runtime, bounded autonomy,
      diagnostics, settings, migration round-trip, MCP history, and Web API.
- [ ] Run the full backend test suite and verify exactly one Alembic head.

## 7. Review and rollback gates

- [ ] Before rollout, compare estimated before/after context and latency using
      synthetic long conversations and multi-tool runs without logging content.
- [ ] Confirm disabling the flag immediately restores the existing loader and
      does not require schema downgrade or data deletion.
- [ ] Stop before deployment if migration head count, tenant isolation,
      tool-pair validity, citation allow-list, pending-action safety, or
      production log privacy regresses.
