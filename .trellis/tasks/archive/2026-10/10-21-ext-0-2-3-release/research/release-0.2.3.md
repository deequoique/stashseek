# 插件 0.2.3 发布包（2026-10-21，dev d6c91ca 之上）

工具链：Node v22.20.0 + corepack pnpm 11.22.0。`install --frozen-lockfile`、`check:api`、`typecheck`、`lint`、`test`（49 文件，778 通过，8 跳过）、`build`、`run audit`、`package:release` 全部退出 0。

| 包 | 用途 | 文件数 | 大小 | sha256 |
|---|---|---|---|---|
| `stashseek-chat-browser-companion-production-0.2.3.zip` | 离线安装（manifest 带 `key`，扩展 ID `omogodipchfidpikpeebgmlplpkjnpfm`）；已入库 `deploy/offline-extension/`，部署时复制到 `extension/dist/assets/` | 94 | 1048571 | `3375a7200d999b224ca6fe41f6c4da4d6ff2ae051e991aa193363cdde75df2c9` |
| `stashseek-chat-chrome-web-store-0.2.3.zip` | 商店上传（manifest 无 `key`，商店 ID `jeemllapbelpglmpnhjfamcnoibpeail`）；不入库，位于 `extension/release/`（gitignore） | 94 | 1048210 | `0c74aaa1ec067d295fc10baa0b2ac4b20c8edea5b00929cab8748c0554a6a05e` |

两个包都核对过：manifest `version` = 0.2.3，key 有无符合预期，包内没有嵌套 zip。`pytest tests/test_linux_frontend_deployment.py` 6 通过。

相对商店 0.2.2（旧 main `185b73c`）的内容：`extension/src` 32 个提交，含内联引用按钮、B 站收藏夹/分P 导入、键盘与历史加载修复、"已保存"即时反馈、外部登录页面（服务端未配置时不显示）。

服务器侧无需改配置：扩展 ID 不变，`BROWSER_COMPANION_ALLOWED_ORIGINS` 不变。网站的"下载离线安装包"链接随本提交指向 0.2.3，发布到 main 并部署后生效。
