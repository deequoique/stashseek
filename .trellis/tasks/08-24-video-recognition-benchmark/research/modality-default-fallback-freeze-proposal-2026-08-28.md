# ASR / Vision / OCR 默认与 fallback 冻结提案（2026-08-28）

## 状态与边界

这是研究阶段的 pilot 选择提案，不是生产配置，也不授权任何 provider 调用。正式执行仍须完成 pilot 的人工查询真值、媒体窗口/代表帧冻结和执行 gate。首阶段不以预算作为准入门槛，但仍记录延迟、调用量和估算费用。

本提案只使用已有研究材料：

- [公开 benchmark 证据地图](public-benchmark-evidence-map-2026-08-26.md)：访问日期 2026-08-26；
- [Provider preflight 复核](provider-preflight-reverification-2026-08-25.md)：官方能力、凭证和运行前置条件，访问日期 2026-08-25；
- [OpenAI synthetic smoke](openai-synthetic-smoke-2026-08-26.md)：仅证明最小接口兼容性，访问日期 2026-08-26；
- [Shortlist](model-shortlist-2026-08-24.md)、`prd.md`、`design.md` 以及当前 registry `evals/video_recognition/catalog.yaml`。

公开 benchmark 的不同比较不合并为总排名。下表是“按产品政策和协议先选一个默认、一个 fallback”，不是质量冠军声明。

## 提案总表

| 模态 | 默认 profile | fallback profile | 研究选择状态 | 仍未证明的事项 |
| --- | --- | --- | --- | --- |
| ASR | `asr-qwen-filetrans` / `qwen-audio-3.0-asr-flash-filetrans` | `asr-whisper-1` / `whisper-1` | `proposed_for_pilot` | Qwen file-transcribe 的真实产品质量、混合语和 timestamp 命中；没有四候选同场中文榜单 |
| Vision | `vision-gpt-5-6-luna` / `gpt-5.6-luna` | `vision-gpt-5-6-terra` / `gpt-5.6-terra` | `proposed_for_pilot` | 三个 GPT-5.6 档位与 Gemini 在代表帧搜索任务上的质量差异 |
| OCR | `ocr-google-cloud-vision` / `TEXT_DETECTION` | `ocr-aliyun-recognize-all-text` / `RecognizeAllText` | `proposed_for_pilot` | 两个托管 OCR 在中英文小字、代码/UI 和真实帧上的召回/框质量；同场公开 leaderboard 未核验 |

“proposed_for_pilot”表示可以作为下一步小规模产品验收的唯一默认/fallback 对；不表示已经通过产品门槛。若 pilot 结果不足以让默认通过，报告应保留失败原因，而不是启用第三个候选。

## ASR 提案

### 选择

- 默认：`asr-qwen-filetrans`。
- fallback：`asr-whisper-1`。
- 允许触发：`default_quality_gate_failed`、`default_protocol_failed`、`default_provider_unavailable`。

### 证据与产品适配

Qwen 的官方 preflight 记录确认非实时文件转写、普通话/英语等多语能力以及句级和词级时间戳，直接对应当前“必须能定位时间段、且包含中文/英语/中英混合”的产品硬约束。公开 Open ASR 结果只给出 `Qwen/Qwen3-ASR-1.7B` 开源权重的英文/部分多语行，并不是 `qwen-audio-3.0-asr-flash-filetrans` 服务结果；它只能作为家族级先验，不能当成默认胜出证据。

Whisper 的官方文档和 preflight 确认 `verbose_json` 的 segment/word timestamp 接口；OpenAI synthetic smoke 已证明 `whisper-1` 的最小请求成功。因此它是最可审计的协议与运行基线，但公开结果快照没有当前 hosted `whisper-1` 的同场质量行，不能据此声称质量领先。

### 凭证与运行依赖

| profile | 依赖 | 风险边界 |
| --- | --- | --- |
| Qwen file-transcribe | `DASHSCOPE_API_KEY`；北京或新加坡区域；短期受控公网输入 URL | adapter 必须只暴露有界音频、限制 URL 生命周期和访问范围；不得提交完整视频 URL 或保存供应商原始响应 |
| Whisper | `OPENAI_API_KEY`；本地有界音频；单文件不超过官方 25 MB 限制 | 需要在提交前切分窗口；synthetic smoke 成功不等于真实媒体质量通过 |

### 为什么不把其他候选设为默认

- AssemblyAI `universal-2` 的官方能力与语言/词时间戳资料匹配，但公开榜单看到的是 `universal-3-pro`，不是 `universal-2`；没有同场版本证据，保留为未触发候选。
- Google `chirp_2` 在公开英文短音频 CSV 有一行，但没有中文/混合/产品时间命中证据，且 GCP recognizer/项目配置更重；不据此替换 Qwen 或 Whisper。
- 三个候选之间的 WER 不可跨数据集、版本和 API 形态合并。默认的依据是产品语言/时间戳优先级与协议风险，而非一个综合榜单分数。

## Vision 提案

### 选择

- 默认：`vision-gpt-5-6-luna`。
- fallback：`vision-gpt-5-6-terra`。
- 允许触发：`default_quality_gate_failed`、`default_protocol_failed`、`default_provider_unavailable`。

### 证据与产品适配

OpenAI 官方模型资料确认 Luna、Terra、Sol 均支持 image input 与 Structured Outputs；synthetic smoke 记录三者最小图像请求均成功。当前产品是搜索型预处理，只需对代表帧生成短场景描述、实体/动作、屏幕主题、搜索词和证据帧时间，不要求长篇逐帧视觉理解。因此 Luna 作为高吞吐默认符合当前明确的“搜索摘要优先”政策；Terra 作为同协议、更高智能档 fallback，用于默认在图表、录屏状态或跨帧语义上通过不了产品门时补足。

这不是公开质量排名：MMMU 仅能作为通用多模态推理 benchmark 入口，本次没有核验 Luna/Terra/Sol/Gemini 在同一代表帧、多图、UI/代码、图表和场景文字任务上的结果。Sol 保留为质量 ceiling 研究候选，不进入本轮唯一 fallback，避免把“更强”未经产品证据直接当作更适合搜索。

### 凭证与运行依赖

- 两个 profile 均依赖 `OPENAI_API_KEY`，使用与 smoke 相同的图片输入/结构化输出 adapter；只发送冻结的代表帧 bundle，不发送完整视频。
- 当前 smoke 只证明 OpenAI 三个 endpoint 的最小兼容性和清理路径；未证明真实帧召回、跨帧证据归因或时间段命中。
- Gemini `GEMINI_API_KEY`、模型状态、多图限制和结构化输出 preflight 仍未完成，不作为本轮 default/fallback。

## OCR 提案

### 选择

- 默认：`ocr-google-cloud-vision`。
- fallback：`ocr-aliyun-recognize-all-text`。
- 允许触发：`default_quality_gate_failed`、`default_protocol_failed`、`default_provider_unavailable`。

### 证据与产品适配

Google Cloud Vision preflight 已确认 `TEXT_DETECTION` 可返回文字与 polygon，并可启用 confidence；官方语言资料覆盖中文/多语言。它不需要在目标 Linux 主机常驻模型，适合作为托管默认，且直接覆盖协议要求的 `text + polygon + confidence + frame_timestamp`（其中 frame timestamp 由本地输入 bundle 绑定）。

Alibaba `RecognizeAllText` preflight 已确认支持 `General`/`MultiLang`、文字块/字符、四点坐标和 0–100 confidence；中文屏幕文字是它最有针对性的 fallback 理由。其官方资料还记录公共云原图不持久化，但区域、RAM 权限和服务条款仍要在执行前确认。

当前没有核验 Google Vision、RecognizeAllText、PP-OCRv6 在同一 OCRBench/ICDAR/文档数据、同一预处理和同一坐标/置信度指标下的 leaderboard 结果。OCRBench 类分数（若未来找到）也不能自动证明托管 OCR 的框坐标和 confidence 可用。

### 凭证与运行依赖

| profile | 依赖 | 风险边界 |
| --- | --- | --- |
| Google Cloud Vision | `GOOGLE_APPLICATION_CREDENTIALS`、GCP project/API；只传冻结帧 bytes | 仅在文字疑似帧/查询需要时调用；遵守图片大小和请求上限；不传视频页面 URL、Cookie 或签名 URL |
| Alibaba RecognizeAllText | 阿里云凭证、RAM `ocr:RecognizeAllText` 权限、固定地域；bytes 或受控 URL | 执行前补齐精确价格/地域和保留条款；不因“中文专项”绕过统一输出协议 |

`ocr-pp-ocrv6-small` 不列为 fallback：preflight 尚未重新核验权重/运行时/许可，且目标 Linux 主机只有约 3.7 GiB 内存、无 GPU；它仍可作为未来本地降级研究项，但不能用“本地免费”推断适合常驻生产。

## Fallback 触发合同（所有模态统一）

默认 profile 每个 sample/modality unit 先运行一次。网络或 provider 的瞬时失败、以及需要确认的协议不稳定，先按现有 pilot retry policy 使用允许原因 `provider_transient` / `protocol_instability`，最多补 2 次。只有重试后仍满足以下一种稳定条件，才允许运行该模态唯一 fallback：

1. `default_provider_unavailable`：默认 provider 在凭证/区域/服务状态确认后仍不可用，或重试耗尽仍为 provider failure；
2. `default_protocol_failed`：默认返回无法满足统一 schema/必需时间戳、坐标、置信度或证据归因要求，且协议重试后仍失败；
3. `default_quality_gate_failed`：默认响应协议有效，但未通过已冻结的产品硬门（正确视频/时间段、关键术语、视觉/OCR 召回等）。

明确不允许以下情况触发 fallback：单纯成本或延迟更高、输出描述较短、与另一榜单分数不可比、未冻结的人工偏好、或为了生成内部排行榜。fallback 本身失败后不再选择第三个模型；记录 stable error/质量失败并结束该 unit。Fallback 触发必须保存脱敏的 trigger、默认 profile、fallback profile、输入 unit identity 和最终状态，不保存原始响应、凭证或完整媒体。

## 冻结前最小确认与结论

本提案不需要新增模型政策选择：它沿用已经确认的三条产品政策——ASR 优先普通话/英语/中英混合与时间定位、Vision 只需搜索型短理解、OCR 允许托管但必须返回文字/坐标/置信度。若产品负责人改为“视觉质量优先且不考虑高吞吐成本”，最小变更是把 Vision default 从 Luna 改为 Terra，并保留 Sol 为未选候选；这不是本提案默认假设。

下一步只需在不改候选的前提下完成：

- 人工冻结 4 个核心样本的 12 条查询（必要时扩展到 18 条）、正确视频和可接受时间区间；
- 冻结每个查询真正需要的有界音频窗口、代表帧 bundle 和 OCR 帧；
- 在 provider gate 仍关闭的情况下，将本提案作为待审 selection 写入新的 pilot revision；
- 产品验收只先运行默认，失败时按上面的三个 trigger 之一运行唯一 fallback。

截至本提案，三种模态均为 `proposed_for_pilot`，不是“已通过产品质量门”的生产默认。公开榜单支持家族级预筛选，无法替代这次小规模产品验收；也没有任何未核验的候选成绩被写成胜者。

## 审计

- 新增文件仅为本研究 artifact；没有修改代码、catalog、pilot manifest 或生产文件。
- 没有调用 provider、模型或数据集，没有读取 `.env`；`external_calls=0`，`provider_calls=0`。
- 报告中的模型/凭证/运行事实均来自已存在的任务研究材料；本次没有新增 web source。
