# 视频识别灰度上线与历史回填

## 2026-10-15 方案变更

用户移除了独立的 Vision/OCR 识别。依据见父任务 `08-24-intent-aware-video-recognition-routing/prd.md` 的“2026-10-15 方案变更”。对本任务的影响：

- 运行依赖和 admission 中的 OCR 部分作废。
- 需要灰度和 canary 的托管 provider 只剩 Fun-ASR（默认）和 `whisper-1`（备选）。

## Goal

按租户和平台灰度启用识别，验证运行依赖与 canary，有界回填历史公开 needs_asr，并完成回滚演练。

## 依赖

- 前六个子任务全部完成并归档。

## 需求

- ffmpeg/OCR/provider/recognition worker admission、配置、safe diagnostics 和临时媒体 TTL。
- 测试租户 → YouTube canary → Bilibili canary → 新提交的阶段开关。
- 可停止、可恢复、有游标的历史公开 needs_asr 回填；排除登录态/私有条目。
- 每阶段检查质量、延迟、调用量、错误、临时对象和重复任务。
- 关闭新路由/停止 Worker 的回滚，不删除前向兼容 schema、任务或证据。

## 验收标准

- [ ] 公开 YouTube/Bilibili 的完整/部分/无字幕端到端场景通过。
- [ ] pending、阶段观测、新消息中断和自动续答在真实 channel smoke 中通过。
- [ ] 历史回填可暂停/恢复且不重复处理、不包含私有媒体。
- [ ] 单一 migration head、生产 admission、全量回归和日志脱敏通过。
- [ ] 回滚演练恢复旧字幕路径，数据无需修复。

## 不在范围

- 新功能开发、私有媒体、客户端迁移和预算系统。

## Planning Gate

所有上游完成后再规划具体生产批次、阈值和维护窗口。
