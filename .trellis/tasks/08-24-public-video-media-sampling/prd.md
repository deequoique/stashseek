# YouTube 与 Bilibili 公开视频采样

## Goal

实现后端公开媒体获取、ffmpeg 安全边界、语音活动/场景变化/去重采样，以及条件式 OCR 所需的文字密度信号与候选帧输入。

## 依赖

- `08-24-video-recognition-benchmark` 的 MediaSampleBundle/OCR fixtures。
- 消费 `08-24-discovery-index-foundation` 的缺口区间协议；不拥有发现 schema。

## 需求

- 独立 YouTube/Bilibili MediaAdapter，统一输出有界媒体对象与稳定错误。
- ffmpeg/ffprobe admission、有界子进程、TLS/proxy 继承、临时媒体和清理意图。
- 时间覆盖 + VAD + 场景变化 + 感知去重 + 局部加密；部分字幕只采样缺口区间。
- 文字密集判断和条件式 OCR 候选帧选择；本任务不调用托管视觉、ASR 或 OCR。
- 不接收 Cookie、令牌或签名 URL，不支持登录态/私有/DRM 媒体。

## 验收标准

- [ ] 两个平台的公开 fixtures 和真实 canary 均生成协议一致的 MediaSampleBundle。
- [ ] 长视频样本满足冻结后的采样工作量和时间覆盖门。
- [ ] 限流、登录要求、地区限制、删除、超限、格式变化和子进程超时映射为稳定错误。
- [ ] 日志/任务载荷不含媒体内容、签名 URL、stderr、临时路径或凭证。
- [ ] 成功、失败、取消和崩溃恢复均能清理临时对象。

## 不在范围

- 托管模型调用、RecognitionJob 状态机、搜索和 Agent。

## Planning Gate

到达本阶段时补齐独立设计/实施计划并评审运行依赖与真实 canary 范围。
