# 检索 Agent 预算浪费：跳过调用计数、检索阶段思考、被丢弃的回答

## Goal

The primary (retrieval) Agent should spend its request, tool-call and
output-token budget on retrieval, not on discarded output. Answer quality must
stay at the post-fix level: the 2026-10-06 production-path rerun answered
30/30, and gold evidence was in the retrieved pool for 26/28. Latency and token
use should drop.

## Background (confirmed 2026-10-07)

### Post-fix production-path rerun (30 cases)

- Answered 30/30; median latency 13.7 s.
- Retrieval-stage limits were hit in 6/30 cases, all `output_tokens`
  (`AGENT_OUTPUT_TOKEN_LIMIT` = 2000).
- 37 tool calls were skipped vs 68 executed.
- Data: `.trellis/tasks/archive/2026-10/10-06-meeting-qa-quality/research/postfix-rerun-20261006.*`.

### Issue A: skipped calls consume the tool-call limit

- DeepSeek (`deepseek-v4-flash`) emits several tool calls per response despite
  `parallel_tool_calls=False`. This was observed directly: 2 calls on the first
  step of a probe.
- `AgentDeps.reserve_retrieval()` (`app/agent/runtime_state.py:150-170`) lets
  only the first retrieval per model step reach a backend. The others return
  `skipped/same_model_step` via `ToolPolicy.skipped_payload`
  (`app/agent/agent_tools/policy.py:317-339`).
- PydanticAI's `UsageLimits(tool_calls_limit=AGENT_TOOL_CALLS_LIMIT=10)` counts
  every call, including skipped ones. Example: on 2026-10-06 case `Bro022:0`
  hit the limit with 5 executed + 5 skipped calls.
- The one-backend-retrieval-per-step rule is a documented contract
  (`.trellis/spec/backend/agent-retrieval-convergence.md:149-157`, plus test
  expectations around line 301).
- Server-owned stage budgets already bound real work: 5 retrievals in total,
  2 searches, 3 expansions (`NORMAL_*_CALLS_LIMIT`).

### Issue B: thinking stays on in the retrieval phase

- The retrieval phase sends only `{"parallel_tool_calls": False}`
  (`app/agent/orchestrator.py:609-617`).
- The answer phase disables thinking through `composer_model_settings`
  (`app/agent/provider.py:45-58`, DeepSeek
  `extra_body={"thinking":{"type":"disabled"}}`).
- The spec currently says "Retrieval model settings remain unchanged"
  (`agent-retrieval-convergence.md:213`).
- A probe returned 44 reasoning tokens on a trivial first step. Reasoning
  tokens count toward the 2000-token retrieval output limit.

### Issue C: the retrieval Agent writes an answer that is always discarded

- `BOUNDED_AUTONOMY_INSTRUCTIONS` rule 2 (`app/agent/agent_builder.py:26-30`)
  asks for a final answer with exact `[S<id>]` markers.
- After any search:
  - with evidence, `_finalize_primary_result` always routes to the Composer
    (`app/agent/orchestrator.py:1147-1155`);
  - without evidence, it returns the canonical no-evidence answer.
  - The primary text is never shown in either case.
- The primary text is the user-visible answer only when no search ran:
  greetings, capability questions, clarification, and management reads
  (`orchestrator.py:1016-1070`).

## Requirements

- R1 (Issue C) Once the turn has run `search_segments` (or `search_media`), the
  primary Agent ends with a minimal completion output instead of a cited answer.
  No-search paths keep producing user-facing natural text exactly as today. The
  server must not depend on the model obeying this: the discard behavior stays
  the same, and the change only saves tokens.
- R2 (Issue B; REVISED by user decision B1, 2026-10-07) **Keep provider thinking
  ON in the retrieval phase.** The first verification rerun (thinking off)
  answered 27/30. Two content questions ended without any search call
  (`search_required`):
  - `Bed015:2`: thinking off failed 2/3 retries; thinking on succeeded 3/3.
  - `ES2009c:0`: thinking off failed 2/3 retries; thinking on succeeded 3/3.

  Thinking costs about 300–1,200 retrieval output tokens and about 3 s per turn.
  The retrieval phase keeps sending only `parallel_tool_calls=False`. The spec
  text "Retrieval model settings remain unchanged" stays valid. The
  `stage_usage` diagnostic (R4) remains.
- R3 (Issue A; user decision A1, 2026-10-07) Execute every retrieval call in a
  model step while the server-owned budgets allow it:
  - Remove the "one backend retrieval per model step" gate from
    `reserve_retrieval`.
  - Calls run sequentially through the existing local sequential execution.
  - Only calls beyond the stage budgets return `skipped/budget_exhausted`.
  - Budgets are unchanged: 5 total, 2 searches, 3 expansions.
  - The spec contract and the tests asserting one-retrieval-per-step are
    rewritten to the new rule.
  - Empty-search recovery semantics (`reformulate_search` grant) still apply
    between sequential calls in the same step.
- R4 Diagnostics: keep counting skipped calls; add per-stage usage visibility
  (output tokens, requests) in the redacted diagnostics, without model text.

## Acceptance Criteria

- [ ] Unit tests cover:
  - retrieval-phase model settings keep thinking on (B1);
  - no-search natural answers unchanged;
  - post-search completion output is ignored safely;
  - the Issue A policy.
- [ ] A production-path rerun of the 30 cases on the Neon test branch, with
      `DATABASE_URL` overridden, reports against the 2026-10-06 post-fix run:
  - answered stays 30/30;
  - retrieved-pool gold coverage is not lower;
  - output-token limit hits drop to 0, with thinking on now that the discarded
    answer is gone;
  - median latency and retrieval-stage output tokens drop.
- [ ] Spec updated: retrieval settings, the skipped-call accounting rule, and
      the post-search completion contract.

## Out of Scope

- Changing the stage budgets, `AGENT_TOOL_CALLS_LIMIT` or
  `AGENT_OUTPUT_TOKEN_LIMIT` defaults.
- Retrieval ranking, lexical or diversification changes.
- Composer changes.

## Addendum (user, 2026-10-07)

- R5 Raise the `AGENT_OUTPUT_TOKEN_LIMIT` default from 2000 to 3000
  (`app/config.py`, `.env.example`, `docs/reference/configuration.md`). The
  final verification run peaked at 1,975 retrieval-stage output tokens with
  thinking on. The Composer constraint (`AGENT_COMPOSER_MAX_TOKENS` ×
  `COMPOSER_VALIDATION_REQUEST_LIMIT` ≤ limit) still holds.
