# Notebook Agent 文档

这里是 Notebook Agent 的自托管与集成文档。第一次使用时从教程开始；已经知道目标时，直接选择对应的操作指南。

## 第一次运行

[完成第一次本地运行](tutorials/first-run.md)会带你启动只读 MCP、创建用户与授权，并完成一次可验证的资料库问答。教程只使用成功运行所需的最小配置。

需要保存视频、使用 Web 资料库或接入聊天平台时，完成教程后继续阅读[启用完整资料库](how-to/run-full-library.md)。

## 按目标查找

| 你的目标 | 从这里开始 |
| --- | --- |
| 浏览、保存和管理视频资料 | [使用 Web 资料库](how-to/use-web-library.md) |
| 连接 Claude、Codex 或其他 MCP 客户端 | [连接 MCP 客户端](how-to/connect-mcp-client.md) |
| 保存当前浏览器页面中的字幕 | [使用浏览器伴侣](how-to/use-browser-companion.md) |
| 通过 Telegram 或微信使用资料库 | [接入 LangBot](how-to/connect-langbot.md) |
| 部署公开服务 | [部署 Notebook Agent](how-to/deploy-production.md) |
| 备份、升级或解决运行故障 | [操作指南目录](how-to/README.md) |
| 查询配置、命令或接口定义 | [参考手册](reference/README.md) |
| 理解架构、检索和隐私设计 | [原理解读](explanation/README.md) |
| 运行仓库已经使用的特定生产方案 | [特定生产环境手册](operations/production/README.md) |

## 文档类型

- **教程**陪你从零完成一个结果。请按顺序操作。
- **操作指南**解决一个明确问题。它们假定基础环境已经可用。
- **参考手册**准确描述命令、配置和接口，适合查阅。
- **原理解读**说明系统为什么这样工作，以及各组件之间的边界。

文档随仓库版本维护。若文档与当前检出的命令行为不一致，请以同一版本的 `--help` 输出和代码为准，并提交问题说明版本与复现步骤。
