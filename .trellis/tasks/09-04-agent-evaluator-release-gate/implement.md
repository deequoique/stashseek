# Agent Benchmark 实施计划

> 2026-10-07 二次修订：单次 benchmark。之前的计划存档在 `research/archived-release-gate-plan-2026-10-07/implement.md`。

## 开工前

- [x] 用户确认 Q1–Q3，全部按建议（2026-10-07）。
- [x] 确认结果已写进 PRD"已确认"一节。
- [x] 用户批准开始实施（2026-10-07）。真实模型调用（正式运行）仍需在第 6 步前再次确认。

## 0. 准备被测版本

- [ ] 前置条件：评测代码 `evals/meeting_gold/` 和它的测试，目前只在 dev 上、没有提交，属于任务 09-12。具体做法：
  - 从 main 拉一个分支，只放进这些评测文件，不带 dev 上的其他改动。
  - 在这个分支上确认 meeting_gold 的已有测试能通过。

  提交前要征得用户同意。
- [x] 确认 `data/meeting_gold/final` 是唯一使用的题库目录（`DEFAULT_RQ1_CASES`），dataset_hash `4aeccba…`。
- [x] 准备一个基于 `origin/main`（`7418262`）的 worktree，作为开发和运行目录。2026-10-07 已完成，位置 `.runtime/worktrees/agent-benchmark`（沿用仓库惯例），分支 `feat/agent-benchmark`：
  - 把 `evals/meeting_gold/`、`tests/test_meeting_gold*.py`、`tests/fixtures/meeting_gold/` 从 dev 复制过去，保持 untracked，等用户同意后再提交。
  - `data/`、`.env`、`.env.neon-test` 用软链接指向 dev 目录，这些本来就不入库。
  - 用 dev 的 `.venv`，并确认 `import app` 解析到的是 worktree 里的代码，不是 dev 的。
  - 在 worktree 里跑一遍 meeting_gold 的已有测试，作为 main 代码上的基线：129 passed、1 skipped；natural_language 43 passed。
  - 依赖：dev 只比 main 多 `opencc-python-reimplemented`，所以 dev 的 `.venv` 可用；`import app` 已确认解析到 worktree。

## 1. 题集文件

- [x] 写 `benchmark_spec.py`：题集 schema、校验和 hash，规则见 design §3。
- [x] 生成 `benchmarks/agent-benchmark-v1.yaml`：
  - [x] 具体题：specific-30 的全部 30 题。
  - [x] 概括题：从 val 中有完整证据标注的 `general` 题里，按领域用固定种子抽 10 题。
  - [x] 故障题：从具体题里挑 8 题，写明在第几次 `search_segments` 调用失败，以及错误类型。
- [x] 加 `benchmark validate` 子命令，离线运行，不调用任何外部服务。

测试：题库 hash 不对、题不在 val、没有证据标注、缺审核记录、ID 重复，以上情况都要报错；同样输入两次生成的 hash 相同。

## 2. 补齐轨迹记录（只在评测进程里挂钩）

- [x] 记录模型发出的每个工具请求：包括被参数校验拒绝的、同一次响应里的多个请求，并保留顺序。先确认用哪种方式能做到不改 app 代码（design §5）。
- [x] 记录写答案阶段（规划、分段、恢复）各自的 token。
- [x] 从回答里提取 `[S…]` 标记集合，回答原文不落盘。
- [x] 把模型请求、工具调用事件、后端检索三类记录按调用序号对齐，合成一条有序轨迹；对不上就标"不完整"。
- [x] 故障注入：在 `RecordingServices` 同层，对指定题的指定次调用抛出 `RetrievalUnavailable` 或 `EmbeddingUnavailable`。

测试：
- 用假模型和假服务跑一遍，确认以下情况记录完整、顺序正确：批量请求、参数被拒、跳过、失败后重试。
- 挂钩不改变 Agent 的返回结果。
- 故障只在指定的那次调用上触发。

## 3. 规则检查和指标

- [x] 写 `trace_rules.py`：实现 T1–T8 和工具参数出错率（design §6），全部是纯函数。
- [x] 写 `benchmark_report.py`：有效性指标、稳定性指标、置信区间，输出报告（design §7、§9）。
- [x] 用假轨迹写测试，每条规则都要有正例和反例。至少覆盖以下情况：
  - 改写查询带来新片段（不算空转）；改写查询没带来新片段
  - 合法重试（不算重复）；同参重复
  - 被预算拦下；扩展了没查到过的片段
  - 引用不在检索结果里；`[S…]` 标记和引用对不上
  - 故障后恢复；故障后如实报错；故障后假装成功
  - 轨迹不完整时判"未知"
- [x] **回放历史记录**：用 `data/meeting_gold/runs/2026-10-specific30/05-*` 的 30 条记录离线跑规则。缺少的字段会判"未知"，这是预期结果。人工核对每条不通过的原因是否合理，再确定规则版本 v1。
  - 2026-10-07 回放结果（30 条，dev 代码跑的历史记录）：T1 30 过；T2 29 过 1 败（TS3009c:0 调用了管理工具 `list_saved_items`）；T3 28 过 2 败（ES2009c:0、education_0:4 对未检索到的片段 ID 调用 `get_neighbors`）；T4 27 过 3 未知；T5 30 过；T6 27 过 3 败（3 次被预算拦下）；T7 30 未知（历史记录缺少标记和 URL）；T8 不适用。失败原因已人工核对原始记录，均属实。

## 4. 运行脚本

- [x] 写 `benchmark_run.py` 和 `benchmark run` 子命令。具体要求：
  - 运行前检查：只连测试分支；worktree 干净；记录 commit 和模型配置
  - 入库或复用每场会议的测试账号
  - 每题每次运行一个独立进程、一个新对话
  - 清掉追踪类环境变量
  - 每次运行写一个原始记录文件
  - 中途失败也要写记录，并且能从中断处继续，已完成的运行不重跑
- [x] `--dry-run`：检查题集、入库检查点和环境，不调用模型。
- [x] `benchmark report` 子命令：只读原始记录，生成 `report.json` 和 `report.md`。

## 5.（Q1 确认后）答案正确性打分

- [x] 固定打分模型、提示词和输出格式（`benchmark_judge.py`，prompt 版本 `meeting-benchmark-judge-prompt-v1`，计入 config hash；打分模型在正式运行时用 `--judge-model` 指定）。输入是问题、Agent 的回答、QMSum 参考答案和证据句；输出"正确 / 部分正确 / 错误 / 无法判断"及原因码。
- [ ] 抽 10 题左右人工打分，和 LLM 的打分对比，在报告里写明一致率。
- [x] 回答原文和打分理由只放本地 `data/`（`rationales.json`、`review.csv`），`verdicts.json`/`config.json`/报告只放统计结果。

## 5b. 代码审查结论（2026-10-08）

- 审查代理两次因 watchdog 中断，剩余审查由主会话完成。修复：
  - T7：`ok` 答案没有任何引用时判失败（`ok_answer_without_citations`），原先误判为不适用。
  - `agent_smoke`：去掉 main 不支持的 `multimodal_embedding_provider` 参数；文本守卫补丁改为调用原函数。
  - 报告：`ok` 请求的 token 为 0/0 时记为"未知"（`usage_unreported`）。
  - judge：能读取 `run` 写的 spec 快照；被拒输出的 token 也计入用量。
- 测试：benchmark 相关 410 passed、1 skipped。完整测试集有 10 个失败，与干净 main 上的结果完全一致，属于环境原有问题，与本任务无关。

## 5c. 试跑发现与修复（2026-10-08）

- 试跑两次（`data/meeting_gold/runs/benchmark-trial-20261008`、`benchmark-trial2-20261008`，各 2 题 × 1 次，不计入正式结果）：记录完整、故障按指定位置触发、引用/标记/时间对得上。
- 发现：搜索预算（2 次）用完后，main 的 `prepare_search` 会隐藏该工具，模型仍继续调用，PydanticAI 以 `Unknown tool name` 拒绝。原记录把它当成"参数无效"。修复：记录模块单独标为 `unavailable_tool`；规则里不计入参数出错率、不参与 T3/T4，在 T6 记为 `hidden_tool_called`（`RULES_V1.hidden_tool_call_policy`）。两次试跑的两道题都出现了这种情况。
- 打分模型：`openai:gpt-5.5-2026-04-23`（带日期的固定版本）。它是推理模型，不支持 temperature，所以配置改为固定 `openai_reasoning_effort=medium`、`max_tokens=6000`，不再写一个会被忽略的 temperature。已知限制：PydanticAI 读不到打分请求的 token 用量（记为 0），打分成本不纳入报告。

## 6. 正式运行

- [x] 先用 `--dry-run` 检查（2026-10-07 已通过：commit 7418262、app 未改动、22 场会议待入库、144 次运行），再跑 1 道题试跑，核对记录（见 5c）。
- [ ] 用户批准后正式运行（2026-10-08 已批准并启动，输出目录 `data/meeting_gold/runs/benchmark-20261008`）。
- [ ] 生成报告，人工抽查 5–10 条原始记录，确认报告里的数字和原始记录一致。
- [ ] 报告里把历史参考数字放在旁边对照，并注明来源。
- [ ] 发现的 Agent 问题，各自另建任务。

## 验证命令

```bash
# 题库与题集（离线）
.venv/bin/python -m evals.meeting_gold validate --output data/meeting_gold/final
.venv/bin/python -m evals.meeting_gold benchmark validate --spec evals/meeting_gold/benchmarks/agent-benchmark-v1.yaml

# 单元测试：强制测试分支，不得裸跑 pytest
data/meeting_gold/runs/2026-10-specific30/scripts/run_tests_testdb.sh \
  tests/test_meeting_gold*.py tests/test_meeting_gold_benchmark*.py \
  tests/test_natural_language_evaluator.py tests/test_natural_language_quality.py

# 运行（需要用户批准真实调用）
.venv/bin/python -m evals.meeting_gold benchmark run --spec SPEC --output data/meeting_gold/runs/benchmark-<date> --dry-run
.venv/bin/python -m evals.meeting_gold benchmark run --spec SPEC --output data/meeting_gold/runs/benchmark-<date>
.venv/bin/python -m evals.meeting_gold benchmark report --output data/meeting_gold/runs/benchmark-<date>
```

`run_tests_testdb.sh` 在不入库的 `data/` 目录下。如果不可用，等价做法是：从 `.env.neon-test` 读取连接串，断言它的 host 不是 `.env` 里的生产 host，再显式设置 `DATABASE_URL`。

## 完成标准

- [ ] PRD 的 AC1–AC9 全部有测试或实际产物证明。
- [ ] 报告里没有用假数据或试跑数据冒充正式结果。
- [ ] app 代码没有为评测而修改。
- [ ] 最终检查、spec 更新、提交和归档，按 Trellis 流程单独执行。
