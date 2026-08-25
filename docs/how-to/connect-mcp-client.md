# 连接 MCP 客户端

## 目标

为一个具体的 MCP 客户端签发租户绑定的 grant，并用 stdio 或 Streamable HTTP
完成 `initialize -> tools/list -> tools/call`。MCP grant 是 capability，不是
全局管理员账号；一个用户可以有多个不同 scope 的 grant。

## 1. 选择权限范围

| scope | 默认可发现的工具 | 适合场景 |
| --- | --- | --- |
| `read` | `ask_stashseek`、`ask_notebook_agent`（兼容别名）、`list_saved_items`、`get_saved_item` | 只读问答、库存查看、评测 |
| `full` | 最多 11 个工具，且必须通过 mutation readiness | 保存、更新、删除/恢复、重试 |

`full` 不是“永远可写”。PostgreSQL、Redis、object store、maintenance 配置和
Celery worker 任一项不可用时，服务会隐藏或拒绝 mutation tools。不要用一个固定的
管理员用户或把 `app_user_id` 作为 tool argument；身份由 grant 映射到当前租户。

如果运行时由 `./scripts/stashseek init` 创建，请先沿用[首次运行教程中的
`stashseek_run` helper](../tutorials/first-run.md)，让下面的 operator 命令读取同一份
`.env` 与 `.env.runtime`。只维护根 `.env` 的手工部署可以直接运行 `app.cli`。

## 2. 签发 grant

在 StashSeek Chat 项目根目录执行：

```bash
stashseek_run .venv/bin/python -m app.cli mcp-grant issue \
  --user-id <user-id> \
  --scope read \
  --label my-client \
  --expires-at 2026-12-31T23:59:59+00:00
```

`--expires-at` 可省略；省略表示没有 inactivity 过期，直到显式 rotate/revoke/disable
或账号被停用。复制命令输出的 raw token 到客户端私有 secret 配置。它只显示一次，
服务端只保存 SHA-256 hash。

列出和查看 grant 只显示元数据：

```bash
stashseek_run .venv/bin/python -m app.cli mcp-grant list --user-id <user-id> --limit 100
stashseek_run .venv/bin/python -m app.cli mcp-grant show <grant-id>
```

不要把 raw token 写到 `.env.example`、shell history、日志或 URL。疑似泄露时立即
轮换或撤销：

```bash
stashseek_run .venv/bin/python -m app.cli mcp-grant rotate <grant-id>
stashseek_run .venv/bin/python -m app.cli mcp-grant revoke <grant-id>
stashseek_run .venv/bin/python -m app.cli mcp-grant disable <grant-id>
```

轮换会让旧 token 失效；在新 token 写入客户端前，不要把它发送到第三方聊天工具。

## 3. 使用 stdio

stdio 适合与本机桌面客户端或开发工具一起运行：

```bash
export MCP_TOKEN='<raw-token>'
stashseek_run .venv/bin/python -m app.cli mcp-server --transport stdio
unset MCP_TOKEN
```

客户端配置应等价于以下信息（具体 JSON 键名由客户端决定）：

```json
{
  "command": "/absolute/path/to/stashseek/.venv/bin/dotenv",
  "args": [
    "-f", "/absolute/path/to/stashseek/.env.runtime",
    "run", "--no-override", "--",
    "/absolute/path/to/stashseek/.venv/bin/python",
    "-m", "app.cli", "mcp-server", "--transport", "stdio"
  ],
  "env": {"MCP_TOKEN": "<raw-token>"}
}
```

这个示例适用于只使用 launcher-owned `.env.runtime` 的本地运行时。如果根 `.env`
还覆盖了运行时变量，请让客户端调用一个实现上述 `stashseek_run` 顺序的私有 wrapper，
不要交换 `.env` 与 `.env.runtime` 的优先级。客户端工作目录应为项目根目录。
stdout 只能有 MCP 协议字节；调试输出放 stderr 或受控日志。不要在 wrapper 中用
`echo` 打印 token。

## 4. 使用 Streamable HTTP

服务端设置默认值即可监听 loopback：

```dotenv
MCP_HOST=127.0.0.1
MCP_PORT=8000
MCP_PATH=/mcp
MCP_URL_TOKEN_MODE=false
```

标准客户端发送：

```text
POST https://agent.example/mcp
Authorization: Bearer <raw-token>
```

生产环境应把应用绑定到 loopback，再由 TLS reverse proxy 提供 HTTPS，并保留 SDK 的
DNS rebinding/Host 校验。非 loopback 绑定必须显式设置
`STASHSEEK_ALLOW_NON_LOOPBACK=true`，且仅适用于已经审核过的网络边界。

如果客户端只能使用 URL 字段，才开启 capability path：

```dotenv
MCP_URL_TOKEN_MODE=true
```

使用 `https://agent.example/mcp/c/<raw-token>`。path token 也是真实凭据：代理、
MiXer 和基础设施可能看到完整 URL，因此必须关闭 query/access/analytics 日志中的
原始 URI，发现暴露后立即 rotate/revoke。永远不要使用 `?token=<raw-token>`，也不要
在 HTTP 明文上发送 path token。

## 5. 验收工具和租户边界

连接后按顺序执行：

1. `initialize`；
2. `tools/list`，确认 read scope 有四个工具（包括兼容别名），full scope 只在 readiness ready
   时显示管理工具；
3. `list_saved_items`，确认结果属于签发 grant 的用户；
4. `ask_stashseek`，询问一个已经保存的视频主题，确认回答包含服务器生成的
   引用/时间戳，而不是模型自写的 URL。

旧客户端仍可调用 `ask_notebook_agent`，它是兼容别名并保留相同租户、范围和证据行为。
`submit_knowledge_urls`、更新、删除、恢复和重试都是服务端拥有最终结果的操作。客户端
不得把自然语言回答当成写入已完成，也不得自行拼接确认码。更多工具 schema、transport
约束和错误码见 [MCP 参考](../reference/mcp.md)。
