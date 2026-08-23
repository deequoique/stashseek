# 架构与数据流

Notebook Agent 把“进入系统的渠道”和“知识处理的核心”分开。Web、MCP、CLI
和 LangBot 不各自实现一套 Agent；它们把已经认证的消息转换为统一的
`ChannelEnvelope`，再交给同一个租户绑定的 `ChannelService`。

## 组件关系

```text
Web / MCP / CLI / LangBot plugin / browser companion
       │              │                    │
       │              └─ capture.v1 ───────┘
       ▼
认证与 capability boundary
       ▼
ChannelService ── identity + thread + idempotency
       ▼
KnowledgeAgent
   ┌───┴───────────────┐
   │                   │
retrieval           actions
   │                   │
PostgreSQL + vector  submission → Redis/Celery → connectors
   │                                      │
   └──────── citations/evidence ◄────────┘
                    │
              validated answer

PostgreSQL: users, identities, items, segments, conversations, grants
MinIO/S3: raw subtitle objects
Redis: broker、Celery backend、可选 Web session projection
Celery: ingest 与 maintenance worker、单一 Beat
```

## 入口和组合根

- `app.api.runtime.build_web_app` 组合 canonical FastAPI app、email auth、
  library、conversation、browser companion 和 capture submission。
- `app.mcp_server` 用官方 MCP SDK 注册 typed tools，并在 transport 外包一层
  grant resolution；MCP 不导入 LangBot plugin。
- `app.channels.http_gateway` 是 loopback-only 的 HMAC bridge。LangBot plugin
  在进程外把平台消息转成 signed HTTP request。
- `app.cli` 提供 operator 和本地 smoke 命令；`scripts/notebook-agent` 只负责
  单机生命周期和 profile 组合。

combined ASGI 模式由 `app.web_runtime` 分派：`MCP_PATH` 及其子路径先进入
MCP，其他请求进入 Web app。这样同一 listener 可以服务 authenticated SPA、
`/api/v1/*` 和 MCP，但两种 credential 仍由各自的 middleware 解析。

## 一次问答

1. transport 验证 session、MCP grant、CLI identity 或 gateway HMAC，并得到
   `TenantContext`。
2. `ChannelService` 以 channel/account/external user/conversation 形成
   identity key，串行化同一对话，处理 `/link`、`/new` 等确定性命令。
3. 对普通问题，它加载有界历史，构建 `AgentRequest`，将租户和 thread 传给
   `KnowledgeAgent`。
4. Agent 通过 `KnowledgeServices` 查询当前租户的 ready、active、未归档
   segments，或调用保存/管理 action。
5. 有证据的回答进入结构化 Composer，服务端验证 citation selection 和
   item/segment 上限，再把来源和时间戳渲染给每个 transport。
6. 只有成功且可持久化的 final answer 会写入 `ConversationTurn`；中间 prompt、
   provider body、无效 draft 和 partial stream 不写入历史。

同一 `message_id` 的重复投递会读取已经完成的 turn，而不会再次调用模型或
   重复执行 action。Web SSE 只是这一次 ChannelService 执行的安全事件投影，
   不是第二条处理路径。

## 一次保存与后台导入

Web batch save、MCP `submit_knowledge_urls`、Agent action 和浏览器 capture
都先进行本地 URL/schema/tenant/quota 校验，然后创建 tenant-owned item 和
durable dispatch。真正的 connector、字幕处理、chunk/embed 在 Celery worker
执行；broker 消息只携带 dispatch id，不携带字幕、cookie、signed URL 或
第三方 token。

普通 URL 流程是：

```text
canonical URL → pending item → dispatch → fetching metadata/text
              → raw object → chunking → embedding → ready
```

外部 PostgreSQL、Redis、MinIO 属于 deployment dependency；launcher 会做
readiness admission，但不会把外部资源当作自己拥有的服务来停止或重建。

## Profile 是部署边界，不是产品权限

`read` profile 只组合 MCP process（它不要求本地 Redis、MinIO、worker 或 Beat）；
`full` 加入 worker、Beat、gateway 和 mutation readiness；`langbot` 去掉 public
MCP 但保留后台/渠道 runtime。profile 决定哪些进程应当存在，grant scope 决定某个调用方能看到哪些 MCP tools，
`TenantContext` 决定它能看到哪些数据。三者不能互相替代：启动 full 不会授予
用户权限，签发 full grant 也不会绕过 worker/object-store readiness。

## 故意保留的分层

- PostgreSQL 是身份、租户归属和 session 的 authority；Redis 的 Web session
  projection 只能加速解析，不能决定登录。
- MinIO 保存 raw transcript，以便 worker 重试；citation 和对话只投影必要
  的公开来源字段。
- Browser companion 是 acquisition client，不是通用 remote browser。它把
  页面授权只用在浏览器本地，再提交 `capture.v1`。
- LangBot 是可选 out-of-process adapter，不是 MCP 的依赖，也不应把平台 SDK
  或 plugin registry 引入 `app/` 的核心 MCP boundary。
