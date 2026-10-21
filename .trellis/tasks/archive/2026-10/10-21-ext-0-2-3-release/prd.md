# 发布插件 0.2.3：升版本、打包、替换离线包

## 目标与用户价值

让 Chrome 商店用户拿到 dev 已合入 main 的插件改动（内联引用按钮、B 站收藏夹/分P 导入、键盘与历史加载修复、"已保存"即时反馈等）。新服务器（4eefa34）给所有浏览器客户端的回答正文带 `[^n]` 引用标记，商店里的 0.2.2 把它显示为普通文字，只有发新版才能消掉。

## 已确认事实

- 商店 0.2.2 从旧 main `185b73c` 打包；之后 `extension/src` 进了 32 个提交未发布。
- 源码仍标 0.2.2 但内容已不是商店那份（`10-21-release-smoke/research/merge-resolution.md` 第 2 点），打包前必须先升号。
- 版本写死在 8 处：`extension/manifest.json`、`extension/package.json`、`extension/src/protocol.ts`（`CLIENT_VERSION`）、`extension/src/app/browserCompanion.ts`（下载地址）、`extension/src/auth/LoginPage.test.tsx`、`extension/src/account/BrowserCompanionPage.test.tsx`、`extension/src/showcase/ShowcasePage.test.tsx`（下载地址断言）、`extension/src/sidepanel/manifest.test.ts`（版本断言）。
- `pnpm package:release` 写到 `extension/release/`（gitignore），产出 `…-production-<版本>.zip`（带 key，离线安装）和 `…-chrome-web-store-<版本>.zip`（无 key，商店上传）。
- 部署脚本和 `tests/test_linux_frontend_deployment.py` 只按 `deploy/offline-extension/*.zip` 通配，不需要改。
- 扩展 ID 不变（`omogodipchfidpikpeebgmlplpkjnpfm` 离线 / `jeemllapbelpglmpnhjfamcnoibpeail` 商店），服务器 `BROWSER_COMPANION_ALLOWED_ORIGINS` 不需要改。
- 前端工具链：Node 22（nvm v22.20.0）+ `corepack pnpm`。

## 需求

- R1 版本号 0.2.2 → 0.2.3，八处全改，`grep -rn "0\.2\.2"` 在源码、deploy、tests 下无残留。
- R2 `pnpm install --frozen-lockfile`、`check:api`、`typecheck`、`lint`、`test`、`build`、`run audit` 全部退出 0；`pnpm package:release` 产出两个 0.2.3 zip。
- R3 `deploy/offline-extension/` 只保留 `…-production-0.2.3.zip`，删除 0.2.2；`pytest tests/test_linux_frontend_deployment.py` 通过。
- R4 只提交本任务的路径到 dev；不推送、不开 PR。
- R5 告诉用户商店包路径和上传步骤。

## 验收标准

- `git show --stat HEAD` 只包含上述文件与 zip 替换。
- `extension/release/stashseek-chat-chrome-web-store-0.2.3.zip` 存在，解包后 manifest 无 `key`、版本 0.2.3。
- 离线包解包后 manifest 带 `key`、版本 0.2.3。

## 不在范围

- 上传到 Chrome 开发者后台（用户手动）。
- 发布到 main 与部署（走既有 release 流程）。
