# 时间段发现索引与字幕质量基础

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
