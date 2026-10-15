# 视频识别公开基准选型与产品验收：实施计划

## 2026-10-15 方案变更

Vision/OCR 已移除，ASR 不再测试，直接选定 Fun-ASR 默认、`whisper-1` 备选。下面第 1、3、4 节中未完成的冻结、运行和报告项不再执行，第 5 节按 ASR-only 收口。剩余工作见 `prd.md` 的“2026-10-15 方案变更”。

## Gate

- [x] 用户批准从全候选内部 benchmark 收缩为公开证据 + 4–6 视频产品验收。
- [x] OpenAI provider compatibility smoke 通过；凭证只通过环境提供。
- [ ] 新 pilot manifest 已部分冻结查询真值和最小媒体输入；正式 provider execution 保持关闭，直到剩余人工确认、public-evidence profile selection 和样本 readiness gates 完成。

## 1. 迁移规模与公开证据

- [x] 把原 12 个样本标记为 regression pool，保留 metadata/subtitle probe、公开性和权利不确定性。
- [x] 建立 ASR、Vision、OCR 公开 benchmark/模型卡证据矩阵，记录版本/日期、指标定义、语言/领域、来源性质和局限。
- [ ] 每种模态根据公开证据冻结一个 default 和一个 fallback；不得为了内部排名运行全部 shortlist。
- [ ] 明确公开证据缺口和后续重新核验日期。

## 2. 收缩 harness 与 catalog

- [x] 新增独立 planning-only pilot manifest/schema：引用 12 个 regression-pool identity，首轮 4 个核心样本、每样本 3 个 query slot，以及 modality-driven media/profile/retry contracts；旧 catalog/probe invariants 保持不变。
- [x] 新增 offline `--validate-pilot` / `--pilot-dry-run` 规划路径；动态报告 pilot units，fallback 仅在显式允许 trigger 下计划，provider execution 与旧 `--run` gate 保持关闭。
- [x] 采用独立 pilot schema/manifest 表达 12 个 regression pool identity、4–6 个 pilot selection、12–18 条 pilot queries、每模态 default/fallback 和默认单次运行；旧 benchmark schema 的 12/30/11/3 回归不变量保持不变。
- [x] pilot 计划单元只由入选样本、查询所需模态和实际触发的 default/fallback 动态计算，不再把固定 396 单元用于 pilot。
- [x] 保留原 provider-neutral ASR/Vision/OCR、MediaSampleBundle、Discovery Segment、Recognition result/error 协议。
- [x] 调整执行 blockers：regression pool 不要求全量 truth；只有 pilot 样本/查询/媒体/default profile 必须冻结。
- [x] 更新 fixtures、README、CLI summary 和测试，明确小样本产品准入不等同于模型排行榜。
- [x] 将研究中的 12 条 query、保守 distractor 和有界媒体候选写入 planning-only manifest；候选不转为 truth，profile 提案不转为 winner。

## 3. 冻结产品验收集

2026-09-05 人工复核已完成首轮部分冻结：12 条 query 中 10 条已有 scoped
expected video、video-absolute ranges、required key terms 和 evidence notes；
YT-01 OCR 与 BI-06 speech 仍待最小人工确认。BI-01 的媒体输入已冻结，
YT-01、YT-03、BI-06 的候选媒体仍为 `pending_manual_review`。manifest 保持
`planning_only`，这些未完成项以及 public-evidence profile selection、样本内容
分类和正式 probe readiness 继续关闭执行 gate。

- [x] 冻结核心 `YT-01`、`YT-03`、`BI-01`、`BI-06`；只有核心覆盖不足时加入 `YT-04`、`BI-04`。
- [ ] 每个实际入选样本冻结 3 条查询，共 12 条；当前已冻结 10 条，YT-01 OCR 与 BI-06 speech 待确认。
- [ ] 只为查询需要选择有界音频窗口和 3–6 帧 bundle；当前仅 BI-01 media selection 完成冻结。
- [ ] 在不含供应商结果的 dry review 中冻结正确视频、可接受时间范围、required modalities/key terms 和 hard-gate 输入；default/fallback 仍待 public evidence 选择。

- [x] 2026-08-28 完成 4 个核心样本的 12 条 partial-candidate query/media 整理；候选仍保持人工复核状态，不改变下方冻结或 provider execution gate。

## 4. Compatibility 与产品运行

- [x] 实现并复核独立 OpenAI synthetic smoke，默认关闭且需显式确认，不读取 catalog 或解锁 provider gate。
- [x] `whisper-1` 与 `gpt-5.6-luna/terra/sol` synthetic smoke 全部成功；只保留脱敏状态、延迟、usage 和清理结果。
- [x] 新增独立 OpenAI annotation-assist CLI、严格有界的四样本媒体计划、绝对时间戳归一化、视觉 OCR assist 标识、脱敏审核报告和成功/失败清理测试；它不修改 pilot truth 或正式 gate。
- [x] ASR 候选在 schema/时间戳校验后统一做 Unicode/空白与简繁归一化，覆盖全文、segment 和 word 文本；时间戳保持不变，OpenCC 缺失时 fail-closed。
- [x] 2026-09-05 实际运行 annotation assist：11 条记录全部完成（6 个 ASR 窗口、5 组视觉/OCR-assist 帧），报告已写入 `research/openai-annotation-assist-2026-09-05.md`，临时媒体已清理；结果仍待用户人工审核，未经审核不得回写 `pilot_catalog.yaml`。
- [ ] 对每种模态只运行 default；通过 hard gates 即停止。
- [ ] 只有 default 未通过或不可用时才运行冻结 fallback；重跑必须符合预先失败/不稳定规则。
- [ ] 生成产品质量、时间定位、协议、延迟、用量、费用和失败摘要；不生成通用模型排名。

## 5. 选择与交接

- [ ] 写 default/fallback 选择报告，区分公开 benchmark 证据、compatibility smoke 和产品验收证据。
- [ ] 提交脱敏成功/失败 fixtures 与 schema；不提交原始媒体或供应商响应体。
- [ ] 把冻结产物加入父任务和阶段 2–4 子任务上下文，然后归档本子任务。
- [ ] 将 12 个 regression pool 留给后续版本回归、真实失败补样或模型升级复核。

## Validation

- Trellis context validation。
- [x] Pilot manifest validation and offline pilot dry-run report zero external calls while preserving the closed truth/media/public-evidence/classification/readiness gates during partial freeze.
- Harness unit tests：catalog/schema/planning/scoring/cleanup/redaction/default→fallback gate。
- Synthetic smoke 默认关闭测试和 fake transport 测试。
- [x] Annotation assist 的 dedicated confirmation、CLI 隔离、固定 URL/窗口、fake transport、绝对时间戳、内容脱敏、媒体子进程 secret 隔离与清理测试。
- [x] 真实运行后脱敏扫描通过；`--validate-pilot` 仍报告 `execution_gate=closed`、`external_calls=0`、`planned_default_units=0`。
- [x] Pilot dry-run：只计划入选样本/default，不产生外部调用。
- 正式结果完整性：每个实际计划单元有终态，未触发 fallback 没有调用记录。
- 仓库 secret/content sentinel 扫描与 `git diff --check`。

## Rollback

本任务不修改生产 schema 或运行路径。迁移失败时恢复旧 benchmark catalog/schema，删除未冻结 pilot 派生产物，并保留 12 个公开样本 provenance、公开证据研究和 synthetic smoke 结果。
