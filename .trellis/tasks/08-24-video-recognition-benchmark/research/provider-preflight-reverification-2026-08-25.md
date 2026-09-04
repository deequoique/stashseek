# Provider preflight 复核（2026-08-25）

状态：执行前研究快照。只核验官方文档；未调用识别供应商、未测试凭证、未修改 benchmark catalog。本文件记录研究子代理在 2026-08-25 已完成的官方核验，未核验项明确保留为 blocker。

## 结果表

| Profile | 状态 | 当前核验结论 | 调用前置条件 / 未决项 |
| --- | --- | --- | --- |
| `qwen-audio-3.0-asr-flash-filetrans` | verified | DashScope 异步文件转写模型；最长 12 小时、最大 2 GB；提供句级时间戳并可提供词级时间戳；北京/新加坡区域可用 | `DASHSCOPE_API_KEY`；临时可访问输入 URL；执行前固定区域并复核当日 CNY 价格 |
| `whisper-1` | verified | OpenAI `/v1/audio/transcriptions`；`verbose_json` 支持 segment/word timestamps；25 MB 输入限制；公开价 `$0.006/min` | `OPENAI_API_KEY`；超限音频必须保持冻结窗口而非提交整片 |
| AssemblyAI `universal-2` | verified | 预录音频模型；99 种语言含中文、支持 code switching、词时间戳和 confidence；服务限制 5 GB / 10 小时；公开价 `$0.15/hr` | `ASSEMBLYAI_API_KEY`；执行前复核异步保留和模型训练设置 |
| Google STT V2 `chirp_2` | corrected | 公开 GA；`us-central1`、`europe-west4`、`asia-southeast1`；支持 recognize/streaming/batch；batch 约 1 分钟–8 小时；词时间戳可启用 | `GOOGLE_APPLICATION_CREDENTIALS`、项目/区域/recognizer；旧的“batch 无词时间戳”假设不得继续使用 |
| `gpt-5.6-luna` | verified | 官方公开 model ID；支持图片输入、多图请求和 Structured Outputs | `OPENAI_API_KEY`；Responses/Chat Completions adapter；执行前锁定具体 snapshot、价格和数据保留设置 |
| `gpt-5.6-terra` | verified | 官方公开 model ID；支持图片输入、多图请求和 Structured Outputs | 同上 |
| `gpt-5.6-sol` | verified | 官方公开 model ID；支持图片输入、多图请求和 Structured Outputs | 同上 |
| `gemini-3.7-flash` | unresolved | 本轮研究子代理未完成官方复核，不能仅凭旧 shortlist 开启执行 | `GEMINI_API_KEY`；必须先核验公开 model ID、GA/preview 状态、结构化输出、多图限制、价格和数据条款 |
| Google Vision `TEXT_DETECTION` | verified | 返回文字与 polygon；启用 `enable_text_detection_confidence_score=true` 后返回 confidence；支持中文/多语言；同步请求最多 16 张，图像 20 MB、JSON 10 MB；首个免费层后公开价 `$1.50/1,000` | `GOOGLE_APPLICATION_CREDENTIALS`、项目/API；只传冻结帧 bytes |
| Alibaba OCR `RecognizeAllText` | verified_with_pricing_gap | 公开 RPC；`General`/`MultiLang`；bytes 最大 10 MB 或 URL；返回 block/character text、四点坐标和 0–100 confidence；官方声明公共云原图不持久化 | 阿里云凭证与 RAM `ocr:RecognizeAllText`；统一 API type 到实际价格的映射尚未复核，正式运行前必须补齐 |
| `PP-OCRv6-small` | unresolved | 本轮研究子代理未重新核验权重标识、运行时组合和当前许可/资源需求 | 不需要托管凭证；安装或运行前必须重新核验 Paddle/RapidOCR 官方模型映射，且不得默认适合常驻 2 vCPU/3.7 GiB 主机 |

## 已确认的执行 gate

- 当前环境 preflight 未发现 `DASHSCOPE_API_KEY`、`OPENAI_API_KEY`、`ASSEMBLYAI_API_KEY`、`GOOGLE_APPLICATION_CREDENTIALS` 或 `GEMINI_API_KEY`。
- 即使凭证随后出现，catalog 的人工查询真值、媒体窗口/frame bundle 和内容分类未冻结前，provider adapter 必须保持关闭。
- `gemini-3.7-flash`、`PP-OCRv6-small` 和 Alibaba OCR 精确价格在补完官方复核前不得进入正式比较。
- 本阶段只记录官方能力与前置条件，不将研究文档等同于已验证的真实调用结果。

## 官方来源

- [OpenAI models](https://developers.openai.com/api/docs/models)
- [OpenAI image inputs](https://developers.openai.com/api/docs/guides/images-vision)
- [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
- [OpenAI data controls](https://developers.openai.com/api/docs/guides/your-data)
- [OpenAI speech-to-text](https://developers.openai.com/api/docs/guides/speech-to-text)
- [Alibaba Model Studio non-realtime speech recognition](https://help.aliyun.com/zh/model-studio/non-realtime-speech-recognition-user-guide)
- [Alibaba Model Studio pricing](https://help.aliyun.com/zh/model-studio/model-pricing)
- [AssemblyAI models](https://www.assemblyai.com/docs/getting-started/models)
- [AssemblyAI retention and model training](https://www.assemblyai.com/docs/data-retention-and-model-training)
- [Google Chirp 2](https://docs.cloud.google.com/speech-to-text/docs/models/chirp-2)
- [Google Speech-to-Text data usage](https://docs.cloud.google.com/speech-to-text/docs/v1/data-usage-faq)
- [Google Cloud Vision OCR](https://docs.cloud.google.com/vision/docs/ocr)
- [Google Cloud Vision quotas](https://docs.cloud.google.com/vision/quotas)
- [Alibaba RecognizeAllText](https://help.aliyun.com/zh/ocr/developer-reference/api-ocr-api-2021-07-07-recognizealltext)
