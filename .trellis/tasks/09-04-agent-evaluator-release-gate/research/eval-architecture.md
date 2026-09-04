# Agent evaluator / release gate 研究报告

## 结论

当前仓库已经有一个可复用的“真实模型、单臂、行为契约 + Gold 诊断 + 人工复核”评测骨架，但还不是用户要求的 evaluator。evals/natural_language 能安全地跑固定 case、保留重复 attempt、按工具策略和引用 Gold 做单臂汇总；它不能保证 baseline/candidate 使用完全相同的冻结数据和运行环境，也不能从现有 trace 计算参数合法率、冗余调用率或一般 loop rate。

建议把现有 LiveEvaluator 作为单臂 transport/lifecycle adapter，在其上增加冻结 manifest、轨迹投影、视频证据校验、judge、配对比较和 release gate，而不要重写 Agent 行为。本报告只做规划研究，未修改产品代码。

现状的直接证据：

- 行为目录是 catalog.yaml 中的 22 个 case，按 retrieval、save、inventory、context、conversation、safety 六类组织；检索只有 5 个 case，且 Case 只有自由文本 tags，没有四个要求的显式 strata 或 hop/故障契约（evals/natural_language/schema.py:88-123；catalog.yaml:8-43）。
- 真实运行有非 production、专属评测用户、migration、MCP readiness、临时 grant、精确工具 profile 和真实模型检查（runner.py:140-184、187-245），但尚未组成 baseline/candidate 共同引用的 environment snapshot。
- 重复运行已保留每次 AttemptResult，并以 case pass rate 判定（runner.py:341-372），可复用为关键 case 重复运行的执行语义。
- 汇总已有 task/tool/citation/conversation/safety 指标、turn p50/p95、模型/工具调用均值和 agent_loop_limit_rate（runner.py:809-926），但没有 baseline delta、单次成功成本或发布决策。
- Gold scorer 已分开计算 Recall@1/3、MRR、Citation Precision/Completeness、Timestamp Hit Rate，并为 no-evidence 单独计算错误检索/错误引用（quality.py:383-467）；仍只按稳定 item/segment/start range 比较，不验证视频 URL/ID、链接时间戳和字幕文本支持。

## 可复用边界

| 能力 | 现有实现和证据 | 对新 evaluator 的用法 |
| --- | --- | --- |
| Case / tool contract | Expectation 严格校验 required/allowed/forbidden tools、status、error、citation 和回答 marker；未知工具、工具冲突、模板越界会拒绝（schema.py:32-55、63-85、116-152）。 | 保留为行为回归层；新增 strata 字段不要替换原有断言。 |
| Live MCP 生命周期 | preflight 检查真实模型和 key、非 production、评测用户、migration、MCP readiness；运行中发现精确工具集合；finally 停 session 并撤 grant（runner.py:140-184、212-270）。 | 抽成 RunEnvironment/profile 适配器，baseline 与 candidate 共享同一 snapshot。 |
| Repeats / case status | run() 对每个 case 重复执行并保留 attempts；缺 fixture 显式 skip，Gold 记录失败为 pending_review（runner.py:341-372）。 | 用 (experiment_id, arm, case_id, attempt) 作配对主键；skip、unscorable、pending 不得静默进入成功分母。 |
| Safe trace | ToolTrace 只保存 tool name、call index、outcome、boundary；collector 按 request id 关联 tool_call，retrieval detail 仅投影有限 item/segment/start/score（mcp_runtime.py:59-215）。生产 diagnostics 也只允许 allow-list 字段（app/diagnostics.py:265-376）。 | 在 eval-only boundary 增加安全参数摘要/校验结果和 skipped call 记录，发布报告不放 raw args/results。 |
| Gold quality | GoldSample 具备稳定 fixture ref、item/segment key、timestamp range/tolerance、evidence groups、answer boundary/no-evidence 约束（quality.py:47-164）；完整 Gold gate 为 20–30 条且要求六类 kind、多 segment/no-evidence 数量（quality.py:186-213）。 | 复用字段和 scorer，但以新的四-stratum manifest 作为冻结 benchmark 顶层事实源。 |
| Human review | 六项 0/1 rubric；只有 accepted 聚合，pending/disputed 不当作 0（human_review.py:26-78、130-155）。回答正文仅 opt-in 写入被忽略的 .eval-results（runner.py:514-676；.gitignore:19）。 | 保留人工 adjudication；增加 judge disagreement sampling，不把 judge 或未处理 review 当作 deterministic correctness。 |
| 视频 benchmark 模式 | video_recognition 已有 catalog_status、revision、freeze/annotation blockers、固定 runs-per-sample（schema.py:418-510）；dry-run 明确 external_calls: 0 并验证 cleanup（runner.py:99-158）。 | 借用其 manifest/freeze hash、closed gate、safe dry-run、usage/cost 设计。 |
| 输入协议 / 成本原型 | MCP 输入模型有严格字段边界（app/mcp_server.py:143-181、251-365）；video protocol 的 Usage 已包含 token、媒体量和 estimated_cost_usd（evals/video_recognition/protocol.py:18-30）。 | 参数合法率复用 Pydantic schema；成本采集复用 usage normalization，但缺失 usage 必须是 unknown。 |

## 要求、现状与缺口

### 1. 四个显式数据 strata

当前 retrieval case 没有 stratum，也没有 trajectory gold；retrieval.neighbors 和 retrieval.metadata-location 只是通过 required tools 间接表达多步意图（catalog.yaml:16-29），retrieval.no-evidence 只是要求 not_found/无引用（catalog.yaml:38-43）。inventory.retry 是对正常失败条目的业务重试，不是故障注入（catalog.yaml:143-149）。

建议新增冻结数据文件，而不是把所有内容塞进旧 Expectation：

~~~yaml
dataset_id: agent-retrieval-v1
revision: 1.0.0
freeze_hash: sha256:...
fixture_snapshot: fixture-v...
cases:
  - id: simple-001
    stratum: simple_retrieval   # simple_retrieval | multi_hop | no_answer | tool_exception
    case_id: retrieval.search
    turn_index: 1
    query: "..."
    expected_hops: 1
    expected_tools: [search_segments]
    fault: null
    answer_contract: { ... }
~~~

每个 stratum 应有独立样本下限与成功率分母：

- simple_retrieval：一次搜索即可回答，gold item/segment/timestamp；
- multi_hop：明确 hop 数、每 hop 的允许工具和依赖（例如 search → neighbors → open_at），同时保留“可选合理调用”和“必需调用”的区别；
- no_answer：知识库无证据，要求 no-answer 语义、无 unrelated citation，并单独统计 false positive；
- tool_exception：明确注入点、错误类别、预期 retry/recovery、最终状态和无幽灵数据断言。异常必须可重复、有限次、隔离 fixture，不能用普通 failed status 冒充注入。

冻结流程应 canonicalize YAML/JSON，校验稳定 fixture alias/platform ID、字幕/segment identity 和 answer contract，计算 SHA-256；执行时报告 dataset revision/hash，任何内容或 fixture snapshot 漂移都 fail closed。现有 HumanEvalDataset 解决稳定 key 和 evidence 完整度，但没有 revision/freeze hash、四层分布、故障场景和 hop trajectory，只能作为 Gold 子集来源。

### 2. 固定模型、参数、工具和环境

现在 Settings() 从实时环境读取 model、timeout、request/tool/output limits 等（app/config.py:361-393），build_model() 根据当前 provider/base URL 构造模型（app/agent/provider.py:80-107）；live report 主要记录 model/provider（runner.py:750-789），没有完整 settings、temperature/seed、tool schema hash、MCP server revision、DB/embedding/fixture/runtime fingerprint。

建议建立成对的 ExecutionProfile：

~~~text
experiment_id / pair_id
dataset_id + freeze_hash + fixture_snapshot
model provider/name/revision + sampling/max-token settings + seed (若支持)
agent budgets/timeouts + MCP tool schema hash + server/app revision
DB migration head + embedding/index revision + Python/dependency/runtime fingerprint
~~~

baseline 和 candidate 必须在同一 profile 下运行，仅 solution_id/code revision 可变；命令行和环境变量不得在运行中隐式覆盖 profile。preflight 先比较 profile hash，再允许付费调用。真实 provider 不支持 seed 时记录 seed: unsupported，不可伪称 deterministic；关键 case 至少重复 3 次，完整集的 repeat 由 manifest 固定。

### 3. 轨迹层指标

现有 trace 没有模型发出的参数；ToolTrace 只有四个字段（mcp_runtime.py:59-65），Agent 的边界明确“never its arguments or output”（app/agent/agent_tools/policy.py:118-127）。因此不能直接计算：

- tool selection accuracy：只能算 required/allowed/forbidden policy pass，不能判断每一步是否选到 gold tool/path；
- parameter legality：只能依赖服务端是否成功，不能区分合法参数、schema reject、业务 not-found 和 provider fault；
- redundancy：没有 call fingerprint、相同 query/item/segment 重复、无效 reformulation 的判定；
- loop rate：当前 agent_loop_limit_rate 只数达到 limit 的 attempt（runner.py:915-925），不是所有可观测循环。

建议 trajectory.py 在评测专用边界记录 privacy-safe projection，不落 raw 参数：

~~~text
call_index, model_step, tool_name, phase, outcome, skip_reason
parameter_schema_valid, parameter_error_class, parameter_shape_digest
stable argument features (query/item/segment/url-id class; never values)
call_fingerprint, repeated_fingerprint_count, recovery/retry index
~~~

指标定义：

- tool selection accuracy = 有 gold tool step 的步骤中，实际首次选择命中 gold tool/path 的比例；policy violation 和 missing required tool 另列，不能互相替代；
- parameter legality = 收到的模型调用中通过对应 MCP/Pydantic input schema 且满足业务前置约束的比例；schema reject、malformed JSON、越权/非法 scope 分开报；
- redundant call rate = 可判定为同一 (tool, normalized intent, relevant stable target) 且前一调用已有足够成功结果、后一次没有信息增益的调用数 / 可执行调用数；合法 retry/fallback、不同 hop 和失败后的同意 retry 不计冗余；
- loop rate = attempt 中出现重复 fingerprint/reformulation、同一步 skipped、budget exhausted 或真正 loop-limit 的比例，并分别列出原因。AgentDeps 已有 per-step same_step_skipped/stage_budget_exhausted 和 search/expansion budget，可作为观测底座（app/agent/runtime_state.py:20-36、116-136；app/agent/agent_tools/policy.py:300-322）。

报告仍可保留 average_tool_calls 作为诊断，但发布比较不得把“更少调用”当作目标：减少调用若导致 selection/legality/task correctness 下降，应失败。

### 4. 最终回答的视频 ID、链接、时间戳与字幕证据

现有 assert_expectation 只检查 citation 是否存在和 exact URL scope（runner.py:698-745）；CitationProjection 字段为 item/segment/title/excerpt/url/start_sec，没有 end_sec 或 canonical platform/video ID（app/mcp_server.py:184-191）。Gold scorer 的 timestamp 只对稳定 segment 的 start/end 与 gold range 做相交比较（quality.py:373-380），不验证最终 URL 的 video ID 是否和 citation 一致，也不验证 URL ?t=/fragment 与时间戳一致，更不检查字幕正文支持回答结论。

建议 answer_evidence.py 采用强制顺序：

1. parse answer citations，按 YouTube/Bilibili canonicalizer 验证 platform、video ID、URL 归一化和允许 scope；拒绝 ID 与 URL 不一致、非 canonical/非目标视频 citation；
2. 验证 timestamp 是 finite、非负、在视频范围内，并检查 URL timestamp link 与引用 start（允许显式 tolerance）；缺失 end 时标记为 point citation，不假设整段；
3. 用已解析的 citation (video_id, segment_id, range) 读取/匹配本次 fixture 的字幕 segment，判断回答中的 reference_points/claim 是否被字幕文本支持；no-answer 必须没有 unrelated citation，不能仅凭 status=not_found 通过；
4. 输出 video_id_valid、url_valid、timestamp_valid、subtitle_supported、unsupported_claims 等独立布尔/原因码，再汇总 citation_validity。没有字幕或 evidence projection 不完整时是 unscorable/fail closed，而不是 0 或通过。

生产侧已有 normalize_item_reference 用于 scope 比较（app/agent/runtime_state.py:60-70），可复用 parser，但 evaluator 仍需单独实现严格 canonical link/timestamp/字幕支持契约，不能把 production citation 的旧 allow-list 当成完整验证。

### 5. Rubric LLM-as-judge 与人工分歧抽检

当前人工链路只支持人工 verdict/rubric；human-benchmark 的 Gold diagnostics 不自动决定 pass/fail（evals/natural_language/README.md:57-73），HumanReviewDataset 没有 judge record、judge model/config/version 或分歧采样字段。历史 benchmark 设计曾把 LLM judge 定为探索性信息；本次需求明确要求 rubric judge，因此应新增而不是复用旧 human verdict 语义。

建议 judge.py：

- 输入只含 answer、经过第 4 节验证的 citations/subtitle evidence、reference answer/points/answer boundary、stratum；不把未验证的 provider 原文直接喂给 judge；
- 使用固定 judge model/profile、版本化 rubric，要求 strict JSON schema：每 rubric 0/1/unknown、理由 code、overall、judge confidence；保存 judge run/config hash；
- 先跑 deterministic correctness，再跑 judge。judge 不能覆盖 URL/ID/timestamp/schema 等硬错误；主观分是独立维度；
- 以 judge 与 deterministic/human 的不一致、低 confidence、边界分和随机/分层样本组成抽检池；人工 reviewer 对抽检样本 adjudicate，记录 accepted/pending/disputed，只发布聚合值和分母；
- judge 失败、无效 JSON、缺 evidence 或人工抽检未完成时，主观指标标记 unknown，发布门禁 fail closed，不伪造分数。

### 6. 运行层：p95 与单次成功成本

现有 p95 是所有 turn elapsed_ms 的串行百分位（runner.py:895-907），没有 token/usage/cost 字段；TurnResult 也只有 elapsed/model calls/tool calls 等（runner.py:100-118）。因此不能得到“单次成功成本”。video harness 的 Usage/QueryScore 已证明可承载 token、媒体量和 estimated_cost_usd（evals/video_recognition/protocol.py:18-30；scoring.py:80-95），但自然语言 evaluator 仍需 provider usage normalization 和价格表版本。

建议记录：

- attempt-level wall-clock：排除 preflight/fixture setup，明确是否含 MCP startup；p50/p95 按 case/attempt 配对计算，样本数不足时标记 low-n；
- provider usage：input/output tokens、reasoning tokens（若有）、模型调用次数、工具/外部服务成本；未知 usage 为 null/unknown，不能写成 0；
- cost_per_successful_task = total attributable cost / successful task attempts，另外发布 cost/attempt 和 no-success 的 undefined；价格表和估算版本进入 environment snapshot；
- 成本不能成为正确性替代指标；只在 correctness gate 通过后作为运行效率诊断。

## 建议模块边界

保持 runner.py 负责现有 MCP session、grant、fixture 和单臂执行；新增层建议如下：

| 模块 | 职责 |
| --- | --- |
| experiment_schema.py | FrozenDatasetManifest、ExecutionProfile、baseline/candidate arm、judge 和 gate 配置；版本、hash、四 strata、重复次数。 |
| dataset.py / freeze.py | schema 校验、fixture/字幕/segment resolver、canonical serialization、freeze fingerprint 和 drift 检查。 |
| trajectory.py | eval-only tool-call/参数安全投影、Pydantic legality、gold path、fingerprint、redundancy/loop 统计；保留 skipped calls。 |
| answer_evidence.py | video ID↔URL、canonical link、timestamp range/link 和 subtitle claim support 的程序化验证。 |
| judge.py | 固定 rubric judge 的 strict I/O、版本/config 记录、disagreement sampling 和 human adjudication 输入。 |
| comparison.py | 以 case/attempt 配对 baseline/candidate，计算 task/citation/trajectory/runtime 的分层值、delta、置信区间和缺失状态。 |
| release_gate.py | correctness non-regression、loop/p95 improvement、stratum floors、unknown/不配对 fail-closed，输出可审计 gate decision。 |
| 既有 quality.py / human_review.py | deterministic Gold 和人工 accepted-only 聚合；不把 judge 或 policy pass 直接混入 correctness。 |

建议报告目录至少保存 manifest.json、profile.json（敏感值 hash）、baseline/、candidate/、comparison.json 和 sanitized report.json/md；raw answer/validated evidence 只在显式 opt-in 的 ignored local review package 保存。

## 指标与发布门禁

结果层按四 strata 和全量分别给出：

~~~text
task_success_rate
retrieval/citation correctness (Recall@1/3, MRR, precision, completeness,
  timestamp + video-link + subtitle-support validity)
no_answer_false_positive_rate
tool_exception recovery/final-success rate
~~~

轨迹层给出：

~~~text
tool_selection_accuracy
parameter_legality_rate
redundant_call_rate
loop_rate {duplicate, reformulation, same_step_skipped,
           budget_exhausted, loop_limit}
safety_violation_rate (zero tolerance)
~~~

运行层给出：

~~~text
p50/p95 latency (paired attempt level)
cost_per_successful_task, cost_per_attempt, unknown_usage_count
~~~

release_gate.py 的默认语义应为 fail closed：

1. 先确认两臂的 dataset/profile/fixture/tool-schema hash 相同、每个要求的 case/attempt 都可配对，且四 strata 均达到最低样本数；
2. candidate 的 correctness 指标（task success、video/link/timestamp/subtitle validity、no-answer false positive、exception recovery 等）不得低于 baseline。为应对随机性，配置绝对容差或 paired confidence interval；“不下降”的容差必须写入 manifest，缺失/unknown 不能视为相等；
3. candidate 的 loop rate 和 p95 必须达到明确的改善阈值（默认严格小于 baseline，或 manifest 指定最小绝对/相对改善）；若“改善”允许非严格，应由配置显式决定，不能解释性放宽；
4. 任一 safety violation、参数非法率下降、分层 correctness floor 失败、judge/human 必要数据缺失或环境漂移，均拒绝发布。成本只作为发布后比较/报告，不能抵销正确性回归。

这直接落实“正确性不下降，同时循环率和 p95 改善才发布”：只少调用但没有质量改善，或者 p95/loop 仅一个改善，都应 eligible: false。

## 分阶段实施计划

1. 冻结数据与离线契约：新增四-stratum manifest、stable fixture/segment/subtitle resolver、revision/hash、schema/分布/负例/故障注入校验；先完成 validate-dataset 和无外部调用的 freeze/drift tests。
2. 单臂轨迹与证据投影：在不改变 Agent policy 的前提下增加 eval-only parameter adapter、工具 path、合法率、fingerprint/redundancy/loop；完成 ID/link/timestamp/subtitle deterministic scorer。
3. 配对运行：把现有 LiveEvaluator 适配成 baseline/candidate 两臂；同 profile、同 fixture、同 case/attempt seed/order，关键 case 固定 repeat（建议 3）；写 paired aggregation、p95/cost normalization。
4. 主观评测：接入 rubric judge、strict schema、分歧采样和人工 adjudication；主观指标与硬正确性分开发布。
5. 发布门禁与 CI：实现 fail-closed comparison/gate、safe report 和审计产物；只有显式 opt-in 才开真实付费运行，默认 dry-run/preflight 不产生外部调用。

## 建议验证命令与测试

已有安全/回归命令：

~~~bash
.venv/bin/python -m evals.natural_language --validate-catalog
.venv/bin/python -m evals.natural_language --preflight
.venv/bin/python -m evals.natural_language --case retrieval.search --repeat 1
.venv/bin/python -m evals.natural_language --all --repeat 3
.venv/bin/python -m evals.video_recognition --validate-catalog
.venv/bin/python -m evals.video_recognition --dry-run
~~~

新增后建议提供：

~~~bash
.venv/bin/python -m evals.natural_language --validate-dataset \
  --dataset evals/natural_language/datasets/agent-retrieval-v1.yaml
.venv/bin/python -m evals.natural_language --freeze-check \
  --manifest ... --profile ...
.venv/bin/python -m evals.natural_language --experiment ... \
  --baseline ... --candidate ... --repeat 3
~~~

离线单元测试至少覆盖：manifest/hash/drift、四 strata 分布和 no-answer/fault schema、stable fixture resolution、tool selection/argument legality、duplicate/retry classification、URL/ID/timestamp/subtitle support、judge JSON/unknown/disagreement、paired aggregation/p95/cost 和 every release-gate branch。真实运行测试必须显式标注付费/写状态，并验证 teardown/fixture rollback；不要用 fake model 的结果冒充 release evidence。

## 已运行验证与限制

- .venv/bin/python -m evals.natural_language --validate-catalog 已通过：catalog 1.0.0: 22 cases valid。
- .venv/bin/python -m evals.video_recognition --validate-catalog 已通过；video benchmark 当前执行 gate 仍因 catalog_status_not_frozen 等 blocker 关闭，说明其 freeze/closed-gate 模式是可复用设计，不是本项目已完成的发布资格。
- .venv/bin/python -m evals.video_recognition --dry-run 已通过，external_calls: 0，would_run_count: 396，cleanup 成功。
- .venv/bin/python -m pytest -q tests/test_natural_language_evaluator.py tests/test_natural_language_quality.py 结果为 42 passed, 1 failed。失败是已有环境/config drift：test_preflight_refuses_production_before_opening_database 在 production 设置下被 wildcard browser companion origin 校验拒绝（测试位置 tests/test_natural_language_evaluator.py:103-116，相关 Settings 校验在 app/config.py），不是本研究新增改动造成；实施时应单独修复/稳定测试环境。

## 开放风险

- provider/model 随机性、限流和版本漂移会影响“不下降”和 p95；需要 paired repeats、CI/容差和 low-n 标记。
- 为计算参数合法率而捕获参数可能泄露 query、URL 或 ID；必须采用 eval-only、字段类型/shape/fingerprint projection，禁止 raw args 进入发布报告。
- 字幕缺失、重新入库或切段版本漂移会让 subtitle support 不可比；fixture snapshot、segment schema hash 和 unscorable 规则必须冻结。
- mutable fixture、MCP session/worker/Redis/MinIO/embedding 故障注入可能污染共享状态；每个 scenario 需要独立 namespace、bounded recovery 和 teardown 断言。
- provider usage/价格表不完整时，cost per success 是 unknown，不可把未知成本计为零；tiny-n 的 p95 只能作为低置信诊断。
- judge 有 rubric bias/自洽偏差；分歧抽检和人工 adjudication 是必要校准流程，judge 不能替代 deterministic evidence gate。
- “循环率改善”和“p95 改善”的严格程度会改变发布结果，必须在冻结 manifest 中声明阈值、方向和统计方法，不能在看到结果后调整。
