# 发布准备：dev 合 main 前置（父任务）

父任务只管来源需求、任务图、跨子任务验收和最终集成评审，不直接实现。

## 目标

把 dev 上已完成的工作（检索修复 `58326da`、引用按钮 `26d4267`、入库状态、Bilibili 收藏夹与分P、外部登录基础、认证修复等）安全地发布到 main 并部署到生产，而不中断线上网页版、API、插件和 MCP。

## 来源

- 2026-10-20 的发布评估（本会话）：dev 比 main 多 84 个提交（首层 49 个），单独挑提交冲突 11 个文件，其中 4 个文件 main 上不存在，所以必须整体发布。
- 用户 2026-10-21 决定：网页版现在不能删；WeMM 决定更换 embedding 模型，现有半成品不能带着原样上线。
- 发现的阻塞项（按严重程度）：
  1. **服务起不来**：dev 的 c9a4c0e 删除了 `web/` 和部署脚本里的网页构建；extension 构建只产出 `workspace.html`、`sidepanel.html`，没有 `index.html`；生产主机的 systemd 单元（主机管理，部署脚本不改）写死 `WEB_SERVE_STATIC=true`、`WEB_STATIC_DIR=web/dist`；而且 dev 的 `app/api/runtime.py::build_api_app` 根本不读这两个设置，固定传 `static_dir=None`（2026-10-21 子任务 1 实测：服务能起来，`/` 和 `/assets` 返回 404 JSON，API 和 `/privacy` 正常），所以发布 dev 的直接后果是网站消失而不是服务崩溃；把接线恢复成 main 的写法后，`_mount_spa` 找不到 `index.html` 才会抛 `ValueError`。页面代码没有丢：main `web/src` 的全部页面文件在 dev `extension/src` 里都在，`workspace.html` 加载的就是网页入口 `/src/main.tsx`。
  2. **CI 红**：PR 到 main 跑 `.github/workflows/extension-api-contract.yml`（`pnpm typecheck && pnpm lint && pnpm test && pnpm build && pnpm run audit`）。dev 当前 `pnpm typecheck` 42 个错误（`tsconfig.test.json`：FavoritesPicker、PartsPicker、positioning、ChatPage 的测试），`pnpm lint` 3 个错误（`AccountLinkPage.test.tsx`、`api/client.ts` 两处未用导入）。
  3. **4 个迁移**要在生产跑：生产在 `d04fdae36884`；dev 多 `c9d0e1f2a3b4`（WeMM 媒体向量表）、`cff884c74a1e`（入库阶段表）、`e2f3a4b5c6d7`（外部登录）、`f8a9b0c1d2e3`（Bilibili 列）。都是建表、加可空列、小表建索引；测试分支已升到头。
  4. **WeMM 半成品** `3b64d34`：运行时只有 `app/models.py`、`app/retrieval/media.py`、迁移 `c9d0e1f2a3b4`、`openapi.json` 的媒体接口，全部在 `MULTIMODAL_RETRIEVAL_ENABLED`/`WEMM_ENABLED`（默认 false）之后；`tests/test_inspect_media_agent_tool.py` 收集阶段报错。
  5. **未在干净环境验证**：dev 全量 pytest 有 42 个失败，已核对为本地 `.env` 所致，但没有在干净环境证明全绿；入库状态、外部登录、Bilibili 收藏夹导入没上过线。
  6. dev 删除了 main 的 `web-auth-contract.yml` 工作流，合并后它消失。

## 任务图（按顺序）

| 顺序 | 子任务 | 交付物 | 依赖 |
|---|---|---|---|
| 1 | `10-21-web-entry-restore` | extension 构建产出 `index.html`；部署脚本构建前端并提供 `web/dist` 兼容链接；repo 单元与文档更新；本地起 API 能打开网页版 | 无 |
| 2 | `10-21-wemm-quarantine` | 迁移 `c9d0e1f2a3b4` 从链上摘掉或改为空；开关关闭时没有媒体接口和媒体代码路径暴露；收集报错的测试修掉或明确跳过 | 无（可与 1 并行） |
| 3 | `10-21-ci-green` | `pnpm typecheck`、`pnpm lint`、`pnpm test`、`pnpm build`、`pnpm run audit` 在 dev 上全绿；CI 子集 pytest 全绿 | 1、2 完成后再跑一遍 |
| 4 | `10-21-release-smoke` | 用测试分支把 dev 完整栈起一遍冒烟（网页版、插件、API、MCP、入库状态、Bilibili 导入默认关闭）；开 release 分支合 dev、PR、CI、放行部署；部署后 `python -m app.cli reindex` | 1、2、3 |

## 跨子任务验收

- [x] release 分支的 PR 上 `extension-api-contract` 工作流全绿。 —— PR #36：`deterministic-extension-gates` SUCCESS；`deploy-production` 在 PR 上按设计跳过，合并后在 main 运行并由用户放行
- [x] 部署后：网页版（登录、视频库、聊天、视频详情）、插件（工作区、侧边栏）、API、MCP 都可用；`/privacy` 可访问；`/api/*` 不被 SPA 吞。 —— 2026-10-21 已核对：`/`、`#/login` 表单、`/privacy`、`/api/v1/health`、`/api/v1/does-not-exist` 为 JSON 404、离线包下载、聊天页构建块含引用按钮代码；**未在生产实测**登录后的视频库/聊天/视频详情、插件侧边栏、MCP（需邮箱验证码与访问令牌），用户知悉后归档。见 `10-21-release-smoke/research/smoke-2026-10-21.md`
- [x] 生产 Alembic 版本到达 release 头；`reindex --dry-run` 数量符合预期后执行 `reindex`，`research/tenant211-compare.md` 补上部署后结果（`10-20-cn-recall-fusion`）。 —— `alembic current` = `f8a9b0c1d2e3 (head)`，`alembic check` 无差异；干跑 114 / 122 / 0 失败，正式执行同数，再干跑 0 / 0 / 0 跳过 122；对比文档已补「部署后复核」，429/430 四个查询全进池
- [x] 多模态和 WeMM 开关在生产保持关闭；生产没有 WeMM 的表（或为空且不被引用）。 —— 生产环境文件无 `MULTIMODAL_*`/`WEMM_*` 键（默认关闭）；媒体表迁移从未进入 main，`alembic check` 报 No new upgrade operations，库里没有模型之外的表
- [x] 回滚方案写在 `10-21-release-smoke` 里：切回上一个 release 目录与 Alembic 降级顺序。 —— `10-21-release-smoke/research/rollback.md`

## 不在范围

- 更换 WeMM 的 embedding 模型本身（另立任务）。
- 新功能开发。
- 主机单元的长期整理（只做让本次发布不出事的最小改动）。

## 子任务状态（2026-10-21 归档时）

| 子任务 | 提交 | 状态 |
|---|---|---|
| `10-21-web-entry-restore` | dev `d4bc689` | 完成，随 PR #36 上线 |
| `10-21-wemm-quarantine` | dev `8b7c12a` | 完成，随 PR #36 上线 |
| `10-21-ci-green` | dev `90c55a4` | 完成，PR #36 CI 全绿 |
| `10-21-release-smoke` | 合并 `457e6e7`/`b6123be`，PR #36 → main `4eefa34`，生产已部署并补索引 | 完成；主机单元 `WEB_STATIC_DIR` 一次性改动用户决定暂不做（兼容链接可用） |

发布后：`origin/main` 已合回 dev（`d6c91ca`）；插件 0.2.3 发布包另立任务 `10-21-ext-0-2-3-release`（dev `ac7fafb`）。
