# 执行计划：标题入索引、RRF 融合、中文词法

需求见 `prd.md`，设计见 `design.md`。按顺序执行；每个带"门"的步骤通过后再往下。实现由 `trellis-implement` 子代理（model sonnet）完成，检查由 `trellis-check` 完成，每次派发的提示第一行写 `Active task: .trellis/tasks/10-20-cn-recall-fusion`。

## 0. 前置

- [x] 确认 `.env.neon-test` 指向 Neon 测试分支（主机 `ep-falling-fog-azrht8ir-pooler…`，与 `.env` 的 `ep-royal-cherry…` 不同；文件里的值带双引号且 scheme 要改成 `postgresql+psycopg://`）。所有 pytest 和评测命令都先执行：

```bash
export DATABASE_URL="$(grep '^DATABASE_URL=' .env.neon-test | cut -d= -f2- | tr -d "\"'" | sed -E 's#^postgres(ql)?(\+[a-z0-9]+)?://#postgresql+psycopg://#')"
case "$DATABASE_URL" in postgresql+psycopg://*@ep-falling-fog-*) ;; *) echo "not the test branch"; exit 1;; esac
```

- [x] 在测试库验证 `SELECT to_tsvector('simple', '电脑 脑价 ddr5')` 得到 3 个词元；结果已记到 `research/zh-fts-check.md`（`websearch_to_tsquery('simple', 'a OR b')` 可用，不需要改 `to_tsquery`）。

## 1. 检索后端（纯函数，先做）

- [x] 新建 `app/retrieval/fusion.py`：`FusionConfig`、`fuse_rrf`、`fuse_raw`、`select_window`（契约见 `design.md` 3.1）。`fuse_rrf` 的去重和名次逻辑从 `evals/meeting_gold/retrieval_study.py` 的 `fuse_rrf`/`fuse_single` 迁移过来，不要复制第二份。
- [x] 新建 `tests/test_retrieval_fusion.py`：同一片段双路命中得分相加；单路为空；权重 0 等价于关闭该路；k 变化只影响尺度不影响顺序；`select_window` 每视频上限、总数上限、`per_item_cap >= limit` 等价不限制；排序确定性（并列按 segment_id）。
- [x] 新建 `app/retrieval/zh_tokens.py`：`cjk_tokens`、`zh_tsvector`、`zh_tsquery`（契约见 3.2）。
- [x] 新建 `tests/test_zh_tokens.py`：双字、单字段、ASCII 保留与小写、助词双字跳过、去重、64 上限、空字符串、全标点。
- [x] `app/retrieval/search.py`：`Hit` 加 `boundary_kind: str = ""`；`_hits` 填充；`bm25_search` 中文分支改为 `fts @@ zh_tsquery` 按 `ts_rank_cd` 排序，去掉 `lang LIKE 'zh%'`、`%`、`similarity`、ILIKE；英文分支不动。
- [x] 新建 `tests/test_bm25_search_zh.py`（仿 `tests/test_vector_search_filter_first.py` 的编译 SQL 断言）：中文查询编译出的 SQL 含 `websearch_to_tsquery('simple'` 和 `ts_rank_cd`，不含 `similarity`、`ILIKE`、`lang LIKE`；英文查询 SQL 不变。

**门 1**：上述测试通过。

```bash
.venv/bin/python -m pytest -q tests/test_retrieval_fusion.py tests/test_zh_tokens.py tests/test_bm25_search_zh.py tests/test_vector_search_filter_first.py
```

## 2. 配置与服务层

- [x] `app/config.py`：`RETRIEVAL_*` 设置（六个环境变量，见 3.1 表）与 `__post_init__` 校验；`.env.example` 加注释行。
- [x] `app/agent/types.py`：`Citation.kind: Literal["segment", "title"] = "segment"`（从门 3 提前到这里，因为 `_citation` 需要它）。
- [x] `app/bootstrap.py:97`：从 `Settings` 构造 `FusionConfig` 传入 `KnowledgeServices(..., fusion=...)`。
- [x] `app/agent/services.py`：
  - `KnowledgeServices.__init__` 接收 `fusion: FusionConfig | None`，默认 `FusionConfig()`；
  - `search_segments(query, *, limit, item_id=None)`（签名不变）：取词法和向量候选（候选池大小来自配置）→ `method == "rrf"` 走 `fuse_rrf` + `select_window`，`method == "raw"` 走 `fuse_raw` + 原 `_diversify_hits` → 水合不变；`_embed_query` 不变；
  - `_citation`：`boundary_kind == "title"` 时 `kind="title"`、`start_sec=None`、`url=item.url`；
  - `get_neighbors`（锚点和邻居两条查询）与 `open_at` 加 `Segment.boundary_kind != "title"`。
- [x] `tests/test_knowledge_services.py`：现有 diversify 测试改为在 `FusionConfig(method="raw")` 下运行；新增 rrf 默认路径测试（词法低分命中进入窗口、6 个视频可同时出现、每视频上限）、标题行引用投影测试、邻居排除标题行测试。
- [x] 新建 `tests/test_retrieval_config.py`（仿 `tests/test_asr_config.py`）：非法 method、k=0、权重同为 0、上限越界都让 `Settings` 校验失败；默认值与 `design.md` 3.1 表一致。

**门 2**：`tests/test_knowledge_services.py` 与设置测试通过。

## 3. 答案层

- [x] `app/agent/types.py`：`Citation.kind: Literal["segment", "title"] = "segment"`。
- [x] `app/agent/answer_pipeline.py`：`_render_composer_evidence` 对 `kind == "title"` 输出"视频《T》的标题与简介："；`_append_sources` 输出"标题与简介："前缀；`COMPOSER_INSTRUCTIONS` 加一句"标题与简介只说明视频是什么，具体事实以正文片段为准"。
- [x] `tests/test_agent_runtime.py`：标题行的 Composer 渲染与来源区块渲染；`Citation` 序列化含 `kind` 且默认 `segment`。

**门 3**：答案层测试通过，全量 pytest 通过一次。

```bash
.venv/bin/python -m pytest -q
```

## 4. 入库与运维命令

- [x] `app/ingest/tasks.py` 片段插入处：中文条目 `fts = zh_tsvector(part.text)`；标题文本加入同一批向量化（`semantic([title_text, *texts])`），插入 `seq=-1, start_sec=0, end_sec=0, boundary_kind="title"` 的标题行；标题文本构造抽成 `app/ingest/title_row.py`（标题 + 简介前 400 字符），供 CLI 复用。
- [x] `tests/test_tasks.py` / `tests/test_asr_ingest_flow.py`：中文条目 `fts` 非空；每次入库恰好一条标题行；向量化调用数 = 片段数 + 1；标题行计入向量化预算。
- [x] `app/cli.py` 新增 `reindex` 子命令（契约见 3.4），实现放在 `app/ingest/reindex.py`；输出只有数量和条目 ID。
- [x] 新建 `tests/test_reindex_cli.py`：干跑不写库；只补缺失或过期的标题行；`--user-id` 过滤；中文 `fts` 补写不调向量 API；向量 API 失败时该条目跳过并计入 failed、退出码非 0。

**门 4**：相关测试通过。

## 5. 评测接入与回归

- [x] `evals/meeting_gold/retrieval_study.py`：改为从 `app/retrieval/fusion.py` 导入融合函数；`rank_setting` 增加 `per_item_cap`；新增设置 `rrf/AND/cap3`；`r2_equivalence_check` 改为与 `FusionConfig(method="raw")` 下的 `search_segments` 比较，并新增对 `rrf/AND/cap3` 与默认配置 `search_segments` 的等价检查。
- [x] `evals/meeting_gold/production_path.py` 第 372、467 行附近：片段枚举跳过 `boundary_kind == "title"`（统计里单独计数）。
- [x] 在测试分支跑 RQ1（约 35 场会议入库，会调向量 API；入库是幂等的，可复用已有 library）：

```bash
.venv/bin/python -m evals.meeting_gold rq1 --output data/meeting_gold/runs/rq1-20261021-rrf
```

- [x] 写 `research/rq1-compare.md`：`raw/AND/div-on`（旧线上）对 `rrf/AND/cap3`（新线上）的范围 A/B Hit@5、Recall@5、MRR 和配对统计。范围 A 退步超过 0.02 或显著则停下来，回到设计调整 `per_item_cap` 或权重。
- [x] 租户 211 中文对比：把 `research/replicate_pool_tenant211.py` 改为直接调用 `KnowledgeServices.search_segments`，在 `raw` 和 `rrf` 两种配置下输出四个查询的候选池；写 `research/tenant211-compare.md`，其中 430 对「电脑」的结果只记录、不作门槛。部署前只能验证 429（标题行还没补到生产），430 在部署后 `reindex` 完成再补一行结果。

**门 5**：RQ1 门槛通过；429 在四个查询下都进池。

## 6. 规范、文档与收尾

- [x] `.trellis/spec/backend/agent-retrieval-convergence.md`：第 2、3 节改写融合与窗口规则（RRF、`per_item_cap`、无 5 视频上限、配置项、`raw` 回滚开关、标题行引用的渲染与排除）。
- [x] `.trellis/spec/backend/ingestion-chunking-embedding.md`：加"标题行与中文 fts"的入库契约和 `reindex` 命令。
- [x] `.trellis/tasks/10-18-stashseek-search-api/prd.md` 搜索-2 加一句：RRF 融合函数和配置由 `10-20-cn-recall-fusion` 提供，新接口复用。
- [x] 运行 `trellis-check`（规范一致性、测试、lint）。
- [x] 按路径提交到 dev：`git commit -- app/retrieval app/agent app/ingest app/cli.py app/config.py app/bootstrap.py evals/meeting_gold tests .env.example .trellis/spec/backend .trellis/tasks/10-20-cn-recall-fusion .trellis/tasks/10-18-stashseek-search-api/prd.md`。
- [x] 发布（2026-10-21 起由 `10-21-release-readiness` / `10-21-release-smoke` 承接，dev 整体合 main）：部署后在生产主机执行 `python -m app.cli reindex --dry-run` 看数量，再执行 `reindex`，然后补 `research/tenant211-compare.md` 的部署后结果。运维备注：`reindex` 只处理未归档条目，归档条目恢复后需要再跑一次才有标题行和中文 `fts`。 —— 2026-10-21 完成：PR #36 → main `4eefa34` 部署，`reindex` 114 / 122 / 0 失败，对比结果已补

## 风险文件与回滚点

| 文件 | 风险 | 回滚 |
|---|---|---|
| `app/agent/services.py` | 所有知识问答的检索入口 | `RETRIEVAL_FUSION=raw` 恢复旧融合与窗口，不用回代码 |
| `app/retrieval/search.py` 中文分支 | 中文词法命中为 0 时只剩向量（和现在一样） | 回代码 |
| `app/ingest/tasks.py` | 入库主流程多一次向量化和一行插入 | 回代码；`DELETE FROM segment WHERE boundary_kind='title'` 清除标题行 |
| `evals/meeting_gold/retrieval_study.py` 等价断言 | 断言写错会让 RQ1 整体失败 | 先在 `--case-id` 单例上跑通再全量 |

## 开工前检查

- `prd.md` 无阻塞问题；`design.md`、`implement.md` 已被用户确认。
- `implement.jsonl` 和 `check.jsonl` 已有真实条目（已验证通过）。
- `python3 ./.trellis/scripts/task.py start .trellis/tasks/10-20-cn-recall-fusion` 只在用户确认后执行。

## 收尾记录（2026-10-21 归档）

- 上线：dev `58326da` 随 PR #36（main `4eefa34`）部署到生产；生产 `reindex` 已执行并验证幂等。
- 回滚：`RETRIEVAL_FUSION=raw` 可单独回退融合，但会丢掉「电脑相关的视频」对 430 的召回。
- 运维备注保留：归档条目恢复后要再跑一次 `reindex`。
