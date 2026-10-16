# Design: retrieval-agent budget

## Boundary

| File | Change |
|---|---|
| `app/agent/runtime_state.py` | `reserve_retrieval`: drop the same-step gate. `SAME_STEP_SKIPPED` is no longer returned; keep the enum member for type compatibility. Remove `last_retrieval_run_step` bookkeeping if unused elsewhere (grep first). |
| `app/agent/agent_tools/policy.py` | `skipped_payload`: only `budget_exhausted` remains reachable. |
| `app/agent/provider.py` | (Revised B1) No thinking change for retrieval. Any helper added must not disable thinking. |
| `app/agent/orchestrator.py` | `record_model_attempt` returns `dict(self._retrieval_model_settings)`, computed once in `__init__` from the primary model. After `_run_primary_agent` finishes (any outcome: success, `UsageLimitExceeded`, timeout), emit one usage diagnostic from the stage's `RunUsage`. |
| `app/agent/agent_builder.py` | Rule 2 of `BOUNDED_AUTONOMY_INSTRUCTIONS`: post-search completion contract (below). |
| `app/diagnostics.py` | New stage `stage_usage` with allow-listed integer fields `request_count`, `input_tokens`, `output_tokens`, `tool_call_count`, all passed through `_safe_int`, plus `agent_phase`. No text. |
| Tests | Rewrite `tests/test_agent_runtime.py` (around line 1546, `same_model_step`) and any test asserting one retrieval per step. Add tests per the PRD. |
| Spec | `.trellis/spec/backend/agent-retrieval-convergence.md`: §3 contract (lines ~149-157), the error-matrix row (~256), cases (~278, ~291), tests (~301), "Retrieval model settings remain unchanged" (~213) stays true (B1); add the reason. Add a `stage_usage` note to `provider-tls-diagnostics.md` if it lists stages. |

## A. Multiple retrievals per step (decision A1)

```python
def reserve_retrieval(self, *, run_step: int, kind: RetrievalKind) -> ReservationResult:
    """Atomically reserve one backend retrieval within the stage budgets."""
    with self._tool_lock:
        if self.retrieval_calls >= NORMAL_RETRIEVAL_CALLS_LIMIT:
            return ReservationResult.STAGE_BUDGET_EXHAUSTED
        ...search/expansion sub-budgets unchanged...
        self.retrieval_calls += 1
        return ReservationResult.EXECUTE
```

- Execution stays local and sequential
  (`parallel_tool_call_execution_mode("sequential")`), so calls in one batch
  run one after another and each observes the previous call's recorded state.
  Empty-search recovery (`last_empty_search_fingerprint` + `reformulate_search`
  grant) and pending-read-failure logic keep working unchanged between them.
- `prepare_search` / `prepare_expansion` still hide tools once budgets are
  exhausted, for the next model request.
- Expansions in one batch (e.g. 3× `get_neighbors`) now all execute within the
  expansion budget.

## B. Retrieval-phase thinking (revised: stays ON)

Decision B1 (2026-10-07): with thinking off the model skipped the mandatory
search on 2 of 30 questions, so thinking stays on.

- Do not add `extra_body` thinking-disabled or `thinking=False` to retrieval
  settings.
- `record_model_attempt` keeps returning `{"parallel_tool_calls": False}`.
- If a `retrieval_model_settings` helper was added, it must return exactly that
  and carry a docstring explaining why thinking is intentionally left on, or it
  must be removed. Tests assert that thinking is **not** disabled for the
  retrieval phase.

## C. Post-search completion contract (prompt)

Replace rule 2's "最终回答必须…使用 [S<segment_id>] 标记 / 进入 grounded 回答"
wording with:

- 视频内容问答必须先搜索再决定是否继续检索。
- **本轮一旦调用过任何检索工具（search_segments / search_media 等），不要撰写
  回答正文、不要输出 [S…] 标记或来源**；服务器会用本轮证据单独生成最终回答。
  检索完成后只输出「检索完成」。
- 没有调用检索工具的回合（问候、能力说明、澄清、库存/管理读取）照常用自然语言回答，
  且不得包含 Citation 标记。

Keep all other rules. The server's behavior is unchanged: after any search the
primary text is ignored. With evidence the Composer runs; with no evidence the
reply is canonical no-evidence. So disobedience costs tokens only and is never
a correctness risk. `todo_store.finalize` and
`_allow_blocked_todo_clarification` receive the short text and must still work.
Verify with an existing todo test.

## D. Usage diagnostics

After the primary run, call:

```python
diagnostics.event("stage_usage", agent_phase="retrieval",
                  request_count=usage.requests, input_tokens=usage.input_tokens,
                  output_tokens=usage.output_tokens, tool_call_count=usage.tool_calls)
```

Use the attribute names from the installed PydanticAI `RunUsage`; check
`.venv`. The event is emitted on every exit path of `_run_primary_agent`
(success, limit, timeout, exception) via `finally`.

## Verification run

Use the scratch production-path harness
`.trellis/tasks/archive/2026-10/10-06-meeting-qa-quality/research/meeting_agent_prodpath.reference.py`,
copied to the session scratchpad and kept outside the repo:

- reuse the 2026-10-06 post-fix test-branch ingest (`ingest.json` from
  `prodpath-postfix`, tenants/items 99–118), so no re-ingestion;
- one process per case;
- web `agent.stream`;
- `DATABASE_URL` = test branch only.

Compare with
`.trellis/tasks/archive/2026-10/10-06-meeting-qa-quality/research/postfix-rerun-20261006.*`:

- answered;
- pool gold hit / recall;
- cited-gold metrics;
- limit hits;
- skipped vs executed calls;
- provider calls;
- median latency;
- retrieval-stage `output_tokens` (new run only; the old run has no usage data).

About 250 LLM calls (deepseek-v4-flash) plus 30 query embeddings.

## Risks

- Thinking off may change query wording or retrieval choices. The rerun's
  pool-recall check guards this.
- A model that issues more than 2 searches in one batch hits
  `budget_exhausted` for the extras. This is expected and still counted by
  PydanticAI; `AGENT_TOOL_CALLS_LIMIT` (10) stays a safety ceiling.
- `tests/test_agent_runtime.py` and the streaming tests may encode step
  semantics. Rewrite them, do not delete them.

## Rollback

Revert the four code files. There are no data or schema changes.
