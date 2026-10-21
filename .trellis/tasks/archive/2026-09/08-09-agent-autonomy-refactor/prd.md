# Agent 有界自主性重构

## Goal

在不迁移 Agent 框架、不重写领域服务、不削弱租户与副作用安全边界的前提下，
让现有知识库 Agent 从“所有普通消息都必须进入固定检索工作流”调整为一个有界的
主 Agent：模型自行判断当前消息是否需要知识工具，可以自然处理问候、感谢、能力
询问、澄清和依赖上下文的追问；复杂请求可以使用本轮 Todo 组织步骤，并在服务端许可
范围内进行有限恢复；一旦使用知识工具，回答仍必须由本轮可信证据支持。

本任务追求的是更灵活的单 Agent，而不是通用无边界聊天机器人、多个执行器或新的
显式状态图。

## Background

当前运行时把检索规划、18 个工具、动态显隐、检索预算、terminal action、独立
Composer、引用校验和 fallback 集中在同一模块中。任何没有产生 action 的请求都被
要求至少调用一次 `search_segments`，因此问候、感谢、能力询问和澄清也容易退化为
`search_required` 或无意义检索。同时，工具 Agent 对历史的理解在独立 Composer 阶段
被压缩为“当前原始问题 + Citation”，使“这个呢？”一类追问容易失去已解析语义。

这些问题不能通过删除所有状态解决。租户身份、精确 URL scope、pending confirmation、
幂等、删除 effect claim、检索引用白名单和硬预算均来自真实安全/正确性要求，必须保留。

## Requirements

### R1 — One bounded primary Agent

- 保留一个主要的 tool-using Agent，由它理解本轮意图、判断是否需要知识工具、选择工具
  顺序，并生成自然语言回答。
- 不新增 Inventory/Mutation/Save/Delete 等执行器或多 Agent handoff；原子动作继续作为
  Tool，实际数据库和业务逻辑继续由现有 Domain Services 完成。
- 不为每条消息预先编排固定 workflow。模型可以零工具直接回答、提出澄清，也可以在
  有界循环中调用一个或多个允许的工具。

### R2 — Natural conversation and clarification

- 问候、感谢、结束语、助手能力询问和不需要私有知识事实的普通交流应允许零知识工具、
  零 embedding、零 retrieval，并返回一次自然回复。
- 当模型认为缺少可信指代或问题信息时，可以先提出澄清，而不是机械搜索、猜测 item，
  或返回 `search_required`。
- 普通无工具回复不得伪造 Citation、URL、item/segment ID 或声称已经查询了知识库。
- 产品仍定位为私人知识库助手；本任务不把它扩展为可以自由回答任意外部知识的通用聊天机器人。

### R3 — Model-selected grounding with server validation

- 对没有显式受支持 URL 的普通自然语言消息，由主 Agent 判断是否需要调用知识工具；
  第一版不增加独立 intent-router 模型调用。
- 显式 URL 内容问题继续由服务端建立 exact-reference scope；该 scope 只能收紧，不能被
  模型、历史或工具参数清空为 tenant-wide 搜索。
- 一旦本轮成功调用知识检索工具，最终回答必须包含本轮 Citation cache 中的内联
  `[S<segment_id>]` 标记；模型不得直接渲染来源 URL。
- 服务端必须拒绝缺失、过期、伪造、跨 scope 或并非来自本轮工具结果的 Citation 标记，
  允许至多一次有界修复，随后使用可信 evidence fallback 或稳定失败。
- 服务器根据真实工具执行轨迹区分 grounded、read-only tool-supported、action 和 no-tool
  conversation，不要求模型输出复杂 `AnswerDraft.sections` 或自行声明可信运行状态。

### R4 — Context continuity and read-only composition

- 为主 Agent 构造有界的可信 Turn Context，至少包含最近会话历史、上一轮 Citation focus、
  最近 inventory 结果的有界顺序、当前显式引用和最小 pending-action snapshot。
- 该 context 从当前 tenant 的既有 `ConversationTurn.sources`、`action_results` 和模型历史
  推导；本任务不新增长期记忆、用户画像或数据库迁移。
- read-only inventory/detail 结果不应强制结束整轮；主 Agent可以在同一轮继续进行只读
  检索和回答，例如“列出我的收藏，然后总结第二个”。
- destructive mutation、保存提交和 durable confirmation outcome 继续是 terminal server
  outcome；模型生成的 prose 不得覆盖 canonical action result。

### R5 — Tool visibility and bounded autonomy

- Toolset Policy 只根据可信运行时状态和已启用功能隐藏不可能或不适用的工具，例如没有
  matching pending action 时不暴露对应 confirm/cancel/clarify 工具。
- Tool schema 继续不接受 `user_id`、tenant、thread、pending action、claim 或授权 scope。
- 保留并验证现有 wall-clock timeout、provider request、raw tool call、检索 search/expansion
  和输出 token 上限；本任务不得通过简单提高上限掩盖循环问题。
- 第一版不引入自由 tool discovery、任意 MCP delegation、web/filesystem/terminal 工具或
  可由模型扩展的 capability registry。

### R6 — Optional turn-scoped Todo

- 提供可选的 `todo_write` 原子工具和内存 `TurnTodoStore`，供主 Agent 在同一轮存在多个
  相互依赖步骤时记录、更新和收敛计划；普通对话和单工具请求不应创建 Todo。
- Todo 最多包含 6 个短步骤，状态仅允许 `pending`、`in_progress`、`completed`、`blocked`，
  且任一时刻最多一个步骤为 `in_progress`。
- Todo 只是模型的临时工作记忆，不是业务事实、授权证明、工具调用结果或 durable workflow；
  服务端不得因为 Todo 声称 `completed` 而认可读取或副作用已经成功。
- Todo 仅存在于当前 `run()`，不写入 PostgreSQL、ConversationTurn、跨轮上下文或生产日志；
  其中不得放入 URL、tenant/thread/pending/claim ID、工具 payload、证据或其他敏感内容。
- 普通完成时，已创建的 Todo 不得遗留 `pending`/`in_progress`；缺少上下文或安全失败可以把
  未完成步骤标记为 `blocked` 并向用户提出澄清或说明部分完成。terminal action 可以由
  canonical outcome 提前结束本轮，Todo 不得改变其结果。

### R7 — Typed errors and bounded recovery

- 工具/运行时失败必须映射为安全的 typed `ErrorEnvelope`；不向模型暴露异常正文、provider
  body、URL、tenant、内部 ID、SQL、工具 payload 或堆栈。
- `RecoveryPolicy` 根据错误类别、操作类别、可信执行轨迹和剩余预算给出允许的恢复集合；
  主 Agent只能在该集合中选择，不能通过改 Todo、改工具参数或换工具绕过拒绝。
- 同一个只读工具的同参数瞬时失败最多自动恢复重试一次；整轮最多执行两次 recovery action；
  answer/Citation 修复最多一次，并计入整轮 recovery 上限。所有恢复继续消耗原有 provider、
  tool、retrieval 和 wall-clock 预算，不获得额外额度。
- 空搜索结果是正常 observation，不伪装成异常；模型可在剩余检索预算和整轮 recovery 上限内
  改写查询。缺少可信上下文时应提出澄清并把相关 Todo 标记为 `blocked`。
- policy/security、tenant/scope、deleted/non-ready 和授权失败不得由模型恢复；mutation、保存、
  confirmation 及副作用状态未知/进行中不得自主重试，必须返回现有 canonical outcome。
- provider/model 失败不得在 Agent 层重试；已有可信证据时使用安全 evidence fallback，否则
  返回稳定失败。answer/Citation 校验失败仅可针对同一 evidence allow-list 修复一次，之后
  fallback 或稳定失败，不得重新检索。
- `RecoveryLedger` 仅保存本轮安全计数、错误类别和调用指纹，不持久化、不进入模型历史，
  也不得记录敏感参数。显式用户请求的领域动作（例如重新处理失败条目）不等同于自主恢复，
  仍走既有校验、幂等和 terminal action 路径。

### R8 — Compatibility and rollout

- 保持 `AgentRequest`/`AgentAnswer`、ChannelService、LangBot bridge、CLI 和 MCP 公共协议兼容；
  内部字段允许向后兼容地扩展。
- 继续支持配置的 PydanticAI provider 和 OpenAI-compatible gateway；不得只针对一个模型
  或 provider 的非标准行为实现。
- 新行为必须由 rollout flag 控制。关闭 flag 时保留当前 fail-closed 运行路径，便于真实
  模型对照、灰度和回滚。
- 不新增数据库 schema，不修改 tenant identity、pending action、ingestion、回收站或 purge
  状态机。

### R9 — Behavioral evaluation and diagnostics

- 开发侧先建立一份可自动运行的最小行为/安全文本集，用户在实现跑通后独立进行真实模型
  整体效果验收；文本集至少覆盖：社交对话、能力询问、澄清、普通知识问题、上下文追问、显式 URL、
  inventory→retrieval 组合、应该/不应该使用 Todo、瞬时读取失败、空结果改写、部分成功、
  provider/validation 失败、pending confirmation、mutation 和诱导绕过引用或恢复边界。
- 自动测试使用 `FunctionModel`/`TestModel` 验证精确工具轨迹、回复数、Citation 和 action
  outcome；真实模型 A/B 验收比较旧路径和新路径的任务成功率、错误工具率、引用正确率、
  延迟和 token 使用。
- 生产诊断只增加固定 mode/toolset/outcome 和数值字段，不记录问题、历史、模型回答、工具
  payload、证据、URL 或外部身份。

## Non-goals

- 迁移到 LangGraph、OpenAI Agents SDK、Agno 或外部 Agent Harness。
- 暴露原始 retrieval MCP tools，或把 Notebook Agent 变成通用 MCP orchestrator。
- 多 Agent、Agent handoff、长期语义记忆、用户画像或自主后台任务。
- 持久化 Todo、跨轮任务恢复、通用 task engine、队列调度器或新的 durable workflow 状态机。
- 通用“遇错就重试”、为 mutation/provider 增加 Agent 自主重试，或通过提高预算掩盖失败。
- 在一个 turn 内组合多个 destructive mutation，或允许模型绕过 confirmation。
- 重写 retrieval ranking、embedding、ingestion、数据库模型或渠道适配器。
- 以自然度为理由放宽 tenant、exact-reference、deleted-content、引用白名单或日志隐私边界。

## Acceptance Criteria

- [ ] Rollout flag 开启时，问候、感谢、能力询问和澄清 case 各自得到一次自然回复，且
      model trace 中没有 knowledge tool、embedding 或 retrieval 调用，也不返回
      `search_required`。
- [ ] 普通私有知识问题由模型选择知识工具；成功回答至少执行一次搜索，所有 `[S…]`
      标记均来自本轮 Citation cache，服务器统一渲染来源。
- [ ] 模型省略 Citation、引用旧历史 ID、伪造 ID 或引用当前 scope 外证据时不会向用户
      返回未验证草稿；至多一次修复后进入可信 fallback/稳定失败。
- [ ] 显式 URL 内容问题仍只检索和引用该 tenant 下的精确目标；裸 URL 保存确认、旧 pending
      action 隔离和不存在/删除/未 ready 条目继续 fail closed。
- [ ] 至少一个测试覆盖同轮 `list_saved_items → 选择可信返回条目 → search_segments →`
      带引用回答；read-only 组合不再被第一个 inventory outcome 提前终止。
- [ ] 上述多步 case 使用不超过 6 项的 turn-scoped Todo 表达 list→resolve→retrieve→answer，
      结束时步骤为 `completed`/`blocked`；问候、已有明确答案的澄清和单工具读取 case 不调用
      `todo_write`。Todo 不被持久化、写入模型历史或生产日志。
- [ ] 缺少“第二个”的可信列表时，Agent 不猜测 item；如已创建 Todo，则相关步骤标记为
      `blocked`，最终自然询问用户，且不执行无 scope 检索。
- [ ] 同参数只读工具发生可恢复瞬时失败时，Policy 最多允许一次重试；重试成功则继续原
      Todo，重试仍失败则返回稳定不可用或在已有可信结果上部分完成，不能进入无限循环。
- [ ] 空搜索可以在原有 search budget 和整轮两次 recovery action 上限内改写查询；已有
      inventory 但后续 retrieval 不可用时，回答明确区分已完成列表与未完成总结，不伪造引用。
- [ ] 保存、更新、删除请求/确认/取消、恢复和 retry 的 canonical terminal outcome、确认码、
      TTL、重复 delivery 与 effect-claim 行为保持不变。
- [ ] mutation/confirmation/副作用状态未知或进行中时，Agent 自主重试次数为 0；显式领域
      retry 仍只通过现有校验后的 canonical action 执行。policy/security 失败不能换工具绕过。
- [ ] provider/model 失败的 Agent-level retry 为 0；有证据时走 evidence fallback，无证据时
      返回稳定失败。answer/Citation 校验失败至多使用同一证据修复一次，且整轮 recovery
      action 总数不超过 2。
- [ ] 无工具回答不能包含 Citation marker 或服务端来源区块；知识工具回答不能没有有效
      Citation。action 路径不能被模型 prose 覆盖。
- [ ] 新路径继续满足现有 request/tool/output/wall-clock 上限；普通零工具对话最多一个主
      Agent run，不增加 Composer 调用。
- [ ] 开发侧完成确定性自动测试和一组基本运行 smoke，并提供 flag-on/off 与运行方式给用户；
      真实模型整体效果和自然度由用户独立验收，在结果确认前不得默认开启 rollout flag。
- [ ] Agent、action、pending confirmation、conversation persistence、exact-reference、tenant
      isolation、MCP/Channel 和日志隐私相关自动回归通过；可用环境中完成 PostgreSQL 集成。
- [ ] Rollout flag 关闭时旧行为仍可运行；回滚不需要数据迁移或修复持久化状态。

## Planning Gate

用户已评审并批准 `prd.md`、`design.md` 和 `implement.md` 的实现方向。行为测试分工调整为：
开发侧验证自动测试与基本链路能跑通，用户负责后续真实模型整体效果验收；因此不再以用户
文本集作为 `task.py start` 的前置条件。
