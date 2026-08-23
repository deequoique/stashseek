# 解释

解释页回答“为什么 Notebook Agent 这样设计、边界在哪里”。它们帮助你建立
系统模型，不是安装清单；需要具体命令时请转到[操作指南](../how-to/README.md)，
需要字段和接口时请转到[参考](../reference/README.md)。

## 主题

- [架构与数据流](architecture.md)：入口、应用服务、数据存储、后台运行时和
  transport 如何组合。
- [导入、分块、检索与回答](ingestion-and-retrieval.md)：为什么“保存链接”
  与“可依据检索的资料库”不同，以及 citation 如何形成。
- [隐私与可信边界](privacy-and-trust.md)：租户隔离、凭据分层、证据校验和
  浏览器 capture 的安全取舍。
- [身份、租户与渠道](identities-and-channels.md)：`AppUser`、
  `ChannelIdentity`、`TenantContext` 与 Web/MCP/LangBot 的关系。

这些页面描述的是当前实现：YouTube/Bilibili server connector、YouTube 和
NTULearn/Kaltura browser companion、Web、MCP 以及可选 LangBot。ASR、任意
登录网站抓取和更多平台不是默认已交付能力。
