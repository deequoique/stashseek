# 渠道保存与链接能力路由执行计划

## Phase 0 — 依赖与事件验证

- [ ] 父任务 ResponseEnvelope、canonical/action section 和 adapter 方案通过 review；未通过前本任务保持 `planning`。
- [ ] 用 LangBot fixture/真实 smoke 确认 reply/quote element 及文字字段，记录正文与 quote 边界；不能结构化区分就停止 quote 功能。
- [ ] 为原事故对话建立回归 fixture，断言 route、section kind、Action、pending、入队副作用和最终投影。
- [ ] 盘点本任务触及的 public/persisted/diagnostic code、template key、Action code 和非错误状态。

## Phase 1 — Error catalog 与共享 section 接口

- [ ] 建立唯一 error catalog、safe renderer 和 legacy projection，保证不反向依赖领域模块。
- [ ] 拆分重载 code，验证唯一性、snake_case、visibility、retryable 和安全默认文案。
- [ ] 注册 `supported_video_links`、`save_target_missing` 以及 save Action 的 canonical/action 投影；不新增 channel 字符串旁路。
- [ ] 增加审计，阻止本任务边界写入未注册 code/template key，同时排除 FSM/disposition。

## Phase 2 — SaveTargetSet 与自然保存提议

- [ ] 服务器从正文和结构化 quote 构建 SaveTargetSet，覆盖顺序、重复项、批量、无效 URL、scope 和短链。
- [ ] “保存但无可信目标”走 pre-model canonical section；active pending 的确认语义仍由模型选择服务器 Action。
- [ ] 新增 `offer_video_save`：精确匹配本轮目标集，先提交 pending，再生成成功 Action section。
- [ ] 让裸 URL 确认、offer 和显式 save 共用 SaveTargetSet、幂等键和 tenant/thread 校验。
- [ ] 覆盖 offer → “保存/需要” → pending 幂等消费 → 单次入队完整对话。

## Phase 3 — 引用框接入

- [ ] 扩展 bridge payload 和 `ChannelEnvelope.quoted_text`，覆盖签名、长度和旧 payload 兼容。
- [ ] 只从结构化 reply/quote element 提取；普通正文模仿引用格式不能升级为 quote。
- [ ] 将 quote 目标用于保存与 exact reference scope，不允许历史 source/text 扩大授权。

## Phase 4 — Bilibili worker 与 `b23.tv`

- [ ] 完成公开 metadata、官方字幕/文本、错误分类和 bounded yt-dlp runtime，接入 connector factory。
- [ ] 普通 BV/av 复用 raw object、chunk、embed、completion；覆盖无字幕、登录字幕、429、超时和非单视频形态。
- [ ] 实现 `b23.tv` bounded resolver：逐跳 SSRF/IP、官方终点 allow-list、重定向/时间/大小限制和无凭据转发。
- [ ] canary 未真正达到 `ready` 前，canonical 能力模板不得宣传 Bilibili 可保存。

## Phase 5 — 集成与人工复验

- [ ] 运行 Agent、channel、LangBot、submission、pending、URL、connector、worker、多用户和错误目录回归。
- [ ] 真实 BV/av 与 b23 canary 验证 ready、segment、检索引用、completion；失败不伪装成功。
- [ ] Telegram smoke 覆盖能力询问、quote 视频、Bot 提议、下一条确认、短链和官方直链；日志无正文/URL。
- [ ] 只导出一次聚焦 human review；记录 Agent 回答与 tool trace，人工填写最终结论。
- [ ] 更新 backend response-boundary、channel input、Bilibili connector 和 error-handling spec。

## Validation commands

```bash
.venv/bin/pytest -q tests/test_agent_runtime.py tests/test_agent_actions.py
.venv/bin/pytest -q tests/test_exact_video_reference_routing.py tests/test_langbot_bridge_plugin.py tests/test_http_gateway.py
.venv/bin/pytest -q tests/test_bilibili_url.py tests/test_ingest_submission.py tests/test_multiuser_integration.py
.venv/bin/pytest -q tests/test_tasks.py tests/test_ingest_completion.py tests/test_ingest_notifications.py
.venv/bin/pytest -q tests/test_error_catalog.py tests/test_diagnostics.py tests/test_mcp_server.py
python3 ./.trellis/scripts/task.py validate 08-19-trusted-response-boundary
python3 ./.trellis/scripts/task.py validate 08-18-channel-save-link-routing
```

## Review gates

- 父任务共享合同未通过，不开始 channel 领域实现。
- quote 无法结构化识别时停止，不退化为字符串猜测。
- pending 未在确认问句前提交时失败关闭。
- Bilibili canary 未达到 ready 时不发布 connector 能力。
- 新旧 code 无兼容投影时停止 catalog 迁移。
