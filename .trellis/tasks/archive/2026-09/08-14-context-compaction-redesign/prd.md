# 上下文分层压缩与 Tool 输出裁剪

## Goal

将当前“按最近轮次直接截断历史”和“回答输出超限后压缩 Citation”的两个局部机制，改造成可观测、可回退的分层上下文压缩机制。在不削弱租户隔离、工具授权、删除确认、引用校验和幂等语义的前提下，优先移除单次 Agent 运行中可重建或重复的 tool 输出，并为超过预算的旧对话提供连续性更好的结构化摘要。

用户价值是：长对话和多工具步骤不再因为早期重要意图被直接丢弃而失去上下文，同时避免把大段检索结果、重复库存页、tool 错误载荷等低价值内容持续发送给模型。

## Background and Confirmed Facts

- 当前跨轮历史由 `load_message_history()` 从最新 completed turn 向旧累计，受 `CONTEXT_MAX_TURNS` 和 `CONTEXT_TOKEN_BUDGET` 限制；超过预算后直接丢弃更旧轮次，不生成摘要。
- 当前 token 预算是序列化 JSON 字符数除以 3 的 provider-neutral 估算，不使用 provider 返回的精确 usage。
- 当前持久化历史通过 `_canonical_history()` 只保存规范化用户问题和最终可见回答。tool 参数、tool 返回、planner 文本、无效草稿、Composer 重试提示均不进入下一轮历史。
- 因此 tool 输出裁剪的主要作用域是单次 Agent run 内的 message history，而不是数据库中已经持久化的跨轮历史。
- 当前 `context_compressed` 仅指 Composer 输出长度耗尽后的 Citation 投影：最多 8 个片段、每来源优先一条、摘录从 360 字符降至 180 字符。它不压缩对话历史，也不由 wall-clock timeout 触发。
- `ContextBuilder` 提供受信任的库存和历史来源焦点；旧 segment ID 不能自动进入当前 run 的 Citation allow-list。
- ConversationThread 当前没有摘要或 compaction checkpoint 字段。若采用跨请求摘要，需要新增持久状态及迁移/并发语义。

## Requirements

### R1. 分层预算与触发

- 压缩必须由明确的输入上下文预算阈值触发，不等到 provider 输出截断或 wall-clock timeout 才处理。
- 预算计算必须覆盖实际将发送给模型的 system/instruction、历史消息、当前消息及当前 run 工具交互；若 provider usage 可用，可用于校准或观测，但不得把不同 provider 的非等价字段当成唯一正确来源。
- 保留现有硬 request/tool/output/time limits，压缩不能成为突破安全预算的方式。

### R2. 单次运行内 Tool 输出优先裁剪

- 在删除用户/助手语义内容前，优先处理已完成、可重建或重复的 tool 交互。
- 可裁剪候选包括：已被后续 tool 结果取代的检索页、重复返回的同一记录、过长证据正文、已消费的临时工作计划以及对后续推理无贡献的成功/错误细节。
- 不得裁剪仍用于授权、范围限定或恢复的可信状态，包括当前租户/thread 标识、精确 URL scope、当前 Citation allow-list、待确认动作、mutation/action terminal outcome、RecoveryLedger grant、未完成 todo 依赖以及工具调用配对所需的协议字段。
- 裁剪后必须保持 provider 所需的 tool-call/tool-result 配对合法；不能留下孤立 tool result 或破坏消息序列协议。
- Tool 内容应以服务器生成的有界投影或引用占位替代，而不是让模型自由总结安全关键载荷。

### R3. 旧对话摘要

- 超过历史预算时，不再只按轮数丢弃所有更旧上下文；系统应支持把已完成的旧对话压缩为结构化、可验证的 continuation summary，并保留最近若干原始轮次。
- 摘要至少区分：用户目标与约束、已确认事实、重要实体/条目引用、已完成动作、未解决问题和最近对话焦点。
- 摘要不得携带原始 tool 载荷、秘密、provider 错误正文、未验证模型声明或可直接授权 mutation 的状态。
- 历史 Citation/segment ID 只可作为对话焦点线索；不得绕过当前 run 检索并进入 Citation allow-list。
- 新 completed turn 到来后，摘要 checkpoint 必须能单调推进，不能重复总结、跳过未覆盖轮次或覆盖更新后的最新内容。

### R4. 失败与回退

- 摘要生成失败、超时、输出不合法或数据库竞争时，本轮请求不得丢失已持久化历史；回退到当前确定性裁剪策略或安全的最近轮次窗口。
- wall-clock timeout 不触发无界摘要重试；压缩与正常 Agent 执行必须有独立且受控的预算关系。
- `/new` 关闭旧 thread 后，新 thread 不得继承旧摘要。
- 重放相同 message_id、服务重启和多渠道同 label 场景必须保持现有幂等与租户隔离。

### R5. 可观测性和隐私

- 新增压缩诊断只能记录安全计数和分类，例如触发原因、估算前后 token、被投影的 tool 结果数、摘要覆盖轮次和回退原因。
- 生产日志不得包含用户问题、tool 参数/结果、摘要正文、Citation 摘录、URL、原始 ID 或 provider 异常正文。
- 将历史压缩与现有 Citation 压缩使用不同的 stage/event 名称，避免 `context_compressed` 语义混淆。

### R6. 兼容和发布

- 默认关闭或通过独立 rollout 配置启用，保留现有直接裁剪路径以便回滚。
- 旧 thread 没有摘要时必须按需安全工作，不要求离线回填才能部署。
- 数据库迁移必须与现有 migration head 和生产启动检查兼容。

## Acceptance Criteria

- [ ] 历史预算未超限时，发送给模型的历史与当前可见语义保持兼容，不额外调用摘要模型。
- [ ] 单次 run 上下文接近阈值时，系统先将符合规则的 tool 输出变为有界投影；测试证明用户/助手语义消息仍被保留。
- [ ] 裁剪后不存在孤立 tool call/result，安全关键的 pending action、scope、allow-list 和 recovery 状态仍可执行原有校验。
- [ ] 长会话超过预算后，模型收到“旧历史结构化摘要 + 最近原始轮次 + 当前消息”，而不是只有最近窗口。
- [ ] 摘要不授予 mutation 权限，历史 Citation ID 不能被当作当前 run 可引用证据。
- [ ] 摘要失败、超时或格式非法时，本轮按确定性最近窗口继续，旧 turns 和已有 checkpoint 不被破坏。
- [ ] 同一 message_id 重放不重复推进摘要；服务重启后继续使用同一安全 checkpoint。
- [ ] 并发请求不会产生跨 thread/tenant 摘要混用、覆盖或错序。
- [ ] `/new` 后新 thread 不继承旧 thread 的摘要。
- [ ] 诊断覆盖未触发、tool 投影、历史摘要成功、摘要失败回退和并发冲突路径，且日志测试证明不泄露正文或敏感字段。
- [ ] rollout flag 关闭时，现有行为和相关测试保持通过；开启时新增集成测试覆盖长会话和多工具步骤。
- [ ] 现有 Composer Citation 压缩保持独立并继续通过输出长度、allow-list 和 evidence fallback 测试。

## Out of Scope

- 允许历史摘要替代当前 run 的知识检索或引用校验。
- 在摘要中持久化完整 tool payload、CoT、planner 草稿或 provider 原始响应。
- 修改租户、channel/thread 身份模型，或放宽 pending action 和删除确认规则。
- 用压缩提高现有工具调用、模型请求、输出 token 或 wall-clock 硬上限。
- 一次性离线重写所有既有 ConversationTurn。

## Key Product Decision

- 本任务 MVP 同时包含两层：单次 Agent run 内的 tool 输出优先裁剪，以及跨请求复用的旧历史结构化摘要。
- 旧历史摘要持久化并使用 checkpoint 增量推进；最近原始轮次继续保留。
- 数据库迁移只新增摘要状态，不批量回填或改写既有 ConversationTurn。
