# 隐私与可信边界

Notebook Agent 的“私人知识库”不是一句营销标签，而是一组独立的边界：
身份解析、租户查询、凭据作用域、证据选择和日志投影必须同时成立。任何一个
入口都不能只靠 prompt、前端过滤或“调用方自报 user id”来提供隔离。

## 租户是服务端边界

每个请求先解析成 `TenantContext`，包含内部 `app_user_id`、
`channel_identity_id`、channel、account 和 external user。保存 item、dispatch、
segment、conversation、pending action 和 citation hydration 都按这个上下文
执行。vector/lexical search、item metadata、neighbor、transcript 和 timestamp
resolution 会重复检查：

```text
current tenant ∧ active ∧ not deleted ∧ not archived ∧ ready（按查询适用）
```

模型提供的 `item_id`、历史消息中的旧 segment id、URL、channel label 都不是
授权。历史只能帮助理解当前问题，不能扩大当前 tenant 或 URL scope。跨租户
查询失败时返回空/安全错误，不回退到“最近的其他视频”。

## 凭据分层

| 凭据 | 谁持有 | 能做什么 | 不能做什么 |
| --- | --- | --- | --- |
| Web `__Host-kb_session` + CSRF | 浏览器 | 当前 Web identity 的资料库、对话和设备管理 | 不认证 MCP、Gateway 或 extension capture |
| MCP raw grant token | 某个 MCP client/process | 该 grant scope 下的 MCP tools | 不接受 caller user id；不认证 Web 或 extension |
| Browser companion Bearer | 已配对扩展 | `capture:write` 提交规范化字幕 | 不读资料库、对话、Web session 或 MCP |
| `CHANNEL_GATEWAY_SECRET` | Notebook Agent gateway 与 LangBot plugin | HMAC 认证 loopback `/v1/messages` bridge | 不是浏览器登录或 MCP token |
| channel link token | 用户当前 Web/Telegram/WeChat flow | 一次性、短期跨渠道 identity merge | 不直接执行 Agent 或授予任意 tenant |

凭据在数据库中只保 hash 或不可逆摘要：MCP grant token、browser pairing/grant、
Web session/CSRF、link token 和 email login code 都不是明文存储。raw token 只
在 issue/rotation/exchange 的规定响应中出现一次。

## Web 会话和 CSRF

生产 Web 使用 email OTP；challenge response 不区分未知邮箱与限流状态，避免
account enumeration。认证成功后设置 `__Host-kb_session`（HttpOnly）和
`__Host-kb_csrf`（脚本可读）两个 cookie；unsafe request 必须同时满足：

1. `Origin` 等于精确的 `WEB_PUBLIC_ORIGIN`（兼容开发路径是 `WEB_ORIGIN`）；
2. `X-CSRF-Token` 与 CSRF cookie 做 double-submit 比较；
3. session 在 PostgreSQL joined resolver 中仍然有效，identity/user 未禁用且
   属于固定 `web/web` namespace。

Redis session cache 只存 generation-bound、≤4 KiB 的 projection，并把 TTL 限制
在 session lifetime 内。cache miss、timeout、未知字段、过期 projection 和
invalidation failure 都回到 PostgreSQL 或 bump generation；cache 不能把注销、
禁用、merge 后的旧 session 重新变成有效身份。

## MCP capability 和公网风险

MCP raw token 至少 256 bit entropy，数据库仅保存 SHA-256。HTTP 默认要求
`Authorization: Bearer`；URL path capability 是兼容功能，不是更安全的登录方式：
完整 URL 可能落入 reverse proxy、MiXer 或基础设施记录。因而：

- 禁止 `?token=`，path token 必须 HTTPS；
- 应用、访问、error 和 analytics 日志只保留 canonical `/mcp`，不保留 token；
- SDK DNS-rebinding protection 保持开启，只允许 loopback 和已验证 public origin；
- `MCP_HOST` 默认 loopback；非 loopback 必须显式 acknowledgement，并位于 TLS
  reverse proxy 后；
- revoke/disable/rotate 是 operator 级撤销动作，不能用“删除客户端配置”代替。

`read` scope 只读 discovery；full mutation tools 还需要数据库、Redis、MinIO、
maintenance 和 worker readiness。隐藏 tool 的手工 invocation 仍 fail closed。

## Gateway 和渠道

Channel Gateway 只绑定 `127.0.0.1`/localhost。每个 POST body 最大 64 KiB，要求
timestamp、nonce 和 HMAC-SHA256 signature；时间窗默认 60 秒，nonce 在进程内
短期记录以拒绝 replay。它会覆盖 untrusted envelope 的 request id，再交给
`ChannelService`。gateway 日志不写 message body、nickname、external sender id、
token、HMAC secret 或 QR code。

LangBot plugin 是 out-of-process bridge。production patch 用
`required_plugins`/`initialized` readiness 保护 Telegram/WeChat adapter；bridge
断开、事件缺少 required plugin 或没有 `prevent_default()` 时 fail closed，不把
原始消息落入另一个 Local Agent。plugin `.env` 应 mode `0600`，并与 root app
配置分开。

## 浏览器 capture 的最小权限

扩展 pairing 是 challenge/verifier 的一次性 flow；grant hash-at-rest，只允许
`capture:write`。状态改变的 extension request 要求精确
`chrome-extension://<id>` Origin；唯一的无 Origin 例外是精确的只读 pairing
status GET。生产禁止 `chrome-extension://*`。

页面授权始终留在浏览器：

- Cookie、SAML、Kaltura KS、signed caption/media URL、Authorization header 和
  page exception 不会进入 Notebook Agent payload、broker、PostgreSQL 或日志；
- server 只接收 `capture.v1` 的 platform id、secret-free canonical/page URL、
  bounded public metadata、ordered cues 和 server-defined cue hash；
- 浏览器请求、caption body 和 publish 都有独立大小/时间预算；扩展不可因
  server 不可达而无限等待；
- disconnect 先请求 server revoke，再清理本地 credential；Web 账户页可以按
  tenant revoke device。

这也是为什么 browser companion 不是“任意登录网站通用抓取”：manifest 和
服务器都使用精确的 YouTube/NTULearn/Kaltura host/平台 allowlist。

## 数据、队列和日志

原始字幕进入 tenant-prefixed object key；Celery broker 只收到 dispatch id。日志
只允许固定 safe fields（状态、phase、bounded error code、耗时、计数），不能写
问题文本、tool 参数/结果、excerpt、segment/item id、URL、provider body 或
exception message。开发环境可显式开启受限 retrieval detail，但 production
必须保持 `NOTEBOOK_AGENT_LOG_RETRIEVAL_CONTENT=false`。

`MIGRATION_DATABASE_URL` 只在一次性 migration 子进程中存在；launcher 不复制
完整 `.env.example` 到 `.env.runtime`，不会把 secrets 传给不需要的长期子进程。
生产系统的 Redis/MinIO ports 应只发布到 loopback，Caddy/NGINX 只代理已拥有的
Notebook Agent site block，并在完整候选配置上验证后 reload。

## 删除、恢复与可信回答

删除先生成 server-owned pending action 和 one-time confirmation code。marker
只有在 durable ordering record 写入后才会返回；marker 失败会取消 pending
action、不给 code。确认后先进入可恢复 recycle bin，worker/object purge 在
retention policy 下运行；restore/retry 仍重新验证 tenant ownership。

问答的可信边界也由服务端拥有：模型不能自选不在当前 run 的 citation、不能
伪造来源 URL 或 chapter title，不能把 unsupported text 伪装成 grounded。服务端
先验证结构化答案，再渲染 citation marker 和 timestamp source。三次 Composer
失败后清空 citations 并返回 `answer_unavailable`，不把无效 draft 当作答案保存。
