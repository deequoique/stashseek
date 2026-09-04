# Agent Evaluator Implementation Plan

## Planning Gate

- [ ] 用户确认 `design.md` 第 10.3 节的 V1 gate policy，或给出替代阈值。
- [ ] 将确认后的值从 PRD `Blocking Decision` 收敛进 R9/AC9，并完成最后一次 PRD convergence pass。
- [ ] 用户在最终规划摘要之后明确批准进入实施；批准前不运行 `task.py start`，不修改产品/evaluator 代码，不触发付费评测。

本任务保留为一个端到端实现任务：冻结 schema、trace、evidence、judge 和 comparator 共用同一 artifact/profile contract，拆成独立 Trellis 子任务会在接口尚未稳定前增加交叉版本风险。执行仍按以下可独立验证的里程碑推进；若某一里程碑暴露产品 Agent 缺陷，另建产品修复任务，不在 evaluator 任务内顺手改策略。

## 0. 建立实施基线

- [ ] 记录当前 committed base/candidate 候选 revision、Python/依赖锁、PydanticAI 版本、migration head 和现有 CLI/schema 版本；不把当前 dirty working tree 当作 arm。
- [ ] 运行现有离线 catalog/video validation 和 evaluator 单元测试，保存基线。已知 `test_preflight_refuses_production_before_opening_database` 会被 ambient development wildcard origin 影响；通过显式 test fixture 隔离配置或修复测试构造，不能 `xfail`/忽略。
- [ ] 确认 `.eval-results`/新 experiment 目录被 Git 忽略，现有报告 redaction、MCP grant teardown 和非 production preflight 保持有效。
- [ ] 为新增 artifact schema 分配 major version；列出旧 reader/CLI 的兼容行为。

验证：

```bash
.venv/bin/python -m evals.natural_language --validate-catalog
.venv/bin/python -m evals.video_recognition --validate-catalog
.venv/bin/python -m evals.video_recognition --dry-run
.venv/bin/python -m pytest -q \
  tests/test_natural_language_evaluator.py \
  tests/test_natural_language_quality.py
```

## 1. 冻结数据、profile 与 run-plan schema

- [ ] 新增 strict `extra=forbid` schema：dataset/fixture/case、decision DAG、Gold/answer contract、fault/repeat、`metric_applicability`、annotation review proof、execution profile、run plan、arm/task-attempt/stage-attempt、comparison 和 gate decision。
- [ ] 实现 canonical model dump + SHA-256；hash 排除自身字段，稳定处理键顺序、Unicode、换行、整数/浮点和 null。分别维护 dataset、profile、tool schema、rubric 和 price table version/hash。
- [ ] 校验四层最小数量、每层 key case、3/1 重复、至少 4 个视频、YouTube/Bilibili 覆盖、唯一 case/pair ID，以及 no-answer/tool-error 的互斥约束；为每个指标声明适用性并锁定零分母 `not_applicable`/`unknown` 语义。
- [ ] 为 decision DAG 校验无环、可达、必需节点、允许工具和错误/recovery 分支；多跳必须有至少两个依赖 evidence/decision group。
- [ ] 生成不可变 `run_plan.json`，固定每个 case/attempt 的 pair ID、repeat、seed 支持和 order slot；重复 attempt 必须有独立 conversation/fault namespace。
- [ ] 新增 versioned rubric 与 price table schema。缺模型价或 token 类别时允许校验通过但成本状态必须是 `unknown`。
- [ ] 提供纯离线 CLI，规范命令统一为 `--validate-dataset`、`--freeze-check`、`--run-arm`、`--compare`；freeze/build-run-plan 作为内部库操作，默认 external calls 为 0。
- [ ] 从归档 20 条 Gold 中迁移可验证的标注来源；拒绝仅有数字数据库 ID、缺 fixture/platform/transcript/segment hash 的条目。
- [ ] 建立 V1 至少 32-case 的标注清单：四层各至少 8 条、每层 key ≥2、视频/语言/证据跨度/异常类型满足 PRD；写入 reviewer role/reference、reviewed_at、review_revision、覆盖计数/digest 和 `status=approved`。标注未完成或未批准前 manifest 保持 draft，不伪造 `frozen`。

单元测试：

- canonical hash golden vectors、任一受管字段变更导致 hash 变化、自身 hash 不参与；
- 重复/未知 ID、非法 stratum、DAG cycle/unreachable、错误 Gold/fault 组合、NaN/负/逆序时间；
- 31 case、单层不足 8、key 不足、单平台/视频不足、repeat 非 3/1、缺 annotation approval 均 fail closed；
- run plan 顺序与 pair key 稳定，同 seed 输入可重建相同内容。

**Review Gate A：** schema/hash/run-plan 全部 network-free 测试通过，32-case 标注由人复核之前，不进入真实 fixture freeze。

## 2. 只读 fixture resolver 与视频证据硬校验

- [ ] 实现评测 tenant 下的只读 resolver：`platform + platform_id` 唯一 ready item、canonical base URL、duration、transcript/content hash/raw format/text source，以及 segment seq/start/end/text hash。
- [ ] 将 stable fixture/segment key 映射为本次 numeric ID，仅保存在 arm 进程内；区分 `missing`、`duplicate`、`not_ready`、`transcript_drift`、`segment_drift`，全部在 provider 调用前中止。
- [ ] 实现 YouTube/Bilibili 平台专用 citation parser。Bilibili 先解析唯一 `t` 再 canonicalize base URL；拒绝 host confusion、credentials、port、fragment、非法 ID/额外 query。
- [ ] 依固定顺序实现 status/schema、item/segment 归属、video ID/URL、timestamp/deep-link、duration/segment range、transcript/text hash、excerpt 和 `[S...]` union checker。
- [ ] no-answer 校验无 Citation/marker/来源链接，tool-error 校验 phase/status/error code；硬失败不创建 judge input。
- [ ] checker 只导出固定 bool/enum/reason code；不把 URL、字幕、numeric ID 或 excerpt 写进 sanitized artifact。

单元/集成测试：

- 正确/错误 platform、ID、host/path、跨 tenant/fixture segment、deleted/not-ready item；
- negative/NaN/infinite/out-of-duration/out-of-segment timestamp、tolerance 边界、URL `t` 与 start 不一致；
- YouTube canonical/deep-link 与 Bilibili `?t=42` 正例，跟踪 query/fragment/host confusion 反例；
- transcript/segment hash drift、whitespace-normalized excerpt、未知/重复/缺失 marker；
- hard failure 时 judge spy 为零调用。

**Review Gate B：** resolver 对冻结 fixture 全部通过，任何 drift 在模型调用前停止；Bilibili deep-link 回归与现有 connector tests 同时通过。

## 3. 完整、安全的 trajectory 与 usage 投影

- [ ] 在 eval-only model-tool event seam 捕获每次请求和完整 lifecycle 顺序，使用 PydanticAI `FunctionToolCallEvent.args_valid` 覆盖 handler 前失败；保留现有 public execution timeline 和生产 diagnostics contract，并将 trace completeness 作为 required metric 状态。
- [ ] 在 MCP input/service boundary 投影 schema/type/range/scope/precondition 的固定 validation code，分母包含全部模型 tool request；setup/housekeeping 标为 `not_applicable`。
- [ ] 在内存中做 server-owned canonical operation comparison；event 只保留 sequence、bool、enum/count 和 `duplicate_of_present`，不保存参数、URL、ID 或 digest。
- [ ] 实现 decision DAG 对齐、required recall、allowed precision、forbidden rate 和 trajectory contract pass；对可达节点按 required 优先、声明顺序和 allow-list 顺序 deterministic tie-break，批量请求按 sequence 独立匹配。
- [ ] 实现 progress epoch、合法 retry/fallback 豁免、request/backend redundancy，以及版本化 loop detector；loop 以全部 required task attempts 为分母，trace 不完整标 unknown/incomparable，budget/usage limit 单独统计。
- [ ] 防止现有 `(call_index, tool_name)` lifecycle merge 丢失 multiplicity；旧兼容 projection 仍可生成，但新 scorer 使用 lossless event stream。
- [ ] 在 primary、answer plan、每个 answer section 和每个 recovery owner boundary 汇总独立 `RunUsage`；标准化 token 类别，不暴露 provider raw payload。
- [ ] 使用 price table 计算 known cost 与 unknown 状态；每个 `task_attempt_id` 下的 stage/recovery usage 只计一次，总成功成本的分子包含失败/retry，零成功为 undefined；成本 unknown 仅作非阻塞诊断。

synthetic trace/usage tests：

- 单跳、search→neighbors→open_at 合理多跳、不同参数、相同 operation 无进展重复、真实 query reformulation；
- allowed exact retry、failed→retry、state/evidence progress 后重读、same-step skipped、跨 step repeated skipped、budget exhausted、长度 2–4 cycle；
- malformed JSON/schema reject/范围/scope/precondition failure 都进入参数分母；
- batched provider calls 保持顺序和 multiplicity；
- primary + plan + sections + 三次 recovery usage 求和、price 算术、cache/reasoning/audio、缺 usage/价目→unknown；
- 将敏感 sentinel 放入 args/result/provider error，确认 trajectory、diagnostics、SSE 和 sanitized serializer 均无泄露。

**Review Gate C：** 指标定义的分子/分母由 synthetic golden tests 锁定，现有 Agent 行为与 public SSE 无语义 diff，隐私 sentinel 全部通过。

## 4. 单臂执行、profile preflight 与 artifact

- [ ] 将现有 `LiveEvaluator` 包装为 immutable arm executor，保留 migration/readiness、专属 user、真实 model、临时 grant、fixture setup/teardown 和现有 Expectation。
- [ ] preflight 计算 comparison profile/tool-schema/runtime hash，并拒绝未提交/不匹配的 arm identity；secret 只检查 presence/target key，不写入 artifact。
- [ ] 每个 arm 消费同一 run plan，按固定 case/attempt/order 执行；每个 attempt 单独保存 outcome、trajectory、deterministic evidence、usage、latency 和 safe reasons。
- [ ] attempt latency 明确覆盖第一 turn dispatch 到 terminal response，排除全局 preflight/freeze；保留 turn latency、successful/all 分母和 common-success pairing 所需字段。
- [ ] crash/timeout/skip/unscorable 都写入 attempt row；finally 保证 session/grant/fault/fixture teardown，arm artifact 可在中断后判断 incomplete，不做静默续跑。
- [ ] 新 CLI 支持 `--run-arm --manifest --profile --run-plan --arm --solution-id --results-dir`（或等价子命令），不执行 Git switch，不默认开启 judge/review 正文导出。
- [ ] `arm.json`/`attempts.jsonl`/`summary.json` 使用 allow-list serializer，并带 schema/profile/dataset/run-plan/tool hash 和 readiness/teardown proof。

测试：

- 两个 fake committed arm 消费相同 run plan，生成唯一 pair key；缺/重复/顺序错误/incomplete attempt 可检测；
- model、prompt、budget、tool schema、migration/index、fixture、fault、judge、price/warm policy 任一 mismatch 被拒绝；
- provider 不支持 seed 显示 `unsupported`，不宣称 deterministic；
- teardown 在成功、断言失败、provider error、取消和 fault failure 下都运行；
- 现有 `--case/--all/--human-benchmark` 路径继续通过。

**Review Gate D：** 先用 fake/synthetic arm 完成全 artifact round-trip；真实付费调用必须另行显式批准并且 Gate A–D 全绿。

## 5. Rubric judge 与人工 review

- [ ] 定义固定六维 rubric、strict judge input/output、reason-code allow-list、confidence/unknown 规则；model/prompt/schema/rubric hash 纳入 profile。
- [ ] judge 只接收已硬校验的 bounded evidence package，arm label 盲化，无工具/网络/历史；hard fail/no evidence-integrity failure 不调用 judge。
- [ ] 固定 judge retry/timeout/temperature/seed；invalid JSON/provider error 不选择性重跑到 pass，所有允许 retry 均计 usage/cost。
- [ ] 建立抽检选择器：全部 hard-vs-judge、arm-vs-arm、judge-vs-existing-human 分歧，全部 unknown/低置信/边界/judge error/tool-error recovery，以及每层至少 1 且不少于 agreement 10% 的预注册 control。
- [ ] 扩展现有 human review export/import，保持 sample ID 稳定和 arm 盲化；`accepted/pending/disputed` 语义与 accepted-only 聚合兼容。
- [ ] review package 显式 opt-in、gitignored，可包含 question/answer/verified subtitle/judge rationale；sanitized report 只保留聚合与 reason code。

测试：

- 支持/不支持/部分支持结论，正确 Citation 但语义不支持，无答案胡答，错误恢复说成成功；
- judge invalid JSON/timeout/provider error/unknown/低 confidence；
- hard checker 失败时 judge 零调用；
- sampling 覆盖全量分歧、全部规定异常和 deterministic agreement control；
- pending/disputed/missing required review 使 release comparison 不完整。

**Review Gate E：** 用人工构造样本做一次 blind calibration，rubric/queue 经人工确认后冻结；不得在看到 candidate 结果后改 rubric 或抽样规则。

## 6. Paired comparison、报告与 release gate

- [ ] compare 在聚合前验证 artifact major version、dataset/profile/run-plan/tool-env hash、case/attempt pair、repeat 和 required denominator。
- [ ] 按 attempt、case、四个 stratum 和 overall 输出 task/retrieval/citation/evidence/no-answer/tool-error、tool selection/parameter/redundancy/loop、latency/usage/cost；所有值带 numerator/denominator/status。
- [ ] nearest-rank p50/p95 使用确定性函数，报告 successful/all/common-success `n`；gate 使用冻结 policy 指定的 successful-attempt p95。
- [ ] 率指标用 case-cluster paired bootstrap（或等价预注册方法）计算 95% CI，保留 key repeat 的 case 内相关性；关键硬 case 另做 zero-regression。
- [ ] 实现三态 `eligible/rejected/incomparable` 和逐 gate reason：正确性方向、CI margin、关键 case、安全、loop、p95、人工完成度和 profile/pair 完整度。
- [ ] redundancy/call count/cost 只作诊断，不能补偿正确性、loop 或 p95；成本 unknown 清楚显示但不伪造 0。
- [ ] 输出 machine-readable `comparison.json`/`gate.json` 和同源 `report.md`；Markdown 不自行重新计算数字。
- [ ] 为 sanitized artifacts 做 allow-list schema + sentinel scan；每个 gate 结论可回溯到 arm、case/attempt、policy/hash，但不暴露正文。

gate matrix 至少覆盖：

- correctness 点估计下降、CI 超 margin、关键 case 回归、safety violation；
- tool selection/parameter validity 下降；
- loop 改善/持平/恶化，以及 baseline=0 的确认后语义；
- p95 达标、只改善不足阈值、持平/恶化/缺失/non-finite；
- 仅调用次数或成本下降；
- judge/review pending、usage/cost unknown、profile drift、missing/duplicate pair；
- 所有门满足的唯一 `eligible` 路径。

**Review Gate F：** comparison/gate 只用 golden synthetic artifacts 验证全部分支；用户确认 policy hash 后再冻结真实 dataset/profile。

## 7. 冻结、真实运行与项目报告

- [ ] 对至少 32 条标注和 4+ ready 视频执行 resolver，人工复核 query、Gold evidence groups、answer boundary、fault 和 key-case 分布；写入新 revision 并冻结 hash。
- [ ] 选定两个明确的 committed solution revision。若当时只有一个可评方案，先运行 baseline health report；不得复制 baseline 成 candidate 或声称发布资格。
- [ ] 在同一专属非 production 环境、短时间窗口和固定 order 下运行 baseline/candidate；每 arm 至少执行 run plan 的 48 个 attempt。真实付费运行必须显式 opt-in。
- [ ] 运行 fixed judge，导出并完成全部 required human review；保留 pending/disputed 时 gate 维持 incomparable。
- [ ] 生成最终 sanitized project report，人工核对分母、profile/dataset hash、关键 case、failure reasons、p95/cost unknown 和 gate conclusion。
- [ ] evaluator 发现的 Agent 缺陷单独建 Trellis task；修复后以新 candidate revision 重新跑完整冻结 plan，不能覆盖旧结果。

建议命令形态（实施后以 `--help` 为准）：

```bash
.venv/bin/python -m evals.natural_language \
  --validate-dataset evals/natural_language/datasets/agent-evaluator-v1.yaml

.venv/bin/python -m evals.natural_language \
  --freeze-check --manifest MANIFEST --profile PROFILE --run-plan RUN_PLAN

.venv/bin/python -m evals.natural_language \
  --run-arm --arm baseline --solution-id BASE_SHA \
  --manifest MANIFEST --profile PROFILE --run-plan RUN_PLAN

.venv/bin/python -m evals.natural_language \
  --run-arm --arm candidate --solution-id CANDIDATE_SHA \
  --manifest MANIFEST --profile PROFILE --run-plan RUN_PLAN

.venv/bin/python -m evals.natural_language \
  --compare BASELINE_ARTIFACT CANDIDATE_ARTIFACT --policy GATE_POLICY
```

## 8. Final Validation

- [ ] 运行新增 evaluator 单元/集成测试、现有 natural-language tests、connector URL tests、Agent trajectory/streaming/diagnostics privacy tests 和完整 pytest。
- [ ] 运行 dataset/profile/freeze/run-plan offline commands，确认外部调用数为 0；检查 artifact schema round-trip 和 deterministic hash。
- [ ] 运行 Alembic single-head/current preflight；本设计预计不新增 DB migration，若实现确需 migration，必须返回规划阶段补充 database rollout/rollback。
- [ ] 检查 package data 是否包含新增 committed YAML/schema；修改用户已有 dirty `pyproject.toml` 时只做最小增量并保留其改动。
- [ ] 复查 README：数据标注、两个 checkout 的 arm 运行、付费边界、review 导出、比较、门禁、unknown 和不能声称的内容。
- [ ] 对最终 report/log/SSE/review paths 做敏感 sentinel 扫描；确认 review 目录 ignored，sanitized report 无正文。

预期测试命令：

```bash
.venv/bin/python -m compileall -q evals/natural_language
.venv/bin/python -m pytest -q \
  tests/test_natural_language_evaluator.py \
  tests/test_natural_language_quality.py \
  tests/test_natural_language_experiment.py \
  tests/test_bilibili_url.py
.venv/bin/python -m pytest -q
```

## 9. Risky Files and Rollback Points

| 区域 | 风险 | 回滚点 |
| --- | --- | --- |
| `evals/natural_language/runner.py`, CLI | 破坏现有单臂/人工评测兼容 | 新 experiment 路径保持独立；每个里程碑重跑旧 CLI tests，可整体关闭新入口。 |
| `mcp_runtime.py` / Agent event seam | 丢 lifecycle、改变执行或泄露参数 | callback 默认无操作且 eval-only；public event/diagnostics golden tests 有任何 diff 即回退。 |
| primary/composer/recovery usage owners | 错算成本或改变 budget | 只读 snapshot/callback，不共享或重置 `RunUsage`；缺失时 unknown。 |
| fixture resolver / fault injection | 共享状态污染或跨 tenant | 只读 resolver、专属 tenant、独立 namespace、production 禁用、finally teardown。 |
| URL/timestamp validator | Bilibili `?t=` 合法深链误拒 | 平台专用 parser + connector regression；不修改 ingestion admission contract。 |
| judge/review export | 答案/字幕泄露或 judge 覆盖硬错误 | 默认关闭正文 export，hard gate 在 judge 前，allow-list report + sentinel scan。 |
| dataset/profile/policy | 结果后调参或旧数据被覆盖 | content-addressed/versioned 只读 artifact；任何变化生成新 hash/revision并重跑两臂。 |
| `pyproject.toml` package data | 与用户现有未提交改动冲突 | 实施前 diff，最小 patch，只新增必要数据路径，不重写文件。 |

## 10. Completion Checklist

- [ ] PRD AC1–AC11 全部由自动化测试或可审计 artifact 证明。
- [ ] AC12 至少完成真实 baseline health report；若有 candidate，完成全量 pair、judge、人工抽检和三态 gate。
- [ ] 没有把 synthetic/fake/dry-run 数字写成项目真实指标。
- [ ] 所有 evaluator 发现的产品缺陷已单独记录，没有夹带未规划的 Agent 行为改动。
- [ ] 最终检查、spec update、commit 和 Trellis archive 按 workflow 单独执行。
