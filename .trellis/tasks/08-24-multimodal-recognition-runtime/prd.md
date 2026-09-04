# 异步多模态识别运行时

## Goal

实现 RecognitionJob、独立队列、托管 ASR/视觉/OCR 适配、时间轴融合、索引发布、事件和清理收敛。

## 依赖

- Benchmark 的模型/协议 fixtures。
- Discovery index 的 revision/coverage schema。
- Public media sampling 的 MediaSampleBundle。

## 需求

- 入库预处理与问题相关识别共享幂等 Job，但拥有明确 kind、目标区间和模态集合。
- 独立 recognition queue/worker；消息只携带内部 job ID，PostgreSQL 是真相源。
- ASR/视觉/OCR 供应商适配层、真实阶段、稳定错误、用量/延迟/估算费用记录；OCR 只接收有界代表帧 bytes，不接收视频 URL 或页面身份信息。
- partial 缺口补齐、不收敛自动升级、ASR/OCR/视觉时间轴融合和 answer eligibility。
- 追加式事件、handler delivery、claim/ACK、stale recovery、删除/取消和临时媒体清理。
- 在现有 `python -m app.cli` 下提供 Recognition 测试命令组，至少包含 `probe/status/events/watch/result/cancel`；CLI 必须调用与生产路径相同的 runtime service、数据库状态机和 recognition queue，不能形成第二套供应商调用逻辑。
- `probe` 直接测试公开 YouTube/Bilibili URL、显式模态和有界时间范围；只完成 admission、幂等建 Job 与 dispatch，立即打印 `pending`/`job_id` 后退出，不等待模型。
- probe 结果有 TTL，不写资料库、Discovery Segment 或 active revision；所有命令要求有效 `--user-id` 并按 tenant 隔离。
- 普通测试使用默认 profile；仅允许从服务器配置的 benchmark profile ID allowlist 选择候选，不接受 provider key、base URL 或任意模型字符串。
- `watch` 可等待并打印追加式事件，但 Ctrl-C 只停止观察、不取消后台 Job；机器模式必须提供稳定 `--json/--jsonl`、stdout/stderr 分离和可判定退出码。

## 验收标准

- [ ] 重复/并发请求只产生一个有效任务，不重复模型调用。
- [ ] 完整字幕、部分字幕、无字幕和升级路径均原子发布正确 revision。
- [ ] at-least-once 事件重复、Worker 崩溃和 ACK 窗口不会重复发布证据。
- [ ] 删除、恢复、取消、provider failure 和媒体清理均稳定收敛。
- [ ] 首期无费用准入，但用量与估算费用完整且日志安全。
- [ ] 在服务器 shell 中可用 CLI 对公开 YouTube/Bilibili 创建 probe；命令快速输出 `pending`/`job_id` 后退出，并可用 status/events/watch/result 观察终态和供应商无关结果。
- [ ] 相同幂等请求复用 Job；错误 user、任意 URL/profile、超限范围、取消竞态和 watch 中断均满足隔离、安全与不中止后台任务的契约。

## 不在范围

- Agent 对话、候选检索实现、UI/插件、历史批量回填、公网 Recognition HTTP API、匿名识别、任意 URL/媒体上传和外部 callback/webhook。

## Planning Gate

三个上游 contract 完成后单独补齐设计与实施计划并评审。
