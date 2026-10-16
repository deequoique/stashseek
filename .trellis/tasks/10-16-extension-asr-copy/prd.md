# 插件 ASR 状态与失败文案

## 背景

后端已经支持 ASR（任务 `10-15-asr-transcription-pipeline`，默认关闭）。2026-10-16 起，插件页面采集到的无字幕公开 YouTube 视频也会进入 ASR。但插件里与 ASR 相关的提示仍然沿用旧的假设：

- **弹窗。** `extension/src/popup.ts:95` 在 lifecycle 为 `needs_asr` 时显示“视频没有可用字幕，已标记为需要语音转写。”
  - ASR 开启时，这个视频其实正在转写；
  - ASR 关闭，或者是 NTULearn/Kaltura 视频时，这个视频不会被转写。
- **资料库状态。** `extension/src/library/lifecycle.ts`：
  - `processing` 的说明是“正在获取视频信息和字幕”，不区分是在获取字幕还是在做语音转写；
  - `needs_action` 的说明是“字幕不可用”。
- **失败提示。** `extension/src/videos/VideoDetailView.tsx` 只给 `youtube_rate_limited` 和 `bilibili_rate_limited` 提供了专门提示。`asr_*` 错误码只能显示笼统的“整理失败”。

## 需求

1. **弹窗文案按真实情况区分。**
   - 正在转写：例如“视频没有可用字幕，正在进行语音转写”。
   - 不会转写（ASR 未开启或私有视频）：例如“暂时无法自动转写”。

   插件需要知道 ASR 是否开启。可以由后端在 capabilities 或采集响应中提供一个只读标志，具体方案在设计阶段确定。
2. **资料库处理中的状态，尽量提示“语音转写中”。**
   - 后端目前用 `fetching` 同时表示获取字幕和转写，可能需要在 projection 中增加一个只读提示字段，不改动 lifecycle 枚举。
   - 设计阶段需要评估这个字段是否值得加。
3. **为下列 ASR 错误码提供中文提示和可执行的下一步：**

   | 错误码 | 含义 |
   | --- | --- |
   | `asr_no_speech` | 没有识别到语音 |
   | `asr_too_long` | 超过时长上限 |
   | `asr_audio_unavailable` | 无法获取音频 |
   | `asr_audio_too_large` | 音频超过大小上限 |
   | `asr_provider_failed` | 转写服务暂时不可用，可以重新整理 |
   | `asr_not_configured` | 未配置转写服务 |

## 约束

- 插件同时有其他任务在开发（`10-15-extension-page-qa` 及其子任务）。动手前先确认对方的进度，避免改同一批文件时互相冲突。
- 在 `dev` 分支开发，具体代码由 Sonnet 子 agent 编写。

## 验收标准

- [ ] 弹窗在“正在转写”和“不会转写”两种情况下显示不同的文案，并有对应测试。
- [ ] 资料库能显示语音转写中的状态（如果设计阶段决定提供），或者明确记录不做的理由。
- [ ] 每个 `asr_*` 错误码都有中文提示，并有对应测试。
- [ ] 后端如果新增只读字段，要同时更新 OpenAPI 和 `extension/src/api/schema.d.ts`。
