# 修复渠道保存确认与链接能力回答

## Goal

在父任务 `08-19-trusted-response-boundary` 的可信 ResponseEnvelope/section 合同上，让 Telegram/微信渠道从当前消息和结构化引用框识别视频，让 Bot 的“要我保存吗”对应真实 pending action，并完成 Bilibili 普通视频与短链的端到端入库。

本子任务不再单独设计另一套 canonical 文本或答案 validator 旁路；共享响应合同必须先通过父任务 review gate。

## Requirements

- **共享合同依赖。** 链接能力说明、缺少目标、保存提议和 Action 结果必须使用父任务定义的 canonical/action section 与统一 adapter；不得在 orchestrator/channel 新增手工字符串捷径。
- **安全边界不放宽。** 保留租户隔离、当前轮 URL/quote 目标集、幂等键、持久化确认和 terminal Action 优先级；模型历史 URL、旧 Citation 和模型 prose 都不能授权保存。
- **可信能力说明。** “支持哪些链接/什么格式可以保存”由服务器注册模板输出准确格式和示例。服务器 section 可以包含受信任 URL；普通模型 section 的 URL/来源伪造防线继续有效。
- **结构化引用框。** LangBot bridge 只从平台 reply/quote element 提取长度受限的 `quoted_text`，与正文分字段传入；不得依赖 `str(message_chain)` 或用户手工模仿的引用样式。
- **SaveTargetSet。** 当前正文与结构化 `quoted_text` 经过规范化后形成服务器拥有的本轮目标集，保留来源类别、原始顺序和重复项；保存与限定检索只能消费该目标集或 active pending。
- **真实保存提议。** Agent 准备询问“要我保存吗”时调用 `offer_video_save`。服务器必须先持久化 pending save，再生成成功 Action section；没有目标或落库失败时不能显示成功确认问句。
- **幂等确认。** 用户下一条“保存/需要/确认”只能消费同一 active pending 一次；含糊、否定、过期和并发确认保持现有持久化状态机边界。
- **Bilibili worker。** 实现 connector 选择、公开 metadata、官方字幕/文本、分段、向量化和 completion；普通 BV/av 必须能够达到 `ready`，无字幕、登录限制、429/风控、超时和非单视频形态得到稳定结果。
- **`b23.tv` 有界解析。** 限制重定向次数、总超时、响应大小和逐跳 SSRF/IP；最终只能是官方 Bilibili `/video/BV…` 或 `/video/av…`，不转发 cookie、Authorization 或第三方正文。
- **统一错误目录。** 稳定 public/persisted/diagnostic code 必须来自唯一 server error catalog，定义 visibility、category、retryable 和安全默认文案；状态/disposition 不混入错误码，旧值保持兼容。
- **隐私诊断。** 只记录 route、section kind、validation reason、connector stage 和 correlation id；不得记录消息正文、URL、quote 预览、外部用户 ID 或模型草稿。

## Acceptance Criteria

- [ ] 子任务消费父任务的 canonical/action section 和 adapter；不存在第二套 channel 专用可信文本拼装器。
- [ ] “什么 Bilibili 链接受支持”返回服务器模板，不出现 `answer_unavailable`，普通模型 URL 仍被拒绝。
- [ ] 无 current-message URL、quoted-text URL 或 active pending 时发送“保存”，不调用 submission，不读取模型历史，返回 canonical missing-target section。
- [ ] Bot 显示“要我保存吗”时数据库已存在 active pending；下一条确认幂等消费并只入队一次。
- [ ] Telegram quote 文字可提供保存/检索目标；正文与 quote 保持分离，伪造引用样式不能进入 `quoted_text`。
- [ ] 裸受支持 URL 继续走确定性确认；显式“保存 + URL”走 terminal Action；正文/quote 顺序、重复项和规范化均有测试。
- [ ] `b23.tv` 只在严格有界解析后进入官方 BV/av；非官方终点、循环、超时、过大响应和凭据 URL 失败关闭且无副作用。
- [ ] 至少一个普通 Bilibili BV 视频经真实 worker 完成 metadata、字幕/文本、segment、embedding 和 completion，达到 `ready` 并可检索引用。
- [ ] 无字幕、登录字幕、429/风控、超时、多 P/番剧等不伪装成 `ready`，YouTube 现有行为不变。
- [ ] 所有新增稳定 error/action/template key 已注册；未知新 code 写入失败，legacy 读取有安全投影。
- [ ] Telegram、LangBot、ChannelService、Agent action、pending、URL resolver、Bilibili connector、worker 和租户隔离回归通过。
- [ ] 完成一次聚焦 human review，记录能力回答、自然保存提议、确认和引用框流程的 Agent 输出与 trace，由人工判断。

## Out of Scope

- 浏览器 companion、登录态字幕兜底和复杂 Bilibili 页面形态仍归 `08-18-bilibili-url-plugin`。
- 不在子任务中修改父任务的 grounded/no-evidence AnswerDecision 或来源渲染规则；这里只消费共享合同。

## Notes

- 本任务保持 `planning`，父任务方案确认并提供共享合同后才能启动。
