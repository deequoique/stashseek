# MCP 参考

Notebook Agent 的 MCP adapter 是一个租户绑定、scope 限制的 application
channel。它支持官方 Python MCP SDK v2 的 `stdio` 和 Streamable HTTP；不支持
旧 SSE transport，也不接受调用方传入 `app_user_id` 或 tenant id。

以下命令假定完整配置已经位于进程环境或根 `.env`。使用 launcher-owned
`.env.runtime` 时，请按[首次运行教程](../tutorials/first-run.md)定义
`notebook_run`，并用它作为 `app.cli` 命令前缀。

## 启动

```bash
# MCP_TOKEN 只存在于这个 stdio 子进程环境
MCP_TOKEN='<raw-token>' \
  .venv/bin/python -m app.cli mcp-server --transport stdio

# HTTP 通常绑定 loopback，再由 TLS reverse proxy 对外提供
.venv/bin/python -m app.cli mcp-server --transport streamable-http
```

默认环境变量：

```dotenv
MCP_HOST=127.0.0.1
MCP_PORT=8000
MCP_PATH=/mcp
MCP_URL_TOKEN_MODE=false
```

## 认证与 grant

operator 为一个已有 `AppUser` 签发 grant：

```bash
.venv/bin/python -m app.cli users create
.venv/bin/python -m app.cli mcp-grant issue \
  --user-id <user-id> --scope read --label local-stdio
```

raw token 具有至少 256 bit 随机熵，只在 `issue` 或 `rotate` 时显示一次；服务
端只存 SHA-256 hash。每次请求按以下链路解析：

```text
raw bearer → token hash → active grant → scope
           → ChannelIdentity(channel=mcp) → AppUser → TenantContext
```

grant 的稳定 `grant_id` 是 MCP principal 的外部标识，不是 secret。grant 会在
重启后保留；`expires_at` 默认 `NULL`，但 revoke、disable、rotate、用户或
identity 禁用都会使旧 capability 无效。身份解析失败只返回 bounded
`invalid_grant`/HTTP 401，不泄漏 token 是否存在或属于哪个 tenant。

### Transport 认证

Streamable HTTP 首选：

```http
POST /mcp
Authorization: Bearer <raw-token>
```

仅当 client 不能发送 header 时，才设置 `MCP_URL_TOKEN_MODE=true` 并使用：

```text
https://agent.example/mcp/c/<raw-token>
```

path capability 的规则：

- 必须是 HTTPS；HTTP path token 被拒绝。
- `?token=<raw-token>`、编码后的 query token 和其他 query token 永远被拒绝。
- `MCP_PATH` 必须是非根绝对路径，无 query、fragment 或 trailing slash。
- SDK DNS-rebinding protection 保持开启；loopback 和已验证的精确
  `WEB_PUBLIC_ORIGIN` 才能进入 allowlist。
- full URL 可能被 MiXer、reverse proxy 或基础设施看到；应用、error、access
  和 analytics 日志必须删掉 capability。怀疑泄漏时立即 rotate/revoke。

stdio 需要环境变量 `MCP_TOKEN`，并在注册 tools 前解析 scope。stdout 只能放
protocol bytes；诊断写 stderr 或 bounded private log。

## Scope 与 discovery

| scope | `tools/list` 默认内容 | 写入条件 |
| --- | --- | --- |
| `read` | `ask_notebook_agent`、`list_saved_items`、`get_saved_item` | 不包含 mutation |
| `full` | 最多全部 10 个 tool | database、broker、object store、maintenance 和 worker readiness 全部通过 |

full readiness 不是一次 ping 就算成功：Celery worker 必须返回 `pong` 并同时
监听 `ingest`、`maintenance`。缺少、异常、超时或 malformed probe 都视为
unavailable。未 ready 时，full server 仍可提供 read discovery，但 mutation
tools 会被隐藏；即使客户端手工调用被隐藏的名称，也会 fail closed。

## Tool surface

所有 tool 都是当前 grant 的 tenant-scoped 操作。输入 schema 使用 strict
字段，未知字段会被拒绝。

| tool | 主要参数 | 结果/限制 |
| --- | --- | --- |
| `ask_notebook_agent` | `question`（1–4000 字符）、`conversation_id`（默认 `default`，最多 128） | 返回 `status`、回答、最多 10 个 citations、request/thread id 和 bounded `error_code` |
| `submit_knowledge_urls` | `urls`（1–10 个，每个最多 4096）、`why_saved`、`conversation_id` | 异步导入；返回 `ok`/`partial`/`failed` 与逐 URL 结果 |
| `list_saved_items` | `kind`、`platform`、`state`、`location=library|trash`、`limit=1..50`、`cursor` | 返回分页资料项和 `next_cursor`；tenant scoped |
| `get_saved_item` | `item_id > 0` | 返回一个资料项及 ingestion state/error |
| `update_saved_item` | `item_id`、`why_saved` | 修改保存备注；`why_saved` 受应用长度上限约束 |
| `request_delete_saved_items` | `item_ids`（1–10，不能重复）、`conversation_id` | 请求可恢复删除，返回一次性 confirmation code；不直接删除 |
| `confirm_item_deletion` | `confirmation_code`、`conversation_id` | 确认 server-owned pending deletion；不接受 item ids |
| `cancel_item_deletion` | `conversation_id` | 取消 pending deletion |
| `restore_saved_items` | `item_ids`（1–10，不能重复） | 从 recycle bin 恢复 |
| `retry_item_ingestion` | `item_id > 0` | 对一个条目重试导入 dispatch |

`item_id` 是 API 内部整数，调用方不能用它越过当前 grant 的 tenant 过滤。
`list_saved_items` 的投影包含 `item_id`、平台、kind、标题、作者、URL、保存
时间、备注、ingestion state 和安全错误码；它不会返回 raw storage key、tenant
id 或 provider trace。

## 问答与引用边界

`ask_notebook_agent` 进入现有 `ChannelService → KnowledgeAgent` 路径。MCP 不
暴露 raw search segment、neighbor expansion、storage、dispatch、模型配置或
purge 控制。非空检索证据会进入结构化 Composer；服务端校验 citation id 后
才渲染 `[S<segment_id>]` 标记和带时间戳的来源。模型不能自行生成 URL、HTML、
source section 或 citation marker。

当前消息中若是 1–10 个纯 supported URL，会直接进入 durable save-confirmation
action，跳过模型和检索；URL 加自然语言问题则是模型上下文，不是自动精确的
授权边界，最终仍由 tenant/item readiness predicates 决定可见证据。

## 错误与日志

常见 bounded code 包括 `invalid_grant`、`mutation_unavailable`、
`management_unavailable`、`not_found`、`confirmation_required`、
`queue_unavailable` 和 `answer_unavailable`。具体 provider body、数据库异常、
原始 URL/token、问题文本、tool 参数/结果和中间 prompt 不进入生产 MCP 输出或
诊断日志。

删除请求只有在 server-owned ordering marker 持久化后才返回 confirmation code；
marker 写入失败时会取消 pending action，并返回不含 code 的安全失败。
