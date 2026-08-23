# 技术设计：安全的 Agent 执行时间线

## 1. 问题与边界

现有链路已经做到答案 section 的 provider 文本流式，但主 Agent 的模型/工具循环仍通过
`Agent.run()` 整体等待。用户在检索、扩展片段和库存操作期间只能看到一个被反复覆盖的活动文案。

本任务把主 Agent 改为消费 PydanticAI 的真实运行事件，并把其中可公开的工具生命周期投影为
安全执行时间线。不会公开 primary Agent 的自由文本、thinking、tool arguments、tool result
payload 或 provider metadata。

执行时间线只属于当前正在运行的浏览器请求：回答开始后仍可展开查看，终态后可短暂保留在当前
页面状态中，但不写入 `ConversationTurn`，刷新或重新打开历史时仍只加载权威问答和 Citation。
持久化执行轨迹是后续独立需求。

## 2. 目标数据流

```text
PydanticAI run_stream_events (single primary run)
  ├─ raw text/thinking/tool parts ──────────────── drop
  ├─ FunctionToolCallEvent ── allow-list ──────── step_started
  ├─ FunctionToolResultEvent + safe tool ledger ─ step_completed
  └─ AgentRunResultEvent ───────────────────────── existing finalization
                                                       │
                                                       ▼
validated answer plan → section_started → text_delta* → section_completed
                                                       │
                                                       ▼
ChannelService single persistence → SSE terminal response → browser timeline + answer
```

身份解析、conversation lock、message 幂等、最终投影与持久化继续由 `ChannelService` 所有。
SSE route 只负责严格协议投影，不能创建第二条 Agent 或 persistence 路径。

## 3. Primary Agent 单次事件流

### 3.1 运行入口

为流式路径增加一个 primary-run event consumer，使用：

```python
async with self._agent.run_stream_events(...) as events:
    async for event in events:
        ...
```

参数与当前 `_run_primary_agent()` 完全一致：history、deps、usage/limits、顺序工具执行、
model settings 和总 timeout 不变。必须保留 async context manager；客户端断开或 consumer
提前退出时，由运行时清理后台任务。

非流式 `KnowledgeAgent.run()` 继续使用 `Agent.run()`。共享的 run 参数构造、异常恢复和结果
finalization 应抽成 helper，避免两条路径的限额、恢复或错误语义漂移。

### 3.2 允许消费的事件

- `FunctionToolCallEvent`：只读取注册工具名以选择服务器 allow-list code；不读取 args。
- `FunctionToolResultEvent`：只用来确定工具边界已经结束；不读取 result content。
- `AgentRunResultEvent`：取得权威 primary run result，进入现有 `_finalize_primary_result()`。

以下事件全部忽略：`ThinkingPart` / `ThinkingPartDelta`、primary `TextPart` / delta、
`ToolCallPart` 的 args、原始 `ToolReturnPart` content、provider details、usage payload 和未知原始
事件。Primary Agent 的最终 prose 也不直接公开；知识回答仍必须经过现有 evidence/Citation 与
AnswerPipeline 边界。

## 4. 安全工具进度账本

PydanticAI 的 result content 不作为公开摘要的数据源。扩展 `AgentDeps` 的 turn-local 状态，记录
固定结构的工具观察：

```python
ToolProgressObservation(
    call_index: int,
    tool_code: PublicToolCode,
    outcome: "started" | "succeeded" | "failed" | "skipped",
    result_count: int | None,
)
```

`ToolPolicy` 与已注册工具已经在 `AgentDeps.tool_event()` 处掌握 server-owned 的工具名、调用序号、
结果数量和 outcome。实施时让每个工具 proposal 恰好产生一个开始观察和一个终态观察，包括：

- 正常成功；
- backend 异常与安全 recovery envelope；
- same-step 或 budget skip；
- tool validation retry；
- action 返回业务失败。

账本只在内存中存在，固定上限不超过本轮 `agent_tool_calls_limit`，并在 run 结束时丢弃。日志继续走
现有 diagnostics allow-list；Todo title 与时间线正文不进入生产日志。

Primary stream 按顺序工具执行，因此同一时刻最多一个 open tool step。内部 provider
`tool_call_id` 只用于本轮配对（必要时），公开 ID 由服务器重新编号为 `step-1`、`step-2` 等。

## 5. 公开步骤分类

公开协议不发送原始 Python 工具名或自由生成 label，而发送闭集 `step_code`，由前端本地化：

| 内部工具 | 公开 `step_code` | 开始文案示例 | 完成摘要 |
| --- | --- | --- | --- |
| `todo_write` | `updating_plan` | 正在整理处理步骤 | 展示验证后的 Todo snapshot |
| `search_segments` | `searching_library` | 正在搜索资料库 | 找到 N 个相关片段 / 未找到匹配片段 |
| `get_neighbors` | `reading_context` | 正在读取相关上下文 | 已读取 N 个相关片段 |
| `get_item`, `open_at` | `checking_source` | 正在核对来源 | 已核对 N 条来源信息 |
| `list_saved_items` | `reviewing_library` | 正在查看资料库 | 找到 N 个条目 |
| `get_saved_item` | `checking_item` | 正在查看条目 | 已读取条目信息 / 未找到条目 |
| 保存、确认、取消工具 | `handling_save` | 正在处理保存请求 | 已处理 N 个条目 / 操作未完成 |
| 更新、删除、恢复、重试工具 | `managing_library` | 正在处理资料库操作 | 已处理 N 个条目 / 操作未完成 |
| 未知/未来工具 | `working` | 正在执行一个步骤 | 步骤已完成 / 未能完成 |

`result_count` 仅允许非负、有界整数。失败只发固定 `outcome=failed`，不发送 exception message、
error payload 或 recovery fingerprint。未知工具不能把内部名称带到 label、message 或日志。

Todo 使用现有 `TurnTodoStore` 的已验证 snapshot：最多 6 项、title 最长 120 字符、状态闭集、
禁止 URL、item/segment/tenant 等 server-owned 标记。公开前再次通过 DTO 长度和字段校验；Todo
是明确的计划 artifact，不是 hidden chain-of-thought。

## 6. 内部与公开事件协议

### 6.1 新事件

扩展 `AgentStreamEvent` 与 `ConversationStreamEvent`：

```text
step_started(
  step_id,
  step_code
)

step_completed(
  step_id,
  step_code,
  step_outcome=completed|failed|skipped,
  result_count?
)

plan_updated(
  plan=[{id, title, status}]
)
```

所有事件继续携带 request ID、message ID 与 SSE sequence。`step_started` 与
`step_completed` 必须 code 一致；一个 step 只能终结一次。`plan_updated` 只在成功的
`todo_write` 后发送，并且 snapshot 相对上次有变化。

### 6.2 生命周期

```text
started
activity(preparing|retrieving)
(step_started → step_completed → activity(retrieving))*
(plan_updated)*
activity(planning_answer)
(section_started → text_delta* → section_completed)*
completed
```

失败/取消可在 open step 时终止。SSE adapter 在发送 terminal 前把 open step 投影为固定的
`step_completed(outcome=failed)`，再处理现有 section abort 与 error/cancelled。客户端拒绝：

- result 没有对应 start；
- 重复 step ID；
- code 不一致；
- 一个以上 open step（当前顺序执行不允许）；
- terminal 后的新事件；
- sequence 缺口或 request/message 不一致。

重复/stale sequence 继续幂等忽略。协议错误不自动向 JSON endpoint 重发同一消息。

## 7. 前端状态与交互

把当前单一 `pendingActivity` 扩展为：

```ts
type PendingExecutionStep = {
  stepId: string;
  code: PublicStepCode;
  outcome: "running" | "completed" | "failed" | "skipped";
  resultCount?: number;
};

type PendingPlanItem = {
  id: string;
  title: string;
  status: "pending" | "in_progress" | "completed" | "blocked";
};
```

呈现规则：

- 回答开始前，时间线默认展开，最新 running step 使用克制的活动指示；已完成步骤保留可扫描状态。
- `step_completed` 原位更新对应步骤，不新增重复行。
- Todo 作为一张独立的“本轮计划”列表按 snapshot 替换，不把 `todo_write` JSON 显示给用户。
- 第一段答案开始后，时间线降为次要层级并允许折叠；答案 section/Citation 保持主视觉。
- terminal 后在当前 pending turn 内显示“已完成 N 个步骤”；历史刷新后仅展示现有最终问答。
- 原有 `pendingActivity` 仍作为 `aria-live=polite` 的当前状态摘要，避免对每个 token 朗读；时间线
  状态使用语义列表与可见文本，失败用 `role=alert`。
- CSS 使用现有 `styles.css`、移动优先、44px disclosure 控件，并为动画提供
  `prefers-reduced-motion` fallback。

前端 copy 只由 `step_code + outcome + result_count` 生成。禁止渲染服务端自由文本为 HTML；回答
Markdown 与 Citation 的现有安全组件不变。

## 8. 兼容、失败与回滚

- `AGENT_STREAMING_ENABLED=false` 继续使用 JSON endpoint。
- provider 不支持 answer token streaming 时仍可走 one-delta answer compatibility；primary 工具时间线
  不依赖 answer provider streaming 能力。
- Action、no-evidence、clarification 和 canonical answer 也可拥有前置工具步骤；最终答案路径不变。
- 客户端断开关闭 `run_stream_events()` context，不持久化未完成 turn。
- 若新 step protocol 出现问题，可停止发送新 step/plan 事件，保留现有 activity/section/terminal 合约；
  不回滚 Citation、授权或持久化校验。

## 9. 可观测性

沿用 `conversation_stream_lifecycle` 和 `RequestDiagnostics`，只增加固定字段/计数：

- `event_type=step_started|step_completed|plan_updated`；
- allow-listed public step code；
- tool step count、outcome、result count、持续时间；
- terminal 时的 completed/failed/skipped 数量。

禁止记录 Todo title、问题、工具 args/result、Citation 内容、primary text/thinking、provider event 或异常消息。

## 10. 关键取舍

- 选择 PydanticAI 原生 run event stream，而不是给每个工具单独加 SSE callback：保留单次 Agent 结果和
  框架清理语义。
- 选择 closed step code + safe numeric metadata，而不是模型生成的“思考摘要”：可预测、可测试，不把
  chain-of-thought 换名泄漏。
- 第一版不持久化时间线：显著降低数据库、隐私、历史 API 与保留策略复杂度，同时直接解决长时间无反馈。
- 保持工具顺序执行：现有预算与同 model-step 收敛依赖这个不变量；并行步骤树不属于本任务。
