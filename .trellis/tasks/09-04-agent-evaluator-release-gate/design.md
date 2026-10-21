# Agent Benchmark 技术设计

> 2026-10-07 二次修订：单次 benchmark，不做版本对比。之前的配对/门禁设计存档在 `research/archived-release-gate-plan-2026-10-07/design.md`。

## 1. 总体流程

```text
题库 final（只读）+ 题集文件（选题 / 故障方案 / 规则版本）
   -> 校验题集（离线，零外部调用）
   -> 入库：每场会议一个测试账号（Neon test 分支；已入库的复用）
   -> 逐题逐次运行（每次一个独立进程、独立对话）
        记录：工具请求序列、后端检索、引用、阶段 token、延迟、状态
   -> 规则检查 T1–T8 + 有效性/稳定性指标（离线，可重复计算）
   -> report.md + report.json
```

"跑"和"算"分开：原始记录落盘后，规则检查和指标计算都是离线纯函数。改了规则实现可以直接重算，不必重跑模型。不过报告必须写明规则版本；规则含义变了要升版本号。

## 2. 代码位置与复用

所有新代码放在 `evals/meeting_gold/` 下，新增一个 `benchmark` 子命令，不另建包。

| 文件 | 作用 | 复用 |
| --- | --- | --- |
| `benchmark_spec.py` | 题集文件的 schema、校验、hash | `core.canonical_hash`、`artifacts` 的 selection 校验 |
| `benchmark_run.py` | 入库、逐题运行、故障注入、写原始记录 | `production_path`（测试分支保护、入库）、`agent_smoke.run_case` 的记录逻辑 |
| `trace_rules.py` | T1–T8 判定和参数出错率；只接收结构化记录，纯函数 | — |
| `benchmark_report.py` | 有效性/稳定性指标、置信区间、报告 | `scoring.score_cases`、`agent_smoke.chunk_metrics/citation_metrics`、`stats.wilson_ci` |

另有 `benchmark_judge.py`：答案正确性打分和人工抽查（§11）。

`agent_smoke.run_case` 现在把记录逻辑和写文件混在一起。需要的话，把记录部分拆成可复用的函数，但 `agent-smoke` 命令的输出保持不变。

CLI：各模块各自提供 `add_*_arguments(subparsers)` 和 `run_*(args) -> int`，最后统一接进 `__main__.py` 的 `benchmark` 子命令（`make-spec`、`validate`、`run`、`report`、`judge`）。

## 3. 题集文件

`evals/meeting_gold/benchmarks/agent-benchmark-v1.yaml`（入库，只放 ID 和配置，不复制题库内容）：

```text
version, golden_dataset_hash, rules_version, repeats: 3
questions:
  - id, kind: specific|general, golden_case_id
  - id, kind: tool_error, golden_case_id, fault: {tool: search_segments, occurrence: 1, error: retrieval_unavailable|embedding_unavailable}
spec_hash
```

- 校验内容：
  - 题库 hash 等于 `final` 的 hash
  - 题目都在 val 集里
  - specific/general 题有完整证据标注（CES `citation_scorable`）
  - 三个领域都覆盖
  - ID 唯一
- 概括题按领域抽取，种子固定，写进文件。

## 4. 运行环境与被测版本

- **被测代码**：分支 `feat/agent-benchmark`（基于 `origin/main` `7418262`），worktree 在 `.runtime/worktrees/agent-benchmark`。只加入评测代码（`evals/meeting_gold` 和新文件），不改 `app/`。`data/`、`.env`、`.env.neon-test`、`.venv` 是指向 dev 目录的软链接。报告记录 commit，以及 `app/` 相对该 commit 是否有改动；有改动就拒绝正式运行。dev 上的未提交改动不参与。
- **数据库**：只用 `.env.neon-test` 的测试分支。运行前断言它不是生产 host（`prepare_test_environment` / `assert_test_environment_active`）。
- **账号布局**：每场会议一个测试账号，和历史运行一致。每道题都在它所属会议的账号里提问。
- **入库**：复用 `production_path.ingest_meeting`。检查点文件记录每场会议的账号、条目、片段及其对应的证据句，以及分块配置。已入库且没有变化的会议直接复用（`_meeting_still_valid`）。
- **隔离**：每次运行一个独立进程、一个新对话，不共享历史。运行前清掉 `LANGSMITH_*`、`LANGCHAIN_*`、`OTEL_*` 等环境变量，避免意外上传。
- **并发**：默认串行运行，记录开始和结束时间。并发会影响延迟，V1 不开。

## 5. 轨迹记录

全部在评测进程内挂钩，不改 app 代码：

| 记录什么 | 怎么拿 | 现状 |
| --- | --- | --- |
| 每次工具调用的开始/成功/失败/被跳过和序号 | 捕获 diagnostics 的 `tool_call` 事件（由 `AgentDeps.tool_event` 发出） | 已有 |
| 实际执行的后端检索：工具、状态、返回的片段 | 用 `RecordingServices` 包装 `KnowledgeServices` | 已有（原始 query/args 也在，只留在本地原始记录里） |
| 模型发出的每个工具请求，包括被参数校验拒绝的和同一次响应里的多个请求，按顺序 | 两种做法：在评测进程里包装模型对象，从每次模型响应中读出工具请求；或者用 PydanticAI 事件流的 `FunctionToolCallEvent`（带 `args_valid`）。实施时选能在不改 app 的前提下拿到的那种 | **缺** |
| 超限类型、引用规范化原因 | diagnostics `limit_kind`、`citation_validated` | 已有 |
| 查资料阶段的 token | diagnostics `stage_usage` | 已有 |
| 写答案阶段（规划、分段、恢复）的 token | 在评测进程里包装 answer pipeline 内各阶段的 run 调用，读取各自的 `RunUsage` | **缺** |
| 最终状态、引用、回答里的 `[S…]` 标记 | `agent.stream` 的 `completed` 事件；回答原文只用于提取标记，不落盘 | 部分已有（标记集合需新增） |
| 延迟 | 从发起提问到 `completed` 事件 | 已有 |

模型请求、工具调用和后端检索这三类记录，用调用序号（`call_index`）对齐，合成每次运行的一条有序轨迹。如果对齐失败（数量对不上），这次运行的轨迹标为"不完整"，所有依赖轨迹的规则判"未知"，不判通过。

**记录手段（已确认可行，PydanticAI 2.15.0）**：

- **模型请求和各阶段 token**：用 `pydantic_ai.models.wrapper.WrapperModel` 写一个记录壳 `RecordingModel(stage=...)`，在评测进程里对每个 Agent 实例进入 `agent.override(model=RecordingModel(原模型))`。覆盖的实例有：`orchestrator._agent` → `primary`，`answer_pipeline.stream_plan` → `stream_plan`，`answer_pipeline.section_streamer` → `section`，`answer_pipeline.composer` → `composer`/`recovery`（按调用入口区分）。每次 `request`/`request_stream` 结束后，读取 `ModelResponse.parts` 里的 `ToolCallPart`（工具名、`tool_call_id`、参数）和 `ModelResponse.usage`。下一次请求消息里出现的 `RetryPromptPart`（同一个 `tool_call_id`）表示该请求被拒绝：`content` 是校验错误列表时记为 `schema`，是字符串时记为 `tool_retry`。
- **参数校验结果**：在评测进程里包装 `orchestrator._PrimaryStreamProjector.on_event`，读取 `FunctionToolCallEvent.args_valid`，按 `tool_call_id` 合并到上面的请求记录里。两个来源不一致时，这次运行的轨迹标为"不完整"。
- **工具调用事件和后端检索**：沿用 `agent_smoke` 的做法（日志 handler 捕获 diagnostics 和 `RecordingServices`）。
- 所有包装只记录、不改返回值；运行结束后在 `finally` 里还原。

### 5.1 每次运行的原始记录格式

`runs/<question_id>__r<n>.json`，`schema_version = "meeting-benchmark-run-v1"`，只存在本地 `data/`：

```text
schema_version, question_id, kind (specific|general|tool_error), repeat_index
golden_case_id, meeting_id (题目所属会议), target_meeting_id (提问所在账号的会议；目前总是等于 meeting_id，为兼容保留)
tenant_user_id, item_id                        # 仅本地
started_at, ended_at, latency_ms               # 提问到 completed 事件
outcome: {completed: bool, status, error_code, citation_count, exception_class|null}
trace_complete: bool, trace_incomplete_reasons: [..]
model_requests: [{seq, stage, usage: {input_tokens, output_tokens, cache_read_tokens, reasoning_tokens|null},
                  tool_calls: [{tool_call_id, tool_name, args: {...}, args_valid: bool|null,
                                rejection: null|"schema"|"tool_retry"}]}]
tool_events: [{call_index, tool_name, outcome: started|succeeded|failed|skipped, result_count|null}]
backend_calls: [{seq, tool, args: {...}, status: succeeded|failed, error_type|null,
                 injected_fault: bool, segment_ids: [..], item_ids: [..]}]
limits: [{kind, phase}]
citation_failures: [reason]
citations: [{segment_id, item_id, url, start_sec|null}]
markers: [segment_id]                          # 回答中 [S…] 标记集合
stage_usage_diagnostics: [...]                 # diagnostics 的 stage_usage，用于交叉核对
stream_failures, guard_rejections, section_aborted   # 沿用 agent_smoke 的失败分类输入
failure_category                               # agent_smoke.categorize_failure
```

- 回答原文单独存为 `answers/<question_id>__r<n>.txt`，只给答案打分和人工抽查用，不进报告。
- `args`、`url`、账号/条目 ID 只在原始记录里，报告不引用。
- 证据指标（严格/宽松）在报告阶段离线计算：用 `segment_ids`/`citations`，结合 `library.json` 中片段到证据句的映射，复用 `scoring.score_cases` 和 `agent_smoke.chunk_metrics/citation_metrics`。

## 6. 规则判定细节

规则只读结构化轨迹，`rules_version` 写进报告。

- **T1 先查后答**：最终回答之前，至少有一次 `search_segments` 后端执行成功。空结果也算成功执行。
- **T2 只用该用的工具**：
  - 允许集合：`search_segments`、`get_neighbors`、`open_at`、`get_item`、`todo_write`。
  - `search_media`/`inspect_media` 如果线上配置启用了，单独统计次数，不算违规。
  - 其他工具出现一次即不通过。
- **T3 扩展有依据**：扩展类调用的目标片段或条目，必须出现在本次运行之前的检索结果里。服务端本来就有这个约束；这条规则验证约束确实生效，同时记录模型尝试越界的次数。
- **T4 不重复查**：同一工具加相同参数，在一次运行内出现两次及以上，即不通过。参数先做规范化（去掉首尾空白，不区分大小写）。例外：前一次失败后，服务端允许的那一次重试。规范化后的参数只在内存中比较，不写进报告。
- **T5 不空转**：按时间顺序，如果连续 2 次后端检索或扩展返回的片段全部是本次运行已经见过的，或者都是空结果，即不通过。阈值 2 写在规则文件里。
- **T6 不撞预算**：没有 `skipped`（被预算拦下）的调用，也没有 `limit_kind` 记录。
- **T7 引用可追溯**：
  - 每条引用的片段都在本次的检索或扩展结果里
  - 片段属于该题会议的条目
  - 片段文本与入库检查点一致
  - 回答里的 `[S…]` 标记集合等于引用集合
- **T8 出错按规矩处理**（故障题）：注入失败之后，同一操作最多重试一次。最终要么正常回答（同时满足 T7），要么返回如实的失败状态。返回正常答案却没有任何有效证据，判为"假装成功"，不通过。

**工具参数出错率** = 被参数校验拒绝的工具请求 / 全部工具请求。分母按单个请求计，同一次响应里的多个请求分别计数。

## 7. 指标口径

- 每个指标都输出 `分子 / 分母 / 状态 / 原因`。状态有三种：
  - `ok`
  - `not_applicable`：该题不适用，比如没有完整证据标注的题没有证据指标
  - `unknown`：应该能算但缺记录
- 分母：
  - 运行级指标（回答成功率、各规则通过率等）的分母，是该题型的全部运行。
  - 有效性证据指标的分母，是有完整证据标注的运行。
- 证据指标用现成实现：
  - 严格口径：`scoring.score_cases`（句子级 Recall@k、MRR、证据召回、引用精确率）
  - 宽松口径：`agent_smoke.chunk_metrics/citation_metrics`
- 同题一致率：一道题 3 次运行结果完全相同记为一致，分母是题数。
- 延迟：p50/p95 用最近秩法（nearest-rank），同时给最大值和样本数。分"全部运行"和"成功运行"两个口径。
- 置信区间：比例类指标用 `stats.wilson_ci`。同一题的 3 次运行不是独立样本，报告里会注明，并同时给出"按题汇总"的结果（先对每题取平均，再对所有题取平均）。

## 8. 故障注入

- 在 `RecordingServices` 同一层，对指定题的指定第几次调用，抛出服务端本来就会处理的错误：`RetrievalUnavailable` 或 `EmbeddingUnavailable`。这样走的是线上真实的恢复路径（`ToolPolicy` 的 `retry_same_read`）。
- 只在评测进程内生效，不需要改 app 代码。
- 跑完检查：该条目和片段的数据没有变化，没有残留的任务或记录。

## 9. 报告与数据存放

```text
data/meeting_gold/runs/benchmark-<date>/
  spec.json            # 题集文件快照 + hash
  environment.json     # commit、模型、thinking、预算、分块配置、测试分支断言结果（不含连接串）
  library.json         # 入库检查点
  runs/<question_id>__r<n>.json   # 每次运行的原始记录（格式见 §5.1，不含回答原文）
  answers/<question_id>__r<n>.txt # 回答原文，仅供打分和人工抽查
  judge/                          # 答案打分结果和人工抽查记录（§11）
  report.json
  report.md
```

- `data/` 不入库。
- `report.md` 可以分享：只放题目 ID、计数、比例和原因码，不放回答、工具参数、URL 或账号 ID。
- 题库是公开数据（MIT），问题原文本身不敏感，但报告里仍然只放 ID，保持简单统一。

## 10. 风险

| 风险 | 处理 |
| --- | --- |
| 在 dev 上跑，测到的不是线上代码 | 规定在基于 main 的干净 worktree 上运行，报告写 commit；`environment.json` 记录 worktree 是否干净，不干净就拒绝运行 |
| 评测挂钩改变 Agent 行为 | 挂钩只记录，不改返回值；故障注入只在故障题上开启；先用 `--dry-run` 和假模型验证 |
| 规则写得太严，合理行为被判失败 | 先用 specific-30 的历史记录回放，人工核对每条不通过的原因，再定规则版本 |
| 每题只跑 3 次，结论不稳 | 报告给置信区间和样本数，不夸大；需要时可以加跑 |
| 裸 `pytest` 连到生产库 | 单元测试不连数据库；需要数据库的命令走测试分支保护 |

## 11. 答案正确性打分

- **打分模型**：OpenAI（`OPENAI_API_KEY`），与被测的 DeepSeek 不同家，减少自评偏差。模型名、温度（0）、提示词和输出 schema 都固定，写进 `judge/config.json` 并参与 hash。具体模型在正式运行前根据账号可用模型确定。
- **输入**：题目、Agent 回答原文、QMSum 参考答案、ExplainMeetSum 证据句。打分模型不联网、不用工具。
- **输出（strict JSON）**：`verdict: correct|partial|incorrect|unknown`、`reason_codes`（固定列表：`matches_reference`、`missing_key_point`、`contradicts_reference`、`unsupported_claim`、`non_answer`、`judge_error`）、`confidence: high|medium|low`。格式不合法或调用失败一律记为 `unknown`，不重试刷结果。
- **人工抽查**：
  - 固定种子，按题型分层抽约 10 题，优先包含 `low` 置信和 `partial`。
  - 导出 `judge/review.csv`，内容包括题目、参考答案、回答和打分结果；用户填写人工结论后导入。
  - 报告给出人工与 LLM 的一致率。
- **报告口径**：答案正确率 = `correct` / 有结论的运行（`partial` 单列）；`unknown` 的数量单独列出，不算对也不算错。