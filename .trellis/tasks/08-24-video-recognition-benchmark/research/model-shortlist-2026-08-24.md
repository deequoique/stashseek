# 视频发现识别模型 Shortlist（2026-08-24）

> **历史文档（已被取代，2026-08-26）**：本文件保留原“全候选/全 12 视频”基准执行计划，仅用于 provenance 与回归参考。该计划已由 `prd.md`、`design.md` 和 `public-benchmark-evidence-map-2026-08-26.md` 取代；不得据此授权 provider 调用，也不得据此执行固定 3 次/全候选运行。

## 结论

首轮 benchmark 固定为 4 个 ASR profile、4 个 Vision profile 和 3 个 OCR profile。候选先满足搜索召回、时间定位和协议稳定性，再比较延迟、调用量、费用与运维复杂度；本阶段不做预算准入。

这不是通用模型排行榜。候选必须符合本产品的硬约束：公开视频预处理、普通话/英语/中英混合、时间戳级搜索、代表帧联合理解、JSON Schema，以及 OCR 的坐标与置信度。

## ASR shortlist

| 候选 | 首轮角色 | 入选原因 | 需要验证的风险 |
| --- | --- | --- | --- |
| `qwen-audio-3.0-asr-flash-filetrans` | 中文与中英混合主候选 | 当前官方推荐的非实时 ASR；覆盖普通话、方言、英语等多语种，固定返回句级和字/词级时间戳，支持热词与 Prompt 上下文，适合公开视频异步转写 | 模型发布较新；文件转写接口使用公网可访问 URL，benchmark adapter 需要临时受控 URL；必须实测中英切换、代码术语和时间戳稳定性 |
| `whisper-1` | 时间戳协议基线 | OpenAI 官方明确推荐在需要词级或片段级时间戳时使用它；多语言、接口成熟，能直接输出 `verbose_json` | 模型较老，中文专有名词、噪声与 code-switching 质量可能落后；不支持文件转写流式返回 |
| AssemblyAI `universal-2` | 低价多语言跨供应商对照 | 覆盖 99 种语言（含中文），官方列出 code switching；响应默认包含逐词时间和置信度；预录音频按秒计费 | 不是 AssemblyAI 当前最高精度档；`universal-3-pro` 尚不支持中文，因此要验证 Universal-2 在中文技术内容上的召回 |
| Google Cloud STT V2 `chirp_2` | 企业云与时间戳对照 | 支持简体中文和英语，并原生提供词级时间戳；适合验证 Google ASR 在课程、幻灯片讲解和噪声素材上的表现 | 需要实测单条音频内中英切换；区域、存储和 recognizer 配置比其他 API 更重 |

首轮所有 ASR 均使用相同的本地有界音频和统一输出协议；供应商需要 URL 时由 adapter 建立短期临时对象，不能把 URL 或供应商原始响应写入 fixture。

### ASR 暂不入选

- `gpt-transcribe`：适合作为纯文本准确率候选，也支持关键词和多语言提示，但 OpenAI 当前 Speech-to-Text 指南明确要求“需要词/片段时间戳时使用 `whisper-1`”。它不满足当前 discovery ASR 的原生时间定位硬门，先不进入首轮；后续可作为按需详细转写的二阶段候选。
- `gpt-4o-transcribe` / `gpt-4o-mini-transcribe`：当前 Transcriptions API 只支持 `json` 响应，不能得到本任务需要的 `verbose_json` 词/片段时间戳。
- Deepgram `nova-3` multilingual：当前官方语言表未列中文，不能覆盖普通话与中英混合硬约束。
- Google `chirp_3`：支持中文且整体更新，但官方当前注明批处理不支持词级时间戳，utterance 时间戳仅在 streaming 路径可用；首轮选择 `chirp_2` 做可比的离线时间戳评估。
- AssemblyAI `universal-3-pro`：当前仅覆盖英语、西语、德语、法语、葡语和意大利语，不覆盖中文。

## Vision shortlist

所有 Vision profile 只接收同一批带时间戳的代表帧，不直接上传完整视频。输出统一限制为搜索型字段：`scene_type`、一句短描述、实体/动作、屏幕主题、搜索词、证据帧时间和置信度；不要求逐帧详解。

| 候选 | 首轮角色 | 入选原因 | 固定配置方向 |
| --- | --- | --- | --- |
| `gpt-5.6-luna` | 低成本默认候选 | OpenAI 当前面向高吞吐、成本敏感负载的档位；支持图片输入和 Structured Outputs | `reasoning.effort=none/low`，低输出上限，按 bundle 多图输入 |
| `gpt-5.6-terra` | 实用质量 fallback | 在 intelligence 与 cost 之间的中档；当 Luna 漏掉图表、录屏状态或细粒度场景时，判断中档能否补足 | 与 Luna 使用完全相同 schema、图片 detail 和提示词 |
| `gpt-5.6-sol` | 质量上限 | 当前 GPT-5.6 frontier 档，支持图片与 Structured Outputs；用于测量搜索任务能从更强视觉推理获得多少实际增益 | 不作为预设生产默认，只作为质量 ceiling；输出仍保持简短 |
| `gemini-3.7-flash` | 跨供应商对照 | 当前 GA 的原生多模态 Flash 模型，支持图片输入和 Structured Outputs；可检验 OpenAI 家族共同偏差 | 首轮禁用直接视频/音频输入，只喂与其他候选相同的代表帧 |

`gpt-4o-mini` 暂列 reserve：它仍支持图片和 Structured Outputs，价格低，但与 `gpt-5.6-luna` 的角色高度重叠且模型更旧。若 Luna 出现延迟或账号可用性问题，再补入第二轮。

Claude 视觉模型暂列 reserve：Claude 支持多图联合分析和 JSON Schema，但首轮已有一个非 OpenAI 对照，继续扩大会增加样本调用数而不提高协议覆盖。若 Gemini 失败或表现异常，再以当前可用的 Haiku/Sonnet 档补测。

## OCR shortlist

| 候选 | 首轮角色 | 入选原因 | 需要验证的风险 |
| --- | --- | --- | --- |
| Google Cloud Vision `TEXT_DETECTION` | 托管默认候选 | 面向一般图片中的稀疏文字，支持单图多语言和中文；响应提供完整文本、字词、边界框及层级置信度，直接覆盖标准协议 | 必须实测屏幕小字、代码、字幕/水印干扰、加拿大区延迟和跨境数据边界 |
| 阿里云 OCR `RecognizeAllText`（`General`/`MultiLang`） | 中文专项对照 | 返回文字块/单字内容、0–100 置信度和四点坐标，适合普通话课程、中文幻灯片及中英混排 | API/地域和鉴权接入更重；需要验证新加坡或北京 endpoint 的延迟、可用性及代码/UI 文字表现 |
| `PP-OCRv6_small` detection + recognition | 本地质量/降级对照 | 单模型覆盖中英及多语种，recognition 模型约 20 MB；用于判断外接 OCR 的质量收益，并保留断网或供应商不可用时的降级可能 | 当前 2 vCPU/低可用内存主机尚未安装运行时；不得预设能作为生产常驻默认 |

Azure Vision Image Analysis 4.0 Read 暂列 reserve。它支持中文，并返回逐词文字、四点多边形和置信度；若 Google/阿里任一候选不可用、协议异常或共同漏识别，再补入第二轮跨云对照。

`PP-OCRv6_tiny/medium` 和 Tesseract 5 暂不进入首轮：tiny 可能牺牲关键小字召回，medium 不符合当前主机资源方向，Tesseract 既未安装又大概率弱于神经 OCR。RapidOCR 不是独立模型；它只是 PP-OCR 权重的部署层，首轮本地对照可用 RapidOCR + ONNX Runtime 跑 small，但评分时模型与运行时分开记录。

### 当前 Linux 生产环境可行性

2026-08-24 对正确的生产目标 `51.79.159.110` 做了只读核验：Ubuntu 26.04 LTS、x86_64、2 vCPU（Haswell，支持 AVX2）、总内存 3.7 GiB、检查时约 1.1 GiB available、无 swap、无 NVIDIA GPU。主机尚未安装 Tesseract、FFmpeg、ONNX Runtime、RapidOCR、PaddleOCR 或 PaddlePaddle。检查时 LangBot 约占 658 MiB RSS，多个现有 Celery 进程合计占用较多内存，因此不能把模型大小直接当作运行时内存。

此前误查的 `175.24.197.99` 主机数据已废弃，不参与本任务决策。

该环境是把首期默认改为托管 OCR 的主要原因。生产边界调整为：

- 默认只对去重后的代表帧和文字疑似帧运行，不连续扫描视频帧。
- `OCRAdapter` 默认把有界代表帧 bytes 提交给胜出的托管 OCR，不提交视频 URL、页面 URL、签名 URL、Cookie 或令牌。
- 托管响应立即规范化为 `text + polygon + confidence + frame_timestamp`，原始响应和临时帧按 run 清理。
- `PP-OCRv6_small` 只做本地 benchmark/降级对照；若未来启用，必须独立 `ocr` queue、concurrency 1、进程回收和内存上限，不能让现有 Celery prefork 子进程各自加载模型。
- 私有/登录态视频仍不进入首期；未来接入前必须单独批准第三方 OCR 处理方、区域和保留策略。

这里的结论不代替 benchmark。首轮托管 OCR 记录响应稳定性、P50/P95、失败率、图片调用数和估算费用；本地对照另记录冷启动、热运行、峰值 RSS、单帧 CPU 时间和队列等待时间。

## 首轮调用矩阵

- ASR：4 profiles × 每个冻结音频样本 3 次。
- Vision：4 profiles × 每个冻结代表帧 bundle 3 次。
- OCR：3 profiles × 每个冻结帧 3 次；托管候选记录结果稳定性、延迟、失败和图片用量，本地对照另记录一次冷启动与三次热运行的资源数据。
- 别名与供应商返回的模型版本同时记录。供应商没有可固定 snapshot 时，保存查询日期和响应版本元数据，后续模型变化必须新建 benchmark revision。

## 晋级规则

- ASR：时间戳 schema、跨运行漂移和可接受时间范围命中先过门；纯文本 WER 更好但无法稳定定位的候选淘汰。
- Vision：JSON Schema 一次通过、查询所需实体/动作/场景召回和证据帧归因先过门；更长描述不加分。
- OCR：关键屏幕词召回、框位置和置信度可用性先过门；只在文字密集或视觉模型提示需要精确文字时触发，不全帧常开。

## 官方资料（查询日期：2026-08-24）

### ASR

- [OpenAI Speech-to-Text：timestamps](https://developers.openai.com/api/docs/guides/speech-to-text#timestamps)
- [OpenAI Transcriptions API](https://developers.openai.com/api/reference/resources/audio/subresources/transcriptions/methods/create)
- [OpenAI GPT-Transcribe](https://developers.openai.com/api/docs/models/gpt-transcribe)
- [OpenAI Whisper](https://developers.openai.com/api/docs/models/whisper-1)
- [阿里云百炼：语音识别模型](https://help.aliyun.com/zh/model-studio/asr-model)
- [阿里云百炼：非实时语音识别与时间戳](https://help.aliyun.com/zh/model-studio/non-realtime-speech-recognition-user-guide)
- [阿里云百炼：模型价格](https://help.aliyun.com/zh/model-studio/model-pricing)
- [AssemblyAI models](https://www.assemblyai.com/docs/getting-started/models)
- [AssemblyAI pricing](https://www.assemblyai.com/pricing/)
- [Google Chirp 2](https://docs.cloud.google.com/speech-to-text/docs/models/chirp-2)
- [Google Chirp 3](https://docs.cloud.google.com/speech-to-text/v2/docs/chirp-model)
- [Google Speech-to-Text pricing](https://cloud.google.com/speech-to-text/pricing)
- [Deepgram models and languages](https://developers.deepgram.com/docs/models-languages-overview/)

### Vision

- [OpenAI current model catalog](https://developers.openai.com/api/docs/models)
- [OpenAI GPT-5.6 Luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna)
- [OpenAI GPT-5.6 Terra](https://developers.openai.com/api/docs/models/gpt-5.6-terra)
- [OpenAI GPT-5.6 Sol](https://developers.openai.com/api/docs/models/gpt-5.6-sol)
- [Google Gemini 3.7 Flash](https://ai.google.dev/gemini-api/docs/models/gemini-3.7-flash)
- [Claude vision](https://platform.claude.com/docs/en/build-with-claude/vision)
- [Claude structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)

### OCR

- [Google Cloud Vision OCR](https://docs.cloud.google.com/vision/docs/ocr)
- [Google Cloud Vision OCR language support](https://docs.cloud.google.com/vision/docs/languages)
- [Google Cloud Vision pricing](https://cloud.google.com/vision/pricing)
- [阿里云 RecognizeAllText](https://help.aliyun.com/zh/ocr/developer-reference/api-ocr-api-2021-07-07-recognizealltext)
- [Azure Vision Image Analysis 4.0 Read OCR](https://learn.microsoft.com/en-us/azure/ai-services/computer-vision/concept-ocr)
- [PaddleX PP-OCRv6 recognition models](https://paddlepaddle.github.io/PaddleX/latest/module_usage/tutorials/ocr_modules/text_recognition.html)
- [RapidOCR PP-OCRv6 runtime/model mapping](https://rapidai.github.io/RapidOCRDocs/main/model_list/)
