# 视频发现检索与多模态证据门

## 2026-10-15 方案变更

用户移除了独立的 Vision/OCR 识别。依据见父任务 `08-24-intent-aware-video-recognition-routing/prd.md` 的“2026-10-15 方案变更”。对本任务的影响：

- 显式画面或屏幕文字问题的证据，不再来自独立视觉/OCR 识别结果。
- 改为如何使用多模态 embedding（`09-09` 工作流）的证据，在本任务规划时确定。
- 字幕和 ASR 证据门不变。

## Goal

实现候选视频检索、partial/complete 覆盖投影、answer eligibility、模态证据校验和可信引用边界。

## 依赖

- Discovery index schema/revision。
- Recognition runtime 的 segment kinds、模态和 answer eligibility。

## 需求

- `search_video_candidates` 返回候选视频、时间段、覆盖状态和发现摘要，不进入详细 Citation cache。
- `search_segments` 只返回当前 active revision、answer-eligible 且满足所需模态的证据。
- `none` 不参与内容语义检索；`partial` 只搜索已发布区间并标记覆盖不完整。
- 保持租户、归档/删除、精确条目、Top-K 视频多样化、邻居和时间戳 URL 边界。
- 字幕不能满足显式视觉/OCR/声音问题，候选摘要不能支持详细事实。

## 验收标准

- [ ] 正确视频 Top-K 和时间段命中达到 benchmark 门。
- [ ] none/partial/complete/failed 状态投影正确且不伪造内容命中。
- [ ] 候选与证据缓存逻辑隔离，伪造 citation fail closed。
- [ ] tenant、删除/恢复、archive、exact item 和 prompt injection 回归通过。
- [ ] feature flag 关闭时旧 `search_segments` 行为可恢复。

## 不在范围

- 媒体/模型执行、pending turn 和 rollout。

## Planning Gate

两个上游子任务完成后单独补齐设计与实施计划。
