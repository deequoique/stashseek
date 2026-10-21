# Web 邮箱登录与跨渠道绑定：技术设计

## 1. 设计目标与非目标

本设计为未来同源 Web 前端提供稳定的后端 API。Web、Telegram 与 WeChat 在身份绑定后
必须进入同一个 `ChannelService -> KnowledgeAgent -> tenant-scoped Retrieval` 路径；入口认证
方式不同，但不能复制 Agent、检索、租户或内容管理逻辑。

本期不实现页面、密码、OAuth、内容管理 Web API、账号拆分、设备数量上限或全部设备退出。
MCP 保留原有协议、路由与 bearer grant 认证，不把 grant 下发给浏览器。

## 2. 总体架构与信任边界

```text
Browser（同源）
  -> HTTPS reverse proxy
  -> Combined ASGI app
       /api/v1/*  -> FastAPI Web API -> Web session / CSRF / WebAuthService
       /mcp       -> 现有 McpAuthMiddleware -> bearer grant
  -> ChannelService -> KnowledgeAgent -> tenant-scoped Retrieval

Telegram / WeChat
  -> LangBot plugin -> loopback HMAC gateway -> ChannelService
```

`/api/v1/*` 与 `MCP_PATH` 由同一 Uvicorn 进程承载，但它们没有共享认证中间件：

- Web 只接受受保护的 `Secure; HttpOnly; SameSite=Lax` session Cookie；从服务端 session
  解析 `AppUser` 与 Web `ChannelIdentity`。
- MCP 继续由 `McpAuthMiddleware` 在每个请求解析 Bearer grant，保持现有 `read/full`
  scope 与 URL token 安全规则。
- LangBot gateway 仍仅接受 loopback HMAC 调用。浏览器绝不获得 gateway secret，也不能
  自行指定 `ChannelEnvelope` 的 channel、external user 或 tenant。

合并后的三渠道拥有相同 `AppUser`，因而访问同一知识库。对话 thread 仍由
`channel_identity_id + conversation_id` 区分，不跨渠道混入历史。模型输出可能因生成随机性
略有措辞差异，但权限、检索和 Agent 配置一致。

## 3. 复用与新增边界

| 层级 | 处理方式 | 原因 |
| --- | --- | --- |
| `AppUser`、`ChannelIdentity`、`TenantContext` | 复用 | 已是租户与可信身份的唯一边界。Web 不创建第二套 user 表。 |
| `ChannelEnvelope`、`ChannelService`、对话持久化、Agent、检索 | 复用 | Web 只做可信 envelope 适配，确保三渠道业务语义相同。 |
| `ChannelLinkToken` 与 tenant merge | 扩展 | 将 `web` 加入可链接渠道，保留现有 token 哈希、锁、重试和内容去重。 |
| `McpAccessGrant` | 修改 merge 行为 | 目标 tenant 的 grant 显式标记撤销，不转移为来源 tenant 的可用授权。 |
| LangBot loopback gateway | 保持不变 | HMAC 信任模型与浏览器 session 不兼容。 |
| WebAuth、EmailSender、Web API、Combined ASGI app | 新增 | 这是邮箱验证、session 和公开 JSON HTTP 契约所需的最小新边界。 |

## 4. 数据与安全模型

### 4.1 复用 Web identity

验证成功后，使用已有 `ChannelIdentity` 创建或查找：

```text
channel = "web"
account_id = "web"
external_user_id = canonical_email
```

`canonical_email` 在一个唯一的认证模块中完成 trim、语法校验与规范化；不会由调用方直接
传给 Agent 或检索。这样 `(channel, account_id, external_user_id)` 唯一约束天然保证一个邮箱
只有一个 Web identity。现有数据库本已保存 Telegram/WeChat external identity；本期不额外
创建重复的 `email_identity` 表。

### 4.2 新表

`web_auth_challenge`

```text
id, email, code_hash,
attempt_count, expires_at, consumed_at, invalidated_at,
created_at, sent_at, delivery_failed_at
```

- `email` 为规范化邮箱，仅用于在验证码验证时寻找当前 challenge；发送新 challenge 时会使
  同一邮箱此前未消费的 challenge 失效。
- 6 位验证码用 `HMAC-SHA-256(WEB_AUTH_SECRET, challenge_id + ":" + code)` 存储；不能用
  可离线穷举的裸 SHA-256。
- 每次验证事务锁定挑战行；先校验失效/过期/尝试次数，再递增次数并以恒定时间比较 HMAC。
  成功时写入 `consumed_at`，任何后续重放失败。

`web_session`

```text
id, app_user_id, channel_identity_id, token_hash,
created_at, expires_at, revoked_at, last_used_at
```

- 原始 session token 至少 256 bit，只在 `Set-Cookie` 出现一次；表中保存 SHA-256 哈希。
- 创建后固定 30 天过期，不滑动续期。可以同时存在多个 session，不设数量上限。
- 当前 session 登出只写本行 `revoked_at`。禁用账户撤销该 tenant 所有 session；tenant merge
  撤销目标 tenant 所有 session。

所有新表的时间字段使用 timezone-aware UTC；索引覆盖 token hash、活跃 session tenant、
challenge email + created/expiry。迁移必须在 `vercel.json` 同步更新 Alembic head，尽管 Web
API 不部署到 Vercel，健康检查仍会校验 schema head。

### 4.3 验证码发送与限流

定义 `EmailSender` 协议，领域服务只调用 `send_login_code(to_email, code, expires_at)`：

- `InMemoryEmailSender` 用于自动化测试，并作为开发/测试未设置 `EMAIL_PROVIDER` 时的
  明确 fallback；它只保存在进程内且不记录敏感值到生产日志。
- `SmtpEmailSender` 使用标准库 SMTP 与 `EmailMessage`，依次执行 `EHLO`、STARTTLS、
  `EHLO`、认证和投递。当前开发环境可配置 Gmail SMTP；host、port、STARTTLS、username、
  password、from address 和 timeout 都由运行配置注入。Gmail App Password 仅是本地/开发
  secret，绝不写入代码、测试、日志或文档。
- `ResendEmailSender` 保留标准 HTTPS `POST /emails` 适配器，从 `RESEND_API_KEY` 和发件
  地址读取配置，供未来具备可验证发件域名时选择。
- composition root 是唯一的 provider 选择者：`smtp` 选择 SMTP、`resend` 选择 Resend；
  开发/测试未选择时使用内存 sender。生产启用 Web 认证时，未选择或配置不完整的 provider
  必须在启动时 fail closed，不能回退内存 sender。
- provider 的正文、认证材料和验证码不出现在异常、日志或 API 响应；失败统一映射为稳定的
  `email_delivery_unavailable`。

定义 `LoginRateLimiter` 协议。生产 `RedisLoginRateLimiter` 使用 HMAC 后的规范化邮箱和
客户端 IP 作为 key，在原子操作中执行：60 秒重发间隔、每邮箱 15 分钟 3 次、每 IP 1 小时
10 次。开发/测试使用内存 fake。Redis 不可用时 fail closed，不创建 challenge 或发送邮件。
从反向代理取得客户端 IP 时仅信任显式配置的本地/受控 proxy headers；不能无条件信任外部
`X-Forwarded-For`。

挑战有效 10 分钟，单个 challenge 最多 5 次校验。限流、错误验证码、未知邮箱与已注册邮箱
在挑战申请路径使用相同的公开成功投影，避免账号枚举；格式错误仍按输入契约拒绝。

## 5. 公开 HTTP 契约

所有 Web API 位于 `WEB_API_PREFIX=/api/v1`，与 `MCP_PATH` 不得重叠。所有可变更状态的路由
均要求 JSON content type 和严格同源 `Origin` 校验；未配置 CORS。Cookie 名称使用
`__Host-` 前缀、`Path=/`、无 `Domain`、`Secure`、`HttpOnly`、`SameSite=Lax`。

| 方法与路径 | 认证 | 输入 | 成功结果 |
| --- | --- | --- | --- |
| `POST /api/v1/auth/challenges` | 无 | `{email}` | 统一 accepted 投影；需要时发送验证码 |
| `POST /api/v1/auth/verify` | 无 | `{email, code}` | 创建/恢复 Web identity，设置 session Cookie，返回当前 session 投影 |
| `GET /api/v1/session` | session | 无 | 当前认证状态与安全的 tenant/session 元数据 |
| `DELETE /api/v1/session` | session + Origin | 无 | 撤销当前 session，清除 Cookie |
| `POST /api/v1/conversations/{conversation_id}/messages` | session + Origin | `{message_id, text}` | `ChannelService.handle()` 的 `AgentAnswer` JSON 投影 |
| `POST /api/v1/conversations/{conversation_id}/reset` | session + Origin | 无 | 复用 `/new` 的安全重置语义 |
| `POST /api/v1/link-tokens` | session + Origin | `{target_channel}` | 返回一次性 link token，目标限 Telegram/WeChat |
| `POST /api/v1/link-tokens/consume` | session + Origin | `{token}` | 消费目标为 `web` 的 token，返回归并后的安全状态 |

Web API 不接受 `app_user_id`、`channel`、`account_id` 或 `external_user_id` 作为权限输入。消息路由
仅在 session 解析完成后构造：

```text
ChannelEnvelope("web", "web", resolved_web_identity.email,
                conversation_id, message_id, text)
```

`/link web` 由 Telegram/WeChat 继续生成 token，文案提示用户在已登录 Web 中调用 consume API；
Web 发起时生成目标为 Telegram/WeChat 的 token，目标渠道继续使用 `/link <code>`。两端都
证明控制权后才可归并。

## 6. 归并、禁用与撤销

扩展 `LINKABLE_CHANNELS` 后，`create_link_token` 与 `consume_link_token` 仍是唯一的 token
生成/消费所有者。归并事务在现有行锁与 ingestion-busy 检查之外：

1. 锁定来源与目标 tenant 的 `web_session` 和 `mcp_access_grant`。
2. 将目标 `web_session.revoked_at` 设为当前时间。
3. 将目标 MCP grants 标记 `revoked_at`，并迁移为来源 tenant 的已撤销审计记录，避免
   `AppUser` 删除时只依赖 cascade 丢失显式撤销语义。
4. 执行现有内容去重、identity/thread/token 迁移，删除目标 `AppUser`。

来源 tenant 的 sessions 与 grants 不变。账号禁用路径复用相同 session revoke helper，在禁用
`AppUser` 的事务中使全部 Web session 失效。

## 7. 运行、配置与可观测性

新增一个组合 ASGI factory：先构造现有 `create_streamable_http_app()`，再以 path dispatcher
将 `MCP_PATH` 请求交给它，其余受允许 Web 路由交给 FastAPI。MCP app 仍接收原始 `/mcp`
path，避免 SDK route 语义变化。Uvicorn 启动这个组合 app；现有 `mcp-server --transport
streamable-http` 演进为组合入口而不改变 MCP URL。

建议新增配置：

```text
WEB_API_PREFIX=/api/v1
WEB_PUBLIC_ORIGIN=https://app.example.com
WEB_AUTH_SECRET=<至少 32 字符的随机密钥>
WEB_SESSION_TTL_SECONDS=2592000
WEB_AUTH_CODE_TTL_SECONDS=600
WEB_AUTH_MAX_ATTEMPTS=5
WEB_AUTH_RESEND_SECONDS=60
WEB_AUTH_EMAIL_WINDOW_SECONDS=900
WEB_AUTH_EMAIL_MAX_SENDS=3
WEB_AUTH_IP_WINDOW_SECONDS=3600
WEB_AUTH_IP_MAX_SENDS=10
EMAIL_PROVIDER=smtp|resend
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_STARTTLS=true
SMTP_USERNAME=<local secret>
SMTP_PASSWORD=<local Gmail App Password secret>
SMTP_FROM_EMAIL=<local secret>
SMTP_TIMEOUT_SECONDS=10
RESEND_API_KEY=<secret>
RESEND_FROM_EMAIL=<verified sender>
RESEND_TIMEOUT_SECONDS=10
```

上列 SMTP Gmail 值只说明配置形状；真实 username、password 和发件地址只存于私有本地
secret。`EMAIL_PROVIDER=resend` 时才读取/校验 Resend 配置，`EMAIL_PROVIDER=smtp` 时才
读取/校验 SMTP 配置。生产 provider 尚未决定，因此生产启用 Web 认证必须显式选择其中一个
并提供完整配置；不能把 Resend 域名验证或任何特定 DNS 控制权作为当前前置条件。

应用监听仍复用当前 `MCP_HOST`/`MCP_PORT`，建议绑定 loopback；HTTPS reverse proxy 终止 TLS。
proxy 的 upstream read timeout 至少配置为 60 秒，覆盖 45 秒 Agent 总预算和网络余量。访问日志
不得记录 Cookie、Authorization、验证码、token、外部 identity、邮件 provider payload 或消息正文。

## 8. 风险、回滚与验证

- 新迁移改变 `vercel.json` 的 expected revision；先用 direct Neon URL 执行 migration，再发布
  使用 pooled runtime URL 的应用。
- 已选择的邮件 provider 不可用或 Redis 限流不可用时，认证 fail closed；已存在的
  MCP/Telegram/WeChat 不受影响。
- 反向代理不满足超时/HTTPS/cookie 转发要求时，Web API 不应视作上线完成。
- 代码回滚不会自动安全地回滚生产 migration；按现有 migration runbook 审核。必要时关闭 Web
  路由而保留已迁移数据和旧渠道。
- 验证包括 SQLite 领域测试、PostgreSQL 归并/并发测试、ASGI loopback HTTP 契约测试、SMTP
  fake 与 Resend HTTP fake 测试，以及现有 MCP/TG/WeChat 全量回归。
