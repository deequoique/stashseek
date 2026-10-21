# 技术设计：冒烟与 release 合并

## 1. 合并策略（`release/2026-10-dev-sync` ← dev）

| 冲突文件 | 取法 |
|---|---|
| `web/src/*`（11 个） | 取 dev 的删除。网页版已由 `extension/` 构建提供（子任务 1）。合并后确认仓库里没有 `web/` 目录 |
| `.github/workflows/web-auth-contract.yml` | 取 dev 的删除（父任务已决定不恢复） |
| `.github/workflows/extension-api-contract.yml` | 以 dev 为准，逐行对照 main 的改动（main 在 PR #34/#35 后可能调过 Node 版本或步骤），保留两边都需要的步骤 |
| `deploy/systemd/notebook-agent.service` | 以 dev 为准（含子任务 1 的四行 `WEB_*`），对照 main 保证 `WEB_PUBLIC_ORIGIN` 不写进 repo、`BROWSER_COMPANION_ALLOWED_ORIGINS` 取两边并集 |
| `extension/manifest.json`、`extension/package.json` | 版本号取 main 的 `0.2.2`，其余字段以 dev 为准；`pnpm-lock.yaml` 若冲突以 dev 为准后重新 `pnpm install --frozen-lockfile` 验证 |
| `extension/src/*`（9 个） | 以 dev 为准，逐个看 main 的改动是否已在 dev 里（PR #32–#35 是从 dev 移植的，多数已包含）；没包含的（例如商店入口文案）手工并入 |
| `.trellis/spec/*`、`README*`、`docs/how-to/*` | 以 dev 为准，并入 main 独有的段落 |

原则：dev 是事实来源；main 只贡献它独有的发布期改动。合并完成后 `git diff dev --stat` 应只剩版本号与 main 独有段落。

## 2. 冒烟环境
- 数据库：Neon 测试分支（`ep-falling-fog-*`，已在 `f8a9b0c1d2e3`，并已按子任务 2 删除媒体表）。
- Redis、MinIO：本地 docker-compose。
- 启动：优先用 `python -m app.deployment` 的本地 profile（见 `docs/reference/runtime-profiles.md`），否则分别起 `web-server`/`mcp-server`、Celery worker、beat。网页版用 `WEB_SERVE_STATIC=true`、`WEB_STATIC_DIR=extension/dist`（先 `corepack pnpm build`）。
- 登录：开发模式下 `EMAIL_PROVIDER` 可为空，验证码取法以 `app/web_auth.py` 的开发路径为准；不要用生产 SMTP。
- 外部调用：Zhipu（embedding）、DeepSeek（Agent）、YouTube 字幕抓取会真实发生，费用很小；不碰生产库和生产对象存储（MinIO 用本地）。

## 3. 发布顺序
1. 子任务 1–3 已在 dev；本地冒烟通过（R1）。
2. 切 release 分支、合并、解决冲突、本地全套命令与 pytest 子集。
3. `research/rollback.md` 写好，用户看过。
4. 征得同意后推送并开 PR；CI 全绿；用户合并并放行 Production gate。
5. 部署后检查；`reindex --dry-run` → `reindex`；补 `tenant211-compare.md`；一次性主机步骤。

## 4. 回滚
- 应用层：`current` 指回上一个 release 目录，重启单元（30 s 内恢复）。
- 数据库：停写后 `alembic downgrade d04fdae36884`；丢失的数据只有新表/新列里的内容（发布后短时间内可接受）。
- 标题行、中文 `fts`、RRF 都只影响检索排序，旧代码读不到也不报错。
