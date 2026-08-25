# 参考

这里记录 StashSeek Chat 当前实现的接口、配置和运行时契约。参考页回答
“这个选项、命令或接口是什么”，不负责带你完成一次部署；需要按目标操作时，
请从[操作指南](../how-to/README.md)进入。

## 按主题查找

| 你要确认的内容 | 参考页 |
| --- | --- |
| launcher 的 `read`、`full`、`langbot` 差异 | [运行模式](runtime-profiles.md) |
| 环境变量、默认值、secret 和重启范围 | [配置](configuration.md) |
| launcher、应用 CLI 和 grant 管理命令 | [CLI](cli.md) |
| MCP transport、grant、scope 和 tool surface | [MCP](mcp.md) |
| Web、会话、资料库、对话和浏览器伴侣 HTTP 合约 | [Web API](web-api.md) |

## 相关概念

参考页中的设计理由见[解释](../explanation/README.md)：

- [架构与数据流](../explanation/architecture.md)
- [导入、分块、检索与回答](../explanation/ingestion-and-retrieval.md)
- [隐私与可信边界](../explanation/privacy-and-trust.md)
- [身份、租户与渠道](../explanation/identities-and-channels.md)

## 版本与事实边界

页面中的命令和字段以当前仓库的 `app/`、`scripts/stashseek`、Web
OpenAPI 生成代码和扩展合约为准。`ASR`、任意认证网站抓取和旧 SSE transport
不是本参考页默认承诺的能力；Web API 的公开前缀固定为 `/api/v1`，不能改成
其他值。配置示例中的
`<...>` 是占位符；真实 token、密码、DSN、HMAC secret 和 capability URL
不得写入仓库或普通日志。
