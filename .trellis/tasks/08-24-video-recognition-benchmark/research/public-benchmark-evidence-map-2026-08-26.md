# Public benchmark evidence map（2026-08-26）

## 决策结论

公开榜单足以做“模型家族级”的预筛选，但不足以直接替当前 shortlist 的所有托管候选定胜负。已核验的公开结果主要是英文短音频，或是不覆盖普通话/英语的多语子集；它们也没有衡量本项目的时间戳级搜索命中、代表帧归因、跨帧语义、OCR 坐标/置信度协议。因此：

- 保留公开榜单作为通用能力、语言覆盖和速度/质量形状的先验；不要把不同数据集的 WER、CER 或多模态分数合成一个排名。
- `Qwen3-ASR` 家族与 `google/chirp_2` 可以作为 ASR 产品验收的优先候选；`whisper-1` 继续作为成熟的时间戳协议基线。Qwen file-transcribe 与 AssemblyAI Universal-2 的公开对应结果仍未知。
- Luna/Terra/Sol 的官方资料支持视觉输入和结构化输出，但没有给出可与 Gemini 或彼此严格可比的搜索型视觉成绩。视觉默认/fallback 仍需小规模产品验收决定。
- Google Vision、Alibaba RecognizeAllText、PP-OCRv6 的直接公开 leaderboard 对比尚未核验；OCR 默认/fallback 应按协议、部署和数据边界选，不能从 OCRBench 类分数推断坐标/置信度能力。
- 仍需保留 4–6 个公开视频、约 12–18 条查询的产品验收，验证正确视频召回、时间段命中、关键词召回、多帧/屏幕文字和实际延迟/失败率。

访问日期：2026-08-26。以下“未知”表示本次已核验材料中没有证据，不表示候选一定不支持该能力。

## 证据使用规则

1. 同一数据集、同一预处理和同一指标才可横向比较；榜单中的数值不能迁移成产品搜索召回率。
2. 开源模型结果不等于同名托管 API 结果；模型家族、权重、解码器、版本和服务配置必须分别记录。
3. WER/RTFx 只说明转写质量和速度形状，不说明词/片段时间戳是否命中本产品接受区间。
4. 官方模型卡是能力/接口和供应商自报资料，不是独立质量排名；公开结果表若未包含精确候选，保持 unknown。

## 已核验来源登记

| ID | 来源（owner / URL） | 方法、指标和领域 | 候选覆盖 | 来源性质 | 主要局限及决策用法 |
| --- | --- | --- | --- | --- | --- |
| A1 | Hugging Face for Audio；[Open ASR Leaderboard 页面](https://huggingface.co/spaces/hf-audio/open_asr_leaderboard) | 公开 leaderboard；页面说明以平均 WER（越低越好）排名，并报告 RTFx（越高越好）；可切换英文、多语、长音频和私有数据视图 | 页面/榜单展示与版本会更新；精确候选覆盖以当前结果 CSV 为准 | 公开 benchmark 服务，评测维护者组织；不是独立实验室对所有 API 的统一复刻 | 可作英文 ASR 的通用先验；不能推导中文、混合语、时间戳或本产品搜索命中。 |
| A2 | Hugging Face for Audio；[Open ASR Leaderboard 评测仓库](https://github.com/huggingface/open_asr_leaderboard)（[README](https://raw.githubusercontent.com/huggingface/open_asr_leaderboard/main/README.md)） | 英文短音频：AMI、Earnings22、GigaSpeech、LibriSpeech、SPGISpeech、VoxPopuli 等；长音频另有 Earnings/CORAAL；多语 benchmark 使用 FLEURS、MCV、MLS。README 说明公开短/多语评测通过固定 Docker/HF Jobs 硬件提高可复现性，API 模型用本地 Docker 适配器 | API 脚本列出 OpenAI Whisper、AssemblyAI 等；Qwen3 开源权重也有结果。多语脚本的已核验配置为德/法/意/西/葡 | 透明、可复现的公开社区 benchmark；API 结果仍受 provider 适配和提交状态影响 | 数据集/语言与公开视频搜索不等价；多语配置没有本项目需要的中文/英语联合结果，未测 code-switch/noise/timestamp 命中。用于家族预筛选，不用于最终默认。 |
| A3 | Hugging Face；[英文短音频 CSV](https://raw.githubusercontent.com/huggingface/open_asr_leaderboard/main/scripts/data/en_shortform.csv) | CSV 字段为平均 WER、RTFx 及 AMI/Earnings22/GigaSpeech/LibriSpeech/SPGISpeech/Tedlium/VoxPopuli 分项 WER | 当前快照可见：`Qwen/Qwen3-ASR-1.7B` 平均 WER 5.76；`assemblyai/universal-3-pro` 6.21；`google/chirp_2` 6.42。`openai/whisper-1`、AssemblyAI Universal-2、Qwen file-transcribe 未见对应行 | 公开结果快照；专有 API 的 RTFx 显示为 -1 | 只覆盖英语短音频；这些数值不能转成普通话/混合语或时间戳结论。Qwen 行是开源权重，不是 file-transcribe API；Universal-3-pro 不是 Universal-2。 |
| A4 | Hugging Face；[英文长音频 CSV](https://raw.githubusercontent.com/huggingface/open_asr_leaderboard/main/scripts/data/en_longform.csv) | Earnings21、Earnings22、Tedlium、CORAAL 等长音频 WER/RTFx | 当前快照包含 AssemblyAI Universal-3-pro、Google Chirp 3 等，但未见本 shortlist 的 Whisper-1、Universal-2、Chirp 2 或 Qwen file-transcribe 对应行 | 公开结果快照 | 不能把 Universal-3-pro/Chirp 3 的长音频结果映射到目标版本；没有时间戳级评价。只用于提醒长音频应单独验收。 |
| A5 | Hugging Face；[多语 CSV](https://raw.githubusercontent.com/huggingface/open_asr_leaderboard/main/scripts/data/multilingual.csv) | FLEURS/CoVoST/MLS 的语言分项 WER 与平均值；当前列为德/法/意/西/葡相关集合 | 当前快照包含 `assembly/universal-3-pro` 平均 3.2315、`Qwen/Qwen3-ASR-1.7B` 平均 5.1146；没有普通话或英语列，也没有 Universal-2/file-transcribe/Whisper-1 的对应可比行 | 公开结果快照 | 多语平均不能代表中文、英语或中英切换；不同语言列不可合并成产品排名。可作为多语家族信号，不能决定 API 默认。 |
| A6 | ESPnet；[ML-SUPERB 官方评测说明](https://raw.githubusercontent.com/espnet/espnet/master/egs2/ml_superb/asr1/README.md) | ML-SUPERB 说明覆盖 143 语言，含单语 ASR、多语 ASR、LID 和 ASR+LID；不同 track 使用相应 ASR/LID 指标，说明示例报告 CER/PER | 未核验 Whisper、Qwen file-transcribe、AssemblyAI、Chirp 2 等托管候选的统一结果 | 独立学术 benchmark/开源评测代码 | 适合检索多语/低资源方法和数据覆盖；不提供本 shortlist 的托管 API 同场结果，也不提供视频时间戳或搜索指标。 |
| A7 | Google；[FLEURS 数据集卡](https://huggingface.co/datasets/google/fleurs) | FLEURS/XTREME-S 资料说明覆盖 102 种语言、3 个领域和 ASR/翻译/分类/检索任务，并提供 16 kHz 音频与 transcription 字段；语言列表含普通话 `cmn_hans_cn` 与英语 `en_us` | 数据集本身覆盖目标语言；已核验的 Open ASR 多语 CSV/脚本没有给出本 shortlist 在普通话/英语上的共同结果 | Google 发布的数据集卡；不是候选模型 leaderboard | 可作为后续中文/英语统一验收或外部复核的数据基础；不能从数据集存在推断某个 API 的成绩、噪声鲁棒性或 timestamp 质量。 |
| A8 | OpenAI；[Whisper-1 官方模型文档](https://developers.openai.com/api/docs/models/whisper-1) | 官方能力卡：通用、多语语音识别/翻译/语言识别；支持音频输入；`v1/audio/transcriptions` 与翻译 endpoint；文档列价而非质量 benchmark | 精确 hosted `whisper-1` 的接口能力已核验；在 A3–A5 当前公开 CSV 快照中未见其数值行 | 官方 provider 文档，能力/接口资料；非独立 benchmark | 支持产品接入与成熟协议基线，但不能用官方卡推断其在中文、噪声或搜索时间区间的质量。 |
| A9 | OpenAI；[GPT-5.6 Luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna)、[Terra](https://developers.openai.com/api/docs/models/gpt-5.6-terra)、[Sol](https://developers.openai.com/api/docs/models/gpt-5.6-sol) 官方模型文档 | 官方资料确认三者均接受 text/image 输入；资料列出 Structured Outputs、模型层级和价格/限制等接口信息 | 未核验三者在 MMMU、ScreenSpot、ChartQA、scene-text 或视频代表帧搜索任务中的统一公开成绩；Gemini 候选同样未知 | 官方 provider 自报能力资料；非独立质量 benchmark | 可支持 Luna 默认、Terra fallback、Sol quality ceiling 的工程分层假设；最终选择必须由多帧搜索型产品验收，而不是模型卡或价格推断。 |
| V1 | MMMU；[官方评测仓库](https://github.com/MMMU-Benchmark/MMMU) | 已核验仓库元数据：面向大规模、多学科多模态理解与推理的 benchmark，并提供评测代码 | Luna/Terra/Sol/Gemini 的同版本结果未核验；其余 UI/代码/图表候选结果未知 | 独立学术 benchmark 代码仓库 | 只能作为通用多模态推理家族证据；不能代表代表帧时间关联、搜索短描述、UI/代码屏幕或场景文字，也不能直接选默认模型。 |

## ASR：shortlist 覆盖与推荐

| 当前候选 | 公开共同结果状态 | 可采纳结论 |
| --- | --- | --- |
| `whisper-1` | Open ASR API 脚本支持该模型配置，但当前 A3–A5 CSV 未见数值行 | 保留为接口/时间戳协议基线；质量排名 unknown。 |
| `qwen-audio-3.0-asr-flash-filetrans` | A3/A5 只看到 `Qwen/Qwen3-ASR-1.7B` 开源权重；不是同一服务接口或版本 | 可把 Qwen3 家族作为优先验收对象，不能把公开 WER 复制给 file-transcribe API。 |
| AssemblyAI `universal-2` | A3/A5 看到的是 `assemblyai/universal-3-pro`，不是 Universal-2 | Universal-2 结果 unknown；不能用 Universal-3-pro 排名替代。 |
| Google `chirp_2` | A3 有 `google/chirp_2` 英文短音频平均 WER 6.42；未核验中文/混合/时间戳成绩 | 可作英文家族信号和验收候选；不构成中文或产品默认结论。 |

硬门仍由产品验收提供：片段/词级时间戳 schema、可接受时间区间命中、中文/英语/中英切换、专业术语和噪声片段。公开 WER 不应豁免这些门。

## Vision：多帧、UI/代码、图表和场景文字

已核验的视觉证据只有 V1 的通用多模态 benchmark 入口与 A9 的官方图像输入/结构化输出资料。当前没有核验到把 `gpt-5.6-luna`、`gpt-5.6-terra`、`gpt-5.6-sol` 和 Gemini 候选放在相同代表帧、多图 bundle、UI/代码屏幕、图表、场景文字和搜索型短描述协议下的公开结果。

因此当前状态如下：

- MMMU 等通用推理分数（即使后续找到）只能作为家族级 prior；不能代替搜索型 frame description、跨帧证据归因或时间戳定位。
- Screen/UI、代码屏幕、图表和 scene-text 的公开 benchmark 候选及这些模型的精确结果：**unknown（本次未核验）**。
- Gemini 候选的同场结果：**unknown（本次未核验）**。
- 暂可按工程角色保留 Luna → Terra → Sol 的默认/fallback/quality ceiling 假设，但须用少量产品查询验证“更强模型是否真正提高正确视频/时间段/关键词召回”。

## OCR：引擎、坐标和置信度

当前任务 shortlist 是 Google Cloud Vision、Alibaba `RecognizeAllText` 和本地 PP-OCRv6。已核验材料没有提供它们在同一公开数据、同一图像预处理和同一输出指标（文字召回、四点/矩形坐标、置信度、小 UI 字体、中英文混排）下的直接 leaderboard 结果，三者的公开可比成绩均记为 **unknown**。

需要特别区分：

- OCRBench/OCRBench v2 等名称可用于后续查找多模态 OCR/视觉问答证据，但本次没有核验其当前候选结果；即使有 VLM OCR 分数，也不自动证明托管 OCR 引擎能返回可用坐标和置信度。
- ICDAR/scene-text、文档 OCR/DocVQA 等 benchmark 家族可帮助覆盖场景文字、文档和小字，但本次没有核验具体 leaderboard 及 Google/Alibaba/PP-OCRv6 的同场行。
- 因此不要把任何未核验的 OCRBench、ICDAR 或供应商自报分数写成 Google Vision、RecognizeAllText、PP-OCRv6 的胜负。

工程决策仍可先按协议分层：托管 OCR 作为默认候选，本地 PP-OCRv6 作为资源可行性验证后的 fallback；最终需要少量中英文屏幕帧验收文字关键词召回、框位置、置信度可用性、延迟和失败重试。

## 推荐的后续最小验收

公开证据只决定“先测谁”，不决定“产品默认是谁”。保留 4–6 个已选公开视频，冻结约 12–18 条搜索查询，至少覆盖：

- speech：中文、英语、中英切换、专业术语、时间段定位；
- visual/OCR：多帧场景、UI/代码、图表、中文/英文屏幕小字；
- combined：语音与画面共同决定结果的查询；
- 工程项：schema 一次通过、正确视频 Top-K、时间段命中、关键词召回、实际 P50/P95、失败率和用量。

这套验收不应扩展成通用模型竞赛；它只补齐公开榜单无法回答的产品问题。

## 不确定性与审计记录

- 已登记并实际核验的来源：9 个来源条目（A1–A9）加 1 个通用视觉 benchmark 入口（V1）；其中 A3–A5 是同一 leaderboard 的不同结果快照，不能按 10 个独立实验计算。
- 已记录的公开数值只限 A3/A5 中明确出现的模型与字段；没有为缺失候选补写或外推数值。
- 本次未调用任何模型/provider API，未读取 `.env`，未下载数据集或媒体；仅读取公开网页、仓库说明、结果 CSV 和官方模型文档。
- 未核验细节统一标记为 unknown，尤其是中文/英语混合、噪声、时间戳命中、Vision 搜索任务和 OCR 坐标/置信度的候选成绩。
