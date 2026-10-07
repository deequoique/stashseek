# 会议问答质量：QMSum specific-30 诊断、修复与 RQ1 检索研究（2026-10-06 ～ 10-07）

> Canonical copy of the meeting-QA quality report. The eval code it refers to (`evals/meeting_gold/`) is still uncommitted on `dev`, under the in-progress task `09-12-qmsum-explainmeetsum-golden-set`.

本报告记录一次完整的质量排查。原始数据保存在本机
`data/meeting_gold/runs/2026-10-specific30/`（`data/` 不入库）和
`data/meeting_gold/runs/rq1-20261006/`。设计与决策记录保存在 Trellis 任务里：

- `.trellis/tasks/archive/2026-10/10-06-meeting-qa-quality/`（父任务），包含
  `fix-chunk-merge`、`fix-answer-fail-closed`、`fix-embed-transient-errors`、
  `rq1-retrieval-quality` 四个子任务
- `.trellis/tasks/10-06-tenant-vector-filter-first/`
- `.trellis/tasks/10-07-retrieval-agent-budget/`

## 1. 评测设置

- **数据**：QMSum + ExplainMeetSum golden set（`data/meeting_gold/final`），val
  split。固定选取 30 条 `specific` 查询，三个领域各 10 条。按 `ces` 策略，其中
  28 条可以按证据打分。
- **生产链路评测**：`scripts/meeting_agent_prodpath.py`，正式版见
  `evals/meeting_gold/agent_smoke.py`。
  - 会议文本先转成 YouTube json3 字幕：每条 cue 最多 12 个词，换说话人另起一条，
    语速按 2.5 词/秒，换人间隔 0.3 秒。
  - 再走 `create_item` + `process_item` 真实入库。
  - 用 Web 的 `agent.stream` 发问，每次新开对话，每条 case 单独一个进程，数据库
    用 Neon 测试分支。
  - 检索指标按"块"计算：句子只要有一部分落在块里就算覆盖，属于宽松口径。
- **RQ1 检索研究**：`python -m evals.meeting_gold rq1`。
  - 查询直接用原始问题，不经过 LLM。
  - 范围 A：已知是哪场会议。范围 B：35 场会议放在同一个租户里检索。
  - 计分为严格口径：句子必须完整落在块内才算覆盖。
  - 统计方法：配对 McNemar、Wilson 区间、bootstrap 置信区间。

## 2. 端到端结果（同一组 30 条）

| 运行 | 回答成功 | 块级 Recall@5 | 检索池金标准命中 / 覆盖率 | 引用含金标准 | 中位延迟 | 输出 token 超限 |
|---|---|---|---|---|---|---|
| 旧评测（每句一段、强制只查当前会议、非流式） | 19/30 | — | — | — | — | 11 次（含工具调用次数超限） |
| 生产链路·修复前（10-06） | 18/30 | 0.147 | 0.750 / 0.384 | 9/16 | 17.4 s | 10 |
| 生产链路·修复后（10-06） | 30/30 | 0.397 | 0.929 / 0.715 | 0.893 | 13.7 s | 6 |
| ＋检索 Agent 预算·关闭思考（10-07） | 27/30 | 0.364 | 0.889 / 0.663 | 0.808 | 5.8 s | 0 |
| **＋检索 Agent 预算·开启思考（最终）** | **30/30** | — | **0.964 / 0.780** | **0.893** | **9.2 s** | **0** |

修复前的 12 条失败中，有 11 条的检索池里其实已经有金标准证据。失败原因：

- 6 条：流式输出遇到纯空白片段，被误判为违规而中止；
- 3 条：流式规划阶段引用超过 8 个，且没有重试机会；
- 3 条：回答器引用超限或重复引用，三次都直接失败。

## 3. 发现的问题与处理状态

| 问题 | 证据 | 处理 |
|---|---|---|
| 分块在每个标点或语义边界都切，块中位只有 12 词，32% 的块 ≤5 词，从未用上 170 词硬切 | 20 场会议走真实入库实测 | 已修复：先按语义切，下限 80 词、上限 200 词，在窗口内选语义差异最大处切，块间约 15% 整句重叠。修复后中位 155 词 |
| 流式输出遇到空白片段就中止回答 | 可复现，6/30 | 已修复 |
| 引用超过 8 个或重复时直接失败 | 6/30 | 已修复：服务器按段轮流截断，重复引用放行 |
| embedding 遇到网络抖动不重试，视频直接入库失败 | `IncompleteRead` | 已修复：每批最多尝试 3 次 |
| 回答器每段证据只读前 360 字符 | 代码 | 已修复：改为 1200 |
| 全库向量检索先在全局 HNSW 里找近邻再按租户过滤（ef_search=40），候选被截断 | 测试分支 25/30 条拿到的候选不足 10 个；生产上最大的租户只拿到 12–39 个（要求 50 个） | 已修复：先筛出租户的条目再精确排序，并删除全局 HNSW 索引（迁移 `d04fdae36884`）。**生产库尚未执行这次迁移** |
| 检索 Agent 浪费预算：同一步内被跳过的调用也计数；写了一段最终会被丢弃的回答 | 37 次调用被跳过，6 次超限 | 已修复：同一步内的多个检索在预算内全部执行；搜索后只输出"检索完成"。输出 token 上限从 2000 提到 3000 |
| 检索阶段关闭思考后，模型偶尔不搜索就直接作答 | A/B：关闭时 6 次中 4 次失败，开启时 6/6 成功 | 结论：检索阶段保持开启思考 |
| 关键词检索用 `websearch_to_tsquery`（AND 语义），30 条全部零命中；关键词分数与向量分数直接混排 | RQ1 | 未修复，见第 4 节 |
| 全库检索时多样性处理把正确会议的证据挤出前 5 | RQ1 | 未修复，建议下一步处理 |
| 回答中止后，未关闭的流式生成器在同一事件循环里取消了下一轮请求 | 仅在评测脚本中观察到 | 未复现，未排查 |
| 片段中没有说话人信息；笼统问题需要改写查询 | 分析 | 未做 |
| 直接运行 `pytest` 会通过 `.env` 连到生产库 | 2026-10-06 事件 | 暂不修复。规避方式：测试时必须覆盖 `DATABASE_URL`（`scripts/run_tests_testdb.sh`）。已清理事件中残留在生产库的 2 个测试 schema |

## 4. RQ1：检索质量（修复 HNSW 截断后，2026-10-07 重算，n=28）

- **上限**：在当前分块下，理想的 5 个块可覆盖 95.3% 的金标准句子。瓶颈在排序，不在分块。
- **基线**（生产当前做法：原始分数混排 + AND + 开启多样性）：

  | 范围 | Hit@5 | Recall@5 | MRR |
  |---|---|---|---|
  | A | 0.679 | 0.381 | 0.467 |
  | B | 0.250 | 0.090 | 0.248 |

- **范围 B 的最佳设置**：只用向量 + 关闭多样性，Recall@5 = 0.210。
  - 相对基线 Δ = +0.120，95% 置信区间 [0.051, 0.201]，McNemar p = 0.125。
  - RRF + OR + 关闭多样性：Recall@5 = 0.204（Δ = +0.114，p = 0.0625）。
- **范围 A 的最佳设置**：min-max 融合 + OR，Recall@5 = 0.443（Δ = +0.061，置信区间
  [−0.006, 0.137]，不显著）。
- **结论**：
  - 全库检索时关闭多样性处理，是修复截断前后两次实验都成立的改进。
  - OR + RRF 在范围 B 的优势，有一部分是截断造成的假象。
  - Recall@5 偏低的主要原因：
    - 证据分散：每条问题的证据中位分布在 4 个块里，第一个金标准块中位排第
      2.5 名，但所有金标准块中位排第 13 名；
    - 只剩向量这一路信号，人名、地名等专名用不上；
    - 笼统问题的问法和会议原话对不上；
    - 范围 B 里还有相似会议的干扰。
  - "跳过相邻块"这个想法验证后反而更差（0.381 → 0.273），不采用。

## 5. 生产环境事实（2026-10-06 只读核查）

- pgvector 0.8.0，PostgreSQL 17，Alembic 版本 `b8c9d0e1f2a3`。
- 共 60,918 个片段、17 个租户。每租户片段数：中位 325，最多 18,900。
- `ix_segment_embedding_hnsw` 索引占 335 MB。带这个索引插入一行约 2.3 ms，不带约
  0.04 ms。重建 4.1 万行：用 64 MB 维护内存需 140 s，用 512 MB 需 32 s。
- Neon 提供的可选扩展：`lakebase_vector`（ANN，支持预过滤，需 PostgreSQL ≥16）和
  `lakebase_text`（BM25）。单租户数据量接近 5 万片段时，再评估是否引入。

## 6. 复现

| 内容 | 命令 / 脚本 |
|---|---|
| 测试（必须连测试分支） | `data/meeting_gold/runs/2026-10-specific30/scripts/run_tests_testdb.sh <tests>` |
| RQ1 | `python -m evals.meeting_gold rq1 --output data/meeting_gold/runs/<run>`（复用已入库的测试分支数据，只做 DB 读取和 30 次 query embedding） |
| 端到端 | `python -m evals.meeting_gold agent-smoke --output …`（35 场会议共用一个租户）；或 `scripts/meeting_agent_prodpath.py`（每场会议一个租户，与本报告第 2 节同口径） |
| 对比脚本 | `scripts/compare_postfix.py`、`scripts/compare_budget_rerun_final.py` |
