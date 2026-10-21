# 接回网页入口：构建、部署与服务单元

父任务 `10-21-release-readiness` 的第 1 个子任务，没有前置依赖。本文只写需求和验收；技术方案在 `design.md`，执行计划在 `implement.md`。

## 目标

dev 发布到生产后，`stashseek.deequoique.tech` 的网页版（登录、视频库、聊天、视频详情、账号页）照常可用，由 extension 工作区的构建产物提供，服务启动不再依赖已删除的 `web/` 目录。

## 背景与现状

- main：`web/` 是独立 vite 工程，部署脚本在 `web/` 下 `corepack pnpm install --frozen-lockfile` 和 `corepack pnpm build` 产出 `web/dist`；生产主机的 systemd 单元（`/etc/systemd/system/notebook-agent.service`，主机管理，部署脚本只启停不安装）写着 `WEB_SERVE_STATIC=true`、`WEB_STATIC_DIR=web/dist`、`WEB_PUBLIC_ORIGIN`、`WEB_COOKIE_SECURE=true`、`WEB_FORWARDED_ALLOW_IPS=127.0.0.1`。
- dev（c9a4c0e）：删除 `web/`；页面全部在 `extension/src`（与 main `web/src` 文件一一对应，无缺失）；`extension/workspace.html` 加载 `/src/main.tsx`，这就是网页入口，`App` 用 `chrome.storage` 和 `chrome-extension:` 协议判断是插件还是网页，网页模式走 cookie 登录；`extension/vite.config.ts` 的 `rollupOptions.input` 只有 `workspace.html`、`sidepanel.html`；`extension/index.html` 是没有脚本的空壳；`extension/package.json` 的 `build` = `clean.mjs && tsc && vite build && copy-static.mjs production`，`copy-static` 只写 `dist/manifest.json`；`deploy/scripts/notebook-agent-deploy` 没有任何前端构建；repo 的 `deploy/systemd/notebook-agent.service` 去掉了 `WEB_*` 行；`app/config.py` 的 `WEB_STATIC_DIR` 默认改为 `extension/dist`，`WEB_SERVE_STATIC` 默认 true；`app/api/app.py::_mount_spa` 要求目录里有 `index.html`，否则启动抛 `ValueError`；`docs/reference/configuration.md` 第 290 行写 `WEB_STATIC_DIR` "未使用"，与代码不符。
- 主机上有 Node 与 corepack（main 的部署脚本一直在用）。
- CI（`.github/workflows/extension-api-contract.yml`）在 `extension/` 下跑 `pnpm build`。

## 需求

### R1 构建产出网页入口

- `extension` 的构建额外产出 `dist/index.html`，加载 `/src/main.tsx`，标题"搜藏助手"，favicon 与 `workspace.html` 一致；`workspace.html`、`sidepanel.html` 不变。
- `pnpm build` 一次产出三个入口；CI 的 `pnpm build` 覆盖到。
- 插件 zip 里多出的 `index.html` 和共享的 assets 不影响插件（manifest 不引用它）。

### R2 部署脚本构建前端

- `deploy/scripts/notebook-agent-deploy` 在创建 release 目录、安装 Python 包之后、写 `.release-built` 之前，以 `notebook-agent` 用户在 `$release_dir/extension` 下执行 `corepack pnpm install --frozen-lockfile` 和 `corepack pnpm build`；任一失败则部署脚本失败，不切换 `current`。
- 兼容链接：构建成功后在 release 目录创建 `web/dist -> ../extension/dist` 的符号链接，这样主机单元即使还写着 `WEB_STATIC_DIR=web/dist` 也能启动；主机单元改成 `extension/dist` 后链接仍无害。

### R3 服务单元与文档

- repo 的 `deploy/systemd/notebook-agent.service` 重新声明 `WEB_SERVE_STATIC=true`、`WEB_STATIC_DIR=extension/dist`、`WEB_COOKIE_SECURE=true`、`WEB_FORWARDED_ALLOW_IPS=127.0.0.1`（`WEB_PUBLIC_ORIGIN` 由主机设置，不写进 repo）。
- `docs/reference/configuration.md` 修正 `WEB_STATIC_DIR`、`WEB_SERVE_STATIC` 的说明；部署文档写明前端构建步骤、`web/dist` 兼容链接，以及发布手册里的一次性主机步骤（把主机单元 `WEB_STATIC_DIR` 改为 `extension/dist`，`daemon-reload`，可在部署后做）。

### R4 本地可验证

- 本地以 `WEB_SERVE_STATIC=true`、`WEB_STATIC_DIR=extension/dist` 启动 API：`GET /` 返回 `index.html`；`/assets/*` 可用；`/privacy` 仍是隐私页；`/api/...` 不存在的路径返回 404 而不是 `index.html`；在内置浏览器里以网页模式（非插件）能打开登录页和视频库页壳子。
- 静态部署断言（`tests/test_deployment_cli.py` 一类）覆盖：repo 单元含上述 `WEB_*` 行；部署脚本含前端构建与兼容链接步骤。

## 验收标准

- [ ] AC1 `corepack pnpm build` 后 `extension/dist/` 有 `index.html`、`workspace.html`、`sidepanel.html`，`index.html` 引用的脚本与 `workspace.html` 同源；`pnpm test`、源码 `tsc` 仍通过。
- [ ] AC2 部署脚本语法检查通过（`bash -n`），静态断言测试覆盖构建步骤和兼容链接；脚本在构建失败时以非 0 退出且不切换 `current`。
- [ ] AC3 repo 单元含四个 `WEB_*` 行；文档修正；发布手册有主机单元的一次性步骤。
- [ ] AC4 本地启动验证 R4 的五项，并在内置浏览器截图存到 `research/`。
- [ ] AC5 按路径提交到 dev，不改 main。

## 不在范围

- 页面功能、样式改动。
- 插件 zip 瘦身（排除 `index.html`）。
- 改生产主机单元（写进发布手册，发布时做）。
- 为网页版单独建一套 vite 工程。

## 约束

- dev 分支开发；按路径提交；其它会话可能同时改 `extension/`，提交前核对 `git status`。
- 插件工具链需要 Node 22 + `corepack pnpm`。
- 部署脚本只能用 `notebook-agent` 用户执行构建，与现有写法一致。
