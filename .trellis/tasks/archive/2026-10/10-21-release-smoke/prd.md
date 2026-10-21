# 完整栈冒烟与 release 分支合并

父任务 `10-21-release-readiness` 的第 4 个子任务，依赖子任务 1、2、3 全部提交。本文写需求、验收与执行清单；合并的冲突处理策略写在 `design.md`。

## 目标

把 dev 整体发布到 main 并部署到生产：发布前在本地用 Neon 测试分支把 dev 完整栈冒烟一遍；release 分支的 PR 过 CI；部署后网页版、插件、API、MCP、隐私页都可用；生产执行 `reindex`；留有可执行的回滚方案。

## 已知事实（2026-10-21）

- 生产主机 `ssh ubuntu@51.79.159.110`，单元与 Caddy 主机管理；部署脚本按 `origin/main` 的 SHA 建 release 目录（现在会在里面构建 `extension/`，并建 `web/dist -> ../extension/dist` 链接）、跑迁移单元、按所有权重启各单元；Production gate 由 owner 放行。部署手册：`docs/how-to/deploy-production.md`、`docs/operations/production/ovh-caddy.md`（含一次性主机步骤：单元 `WEB_STATIC_DIR` 改为 `extension/dist`，可部署后做）。
- 生产 Alembic 在 `d04fdae36884`；release 会应用 `cff884c74a1e`（入库阶段表、`ingest_dispatch.asr_handoff_at`）、`e2f3a4b5c6d7`（外部登录表与列）、`f8a9b0c1d2e3`（Bilibili 三列、检查约束、索引）。三者都有 `downgrade()`。
- `git merge dev` 到 `origin/main` 的干跑（2026-10-21，dev 8b7c12a）有 33 个冲突文件：`web/src/*` 11 个（main 改过、dev 已删）；`.github/workflows/extension-api-contract.yml`、`web-auth-contract.yml`（dev 删）；`deploy/systemd/notebook-agent.service`；`extension/manifest.json`、`extension/package.json`（main 0.2.2，dev 0.2.1）；`extension/src/{app/browserCompanion.ts,auth/LoginPage.test.tsx,protocol.ts,showcase/ShowcasePage.test.tsx,sidepanel/PageWorkspace.tsx,sidepanel/SidePanelCollect.test.tsx,sidepanel/deps.ts,sidepanel/manifest.test.ts,videos/VideoDetailView.tsx}`；`.trellis/spec/backend/{browser-companion-capture,index,web-browser-runtime}.md`、`.trellis/spec/frontend/index.md`；`README.md`、`README.zh-CN.md`、`docs/how-to/README.md`、`docs/how-to/run-full-library.md`。
- 本地有 docker-compose 的 Redis（已 PONG）和 MinIO（`localhost:9000` 健康），可以起完整栈；数据库必须用 Neon 测试分支。登录：生产要求 `EMAIL_PROVIDER` 为 `resend` 或 `smtp`，本地开发模式下允许为空，验证码的获取方式以 `app/web_auth.py` 开发路径为准。
- 没上过线的功能：入库状态（6c3f958）、外部登录（默认关闭）、Bilibili 收藏夹导入（`BILIBILI_PUBLIC_ENABLED` 默认 false）、检索修复（58326da）、引用按钮（26d4267）、网页入口恢复（d4bc689）、WeMM 隔离（8b7c12a）。
- 插件商店版本是 main 的 0.2.2；本次发布不发新插件版本。

## 需求

### R1 本地完整栈冒烟（测试分支）
用 launcher 或等价方式起 API/MCP、worker、beat（ASR worker 可选）与网页版，`DATABASE_URL` 指向测试分支，走一遍并记录结果：
1. 网页版：首页、登录、视频库、聊天页；`/privacy`；`/api/v1/health`。
2. 保存一个 YouTube 视频（有字幕的），看入库状态的阶段展示到 ready；视频库出现；对它提问，回答带引用按钮，悬停有摘录，点击链接正确；"引用 N" 开关可收起。
3. 中文问题检索：对测试分支上已有的中文条目提问，确认候选池含标题行引用（`标题与简介`）。
4. 插件开发版（`extension/dist` 装进 Chrome）：侧边栏页问答、工作区与网页版一致。
5. MCP：一次 `ask_stashseek` 问答，文本仍是 `[S…]`。
6. 关闭的功能保持关闭：`MULTIMODAL_RETRIEVAL_ENABLED`、`WEMM_ENABLED`、`BILIBILI_PUBLIC_ENABLED`、外部登录三家。
7. `python -m app.cli reindex --dry-run` 在测试分支上数量合理，再实际执行一次确认幂等。

### R2 release 分支与 PR
- 从 `origin/main` 切 `release/2026-10-dev-sync`，`git merge dev`，按 `design.md` 的策略解决 33 个冲突；合并后在分支上跑子任务 3 的全套命令和 CI 的 pytest 子集。
- 推送分支、开 PR（标题注明 dev 整体同步、列出七项变更与三条迁移、回滚方案），CI 全绿。推送和开 PR 前征得用户同意。
- 合并由用户操作或同意后操作；Production gate 由用户放行。

### R3 部署后核对与 reindex
- 发布手册的部署后检查：`/`、`/privacy`、`/api/v1/does-not-exist` 为 JSON 404、登录、聊天一次、插件一次、MCP 一次；生产 `alembic current` 为 `f8a9b0c1d2e3`；日志无启动错误。
- 生产主机执行 `python -m app.cli reindex --dry-run`，数量符合（约 149 个中文片段、131 条标题行，以当时为准），再执行 `reindex`；用 `research/compare_fusion_tenant211.py`（`10-20-cn-recall-fusion`）补部署后结果。
- 一次性主机步骤（单元 `WEB_STATIC_DIR=extension/dist`）在部署后做，记录到手册。

### R4 回滚方案（写进 `research/rollback.md`，发布前完成）
- 应用：把 `/opt/notebook-agent/current` 切回上一个 release 目录并重启单元（部署脚本的 release 目录保留）。
- 数据库：`alembic downgrade d04fdae36884`（顺序 `f8a9b0c1d2e3 -> e2f3a4b5c6d7 -> cff884c74a1e`），在停写窗口内执行；说明哪些数据会丢（入库阶段表、外部登录表、Bilibili 三列）。
- 标题行与中文 `fts` 是普通行/列数据，旧代码忽略，不需要回滚。

## 验收标准
- [ ] AC1 冒烟清单 R1 的 7 项每项有结果与截图/日志，记录在 `research/smoke-<date>.md`。
- [ ] AC2 release 分支在本地全套命令和 pytest 子集全绿；PR CI 全绿。
- [ ] AC3 部署后 R3 的检查全部通过，`reindex` 完成，`tenant211-compare.md` 补上部署后结果。
- [ ] AC4 `research/rollback.md` 存在且在发布前经用户看过。
- [ ] AC5 父任务的跨子任务验收全部勾选。

## 不在范围
- 发布新的插件商店版本（manifest/package 版本取 main 的 0.2.2，不升）。
- 恢复 `web-auth-contract.yml`。
- 开启任何默认关闭的功能。
