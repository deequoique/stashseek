# Agent Evaluator 与发布门禁

> **2026-10-07 修订**：数据源从"手工标注 32 条视频 case"改为已建成的 QMSum + ExplainMeetSum 会议 golden set（`data/meeting_gold/final`），单臂执行改为基于 `evals/meeting_gold` 的生产链路 harness。依据见 `research/meeting-gold-reuse-2026-10-07.md`。旧版的视频专属要求（YouTube/Bilibili 覆盖、`?t=` 深链）移出 V1。

## Goal

为当前知识 Agent 建立一套可复现、可审计、可配对比较的 evaluator。它用冻结的会议 golden set 和一致的模型、参数及工具环境比较 baseline 与 candidate，同时分别衡量：

- 最终任务结果
- Agent 工具轨迹
- 证据可信度
- 主观回答质量
- 延迟与成功成本

candidate 只有在正确性不下降，且轨迹/运行指标达到预注册的改善要求时，才获得发布资格。

## Background and Confirmed Facts

### Golden set

- golden set 已经建成：
  - 232 场会议、1,810 条 case，带官方 train/val/test 划分
  - val 272 条 / 35 场会议，test 281 条 / 35 场会议
  - 三个领域：Academic(icsi)、Committee、Product(ami)；query 分 `specific` / `general`
  - 有 dataset/selection hash、source lock 和 leakage 标记
  - 稳定身份是 `meeting_id` + `stable_sentence_id(meeting, turn, sentence)`，不依赖数据库自增 ID
  - evaluator 只绑定 `final`（dataset_hash `4aeccba…`）。其他 accepted 目录是历史版本。
- golden set 自身的约束：
  - 不含 no-answer 负例，也不允许在 golden set 内生成负例
  - 没有 gold 时间戳
  - ExplainMeetSum 的 evidence group 对应参考答案的每个答案句，表示证据覆盖面，不表示有依赖的多跳
  - val 中 CES 可评分的单组证据 specific case 只有 12 条，且偏向 ami
- `evals/meeting_gold/` 及其测试目前在 `dev` 上是 untracked，`main` 还没有；任务 `09-12-qmsum-explainmeetsum-golden-set` 仍为 `in_progress`。

### 生产链路 harness

`evals/meeting_gold` 已具备以下能力：

- 只连 Neon test 分支的环境保护（`prepare_test_environment`）
- 会议转 YouTube json3 字幕，走真实 `create_item` + `process_item` 入库，带 checkpoint 的 library
- 真实 `agent.stream()` 单 case 运行，用 `RecordingServices` 记录后端检索调用
- 采集 diagnostics：`tool_call` / `stage_usage` / `limit_kind` / `citation_validated`
- 句子级严格指标和块级宽松指标
- 失败分类
- McNemar / Wilson / 配对 bootstrap 统计

两种租户布局：

- 历史 specific-30 运行：每场会议一个租户。
- 当前 `agent_smoke.py`：35 场共用一个租户。这种模式还没有用真实模型跑过。

### 已有基线（单臂，非冻结 evaluator 产物）

specific-30（val，每会议一租户），2026-10-07 当前生产配置：

- 30/30 回答成功
- 检索池 gold 覆盖 0.780，引用含 gold 0.893
- 中位延迟 9.15 s，最大 17.5 s
- primary 阶段：模型请求中位 3 次，工具调用中位 3 次（1–6）
- skipped 3 次，完全相同的重复后端操作 0 次，超限 0 次

RQ1 显示：共用租户时 Recall@5 只有 0.09–0.21，瓶颈在检索排序。

### 轨迹信号现状

已有：

- `AgentDeps.tool_event()` 输出 started/succeeded/failed/skipped 和 `call_index`
- 服务端预算：检索共 5 次（search 2、expansion 3），输出 token 上限 3000
- 超预算调用返回 `skipped/budget_exhausted`
- `ToolPolicy` 有只在内存中的 read fingerprint，用于绑定重试

缺失：

- handler 之前被 schema 拒绝的请求（PydanticAI `args_valid`）
- 按顺序、不丢重复次数的模型请求事件流
- progress epoch、冗余、loop 判定
- composer/section/recovery 的 usage 汇总（目前只有 retrieval 阶段发 `stage_usage`）

`evals/natural_language/runner.py` 的 `agent_loop_limit_rate` 只是"撞到预算上限"的比例，不能代表 loop rate。

### 其他事实

- 模型是 DeepSeek `deepseek-v4-flash`，检索阶段开启 thinking。即使设置 `parallel_tool_calls=False`，它也会在一次响应里发出多个工具调用。所以轨迹分母必须按单个工具请求计，不能按模型 step 计。
- 已归档的 `08-18-agent-quality-benchmark` 的 20 条视频 Gold 和 `evals/natural_language` 的 22 条行为契约 case 继续有效，但不再作为本 evaluator 的主数据集。
- LangSmith 官方支持上传在外部完成的 experiment。项目当前没有 LangSmith 依赖。PydanticAI 的全局 OTel instrumentation 不满足本任务的出站边界。

## Requirements

### R1. 基于 golden set 的分层冻结数据集

- 数据由两部分组成：
  - 只读引用的 golden set `final`
  - evaluator 自有的 **overlay manifest**。它引用 golden set 的 `dataset_hash` 和 case ID，声明分层、key case、重复策略、轨迹契约、故障计划、no-answer 负例和审核证明。

  golden set 的文件和 hash 不因 evaluator 改动。
- V1 有五层，每层至少 8 个 case：
  - `specific_local`：gold 证据集中在会议局部，预期一次检索即可覆盖
  - `specific_dispersed`：gold 证据分散在会议多处，预期需要改写检索或扩展
  - `general`：概括性问题
  - `no_answer`：overlay 构造的负例
  - `tool_error`：在 gold case 上叠加确定性故障

  `local` / `dispersed` 由 gold 句子在会议中的位置分布确定性计算，阈值写入 overlay 并参与 hash。不按单组/多组证据分层。
- 三个回答层（`specific_local`、`specific_dispersed`、`general`）只纳入 CES `citation_scorable` 的 val case，并覆盖全部三个领域。specific-30 中 28 条可评分 case 全部纳入 specific 两层，以便与 2026-10 的历史数字衔接。
- 每层至少 2 个 key case。key case 固定跑 3 次，其他跑 1 次。最小规模下每个 arm 至少 60 个独立 attempt。
- `no_answer` 负例由 overlay 构造，构造方式见 D3。要求：
  - 显式声明问题来源、所在租户中不存在支持证据的理由、允许的拒答边界和禁止补充的事实
  - 由人工审核确认，审核证明进入 overlay hash
  - 无关检索、伪引用或常识补答分别记为 false positive
- `tool_error` 使用确定性、有限次、可清理的故障计划：
  - 注入点在 `KnowledgeServices` 检索 seam
  - 声明错误类别、允许的 retry/recovery、预期最终状态，并要求无幽灵数据
  - 空搜索不能冒充工具异常
- 冻结身份为 `golden dataset_hash + case_id + meeting_id + transcript hash + gold sentence IDs`。运行时的 library 映射（meeting → item/segment ID、片段 → 句子）只在 arm 内使用。
- 执行前发现 golden set hash、library 的 chunker fingerprint、片段文本或 overlay 漂移，必须 fail closed。
- gate 只用 val 的冻结 selection。test 作为 held-out，只在里程碑认证报告中运行，不参与日常 candidate 迭代。overlay 冻结后只允许新增 revision。

### R2. Baseline/Candidate 的可比运行协议

- 两个 arm 分别从两个不可变、已提交的 checkout 运行。evaluator 不切换 Git，dirty working tree 不能作为发布证据。
  - 前置条件：`evals/meeting_gold` 和 evaluator 代码已经提交。
  - 首个可用 baseline 只能是已包含 evaluator 的提交。
- 两个 arm 必须引用同一个 run plan，并固定以下字段：
  - golden dataset hash、overlay hash、selection hash
  - 租户布局（D3）、library 的 chunker fingerprint 和 embedding 模型/维度
  - case/attempt ID
  - 模型 provider/name/revision、thinking 开关、采样参数、token 上限、seed 支持状态
  - primary/composer/recovery 的 prompt hash
  - Agent 预算、timeout、retry、并发
  - 工具清单/schema、数据库 migration、故障计划
  - judge profile、价格表、warm/cold 和执行顺序策略
- 除 `solution_id` 和代码 revision 外，其余比较字段必须一致。provider 不支持 seed 时记录 `unsupported`。
- 改动入库/分块的 candidate 会改变 library，V1 判为 `incomparable`。这类 candidate 的每臂独立 library 协议留到后续版本。
- 以 `(experiment_id, case_id, attempt_index)` 配对两个 arm。缺失、重复、skip、unscorable、profile 不一致或分母不完整都必须可见，并使比较结果为 `incomparable`。
- 真实运行只能在 Neon test 分支上进行（`prepare_test_environment`），使用专属评测租户，并且要做 teardown。默认的 validation/dry-run 不产生付费调用或持久副作用。
- 单元测试和离线命令不得连接数据库。需要数据库的命令必须显式覆盖 `DATABASE_URL`。

### R3. 结果层正确性

- 按五层、总体和 key case 分别报告 task success 的分子、分母和置信信息。
- 检索和引用分别报告：
  - 句子级严格指标：Recall@1/3/5、MRR、gold_evidence_recall、gold_citation_precision、reference group any/all hit
  - 块级宽松指标：作为诊断

  不得把"有引用"当作"引用正确"。golden set 没有 gold 时间戳，`timestamp_hit` 声明为 `not_applicable`。
- `no_answer` 单独报告无关检索、错误引用和无依据回答的 false-positive rate。`tool_error` 单独报告错误分类、bounded recovery 和最终任务成功率。
- 安全违规零容忍。工具策略通过、任务成功、引用正确和证据支持必须能分别定位，不能折叠成一个分数。
- 每个指标保留 numerator、denominator、status 和 reason：
  - manifest 声明的结构性不适用才是 `not_applicable`
  - 期望适用却零分母，或轨迹/证据/延迟缺失、skip，记为 `unknown`
  - 硬成功定义不依赖 judge

### R4. 最终回答的证据硬校验

- 对每个最终回答，按以下顺序做程序硬校验：
  1. 响应状态/schema
  2. 引用片段属于该 case 会议在 library 中的 item 和当前租户
  3. 片段文本与 library 记录一致（excerpt / text hash）
  4. 片段到 gold 句子坐标的映射（full/partial）
  5. Citation URL 与该 item 一致，深链 `t` 等于 `floor(start_sec)`，时间 finite、非负且在片段范围内
  6. 可见 `[S...]` marker 与结构化 Citation 一致
- 硬校验失败、片段漂移或证据投影不完整时为 fail/`unscorable`，LLM judge 不得覆盖。
- 只有通过硬校验的回答，才进入"结论是否由会议原文支持"的主观判断。`no_answer` 还必须验证没有伪造 Citation 和未授权事实。
- 真实视频平台身份（YouTube 真实 ID、Bilibili `?t=` 深链）的校验移出 V1（见 D4）。

### R5. 轨迹层质量

- 工具选择准确率：用每层（可按 case 覆盖）的 decision DAG 对齐必需/允许/禁止的 decision point，分别报告 required-tool recall、allowed-tool precision、forbidden-tool rate 和完整 trajectory contract pass。合理的改写检索和扩展不因调用较多而被惩罚。会议问答属于只读检索，save/management 等写操作工具一律禁止。
- 参数合法率的分母是模型发起的全部工具请求，包括 handler 之前被 schema 拒绝的请求和同一响应里的批量请求。分别记录 schema、类型/范围、scope/authorization 和业务前置条件错误。
- 冗余调用率只统计同一 progress epoch 内、同一个 canonical operation 已有充分结果、再次调用又没有带来新证据/状态的请求。以下情况不算冗余：
  - 不同的必需节点
  - 真正的 query 改写（带来新片段）
  - 允许的 exact retry/fallback
  - 状态变化后的必要重读

  canonical operation 复用 server-owned 的内存 fingerprint 机制，不落盘。
- loop rate 是发生无进展 operation/cycle 的 attempt 占比，按 `same_operation_no_progress`、`cycle_without_new_evidence`、`repeated_skipped_batch` 等固定原因分类。`budget_exhausted` skipped 和 usage limit 另报，不能直接当作 loop。
- 同时报告 `redundant_request_rate` 和 `redundant_backend_call_rate`，区分模型请求和实际后端执行。总调用次数只作诊断，不能作为优化目标，也不能抵消正确性回归。
- 参数原值只允许在本次请求内用于 schema 校验和 canonical 比较，不得进入产品日志、SSE 或 sanitized artifact。数据集是公开的，但模型生成的检索参数仍按此边界处理。
- safety violation 只接受 server-owned 的 policy/authorization/scope/confirmation/citation-sanitizer 事件或隐私 sentinel。出现任一事件，按全部 required attempt 计零容忍。

### R6. Rubric Judge 与人工仲裁

- 主观质量使用版本化 rubric 的固定 LLM-as-judge。judge：
  - 固定模型、参数、prompt/schema hash
  - 禁止使用工具、联网或自行检索
  - 只接收：问题、最终回答、程序验证过的有界会议原文、QMSum 参考答案、ExplainMeetSum 证据句和 stratum
- judge 使用 strict structured output，逐项判断：
  - 是否解决问题
  - 结论是否由原文支持
  - 必需证据（answer-sentence group）是否覆盖
  - 是否存在无依据陈述
  - 无答案/异常恢复是否如实
  - 表达是否清楚

  每项允许 `pass/fail/unknown`，并记录固定 reason code 和 confidence。
- judge 结果是独立的主观维度，不能替代硬校验。
- 人工抽检池必须包含：
  - 全部 deterministic-vs-judge 分歧和 baseline-vs-candidate 分歧
  - 全部 `unknown`、低置信/边界样本、judge 异常和 tool-error recovery 样本
  - 预注册的分层 agreement control

  人工记录使用 `accepted/pending/disputed`。必要样本未完成仲裁时，发布门禁 fail closed。
- reason code 使用固定 allow-list，confidence 为 `high|medium|low`。`low`、缺失或非法 confidence 进入人工队列。agreement control 以去重后的唯一 case 为基数：每层至少 1 个，且不少于该层 case 数的 10%（向上取整）。

### R7. 运行层指标与成本

- 同时报告 turn 和 attempt 端到端的 p50/p95，区分 successful 与 all-attempt 分母，并记录样本数、分位算法、warm/cold 和故障 profile。发布门禁使用预注册的 successful-attempt 端到端 p95。
- successful-attempt p95 的最小样本数预注册为 10。低于该值标记为 `low_n`，并阻塞 p95 门禁。
- 汇总 primary、answer plan、section 和全部 recovery 阶段的可归属 usage。当前只有 retrieval 阶段发 `stage_usage`，其余阶段需要补齐只读汇总。
- `cost_per_successful_task = 所有 task attempt（含失败和嵌套重试）的可归属总成本 / 成功的 task attempt 数`。同时报告 cost/attempt 和 unknown usage 数量。
- 无成功 attempt 时，成功成本为 undefined。任何 usage 或价格缺失时，成本诊断为 `unknown`，不得记为 0。成本 unknown 不单独阻塞 V1 门禁。
- 成本和调用次数用于解释效率，不能抵消正确性、证据、工具选择、参数合法性或安全回归。task attempt 与嵌套的 stage/recovery attempt 分层记录，每个 task attempt 的成本只计一次。

### R8. 报告、隐私与审计产物

- 每个 experiment 至少产出：overlay/golden 引用 manifest、非敏感 profile snapshot、run plan、baseline arm、candidate arm、paired comparison、gate decision，以及 sanitized Markdown/JSON 报告。每个数值都保留 numerator、denominator、status 和 reason code。
- QMSum/ExplainMeetSum 是 MIT 许可的公开数据，问题和会议原文本身不敏感。但 sanitized artifact 仍然不包含：模型回答、tool 参数/结果、URL、租户/用户 ID、secret、provider payload/错误正文或持久 argument digest。这样分享和 LangSmith 投影可以复用同一份 allow-list。
- 答案、原文片段、judge rationale 和人工评分只允许写入显式 opt-in、gitignored 的本地 review package。没有真实运行和仲裁证据时，不得填写或宣传准确率、p95、成本或发布结论。
- 再分发包含 golden set 内容的 artifact 时，保留 `NOTICE.md` 和两个上游 LICENSE。
- 报告 schema、overlay、profile、rubric、judge 和价格表都带版本/hash，旧 artifact 保持只读可解释。

### R9. 发布门禁

- 门禁先检查两臂可比性和所有必要指标的完整性。状态只有 `eligible`、`rejected`、`incomparable`。除明确标为非阻塞成本诊断的 unknown 外，缺失或 unknown 不得被当作通过。
- 正确性非劣化按每层和总体分别判断，至少包括：
  - task success
  - 检索/引用/证据支持
  - no-answer false positive
  - tool-error recovery
  - tool selection、parameter validity
  - safety

  低值更好的指标按反向比较。
- 轨迹/运行改善要求见 D1。只减少调用次数或只降低成本，都不能获得发布资格。
- 以下情况直接判为拒绝或不可比：任何安全违规、key case 硬回归、必要人工分歧未解决、golden/overlay/library/profile 漂移、配对不完整。
- 门槛、方向、统计方法和饱和指标例外，必须在看结果之前写入 policy，并参与 profile hash。不得在结果出来后放宽。
- loop rate 的分母是全部 required task attempt。轨迹不完整时标 `unknown`，不剔除。successful p95 低于最小样本数时标 `low_n`，并阻塞发布。

### R10. LangSmith 可选观测镜像

- V1 的 LangSmith 集成默认关闭。只有在本地 arm、judge/review、`comparison.json` 和 `gate.json` 全部最终落盘后，才能由独立的显式 export 命令执行。
  - validate、freeze、run-arm、compare 和 gate 路径必须零 LangSmith 网络调用
  - 上传耗时不得计入配对 p95 或成功成本
- 本地冻结的 overlay/run plan、pair ID、arm artifact、comparison 和 gate 是唯一事实源。LangSmith 上的任何数据、远端编辑或上传成败，都不得参与配对、重算或改变 `eligible|rejected|incomparable`。
- V1 用 LangSmith externally managed experiment 展示已完成的 baseline/candidate。重复次数只由本地 run plan 决定，不使用 LangSmith 的 `num_repetitions`。
- 出站 payload 使用 strict allow-list，只允许：case/pair/attempt/arm、stratum、非敏感版本/hash、枚举/布尔/reason code、分子/分母/计数、latency/usage/cost status，以及本地 evaluator/judge/adjudication 分数。禁止上传：
  - 回答、judge/reviewer 正文
  - URL、数据库/租户身份
  - tool 参数/结果、canonical operation/fingerprint
  - provider payload/error、secret、review package
- 不启用 `Agent.instrument_all()`、`@traceable`、全局 `LANGSMITH_TRACING` 或其他自动内容捕获。arm 进程必须清除 ambient 的 `LANGSMITH_*`、`LANGCHAIN_*` 和 OTel tracing 变量。
- 每次导出生成本地 receipt，状态为 `not_exported|exported|export_failed`。重复导出必须幂等。网络状态不确定时 fail closed 为 `remote_state_unknown`。
- 真实上传前，必须明确 endpoint/区域、workspace/project、保留期、访问控制和删除策略。

## Acceptance Criteria

- [ ] **AC0 — 前置：** `evals/meeting_gold` 和 golden set 测试已提交（09-12 完成或其代码已入库），`data/meeting_gold/final` 的 `dataset_hash` 被 overlay 固定；evaluator 首个 baseline 是包含 evaluator 代码的提交。
- [ ] **AC1 — Overlay 与分层：**
  - overlay 经严格校验后，五层各至少 8 条，每层至少 2 个 key case
  - 回答层只含 val CES 可评分 case，覆盖三个领域，并包含 specific-30 的 28 条可评分 case
  - local/dispersed 阈值确定性、可复算
  - 3/1 重复策略生成唯一且完整的 run plan（每 arm 至少 60 个 attempt）
- [ ] **AC2 — 稳定身份与漂移：**
  - golden hash、overlay hash、selection hash、library 的 chunker fingerprint、片段文本和 meeting→item 映射，任一漂移都在外部调用前 fail closed
  - 源数据中不存在以数据库自增 ID 作为 Gold 身份的条目
  - test split 不会出现在 gate selection 中
- [ ] **AC3 — 冻结与环境：**
  - 修改 overlay 的分层、key、fault、repeat、no-answer 审核，或 tool schema、prompt、model/thinking 参数、budget、judge rubric、价格表中任一受管字段，都会改变对应 hash
  - runtime/tool-env hash 排除 candidate 源码提交
  - baseline/candidate 的任何其他差异（包括 library 不同）都使比较成为 `incomparable`
- [ ] **AC4 — 可配对运行：** 两个已提交的 checkout 能消费同一个 run plan 生成 arm artifact，比较器能按 case/attempt 一一配对。缺失、重复、skip、unscorable 和 arm 崩溃都有显式原因，并且不能进入发布通过的分母。
- [ ] **AC5 — 轨迹指标：** synthetic trace 覆盖以下场景，并且工具选择、参数合法、冗余、backend 冗余和 loop 指标得到预期的分子/分母：
  - 单次检索；改写检索带来新片段 / 改写检索无新片段
  - search→neighbors/open_at 扩展
  - 同一响应批量请求
  - 非法参数、handler 前拒绝
  - 同参重复、合法 retry
  - `budget_exhausted` skipped、跨 step 重复 skipped
  - 无进展 cycle
- [ ] **AC6 — 证据硬校验：** 自动化测试覆盖：
  - 片段属于其他会议/其他租户
  - library 文本漂移
  - 片段→句子映射错误
  - URL item 不符、`t` 与 start 不一致、负数/NaN/越界时间
  - excerpt 与 `[S...]` union

  硬校验失败的样本永远不会被 judge 改判为通过。
- [ ] **AC7 — Judge/人工：**
  - 固定的无工具 judge 能对 strict rubric 输出 `pass/fail/unknown`；每维和 overall 的聚合、reason-code allow-list、confidence 都固定
  - 无效 JSON、timeout、缺证据和低置信都进入人工队列
  - 规定的分歧样本和 agreement control 可以导出、导入和仲裁；`pending/disputed` 不会被静默计为通过
- [ ] **AC8 — 指标与成本：**
  - 报告按五层、总体、case 和 task/stage attempt 给出结果/轨迹/运行指标及分母
  - p95 算法、最小成功样本数和 low-n 有确定性测试
  - usage 覆盖 primary/composer/section/recovery
  - 成功成本包含失败和重试成本，每个 task attempt 只计一次
  - 缺 usage/价格时为非阻塞的 `unknown`
- [ ] **AC9 — 门禁矩阵：** 自动化测试覆盖以下分支，只有满足冻结 policy 的组合返回 `eligible`：
  - correctness 下降
  - loop 未改善 / baseline loop 为 0
  - p95 未改善
  - 只有调用次数或成本下降
  - unknown
  - profile/library 漂移
  - key case 回归
  - 完全通过
- [ ] **AC10 — 隐私：**
  - 用回答、tool 参数、URL、租户 ID、secret、provider body 和 digest sentinel 扫描日志、SSE 和 sanitized artifact，全部不得泄露
  - 只有显式的本地 review export 可以包含回答/原文，且该目录被 Git 忽略
  - 再分发时保留 golden set 的 NOTICE/LICENSE
- [ ] **AC11 — 兼容性：**
  - 既有的 `evals.meeting_gold` 命令（build/validate/select/score/rq1/agent-smoke）继续可用
  - `evals.natural_language` 的 catalog 校验、22 条行为 case、Gold scorer 和 human review 命令继续可用
  - 新增的离线 validation/dry-run 默认零外部调用
  - 已知的 ambient Settings 测试漂移被隔离或修复
- [ ] **AC12 — 项目实测：** 在 Neon test 分支完成至少一次真实的 baseline health report（单臂、全量 run plan）。如果有明确的 candidate revision，再完成全量配对运行和规定的人工抽检。不得伪造 candidate 或发布资格。
- [ ] **AC13 — LangSmith 镜像：**
  - 默认的 evaluator/arm/compare/gate 路径经 network spy 证明零 LangSmith 调用
  - 独立的 exporter 能把两个完成的 arm 映射为同一个 externally managed dataset 下的脱敏 experiment，逐 attempt/pair/repeat 与本地 hash 可对账
  - 字段白名单、环境隔离、敏感 sentinel、重复导出和各种失败场景的测试全部通过
  - 任何导出状态都不能改变本地 `gate.json` 的内容或 hash

## Out of Scope

- 不在本任务中根据评测结果重写 Agent 的检索、回答、工具权限或业务策略。产品缺陷另建任务；本 evaluator 负责测量和复验。RQ1 已知的检索问题（多样性处理、关键词 AND、查询改写）不在此修。
- 不修改 golden set 的数据、schema 或 accepted freeze。evaluator 只通过 overlay 引用它。
- 不让 evaluator 自动切换分支、修改 checkout、部署或发布版本。
- 不扩大 public MCP/HTTP/SSE 契约，不把评测所需的原始参数、usage 或原文暴露给终端用户或生产日志。
- V1 不支持改动入库/分块的 candidate 比较，也不支持真实视频平台（YouTube/Bilibili）的证据层。
- 不做大规模压测、长期 soak、跨 provider 排名，也不用单一 judge 模型宣称普遍的人类偏好。
- 不把"更少的工具调用"本身定义为成功，也不允许成本/调用数抵消正确性下降。
- V1 不使用 LangSmith 执行数据集、重复模型调用、在线 evaluator、prompt 优化、judge 或人工仲裁。

## Blocking Decision

- **D1 — 门禁阈值。** 推荐值见 `design.md` §10.3。
  - 新事实：当前生产预算下，specific-30 没有超限，也没有完全相同的重复操作，baseline loop rate 很可能接近 0。原规则"loop 与 p95 必须同时改善"实际会退化为"loop 保持 0 且 p95 改善 ≥5%"。
  - 需要确认：
    - (a) 接受这种饱和语义；或
    - (b) 改为"loop 不劣化 + p95 改善"；或
    - (c) 按 candidate 声明的意图（降延迟 / 降 loop / 提正确性）选择改善门，其余指标只要求非劣化。
  - 同时确认正确性统计容差（推荐 −2pp）和 p95 最小改善比例（推荐 5%）。
- **D2 — LangSmith V1 角色：** 是否接受 LangSmith 只作为评测完成后、可选上传的脱敏 dashboard 镜像，本地 `gate.json` 是唯一发布依据。数据集公开降低了出站风险，但模型回答和 tool 参数仍然禁止上传。
- **D3 — 租户布局与 no_answer 构造：**
  - 推荐 V1 gate 用"每场会议一个租户"。理由：
    - 与 2026-10 的 30/30 历史数字可比
    - RQ1 显示共用租户时的低召回主要来自已知检索缺陷，会让 gate 测的是检索排序而不是 Agent 轨迹
    - no_answer 可以干净地构造为"在会议 Y 的租户里问会议 X 的问题"：一半跨领域（容易确认无证据），一半同领域不同会议（需要人工审核）
  - 35 场共用一个租户的布局作为非 gate 的诊断 profile。
  - 备选：gate 直接用共用租户布局，no_answer 改用 train split 中不在 library 里的会议的问题，并全部人工审核。
- **D4 — 视频证据层：** 推荐 V1 不包含 YouTube/Bilibili 真实视频层。`?t=` 深链和平台身份校验留给后续 V2 扩展，可复用 08-18 的视频 Gold 或 08-24 视频基准的数据。如果需要 V1 就覆盖视频，需要另补至少 8 条带稳定 fixture 的视频 case 和平台 parser 的工作量。
