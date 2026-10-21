# 时间段发现索引与字幕质量基础

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

- Segment 的模态来源只需覆盖字幕和 ASR，视觉/OCR 来源作废。
- 媒体向量由 `09-09-media-asset-vector-index` 单独建模。

## Goal

扩展 Segment 与发现覆盖生命周期，实现字幕质量门、缺口区间、语义切分、索引 revision 和旧数据兼容回填。

## 依赖

- 必须等待 `08-24-video-recognition-benchmark` 冻结 Discovery Segment 与字幕质量 fixtures。

## 需求

- 扩展现有 Segment 的 index revision、类型、模态来源、证据引用、协议版本和 answer eligibility。
- 新增一视频一行的发现覆盖状态，active revision 原子切换，旧 revision 延后清理。
- 检查字幕时间合法性、有效文本密度、主要区间覆盖和大段缺口；部分字幕输出覆盖区间图。
- 复用并扩展现有 chapter/gap/punctuation/semantic/hard-cut 切分，保持最长边界和重叠。
- 回填旧 ready Segment，不重算 embedding；旧 needs_asr/failed 保持兼容。

## 验收标准

- [ ] 完整、部分、片头/零散和无字幕 fixtures 得到正确覆盖状态与缺口。
- [ ] Segment revision 未完整写入前不可见；切换后搜索读取单一 active revision。
- [ ] 旧 ready 条目回填为 complete 且现有引用不丢失。
- [ ] migration 单一 head、upgrade/downgrade roundtrip、模型/约束一致。
- [ ] feature flag 关闭时旧字幕摄取与检索继续运行。

## 不在范围

- 公开媒体获取、ASR/视觉/OCR 执行、Agent pending 和线上回填。

## Planning Gate

到达本阶段时补齐独立设计/实施计划，显式引用 benchmark fixtures 后再启动。
