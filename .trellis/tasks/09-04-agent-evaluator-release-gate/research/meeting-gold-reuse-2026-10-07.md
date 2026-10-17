# 会议 Golden Set 与现有 harness 的复用评估（2026-10-07）

本文记录 2026-10-07 对仓库现状的核查结果，作为把 evaluator 数据源从"手工标注 32 条视频 case"改为"QMSum + ExplainMeetSum 会议 golden set"的依据。

## 1. Golden set 现状

- 代码：`evals/meeting_gold/`（build / validate / select / score / rq1 / agent-smoke CLI），测试 `tests/test_meeting_gold*.py` 共 10 个文件约 111 个测试。
- **未提交**：`evals/meeting_gold/` 和这些测试在 `dev` 上仍是 untracked；`origin/main` 只有 PR #25 的产品修复，没有 golden set 代码。任务 `09-12-qmsum-explainmeetsum-golden-set` 仍为 `in_progress`。
- 数据：`data/meeting_gold/` 被 `.gitignore` 忽略。共有多份 `freeze_status=accepted` 的产物：

  | 目录 | dataset_hash（前 7 位） | 用途 |
  |---|---|---|
  | `final` | `4aeccba` | `__main__.py` 中 `DEFAULT_RQ1_CASES`；RQ1、specific-30 均使用 |
  | `frozen` | `030deb4` | 早期 freeze |
  | `final-legacy`、`frozen-copyright-corrected` | `190eb50` | 历史版本 |

  evaluator 应只绑定 `final`（`4aeccba…`），并在 overlay manifest 中记录完整 hash。
- 规模（`final`）：232 场会议，1,810 条 case。val 272 条 / 35 场会议，test 281 条 / 35 场会议。领域为 Academic(icsi)、Committee、Product(ami)。query 类型为 `specific` / `general`。
- val 中 CES `citation_scorable` 的分布：
  - specific：单组证据 12 条（icsi 1 / committee 3 / ami 8），多组证据 215 条
  - general：30 条（icsi 5 / committee 6 / ami 19），全部是多组证据
- golden set 自身的约束：
  - 不生成 no-answer 负例；缺失证据是 `unavailable`，不是负标签
  - 不引入时间戳（"No timestamps … synthetic seconds"）
  - 句子证据重叠只是标注覆盖代理，不代表语义支持
- ExplainMeetSum 的 evidence group 对应 QMSum 参考答案的**每个答案句**，表示证据覆盖面，不表示有依赖关系的多跳。因此原 PRD 的 `multi_hop_retrieval` + 依赖 DAG 不能直接映射到这份数据。

## 2. 生产链路 harness 已具备的能力

| 能力 | 位置 | 对应原计划 |
|---|---|---|
| canonical JSON + SHA-256、source lock、dataset/selection hash、leakage 标记 | `core.py`、`artifacts.py` | 里程碑 1 的 hash/冻结机制（数据部分） |
| 稳定身份 `meeting_id` + `stable_sentence_id(meeting, turn, sentence)` | `core.py` | AC2 稳定身份（不依赖自增 ID） |
| 仅连 Neon test 分支的环境保护 | `production_path.prepare_test_environment` | R2 非 production 前置检查 |
| 真实入库：会议转 YouTube json3 字幕，走 `create_item` + `process_item` | `production_path.ingest_meeting` / `ingest_library` | fixture 准备 |
| 共享 library：35 场 val 会议放在一个租户里，有 checkpoint，可复用；`_meeting_still_valid` 做漂移检查 | `production_path`、`library.json` | 只读 resolver 的雏形 |
| 真实 `agent.stream()` 单 case 单进程运行；`RecordingServices` 记录后端检索调用（tool、query、args、status、segment_ids、scores） | `agent_smoke.py` | 单臂执行器、后端轨迹 |
| 捕获 diagnostics：`tool_call`（started/succeeded/failed/skipped + call_index）、`stage_usage`、`limit_kind`、`citation_validated` | `agent_smoke.run_case` | 轨迹与 usage 的原始信号 |
| 引用片段与 library 文本比对（`rejected_hits`） | `agent_smoke.run_case` | R4 片段归属/文本校验的雏形 |
| 句子级严格指标（Recall@k、MRR、gold_evidence_recall、citation precision、group any/all）和块级宽松指标 | `scoring.py`、`agent_smoke.chunk_metrics/citation_metrics` | R3 结果层 |
| 失败分类 `categorize_failure` | `agent_smoke.py` | 失败原因码 |
| `mcnemar_exact`、`wilson_ci`、`paired_bootstrap_ci`、`win_loss_tie` | `stats.py` | 里程碑 6 的统计部分 |

## 3. App 侧已有的轨迹/usage 信号

- `AgentDeps.tool_event()`（`app/agent/runtime_state.py`）投影 `ToolProgressObservation(tool_name, call_index, outcome, result_count)`，并发出 diagnostics `tool_call`。不保存参数和结果。
- `ToolPolicy.skipped_payload()`：超出服务端阶段预算的检索返回 `skipped/budget_exhausted`。2026-10-07 起，同一步内的多个检索在预算内全部执行（`SAME_STEP_SKIPPED` 只为兼容保留，不再返回）。
- 服务端预算：检索共 5 次（search 2 + expansion 3）；`AGENT_OUTPUT_TOKEN_LIMIT=3000`。
- `ToolPolicy` 已有**只存在内存**的 read fingerprint（`pending_read_failures`），用来绑定 transient read 失败后的重试。冗余判断可以复用同类 server-owned canonicalization，但不能落盘。
- `stage_usage` 只在 orchestrator 的 primary/retrieval 阶段发出（`orchestrator.py` 的 finally 块）。answer pipeline 中 composer/section/recovery 各自新建 `RunUsage()`，但**没有汇总发出**。
- 模型：DeepSeek `deepseek-v4-flash`，检索阶段开启 thinking（决策 B1：关闭后 30 条中有 2 条跳过搜索）。即使设置了 `parallel_tool_calls=False`，模型仍会在一次响应里发出多个工具调用。
- 尚未实现：
  - handler 之前被 schema 拒绝的请求（PydanticAI `args_valid`）
  - 按顺序、不丢重复次数的模型请求事件流
  - progress epoch / 冗余 / loop 判定
  - composer/recovery 的 usage 汇总

## 4. 已有基线数字（specific-30，val，单臂，非冻结 evaluator 产物）

| 运行 | 回答成功 | 检索池 gold 命中/覆盖 | 引用含 gold | 中位延迟 | 输出超限 |
|---|---|---|---|---|---|
| 修复后（10-06） | 30/30 | 0.929 / 0.715 | 0.893 | 13.7 s | 6 |
| 预算修复、开启 thinking（10-07，当前生产） | 30/30 | 0.964 / 0.780 | 0.893 | 9.2 s | 0 |

对 10-07 最终一轮 30 条单 case 记录（`data/meeting_gold/runs/2026-10-specific30/05-*/qmsum_*.json`）的轨迹统计：

| 项 | 中位数 | 范围 / 合计 |
|---|---|---|
| primary 模型请求 | 3 | 2–5 |
| 输入 token | ≈16.3k | — |
| 输出 token | ≈430 | 最高 1975 |
| 工具调用（`tool_call_count`） | 3 | 1–6 |
| 实际后端检索 | 3 | 1–5 |
| `skipped` 调用 | — | 3 条 case，共 3 次 |
| 完全相同的重复后端操作（tool + query + args） | — | 0 |
| 端到端延迟 | 9.15 s | 最大 17.5 s |

`stage_usage` 每条 case 只有 1 行（`agent_phase=retrieval`），证实 composer/recovery 的 usage 没有被采集。以上数字可以作为 V1 baseline 的参考，但不是按冻结 run plan 产生的 arm artifact，不能直接进入 gate。"完全相同重复为 0"只说明没有最明显的冗余；改写后仍无新证据的检索，要等 progress epoch 实现后才能判定。

RQ1 的结论（检索排序是瓶颈；全库检索时多样性处理有害；关键词 AND 全部零命中）指向的是检索质量，不是轨迹问题。不过这说明 general/分散证据类问题会触发更多的改写检索，是观察冗余和 loop 的主要场景。

## 5. 对 evaluator 设计的影响

> 注：本节写于第一次修订，当时目标还是新旧版本对比。同日用户明确目标是对当前线上版本做单次 benchmark，所以第 5、6 条中关于两臂和门禁的部分已不适用；第 1–4 条仍然是当前计划的依据。

1. 数据层不再手工标注 32 条视频 case。改为在 golden set 之上加一份 **evaluator overlay manifest**，内容包括：
   - 引用 golden set 的 `dataset_hash` 和 case ID
   - 分层、key case、重复策略、轨迹契约、故障计划
   - 自建 no-answer 负例及其审核证明

   golden set 本身保持不变。
2. 分层按数据可推导的维度来定：`specific_local` / `specific_dispersed`（按 gold 句子在会议中的分散程度）、`general`、`no_answer`（overlay 构造）、`tool_error`（在 gold case 上叠加故障）。单组/多组证据不适合分层，因为单组只有 12 条且偏向 ami。
3. 证据硬校验改为会议语义：引用片段属于该 case 会议的 library item、片段文本与 library 一致、片段到句子的映射、`[S…]` marker 与结构化 Citation 一致、URL 与 library item 一致且 `t=floor(start)`。gold 时间戳不存在，`timestamp_hit` 为 `not_applicable`。YouTube 真实 ID 和 Bilibili `?t=` 的校验移出 V1。
4. 单臂执行器基于 `agent_smoke` 的进程内 `agent.stream()` 路径，不走 MCP `LiveEvaluator`。原有 22 条自然语言行为契约保留，作为独立的安全/行为回归检查。
5. 前置条件：`evals/meeting_gold/` 必须先提交（完成 09-12），否则无法满足"两臂都从已提交 checkout 运行"。
6. 当前生产的检索预算很紧（5 次检索）。10-07 那轮 0 次超限、0 次完全相同的重复操作，baseline loop rate 很可能接近 0。原 D1 要求"loop 和 p95 必须同时改善"，对大多数 candidate 会退化为"loop 保持 0 且 p95 改善 ≥5%"。需要在 D1 中重新确认。
