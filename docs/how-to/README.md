# 操作指南

这里的每一页都围绕一个明确目标，适合已经知道自己要完成什么的读者。先选择
目标，再按页面中的检查点操作；不要为了一个局部任务复制整份 `.env.example`。

## 使用产品

- [启用完整资料库](run-full-library.md)：从只读运行时切换到保存视频、后台整理和条目管理。
- [使用 Web 资料库](use-web-library.md)：登录、保存链接、搜索、查看字幕与时间戳、归档和重试。
- [使用浏览器伴侣](use-browser-companion.md)：在当前 YouTube 或 NTULearn/Kaltura 页面读取字幕并配对设备。

## 连接入口

- [连接 MCP 客户端](connect-mcp-client.md)：签发 grant，选择 stdio 或 Streamable HTTP，并检查 scope。
- [接入 LangBot](connect-langbot.md)：配置可选的 Telegram/微信桥接，并绑定跨渠道身份。

## 部署与运维

- [部署通用生产环境](deploy-production.md)：规划安全边界、依赖、迁移和上线验收。
- [部署独立前端](deploy-frontend.md)：选择 bundled 或 split Web 部署，并保持同源安全模型。
- [备份与恢复](back-up-and-restore.md)：备份 PostgreSQL、对象存储和运行配置，按顺序恢复。
- [升级与回滚](upgrade-and-roll-back.md)：让代码、前端和数据库迁移成对发布。
- [排查问题](troubleshoot.md)：从症状定位启动、依赖、MCP、Web、抓取和渠道故障。

## 相关参考

- [运行 profile 参考](../reference/runtime-profiles.md)
- [环境变量参考](../reference/configuration.md)
- [MCP 参考](../reference/mcp.md)
- [Web API 参考](../reference/web-api.md)

### 安全边界

生产环境中的 secret、session cookie、MCP raw token、URL capability 和浏览器捕获
Bearer 都是凭据。文档中的占位符不能直接用于生产；疑似泄露时先 revoke/rotate，
再检查访问日志和代理配置。不要通过关闭 TLS、放宽 CORS、接受 query token 或把
gateway 暴露到公网来“临时修复”连接问题。
