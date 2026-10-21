# Agent Evaluator Implementation Plan

> **2026-10-07 修订**：数据源改为会议 golden set，执行基于 `evals/meeting_gold` 的生产链路 harness。下文按"已有 / 部分具备 / 待做"重新标注了每个里程碑，并调整了推荐交付顺序。依据见 `research/meeting-gold-reuse-2026-10-07.md`。

## Planning Gate

- [ ] 用户确认 PRD 中的 **D1**：门禁阈值，以及 baseline loop≈0 时的饱和语义，三个选项 a/b/c。
- [ ] 用户确认 **D2**：LangSmith V1 只作为评测后的脱敏镜像。
- [ ] 用户确认 **D3**：V1 gate 用"每会议一租户"布局；no_answer 按"在会议 Y 的租户里问会议 X 的问题"构造，跨领域和同领域各一半。
- [ ] 用户确认 **D4**：V1 不包含 YouTube/Bilibili 真实视频证据层。
- [ ] 把确认后的值从 PRD `Blocking Decision` 收敛进 R1/R9/AC，并完成最后一次 PRD convergence pass。
- [ ] 用户在最终规划摘要之后明确批准进入实施。批准之前：
  - 不运行 `task.py start`
  - 不修改产品/evaluator 代码
  - 不触发付费评测

本任务保留为一个端到端实现任务。原因：overlay、trajectory、evidence、judge 和 comparator 共用同一套 artifact/profile 契约，接口稳定前拆分子任务会增加交叉版本风险。如果某个里程碑暴露了产品 Agent 缺陷，另建产品修复任务，不在 evaluator 任务里顺手改策略。

## 现状盘点（2026-10-07）

| 原里程碑 | 状态 | 已有实现 | 剩余工作 |
| --- | --- | --- | --- |
| 1. 冻结数据 / hash / run plan | **部分具备** | golden set 的 canonical hash、dataset/selection hash、source lock、leakage、accepted freeze（`meeting_gold.core`/`artifacts`） | overlay schema（分层、key、DAG、fault、负例、审核证明）、profile/run-plan/arm/gate schema、local/dispersed 分层计算 |
| 2. 稳定身份与证据硬校验 | **部分具备** | 稳定的 case/sentence ID；library 构建和 `_meeting_still_valid`；`rejected_hits` 片段文本比对；片段→句子映射（`map_chunk_records`） | 统一的只读 resolver 与 `library_hash`；每会议一租户布局；URL/`t`/marker union checker；把 checker 输出改为固定原因码 |
| 3. 轨迹与 usage 投影 | **信号已有，指标缺失** | `tool_event`（含 skipped）、diagnostics `tool_call`、`RecordingServices` 后端记录、retrieval 阶段的 `stage_usage`、server 内存中的 read fingerprint | `args_valid` 捕获、不丢数据的事件流、DAG 对齐、progress epoch、冗余/loop、composer/section/recovery 的 usage |
| 4. 单臂执行与 artifact | **部分具备** | `agent_smoke.run_case`：真实 `agent.stream()`、单 case 单进程、Neon test 环境保护、失败分类 | 按 run plan 执行、重复/pair ID、profile preflight、故障注入 seam、allow-list artifact、teardown 证明 |
| 5. Judge 与人工 review | 待做 | — | 全部 |
| 6. 比较与门禁 | **统计已有** | `meeting_gold.stats`：配对 bootstrap、McNemar、Wilson、win/loss/tie | 配对器、分层 case-macro、unknown 传播、三态 gate、报告 |
| 7. LangSmith 镜像 | 待做 | — | 全部 |
| 8. 真实运行 | **有非冻结参考** | specific-30 的 6 轮历史运行（最新一轮：30/30，中位 9.15 s） | 按冻结 run plan 跑 baseline health report |

### 推荐交付顺序

0 → 1 → 3 → 2 → 4，到这里交付第一个可用成果：**带轨迹指标的 baseline health report**。然后是 6 → 5 → 7 → 8。

把轨迹（3）提到证据（2）之前，原因有两个：

- 轨迹指标是这个任务的核心诉求；
- 已有的 `rejected_hits` 和句子映射已经足够支撑 health report 的结果层。

## 0. 前置条件与实施基线

- [ ] **AC0 前置**：`evals/meeting_gold/` 及 `tests/test_meeting_gold*.py` 在 `dev` 上仍是 untracked，`main` 上没有。需要先完成 `09-12-qmsum-explainmeetsum-golden-set` 的收尾（supervisor 验收、提交）。这一步属于 09-12 任务，不在本任务内提交。提交前要征得用户同意，并且不夹带 dev 上的其他 WIP。
- [ ] 确认 `data/meeting_gold/final` 是唯一被引用的 golden 目录（dataset_hash `4aeccba963f3874f4d7be079d21cb0a19c50445e9160c8a9838342dfa3c0faf0`），并且 `python -m evals.meeting_gold validate --output data/meeting_gold/final` 通过。
- [ ] 记录以下基线：
  - base revision、Python/依赖锁、PydanticAI 版本、migration head
  - 当前模型配置：`deepseek-v4-flash`，检索阶段 thinking on，`AGENT_OUTPUT_TOKEN_LIMIT=3000`
  - 检索预算：5/2/3
- [ ] 运行现有的离线测试并保存基线：
  - meeting_gold 的 10 个测试文件
  - natural_language evaluator/quality 测试
  - 已知 `test_preflight_refuses_production_before_opening_database` 受 ambient wildcard origin 影响，需要用显式 fixture 隔离，不能 `xfail`
- [ ] 确认 `data/agent_eval/` 被 `.gitignore` 的 `data/` 规则覆盖。
- [ ] 记录父进程的 ambient LangSmith/LangChain/OTel 变量，并在测试 fixture 中强制 arm 子进程清除这些变量。

验证（凡是会 import app 配置的 pytest，一律强制 test 分支，不得裸跑）：

```bash
.venv/bin/python -m evals.meeting_gold validate --output data/meeting_gold/final
.venv/bin/python -m evals.natural_language --validate-catalog
data/meeting_gold/runs/2026-10-specific30/scripts/run_tests_testdb.sh \
  tests/test_meeting_gold.py tests/test_meeting_gold_artifacts.py \
  tests/test_meeting_gold_scoring.py tests/test_meeting_gold_integration.py \
  tests/test_meeting_gold_rq1_agent_smoke.py tests/test_meeting_gold_rq1_stats.py \
  tests/test_natural_language_evaluator.py tests/test_natural_language_quality.py
```

`run_tests_testdb.sh` 在被忽略的 `data/` 目录下。如果它不可用，等价做法是：从 `.env.neon-test` 读取 URL，断言它的 host 不是 `.env` 中的 production host，再显式设置 `DATABASE_URL`。

## 1. Overlay、profile 与 run-plan schema

- [ ] 新建 `evals/agent_eval/`，写 strict `extra=forbid` 的 schema，覆盖：
  - overlay（golden 引用、`strata_policy`、cases、negatives、repeat、`metric_applicability`、annotation review proof）
  - execution profile（含 `library_hash` 和模型/thinking/budget）
  - gate policy、run plan、arm/task-attempt/stage-attempt、comparison、gate decision
- [ ] hash 复用 `meeting_gold.core.canonical_hash`，不另写一套 canonical 规则。overlay hash 排除自身字段。overlay、profile、tool schema、rubric、price table、policy 各自维护版本和 hash。
- [ ] 实现 golden 绑定：
  - 只接受 `final` 的 dataset_hash
  - case 必须存在于 val，回答层要求 CES 为 `citation_scorable`
  - 拒绝 test split 的 case 进入 gate selection
- [ ] 实现 `local/dispersed` 分层：只依赖 golden 的 gold 句子位置，阈值在 `strata_policy` 中冻结；specific-30 的 28 条可评分 case 必须全部落入这两层。
- [ ] 校验以下约束：
  - 五层各至少 8 条，每层至少 2 个 key case
  - 3/1 重复策略
  - 回答层覆盖三个领域
  - case/pair ID 唯一
  - `no_answer` 与 `tool_error` 的字段互斥
  - `timestamp_hit` 恒为 `not_applicable`
- [ ] 每层默认 DAG（design §6.2）的 schema 与校验：无环、可达、必需节点、允许工具、retry/recovery 分支。
- [ ] 构造 `no_answer` 负例（D3）：
  - 跨领域和同领域各一半
  - 同领域负例导出人工审核包，审核时对照目标会议全文
  - 审核完成前，overlay 保持 draft
- [ ] 构造 `tool_error` 计划：基于回答层 case，故障类别取自 `ToolPolicy` 现有的 recovery 路径。
- [ ] 生成不可变的 `run_plan.json`，固定每个 case/attempt 的 pair ID、repeat、seed 支持和 order slot。
- [ ] 提供纯离线 CLI：`python -m evals.agent_eval validate-overlay` 和 `freeze-check`，默认零外部调用。

单元测试：

- canonical hash 与 `meeting_gold` 的 golden vector 一致；任一受管字段变化都会改变 hash；hash 不包含自身
- 引用错误的 golden hash、test case、非 CES 可评分 case 进入回答层，都 fail closed
- 层数不足、key 不足、领域缺失、repeat 不是 3/1、缺审核证明，都 fail closed
- 分层计算对 specific-30 稳定可复算
- run plan 的顺序和 pair key 稳定

**Review Gate A**：schema/hash/run-plan 的测试全部无网络通过。overlay 经人工复核（含 no_answer 审核）之前，不进入真实运行。

## 2. Library resolver 与会议证据硬校验

- [ ] 在 `evals/agent_eval/library.py` 中包装 `meeting_gold.production_path`：
  - 支持 D3 的"每会议一租户"布局（历史 prodpath runner 用过这种方式），保留共用租户布局作为诊断 profile
  - 计算 `library_hash`：golden hash、chunker fingerprint、embedding、布局、每个片段的文本 hash 和句子映射
- [ ] 只读 resolver：
  - 外部调用前，复用 `prepare_test_environment` / `assert_test_environment_active`
  - 对每个 meeting 核对 ready item 唯一、chunker fingerprint 一致、片段时间和文本 hash 一致
  - 漂移时区分原因：`missing`、`duplicate`、`not_ready`、`chunker_drift`、`segment_drift`
- [ ] 实现 `answer_evidence.py`，按 design §7 的顺序校验：status/schema、item/租户归属、片段文本、句子映射、URL/`t`（合成 YouTube ID，用 YouTube parser 校验）、`[S...]` marker union。
- [ ] no_answer 校验：没有 Citation、没有 marker、没有来源链接。tool-error 校验：phase、status、error code。硬失败时不生成 judge input。
- [ ] checker 只导出固定的 bool/enum/reason code。

测试：

- 片段属于其他会议、其他租户；item 未 ready 或已删除
- library 文本漂移、chunker fingerprint 漂移
- 片段→句子映射缺失
- URL 的 video ID 不符、`t` 与 start 不一致；负数/NaN/越界时间
- 未知、重复或缺失的 marker
- 硬失败时 judge spy 调用次数为零

**Review Gate B**：resolver 对冻结的 library 全部通过，任何漂移都在模型调用前停止。

## 3. 轨迹与 usage 投影（核心切片）

- [ ] 在 evaluator 进程内挂接 PydanticAI 事件流，用 `FunctionToolCallEvent.args_valid` 捕获 handler 之前的 schema 结果。同一响应里的批量调用按 sequence 拆成多个请求（DeepSeek 实测会批量发出）。
- [ ] 通过 `call_index` 关联三类事件：模型请求、`AgentDeps.tool_event`（started/succeeded/failed/skipped）、`RecordingServices` 的后端执行。`RecordingServices` 只向投影提供 status 和 `new_segment_count`。原始 query/args 只在 attempt 内存中做 canonical 比较。
- [ ] canonical operation 复用 `ToolPolicy` read fingerprint 的同类规则：只在内存中，attempt 结束即丢弃，事件里只记录 `duplicate_of_present`。
- [ ] 实现 DAG 对齐，以及四个工具选择指标：required recall、allowed precision、forbidden rate、contract pass。tie-break 规则见 design §6.2。
- [ ] 实现 progress epoch（新片段、必要状态推进、允许的 recovery、不同的必需节点）、合法 retry 豁免、请求/后端两种冗余率，以及版本化的 loop detector。
  - loop 以全部 required attempt 为分母
  - trace 不完整时标 unknown
  - `budget_exhausted` skipped 和 `limit_kind` 单独统计
- [ ] usage：为 answer plan、section、recovery 补齐只读的 stage usage 汇总（design §9.1），连同现有的 retrieval `stage_usage`，按 `stage_attempt_id` 聚合，单独统计 reasoning token。
- [ ] 用价格表计算 known cost 和 unknown 状态。成功成本的分子包含失败和重试；没有成功 attempt 时为 undefined。

synthetic trace/usage 测试：

- 单次检索；改写检索带来新片段 / 改写检索无新片段
- search→neighbors/open_at 扩展
- 同一响应批量调用；`budget_exhausted` skipped；跨 step 重复 skipped
- 同参重复；transient read 失败后的 `retry_same_read`；长度 2–4 的无进展 cycle
- malformed JSON、schema reject、范围/scope/前置条件失败，都计入参数分母
- primary + plan + sections + 多次 recovery 的 usage 求和；缺 usage 或价格时为 unknown
- 用 specific-30 最新一轮的已记录 trace 做回放（离线，只用 `tool_events`/`retrieval_trace` 的结构字段）。预期：完全相同的重复为 0，skipped 只出现在 3 条 case 中。
- 敏感 sentinel 放入 args/result/provider error，确认 trajectory、diagnostics、SSE 和 sanitized serializer 都没有泄露

**Review Gate C**：指标的分子/分母由 synthetic golden test 锁定；现有 Agent 行为和 public SSE 没有语义差异；隐私 sentinel 全部通过。

## 4. 单臂执行与 baseline health report

- [ ] `arm.py` 包装 `agent_smoke.run_case`：
  - 按 run plan 执行
  - 每个 attempt 一个独立进程和独立 thread
  - 子进程清除 tracing 环境变量
  - 故障注入 seam 与 `RecordingServices` 同层
- [ ] preflight 计算 comparison profile、tool-schema hash、`library_hash` 和 runtime hash；拒绝未提交或不匹配的 arm identity。secret 只检查是否存在。
- [ ] 每个 attempt 保存：outcome、trajectory、证据校验结果、句子级/块级指标、usage、latency 和 safe reason。延迟从第一次 dispatch 算到 terminal，不含 library resolve。
- [ ] crash/timeout/skip/unscorable 都写入 attempt 行；finally 保证 teardown；中断后能判定 incomplete，不静默续跑。
- [ ] CLI：`python -m evals.agent_eval run-arm --overlay --profile --run-plan --arm --solution-id --results-dir`。不切换 Git，默认不导出 review 正文。
- [ ] `arm.json`/`attempts.jsonl`/`summary.json` 使用 allow-list serializer。
- [ ] **交付物**：在当前已提交版本上跑一次全量 run plan 的 baseline health report。内容包括：五层结果、轨迹、运行指标，以及与 2026-10 specific-30 历史数字的对照（标注为非冻结参考）。

测试：

- 两个 fake committed arm 消费同一个 run plan，pair key 唯一；缺失、重复、顺序错误、incomplete 都能检测到
- profile 中任一字段不一致都被拒绝，包括 library、thinking、budget
- teardown 在成功、断言失败、provider error、取消和故障失败时都会执行
- `evals.meeting_gold agent-smoke --dry-run` 和 natural_language 的旧路径继续通过

**Review Gate D**：先用 fake/synthetic arm 完成 artifact 往返测试。真实付费运行必须另行显式批准，并且 Gate A–D 全部通过。

## 5. Rubric judge 与人工 review

- [ ] 定义固定的六维 rubric、strict 输入/输出、reason-code allow-list（`wrong_meeting`、`wrong_segment` 等）和 confidence/unknown 规则。
- [ ] judge 的输入：问题、回答、QMSum 参考答案、ExplainMeetSum 证据句、通过硬校验的有界原文。arm 标签盲化；无工具、无网络。
- [ ] 固定 judge 的 retry、timeout、temperature、seed；所有 retry 计入成本。
- [ ] 抽检选择器：全部分歧样本 + 全部异常样本 + 预注册的 agreement control（按唯一 case 计数）。
- [ ] review package 显式 opt-in、放在 gitignored 目录；sanitized report 只保留聚合结果和 reason code。

测试：

- 支持、不支持、部分支持；引用正确但语义不支持；no_answer 胡答；错误恢复被说成成功
- invalid JSON、timeout、低置信
- 硬失败时 judge 零调用
- pending/disputed 样本使比较不完整

**Review Gate E**：用人工构造的样本做一次 blind calibration，之后冻结 rubric。

## 6. 配对比较、报告与发布门禁

- [ ] compare 在聚合前验证：artifact major version、golden/overlay/library/profile/run-plan/tool-env hash、pair、repeat 和必需分母。
- [ ] 按 attempt、case、五层和总体输出：结果、证据、no-answer、tool-error、轨迹、延迟、usage 和成本。每个值带 numerator/denominator/status。
- [ ] 统计复用 `meeting_gold.stats.paired_bootstrap_ci`：输入是 case 级差值；policy 冻结 `resamples`/`seed`，调用时显式传入。二值指标另报 McNemar 作诊断。
- [ ] 实现三态门禁和逐门 reason，包括 D1 确认后的 loop/p95 语义和饱和例外。
- [ ] 输出 `comparison.json`、`gate.json` 和同源的 `report.md`；Markdown 不自行重新计算数字。

门禁矩阵测试：

- correctness 下降、CI 超出 margin、key case 回归、safety violation
- loop 改善 / 持平 / 恶化 / baseline=0
- p95 达标 / 不足 / 缺失 / low_n
- 只有调用次数或成本下降
- judge pending、成本 unknown
- golden/overlay/library/profile 漂移，pair 缺失或重复
- 唯一的 `eligible` 路径

**Review Gate F**：只用 golden synthetic artifact 验证全部分支；用户确认 policy hash 之后再冻结。

## 7. LangSmith 评测后脱敏镜像

范围和测试要求与原计划一致（design §12）。调整点：

- [ ] `external_dataset_id` 由 golden dataset_hash + overlay hash 派生。
- [ ] tracing 变量清理改在 arm attempt 子进程边界执行。
- [ ] 数据集公开，但模型回答、tool 参数、URL、租户/用户 ID 仍在禁止上传之列。

**Review Gate G**：用 fake transport 完成 projection、隐私、幂等和失败矩阵测试。D2 和 export profile 获批之前，禁止真实上传。

## 8. 冻结、真实运行与项目报告

- [ ] 冻结 overlay revision（含 no_answer 审核）和 library，生成 run plan。
- [ ] 跑 baseline health report（AC12 最低要求）。
- [ ] 选定明确的 committed candidate 之后，在同一 library 和较短时间窗口内按 counterbalanced 顺序跑两臂。每臂至少 60 个 attempt。真实付费运行必须显式 opt-in。
- [ ] 运行固定 judge，完成必需的人工 review。存在 pending/disputed 时，gate 维持 incomparable。
- [ ] 生成 sanitized 项目报告，人工核对分母、hash、key case、失败原因、p95/cost unknown 和 gate 结论。
- [ ] test split 的 held-out 认证报告只在里程碑节点运行一次，不参与 candidate 迭代。
- [ ] evaluator 发现的 Agent 缺陷单独建 Trellis 任务。

建议的命令形态（以实施后的 `--help` 为准）：

```bash
.venv/bin/python -m evals.agent_eval validate-overlay evals/agent_eval/overlays/agent-evaluator-v1.yaml
.venv/bin/python -m evals.agent_eval build-library --overlay OVERLAY --layout per-meeting --output data/agent_eval/libraries/LIB
.venv/bin/python -m evals.agent_eval freeze-check --overlay OVERLAY --profile PROFILE --library LIB --run-plan RUN_PLAN
.venv/bin/python -m evals.agent_eval run-arm --arm baseline --solution-id BASE_SHA \
  --overlay OVERLAY --profile PROFILE --library LIB --run-plan RUN_PLAN --results-dir EXP_DIR
.venv/bin/python -m evals.agent_eval run-arm --arm candidate --solution-id CANDIDATE_SHA \
  --overlay OVERLAY --profile PROFILE --library LIB --run-plan RUN_PLAN --results-dir EXP_DIR
.venv/bin/python -m evals.agent_eval compare EXP_DIR --policy GATE_POLICY
.venv/bin/python -m evals.agent_eval export-langsmith EXP_DIR
```

## 9. Final Validation

- [ ] 运行以下测试，全部经过 test 分支 wrapper：
  - 新增的 evaluator 单元/集成测试
  - meeting_gold 的全部测试
  - natural_language 测试
  - Agent trajectory/streaming/diagnostics 隐私测试
  - 完整 pytest
- [ ] 运行 overlay/profile/freeze/run-plan 的离线命令，确认外部调用为 0；检查 artifact schema 往返和 hash 确定性。
- [ ] 运行 Alembic single-head/current preflight。本设计预计不新增 DB migration；如果确实需要，返回规划阶段。
- [ ] 如果新增的 overlay/profile/policy YAML 需要随包分发，检查 package data。修改用户已有的 dirty `pyproject.toml` 时，只做最小增量。
- [ ] 更新 `evals/agent_eval/README.md`，并在 `evals/meeting_gold/README.md` 中加交叉引用。
- [ ] 对最终的 report、log、SSE 和 review 路径做敏感 sentinel 扫描。
- [ ] 对 LangSmith 的 projection、payload 和 receipt 做 allow-list + sentinel 检查。

## 10. Risky Files and Rollback Points

| 区域 | 风险 | 回滚点 |
| --- | --- | --- |
| `evals/meeting_gold/agent_smoke.py`、`production_path.py` | evaluator 包装时改变 `agent-smoke`/`rq1` 的行为 | 只新增可复用函数，不改变现有 CLI 输出；每个里程碑重跑 meeting_gold 测试。 |
| PydanticAI 事件挂钩 / `AgentDeps.tool_event` | 丢失生命周期事件、改变执行或泄露参数 | 挂钩只在 evaluator 进程内生效，默认无操作；public event/diagnostics 的 golden test 出现任何差异即回退。 |
| answer pipeline 的 usage 汇总 | 算错成本或改变 budget | 只读 snapshot，不共享或重置 `RunUsage`；缺失时为 unknown。 |
| library 构建 / 故障注入 | 污染 Neon test 分支的共享状态 | 只连 test 分支、使用专属租户、attempt 独立 thread、finally teardown；library 带 checkpoint，可重建。 |
| overlay / no_answer 负例 | 负例其实有答案；结果出来后调整阈值 | 人工审核证明进入 hash；任何变化都生成新的 revision，并重跑两臂。 |
| `langsmith_export.py` | 自动 tracing 泄露、上传影响 p95、远端结果反向影响 gate | 评测后独立命令、清理 ambient env、strict projection、receipt/对账；source artifact 只读。 |
| `pyproject.toml` | 与用户现有未提交改动冲突 | 实施前先 diff，只做最小 patch。 |

## 11. Completion Checklist

- [ ] PRD AC0–AC13 全部由自动化测试或可审计的 artifact 证明。
- [ ] AC12 至少完成真实的 baseline health report；如果有 candidate，完成全量配对、judge、人工抽检和三态 gate。
- [ ] AC13 的真实上传只在 export profile 获批后执行。
- [ ] 没有把 synthetic/fake/dry-run 数字，或 2026-10 的非冻结历史运行，写成 evaluator 的正式指标。
- [ ] evaluator 发现的产品缺陷都已单独记录，没有夹带未规划的 Agent 行为改动。
- [ ] 最终检查、spec 更新、提交和 Trellis 归档按 workflow 单独执行。
