# Agent Evaluator 与发布门禁

## Goal

为当前视频知识 Agent 建立一套可复现、可审计、可配对比较的 evaluator。它必须用冻结数据和一致的模型、参数及工具环境比较 baseline 与 candidate，同时分别衡量最终任务结果、Agent 工具轨迹、视频证据可信度、主观回答质量、延迟与成功成本。只有正确性不下降，且 loop rate 与 p95 同时达到预注册的改善要求，candidate 才能获得发布资格。

## Background and Confirmed Facts

- `evals/natural_language` 已能在真实 MCP/模型链路上运行 22 个行为 case，保留重复 attempt，并报告任务、工具、引用和 p95 等单臂指标；本任务应在其上扩展，而不是另写一套 Agent 或替换既有行为契约。
- 已归档的 `08-18-agent-quality-benchmark` 提供了 20 条 Gold 样本和六维人工 rubric，可作为标注来源；其中依赖运行时数据库数字 ID 的内容不能直接进入新冻结集，必须迁移为稳定 fixture 身份。
- 当前 trace 只足够判断工具名、结果和有限 Citation，不能可靠计算模型请求层的参数合法率、无增益重复或一般性 loop；现有 `agent_loop_limit_rate` 也不能代表 loop rate。
- 当前 Citation 尚不足以独立证明视频 ID、链接、时间戳和字幕归属全部正确；尤其 Bilibili `?t=` 深链需要专用解析，不能直接交给普通 URL normalizer。
- primary、composer 和 recovery 路径已有 usage 计数来源，但尚未汇总到 evaluator；缺失 usage 或价格信息时成本必须保持 `unknown`，不能记为 0。

## Requirements

### R1. 分层且冻结的数据集

- V1 至少包含 32 个独立 case，明确分为 `simple_retrieval`、`multi_hop_retrieval`、`no_answer`、`tool_error` 四层，每层至少 8 个。
- 每层至少标记 2 个 key case。key case 固定运行 3 次，其他 case 固定运行 1 次；按最小 32-case 数据集计算，每个 arm 至少产生 48 个独立 attempt。
- 数据集至少覆盖 4 个 ready 视频，并同时覆盖 YouTube 与 Bilibili。问题类型、语言、证据跨度和错误类型应有代表性，不能只围绕一个视频或一种关键词改写。
- `multi_hop_retrieval` 必须用证据组和有依赖的 decision DAG 表达必需步骤、可选合理步骤及前置关系，不能用“调用次数大于一”定义多跳。
- `no_answer` 必须显式声明没有可用证据、允许的拒答边界及禁止补充的事实；无关检索、伪引用或常识补答分别作为 false positive 记录。
- `tool_error` 必须使用确定性、有限次、可清理的故障计划，声明注入点、错误类别、允许的 retry/recovery、预期最终状态和无幽灵数据要求；空搜索不能冒充工具异常。
- 冻结身份由 `fixture alias + platform + platform_id + transcript/content hash + segment seq/text hash` 组成。源数据禁止以自增 item/segment ID 作为唯一身份。
- case、Gold、fixture snapshot、重复策略和 fault schedule 采用 canonical serialization 并生成版本与 SHA-256；冻结后只允许新增 revision，不得原地改写。执行前发现 hash、字幕、分段或 fixture 漂移时必须 fail closed。

### R2. Baseline/Candidate 的可比运行协议

- baseline 与 candidate 必须分别从两个不可变、已提交的 checkout 运行；evaluator 只生成/消费 arm artifact，不自行切换 Git，也不把 dirty working tree 当作可发布证据。
- 两个 arm 必须引用同一 run plan，并固定 dataset、fixture、case/attempt ID、模型 provider/name/revision、采样参数、token 上限、seed 支持状态、system/developer prompt hash、Agent budgets、timeout、retry、并发、工具清单/schema、共享 MCP/tool-server revision、数据库 migration、embedding/index revision、故障计划、judge profile、价格表以及 warm/cold 与执行顺序策略。
- 除 `solution_id`、代码 revision 和被评估实现外，其余比较字段必须一致。provider 不支持 seed 时明确记录 `unsupported`，不能声称 deterministic。
- 以 `(experiment_id, case_id, attempt_index)` 配对两个 arm。缺失、重复、skip、unscorable、profile 不一致或 denominator 不完整必须可见，并使比较结果成为 `incomparable`，不能被静默剔除或补 0。
- 真实模型/外部调用继续要求非 production、专属评测用户、当前 migration、完整 MCP readiness、临时 grant 和 teardown；默认 validation/dry-run 不产生付费调用或持久副作用。
- 完整冻结 manifest 必须带 annotation review proof（审核状态、审核 revision/时间、覆盖计数和 coverage digest）；query/Gold 只在经批准的公开 benchmark source 或受控 review-only 存储中保存，不能把完整 manifest 误当作可分享的 sanitized 报告。

### R3. 结果层正确性

- 按四层、总体和关键 case 分别报告 task success 的分子、分母与置信信息，不能只报告混合平均值。
- 检索和引用分别报告 Recall@1/3、MRR、Citation Precision、Citation Completeness、Timestamp Hit Rate；不得把“有引用”当作“引用正确”。
- `no_answer` 单独报告无关检索、错误引用和无依据回答的 false-positive rate；`tool_error` 单独报告错误分类、bounded recovery 和最终任务成功率。
- 安全违规保持零容忍；工具策略通过、任务成功、引用正确和字幕支持必须是可单独定位的结果，不能折叠成一个模糊分数。
- 指标统一保留 numerator、denominator、status 和 reason：manifest 声明的结构性不适用才是 `not_applicable`；期望适用却零分母、轨迹/证据/延迟缺失或 skip 是 `unknown`/不可比，不能补 0。successful task 的硬成功定义不依赖 judge。

### R4. 最终回答的视频证据门

- 对每个最终回答必须先做程序硬校验，顺序至少为：响应状态/schema → 视频/平台身份 → URL host/path/video ID → 引用与链接时间戳 → segment 对 fixture/item 的归属 → transcript/segment hash 与 excerpt → 可见 `[S...]` marker 和结构化 Citation 一致性。
- 时间戳必须为 finite、非负、在 segment 和视频时长内，并与平台深链的 `floor(start_sec)` 一致；容差只能来自冻结 case。Bilibili 带 `?t=` 的深链必须先拆分时间参数，再对 base URL 做平台专用 canonicalization。
- 硬校验失败、缺字幕、hash 漂移或证据投影不完整时为 fail/`unscorable`，LLM judge 不得覆盖或“挽救”该结果。
- 只有硬校验通过的回答，才进入“结论是否由可信字幕证据支持”的主观判断；`no_answer` 还必须验证无伪造 Citation 和无未授权事实。

### R5. 轨迹层质量

- 工具选择准确率依据每个 case 的 decision DAG 对齐必需/允许/禁止的 decision point，区分 required-tool recall、allowed-tool precision、forbidden-tool rate 和完整 trajectory contract pass；合理多跳不得因调用较多而被惩罚。
- 参数合法率的分母是模型发起的全部工具请求，包括 handler 之前被 schema 拒绝的请求；分别记录 schema、类型/范围、scope/authorization 和业务前置条件错误。确定性 setup 调用标为 `not_applicable`。
- 冗余调用率只统计同一 progress epoch 内、相同 canonical operation 已有充分结果且再次调用没有带来新证据/状态的请求。不同 hop、真实 query reformulation、允许的 exact retry/fallback 和状态变化后的必要重读不得判为冗余。
- loop rate 是发生无进展 operation/cycle 的 attempt 比例，并按 `same_operation_no_progress`、`cycle_without_new_evidence`、`repeated_skipped_batch` 等固定原因分类；budget/usage limit 另报，不能直接当作 loop。
- 同时报 `redundant_request_rate` 和 `redundant_backend_call_rate`，区分模型请求与实际后端执行。总调用次数只作诊断，不能作为优化目标或抵消正确性回归。
- 参数原值仅允许在本次请求内用于 schema 校验和 canonical comparison，不得进入产品日志、SSE、sanitized artifact 或持久 fingerprint。
- safety violation 只接受 server-owned policy/authorization/scope/confirmation/citation-sanitizer 或隐私 sentinel 产生的固定事件；任一事件即按全部 required attempt 计零容忍，来源和分类可审计但不保存 payload。

### R6. Rubric Judge 与人工仲裁

- 主观质量使用版本化 rubric 的固定 LLM-as-judge。judge 固定模型、参数、prompt/schema hash，禁止使用工具、联网或自行检索，只接收问题、最终回答、程序验证过的有界字幕证据、Gold reference points/answer boundary 和 stratum。
- judge 使用 strict structured output，至少逐项判断：是否解决问题、结论是否由字幕支持、必需证据是否覆盖、是否存在无依据陈述、无答案/异常恢复是否如实、表达是否清楚；每项允许 `pass/fail/unknown` 并记录固定 reason code 与 confidence。
- judge 结果是独立的主观维度，不能替代视频/URL/时间戳/schema 等硬校验。
- 人工抽检池必须包含全部 deterministic-vs-judge 分歧、baseline-vs-candidate 分歧、`unknown`、低置信/边界样本、judge 异常和 tool-error recovery 样本，并额外包含预注册的分层 agreement control。人工记录使用 `accepted/pending/disputed`；必要样本未仲裁完成时发布门禁 fail closed。
- judge 的每个维度和 overall 必须使用固定 reason-code allow-list 与 `high|medium|low` confidence；`low`/缺失/非法 confidence 进入人工队列。agreement control 以去重后的唯一 case 为基数（每层至少 1 个且不少于该层 case 的 10%，向上取整），key case 的 repeats 不增加权重。

### R7. 运行层指标与成本

- 同时报告 turn、attempt end-to-end 的 p50/p95，区分 successful 与 all-attempt 分母，记录样本数、分位算法、warm/cold 和故障 profile；发布门禁使用预注册的 successful-attempt end-to-end p95。
- successful-attempt p95 的 V1 最小样本数预注册为 10；低于该值只能标记 `low_n` 并阻塞 p95 门禁，不能用少量成功样本声称改善。
- 汇总 primary、answer plan、section 和全部 recovery stage 的可归属 usage。`cost_per_successful_task = 所有 task attempt（含失败和嵌套重试）的可归属总成本 / 成功 task attempt 数`；同时报告 cost/attempt 和 unknown usage 数量。
- 无成功 attempt 时成功成本为 undefined；任一 usage 或价格缺失时成本诊断为 `unknown`，不得用调用次数猜测或记为 0；成本 unknown 不单独阻塞 V1 正确性/loop/p95 门禁。
- 成本和调用次数用于解释运行效率，不能抵消 task、证据、工具选择、参数合法性或安全回归。
- task attempt 与嵌套 stage/recovery attempt 分层记录；成功成本只对每个 task attempt 计一次，包含其失败/retry stage 的 usage，不能重复计算。usage/price 缺失只使成本诊断为 `unknown`，不伪造 0。

### R8. 报告、隐私与审计产物

- 每个 experiment 至少产出冻结 manifest、非敏感 profile snapshot、run plan、baseline arm、candidate arm、paired comparison、gate decision 和 sanitized Markdown/JSON 报告；每个数值都保留 numerator、denominator、状态和 reason code。
- sanitized artifact 不包含问题/回答、tool 参数/结果、URL、字幕正文、tenant/external identity、secret、provider payload/错误正文或持久 argument digest。
- 答案、可信字幕片段、judge rationale 和人工评分只允许写入显式 opt-in、gitignored 的本地 review package；没有真实运行和仲裁证据时不得填写或宣传准确率、p95、成本或发布结论。
- 报告 schema、dataset、profile、rubric、judge 与 price table 都带版本/hash；旧 artifact 保持只读可解释。

### R9. 发布门禁

- 门禁必须先检查两臂可比性和所有必要指标完整性；状态只有 `eligible`、`rejected`、`incomparable`，除明确标为非阻塞成本诊断的 unknown 外，缺失或 unknown 不得被当作通过。
- 正确性非劣化按每个 stratum 与总体分别判断，至少包括 task success、检索/引用/视频/时间戳/字幕支持、no-answer false positive、tool-error recovery、tool selection、parameter validity 和 safety；低值更好的指标按反向比较。
- candidate 必须同时改善 loop rate 与 successful-attempt p95。只改善其中一个、只减少调用或只降低成本都不能发布。
- 任何安全违规、关键硬 case 回归、必要人工分歧未解决、fixture/profile 漂移或配对不完整都直接拒绝/判为不可比。
- 门槛、方向、统计方法和饱和指标例外必须在看结果前写入 policy 并参与 profile hash；不得在结果出来后解释性放宽。
- loop rate 的分母是全部 required task attempts；轨迹不完整时标 `unknown` 而不是剔除。successful p95 需要预注册最小成功样本数，低于该值标 `low_n` 并阻塞发布。

## Acceptance Criteria

- [ ] **AC1 — 冻结集：** V1 manifest 经严格校验后至少有 32 case、四层各至少 8 条、每层至少 2 个 key case、至少 4 个 ready 视频且覆盖 YouTube/Bilibili；key/non-key 的 3/1 重复策略能够生成唯一且完整的 run plan。
- [ ] **AC2 — 稳定身份：** resolver 能从稳定 fixture/platform/transcript/segment hash 映射运行时 ID；任一平台 ID、字幕 hash、segment seq/text hash 或 ready 状态漂移都会在外部调用前 fail closed。数据文件中不存在以数据库自增 ID 作为唯一 Gold 身份的样本。
- [ ] **AC3 — 冻结与环境：** 修改 query、Gold、fault、repeat、fixture、tool schema、prompt、model parameter、judge rubric 或 price table 中任一受管字段都会改变对应 hash；manifest 带已批准的 annotation review proof；runtime/tool-env hash 排除 candidate source commit，baseline/candidate 的任一其他差异都会使比较成为 `incomparable`。
- [ ] **AC4 — 可配对运行：** 两个已提交 checkout 能消费同一 run plan 生成 arm artifact，比较器能按 case/attempt 一一配对；缺失、重复、skip、unscorable 和 arm 崩溃均有显式原因并且不能进入发布通过分母。
- [ ] **AC5 — 轨迹指标：** synthetic traces 覆盖单跳、合理多跳、非法参数、handler 前拒绝、同参重复、真实改写、合法 retry、skipped batch、budget exhaustion 和无进展 cycle；工具选择、参数合法、冗余、backend 冗余及 loop 指标得到预期分子/分母。
- [ ] **AC6 — 视频证据：** 自动化测试覆盖正确/错误平台和视频 ID、host/path、跨 fixture segment、负数/NaN/越界 timestamp、YouTube/Bilibili 深链（含 Bilibili `?t=`）、excerpt/hash 与 `[S...]` union；硬校验失败的样本永远不会由 judge 改判为通过。
- [ ] **AC7 — Judge/人工：** 固定无工具 judge 能对 strict rubric 输出 `pass/fail/unknown`，每维/overall 的聚合、reason-code allow-list 和 `high|medium|low` confidence 固定；无效 JSON、timeout、缺证据和低置信会进入人工队列；所有规定的分歧样本与按唯一 case 计算的 agreement control 可导出、导入和仲裁，`pending/disputed` 不会被静默计为通过。
- [ ] **AC8 — 指标与成本：** 报告按四层、总体、case 和 task/stage attempt 给出结果/轨迹/运行指标及分母；p95 算法、最小成功样本数和 low-n 状态有确定性测试，usage 覆盖 primary/composer/recovery，成功成本包含失败与重试成本且每个 task attempt 只计一次，缺 usage/价格保持非阻塞的 `unknown`。
- [ ] **AC9 — 门禁矩阵：** 自动化测试覆盖 correctness 下降、loop 未改善、p95 未改善、仅调用次数/成本下降、baseline loop 为 0、unknown、profile drift、关键 case 回归和完全通过等分支；只有满足冻结 policy 的组合返回 `eligible`。
- [ ] **AC10 — 隐私：** 明确区分受控冻结 manifest/source 与可分享 sanitized artifacts；用问题、回答、参数、URL、字幕、tenant、secret、provider body 和 digest sentinel 扫描日志、SSE 与 sanitized artifacts，全部不得泄露；只有显式本地 review export 可包含审核正文且该目录被版本控制忽略。
- [ ] **AC11 — 兼容性：** 既有 catalog 验证、行为 case、Gold scorer、human review 和 natural-language evaluator 命令保持可用；新增 offline validation/dry-run 默认零外部调用，已知 ambient Settings 测试漂移被隔离或修复，不掩盖真实回归。
- [ ] **AC12 — 项目实测：** 在专属非 production 环境完成至少一次真实 baseline/candidate 全量配对运行和规定的人工抽检，生成可审计报告；若尚无明确 candidate revision，可先生成当前已提交版本的 baseline 健康报告，但不得伪造 candidate 或发布资格。

## Out of Scope

- 不在本任务中根据评测结果重写 Agent 检索、回答、工具权限或业务策略；产品缺陷另建任务，本 evaluator 负责测量和复验。
- 不让 evaluator 自动切换分支、修改 checkout、部署或发布版本；它只生成 arm artifact、比较和门禁结论。
- 不扩大 public MCP/HTTP/SSE contract，不把评测所需原始参数、usage 或字幕正文暴露给终端用户或生产日志。
- 不做大规模压测、长期 soak、跨 provider 排名或用一个 judge 模型宣称普遍的人类偏好。
- 不把“更少的工具调用”本身定义为成功，也不允许成本/调用数抵消正确性下降。

## Blocking Decision

- **D1 — V1 非劣化与改善阈值：** 需要在开始实现前确认 correctness 的统计容差、baseline loop 已为 0 时的饱和例外，以及 p95 的最小改善比例。推荐值记录在 `design.md` 的发布 policy 草案中；确认后本节将移除，并把最终值固化进 R9 与验收矩阵。
