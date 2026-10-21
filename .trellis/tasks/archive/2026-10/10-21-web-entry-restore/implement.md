# 执行计划：接回网页入口

实现由 `trellis-implement` 子代理（model sonnet）完成，检查由 `trellis-check` 完成；派发提示第一行写 `Active task: .trellis/tasks/10-21-web-entry-restore`。

## 0. 前置

- [x] 插件工具链：Node 22（`~/.nvm/versions/node/v22*/bin` 放到 `PATH` 前）+ `corepack pnpm`。
- [x] 确认 `app/cli.py` 里启动网页 API 的子命令名和端口参数（`grep -n 'add_parser' app/cli.py`），写进第 3 步的命令。

## 1. 构建入口

- [x] `extension/index.html`：对齐 `workspace.html`（viewport、favicon、`<div id="root">`、`<script type="module" src="/src/main.tsx">`），标题"搜藏助手"。
- [x] `extension/vite.config.ts`：`input` 加 `index.html`。
- [x] `cd extension && corepack pnpm build`，确认 `dist/index.html`、`dist/workspace.html`、`dist/sidepanel.html` 都在，且 `dist/index.html` 引用的 `/assets/*.js` 存在；`corepack pnpm test` 与 `corepack pnpm exec tsc -p tsconfig.json --noEmit` 仍通过。

## 2. 部署脚本与单元

- [x] `deploy/scripts/notebook-agent-deploy`：在 `pip install -e` 之后、`touch .release-built` 之前加前端构建两步和 `web/dist` 兼容链接（见 `design.md` 第 1 节）；`bash -n` 通过。
- [x] `deploy/systemd/notebook-agent.service`：加四个 `WEB_*` 行。
- [x] 找到现有的部署静态断言测试（`grep -rln 'notebook-agent-deploy\|notebook-agent.service' tests/`），补断言：单元含四行；脚本含 `--chdir="$release_dir/extension"`、`corepack pnpm build`、`web/dist`。跑这些测试。

## 3. 本地验证

- [x] 用 `.env`（生产库，只做只读 GET）以 `WEB_SERVE_STATIC=true WEB_STATIC_DIR=extension/dist` 启动 API，按 `design.md` 第 3 节的 curl 逐项核对：`/` 200 html、`/assets` 可用、`/privacy` 200 且不是 index、`/api/不存在` 404。
- [x] 内置浏览器打开首页，确认网页模式界面出现（登录页或视频库壳子），截图到 `research/web-entry-local.png`。不登录、不提交任何表单。

## 4. 文档、检查、提交

- [x] `docs/reference/configuration.md`：`WEB_STATIC_DIR` 改为"网页静态目录，默认 `extension/dist`，需含 `index.html`"，`WEB_SERVE_STATIC` 说明同步。
- [x] 部署文档：前端构建步骤、`web/dist` 兼容链接、发布手册的一次性主机步骤（主机单元 `WEB_STATIC_DIR=extension/dist` + `sudo systemctl daemon-reload`，可在部署后做）。
- [x] 运行 `trellis-check`。
- [x] 按路径提交到 dev（数组展开路径）：`extension/index.html extension/vite.config.ts deploy/scripts/notebook-agent-deploy deploy/systemd/notebook-agent.service docs/reference/configuration.md <部署文档> tests/<静态断言测试> .trellis/tasks/10-21-web-entry-restore`。

## 风险与回滚

| 风险 | 处理 |
|---|---|
| 主机没有 Node 22/corepack | main 的部署脚本一直在用 corepack pnpm 构建 `web/`，环境已具备；发布前在主机 `corepack pnpm --version` 确认 |
| 构建把 `dist` 清空后插件 zip 流程受影响 | `clean.mjs` 本来就清 `dist`，不变 |
| `index.html` 进入插件 zip | manifest 不引用，无害；瘦身不在范围 |
| `_mount_spa` 仍找不到 `index.html` | 部署脚本构建失败会先退出，不会走到重启 |

## 开工前检查

- `prd.md`、`design.md`、`implement.md` 已被用户确认；jsonl 有真实条目。
- `python3 ./.trellis/scripts/task.py start .trellis/tasks/10-21-web-entry-restore` 只在用户确认后执行。
