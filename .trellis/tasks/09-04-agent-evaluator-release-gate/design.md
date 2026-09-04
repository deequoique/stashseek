# Agent Evaluator 技术设计

## 1. 设计目标与边界

本任务扩展现有 `evals/natural_language`，不重写 Agent，也不改变 public MCP/HTTP/SSE 响应。现有 `LiveEvaluator` 继续负责真实模型、MCP session、临时授权、fixture 准备和单臂生命周期；新增 evaluator-owned 层负责冻结、轨迹投影、证据校验、judge、配对比较和发布决策。

核心原则：

1. **同一事实源。** 数据、profile、run plan 和 gate policy 都先冻结再运行；报告中的每个 arm 都引用其 hash。
2. **结果、轨迹和运行指标分离。** 工具调用多不等于差，工具调用少也不等于好；任务和证据正确性先于效率。
3. **先硬校验，后主观判断。** 程序验证身份、URL、时间和字幕归属；judge 只判断已验证证据是否支持结论。
4. **比较 fail closed。** 缺样本、required trace/evidence/latency、profile 漂移、未仲裁分歧或无法配对时不猜测；usage/price 缺失只产生明确的非阻塞成本 `unknown`。
5. **隐私边界不扩大。** 原始参数只在请求内存中存在，答案/字幕只进入显式本地 review package，生产 diagnostics 和 SSE 保持现状。

## 2. 总体数据流

```text
Dataset source + fixture annotations
        -> validate/freeze -> frozen dataset + run plan
                                  |
                 +----------------+----------------+
                 |                                 |
        committed baseline checkout       committed candidate checkout
                 |                                 |
          run one immutable arm             run one immutable arm
                 |                                 |
          baseline artifact                 candidate artifact
                 +----------------+----------------+
                                  |
                   compatibility + paired comparison
                                  |
        deterministic correctness / trajectory / latency / cost
                                  |
                  verified evidence -> fixed rubric judge
                                  |
                     disagreement review/adjudication
                                  |
                  release gate: eligible/rejected/incomparable
```

运行器不负责 Git checkout。freeze/run-plan 命令生成相同的不可变输入，两个已提交 checkout 分别用 `--arm baseline|candidate` 和各自的 `solution_id` 生成 artifact；第三个 compare 命令只消费 artifact。这样避免 evaluator 在 dirty worktree 中隐式切换代码，也使两臂可由 CI 或本机独立执行。

## 3. 推荐模块边界

保持 `runner.py` 的现有兼容入口，按职责新增下列模块；实际实现可在不破坏边界的前提下合并小文件。

| 模块 | 责任 |
| --- | --- |
| `experiment_schema.py` | Frozen dataset、fixture identity、case/fault/repeat、execution profile、run plan、arm/comparison/gate artifact 的 strict schema。 |
| `dataset.py` | 数据集校验、canonical serialization、hash、只读 fixture/segment resolver、freeze/drift 检查。 |
| `trajectory.py` | 完整 model-tool lifecycle 的 eval-only 安全投影、decision DAG 对齐、参数合法性、progress epoch、冗余和 loop 分类。 |
| `answer_evidence.py` | Citation 的平台身份、URL、时间戳、segment/字幕 hash、excerpt 和 `[S...]` marker 硬校验。 |
| `usage.py` | primary/composer/recovery 的 provider-neutral usage 聚合和版本化价格表计算。 |
| `judge.py` | 固定无工具 rubric judge、strict 输出、盲化 arm 标签、分歧/抽检队列。 |
| `comparison.py` | 两臂兼容性检查、case/attempt 配对、按层聚合、delta/CI、unknown 传播。 |
| `release_gate.py` | 只读取预注册 policy 与 comparison，输出三态发布决策和逐条 reason。 |
| 既有 `quality.py` / `human_review.py` | 继续提供 Gold retrieval/citation scorer 与 accepted-only 人工聚合；不让 judge 覆盖硬错误。 |

推荐数据目录：

```text
evals/natural_language/
  datasets/agent-evaluator-v1.yaml
  profiles/<profile-id>.yaml
  rubrics/answer-support-v1.yaml
  prices/<price-table-version>.yaml
```

运行产物仍进入 gitignored results 目录，而不是仓库 source tree。

## 4. 冻结数据契约

### 4.1 顶层 manifest

`FrozenDatasetManifest` 至少包含：

```text
schema_version, dataset_id, revision, frozen_at, dataset_sha256
fixture_snapshot_id, fixtures[]
cases[]
repeat_policy
annotation_review_proof
```

`dataset_sha256` 不计算自身字段；其余字段经排序键、统一 Unicode/换行、稳定数值编码的 canonical JSON 后计算 SHA-256。YAML 只作为作者格式，hash 输入始终是 schema validate 后的 canonical model dump。

数据 hash 覆盖 query/turn、stratum/tags、Gold evidence、decision DAG、fault schedule、case-level threshold、key-case 标记、repeat policy 和 `annotation_review_proof`。`annotation_review_proof` 必须记录 `status=approved`、reviewer role/opaque reviewer reference、reviewed_at、review_revision、覆盖计数以及 coverage digest；它证明四层覆盖和 Gold/故障标注在冻结前经过人工复核，但不把私人联系方式写入发布报告。模型、工具、judge、价格表属于 execution profile，不混进 dataset hash；最终 `comparison_profile_hash` 同时绑定 dataset hash 和所有运行契约。

### 4.2 稳定 fixture 身份

每个 fixture 冻结：

```text
alias
platform + platform_id + canonical base URL
duration_sec + ready state
transcript content hash + raw format + text source
segments: stable key + seq + start/end + normalized text SHA-256
```

只读 resolver 在每个 arm 的外部调用前：

1. 以专属评测 tenant 和 `platform + platform_id` 找到唯一 ready `ContentItem`；
2. 比对 canonical base URL、duration、transcript/content hash 和来源；
3. 以 `seq` 定位 segment，核对时间和 normalized text hash；
4. 只在本次进程内建立 stable key 到 numeric item/segment ID 的映射。

任何缺失、重复或漂移使相关 run plan 在付费调用前失败。自增 ID 只允许出现在 arm 内部的临时关联，不写回冻结数据，也不用于跨 arm 比较。

### 4.3 Case schema

每个 case 至少包含：

```text
case_id, stratum, tags, turns
fixture_refs, gold_evidence, answer_contract
trajectory_contract, key_case, repeat_count
fault (tool_error only)
metric_applicability
```

- `simple_retrieval`：一组充分证据，DAG 通常只有 search 和可选定位/read。
- `multi_hop_retrieval`：至少两个有依赖的 decision/evidence group；DAG 节点声明 predecessors、允许工具集合、是否必需、产生何种 progress。
- `no_answer`：Gold item/segment/time 为空，必须有拒答边界和禁止事实。
- `tool_error`：必须有注入 seam、触发次数、稳定错误类别、允许 recovery 和 teardown 断言。

`metric_applicability` 是冻结的 allow-list，逐 case 声明 retrieval、citation、subtitle、tool-selection、parameter-validity、redundancy、loop、no-answer-false-positive、safety、judge 和 latency 等指标是否适用。指标结果统一使用 `{numerator, denominator, status, reason}`：`ok` 要求 denominator > 0；`not_applicable` 只可由 manifest 明确声明且 denominator=0；期望适用但没有观测、轨迹不完整、fixture skip 或数据缺失为 `unknown`，不得补 0。no-answer 的检索 false-positive 和 safety/latency 仍必须适用；citation/Recall 等无证据维度可显式 `not_applicable`。批量模型响应按每个模型工具请求计数，deterministic/no-tool 路由只有在 manifest 声明时才允许 tool 指标为 `not_applicable`。

旧 20 条人工 Gold 只能作为标注输入；freeze importer 必须拒绝仅含数据库数字 ID、缺 transcript/segment hash 或无法解析的样本。

### 4.4 故障注入

故障由 evaluator-only adapter 在 service/tool seam 注入，而不是依赖随机网络问题。计划以 `(case_id, attempt_index, tool, occurrence)` 定位，支持一次性/固定次数的 timeout、unavailable、schema reject 等固定类别。每个 attempt 使用独立 namespace；teardown 断言 fixture 状态、任务和 Citation 没有幽灵数据。注入功能在 production profile 中不可启用。

## 5. Execution Profile 与运行计划

### 5.1 Hash 分层

每个 arm artifact 保存三个概念：

- `arm_identity`：`arm`, `solution_id`, commit SHA；这是唯一允许不同的部分。
- `comparison_profile`：dataset/run-plan、模型、参数、prompt、budgets、工具、MCP、数据库/index、fixture/fault、judge、价格、顺序与 runtime；两臂必须完全相同。
- `readiness_proof`：非 production、专属 user、migration head、MCP readiness、工具发现、fixture resolver 和 teardown 结果。

secret、DSN、API key 和原始 endpoint 不进入 artifact。需要参与一致性判断的敏感配置由本地 allow-listed config key 或不可逆 safe config digest 表示，报告只显示匹配状态。

`solution_id`/candidate commit 始终只属于 `arm_identity`，不参与 `comparison_profile_hash`。`runtime_env_hash` 只从预注册 allow-list 计算（Python/runtime image、依赖锁、共享 MCP/tool server revision、tool-schema hash、migration/index/embedding、fixture/fault、judge/price/policy）；它明确排除 candidate application source commit、arm 名称、artifact 路径和答案内容。若 candidate 改变了工具 schema 或共享 server revision，则 tool-env hash 不同并判为 `incomparable`，而不是把 candidate 自身代码差异误报为环境漂移。

### 5.2 固定字段

profile 至少冻结：

```text
model provider/name/revision
temperature/top_p/max_tokens/thinking/seed or seed=unsupported
primary/composer/recovery prompt + model-settings hash
request/tool/output/time/retry/concurrency limits
shared MCP/tool-server revision + exact tool names + JSON-schema hash
Python/dependency lock + migration head
embedding model/dimensions + retrieval/index revision
fixture snapshot + fault schedule
judge model/prompt/rubric/schema
price table and included cost categories
warm/cold policy + attempt order/schedule
```

环境变量只允许提供 secret 和 profile 明确列出的连接凭据，不能覆盖已冻结语义字段。runner 在首次 provider 请求前输出 profile diff；存在差异时拒绝运行/比较。candidate application revision 只写入 arm identity；不得作为 runtime/tool-env hash 的输入。

### 5.3 Run plan 与重复

freeze 后生成 `run_plan.json`，列出固定的 `(case_id, task_attempt_id, attempt_index, pair_id, seed/status, order_slot)`。每层至少 2 个 key case × 3 次，其余 × 1。每次 task attempt 使用独立 conversation ID 和故障状态；repeat 不共享 conversation/history。若 provider 不支持 seed，保留相同 pair/order 但标记 stochastic。primary/composer/section/recovery 是同一 `task_attempt_id` 下的 `stage_attempt_id`，不能被误当成额外 task attempts。

为了降低时间漂移，运行文档推荐在较短窗口内按预注册的 counterbalanced arm order 执行；artifact 记录时间和 order slot。比较器不因运行顺序不同自动“校正”结果，只将违背 run plan 的样本判为不可比。

## 6. 轨迹采集与指标

### 6.1 采集边界

PydanticAI 2.15.0 的 `FunctionToolCallEvent.args_valid` 用于捕获 handler 前 schema 结果；MCP/Pydantic input 和 service boundary 再补业务范围/scope 结果。不能只收集成功进入 handler 的调用，否则参数合法率会被高估。

新增内部 `TrajectoryEvent` 建议包含：

```text
sequence_no, turn_index, model_step, call_index
tool_name, phase, lifecycle outcome, backend_started
parameter_validation, parameter_failure_code, validation_boundary
retry_kind, progress_epoch_before/after
is_redundant, duplicate_of_present, duplicate_reason
loop_reason, result_count/evidence_progress booleans
```

参数值、query、URL、item/segment ID、canonical tuple 和 digest 不在 event/report 中。server-owned canonicalization 仅在本次 attempt 的内存表中比较，attempt 结束即丢弃；持久事件只引用先前 sequence 是否存在。

现有 collector 按 `(call_index, tool_name)` 合并生命周期的 projection 继续用于兼容报告，但新指标必须消费不丢 multiplicity 和顺序的 event stream。

### 6.2 Tool selection

trajectory contract 是有向无环图：节点声明 `required|optional`、`allowed_tools`、predecessors、成功/空/错误分支和 progress 类型。scorer 按实际顺序进行一对一对齐：先过滤已满足 predecessors 且未匹配的节点，再按 `required` 优先、节点声明顺序、工具 allow-list 声明顺序做 deterministic tie-break；同一 provider batch 的多个请求按事件 sequence 排序，分别与不同可达节点匹配，不能把 batch 当成一个调用。一个实际请求只能匹配一个节点；等价工具集合在 schema 中显式列出，未列出的替代工具不算命中。多条可选路径必须给出稳定的 path priority，未满足前置的调用标为 contract violation。

- `required_tool_recall = matched required nodes / required nodes`；
- `allowed_tool_precision = requests valid for an active DAG node / observed model tool requests`；
- `forbidden_tool_rate = forbidden requests / observed model tool requests`；
- `trajectory_contract_pass` 要求必需节点、顺序、分支、retry 和预算全部满足。

一个 DAG 节点允许等价工具集合；可选合理调用不扣分。多跳是否成功由依赖和新证据决定，而不是总调用数。

若 manifest 将某个工具维度声明为不适用，则报告 `not_applicable`；若维度适用但没有任何完整模型观测、批量事件丢失或轨迹被截断，报告 `unknown` 并阻塞配对 gate，而不是把空集合当作 100% 或 0%。

### 6.3 Parameter validity

`parameter_validity_rate = passed model tool requests / all applicable model tool requests`。失败分类固定为 malformed/schema/type/range/scope/precondition 等 code；deterministic setup/MCP housekeeping 为 `not_applicable`。参数 schema 通过但 tenant/scope 或业务前置失败仍不算 fully valid，并另报各边界通过率。分母为 0 只有在 manifest 明确声明该 case 无模型工具请求时才是 `not_applicable`；否则是 `unknown` 并阻塞 gate。

### 6.4 Progress、redundancy 与 loop

`progress_epoch` 只在下列 server-observed 事件后递增：获得新的可信 evidence、成功推进必要状态、进入 contract 允许的 recovery 分支，或完成一个不同的必需 DAG 节点。模型自称“有进展”、重复空结果和 skipped call 不递增。

同一 epoch 内，canonical operation 已得到充分结果后再次请求且没有 contract 允许的 retry/recovery 时，该请求标为 redundant。报告：

```text
redundant_request_rate = redundant model requests / applicable model requests
redundant_backend_call_rate = redundant backend starts / non-skipped backend starts
```

V1 loop detector 使用 profile 中版本化的保守规则：同一 operation 在无进展状态下出现至少两次冗余重复，或长度 2–4 的 operation cycle 在无进展状态下完整重复，attempt 标记为 loop。跨 model step 重复的 skipped batch 可形成 loop；单个同批次 `same_model_step` skipped 不自动形成 loop。一次 contract 允许的 exact retry/fallback 排除；budget/usage limit 单独记为 `budget_limit`。

`loop_rate` 的规范分母是 run plan 中全部 required `task_attempt_id`，不是 detector 能“看见”的子集。只有每个 required attempt 的 lossless trajectory 都完整时才计算 `loop attempts / all required task attempts`；任何 trace 截断、缺失或 fixture skip 都将 loop 指标标为 `unknown` 并令比较 `incomparable`，绝不把不可检测的 attempt 当作无 loop。`detectable_attempts` 只作为诊断字段。报告同时列出 loop reason、budget-limit rate 和原始调用计数，避免把不同失败混在一起。

### 6.5 Safety event 与来源

安全事件来自 server-owned 的 tool policy/authorization、tenant/scope guard、confirmation boundary、citation sanitizer 和 evaluator 的 secret/content-leak sentinel；模型自报或 judge 推断不构成安全事件。事件只投影固定枚举（例如 `forbidden_tool`、`cross_tenant_scope`、`mutation_without_confirmation`、`citation_scope_escape`、`sensitive_field_leak`）和 `source_boundary`，不保存 payload。`safety_violation_rate = 含任一安全事件的 required task attempts / 全部 required task attempts`，分母必须 >0；不完整 trace 为 unknown/block，事件率必须为 0 才能过 gate。synthetic cases 覆盖每个枚举和无泄露正例，且继续使用现有安全/租户测试作为来源。

## 7. 最终回答的证据验证

以 MCP structured output 和只读 fixture resolver 为事实源。每个 turn 固定执行：

1. 校验 status/error/citation schema；`ok` grounded answer 必须有 Citation，`not_found/no_evidence` 必须无 Citation，tool error 保持 phase-accurate 状态。
2. 校验正整数 item/segment、segment 属于 resolver 映射的 fixture/item，且 item 是 ready/current tenant。
3. 由平台专用 parser 验证 scheme/host/path/video ID。YouTube 和 Bilibili 的 URL ID 必须等于冻结 `platform_id`。
4. 解析 deep-link timestamp，要求等于 `floor(start_sec)`；`start/end/duration` finite、非负、有序且在视频/segment 范围内，case tolerance 显式生效。
5. 对 Bilibili 先提取且校验唯一 `t` 参数，再移除 query 对 base URL canonicalize；不能把合法 `?t=` deep-link 直接传给当前普通 admission normalizer。
6. 从可信 segment 重新计算 normalized text hash，验证冻结 hash 和 Citation excerpt；标题仅作展示信息，不能证明视频身份。
7. 校验最终可见 `[S<segment_id>]` marker 集合与结构化 Citation union 完全一致，无未知、重复越权或模型自写来源链接。

checker 输出固定字段/原因码，例如 `video_identity_valid`、`url_valid`、`timestamp_valid`、`segment_binding_valid`、`transcript_integrity_valid`、`marker_union_valid`。URL、字幕正文和 numeric ID 不进入 sanitized comparison。

只有全部必要硬校验通过，才生成 bounded evidence package 供 judge 使用。语义上“字幕是否支持答案结论”不由 hash 或 Citation presence 推断。

## 8. LLM-as-Judge 与人工抽检

judge 对 baseline/candidate 标签盲化，逐个 answer 独立评分，不做偏好式二选一。固定输入为：question、answer、stratum、Gold reference points/answer boundary，以及通过程序验证且长度受限的字幕 segment/时间。judge 无工具、无网络、无额外历史。

rubric 版本化并使用 strict structured output：

```text
answered_question
claims_supported_by_subtitles
required_evidence_covered
no_unsupported_claims
truthful_no_answer_or_recovery
clarity_and_guidance
overall: pass | fail | unknown
confidence + allow-listed reason codes
```

每个 rubric 维度的值为 `pass|fail|unknown|not_applicable`；`not_applicable` 只有 manifest 对该 stratum 声明时可用。overall 聚合固定为：任一适用维度 `fail` → `fail`；无 fail 但有 `unknown` → `unknown`；所有适用维度均 pass → `pass`；没有适用维度 → `unknown`。V1 `reason_codes` 固定为 `unsupported_claim`、`wrong_video`、`wrong_timestamp`、`missing_evidence_group`、`non_answer`、`fabricated_source`、`misrepresented_recovery`、`unclear_evidence`、`judge_parse_error`、`missing_input`、`other_allowlisted`；模型不得输出自由文本 reason。`confidence` 是枚举 `high|medium|low`；`low` 或缺失/非法 confidence 必须进入人工队列。

模型/参数、prompt、rubric、JSON schema 和 seed 支持参与 profile hash。invalid JSON、timeout、provider error 或证据不足均为 `unknown`，不能重试到选择性通过；如配置 retry，次数和全部成本必须固定并计入。judge unknown 只有在所需 judge/review 样本完成后才能从 gate 中移除；否则是 blocking unknown。

人工队列包含：

- 全部 hard-check/judge、baseline/candidate 或 judge/historical-human 分歧；
- 全部 `unknown`、低置信、边界分、judge 异常和 tool-error recovery；
- 即使双方一致，也固定抽取每层至少 1 个且不少于 `ceil(10% × 该层唯一 case 数)` 的 control（取较大值，run plan 前预注册）；基数是去重后的 case，不是 attempt/judge row，key case 的 repeats 不增加 control 权重。

人工 reviewer 看不到 arm 名称，使用同 rubric 记录 `accepted/pending/disputed` 和最终 adjudication。发布聚合只消费完成的 accepted adjudication；必要队列中存在 pending/disputed 时 gate 为 `incomparable`。抽样 seed、去重后的 case 清单、每层 control 数和 reviewer/adjudication revision 一并写入 review manifest/hash。

## 9. Usage、延迟与成本

### 9.1 Usage

在一个 `task_attempt_id` 内，primary、answer plan、每个 section、每个 recovery 都各自使用唯一 `stage_attempt_id` 读取 `RunUsage`，标准化为 input/output/reasoning/cache/audio 等 provider-neutral counters。聚合 task attempt 时每个 stage 只计一次；重试产生新的 stage_attempt_id，但不会把同一次 task attempt 的分母重复增加。保留 stage/attempt 和 known/unknown，不转储 provider raw usage。

版本化 price table 明确模型、token 类型、单位和是否包含 embedding/外部工具。只有明确声明免费的本地项可计 0；缺模型价、缺 token 类别或 provider 未返回 usage 时：

- 总成功成本为 `unknown`；
- 可选显示 `known_cost_lower_bound`，但不得作为比较结论；
- `unknown_usage_count` 必须可见。

公式：

```text
cost_per_successful_task =
  attributable cost of every task attempt, including failed task attempts
  and nested stage/recovery retries exactly once
  / successful task attempts
```

零成功时为 undefined。另报 cost/all-task-attempt、model/tool call counts，均不进入正确性判断。缺 usage/price 仅使成本诊断为 `unknown`，不因为成本 unknown 单独阻塞 V1 gate；它必须显示 `unknown_usage_count`，也不能伪造为 0。

### 9.2 Latency

attempt latency 从第一用户 turn dispatch 到最终 terminal result，排除全局 preflight/fixture freeze，包含该 task attempt 的模型、工具、composer/recovery。报告 turn/task-attempt、successful/all/common-success `n`；gate 使用 deterministic-success task-attempt 的 end-to-end p95；无成功样本、non-finite、缺 required attempt 或样本不完整时 p95 为 blocking unknown。另报 common-success paired latency，帮助解释 success mix 变化。

`deterministic_success` 是成功成本和 successful p95 的唯一分母：所有 required turn 的 terminal status/error contract、trajectory contract、硬视频/字幕/marker 校验（或冻结的 no-answer/tool-error contract）、状态迁移和安全零违规全部通过。LLM judge 不参与这个硬成功谓词，judge unknown 不会把失败 attempt 排除；它通过独立的主观 review gate 控制发布完整性。

## 10. Pairing、统计与发布门禁

### 10.1 比较状态

compare 首先验证 schema、dataset/profile/run-plan/tool-env hash、pair key、repeat 和 required denominator。结果状态：

- `eligible`：所有硬门均满足；
- `rejected`：数据完整可比，但至少一个门明确失败；
- `incomparable`：配置/样本/required trajectory/evidence/latency/judge/adjudication 不完整，不能作发布判断。

每项输出 baseline/candidate numerator、denominator、delta、direction、threshold、CI/status 和 reason code。overall 不能掩盖某个 stratum 失败。

缺失语义固定如下：manifest 声明的 `not_applicable` 不参与该维度 gate；期望适用但为 0 分母、trace/fixture/evidence/latency 缺失、judge required unknown 或未完成人工队列为 blocking `unknown`/`incomparable`；usage/price 缺失仅是非阻塞成本诊断 unknown。skip 不进成功分母，也不能当成 N/A。

### 10.2 正确性集合

逐层和总体检查：task success、Recall/Citation/视频/URL/时间戳/字幕支持、no-answer false positive、tool-error recovery、required/allowed/forbidden tool、parameter validity 和 safety。点估计先对每个 case 的 repeats 取均值，再在 stratum 内对 case 做等权 macro；overall 对全部 case 做等权 macro。另报 attempt micro 供诊断，但 gate 使用 case macro。率指标的 paired CI 以 case 为 cluster 重采样，不能把同一 key case 的三次 repeat 当三个完全独立样本。关键硬 case 使用 paired case-level zero-regression 检查。

冗余率、调用次数和成本展示但不补偿正确性；loop 是单独必须改善的门。paired 95% CI 对每层的 case-level candidate−baseline delta 做有放回 bootstrap，固定 `B=10_000`、seed `20260904`，按 case 重采样而不拆散同一 case 的 repeats，使用 percentile 2.5%/97.5% 边界；overall 在全部 case 上同样处理。低值更好的 false-positive/violation 先取反向 delta 再套同一规则。统计算法、seed、聚合权重和 policy hash 必须在运行前冻结。

### 10.3 V1 policy 草案（待用户确认）

推荐冻结为：

1. 每层及总体 correctness 点估计不得变差；方向相反的 false-positive/violation 不得升高。
2. 随机性率指标的 paired 95% CI 下界允许最多 `-2 percentage points` 的非劣化 margin，同时关键硬 case 为零回归。
3. candidate loop rate 必须低于 baseline；baseline 已为 0 时，candidate 保持 0 视为饱和通过，任何大于 0 都失败。
4. candidate deterministic-success task-attempt p95 至少相对改善 5%；`(baseline - candidate) / baseline >= 0.05`。successful 样本数低于预注册最小值 `n=10` 时标为 `low_n`，p95 gate 为 blocking unknown；不以少量成功样本声称改善。
5. safety violation 必须两臂均为 0；除成本/价格外的 required unknown、必要人工未完成、profile drift 或不完整 pair 均不能发布。

这组值在看到结果前进入 `gate_policy_version` 和 comparison profile hash。用户若选择更严格/宽松阈值，只修改 policy 并重新冻结/运行，不能复用旧 arm 来事后调参。

## 11. Artifact 与隐私

每个 experiment 目录：

```text
manifest.json
profile.json
run-plan.json
baseline/{arm.json, attempts.jsonl, summary.json}
candidate/{arm.json, attempts.jsonl, summary.json}
comparison.json
gate.json
report.md
review/                 # explicit opt-in, ignored, may contain answer/evidence
```

冻结 manifest/source 是受控的评测输入，不等同于可分享的 sanitized report：它可包含经批准的公开 fixture platform ID、query、Gold answer boundary、transcript/segment hash 和 annotation review proof，但必须只提交不含真实用户/秘密的 benchmark 数据。若 query/Gold 含私有内容，则 manifest 与 source 一并放入 gitignored review-only 存储，并在 arm artifact 中只引用其 hash。`profile.json`、`comparison.json`、`gate.json` 和 `report.md` 等 sanitized runtime artifacts 只使用 fixture alias/hash、case ID 和固定 reason code，不包含 raw URL、问题/回答、字幕、tool args/results、tenant/private external identity、secret、provider payload/error text 或 argument fingerprint。

所有 JSON schema 设 `extra=forbid`。report redaction 使用 allow-list serializer 后再做 sentinel scan，而不是依赖 key-name 黑名单；review export 是唯一允许正文的路径，必须显式 flag、gitignored 且在 CLI 中提示本地敏感内容。

## 12. 兼容、上线与回滚

- 既有 `--validate-catalog`、`--preflight`、`--case`、`--all`、Gold 和 human-review 路径保持兼容；新入口的规范命令统一为 `--validate-dataset`、`--freeze-check`、`--run-arm`、`--compare`。`freeze-dataset`/`build-run-plan` 只是内部库操作，不作为另一套 CLI 契约。
- 新 trace/usage 信号通过 evaluator-only callback/adapter 接入，不扩展 public response 和浏览器 execution timeline。生产未启用 evaluator 时应无持久数据和语义变化。
- 先交付纯离线 schema/scorer，再接 trace/证据，再接真实 arm，最后启用 judge/gate。每阶段均可退回现有单臂 evaluator。
- dataset、profile、artifact 和 rubric 均版本化；新 reader 必须拒绝未知 major version，旧 artifact 只读，不做静默迁移。
- fault injection 和答案正文导出默认关闭；任一 teardown、隐私 sentinel 或 compatibility 回归失败都停止真实运行并回滚到现有 evaluator。

## 13. 主要风险与缓解

| 风险 | 缓解 |
| --- | --- |
| 小样本与 provider 随机性让 CI 很宽 | 关键 case repeat 3、按 case cluster 统计、显示 low-n；门禁允许不可比，不用平均值强行下结论。 |
| 字幕重新入库/切段导致 Gold 漂移 | 冻结 transcript/segment hash，外部调用前 resolver fail closed，只新增 dataset revision。 |
| 参数观测泄露 query/URL/ID | 只在 request 内存 canonicalize，持久化 enum/bool/count，不保存 digest。 |
| 多跳被误判冗余/loop | 由 decision DAG 和 progress epoch 区分新证据、合法 retry 与无进展 cycle；用 synthetic trace 回归。 |
| baseline/candidate 环境并不相同 | 两个 committed checkout 消费同 run plan，profile/tool/schema/fixture hash 严格比较。 |
| p95 被失败或 success mix 操纵 | 同时报 successful/all/common-success，gate 固定 successful-attempt 定义并要求 correctness 先通过。 |
| judge 偏差或自洽偏差 | 固定 rubric、盲化 arm、无工具、strict output、分歧全量人工 + agreement control。 |
| 成本缺失被误记为便宜 | usage/price 缺失传播为 unknown，显示 unknown count，成本不抵消质量。 |
| 故障注入污染共享状态 | 专属非 production tenant、attempt namespace、有限触发、强制 teardown 和无幽灵数据断言。 |
