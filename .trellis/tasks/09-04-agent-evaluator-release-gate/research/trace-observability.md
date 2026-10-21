# Agent evaluator：追踪可观测性与发布门禁研究

## 结论摘要

当前 evaluator 已经能在真实 MCP/Chat 栈上比较部分结果指标：case/turn 成功状态、允许/禁止的工具名、检索排名、最终 Citation 的 item/segment/timestamp，以及 turn 级 p50/p95。它还具备明确的安全边界：生产诊断不记录问题、答案、工具参数/返回值或 provider payload；human review 才在被明确要求时保留答案。

但它还不能可靠回答本任务最关键的几件事：

- 没有 `simple_retrieval`、`multi_hop_retrieval`、`no_answer`、`tool_error` 四层的冻结数据契约，也没有 baseline/candidate 的同环境比较和 manifest hash。
- `ToolTrace` 只有 tool name、call index、outcome；参数在运行时和报告中被有意丢弃，collector 还会按 `(call_index, tool_name)` 合并生命周期，因此无法计算真实的参数合法率、重复调用率或通用 loop rate。
- primary Agent 与 answer Agent 都创建了 `RunUsage`，但 usage 只留在局部变量；当前没有 token 价格表或成功单次成本投影。
- 最终 Citation 的结构和 gold item/segment/timestamp 可评分，但“视频 ID/链接/时间戳是否由程序验证”和“结论是否得到字幕语义支持”是两个不同层次；后者目前只有人工 rubric，没有带固定 rubric 的 LLM-as-judge。

因此最小安全方案应是 evaluator-only 的进程内投影：保留原有产品诊断和 SSE 的隐私契约，只增加固定枚举、计数、布尔值、顺序号和 usage 汇总；参数原文、工具结果、问题、答案、URL/字幕文本不进入生产日志或 sanitized report。最终流程固定为“程序验证引用 → 仅对通过验证的答案和有界字幕证据做 judge → 对分歧样本人工抽检”。

## 1. 现有链路与可用信号

| 层 | 当前实现 | 可直接复用的信号 | 主要缺口 |
| --- | --- | --- | --- |
| case/turn 结果 | runner 逐 turn 调 MCP，按 expectation 断言，再聚合 case | status、error code、pass/fail、repeat、elapsed | 没有 candidate-vs-baseline；没有四层标签和冻结 hash |
| Agent trajectory | `AgentDeps.tool_event()` 记录 tool lifecycle；diagnostic collector 再投影为 `ToolTrace` | tool name、call index、outcome、result count、phase | 没有参数 schema 结果、顺序完整性、参数身份、重复/loop 结论 |
| 检索质量 | Gold scorer 使用 ranked hits 和最终 citations | Recall@1/3、MRR、citation precision/completeness、timestamp hit；no-evidence false positive | 不验证 URL canonical identity；不判断字幕对答案的语义支持 |
| 运行性能 | MCP facade 与 runner 都测 elapsed；summary 计算 p50/p95 | turn 级延迟、model/tool call 计数 | 没有 attempt/end-to-end 级 p95 约定；没有成功单次成本 |
| 主观质量 | opt-in 本地 human review package | 六项 0/1 rubric、pass/fail、pending/disputed | 没有 LLM judge；没有自动抽取 judge/人工分歧 |

runner 当前 report 只写 catalog version、model/provider、readiness、fixture proof 和 summary【`evals/natural_language/runner.py:752`】；`EvalConfig` 只有 repeat、threshold、timeout 等运行开关【`evals/natural_language/runner.py:71`】。这正是新增 profile/manifest 的位置，但不能把 API key 或完整 endpoint 写进报告。

## 2. 指标逐项审计与建议定义

### 2.1 任务成功率（结果层）

现有 `_assert_turn()` 将模型工具分为 terminal（`succeeded/failed`）和 observed（包含 skipped），再用 `Expectation` 校验工具、状态、error code、Citation 和回答标记【`evals/natural_language/runner.py:425`】【`evals/natural_language/runner.py:439`】【`evals/natural_language/runner.py:698`】；summary 已计算 `task_success_rate`【`evals/natural_language/runner.py:876`】。

建议契约：

1. 一个 turn 只有在状态/错误、工具策略、引用程序校验和（若适用）gold 证据都通过时才成功；`no_answer` 的 `not_found/no_evidence` 是合法成功，不应按普通回答缺 Citation 处理。
2. 一个 case 必须所有 turn 成功；同一 case 的每个 repeat 单独计数，不能用 case 的平均 turn 分数掩盖失败。
3. 按四个 layer 和 case 单独给 numerator/denominator；缺 fixture、基础设施失败、未完成 judge 应为 `unknown/skip`，不能转成 0 或 pass。
4. 正确性主门禁至少包括 task success、工具选择准确率、参数合法率、引用 ID/URL/timestamp 校验率、字幕支持率、no-answer false-positive rate 和安全违规率；各项都按 layer 比较。

### 2.2 工具选择准确率（轨迹层）

catalog 的 `Expectation` 已支持 `required_tools/allowed_tools/forbidden_tools/statuses`【`evals/natural_language/schema.py:32`】；runner 将 terminal 工具和所有 observed 工具分开，因此能识别“实际完成了什么”和“模型请求过什么”【`evals/natural_language/runner.py:439`】。现有 `tool_policy_pass_rate` 只给一个通过率【`evals/natural_language/runner.py:881`】。

建议同时输出以下可解释指标，而不以调用次数替代质量：

- `required_tool_recall`：gold 要求的工具中实际 terminal 的比例；
- `allowed_tool_precision`：observed 工具中属于该 case allow-list 的比例；
- `forbidden_tool_rate`：observed forbidden/safety-critical 工具的比例，安全关键工具仍 zero tolerance；
- `trajectory_contract_pass_rate`：顺序、前置关系、阶段预算和 retry 规则全部满足的 turn 比例。

`multi_hop_retrieval` 应允许并要求有依赖关系的 `search_segments → get_neighbors/get_item/open_at`，不能把“更多但有证据的调用”自动判成坏结果。运行时本来就把同一 model step 的后续 retrieval 作为 `skipped/same_model_step`，把预算耗尽作为 `skipped/budget_exhausted`【`.trellis/spec/backend/agent-retrieval-convergence.md:149`】【`.trellis/spec/backend/agent-retrieval-convergence.md:154`】；选择准确率应把这类请求保留在 observed 轨迹中，同时把它们是否到达 backend 单独报告。

### 2.3 参数合法率

工具边界已经有 typed schema。例如 retrieval 的 `query/limit/item_id` 在 handler 边界校验 item_id 形状【`app/agent/agent_tools/retrieval.py:42`】，management 工具用正整数、list 长度和 Literal/字符串长度约束【`app/agent/agent_tools/actions.py:117`】【`app/agent/agent_tools/actions.py:148`】【`app/agent/agent_tools/actions.py:180`】；MCP public schema 也使用 `StrictInt/StrictStr` 和范围约束【`app/mcp_server.py:177`】。这说明“非法参数被拒绝”可以测，但当前 trace 没有“哪一次校验通过/失败”的字段，Expectation 也没有参数 schema【`evals/natural_language/schema.py:32`】。

建议增加 evaluator-only 固定字段：

```text
parameter_validation = passed | failed | not_applicable
parameter_failure_code = <allow-listed code> | null
validation_boundary = model_tool | mcp_input | service | none
```

具体参数值永远不出进程；参数失败只导出固定 code，例如 `invalid_limit`、`invalid_item_id`、`invalid_radius`、`batch_too_large`。PydanticAI 在 handler 之前拒绝的函数调用也必须经过同一个 evaluator callback，否则合法率会被系统性高估。分母应为模型发起的工具请求（包括 schema failed），而不是仅成功 backend 调用；deterministic/setup MCP turn 标记 `not_applicable`。

### 2.4 冗余调用率

现有 Agent runtime 有内部 read fingerprint，并用它绑定 exact retry/recovery【`app/agent/agent_tools/policy.py:183`】【`app/agent/agent_tools/policy.py:237`】；但日志规范明确禁止输出 exact-read fingerprint、retry arguments 和 error payload【`.trellis/spec/backend/logging-guidelines.md:78`】。`ToolProgressObservation` 也明确只保留 name/index/outcome/result count，不保留 args/results/provider metadata【`app/agent/runtime_state.py:46`】。因此不能从现有 sanitized trace 推断“同一个工具但参数相同”还是“同一个工具的合理改写”。

建议定义：

- 在同一个 turn 内，先对参数做 server-owned canonicalization，再在进程内用 ephemeral digest 或 opaque identity 比较；digest 不写日志、不写 report、不跨 run 保存。
- `is_redundant=true` 只表示同一 canonical operation 在没有新证据/状态变化时被再次请求；不同 query、不同 item/segment、合法的 `retry_same_read`、有状态变化后的必要重读必须有独立 reason，不能误判。
- 同时给两个分母：`redundant_request_rate = redundant_model_requests / all_model_tool_requests`，以及 `redundant_backend_call_rate = redundant_started_calls / all_non_skipped_started_calls`。前者反映 agent 轨迹，后者反映真实资源浪费；skipped 请求不应伪装成 backend 调用。
- 报告只保留 `is_redundant`、`duplicate_of_present`、`duplicate_reason` 固定枚举和聚合计数。不要把 HMAC/fingerprint 本身写入 diagnostics；这会违反现有隐私约束。

### 2.5 Loop rate

现有字段 `agent_loop_limit_rate` 实际只统计 `error_code == "limit"`【`evals/natural_language/runner.py:915`】，它是 budget/limit failure，不是通用循环检测。当前 collector 将同一 `(call_index, tool_name)` 的多个 lifecycle 取优先级最高的一条【`evals/natural_language/mcp_runtime.py:157`】，还会丢失重复调用次数和原始顺序，无法从中恢复 loop。

建议拆分：

- `budget_limit_rate`：服务/Agent 明确返回 `limit` 的 attempt 比例；保留现有含义；
- `loop_rate`：被 detector 判为无进展重复轨迹的 turn/attempt 比例；
- `loop_reason`：`same_operation_no_progress`、`cycle_without_new_evidence`、`repeated_skipped_batch`、`unknown`。

一个可测试的初版 detector 是：同一 canonical operation 在同一 turn 重复且中间没有新 evidence/state，或 model attempt 间出现相同工具签名周期达到阈值；正常 `search → neighbors → open_at` 因工具/参数不同且产生新 evidence，不算 loop。允许的 exact retry 应单独标为 recovery，不直接算 loop。分母用“可检测的 model turn/attempt”，不要用所有 tool call；否则多跳 case 会被错误惩罚。

### 2.6 视频 ID、链接、时间戳的程序验证

MCP 最终输出的 Citation projection 已包含正数 `item_id/segment_id`、title、excerpt、url 和非负 `start_sec`【`app/mcp_server.py:184`】；facade 将 server-owned Citation 映射到该 projection【`app/mcp_server.py:455`】。Citation 的 URL 和 start 是从 tenant-owned item/segment 生成的，视频平台则通过 `timestamp_url()` 构造带时间跳转的 URL【`app/agent/services.py:55`】【`app/agent/services.py:65`】。这些是程序验证的可靠输入，但不是验证已经完成的证明。

最终答案必须按以下顺序处理：

1. 解析每条 final citation，校验 `item_id/segment_id` 类型和正值；通过 `normalize_item_reference` 归一化平台与 platform ID，并与 gold fixture/可信 item resolver 比较；校验 URL host、视频 ID、canonical URL 以及 timestamp 参数。
2. 对 `start_sec` 做 finite、非负、落在可信 segment/item duration 内的校验。当前 public projection 没有 `duration_sec`，所以“是否超出视频长度”不能只靠 MCP output；需要 evaluator 通过 dedicated read-only resolver 或 server-owned `citation_validation` 布尔 projection 取得 duration 结论。
3. 只输出固定结果，如 `video_identity_valid`、`url_valid`、`timestamp_valid` 或 `citation_validation_code`；不要把解析后的原始 URL/字幕内容放入 sanitized report。

Gold scorer 当前只根据稳定的 item/segment identity 和 timestamp range 计算 Citation Precision/Completeness/Timestamp Hit Rate【`evals/natural_language/quality.py:383`】【`evals/natural_language/quality.py:421`】。这能发现检索/选择错误，但不能替代 canonical URL、duration 或实际字幕语义检查。

### 2.7 字幕证据支持结论与 LLM-as-judge

Composer 只接收 server-owned 的 bounded evidence，validator 主要检查 Citation allow-list、结构和范围错误【`app/agent/answer_pipeline.py:321`】【`app/agent/answer_pipeline.py:346`】；`classify_evidence_failures()` 已区分 retrieval miss、evidence selection miss 和 answer contract failure【`evals/natural_language/quality.py:298`】，没有 semantic entailment 类别。因此“有 Citation”不能当作“字幕支持结论”。

建议固定为两阶段：

1. 先完成上节程序校验。程序校验失败的答案标为 factual/citation fail，不能由 judge 挽救；no-answer 则检查无 Citation 且没有未授权事实。
2. 只把“已通过校验的答案 + server-approved subtitle excerpt/segment + gold question kind”放进本地 review package，调用 evaluator-owned 的固定 judge model、固定 prompt/rubric/schema。judge 输出六个 0/1 字段和 `pass/fail`：`answered_question`、`evidence_grounded`、`correct_video`、`timestamp_locatable`、`no_unsupported_claims`、`tone_and_guidance`。现有同名 HumanRubric 已有这六项【`evals/natural_language/human_review.py:26`】。
3. Judge 不能读取未验证 URL、完整历史、provider payload 或其他候选证据；judge 的 rationale/答案只留在被忽略的本地 review package。抽检集合包含程序校验与 judge 分歧、baseline/candidate 分歧、低置信/异常输出，并按四层分层抽样。
4. 人工结果是 release authority 的校准层。当前 aggregator 只把 `adjudication: accepted` 纳入分母，pending/disputed 不当作零分【`evals/natural_language/human_review.py:130`】；沿用这一约定。

## 3. 四层冻结数据集设计

### 3.1 数据结构

现有 catalog 只有 `retrieval/save/inventory/context/conversation/safety` 等类别【`evals/natural_language/schema.py:18`】，已有 search、neighbors、no-evidence case，但没有显式四层 taxonomy【`evals/natural_language/catalog.yaml:8`】【`evals/natural_language/catalog.yaml:16`】【`evals/natural_language/catalog.yaml:38`】。不要把现有 catalog 原地改成不可追溯的新含义；建议新增版本化 evaluator manifest（或把 schema 版本升到下一版），每个样本至少包含：

```yaml
dataset_version: 1.0.0
manifest_hash: <sha256, generated from canonical bytes>
layer: simple_retrieval | multi_hop_retrieval | no_answer | tool_error
case_id: ...
fixture_ref: ...
gold: {item_id, segment_ids, timestamp_range, evidence_groups, no_evidence}
trajectory: {required_sequence, allowed_tools, forbidden_tools, parameter_contract, max_attempts}
fault: {kind, trigger, expected_error_code}   # only tool_error
repeat_policy: {default: 1, key: 3}
```

manifest hash 应覆盖 case text/templates、fixture identity snapshot、gold evidence、trajectory contract、fault schedule、rubric version、tool manifest 和 schema version。任何字段变化都生成新 dataset version；runner 当前只检查 catalog version【`evals/natural_language/runner.py:756`】，需要增加 hash 和 frozen-at metadata。

### 3.2 四个 strata

| layer | 最小语义 | 期望轨迹/结果 | 必须覆盖的失败形态 |
| --- | --- | --- | --- |
| `simple_retrieval` | 一个明确主题、一个可检索 fixture、单步证据足以回答 | `search_segments`，可选一次 `open_at`；`ok`、正确 Citation、ID/URL/timestamp 通过 | 错视频、错 segment、无 timestamp、无证据却胡答 |
| `multi_hop_retrieval` | 需要先找候选，再展开上下文/详情/定位 | 有序 `search_segments → get_neighbors/get_item/open_at`；每一步的输入来自前一步可信结果；允许更多调用但必须有新证据 | 依赖顺序错、重复同参、跳过必要工具、调用预算耗尽 |
| `no_answer` | 查询主题不在知识库或没有满足 gold 证据 | `search_segments` 成功但空结果，server-owned `not_found/no_evidence`，无 Citation、无 unsupported fact | 把空搜索误判成 transient error、常识补答、伪造 Citation |
| `tool_error` | 固定 fault fixture 让某个 tool/embedding/retrieval 失败 | 固定 error code、只执行允许的 recovery；不得无限 retry 或改参逃逸 | transient read、embedding/retrieval unavailable、schema invalid、provider/limit；区分 recovery 与 loop |

`tool_error` 应使用 evaluator 可控的 fault injection/fixture，而不是依赖随机网络故障；同一 fault schedule 必须给 baseline 和 candidate。真实 evaluator 的 preflight 已拒绝 fake model、production、过期 migration 和不完整 readiness【`evals/natural_language/runner.py:140`】【`evals/natural_language/runner.py:156`】，所以故障注入要位于受控的 service/tool seam，并在 profile 中固定声明。

建议 full gate 至少每层 8–10 个独立样本，覆盖视频/主题/错误种类；smoke 可以各层 2–3 个。关键多跳、无答案、每种工具异常至少 repeat 3，所有 repeat 保留单独 attempt row。精确数量由父任务定稿，但不能只报告混合宏平均，否则某一层回归会被其他层抵消。

## 4. baseline、candidate 与工具环境冻结

### 4.1 当前风险

运行配置从环境读取：默认模型、base URL、timeout、request/tool/output limits、composer max tokens 和 streaming 都是 Settings 字段【`app/config.py:361`】；`build_model()` 可以根据任意 configured model/base URL 构造 provider【`app/agent/provider.py:80`】。因此仅在 report 中打印 `model/provider` 不能证明两次实验可比。`parallel_tool_calls=False` 也是 provider hint，不是边界【`.trellis/spec/backend/agent-retrieval-convergence.md:147`】。

### 4.2 固定 profile

建立 evaluator-owned allow-list，例如 `baseline-v1`、`candidate-v1`，profile ID 解析出以下非 secret 字段：

- model/provider/model ID、temperature/seed（若 provider 支持）、primary/composer 参数和 system-prompt hash；
- Agent timeout、request/tool/output/composer budgets、tool retry policy、parallel-call policy；
- embedding model/dimensions、retrieval algorithm/config、MCP server/tool manifest/version；
- repository commit、Python/dependency lock、migration head、fixture snapshot ID/hash；
- dataset manifest hash、judge model/prompt/rubric version、cost price-table version。

运行时只允许 profile 选择和 secret 注入；禁止通过环境变量悄悄覆盖 profile 的模型、参数、工具清单或数据集。baseline 与 candidate 必须在相同 fixture snapshot、专用 evaluation user、独立 conversation namespace、相同 fault schedule 和相同 repeat policy 下运行。现有 runner 已要求 dedicated user 和非 production【`evals/natural_language/runner.py:146`】【`evals/natural_language/runner.py:160`】，应扩展为 profile compatibility preflight，而不是重新绕过这些保护。

报告包含 `profile_id`、profile hash、dataset hash、tool-env hash、code commit 和 readiness proof；API key、raw endpoint secret、tenant identity、question/answer、args/results 不进入 sanitized report。产品 logging 规范明确禁止这些字段【`.trellis/spec/backend/logging-guidelines.md:90`】。

### 4.3 重复运行

`NATURAL_LANGUAGE_EVAL_REPEAT` 当前允许 1–20【`evals/natural_language/runner.py:82`】，runner 为每个 attempt 生成独立 conversation ID【`evals/natural_language/runner.py:374`】。扩展 manifest 的 `repeat_policy.key_cases`，而不是临时命令行挑 case；每次重复记录 `attempt_id`、profile/dataset hash、fixture state 和结果。若 provider 不支持 seed，要明确标记 stochastic，而不是把不同答案误当环境变化；warm/cold、fault injection、数据库 fixture 预热状态也要作为 profile 的固定组成部分。

## 5. 运行指标与成本

### 5.1 p95 延迟

runner 当前在每次 MCP call 周围测 elapsed【`evals/natural_language/runner.py:389`】，summary 对所有 turn 的 `elapsed_ms` 计算 p50/p95【`evals/natural_language/runner.py:895`】，分位点实现是排序后 nearest-rank 风格的 `ceil(n*q)-1`【`evals/natural_language/runner.py:945`】。这不是完整的 attempt/end-to-end 定义，且会混入 direct deterministic turn 和失败 turn。

建议同时报告：

- `turn_latency_ms_p95`：相同 layer、相同 route、相同 repeat 集合；
- `attempt_latency_ms_p95`：从该 attempt 第一 turn 发出到最后一个 turn 完成的 wall time；
- `successful_turn_latency_ms_p95` 与 `all_turn_latency_ms_p95`，明确分母；
- 样本数、quantile algorithm、warm/cold/fault profile。

发布门禁用预先指定的 attempt-level p95；baseline/candidate 必须使用相同样本、顺序策略和环境，缺失/非 finite p95 直接 `incomparable`，不能用 0 替代。

### 5.2 成功单次成本

primary Agent 在 `_run_primary_agent()` 创建 `RunUsage`，并把它传给 Agent，但当前 usage 只在局部变量中【`app/agent/orchestrator.py:560`】【`app/agent/orchestrator.py:581`】。answer plan/section/recovery 也分别新建 `RunUsage`【`app/agent/answer_pipeline.py:682`】【`app/agent/answer_pipeline.py:636`】【`app/agent/answer_pipeline.py:887`】；没有任何 evaluator/app 的 Agent pricing module。故现在的成本只能是 unknown，不能从调用次数猜成本。

最小方案：

1. 在 primary、answer plan、每个 section、每个 recovery attempt 的 owner boundary 捕获 usage counters；聚合 input/output/cache/audio token 等 provider-neutral 数字，不传 provider raw details。
2. evaluator 内置带版本的 `price_table`，按 profile 的 model/provider 和 token 类型计价；价格表变化必须升版本。若 provider 没返回所需 usage，结果是 `cost_status=unknown`，不是 0。
3. 定义 `successful_run_cost_usd = total_known_model_cost / successful_attempt_count`，另报 all-attempt cost、unknown count。是否计 embedding/tool infra 成本要在 profile 中声明；当前代码没有统一的 Agent 成本来源，视频识别的 `estimated_cost_usd` 不能直接复用。
4. 成本用于运行层横向比较和诊断，不替代正确性、loop 或 p95 门禁；不要因为更少调用就判优。

## 6. 最小安全集成方案

按以下顺序实现，能尽量不触碰产品响应契约：

1. **Evaluator schema**：增加 layer、gold trajectory、parameter contract、fault、repeat policy、profile/manifest hash；保持现有 catalog 的 strict extra-forbid 和唯一 ID 校验【`evals/natural_language/schema.py:28`】【`evals/natural_language/schema.py:116`】。
2. **Ephemeral trace adapter**：在 `AgentDeps.tool_event()`/Agent tool-call seam 和 MCP input boundary 采集完整 lifecycle 顺序。内部 event 最少含 `sequence_no, phase, tool_name, call_index, outcome, result_count, parameter_validation, retry_kind, is_redundant, loop_reason`。不要继续用 collector 的 `(call_index, tool_name)` 合并作为唯一来源；它只能作为兼容 projection【`evals/natural_language/mcp_runtime.py:157`】。
3. **隐私投影**：参数 canonicalization、重复判断和 loop detector 在 evaluator 进程内完成；最终只导出固定 enum/bool/count。产品 `RequestDiagnostics` 的 allow-list 不接受任意 extra，生产异常只保留 class/error code【`app/diagnostics.py:311`】【`app/diagnostics.py:362`】。不要把 raw args/results、exact fingerprint、字幕、provider ID 加回日志或 SSE；streaming contract 明确禁止这些字段【`.trellis/spec/backend/agent-execution-streaming.md:9`】。
4. **Usage adapter**：将各 `RunUsage` owner 的 aggregate counters 通过内部 callback 交给 evaluator；不放入 MCP `AskStashSeekOutput`。该 output 当前只有 status/answer/citations/request/elapsed/error【`app/mcp_server.py:223`】，保持 public contract 不变。
5. **Profile/manifest preflight**：解析 allow-listed baseline/candidate，校验 dataset/tool-env/code/fixture/judge/price-table hash 一致；不一致则拒绝比较。现有 preflight 的 dedicated user、real model、非 production、migration/readiness 检查继续保留【`evals/natural_language/runner.py:146`】。
6. **Citation validator**：在 `_assert_turn()` 的 Gold scorer 前加入 item/video/url/timestamp resolver，输出 fixed validation codes；通过才建立给 judge 的 bounded evidence package。Gold scorer 的 retrieval/citation rank 指标继续保留，不把二者混成一个分数。
7. **Judge/review adapter**：使用固定 judge profile、rubric version 和 JSON schema，输出本地 ignored review package；只将聚合的 judge/human 结果用于 report。人工抽检 judge 与程序校验、baseline/candidate 分歧样本，接受现有 `accepted` 分母约定。
8. **Comparison gate**：对齐两个 run 的 manifest/profile/repeat/sample denominator 后，输出 per-layer、per-case、overall 结果和 gate decision；不要修改 public answer、SSE 或生产日志来完成评测。

## 7. 发布门禁草案

对每个 layer `s`，先要求 baseline 与 candidate 的 dataset/profile/tool-env hash、样本集合、repeat policy、fault schedule 和有效 denominator 完全一致。然后：

```text
correctness_candidate(s) >= correctness_baseline(s)  # 所有正确性指标，逐层
loop_rate_candidate      < loop_rate_baseline
attempt_p95_candidate    < attempt_p95_baseline
```

其中“所有正确性指标”至少包括 task success、tool choice、parameter validity、video identity/URL/timestamp validation、subtitle support、no-answer false-positive、safety violation；低值更好的 false-positive/violation 要按反向比较。任一指标为 unknown、denominator 不一致、judge/human 分歧未处理或 profile/hash 不一致，都应为 `blocked/incomparable`，不能发布。严格 `<` 意味着 baseline 已为 0 或 p95 缺失时不能声称“改善”；是否允许饱和指标例外必须由产品 owner 明确写入 gate policy。

成本和调用数量列为运行诊断：报告 `successful_run_cost_usd`、unknown count、model/backend calls 和 redundant request/backend rates，但不把更少调用当作质量优越，也不让成本降低抵消正确性回归。`parallel_tool_calls=False` 不能作为“不可能循环”的证据，因为项目规范明确说明它只是 advisory【`.trellis/spec/backend/agent-retrieval-convergence.md:147`】。

## 8. 测试与验收建议

### 数据与配置

- catalog/manifest：四层必填、case ID 和 gold ID 唯一、gold timestamp finite/non-negative/ordered、no-answer 不得声明 gold item/segment/timestamp；已有 GoldSample validator 可复用这些约束【`evals/natural_language/quality.py:114`】。
- manifest hash：任一 query、gold segment、trajectory、fault、rubric、tool manifest 或 price table 改动都改变 hash；旧版本只读，不能覆盖。
- profile mismatch：model/provider/参数/budget、MCP tool 清单、commit、fixture、dataset、judge、price table 任一不一致，preflight 必须拒绝；secret 不得出现在报告。
- repeat isolation：同一 key case repeat 3，conversation ID 独立，结果/trace 每次分开；失败 fixture 不存在时是可见 skip，而不是伪造通过。

### 轨迹、参数与 loop

- 用 synthetic trace 覆盖：正常 single-hop、多跳不同参数、同参重复、query 改写、合法 exact retry、failed→retry、same-step skipped、budget-exhausted skipped、batched provider calls。断言 lifecycle 顺序和 multiplicity 不丢失。
- 参数 schema：`limit=0`、负 item/radius、空/超长 query、超长 item list、非法 Literal 等必须得到固定 validation code；sentinel 参数原文不得出现在 event/report/log。
- redundancy：相同 canonical args 标为 redundant；不同 query/item/segment 不标；有新 evidence/state 后的必要重读不标；同时验证 request-rate 与 backend-rate 分母。
- loop：重复无进展 operation/cycle 判 loop；`search → neighbors → open_at` 不判 loop；`limit`/provider failure 与 loop 分开；仅 skipped 的同 step batch 不直接等同 loop，重复无进展后才判。

### 结果、证据与主观质量

- citation validator：正确/错误 platform ID、URL host、视频 ID、timestamp 参数、负/NaN/out-of-duration timestamp；跨 fixture URL、segment/item 不匹配、no-answer 伪 Citation 均失败。
- Gold quality：检索 miss、evidence selection miss、answer contract failure 分层；no-answer 的 retrieval/citation false-positive 分母独立。现有 scorer 已提供 Recall/MRR/Citation/Timing 基线【`evals/natural_language/quality.py:428`】。
- judge：固定 schema 只接收已程序验证的 answer 和 bounded subtitle evidence；测试 unsupported claim、正确引用但语义不支持、正确视频错误 timestamp、no-answer；judge 输出失败/超时不变成 pass。
- 人工分歧抽检：至少覆盖每层的程序/judge 分歧、baseline/candidate 分歧和 judge 异常；pending/disputed 不进 accepted 分母，且仍在 report 中可见。

### 性能、成本与隐私

- p95：给定延迟样本验证 percentile、attempt/turn 分母、successful/all 分离和严格 candidate `<` gate；相同样本顺序与 warm/cold profile。
- usage/cost：fake `RunUsage` 只用于单元测试，覆盖 primary、answer plan、section、三次 recovery；价格表算术、cache/audio token、缺 usage→unknown、成功单次成本分母都要有断言。
- 安全回归：向 trace/answer/provider error 注入 question、answer、raw args/results、URL、evidence excerpt、tenant、secret sentinel；验证产品 diagnostics、SSE、sanitized report 均不泄漏。Human review package 是唯一经显式 opt-in 的答案保留位置，当前 README 已将其与 sanitized report 分开【`evals/natural_language/README.md:33`】【`evals/natural_language/README.md:57`】。

以上方案能在不扩大产品日志或 public MCP contract 的前提下，补齐四层数据、baseline/candidate 可比性、轨迹指标、引用/字幕证据链、LLM judge、成本和 release gate；实施前应先将本报告中的字段和 gate 规则固化到父任务的 `prd.md/design.md/implement.md`，再进入代码阶段。
