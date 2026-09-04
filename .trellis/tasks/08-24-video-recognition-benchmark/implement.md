# 视频识别公开基准选型与产品验收：实施计划

## Gate

- [x] 用户批准从全候选内部 benchmark 收缩为公开证据 + 4–6 视频产品验收。
- [x] OpenAI provider compatibility smoke 通过；凭证只通过环境提供。
- [ ] 新 pilot manifest、12–18 条查询真值和默认/fallback 选择冻结前，正式 provider execution 保持关闭。

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

2026-08-28 已完成候选整理，但尚未完成冻结：候选时间范围仅保留在
`annotation_note`，query truth 仍为空且 `time_origin` 为 `pending`；媒体仍为
`pending_manual_review`。BI-01/BI-06 的无音频证据槽位不补写语音关键词，待人工
回放后再决定是否进入 frozen revision。

- [ ] 冻结核心 `YT-01`、`YT-03`、`BI-01`、`BI-06`；只有核心覆盖不足时加入 `YT-04`、`BI-04`。
- [ ] 每个实际入选样本冻结 3 条查询，共 12–18 条；每片至少一条时间段定位查询，总体覆盖 speech、visual/OCR、combined。
- [ ] 只为查询需要选择有界音频窗口和 3–6 帧 bundle；不再机械要求每片两段音频和两组帧。
- [ ] 在不含供应商结果的 dry review 中冻结正确视频、可接受时间范围、required modalities/key terms、default/fallback 和 hard gates。

- [x] 2026-08-28 完成 4 个核心样本的 12 条 partial-candidate query/media 整理；候选仍保持人工复核状态，不改变下方冻结或 provider execution gate。

## 4. Compatibility 与产品运行

- [x] 实现并复核独立 OpenAI synthetic smoke，默认关闭且需显式确认，不读取 catalog 或解锁 provider gate。
- [x] `whisper-1` 与 `gpt-5.6-luna/terra/sol` synthetic smoke 全部成功；只保留脱敏状态、延迟、usage 和清理结果。
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
- [x] Pilot manifest validation and offline pilot dry-run report zero external calls while preserving the closed public-evidence/truth/media/classification gates.
- Harness unit tests：catalog/schema/planning/scoring/cleanup/redaction/default→fallback gate。
- Synthetic smoke 默认关闭测试和 fake transport 测试。
- [x] Pilot dry-run：只计划入选样本/default，不产生外部调用。
- 正式结果完整性：每个实际计划单元有终态，未触发 fallback 没有调用记录。
- 仓库 secret/content sentinel 扫描与 `git diff --check`。

Independent review validation: 78 passed; pilot dry-run `external_calls=0`.

## Rollback

本任务不修改生产 schema 或运行路径。迁移失败时恢复旧 benchmark catalog/schema，删除未冻结 pilot 派生产物，并保留 12 个公开样本 provenance、公开证据研究和 synthetic smoke 结果。
