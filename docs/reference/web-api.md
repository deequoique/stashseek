# Web API 参考

canonical Web app 由 `app.api.app` 组合，固定 API prefix 为 `/api/v1`。生产
`build_web_app` 同时提供认证、资料库、对话和浏览器伴侣路由；设置
API-only runtime 永远不挂载静态客户端；插件工作区从 `extension/dist` 独立构建和分发。OpenAPI 与交互文档位于：

```text
GET /api/v1/openapi.json
GET /api/v1/docs
```

所有浏览器错误都使用 bounded envelope：

```json
{"code":"session_invalid","message":"登录已失效，请重新登录"}
```

不要依赖 `detail` 中的内部异常、SQL、tenant、session 或 provider 信息。

## 公共端点

| 方法 | 路径 | 结果 |
| --- | --- | --- |
| `GET` | `/api/v1/health` | `{"status":"ok"}`；只表示 HTTP app 存活 |
| `GET` | `/api/v1/capabilities` | supported platforms、browser companion、login channels、`external_login_providers`、save/archive/chat 能力和 batch 上限 |
| `GET` | `/api/v1/openapi.json` | 当前 app 的 OpenAPI schema |
| `GET` | `/api/v1/docs` | FastAPI Swagger UI |

`health` 不等于 Redis、MinIO、worker 或 provider ready；需要 mutation readiness
时应检查对应 runtime/MCP status。

## 认证模式

### 生产 email OTP

当 `WEB_AUTH_ENABLED=true` 时，生产 app 使用 `WEB_PUBLIC_ORIGIN` 指定的精确
HTTPS origin 和 email code。流程如下：

```text
POST /auth/challenges {email}
        ↓  email provider
POST /auth/verify {email, code}
        ↓
__Host-kb_session + __Host-kb_csrf
```

```http
POST /api/v1/auth/challenges
Origin: https://app.example.com
Content-Type: application/json

{"email":"person@example.com"}
```

challenge 返回 `{"status":"accepted"}`；正常、未知、限流邮箱不会通过这个
响应被区分。验证码是 6 位数字：

```http
POST /api/v1/auth/verify
Origin: https://app.example.com
Content-Type: application/json

{"email":"person@example.com","code":"123456"}
```

成功响应只投影 `authenticated`、`login_channel="email"` 和 `expires_at`，并
设置两个 `__Host-` cookie。`GET /api/v1/auth/session` 读取 session cookie；
`DELETE /api/v1/auth/session` 是 unsafe mutation，需要精确 `Origin` 和同时
匹配 CSRF cookie/header 的 `X-CSRF-Token`。

email session、CSRF 原文只在浏览器 cookie 中；数据库和可选 Redis cache 只存
hash/受限 projection。Redis miss、损坏或故障会回退 PostgreSQL，不能让缓存
成为身份 authority。

### 可选外部网站登录

启用并配置的提供方枚举是 `microsoft`、`wechat_website`、`qq`，单独出现在
`external_login_providers`；保留原有 `web_login_channels` 语义。首次授权后只形成
匿名待绑定流程，邮箱验证码和显式确认在同一事务内将提供方身份绑定到该邮箱的
原账户；后续授权直接使用已绑定身份。提供方返回的邮箱、昵称或 UnionID 不能
自动选取资料库。平台申请和真实域名验收见[配置指南](../how-to/configure-external-login.md)。

| 方法 | 路径 | 请求/结果 |
| --- | --- | --- |
| `POST` | `/api/v1/auth/external/{provider}/start` | 同源原生表单；精确 Origin；303 跳转到固定授权入口或 `/login/external` 安全失败页 |
| `GET` | `/api/v1/auth/external/{provider}/callback` | 仅 OAuth 协议回调；校验唯一、有界的 code/state/error；303 离开含凭据 URL |
| `GET` | `/api/v1/auth/external/pending` | 当前浏览器流程的安全状态 projection |
| `POST` | `/api/v1/auth/external/pending/challenges` | `{email}`；发送此绑定流程专用验证码；正常/限流同为 accepted |
| `POST` | `/api/v1/auth/external/pending/verify` | `{email,code,confirm_binding:true}`；原子绑定并返回 canonical Session |
| `DELETE` | `/api/v1/auth/external/pending` | 取消流程、清除流程 cookie；204 |

流程 cookie 为 `__Host-kb_external_flow`（HttpOnly）和 `__Host-kb_external_csrf`，
Secure、SameSite=Lax、Path=/、无 Domain。pending 的写操作要求精确 Origin 和
独立的 flow CSRF cookie/header/服务端哈希验证，不能使用普通 Session CSRF。
原生 start 无需已有 Session CSRF；callback 是经过专门校验的 GET 协议例外。

pending 只包含 `status`、`provider`、`expires_at`、可选 `error_code`。状态枚举为
`created`、`exchanging`、`email_required`、`completed`、`failed`、`cancelled`、
`expired`；未知/外来 cookie 返回 expired 安全状态，不创建用户。响应不包含邮箱、
内部 ID、state、flow handle、授权 code、SDK 内容或 token。

外部来源 Session 的公开 `login_channel` 是提供方枚举；其授权 tenant 仍是原邮箱
anchor。服务端每次用一条数据库 join 验证来源、owner、anchor、启用状态及配置，
不将这类 Session 放进 Redis。旧渠道 resolver 不得绕过这些检查。完成后浏览器
仍使用普通 `__Host-kb_session` / `__Host-kb_csrf` 访问资料库和注销。

同一浏览器改用普通邮箱验证码成功登录时，会作废其 cookie 对应的外部授权流程；
旧回调即使稍后完成网络请求，也不能覆盖新的有效登录。错误邮箱验证码不会取消
仍可继续的外部流程。已有有效 Session 时需要明确继续或退出，不能隐式绑定。

错误仍为固定 `{code,message}`：错误验证码 `verification_failed` 为 401，本地
表单可恢复；既有/变化 Session 或身份冲突为 409；流程/CSRF 失败为 403；提供方
或发信不可用为 503。回调失败始终先离开协议 URL，再由安全状态提供重试指引。
应用请求及反向代理日志都必须省略/脱敏 callback query；QQ/微信服务端 GET URL
也包含凭据，不能记录完整 URL。流程默认十分钟，不会延长为长期浏览器凭据。

### 开发/迁移兼容的渠道批准登录

当生产 Web auth 没有启用时，兼容 router 可以用 `WEB_ORIGIN` 与
`WEB_LOGIN_CHANNELS=telegram,wechat`：

```text
POST /api/v1/auth/challenges       {target_channel}
POST /api/v1/auth/challenges/status {public_id} + Bearer browser_secret
POST /api/v1/auth/sessions          {public_id} + Bearer browser_secret
GET  /api/v1/auth/session
DELETE /api/v1/auth/session         + CSRF
```

channel challenge 由 Telegram/WeChat 的 `/web-login <code>` 批准。它是兼容
路径；生产 `build_web_app` 要求开启 email auth，不应把它当作生产默认登录
方式。

## 资料库端点

这些路由都要求有效 `__Host-kb_session`；写操作还要求精确 Origin、CSRF cookie
与 `X-CSRF-Token`。公共 item id 是 opaque `public_id`，不接受跨租户内部 id。

| 方法 | 路径 | 关键参数/请求体 | 说明 |
| --- | --- | --- | --- |
| `GET` | `/api/v1/library/items` | `search`、`collection`、`lifecycle`、`include_archived`、`sort=saved_desc\|saved_asc\|title_asc`、`page`、`page_size=1..100` | 返回当前租户分页资料库 |
| `POST` | `/api/v1/library/items:batch` | `{urls:[...], why_saved?}`；最多 10 个 URL | 需要 `Idempotency-Key` + CSRF；逐项返回 `queued`/`already_exists`/安全失败 |
| `GET` | `/api/v1/library/items/{item_public_id}` | — | 资料项 metadata、生命周期和可用 actions |
| `PATCH` | `/api/v1/library/items/{item_public_id}` | `{why_saved}` | 更新备注 |
| `POST` | `/api/v1/library/items/{item_public_id}:archive` | — | 归档 |
| `POST` | `/api/v1/library/items/{item_public_id}:restore` | — | 恢复 |
| `POST` | `/api/v1/library/items/{item_public_id}:retry` | — | 需要 `Idempotency-Key` + CSRF；重试后台导入 |
| `GET` | `/api/v1/ingest-dispatches/{dispatch_public_id}` | — | 当前租户可见的 queue dispatch 状态 |
| `GET` | `/api/v1/library/items/{item_public_id}/transcript` | `limit=1..100`、`cursor` | 分页全文 blocks、时间戳和下一页 cursor |

资料库 item 投影包含 `platform`、`kind`、canonical URL、title/author、时长、
语言、tags/chapters、cover、保存时间、备注、`text_source`、lifecycle、
`error_code`、latest dispatch public id 等。原始 object key、tenant id、provider
响应不会返回。

`lifecycle` 的公开集合是 `archived`、`ready`、`needs_action`、`failed`、
`processing`、`queued`。`needs_action` 表示需要浏览器伴侣或其他后续处理，
不是已生成 ASR 的承诺。

整理状态的补充字段（都是可选字段，旧客户端可以忽略）：

- `stages`：按顺序列出各整理阶段。每项是 `{key, status, detail?, reason?}`，`key` 是普通字符串
  （当前只有 `body`，以后会增加 `classify`、`summary`、`movie`），`status` 为
  `pending`/`running`/`done`/`failed`/`skipped`，`body` 阶段运行时 `detail` 为
  `queued`/`fetching`/`transcribing`/`indexing`。客户端应忽略不认识的 `key`，不显示 `skipped` 的阶段。
- `reason`：正文没整理完（`failed` 或 `needs_action`）的原因 `{code, category, retry_useful}`，
  只会取自服务端固定的原因表，未知错误统一是 `ingestion_failed`。`category` 为 `no_speech`、
  `no_transcription`、`fetch_failed`、`asr_failed`、`quota`、`too_large`、`login_required`、`error`。
- `body_searchable`：正文是否已进入检索索引（等同于内部状态 `ready`，不受归档影响）。
  后面的阶段失败不会改变它。
- 列表响应的 `unsearchable_total`、`processing_total`：不含归档和已删除的收藏里，
  正文不可检索（`needs_action`/`failed`）和仍在整理（`queued`/`processing`）的条目数，
  不受筛选条件影响。

`available_actions` 里有 `retry` 的条件：`lifecycle` 是 `failed` 或 `needs_action`、没有进行中的整理任务、
未归档，并且原因类别不是 `login_required`（Bilibili 登录字幕需要先登录，再在视频页重新收藏）。
`ready` 的条目即使后面的阶段失败，也不能重新整理。

`GET /api/v1/capabilities` 的 `transcription_platforms` 列出服务器会做语音转写的平台
（`ASR_ENABLED=false` 时为空数组）。

## 对话和会话链接端点

对话路由使用同一 Web session/CSRF boundary，并把一次执行交给一个
`ChannelService`。JSON 与 SSE 不会各自启动第二个 Agent。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/v1/conversations?limit=1..50&cursor=...` | 当前 Web identity 的 thread 摘要 |
| `GET` | `/api/v1/conversations/{thread_id}/turns` | 该 thread 的 completed turns、回答、引用和 action result |
| `DELETE` | `/api/v1/conversations/{thread_id}` | 永久删除当前租户 thread；需要 CSRF |
| `POST` | `/api/v1/conversations/{conversation_id}/messages` | JSON 问答；body `{message_id,text}`，text 最多 16,000 字符 |
| `POST` | `/api/v1/conversations/{conversation_id}/messages/stream` | SSE 问答；需要 `AGENT_STREAMING_ENABLED=true` |
| `POST` | `/api/v1/conversations/{conversation_id}/reset` | 发送 `/new`，清除该 conversation 的上下文 |
| `POST` | `/api/v1/link-tokens` | body `{target_channel:"telegram"|"wechat"}`，生成短期绑定码 |
| `POST` | `/api/v1/link-tokens/consume` | body `{token}`；成功后当前 Web session 会被删除 |

### JSON 问答

```json
{
  "message_id": "client-generated-id",
  "text": "这个视频如何解释检索边界？"
}
```

成功 `ConversationResponse` 至少包含 `status`、`text`、`citations`、
`action_results`、`thread_id` 和可选 `error_code`。citation 只投影 title、
excerpt、真实 URL 和 `start_sec`；不会投影内部 segment/item id。

### SSE 问答

`messages/stream` 的 media type 是 `text/event-stream`。公开事件包括
`started`、`activity`、`step_started`、`step_completed`、`plan_updated`、
`section_started`、`text_delta`、`section_completed`、`section_aborted`、
`completed`、`error` 和 `cancelled`。每条事件包含 request/message/sequence；
provider chunks、tool 参数、hidden reasoning 和异常文本不会越过边界。

一个 grounded section 只有在 citation metadata 被服务端授权后才会公开；
客户端断开、取消、超时或 incomplete section 不会持久化半条 conversation turn。

## 浏览器伴侣端点

配对和设备管理的 Web 侧路由需要 Web session/CSRF；`extension/*` 路由由精确
扩展 Origin 和专用 capture Bearer 保护。capture Bearer 只具备
`capture:write`，不能替代 Web cookie、MCP grant 或 Channel Gateway HMAC。

| 方法 | 路径 | 凭据/说明 |
| --- | --- | --- |
| `POST` | `/api/v1/browser-companion/extension/pairings` | 扩展 Origin；body `challenge`、`client_label`、`client_version` |
| `GET` | `/api/v1/browser-companion/extension/pairings/{pairing_id}` | 只读状态；MV3 可在这个精确 GET 缺少 Origin，其余 extension route 不行 |
| `POST` | `/api/v1/browser-companion/extension/pairings/{pairing_id}:exchange` | 扩展 Origin；body `verifier`；一次性返回 Bearer |
| `POST` | `/api/v1/browser-companion/pairings/{pairing_id}:approve` | Web session + CSRF；批准 pairing |
| `GET` | `/api/v1/browser-companion/devices` | Web session；列出当前租户设备 projection |
| `DELETE` | `/api/v1/browser-companion/devices/{device_id}` | Web session + CSRF；撤销设备 |
| `POST` | `/api/v1/browser-companion/extension/captures` | `Authorization: Bearer` + `Idempotency-Key`；提交规范化 `capture.v1` |
| `DELETE` | `/api/v1/browser-companion/extension/grant` | capture Bearer；撤销自身 token |

capture payload 只允许 `youtube`、`ntu_kaltura`，包含公开 metadata、规范化
caption cues 和 cue hash。请求受 `BROWSER_COMPANION_MAX_REQUEST_BYTES` 和
ingestion limits 约束；签名字幕 URL、Cookie、SAML、Kaltura KS、授权 header
和音视频 bytes 不得进入请求或日志。

## Origin、cookie 和 CSRF 规则

- unsafe Web 请求要求 `Origin == WEB_PUBLIC_ORIGIN`（兼容路径为 `WEB_ORIGIN`）
  和 `X-CSRF-Token` 与 CSRF cookie 的 double-submit 校验。
- 登录前的邮箱验证码请求、原生外部 start 和外部 callback 按上方各自的严格
  Origin/协议规则处理；pending mutation 使用独立 flow CSRF，不能借用现有
  Session 的 cookie/header。其他 protected mutation 继续使用普通 Session CSRF。
- `WEB_FORWARDED_ALLOW_IPS`、`WEB_TRUSTED_PROXY_HOSTS` 只能列出明确受信任
  proxy；不允许 wildcard `*`。
- combined Web/MCP ASGI dispatcher 先按 `MCP_PATH` 选 MCP。Web cookie 不会
  认证 MCP，MCP Bearer 不会认证 Web。
- API-only runtime 不提供客户端 fallback；`/api/*` 未知路由仍返回 JSON 404。
