# 低人工成本的视频识别评估方案与工具选型

## 2026-10-15 方案变更

用户移除了 Vision/OCR 识别，并决定不再测试 ASR 模型：默认 Fun-ASR、备选 `whisper-1`，依据见父任务 `08-24-intent-aware-video-recognition-routing/prd.md`。因此本任务要评估的“ASR/Vision/OCR 模型选型”已经不存在：

- Vision/OCR 的代理指标、抽样和工具选型不再需要；
- ASR 不再做模型对比。

**2026-10-15 用户决定关闭本任务并归档，不改作质量监控。** 

关闭时的状态：

- 验收标准都未完成。
- `evals/video_recognition/quality_report.py` 和 `sampling.py` 在 2026-09-07 有未提交的改动，时间与本 PRD 的最后修改相同，看起来对应 `implement.md` 的步骤 2–3（自动质量报告、风险抽样）。
- 这些代码是否保留，由 `08-24-video-recognition-benchmark` 的 ASR-only 收口工作决定。

## Goal

将当前“逐条人工制作 query truth、再人工审核全部模型结果”的视频识别
evaluation，改造成自动检查为主、少量风险抽查为辅的流程，降低持续迭代成本。

## Requirements

- 保留现有 provider-neutral schema、时间戳和搜索索引 contract。
- 不要求人工为每个视频制作完整 transcript、OCR truth 或逐条时间边界。
- 有字幕/标题/描述/章节时，优先将它们作为弱参考信号，而非绝对真值。
- 自动检查 schema、时间戳范围、单调性、跨视频证据隔离、文本可检索性、失败分类、延迟和用量。
- 对 ASR、Vision、OCR 分别定义可计算的代理指标；不能计算的质量只进入抽样审核。
- 人工审核只抽查高风险或高不确定性样本，并能根据失败结果动态加样。
- LangSmith、Phoenix、Braintrust 等平台只接收脱敏的输入摘要、预测结果、评分和 trace，不上传完整视频、音频、字幕、API key 或原始 provider 响应。
- 先产出工具选型结论和最小可行集成方案，再决定是否引入外部平台依赖。

## Acceptance criteria

- 有一份自动评估指标与人工抽样规则文档。
- 有一份 LangSmith/Phoenix/Braintrust/本地方案对比，按视频时间戳、视觉/OCR、人工审核、脱敏和部署成本评价。
- 现有 pilot gate 不再要求 12 条 query truth 全部人工冻结才能进行离线质量检查。
- 至少定义一轮小样本抽查预算：每模态固定少量样本 + 风险触发补样。
- 形成可执行的后续子任务拆分，且不修改生产 ingestion、队列或搜索 schema。

## Out of scope

- 本任务不直接接入 LangSmith 或其他 SaaS。
- 不把外部平台评分当作唯一质量结论。
- 不重新跑公开视频或扩大 provider 调用范围。
