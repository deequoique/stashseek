# 实施计划：安全的 Agent 执行时间线

## 1. 先建立协议与安全投影

- [ ] 在 Agent 层定义 closed public step code、tool-to-step allow-list、safe outcome/result-count DTO。
- [ ] 扩展 turn-local `AgentDeps` 工具进度账本；补齐正常、失败、recovery、skip、retry 与 action 业务失败，
      保证每个 tool proposal 恰好一个 start 和一个 terminal observation。
- [ ] 为 Todo public projection 复用现有 snapshot 验证，并测试 URL、内部 ID、payload 关键字、超长和非法
      状态全部在 SSE 前失败关闭。
- [ ] 单元测试未知工具只能投影为 `working`，原始名称/args/result/exception 不出现。

## 2. 把 primary run 接入真实事件流

- [ ] 抽取 `_run_primary_agent()` 与流式路径共享的 history、limits、usage、model-settings、timeout 和异常恢复
      helper，保持非流式行为不变。
- [ ] 在 `KnowledgeAgent.stream()` 使用 `async with Agent.run_stream_events()`；只消费 function-tool 边界与
      `AgentRunResultEvent`。
- [ ] 为每个内部 tool call 分配 server-owned `step-N`，发送 `step_started`，在结果边界结合安全账本发送
      `step_completed`；Todo snapshot 变化后发送 `plan_updated`。
- [ ] 明确丢弃 primary text/thinking/tool args/raw result/provider details，并增加敏感哨兵测试。
- [ ] 保持现有 `_finalize_primary_result()`、AnswerPipeline、Citation-first section stream、失败恢复和 usage/tool
      limits；不得增加第二次 Agent run。
- [ ] 测试 consumer 提前关闭、客户端取消、timeout 和异常时 PydanticAI context 被退出且 open step 进入固定终态。

## 3. 扩展内部事件、SSE 与 OpenAPI

- [ ] 扩展 `AgentStreamEvent`：`step_started`、`step_completed`、`plan_updated` 及其 closed fields。
- [ ] 扩展 `ConversationStreamEvent` Pydantic validator：step ID/code/outcome、result-count bounds、plan item bounds，
      严格拒绝跨事件非法字段。
- [ ] 更新 SSE adapter 状态机：单 open step、start/result 配对、terminal 前关闭 open step、现有 section 状态机
      与 terminal 屏障并存。
- [ ] 更新 `docs/interfaces/web-api.md` 的完整生命周期和安全边界。
- [ ] 重新生成 `web/src/api/openapi.json`、`schema.d.ts`，再从 generated schema 更新语义 alias；不得手改生成类型。

## 4. 更新浏览器 parser 与执行时间线 UI

- [ ] 扩展 SSE runtime validator 与 parser state：step IDs、code 一致性、单 open step、重复/乱序/缺口与 terminal
      后事件处理。
- [ ] 将 ChatPage pending 状态拆为 current activity、execution steps、plan、answer sections；终态/新请求/切换会话时
      确定性清理。
- [ ] 新建窄 presentation component（如 `ExecutionTimeline`），使用语义列表与 native disclosure；页面继续所有
      network orchestration。
- [ ] running 时默认展开；回答开始后降为次要层级且可折叠；`step_completed` 原位更新，不重复追加。
- [ ] step copy 只从 closed code/outcome/count 生成；Todo title 作为纯文本；不得引入 raw HTML 或第二套 API type。
- [ ] 在 `styles.css` 完成移动优先、清晰状态、44px 控件与 reduced-motion fallback。

## 5. 测试矩阵

- [ ] Fake primary Agent：两次以上 tool calls，start/result 在真实完成时间前后到达，并最终只有一个
      `AgentRunResultEvent`/持久化 turn。
- [ ] Tool matrix：search、context read、item read、inventory read、save/confirm、management mutation、Todo、unknown、
      skipped、retry、backend failure、business failure。
- [ ] Security matrix：thinking、primary prose、tool args、query、item/segment/tenant ID、字幕、URL、raw JSON、provider
      payload 与 exception sentinel 不进入 SSE、DOM 或 production logs。
- [ ] Protocol matrix：result-without-start、duplicate step、mismatched code、two open steps、sequence duplicate/gap、wrong
      request/message、terminal tail、cancel/timeout/disconnect。
- [ ] UI matrix：步骤原位更新、Todo replacement、答案开始后的折叠层级、终态计数、失败/取消清理、aria-live 不逐 token
      轰炸、键盘与 reduced motion。
- [ ] Regression：Citation-first multiple deltas、unsupported section、one-delta fallback、JSON endpoint、Action、no evidence、
      tenant isolation、history and single persistence。

## 6. 验证命令

```bash
.venv/bin/pytest -q \
  tests/test_agent_runtime.py \
  tests/test_agent_actions.py \
  tests/test_conversation_streaming.py \
  tests/test_citation_first_provider_streaming.py \
  tests/test_trusted_response_boundary.py

pnpm --dir web test
pnpm --dir web run typecheck
pnpm --dir web run build
pnpm --dir web run check:api

PYTHONPATH=. .venv/bin/python scripts/export_web_openapi.py --check
python3 ./.trellis/scripts/task.py validate 08-22-agent-step-streaming
git diff --check
```

聚焦测试通过后运行完整 Python suite；若工作区已有无关失败，单独记录证据，不修改或提交用户的其他改动。

## 7. Review Gates

- [ ] Primary Agent text/thinking/args/result 没有任何公开通路。
- [ ] 所有显示步骤都对应真实 runtime/tool 事件，没有虚构延迟或补演步骤。
- [ ] 一个 message 只有一次 Agent run、一次 terminal response、最多一次 persistence。
- [ ] SSE step 与 answer section 两套状态机在取消、timeout、disconnect 下都能关闭。
- [ ] 非流式 JSON、Citation/tenant、安全日志与工具预算语义没有改变。
- [ ] 时间线不写入 conversation history；未来若需要持久化，另建带保留/隐私设计的任务。

## 8. 回滚点

- 新 primary event consumer 可以退回 `_agent.run()`，保留非流式兼容与现有 answer streaming。
- SSE adapter 可停止发送 step/plan 事件，客户端仍接受原 activity/section/terminal 生命周期。
- `AGENT_STREAMING_ENABLED=false` 保留为部署级 kill switch。
