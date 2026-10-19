# 整理状态：执行计划

前置阅读：`prd.md` 写需求和验收，`design.md` 写技术方案。本文件只写顺序、验证命令、评审关口和回滚点。设计上有疑问，以 design.md 为准；design.md 也没写到的，停下来问主会话，不要自己发挥。

## 工作位置与规则

- **在独立 worktree 里开发**：`.claude/worktrees/stashseek-ingest-status`，分支 `feat/stashseek-ingest-status`，从 `dev` 切出。不要在共享的 dev 检出目录里改代码。
- **代码由 Sonnet 子代理（`trellis-implement`）编写，子代理一律不提交。** 主会话负责评审 diff、跑验证，并在 worktree 分支上提交，最后落到 `dev`。合并到 `main` 要等用户明确要求。
- **数据库安全（必须遵守）**：直接运行 `pytest` 会连到 `.env` 里的生产库。worktree 里没有 `.env`，但每个要跑测试的 shell 都先执行下面两行，作为保险：

  ```bash
  export DATABASE_URL="$(grep '^DATABASE_URL=' /Volumes/PeeB/projects/notebook-agent/.env.neon-test | cut -d= -f2- | tr -d "\"'" | sed -E 's#^postgres(ql)?(\+[a-z0-9]+)?://#postgresql+psycopg://#')"
  case "$DATABASE_URL" in postgresql+psycopg://*@ep-falling-fog-*) ;; *) echo "refusing: DATABASE_URL is not the test branch"; exit 1;; esac
  ```

- **Python 环境**：用 worktree 自己的 `.venv`（`uv venv` 加 `uv pip install -e '.[dev]'`）。用 `.venv/bin/python -m pytest` 跑测试，并确认 `.venv/bin/python -c "import app; print(app.__file__)"` 打印的是 worktree 里的路径。
- **插件环境**：在 `extension/` 下执行 `pnpm install --frozen-lockfile`。
- **不要动的东西**：生产环境的 ASR 开关、任何生产数据，以及 design.md 第 9 节以外的重构。

## 阶段 A：后端（一个子代理）

按顺序做。每一步完成后跑对应的测试。

- [ ] **A1 迁移和模型**（design 4.1）
  - 先运行 `alembic heads`，确认 head 只有一个，再新建迁移文件，接在当前 head 后面：
    - 新建表 `content_item_stage`，带 design 里写的 CHECK 约束和主键；
    - 在 `ingest_dispatch` 上新增 `asr_handoff_at`；
    - downgrade 删掉这张表和这一列。
  - `app/models.py` 里新增 `ContentItemStage`，并给 `IngestDispatch` 加上 `asr_handoff_at`。
  - 更新 `tests/test_web_auth_migration.py` 里写死的 head。
- [ ] **A2 原因表和阶段注册**（design 2.5、4.6）
  - 新建 `app/ingest/reasons.py`：原因码 → 类别、`retry_useful`；不认识的码归到 `ingestion_failed`，类别为 `error`。
  - 新建 `app/ingest/stages.py`：`KNOWN_STAGES`、`ENRICHMENT_STAGES = ()`，以及 `mark_stage`。
  - 测试：新建 `tests/test_ingest_reasons.py` 和 `tests/test_ingest_stages.py`。
- [ ] **A3 整理流程写入原因码和转写标记**（design 4.2、4.3、4.4）
  - `NeedsExtension` 新增 `code` 字段；B站连接器传入两个具体的码。
  - `process_item` 的三个 needs_* 分支写入 `fail_reason`。
  - `_complete_dispatch` 新增参数 `converge_reason`；交给转写队列失败时写入 `asr_handoff_failed`；`asr_backfill.py` 的发布失败也写入 `asr_handoff_failed`。
  - `_handoff_dispatch_to_asr` 写入 `asr_handoff_at`。
  - `_ASR_CAPTURE_PLATFORMS` 加上 bilibili，`library.py` 里的镜像常量同步改。
  - 测试：在 `tests/test_asr_ingest_flow.py` 及同类文件里补充 ASR 开着和关着两种情况。
- [ ] **A4 推算、重新整理和计数**（design 2.3、2.4、4.5）
  - 新增 `project_reason`、`project_body_stage`、`project_stages`。
  - `available_actions` 和 `retry` 的校验改成新规则；`submission.py` 里 Agent 和 MCP 的重试校验同步修改。
  - DTO 填充 `stages`、`reason`、`body_searchable`：后续阶段的行要一次批量读出来；`ContentLibraryService` 新增构造参数 `enrichment_stages`。
  - 新增 `availability_counts`，列表响应加上两个计数。
  - 测试：在 `tests/test_web_library.py`、submission 的相关测试和 PostgreSQL 测试里覆盖 design 6.1 列出的各种情况。旧的 `SimpleNamespace` 假对象不能因此崩掉。
- [ ] **A5 接口和契约**（design 2.6、4.7、5.6 的后端部分）
  - `app/api/library_schemas.py` 新增三个响应模型和新字段，新字段都要有默认值。
  - `CapabilitiesResponse` 新增 `transcription_platforms`，并在运行时赋值；更新 `tests/test_web_api_public.py`。
  - 在 `extension/` 下运行 `pnpm generate:api`。
  - 在 `extension/package.json` 新增脚本 `generate:types`，用 openapi-typescript 7.13.0 从 `src/api/openapi.json` 生成 `src/api/schema.d.ts`，然后运行它。确认 `pnpm check:api` 能通过。
- [ ] **A6 自查**：按下面的“后端验证”一节全部跑一遍，把结果原样写进回报。

**后端验证**（在 worktree 根目录执行，先跑上面的数据库保险两行）：

```bash
.venv/bin/python -m pytest -q tests/test_ingest_reasons.py tests/test_ingest_stages.py tests/test_web_library.py tests/test_asr_ingest_flow.py tests/test_web_api_public.py tests/test_library_extension_transport.py tests/test_web_auth_migration.py
.venv/bin/python -m pytest -q tests/test_extension_auth.py tests/test_browser_companion_api.py tests/test_web_api_runtime.py tests/test_web_api_app.py tests/test_mcp_server.py   # CI 跑的契约测试
.venv/bin/python -m pytest -q -x --ignore=evals   # 全量测试；和基线对比，只允许出现基线里已有的失败
.venv/bin/alembic heads    # 必须只有一个 head
(cd extension && pnpm check:api)
```

**关口 A（主会话负责）**：
1. 逐个文件读 diff，对照 design 第 2 节和第 4 节检查。
2. 在测试库上跑一遍迁移的 upgrade 和 downgrade。
3. 用上面的命令重跑一遍测试，和基线对比。

通过以后，在 worktree 分支上提交一次，作为**回滚点 A**。没通过就退回给子代理修。

## 阶段 B：插件（一个子代理，等关口 A 通过后再开始）

- [ ] **B1 共享模块** `extension/src/ingest/`（design 5.1）
  - 新建 `ingestCopy.ts`、`ingestModel.ts`、`IngestProgress.tsx`、`IngestUnfinished.tsx`、`ingest.css`。
  - 每个文件都要有测试。要覆盖：不认识的 lifecycle 和阶段 key 不会崩溃；skipped 的阶段不显示；服务器没返回 `stages` 时能退回推算。
  - `contracts.ts` 新增 `IngestStage`、`IngestReason`、`IngestReasonCategory` 三个别名；`LibraryItemSummary` 的 `Pick` 里加上新字段。
- [ ] **B2 侧栏收藏流程**（design 5.2）
  - 修改 `collectModel`：读取新字段，改轮询停止的条件，`StoredCollect` 和 `parseStored` 加上新字段。
  - `useCollect` 新增在侧栏里重新整理；`login_required` 时「重新收藏」直接调用 `collect()`；「仅查标题 / 备注」打开资料库的搜索。
  - `deps` 新增 `retryItem`。
  - `QaThread` 渲染新的画面。
  - `steps.ts` 删掉 `FAILURE_REASON` 和 `NEEDS_ACTION_REASON`，并修改退回链接导入时的文案。
  - 更新 `SidePanelCollect.test.tsx`、`collectModel.test.ts`、`steps.test.ts`。
- [ ] **B3 资料库**（design 5.3）
  - `LibraryPage` 读取 URL 参数 `search` 和 `focus`。
  - `VideoCard` 显示状态标签、原因短句和“正文不可检索”标记。
  - 修改筛选项的文字。
  - 用 `ingestCopy` 替换 `lifecycle.ts`，并加上兜底。
  - 补测试。
- [ ] **B4 详情页**（design 5.4）
  - 没整理完时显示 `IngestUnfinished`。
  - 新增 `StageRegion`。
  - 显示“正文不可检索”标记；重新整理遇到 429 时显示额度不足的文案。
  - 删掉限流文案。
  - 补测试。
- [ ] **B5 页面问答提示**（design 5.5）
  - `SidePanel` 读取 `transcription_platforms` 并传给 `PageWorkspace`，按平台显示两种不同的文案。
  - 补测试。
- [ ] **B6 自查**：按下面的“插件验证”一节全部跑一遍，把结果原样写进回报。

**插件验证**（在 `extension/` 下执行）：

```bash
pnpm typecheck && pnpm lint && pnpm test && pnpm check:api && pnpm build && pnpm run audit
```

**关口 B（主会话负责）**：
1. 读 diff，对照原型 `reference/prototype.html` 的 P01b、P01c、D02 三页检查画面结构和文案。
2. 用 vite 预览或测试快照，检查侧栏宽度（约 420px）下的显示效果。

通过以后，提交一次，作为**回滚点 B**。

## 阶段 C：全量检查和收尾

- [ ] 派一个 `trellis-check` 子代理（Sonnet），对照 prd、design 和 spec 检查整个 diff；需要修改代码时，由它自己修。
- [ ] 主会话再跑一遍“后端验证”和“插件验证”。
- [ ] 更新 spec（Trellis 第 3.3 步，见 design 第 10 节）。
- [ ] 落到 `dev`：先把分支 rebase 到最新的 `dev` 上，再按路径落地。共享检出目录里如果有其他会话的修改，按记忆里“update-ref 加 apply”的流程处理。不推送 `main`。
- [ ] 请用户在真实插件里实测：
  - 无字幕视频收藏后显示“语音转写中”；
  - 转写失败时的画面；
  - B站登录字幕的提示；
  - 在侧栏里重新整理；
  - 「仅查标题 / 备注」；
  - 页面问答里的新提示。

## 回滚

- 代码：回到回滚点 A 或 B 对应的提交，或者直接丢掉 worktree 分支。
- 数据库：迁移只新增了表和列，回滚代码后可以不动；需要的话执行 `alembic downgrade -1`。本任务期间 `content_item_stage` 里没有数据。
- 插件：旧版插件和新后端兼容，所以插件不用跟着回滚。
