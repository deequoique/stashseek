# 完整栈冒烟与 release 分支合并

父任务 `10-21-release-readiness` 的第 4 个子任务，依赖 1、2、3。待规划（本文只记范围与已知事实）。

## 已知事实
- 生产主机：`ssh ubuntu@51.79.159.110`，单元与 Caddy 配置主机管理；部署脚本按 `origin/main` 的 SHA 建 release 目录、跑迁移单元、重启各单元；Production gate 由 owner 放行。
- 生产 Alembic 在 `d04fdae36884`；发布会应用 `cff884c74a1e`、`e2f3a4b5c6d7`、`f8a9b0c1d2e3`（`c9d0e1f2a3b4` 由子任务 2 处理）。
- 没上过线的功能：入库状态（6c3f958，备忘"live smoke + main merge pending"）、外部登录（默认关闭）、Bilibili 收藏夹导入（`BILIBILI_PUBLIC_ENABLED` 默认 false）、检索修复、引用按钮。
- 部署后要在生产执行 `python -m app.cli reindex --dry-run` 再 `reindex`（`10-20-cn-recall-fusion`），并补 `research/tenant211-compare.md`。

## 范围
- 用 Neon 测试分支在本地或临时环境把 dev 完整栈起起来（API、worker、beat、ASR worker 可选、网页版、插件开发版、MCP），走一遍：登录、保存视频、入库状态、聊天与引用按钮、视频库搜索、MCP 问答；Bilibili 导入保持关闭。
- 从 main 切 `release/<date>-dev-sync`，合并 dev，解决冲突（预期主要是 `.github/workflows`、`deploy/`），开 PR，CI 全绿，请用户放行部署。
- 部署后核对：网页版、插件、API、MCP、`/privacy`；跑 `reindex`；记录回滚方案（切回上一个 release 目录、Alembic 降级顺序）。

## 验收（草案）
- [ ] 冒烟清单每项有结果记录在 `research/`。
- [ ] PR 合并、部署成功、reindex 完成、`tenant211-compare.md` 补上部署后结果。
