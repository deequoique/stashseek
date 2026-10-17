# 会议问答检索评测：test 集全量（RQ1 扩到 281 题）

## Goal

用 QMSum test 集的全部题目重跑 RQ1 检索评测，用更大的样本确认线上检索的召回水平和几个结论。之前的 RQ1 只有 val 集 30 道题（可评分 28 道），置信区间很宽：关闭多样性的改进 p = 0.125。只测检索，不跑 Agent，不调用聊天模型。

要回答的问题：

1. 线上检索在已知会议时（范围 A）、全库 35 场会议时（范围 B）的 Hit@5、Recall@5 和候选池召回率各是多少？
2. 全库检索比已知会议损失多少？损失里有多少来自"认错会议"，有多少来自多样性处理？
3. RQ1 在 val 上的结论，在 281 题上是否成立：关闭多样性有改进；OR / RRF 不显著；排序是瓶颈而不是分块。

## Background（已核实，2026-10-08）

- **题库**：`data/meeting_gold/final`（dataset_hash `4aeccba…`）。
  - test 集 281 题、35 场会议：244 道具体题（specific）、37 道概括题（general）。
  - 按来源：AMI 151 题 / 20 场，议会委员会 72 题 / 6 场，ICSI 58 题 / 9 场。
  - 可评分题（CES citation_scorable）270 道，其余为 answer_only 或 partial，照常检索但不计入指标。
  - test 集没有参与切块参数调优。
  - `qmsum:Bmr023:specific:2` 的参考答案与 val 集中的一题相同（`held_out_eligible: false`）。
- **现有代码**：`evals/meeting_gold/retrieval_study.py`（`rq1` 子命令，dev 工作区，未提交，属任务 09-12）。
  - 写死了 val 的 30 道题（`RQ1_CASE_IDS`）和 val 的 35 场会议（`production_path.val_meeting_ids`）。
  - 已有：
    - 两个范围（A：限定当前会议；B：一个账号装下全部会议）；
    - 36 种设置：融合方式 × AND/OR × 多样性开关 × 范围；
    - 严格包含口径下的 Hit / Recall@1/3/5/10、MRR、nDCG@10、会议命中、理想上限；
    - McNemar、Wilson、bootstrap。
  - 还没有：候选池召回率；按题型（具体 / 概括）分开统计。
- **对照数字**（val RQ1，2026-10-07，n = 28，线上设置）：
  - 范围 A：Hit@5 0.679、Recall@5 0.381；
  - 范围 B：Hit@5 0.250、Recall@5 0.090；
  - 范围 B 关闭多样性后 Recall@5 0.210。
- **口径区别**：PPT 上"Hit@5 0.714、候选池召回率 0.780"来自 Agent 端到端运行，检索词由 Agent 自己写，并且每场会议一个账号。本任务用原题直接检索，两组数字口径不同，不能直接比，报告里要写明。
- **检索相关代码**：dev 工作区的 `app/retrieval/search.py`、`app/ingest/chunker.py`、`app/ingest/tasks.py`、`app/agent/services.py` 与线上 main `7418262` 完全一致。

## Requirements

- **R1 题集**：test 集全部 281 题。指标只算可评分题。具体题、概括题分开出数，也给合并数。`Bmr023:specific:2` 在汇总里剔除并注明。
- **R2 入库**：test 的 35 场会议全部导入同一个测试账号，走线上链路：字幕轨 → `create_item` + `process_item`，使用 main 的切块和向量化代码。范围 A 在这个账号里限定到题目所在会议；范围 B 不限定。
- **R3 设置**：沿用 RQ1 的 36 种设置，并标出线上基线（原始分数混排 + AND + 开启多样性）。不调用聊天模型，只调用 embedding。
- **R4 指标**：
  - 在 RQ1 已有指标之外，新增候选池召回率：50 个向量候选加 50 个关键词候选去重合并后，覆盖了多少金标准句子。
  - 新增"会议定位"拆解：范围 B 前 5 条里有没有正确会议；第一名是不是正确会议；认错时，错认的会议是不是同一来源的相似会议。
- **R5 统计**：每种设置和同一范围的线上基线做配对比较（McNemar 精确检验，Recall 用 bootstrap 置信区间）；比例写成"分子/分母"，并附 Wilson 区间。
- **R6 报告**：
  - 写一份中文报告，和 val RQ1 并排对照，写明 R3 / Background 里的口径区别。
  - 输出可直接用于 PPT 的汇总表：范围 A/B × 具体/概括 × Hit@5 / Recall@5 / 候选池召回率。
- **R7 安全**：
  - 数据库只用 Neon 测试分支，断言 host，不打印连接串；检索阶段用只读会话。
  - 运行数据放 `data/`（gitignored）。

## Acceptance Criteria

- [x] AC1：281 题全部检索完，每题都有记录；缺失题列出原因。可评分题数与题库计数一致（预期 270）。
- [x] AC2：35 场 test 会议在同一个测试账号下，跑完后入库数据完好（片段数和文本 hash 不变）。
- [x] AC3：报告给出 R4、R5 的全部指标，具体题与概括题分开。
- [x] AC4：在 val 的 30 道题上用改动后的代码重跑，线上基线的数字与 2026-10-07 的 RQ1 一致：范围 A Recall@5 0.381、范围 B 0.090。这一条用来证明改动没有改变原有口径。
- [x] AC5：`evals/meeting_gold` 的单元测试通过，测试数据库强制为测试分支；没有修改 `app/`。
- [x] AC6：报告写明与 PPT 上 Agent 端到端数字的口径区别。

## Out of Scope

- 跑 Agent、LLM 裁判、轨迹规则（任务 09-04 负责）。
- 修改检索或切块代码；根据结果调参。发现的问题另建任务。
- train 集。
