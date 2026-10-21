# Agent 异步识别与自动续答

## 2026-10-15 关闭

本任务已关闭归档，由 `10-15-asr-transcription-pipeline` 取代。

关闭原因：

- 用户移除了 Vision/OCR，视频方案改为“多模态 embedding + ASR”；
- ASR 不再测试，直接选定 Fun-ASR 默认、`whisper-1` 备选。

ASR 只需在入库时对无字幕视频做一次整段转写（Fun-ASR 约 ¥0.79–0.94/音频小时），原先为按需视觉/OCR 设计的发现索引、意图路由、异步识别续答和稀疏采样架构不再需要。

本任务中仍有价值的已确认决策，已在新任务 PRD 中继承：

- 只处理公开 YouTube/Bilibili，由后端获取；
- 首期不做预算或额度控制，但记录用量；
- 无字幕视频自动处理，不需逐条确认。

画面检索见 `09-09-wemm-multimodal-embedding-migration`。

## 2026-10-15 方案变更

用户移除了独立的 Vision/OCR 识别。依据见父任务 `08-24-intent-aware-video-recognition-routing/prd.md` 的“2026-10-15 方案变更”。对本任务的影响：

- 需要新识别的异步路由只剩 ASR。
- vision/ocr 识别路由作废。
- 画面类问题如何接入多模态 embedding 检索和 `09-09-inspect-media-agent-tool`，在本任务规划时确定。

## Goal

在现有主 Agent 中实现结构化模态路由、recognition_pending、事件观测、中断和 PendingAnswer 自动续答。

## 依赖

- Recognition runtime 的 Job/event contract。
- Retrieval/evidence gate 的两个读面与模态充分性结果。

## 需求

- 确定性高置信提示 + 现有主 Agent 结构化工具路由 + 服务端证据门；不增加独立分类 LLM。
- 所有新识别立即 pending；缓存命中可同步回答。
- pending turn 不进入普通 completed 历史；保存原问题和服务器验证目标快照。
- 阶段事件驱动汇报，不保持长模型请求、不高频轮询、不伪造百分比。
- 新用户问题中断前台等待但不取消任务；完成后重新校验并续答原问题。
- 多 pending 可乱序完成，不串答、不复用最新消息重建旧目标。

## 验收标准

- [ ] text/audio/vision/ocr/组合/澄清路由通过离线意图集。
- [ ] 新识别请求在固定短路径内返回 pending，断线不取消任务。
- [ ] 新消息即时处理；旧任务完成后答案关联回原消息。
- [ ] 重复事件、两个并发 pending、乱序完成和 continuation claim 崩溃均幂等。
- [ ] 删除/权限失效/provider failure 产生稳定终态，不编造答案。

## 不在范围

- 模型执行、索引 schema、UI/插件迁移和历史回填。

## Planning Gate

Recognition 与 Retrieval 两个上游完成后单独补齐设计与实施计划。
