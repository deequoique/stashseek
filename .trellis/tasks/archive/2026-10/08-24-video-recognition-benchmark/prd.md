# 视频识别公开基准选型与产品验收

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

## 2026-10-15 方案变更（与下文冲突时以本节为准）

用户在 2026-10-15 确认：

- **移除 Vision 与 OCR。** 本任务中 Vision 和 OCR 的公开证据、选型和产品验收都不再执行。
- **不再测试 ASR 模型。** pilot 的正式 provider 运行不再执行，剩余的查询和媒体冻结也不再推进；`execution_gate` 保持关闭。
- **ASR 直接选定：** 默认 Fun-ASR（`fun-asr`），备选 `whisper-1`。2026-08-28 的 Qwen filetrans 默认提案作废。依据和风险见父任务 `08-24-intent-aware-video-recognition-routing/prd.md` 的“2026-10-15 方案变更”。

仍然有效的已有成果：

- `asr.v1` 等供应商无关协议；
- ASR 文本归一化（OpenCC `t2s`）；
- `whisper-1` synthetic smoke；
- 2026-09-05 的 annotation-assist 结果。

本任务剩余工作：

1. 按 ASR-only 收口供应商无关的 `asr.v1`、Discovery Segment、RecognitionJob 结果和安全错误 fixtures，交给下游子任务。
2. 在 profile registry（`evals/video_recognition/catalog.yaml`）中加入 Fun-ASR。目前 registry 里只有 Qwen、`whisper-1` 等候选，没有 Fun-ASR。
3. 处理代码中的三模态遗留，删除或保留为历史由实现时决定：
   - `pilot_schema.py` 仍要求 asr/vision/ocr 三个 profile selection；
   - `pilot_catalog.yaml` 仍包含 visual/OCR 查询。
   - 已归档任务 `09-05-low-human-video-evaluation` 留下了未提交的 `quality_report.py` 和 `sampling.py` 改动，需决定保留还是删除。
4. 交接后归档本任务。


## Goal

利用独立公开 benchmark、供应商模型卡和当前官方能力资料完成 ASR、Vision、OCR shortlist；不自行复刻通用模型排行榜。随后只用 4–6 个用户选定的公开视频做搜索产品特有验收，验证时间戳定位、代表帧搜索语义、屏幕文字、协议稳定性和实际调用延迟，为按需视频识别路由冻结首期默认模型与 fallback。

## Background and Confirmed Decisions

- 父需求：`08-24-intent-aware-video-recognition-routing`；本子任务是后续识别运行时、预处理和搜索实现的第一阶段。
- 2026-08-26 用户确认：通用性能不再由本项目对全部候选重复测试，优先采用已有公开榜单/模型卡；本项目只保留小规模产品验收。
- 原 12 个 YouTube/Bilibili 视频继续保留为 `regression_pool` 和来源/字幕/权利记录，不再要求首轮全部提取媒体、全部标注或全部调用模型。
- 首轮验收使用 4 个核心样本，只有覆盖缺口需要时再加入最多 2 个风险扩展样本：
  - 核心候选：`YT-01`（字幕课程/幻灯片）、`YT-03`（代码/UI）、`BI-01`（技术动画/硬字幕）、`BI-06`（实景动作）。
  - 风险扩展：`YT-04`（长纪录片/partial 字幕）、`BI-04`（长录屏/密集 UI）。
- 每个实际入选样本只冻结 3 条产品查询，共 12–18 条；必须覆盖 speech、visual/OCR 和 combined，并且每个样本至少有一条时间段定位查询。
- 每种模态只运行公开证据选出的一个默认模型；只有默认模型未通过产品门槛或接口不可用时才运行一个预先指定 fallback。默认单次运行，只有协议/结果不稳定或供应商失败时才按预先规则重跑，最多补 2 次。
- 首阶段不做预算准入；仍记录调用量、延迟和估算费用。
- 已完成隔离 OpenAI synthetic smoke：`whisper-1`、`gpt-5.6-luna`、`gpt-5.6-terra`、`gpt-5.6-sol` 的最小请求均成功；这只证明兼容性，不证明产品质量。

## Requirements

1. 为每个模态建立公开证据表：benchmark/模型卡名称、版本或查询日期、语言/领域匹配度、指标、独立或供应商来源、已知局限和产品适用结论。
2. 公开证据只用于 shortlist，不把不同数据集、不同指标或供应商自报数字直接拼成无条件总排名；证据不足时明确记录不确定性。
3. 从 12 个 regression pool 中冻结 4 个核心产品验收样本；只有长视频、噪声或密集 UI 覆盖不足时才增加最多 2 个风险扩展样本。
4. 每个验收样本只提取冻结评测所需的有界音频窗口和 3–6 帧 bundle；不把完整视频、完整音频、完整字幕、签名 URL 或供应商原始响应提交到仓库。
5. ASR 必须返回稳定时间戳；视觉必须支持多图和结构化搜索字段；OCR 必须输出文字、坐标与置信度。协议不符合即失败，不用公开榜单成绩豁免。
6. 产品验收只衡量本产品无法从公开榜单获得的能力：正确视频召回、时间段命中、关键搜索词、跨视频证据隔离、JSON/schema 稳定性、实际延迟/失败和用量。
7. 默认模型通过产品门槛即结束该模态验收；不为生成内部排行榜继续调用其余候选。fallback 只在默认失败时运行。
8. 输出供应商无关的 Discovery Segment、MediaSampleBundle、RecognitionJob result 和安全错误 fixtures；正式结果不得包含供应商 SDK 对象或原始响应。
9. 只处理用户选定的公开样本，不处理登录态、私有或 DRM 媒体；第三方服务只接收冻结的最小输入，不接收视频页面 URL 或账户信息。
10. 标注工作流允许先对严格有界的候选音频/帧运行独立的模型预标注，再由用户人工审核；预标注结果不得自动写入 frozen truth、参与评分或打开正式 provider execution gate。

## Product Acceptance Gates

- 所有实际运行均通过 schema，且没有跨视频证据污染、原始响应/凭证/媒体泄漏或临时文件残留。
- 12–18 条产品查询的正确视频 Top-3 命中率不低于 90%。
- 需要时间定位的查询中，至少 80% 的结果中点落入人工可接受区间，或与区间的 temporal IoU 不低于 0.30。
- required key terms 召回率不低于 80%；visual/OCR/combined 子集不得出现系统性零召回。
- 默认模型失败时，报告明确区分产品质量失败、协议失败、媒体/网络失败和供应商失败，再决定是否启用 fallback。
- 延迟、用量和估算费用只作为通过后的运营信息，不抵消质量失败；异步产品路径不以本次请求同步等待为前提。

## Acceptance Criteria

- [ ] ASR、~~Vision、OCR~~ 均有可追溯的公开 benchmark/模型卡证据表和证据局限说明，shortlist 不是由本项目全候选跑分得出。（2026-10-15：只保留 ASR；Fun-ASR 的公开依据记录在父任务 PRD）
- [ ] 12 个原始 URL 作为 regression pool 保留公开性、结构、字幕轨/覆盖、权利不确定性和 preliminary 内容记录，但未被错误当成首轮全部处理承诺。
- [ ] ~~4 个核心样本和可选的 0–2 个风险扩展样本在运行前冻结实际媒体输入、12–18 条查询、正确视频、可接受时间范围、所需模态和关键术语。~~（2026-10-15 作废：不再运行 pilot）
- [ ] ~~每个模态默认只运行一个公开证据选出的默认模型；fallback 的触发原因与结果可复核，没有为了内部排名调用未触发候选。~~（2026-10-15 作废：不再运行模型）
- [ ] ~~报告包含搜索/时间命中、关键术语、schema 通过率、延迟、调用量和估算费用，并明确小样本验收不等同于通用排行榜。~~（2026-10-15 作废：不再运行 pilot）
- [ ] ~~胜出的默认模型通过所有产品门槛；未通过时有明确 fallback 或 blocked 结论。~~（2026-10-15 作废：ASR 按公开资料直接选定 Fun-ASR + `whisper-1`）
- [x] 2026-10-15 ASR 默认与备选已由用户决定：Fun-ASR（`fun-asr`）+ `whisper-1`。
- [ ] 下游 fixtures 不包含供应商 SDK 对象、签名 URL、凭证、原始 stderr、完整媒体或可还原的供应商响应体。
- [ ] 父任务和直接下游子任务引用冻结后的 contract/fixture 与公开证据结论。
- [ ] 模型预标注可通过独立 CLI 生成待人工审核包，完整媒体、临时文件、供应商原始响应和凭证均不残留，且正式 pilot gate 仍关闭。

## Risks and Deferred Items

- 公开榜单可能数据集陈旧、语言/领域不匹配或由供应商自报；必须记录来源性质和迁移风险，不能只摘一个总分。
- 4–6 个样本只能作为产品准入测试，不能证明模型在所有视频上的普遍领先；12 个 regression pool 留给后续回归和真实失败补样。
- 部分样本没有开放再利用许可。首轮优先选技术/课程/实景核心样本，并继续通过最小片段、临时处理、不公开媒体降低风险。
- 生产数据保留政策、预算准入、线上 rollout 和大规模回填在后续子任务处理。

## Out of Scope

- 自建通用 ASR/Vision/OCR 排行榜或复刻公开 benchmark 全集。
- 对 12 个 regression-pool 视频全部提取媒体、全部人工标注、全候选三次运行。
- 生产 schema、队列、媒体下载服务、Agent 工具或线上 rollout。
- 登录态、私有或 DRM 媒体。
- 费用预算或额度准入。
- 发布、分发或把完整样本媒体作为 fixture 保存。
