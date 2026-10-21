# 执行计划：冒烟与 release 合并

主会话亲自操作涉及生产和推送的步骤；本地冒烟与合并解决可派 `trellis-implement`（派发提示第一行 `Active task: .trellis/tasks/10-21-release-smoke`）。

## 0. 前置
- [x] 子任务 1、2、3 已提交 dev；`git log --oneline -5 dev` 核对。
- [x] 测试分支环境变量（只连 Neon 测试分支）；本地 Redis/MinIO 在跑（`redis-cli ping`、`curl localhost:9000/minio/health/live`）。
- [x] `cd extension && corepack pnpm build` 产出 `dist/index.html`。

## 1. 本地完整栈冒烟（R1）
- [x] 起完整栈（profile 或分别起），网页版 8766 或 profile 端口。
- [x] 按 R1 的 7 项逐条做，每项记结果、截图或日志到 `research/smoke-<date>.md`；失败项先修（回到对应子任务）再继续。
- [x] `python -m app.cli reindex --dry-run` 与实际执行一次（测试分支），记录数量与幂等结果。

## 2. release 分支（R2）
- [x] `git fetch origin main && git worktree add ../release-dev-sync origin/main -b release/2026-10-dev-sync`，在 worktree 里 `git merge dev`，按 `design.md` 第 1 节解决 33 个冲突；`git diff dev --stat` 核对只剩预期差异。
- [x] 在 worktree 里：`corepack pnpm install --frozen-lockfile`，子任务 3 的全套命令（`check:api`、`typecheck`、`lint`、`test`、`build`、`run audit`），`alembic heads`，CI 的 pytest 子集（干净环境）；全部退出 0。
- [x] 写 `research/rollback.md`（R4），请用户过目。
- [ ] **征得用户同意后**：`git push -u origin release/2026-10-dev-sync`，`gh pr create` 到 main（正文：七项变更、三条迁移、一次性主机步骤、回滚方案）；用 ccd_pr 工具绑定并读 CI。
- [ ] CI 全绿后由用户合并、放行 Production gate。

## 3. 部署后（R3）
- [ ] 发布手册的部署后检查逐项做并记录；生产 `alembic current`。
- [ ] 生产主机：`python -m app.cli reindex --dry-run` → 数量符合 → `reindex`；记录输出（只有数量和 ID）。
- [ ] 运行 `10-20-cn-recall-fusion/research/compare_fusion_tenant211.py`（dev 代码此时与生产 schema 一致），补 `tenant211-compare.md`。
- [ ] 一次性主机步骤：单元 `WEB_STATIC_DIR=extension/dist` + `daemon-reload`，手册打勾。
- [ ] 父任务跨子任务验收打勾；`/trellis:finish-work` 归档父任务与四个子任务。

## 风险
| 风险 | 处理 |
|---|---|
| 合并漏掉 main 独有改动 | 合并后 `git diff origin/main --stat` 逐文件看 main 侧的删改是否有意 |
| 部署脚本在主机上构建失败（Node/corepack） | 发布前 `sudo -u notebook-agent corepack pnpm --version`、`node --version` |
| 迁移在生产耗时 | 三个迁移都是建表/加可空列/小表索引，预计秒级；部署脚本先停写 |
| 旧插件 0.2.2 对新服务器 | 接口只增不改；`[^n]` 标记在旧插件显示为文字 |
