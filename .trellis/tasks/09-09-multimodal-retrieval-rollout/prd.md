# 混合检索、迁移与灰度验收

## 2026-10-15 方案变更

视频方案改为“多模态 embedding + ASR”，独立 OCR 识别已移除。依据见已归档的 `archive/2026-10/08-24-intent-aware-video-recognition-routing/prd.md` 的“2026-10-15 方案变更”；ASR 实现由 `10-15-asr-transcription-pipeline` 负责。

- 下文“OCR/ASR”中，ASR 文本来自 Fun-ASR（默认）或 `whisper-1`（备选）。
- OCR 文本还有没有来源（例如 `inspect_media` 的按需结果），需要在实施时重新确认。

## 2026-10-15 路由决定：三路合并

用户决定：不再由主 Agent 自己判断“什么时候走 WeMM”，每次检索固定执行三路召回，再合并结果。

- 三路：① BM25 关键词（文本 Segment，含 ASR 文本）；② 智谱向量（文本 Segment，1536 维）；③ WeMM 向量（图片/视频片段，1024 维，查询文本也用 WeMM 编码）。
- 合并后由一次检索调用返回，主 Agent 不再在 `search_segments` / `search_media` 之间二选一，原提示词中“视频问答先搜文字”与“视觉问题用 search_media”的冲突随之取消。
- WeMM 候选仍只是候选；要作为回答证据，仍需 `inspect_media` 核验（见父任务 2026-10-15 分工）。
- feature flag 关闭时退回现有两路（①②），行为与现在一致。
- 合并方式、权重和 WeMM 不可用时的降级策略见 `design.md`，最终参数以 `09-09-wemm-provider-benchmark` 的结果为准。

## 目标
把文本、媒体向量、BM25、OCR/ASR 和结构化过滤组合成统一检索面，完成旧文本兼容、回填、离线评测、灰度和回滚。

## 验收
- [ ] 文本查询、图片查询和跨模态查询有统一结果协议。
- [ ] 旧 `Segment.embedding` 路径和新媒体路径可并行运行。
- [ ] 回填可暂停、重试、按版本重建，不覆盖旧向量。
- [ ] feature flag off 后旧文本行为回归通过。
- [ ] 具备质量、延迟、调用量、失败率和缓存命中指标。
