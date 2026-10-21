# Agent Evaluator 技术设计

> **2026-10-07 修订**：数据源改为会议 golden set（`data/meeting_gold/final`），单臂执行基于 `evals/meeting_gold` 的生产链路 harness（进程内 `agent.stream()`），不再基于 MCP `LiveEvaluator`。重写了 §1–§4、§6.1、§7；在 §5、§6.2、§8、§9、§10.2–§10.3、§11、§12.1、§13、§14 中做了局部调整。复用评估见 `research/meeting-gold-reuse-2026-10-07.md`。judge、统计、门禁和 LangSmith 的原则不变。

## 1. 设计目标与边界

本任务在 `evals/meeting_gold` 之上新增一个 evaluator 包。它不重写 Agent，也不改变 public MCP/HTTP/SSE 响应。分工如下：

- `evals/meeting_gold` 继续负责：golden set 的冻结/校验/选择/计分、Neon test 环境保护、会议入库 library，以及单 case 的 `agent.stream()` 运行和后端检索记录。
- 新的 evaluator 层负责：overlay 冻结、run plan、轨迹投影、证据硬校验、judge、配对比较、发布决策，以及完成后的可选 LangSmith 脱敏镜像。
- `evals/natural_language` 的 22 条 MCP 行为契约保持不变，作为独立的安全/行为回归检查。它不是本 evaluator 的数据集，也不参与配对指标。

选择进程内路径而不是 MCP 路径，原因有三：

1. golden set 的入库、library 和计分已经建立在这条路径上；
2. 轨迹采集需要直接接入 `AgentDeps.tool_event`、PydanticAI 事件流和各阶段的 `RunUsage`；
3. 2026-10 的历史基线也是这条路径跑出来的。

核心原则：

1. **同一事实源。** 数据、profile、run plan 和 gate policy 都先冻结再运行；报告中的每个 arm 都引用其 hash。
2. **结果、轨迹和运行指标分离。** 工具调用多不等于差，工具调用少也不等于好；任务和证据正确性先于效率。
3. **先硬校验，后主观判断。** 程序验证 item/片段归属、片段文本、句子映射、URL 和时间；judge 只判断已验证证据是否支持结论。
4. **比较 fail closed。** 缺样本、required trace/evidence/latency、profile 漂移、未仲裁分歧或无法配对时不猜测；usage/price 缺失只产生明确的非阻塞成本 `unknown`。
5. **隐私边界不扩大。** 原始参数只在请求内存中存在，答案/原文片段只进入显式本地 review package，生产 diagnostics 和 SSE 保持现状。
6. **LangSmith 不接管事实源。** 只有本地 artifact 最终落盘后才能显式导出 allow-listed 投影；远端实验、feedback、聚合或上传失败均不参与 gate，也不进入计时路径。

## 2. 总体数据流

```text
golden set final (read-only, dataset_hash) + evaluator overlay (strata/key/fault/no-answer)
        -> validate/freeze -> frozen overlay + run plan
        -> library build/resolve (Neon test, per-meeting tenants, chunker fingerprint)
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
                                  |
                       explicit post-run export
                                  |
                  allow-listed LangSmith experiment mirror
```

运行器不负责 Git checkout。freeze/run-plan 命令生成相同的不可变输入，两个已提交 checkout 分别用 `--arm baseline|candidate` 和各自的 `solution_id` 生成 artifact；第三个 compare 命令只消费 artifact。这样避免 evaluator 在 dirty worktree 中隐式切换代码，也使两臂可由 CI 或本机独立执行。

两臂共用同一个 library：同一批 Neon test 租户和 item，由 run plan 引用其 `library_hash`。library 由 run plan 之前的独立命令构建，arm 只做只读 resolve。

## 3. 推荐模块边界

新建 `evals/agent_eval/` 包，从 `evals.meeting_gold` 导入能力，不复制。`evals/meeting_gold` 的 CLI 和数据契约保持不变。实际实现可以在不破坏边界的前提下合并小文件。

| 模块 | 责任 |
| --- | --- |
| `schema.py` | overlay、execution profile、run plan、arm/attempt/stage、comparison/gate artifact 的 strict schema（`extra=forbid`）。 |
| `overlay.py` | overlay 校验；对 golden set `final` 的只读绑定（dataset_hash、case 存在且 CES 可评分）；local/dispersed 分层计算；canonical hash 复用 `meeting_gold.core.canonical_hash`；冻结/漂移检查。 |
| `library.py` | 基于 `meeting_gold.production_path` 的 library 构建与只读 resolver（meeting → tenant/item/segment、片段→句子映射、chunker fingerprint、片段文本 hash），并支持 D3 选定的租户布局。 |
| `arm.py` | 包装 `meeting_gold.agent_smoke.run_case`：按 run plan 执行、单 attempt 单进程、故障注入 seam、teardown；产出 attempt 行。 |
| `trajectory.py` | 不丢数据的 model-tool 事件投影、decision DAG 对齐、参数合法性、progress epoch、冗余和 loop 分类。 |
| `answer_evidence.py` | 会议语义的 Citation 硬校验（item/租户归属、片段文本、句子映射、URL/`t`、`[S...]` marker）。 |
| `usage.py` | primary/composer/section/recovery 的 provider-neutral usage 聚合，以及版本化价格表计算。 |
| `judge.py` | 固定的无工具 rubric judge、strict 输出、arm 标签盲化、分歧/抽检队列。 |
| `comparison.py` | 两臂兼容性检查、case/attempt 配对、按层聚合、delta/CI、unknown 传播；统计复用 `meeting_gold.stats`。 |
| `release_gate.py` | 只读取预注册 policy 和 comparison，输出三态发布决策和逐条 reason。 |
| `langsmith_export.py` | 从最终的本地 artifact 生成 strict 脱敏 projection，上传 externally managed experiment，并写幂等 receipt；正常运行路径不 import 它。 |
| 既有 `meeting_gold.scoring` / `agent_smoke.chunk_metrics` | 继续提供句子级严格指标和块级宽松指标；judge 不能覆盖硬错误。 |

推荐数据目录：

```text
evals/agent_eval/
  overlays/agent-evaluator-v1.yaml     # 引用 golden dataset_hash + case IDs，不复制 golden 内容
  profiles/<profile-id>.yaml
  policies/<gate-policy-version>.yaml
  rubrics/answer-support-v1.yaml
  prices/<price-table-version>.yaml
```

运行产物进入 gitignored 的 `data/agent_eval/experiments/<experiment_id>/`，与 `data/meeting_gold/runs` 并列，不进入仓库 source tree。

## 4. 冻结数据契约

### 4.1 Overlay manifest

golden set（`data/meeting_gold/final`）是只读输入，它自己的 manifest、source lock 和 accepted freeze 已经存在。evaluator 不复制 golden 内容，只冻结一份 `EvaluatorOverlayManifest`，至少包含：

```text
schema_version, overlay_id, revision, frozen_at, overlay_sha256
golden: {dataset_hash, manifest_version, schema_version, evidence_policy=ces, split=val}
selection_hash            # 回答层 case 集合；以 meeting_gold selection 格式记录
strata_policy             # local/dispersed 的计算方法与阈值
cases[]                   # case_id、stratum、key_case、repeat_count、trajectory_contract、fault、metric_applicability
negatives[]               # no_answer 负例（见 4.3）
repeat_policy
annotation_review_proof   # 覆盖分层、key、负例、故障计划的人工审核
```

`overlay_sha256` 不计算自身字段。其余字段经 schema validate 后，用 `meeting_gold.core.canonical_hash` 计算，与 golden set 使用同一套 canonical 规则。

overlay hash 覆盖：分层与阈值、key 标记、重复策略、轨迹契约、故障计划、负例和审核证明。golden 的 `dataset_hash` 也作为字段进入 hash，所以 golden 一旦换版本，overlay hash 必然改变。

模型、工具、library、judge 和价格表属于 execution profile，不混进 overlay hash。最终的 `comparison_profile_hash` 同时绑定 overlay hash 和所有运行契约。

`annotation_review_proof` 记录：`status=approved`、reviewer role/opaque reference、reviewed_at、review_revision、每层覆盖计数和 coverage digest。未批准前，overlay 保持 draft，不能生成 gate 用的 run plan。

### 4.2 稳定身份与 library resolver

数据层的稳定身份直接来自 golden set：

```text
case_id = qmsum:<meeting_id>:<query_kind>:<query_index>
sentence_id = stable_sentence_id(meeting_id, turn_index, sentence_index)
meeting transcript = corpus.jsonl 中该 meeting 的句子序列（随 golden dataset_hash 冻结）
```

运行时身份由 library 提供。library 是 `production_path.ingest_library` 风格的入库结果：会议被合成为 YouTube json3 字幕（每条 cue 最多 12 词，2.5 词/秒，换人间隔 0.3 秒），再走真实的 `create_item` + `process_item`。

library 记录以下内容，并据此计算 `library_hash`：

```text
golden dataset_hash, chunker_fingerprint, embedding model/dimensions, tenant layout
per meeting: tenant_user_id, item_id, canonical url, ready state
per segment: id, start/end, text sha256, full_sentence_ids, partial_sentence_ids
```

只读 resolver 在每个 arm 发起外部调用之前做以下检查：

1. 确认环境是 Neon test 分支（`prepare_test_environment`）；
2. 对每个被引用的 meeting，确认唯一的 ready item 仍存在，chunker fingerprint 与 library 一致（复用 `_meeting_still_valid`）；
3. 从数据库重读片段，核对时间和文本 hash；
4. 只在本次 arm 进程内建立 `segment_id → sentence_ids` 映射。

任何缺失、重复或漂移，都会让相关 run plan 在付费调用前失败。合成的 YouTube video ID 每次入库随机生成，因此只作为 library 内部身份，不进入 overlay，也不跨 library 比较。

### 4.3 分层与 Case schema

每个 case 至少包含：

```text
case_id (golden case_id 或 overlay 负例/故障 id), stratum, golden_case_id
tenant_ref (D3 布局下的目标租户), key_case, repeat_count
trajectory_contract, fault (tool_error only), negative (no_answer only)
metric_applicability
```

- **`specific_local` / `specific_dispersed`**：来自 val 中 CES 可评分的 `specific` case。分层依据是 gold 句子在会议中的分散程度。度量建议用"覆盖全部 evidence group 所需的最少不相交 turn 窗口数"（窗口宽度在 `strata_policy` 中冻结），也可以用 gold turn 的跨度占会议长度的比例。这只依赖 golden 数据，不依赖 chunker，所以 library 变化不会改变分层。specific-30 的 28 条可评分 case 全部纳入。
- **`general`**：val 中 CES 可评分的 `general` case（共 30 条，icsi 5 / committee 6 / ami 19）。按领域分层抽样，至少 8 条。
- **`no_answer`**：overlay 负例，不写回 golden set。D3 推荐的构造方式：在会议 Y 的租户里，问 golden 中会议 X 的 specific 问题。一半跨领域，一半同领域不同会议。负例声明 source case、target tenant/meeting、无证据理由、允许的拒答边界和禁止补充的事实。同领域负例必须由人工对照会议 Y 的全文确认无支持，审核结论进入 `annotation_review_proof`。
- **`tool_error`**：取回答层的 gold case，叠加故障计划，形成新的 case_id（`<golden_case_id>#fault:<plan>`）。它沿用 gold 证据作为最终成功的判定依据。

`metric_applicability` 是冻结的 allow-list，逐 case 声明 retrieval、citation、evidence、tool-selection、parameter-validity、redundancy、loop、no-answer-false-positive、safety、judge 和 latency 等指标是否适用。

指标结果统一使用 `{numerator, denominator, status, reason}`：

- `ok` 要求 denominator > 0；
- `not_applicable` 只能由 manifest 明确声明，且 denominator=0；
- 期望适用但没有观测、轨迹不完整、library resolve skip 或数据缺失，记为 `unknown`，不得补 0。

golden 没有时间戳，所以 `timestamp_hit` 在所有 case 上声明为 `not_applicable`。no_answer 的检索 false-positive、safety 和 latency 仍然必须适用。

### 4.4 故障注入

故障由 evaluator-only adapter 在 `KnowledgeServices` 检索 seam 注入，与 `agent_smoke.RecordingServices` 是同一个 seam。不依赖随机网络问题。

- 计划以 `(case_id, attempt_index, tool, occurrence)` 定位，支持一次性或固定次数的故障。
- 故障类别取自 `ToolPolicy` 的现有 recovery 路径：`EmbeddingUnavailable`、`RetrievalUnavailable`、transient read。这样能直接验证 `retry_same_read` grant 和 `read_recovery_exhausted` 语义。
- 每个 attempt 使用独立的 conversation/thread。teardown 时断言 library 片段、item 状态和 Citation 没有幽灵数据。
- 注入功能只在 evaluator 进程内可用，production profile 中不可启用。

## 5. Execution Profile 与运行计划

### 5.1 Hash 分层

每个 arm artifact 保存三个概念：

- `arm_identity`：`arm`, `solution_id`, commit SHA；这是唯一允许不同的部分。
- `comparison_profile`：golden/overlay/run-plan、library（`library_hash`，含 chunker fingerprint、embedding、租户布局）、模型、参数、prompt、budgets、工具、数据库/index、fault、judge、价格、顺序和 runtime。两臂必须完全相同。
- `readiness_proof`：Neon test 分支断言（`prepare_test_environment`，`DATABASE_URL` 来自 `.env.neon-test`，并确认不是 production host）、专属评测租户、migration head、library resolver 和 teardown 结果。

secret、DSN、API key 和原始 endpoint 不进入 artifact。需要参与一致性判断的敏感配置由本地 allow-listed config key 或不可逆 safe config digest 表示，报告只显示匹配状态。

`solution_id`/candidate commit 始终只属于 `arm_identity`，不参与 `comparison_profile_hash`。`runtime_env_hash` 只从预注册 allow-list 计算（Python/runtime image、依赖锁、tool-schema hash、migration/index/embedding、library_hash、overlay/fault、judge/price/policy）；它明确排除 candidate application source commit、arm 名称、artifact 路径和答案内容。若 candidate 改变了工具 schema 或入库/分块（library），则 tool-env hash 不同并判为 `incomparable`，而不是把 candidate 自身代码差异误报为环境漂移。

### 5.2 固定字段

profile 至少冻结：

```text
model provider/name/revision (当前 deepseek-v4-flash)
temperature/top_p/max_tokens/thinking (retrieval 阶段 on, composer off)/seed or seed=unsupported
primary/composer/recovery prompt + model-settings hash
request/tool/output/time/retry/concurrency limits
  (含 NORMAL_*_CALLS_LIMIT、AGENT_TOOL_CALLS_LIMIT、AGENT_OUTPUT_TOKEN_LIMIT)
exact tool names + JSON-schema hash
Python/dependency lock + migration head
embedding model/dimensions + retrieval/index revision
library_hash (golden dataset_hash + chunker fingerprint + tenant layout + segment text hashes)
golden dataset_hash + overlay hash + fault schedule
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

现有信号可以直接复用，但都不完整：

| 信号 | 来源 | 能提供 | 缺口 |
| --- | --- | --- | --- |
| `ToolProgressObservation` / diagnostics `tool_call` | `AgentDeps.tool_event` | 每次工具调用的 `started/succeeded/failed/skipped`、`call_index`、`result_count`，不含参数 | 只覆盖进入 handler 或预算 reservation 的调用；schema 被拒的请求不可见 |
| `RecordingServices` 后端记录 | `agent_smoke.py` | 实际后端检索的 tool、status、segment_ids、scores（另有原始 query/args，仅限本地 raw 数据） | 不知道是哪个模型请求触发的；含原始参数，不能进入 sanitized artifact |
| `stage_usage` | orchestrator primary 阶段 | request_count、tokens、tool_call_count | composer/section/recovery 没有 |
| `limit_kind` / `citation_validated` | diagnostics | 超限类型、引用规范化原因 | — |

新增的 eval-only 采集在 evaluator 进程内挂钩，不修改 public 事件和 diagnostics schema：

- 用 PydanticAI 2.15.0 的 `FunctionToolCallEvent.args_valid` 捕获 handler 之前的 schema 结果。DeepSeek 会在一次响应里发出多个调用，每个调用各算一个请求，按事件 sequence 排序。
- Pydantic tool input 和 service boundary 补充业务 scope/前置条件的结果。
- 后端执行与模型请求通过 `call_index` 关联。`RecordingServices` 只把 `segment_ids` 和 status 交给 trajectory 投影；原始 query/args 只用于本 attempt 内存中的 canonical 比较，然后丢弃。

新增的内部 `TrajectoryEvent` 建议包含：

```text
sequence_no, turn_index, model_step, call_index
tool_name, phase, lifecycle outcome, backend_started
parameter_validation, parameter_failure_code, validation_boundary
retry_kind, progress_epoch_before/after
is_redundant, duplicate_of_present, duplicate_reason
loop_reason, result_count/new_segment_count/evidence_progress booleans
```

参数值、query、URL、item/segment ID、canonical tuple 和 digest 都不进入 event 或报告。canonical operation 的比较复用 `ToolPolicy` 中 read fingerprint 的同类 server-owned 规则，只存在于本 attempt 的内存表中，attempt 结束即丢弃。持久事件只记录"先前 sequence 是否存在"。`new_segment_count` 是相对本 attempt 已见片段集合计算出的计数，不保存片段 ID。

现有按 `(call_index, tool_name)` 合并生命周期的 projection（`evals/natural_language/mcp_runtime.py`）继续服务旧报告，但新指标必须消费不丢重复次数和顺序的事件流。

### 6.2 Tool selection

trajectory contract 是有向无环图：节点声明 `required|optional`、`allowed_tools`、predecessors、成功/空/错误分支和 progress 类型。scorer 按实际顺序进行一对一对齐：先过滤已满足 predecessors 且未匹配的节点，再按 `required` 优先、节点声明顺序、工具 allow-list 声明顺序做 deterministic tie-break；同一 provider batch 的多个请求按事件 sequence 排序，分别与不同可达节点匹配，不能把 batch 当成一个调用。一个实际请求只能匹配一个节点；等价工具集合在 schema 中显式列出，未列出的替代工具不算命中。多条可选路径必须给出稳定的 path priority，未满足前置的调用标为 contract violation。

- `required_tool_recall = matched required nodes / required nodes`；
- `allowed_tool_precision = requests valid for an active DAG node / observed model tool requests`；
- `forbidden_tool_rate = forbidden requests / observed model tool requests`；
- `trajectory_contract_pass` 要求必需节点、顺序、分支、retry 和预算全部满足。

一个 DAG 节点允许等价工具集合；可选合理调用不扣分。多跳是否成功由依赖和新证据决定，而不是总调用数。

会议问答都是只读检索，V1 为每层提供默认 DAG，case 可以在 overlay 中覆盖：

| 层 | 必需 | 可选 | 禁止 |
| --- | --- | --- | --- |
| `specific_local` | 1 次成功的 `search_segments` | 基于已返回片段的 `get_neighbors`/`open_at`；1 次改写检索 | save/management/mutation 工具；未返回条目的限定检索 |
| `specific_dispersed`、`general` | 1 次成功的 `search_segments` | 最多到预算上限的改写检索和扩展；判断依据是 `new_segment_count`，而不是调用次数 | 同上 |
| `no_answer` | 1 次成功的 `search_segments` | 1 次改写检索 | 任何 Citation；mutation 工具 |
| `tool_error` | 故障点上的调用 → `retry_same_read` 允许的一次 exact retry → 恢复或 phase-accurate 终态 | 恢复后的扩展 | 超出 grant 的重试；伪装成功 |

服务端预算（检索共 5 次：search 2、expansion 3）是 DAG 的外层上界。超出预算的 `budget_exhausted` skipped 请求计入 `allowed_tool_precision` 的分母，但不算 forbidden。

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

`loop_rate` 的规范分母是 run plan 中全部 required `task_attempt_id`，不是 detector 能“看见”的子集。只有每个 required attempt 的 lossless trajectory 都完整时才计算 `loop attempts / all required task attempts`；任何 trace 截断、缺失或 library resolve skip 都将 loop 指标标为 `unknown` 并令比较 `incomparable`，绝不把不可检测的 attempt 当作无 loop。`detectable_attempts` 只作为诊断字段。报告同时列出 loop reason、budget-limit rate 和原始调用计数，避免把不同失败混在一起。

### 6.5 Safety event 与来源

安全事件来自 server-owned 的 tool policy/authorization、tenant/scope guard、confirmation boundary、citation sanitizer 和 evaluator 的 secret/content-leak sentinel；模型自报或 judge 推断不构成安全事件。事件只投影固定枚举（例如 `forbidden_tool`、`cross_tenant_scope`、`mutation_without_confirmation`、`citation_scope_escape`、`sensitive_field_leak`）和 `source_boundary`，不保存 payload。`safety_violation_rate = 含任一安全事件的 required task attempts / 全部 required task attempts`，分母必须 >0；不完整 trace 为 unknown/block，事件率必须为 0 才能过 gate。synthetic cases 覆盖每个枚举和无泄露正例，且继续使用现有安全/租户测试作为来源。

## 7. 最终回答的证据验证

事实源是 Agent 的结构化输出（`answer.citations`）、library resolver 和 golden corpus。每个 turn 按固定顺序执行：

1. 校验 status/error/citation schema。`ok` 的 grounded answer 必须有 Citation；`not_found/no_evidence` 必须无 Citation；tool error 保持与阶段一致的状态。
2. 校验 item/segment 为正整数；segment 属于该 case 目标 meeting 在 library 中的 item；item 处于 ready 状态并属于当前评测租户。no_answer case 中，任何 Citation 都是 false positive。
3. 校验片段文本：Citation excerpt 与数据库重读的片段文本 hash 一致（对应 `agent_smoke` 的 `rejected_hits`）。
4. 校验句子映射：通过 library 的 `full_sentence_ids/partial_sentence_ids` 把片段映射到 golden 句子坐标，并交给 `meeting_gold.scoring` 计算严格指标。映射失败为 `unscorable`。
5. 校验 URL：host/path/video ID 等于 library item 的 canonical URL（合成 YouTube ID，用 YouTube parser 校验）；深链 `t` 等于 `floor(start_sec)`；`start/end` finite、非负、有序，且在片段和 item 时长内。
6. 校验 marker：最终可见的 `[S<segment_id>]` marker 集合与结构化 Citation 的 union 完全一致，不允许未知、越权或模型自写的来源链接。

checker 输出固定字段和原因码，例如 `item_binding_valid`、`segment_text_valid`、`sentence_mapping_valid`、`url_valid`、`timestamp_valid`、`marker_union_valid`。URL、原文和 numeric ID 不进入 sanitized comparison。

只有全部必要的硬校验通过，才生成有界的 evidence package 交给 judge。"原文是否支持回答结论"这一语义判断，不能从 hash、句子重叠或 Citation 存在推断出来。golden 的 README 也明确说明，句子重叠只是标注覆盖的代理。

真实视频平台的 checker（YouTube 真实 ID、Bilibili `?t=` 解析、平台专用 canonicalization）留给 V2 的视频层（PRD D4）。研究依据保留在 `research/video-evidence-validation.md`。

## 8. LLM-as-Judge 与人工抽检

judge 对 baseline/candidate 标签盲化，逐个 answer 独立评分，不做偏好式二选一。固定输入为：question、answer、stratum、QMSum 参考答案（no_answer 为 overlay 声明的拒答边界）、ExplainMeetSum 按答案句分组的证据句，以及通过程序验证、长度受限的会议原文片段。judge 无工具、无网络、无额外历史。QMSum/ExplainMeetSum 是公开数据，把参考答案交给 judge 不涉及隐私，但 judge prompt 仍然只放进 review package，不进入 sanitized artifact。

rubric 版本化并使用 strict structured output：

```text
answered_question
claims_supported_by_transcript
required_evidence_covered
no_unsupported_claims
truthful_no_answer_or_recovery
clarity_and_guidance
overall: pass | fail | unknown
confidence + allow-listed reason codes
```

每个 rubric 维度的值为 `pass|fail|unknown|not_applicable`；`not_applicable` 只有 manifest 对该 stratum 声明时可用。overall 聚合固定为：任一适用维度 `fail` → `fail`；无 fail 但有 `unknown` → `unknown`；所有适用维度均 pass → `pass`；没有适用维度 → `unknown`。V1 `reason_codes` 固定为 `unsupported_claim`、`wrong_meeting`、`wrong_segment`、`missing_evidence_group`、`non_answer`、`fabricated_source`、`misrepresented_recovery`、`unclear_evidence`、`judge_parse_error`、`missing_input`、`other_allowlisted`；模型不得输出自由文本 reason。`confidence` 是枚举 `high|medium|low`；`low` 或缺失/非法 confidence 必须进入人工队列。

模型/参数、prompt、rubric、JSON schema 和 seed 支持参与 profile hash。invalid JSON、timeout、provider error 或证据不足均为 `unknown`，不能重试到选择性通过；如配置 retry，次数和全部成本必须固定并计入。judge unknown 只有在所需 judge/review 样本完成后才能从 gate 中移除；否则是 blocking unknown。

人工队列包含：

- 全部 hard-check/judge、baseline/candidate 或 judge/historical-human 分歧；
- 全部 `unknown`、低置信、边界分、judge 异常和 tool-error recovery；
- 即使双方一致，也固定抽取每层至少 1 个且不少于 `ceil(10% × 该层唯一 case 数)` 的 control（取较大值，run plan 前预注册）；基数是去重后的 case，不是 attempt/judge row，key case 的 repeats 不增加 control 权重。

人工 reviewer 看不到 arm 名称，使用同 rubric 记录 `accepted/pending/disputed` 和最终 adjudication。发布聚合只消费完成的 accepted adjudication；必要队列中存在 pending/disputed 时 gate 为 `incomparable`。抽样 seed、去重后的 case 清单、每层 control 数和 reviewer/adjudication revision 一并写入 review manifest/hash。

## 9. Usage、延迟与成本

### 9.1 Usage

在一个 `task_attempt_id` 内，primary、answer plan、每个 section、每个 recovery 都各自使用唯一 `stage_attempt_id` 读取 `RunUsage`，标准化为 input/output/reasoning/cache/audio 等 provider-neutral counters。聚合 task attempt 时每个 stage 只计一次；重试产生新的 stage_attempt_id，但不会把同一次 task attempt 的分母重复增加。保留 stage/attempt 和 known/unknown，不转储 provider raw usage。

现状：只有 orchestrator 的 primary 阶段在 finally 块里发出 `stage_usage`（request_count、input/output tokens、tool_call_count）。answer pipeline 在 composer/section/recovery 各自新建 `RunUsage()`，但没有发出。V1 需要补一个 eval-only 的只读 usage 汇总：可以给这些阶段加上同形的 `stage_usage` 事件，并带 `agent_phase`/stage 标签；也可以在 evaluator 进程内包装各阶段的 run 调用。两种方式都不改变 budget 行为，也不共享或重置 `RunUsage`。DeepSeek 的 reasoning token 计入检索阶段的输出上限，需要单独计数。

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

attempt latency 从第一用户 turn dispatch 到最终 terminal result，排除全局 preflight、library 构建/resolve，包含该 task attempt 的模型、工具、composer/recovery。报告 turn/task-attempt、successful/all/common-success `n`；gate 使用 deterministic-success task-attempt 的 end-to-end p95；无成功样本、non-finite、缺 required attempt 或样本不完整时 p95 为 blocking unknown。另报 common-success paired latency，帮助解释 success mix 变化。

`deterministic_success` 是成功成本和 successful p95 的唯一分母：所有 required turn 的 terminal status/error contract、trajectory contract、硬证据校验（item/片段文本/句子映射/URL/marker，或冻结的 no-answer/tool-error contract）、状态迁移和安全零违规全部通过。LLM judge 不参与这个硬成功谓词，judge unknown 不会把失败 attempt 排除；它通过独立的主观 review gate 控制发布完整性。

## 10. Pairing、统计与发布门禁

### 10.1 比较状态

compare 首先验证 schema、golden/overlay/library/profile/run-plan/tool-env hash、pair key、repeat 和 required denominator。结果状态：

- `eligible`：所有硬门均满足；
- `rejected`：数据完整可比，但至少一个门明确失败；
- `incomparable`：配置/样本/required trajectory/evidence/latency/judge/adjudication 不完整，不能作发布判断。

每项输出 baseline/candidate numerator、denominator、delta、direction、threshold、CI/status 和 reason code。overall 不能掩盖某个 stratum 失败。

缺失语义固定如下：manifest 声明的 `not_applicable` 不参与该维度 gate；期望适用但为 0 分母、trace/fixture/evidence/latency 缺失、judge required unknown 或未完成人工队列为 blocking `unknown`/`incomparable`；usage/price 缺失仅是非阻塞成本诊断 unknown。skip 不进成功分母，也不能当成 N/A。

### 10.2 正确性集合

逐层和总体检查：task success、句子级 Recall/MRR/gold_evidence_recall/citation precision、证据硬校验通过率、原文支持（judge 经人工仲裁后）、no-answer false positive、tool-error recovery、required/allowed/forbidden tool、parameter validity 和 safety。点估计先对每个 case 的 repeats 取均值，再在 stratum 内对 case 做等权 macro；overall 对全部 case 做等权 macro。另报 attempt micro 供诊断，但 gate 使用 case macro。率指标的 paired CI 以 case 为 cluster 重采样，不能把同一 key case 的三次 repeat 当三个完全独立样本。关键硬 case 使用 paired case-level zero-regression 检查。

冗余率、调用次数和成本展示但不补偿正确性；loop 是单独必须改善的门。paired 95% CI 对每层的 case-level candidate−baseline delta 做有放回 bootstrap，固定 `B=10_000`、seed `20260904`，按 case 重采样而不拆散同一 case 的 repeats，使用 percentile 2.5%/97.5% 边界；overall 在全部 case 上同样处理。低值更好的 false-positive/violation 先取反向 delta 再套同一规则。统计算法、seed、聚合权重和 policy hash 必须在运行前冻结。

实现上复用 `meeting_gold.stats`：

- `paired_bootstrap_ci` 的输入是 case 级差值（同一 case 的 repeats 已先取均值），这就是按 case cluster 重采样；调用时显式传入 policy 冻结的 `resamples` 和 `seed`，不依赖其默认值 `20261006`。
- 二值硬指标另报 `mcnemar_exact` 和 `win_loss_tie` 作诊断。
- 单臂比例用 `wilson_ci`。

### 10.3 V1 policy 草案（待用户确认）

推荐冻结为：

1. 每层及总体 correctness 点估计不得变差；方向相反的 false-positive/violation 不得升高。
2. 随机性率指标的 paired 95% CI 下界允许最多 `-2 percentage points` 的非劣化 margin，同时关键硬 case 为零回归。
3. candidate loop rate 必须低于 baseline；baseline 已为 0 时，candidate 保持 0 视为饱和通过，任何大于 0 都失败。
   - 2026-10-07 补充：specific-30 当前生产运行中没有超限，也没有完全相同的重复后端操作，baseline loop rate 预计为 0 或接近 0。因此这一条在实践中多半退化为"loop 保持 0"，发布与否主要由 p95 门决定。PRD D1 给出三个备选：(a) 保持饱和语义；(b) loop 只要求非劣化；(c) 按 candidate 声明的意图选择改善门。确认前不冻结 policy。
4. candidate deterministic-success task-attempt p95 至少相对改善 5%；`(baseline - candidate) / baseline >= 0.05`。successful 样本数低于预注册最小值 `n=10` 时标为 `low_n`，p95 gate 为 blocking unknown；不以少量成功样本声称改善。
5. safety violation 必须两臂均为 0；除成本/价格外的 required unknown、必要人工未完成、profile drift 或不完整 pair 均不能发布。

这组值在看到结果前进入 `gate_policy_version` 和 comparison profile hash。用户若选择更严格/宽松阈值，只修改 policy 并重新冻结/运行，不能复用旧 arm 来事后调参。

## 11. Artifact 与隐私

每个 experiment 目录：

```text
overlay.json            # 冻结的 overlay，以及对 golden dataset_hash/selection_hash 的引用
library.json            # library_hash 与 resolver 证明（不含原文）
profile.json
run-plan.json
baseline/{arm.json, attempts.jsonl, summary.json}
candidate/{arm.json, attempts.jsonl, summary.json}
comparison.json
gate.json
report.md
review/                 # explicit opt-in, ignored, may contain answer/evidence
langsmith/              # optional projection + receipt; never authoritative
```

overlay 是受控的评测输入，不等同于可分享的 sanitized report。它只引用 golden 的 case ID 和 hash，外加负例声明和审核证明，可以提交到仓库。golden set 本身（`data/meeting_gold/final`）继续由 gitignored 的数据目录承载；需要再分发时，附带 `NOTICE.md` 和两个上游 LICENSE。QMSum/ExplainMeetSum 的问题和原文是公开数据，但 sanitized artifact 仍按 allow-list 只放 ID/hash/计数，使分享和 LangSmith 投影共用同一套边界。`profile.json`、`comparison.json`、`gate.json` 和 `report.md` 等 sanitized runtime artifacts 只使用 meeting/case ID、hash 和固定 reason code，不包含 raw URL、回答、原文片段、tool args/results、tenant/private external identity、secret、provider payload/error text 或 argument fingerprint。

所有 JSON schema 设 `extra=forbid`。report redaction 使用 allow-list serializer 后再做 sentinel scan，而不是依赖 key-name 黑名单；review export 是唯一允许正文的路径，必须显式 flag、gitignored 且在 CLI 中提示本地敏感内容。

## 12. LangSmith 可选观测镜像（待 D2 确认）

### 12.1 选择 post-run externally managed experiment

V1 不使用 PydanticAI 的 `Agent.instrument_all()`、LangSmith `@traceable`、`LANGSMITH_TRACING` 或 OpenTelemetry 自动 instrumentation。它们会在 evaluator-owned allow-list 之前接触 prompt、completion、tool args/results 和 schema，且 live export 会污染 attempt latency。LangSmith 官方支持通过 `POST /api/v1/datasets/upload-experiment` 上传在外部完成的实验，因此采用：

```text
local arm artifacts + comparison.json + gate.json
        -> strict source/hash validation
        -> LangSmithProjection (no source text/payload)
        -> privacy sentinel + canonical hash
        -> explicit external-experiment upload
        -> local LangSmithExportReceipt
```

传输层通过小接口隔离。V1 首选现有 `httpx` 调用官方 REST contract，不引入 LangChain/OTel，也不让 LangSmith SDK 进入生产依赖；若实现阶段确认 SDK 能稳定覆盖同一 externally managed upload/reconciliation contract，则只能放入 evaluator-only optional extra，并由 export 命令延迟 import。无论 transport 选择如何，正常 validate/freeze/run-arm/compare/gate 的模块导入和执行都不读取 LangSmith credential、不建 client、不访问网络。

arm 为每个 attempt 启动子进程时（沿用 `agent_smoke` 的单 case 单进程方式），显式移除 `LANGSMITH_*`、`LANGCHAIN_*`、`OTEL_EXPORTER_*` 以及会开启 tracing 的相关变量，只保留 profile 允许的 provider/数据库凭据。这样开发机的 ambient 设置不会让 Agent 在 timed arm 中自动出站。仅 exporter 进程读取 API key/endpoint；credential 不进入 config hash、artifact 或 receipt。

### 12.2 Dataset、experiment 与 row 映射

- `external_dataset_id = UUIDv5(namespace, golden dataset_hash + overlay_sha256 + projection_schema_major)`；name 同时包含公开 dataset ID/revision 与短 hash，baseline/candidate 使用同一个 externally managed dataset。
- 每个 arm 是一个 experiment，名字/metadata 绑定 local experiment ID、arm、solution ID、arm artifact hash、comparison/gate hash 和 export key；solution ID 只作标签，不进入比较 profile。
- run plan 中每个 required `task_attempt_id` 是一行。`row_id = UUIDv5(external_dataset_id, pair_id)`，不包含 arm，所以两个 experiment 的同一 pair 对齐；key case repeats 由不同 pair ID 形成不同的已冻结行。
- `inputs` 只含 case ID、pair ID、attempt index、stratum、key-case；不含 query。`actual_outputs` 只含 task/evidence/trajectory/judge/adjudication 的固定 status/bool/enum。逐 attempt 指标放入 `evaluation_scores`，overall/stratum case-macro、p95/cost status 和 local gate 放入 summary scores/metadata。
- LangSmith `num_repetitions` 不执行；上传的是已经在固定 checkout/tool/fault 环境中完成的 repeats。LangSmith 计算的 latency/聚合只用于 UI，本地 nearest-rank p95、case-macro、bootstrap CI 和 gate 始终权威。

`LangSmithProjection` 的字段由 `extra=forbid` schema 正向列举。允许 case/pair/arm、非敏感版本/hash、bool/enum/reason、numerator/denominator/count、local latency/usage/known-cost/status；禁止 question/answer/Gold/judge/reviewer text、URL/platform/database/tenant identity、subtitle/excerpt/segment 内容或 hash、tool args/results/canonical tuple/fingerprint、provider body/error、secret 和 review package。发送前对 canonical payload 运行与 sanitized artifact 相同且更严格的 sentinel suite；通用 masking 只能作为纵深防御，不能把禁止字段变成可上传字段。

### 12.3 幂等、receipt 与失败语义

`export_key` 绑定 local experiment ID、golden/overlay/library/profile/run-plan hash、baseline/candidate artifact hash、comparison/gate hash、projection schema/hash 和 export profile version。dataset/experiment name、external IDs 和 row IDs 都确定性派生。

`langsmith/receipt.json` 使用独立 schema：

```text
status: not_exported | exported | export_failed
source_hashes + projection_schema/hash + export_key
transport/version + remote dataset/experiment opaque IDs
started_at/completed_at + allow-listed error_code
```

receipt 不保存 API key、endpoint credential、完整请求/响应或远端错误正文。已有成功 receipt 且 source/projection hash 相同则直接返回；hash 不同必须产生新 export key。若请求可能已被服务端接受但本地未收到响应，自动重试前先以 export key/确定性 name 查询并对账：唯一 hash 匹配视为成功，零匹配才创建，多匹配或不一致标 `remote_conflict`。无法确认时写 `export_failed/remote_state_unknown`，不猜测也不无限重试。

任一 optional dependency 缺失、credential/endpoint 未配置、timeout、429/5xx、权限错误、partial arm upload、远端删除/编辑或 feedback 冲突只改变 receipt/dashboard 可用性，不得写回 arm/comparison/gate。exporter 以 read-only 打开 source artifacts，并在测试中比较导出前后的 `gate.json` byte/hash。

V1 不读取 LangSmith feedback。未来若让 LangSmith judge 或人工标签参与发布，必须先导入为版本化、带 provenance/hash 的本地 review snapshot，经现有 schema、分歧抽检与人工仲裁后重新运行本地 comparator/gate；远端字段不能直接放行。

真实上传另需冻结 `langsmith_export_profile`：Cloud 区域或 self-hosted endpoint、workspace/project、保留期、访问控制、删除策略、timeout/retry 和 DPA/合规结论。它与 comparison profile 分离，不影响 baseline/candidate 可比性或 gate；未批准时 exporter 只能使用 fake transport/offline projection。

### 12.4 验证

- 正常 evaluator、arm、judge、compare/gate 路径的 client/network spy 调用为 0，timed latency 不包含 projection/upload；
- arm attempt 子进程环境删除 LangSmith/LangChain/OTel ambient tracing 变量，其他必需环境保持不变；
- projection/schema golden、两臂 row 对齐、repeat/pair、score/summary/latency 映射正确；
- 敏感 sentinel 遍历 source 各层，payload/projection/receipt 均无命中；
- 首次、重复、partial、ambiguous timeout、429/5xx、权限失败、remote conflict 和缺 optional dependency 的 receipt/对账行为确定；
- 任意远端响应/feedback 或失败注入后，本地 `comparison.json`、`gate.json` 内容与 hash 不变。

## 13. 兼容、上线与回滚

- `evals.meeting_gold` 的 build/validate/select/score/rq1/agent-smoke 命令和数据契约保持不变；evaluator 只 import 其函数。`agent_smoke.run_case` 如果需要拆出可复用的部分（例如返回结构化事件，而不是直接写 JSON），应保持 `agent-smoke` CLI 的输出兼容。
- `evals.natural_language` 的 `--validate-catalog`、`--preflight`、`--case`、`--all`、Gold 和 human-review 路径保持兼容，22 条行为契约作为独立回归检查。
- 新入口是 `python -m evals.agent_eval`，子命令统一为 `validate-overlay`、`build-library`、`freeze-check`、`run-arm`、`compare`、`export-langsmith`。`freeze-overlay`/`build-run-plan` 只是内部库操作，不另立 CLI 契约。
- 新 trace/usage 信号通过 evaluator-only callback/adapter 接入，不扩展 public response 和浏览器 execution timeline。生产未启用 evaluator 时应无持久数据和语义变化。
- 先交付纯离线 schema/scorer，再接 trace/证据，再接真实 arm，最后启用 judge/gate。每阶段均可退回现有单臂 evaluator。
- dataset、profile、artifact 和 rubric 均版本化；新 reader 必须拒绝未知 major version，旧 artifact 只读，不做静默迁移。
- fault injection 和答案正文导出默认关闭；任一 teardown、隐私 sentinel 或 compatibility 回归失败都停止真实运行并回滚到现有 evaluator。
- LangSmith exporter 默认关闭且位于本地 gate 之后，可整体禁用/移除而不影响 evaluator artifact；真实上传只有在 export profile 和 D2 获批后开放。

## 14. 主要风险与缓解

| 风险 | 缓解 |
| --- | --- |
| 小样本与 provider 随机性让 CI 很宽 | 关键 case repeat 3、按 case cluster 统计、显示 low-n；门禁允许不可比，不用平均值强行下结论。 |
| 重新入库/切段导致片段→句子映射漂移 | 分层只依赖 golden 数据；library_hash 绑定 chunker fingerprint 和片段文本 hash，外部调用前 resolver fail closed。改动入库的 candidate 在 V1 判为不可比。 |
| golden set 换版本，或多个 accepted 目录被混用 | overlay 固定 `final` 的 dataset_hash；resolver 拒绝其他 hash；历史目录（`frozen`、`final-legacy`）不被引用。 |
| 在 val 上反复调优导致 gate 过拟合 | gate 只做回归/非劣化判断，不宣称泛化；test 只在里程碑认证报告中跑，不参与迭代。specific-30 曾用于 2026-10 的修复调优，报告中单独标出。 |
| no_answer 负例其实有答案（同系列会议话题重叠） | 同领域负例逐条人工对照目标会议全文审核；跨领域负例作为对照；审核证明进入 overlay hash。 |
| 共用租户布局的检索缺陷掩盖 Agent 轨迹差异 | V1 gate 用每会议一租户（D3）；共用租户作为非 gate 的诊断 profile。 |
| 合成时间轴被误当作真实时间戳 | `timestamp_hit` 声明为 not_applicable；URL `t` 只校验与片段 start 一致，不与 gold 比较。 |
| 未提交的 golden/harness 代码导致两臂不可复现 | AC0：先提交 `evals/meeting_gold` 和 evaluator，baseline 必须是包含它们的提交。 |
| 参数观测泄露 query/URL/ID | 只在 request 内存 canonicalize，持久化 enum/bool/count，不保存 digest。 |
| 多跳被误判冗余/loop | 由 decision DAG 和 progress epoch 区分新证据、合法 retry 与无进展 cycle；用 synthetic trace 回归。 |
| baseline/candidate 环境并不相同 | 两个 committed checkout 消费同 run plan，golden/overlay/library/profile/tool-schema hash 严格比较。 |
| p95 被失败或 success mix 操纵 | 同时报 successful/all/common-success，gate 固定 successful-attempt 定义并要求 correctness 先通过。 |
| judge 偏差或自洽偏差 | 固定 rubric、盲化 arm、无工具、strict output、分歧全量人工 + agreement control。 |
| 成本缺失被误记为便宜 | usage/price 缺失传播为 unknown，显示 unknown count，成本不抵消质量。 |
| 故障注入污染共享状态 | Neon test 分支、专属评测租户、attempt 独立 thread、有限触发、强制 teardown 和无幽灵数据断言。 |
| 裸 `pytest` 通过 `.env` 连到生产库 | evaluator 单元测试不连数据库；需要数据库的命令走 `prepare_test_environment`，并显式覆盖 `DATABASE_URL`。 |
| 自动 tracing/ambient 环境泄露正文或污染 p95 | 禁止全局 instrumentation，arm subprocess 清理 tracing env，只有本地 artifact 最终化后显式投影/上传。 |
| LangSmith 重试产生重复实验或远端结果反向影响 gate | deterministic identity + receipt + ambiguous-state reconciliation；远端永不作为 comparator/gate 输入。 |
