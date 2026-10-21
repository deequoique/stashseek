# 实施计划

> **2026-10-15：** Vision/OCR 已移除，ASR 不再测试。本文暂停执行，见同目录 `prd.md` 的“2026-10-15 方案变更”。

1. 盘点现有 evaluator、pilot gate 和 annotation-assist 输出，标出必须保留的 contract。
2. 新增 provider-neutral 自动质量报告：schema、时间戳、弱参考、检索字段、隔离、延迟、用量和失败分类。
3. 新增风险评分与抽样器，支持固定基线样本、风险触发补样和人工结果回填。
4. 将“人工 truth 未完成”从自动质量报告的硬阻断中移除，但保留正式发布/生产准入的独立 gate。
5. 生成脱敏评估事件格式，明确禁止完整媒体、字幕、URL、凭证和原始响应。
6. 用当前已有预标注报告做离线演练，不新增 provider 调用。
7. 根据工具对比决定是否创建 LangSmith/Phoenix 集成子任务；本任务只交付设计，不接 SaaS。

## Validation

- 现有视频识别测试继续通过。
- 自动报告不包含 API key、完整媒体、签名 URL、原始 provider body。
- 同一输入重复运行的自动指标稳定。
- 人工抽样规则能覆盖低置信度、代理冲突和历史失败样本。
- Trellis context validation 通过。
