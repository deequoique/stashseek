# LangSmith 集成调研

## 结论

V1 将 LangSmith 定位为 **本地 evaluator 完成后的可选、只出站、脱敏观测镜像**，不把它放进 Agent 的 timed execution path，也不让 LangSmith dataset、run、feedback 或远端 evaluator 成为发布判断的事实源。

权威链路保持为：

```text
frozen local dataset/run plan
  -> baseline/candidate immutable arms
  -> local deterministic checks + fixed judge + adjudication
  -> local comparison.json + gate.json
  -> allow-listed LangSmith projection
  -> externally managed LangSmith experiments/dashboard
```

选择这一形态的原因是：项目必须冻结自己的 case identity、重复策略、故障计划、证据验证、轨迹分母和三态发布门禁；这些语义比通用 tracing 更严格。LangSmith 很适合比较和排查，但不能反向改变本地 artifact 或 gate。

## 官方能力核对

### PydanticAI 自动 tracing

LangSmith 官方 PydanticAI 指南使用 OpenTelemetry：配置 `langsmith.integrations.otel.configure()` 后调用 `Agent.instrument_all()`，并建议 `langsmith>=0.4.26` 与 `opentelemetry-exporter-otlp`。这会自动发送 PydanticAI trace，适合通用观测，但并不天然满足本项目“问题、回答、URL、字幕、tool args/results 和 provider payload 不得出站”的 allow-list 契约。

因此 V1 不启用 `Agent.instrument_all()`、`@traceable` 或全局 `LANGSMITH_TRACING`。官方同时提供隐藏/转换 inputs、outputs 和 metadata 的机制，但那仍是在通用 trace serialization 路径上做掩码；本项目选择更强的做法：只从已经脱敏的本地 artifact 构造全新的有限 schema，敏感正文从不进入待上传对象。

来源：

- [Trace PydanticAI applications](https://docs.langchain.com/langsmith/trace-with-pydantic-ai)
- [Prevent logging of sensitive data in traces](https://docs.langchain.com/langsmith/mask-inputs-outputs)

### 上传外部已运行实验

LangSmith 官方支持将外部管理的数据集/实验通过 `POST /api/v1/datasets/upload-experiment` 上传。一次请求包含外部 dataset identity、experiment metadata、summary scores，以及逐行的 inputs、actual/expected outputs、evaluation scores、run metadata 和起止时间；使用相同 external dataset identity 上传多个 experiment 后可在 UI 中比较。

这个接口只支持由该 endpoint 创建的 externally managed dataset。它恰好允许本项目先完成 baseline/candidate 本地运行，再把两个 arm 作为同一外部 dataset 下的两个实验镜像。V1 使用现有 `httpx` 访问该 REST contract，不需要把 LangChain、OpenTelemetry 或 LangSmith tracing SDK 放进生产依赖；若实施时为认证/重试采用 LangSmith SDK，它必须作为 evaluator-only optional extra 锁版本，并且仍只能由独立 export 命令加载。

来源：

- [Upload experiments run outside of LangSmith](https://docs.langchain.com/langsmith/upload-existing-experiments)

### 重复运行

LangSmith 自身的 `evaluate/aevaluate` 支持 `num_repetitions`，但 V1 不用它触发运行。项目的 key/non-key 3/1 重复已经写入冻结 run plan，并且 baseline/candidate 必须按 `(experiment_id, case_id, attempt_index)` 配对；让远端重新运行会绕过固定 checkout、MCP/tool 环境、fault schedule 和本地计时边界。LangSmith 只展示已经完成的重复 attempt 和本地 case-macro 聚合。

来源：

- [How to evaluate with repetitions](https://docs.langchain.com/langsmith/repetition)

## 当前仓库适配点

- 项目当前使用 `pydantic-ai-slim[openai]==2.15.0`，没有 LangSmith/LangChain 直接依赖；`opentelemetry-api` 只是传递依赖，不能据此视为已经配置 export。
- `evals/natural_language/runner.py` 的 `LiveEvaluator.run()` / `_run_attempt()` 已形成 case × attempt 生命周期，`write_report()` 负责本地 artifact；新 experiment runner 应先把更完整的 arm/comparison/gate artifact 落盘，再允许导出。
- `evals/natural_language/mcp_runtime.py` 的 `DiagnosticCollector`/`ToolTrace` 已有部分安全枚举，但当前 projection 会按 `(call_index, tool_name)` 合并 lifecycle，且 retrieval detail 含运行时数字 ID；LangSmith 不能直接消费现有 collector dump。必须先完成计划中的 lossless、allow-listed trajectory artifact，再上传其派生标量。
- `app/agent/orchestrator.py` 和 `app/agent/answer_pipeline.py` 已拥有 `FunctionToolCallEvent`/`RunUsage` 的近端 seam，但参数合法率和完整 usage 尚未进入 evaluator。LangSmith 不负责补算这些指标，只展示本地 scorer 的结果。
- `LiveMcpSession.start()` 当前复制整个父进程环境给 MCP subprocess。即使 V1 只做 post-run export，arm executor 也应显式移除 `LANGSMITH_*`、`LANGCHAIN_*` 和相关 OTel exporter/tracing 变量，防止开发机 ambient 配置让子进程意外出站。
- 当前 `redact_report()` 采用 key-name 过滤；LangSmith payload 不能复用它作为唯一防线，必须使用 `extra=forbid` 的正向字段模型、canonical projection 和发送前 sentinel scan。

## V1 数据映射

本地 `dataset_sha256` 派生 deterministic external dataset UUID/name；baseline 与 candidate 分别形成一个 experiment。每个 required task attempt 映射成一行，`row_id` 由 external dataset identity、pair ID 和 projection schema major version 确定性生成，**不包含 arm**，从而让两臂在 LangSmith 比较视图中对齐到同一行。key case repeats 保持不同的 `attempt_index`/`pair_id`，不让 LangSmith 生成额外运行。

允许上传：

- `case_id`、`pair_id`、`attempt_index`、`arm`、stratum、key-case 标记；
- dataset/profile/run-plan/tool-schema/rubric/policy/artifact hash 与非敏感 schema version；
- task/evidence/judge/adjudication 的枚举、布尔和 allow-listed reason code；
- required/allowed/forbidden tool、parameter validity、redundancy、loop、safety 的分子/分母、状态和有限事件计数；
- local measured latency、usage counters、cost status/known aggregate，以及 local comparison/gate 的固定状态；
- 由本地 artifact 保存的 run start/end time，用于 UI 展示；本地 p95 仍是权威。

禁止上传：

- 原始问题、回答、Gold/reference text、judge rationale、人工 reviewer 正文；
- URL、platform ID、字幕、excerpt、segment text/hash 与 numeric item/segment/user/tenant ID；
- tool arguments/results、canonical operation tuple、argument fingerprint/digest；
- provider request/response、错误正文、secret、token、DSN、endpoint credential；
- review package 或任何未通过 allow-list schema 的 metadata。

为了在 LangSmith 中比较，逐 attempt 的本地指标作为 `evaluation_scores`/有限 metadata 上传；overall/stratum case-macro、p95、成本状态和 gate 作为 summary scores/metadata 上传。LangSmith UI 的自动聚合只作展示，报告和发布结论不读取其计算值。

## 导出、幂等与失败语义

- 独立命令形态：`--export-langsmith EXPERIMENT_DIR`（最终 CLI 可改为等价 subcommand），只读取已经完成并通过 schema/hash 检查的本地 experiment 目录。
- export 必须发生在两个 arm、judge/review、`comparison.json` 和 `gate.json` 全部最终落盘之后；不计入 turn/attempt latency、p95 或 cost-of-success。
- 发送前生成 `langsmith-projection.json` 或内存中的同源 canonical projection，执行 schema validation、禁止字段测试和敏感 sentinel scan；默认不保存含 credential 的请求。
- `export_key` 绑定 local experiment ID、dataset/profile/run-plan hash、两臂 artifact hash、comparison/gate hash 和 projection schema/hash。dataset/experiment name、external dataset ID、row UUID 由该 key 确定性派生。
- 本地 receipt 记录 `not_exported|exported|export_failed`、source/projection hash、export key、transport/schema version、remote dataset/experiment ID、时间和 allow-listed error code。API key、请求正文、远端错误正文不进入 receipt。
- 重复命令必须先核对 source hash 与已有 receipt，并使用 deterministic identity/reconciliation 防止静默创建重复实验；网络结果不确定时标 `export_failed/remote_state_unknown`，不得猜测成功，也不得改变 local gate。
- upload timeout、429、5xx、权限错误、SDK/extra 缺失或远端删除/编辑都只影响 receipt/dashboard 可用性。`eligible|rejected|incomparable` 只能由本地 comparator/gate 生成。
- V1 不从 LangSmith 导入 feedback。未来若允许远端 judge/人工反馈参与判断，必须先导出为版本化、可审计、hash 的本地 review snapshot，再重新运行本地 comparator/gate；不得实时读取远端字段直接放行。

## 测试与上线前决策

必须覆盖：

1. 普通 validate/freeze/run-arm/compare/gate 路径对 LangSmith client 和网络的调用数为 0；timed attempt 注入网络 spy 后仍为 0。
2. MCP subprocess 环境不含任何 LangSmith/LangChain/OTel tracing/exporter ambient 开关。
3. allow-list payload golden schema；把问题、回答、字幕、URL、args/results、ID、provider body、secret 和 digest sentinel 放入所有本地输入后，出站 payload/receipt 均无命中。
4. deterministic dataset/experiment/row identity、两 arm 同 dataset、pair/repeat 映射、summary score 和 latency 映射正确。
5. 首次上传、重复上传、部分成功、timeout、429/5xx、权限错误、remote-state-unknown 和 optional dependency 缺失；任何结果都不改变 `gate.json` 内容/hash。
6. LangSmith 远端分数、feedback 或 metadata 与本地冲突时，sanitized report 和发布状态仍只读取本地 artifact。

真实上传前仍需确定 LangSmith Cloud 区域或 self-hosted endpoint、workspace/project、保留期、访问控制、删除流程和合规责任。上述选择不妨碍先完成纯离线 projector/transport fake 测试，但没有明确批准前不得发送真实项目数据。
