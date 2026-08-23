# Web API 参考

canonical Web app 由 `app.api.app` 组合，固定 API prefix 为 `/api/v1`。生产
`build_web_app` 同时提供认证、资料库、对话和浏览器伴侣路由；设置
`WEB_SERVE_STATIC=true` 时还提供 `web/dist` 的 SPA。OpenAPI 与交互文档位于：

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
| `GET` | `/api/v1/capabilities` | supported platforms、browser companion、login channels、save/archive/chat 能力和 batch 上限 |
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
- `WEB_FORWARDED_ALLOW_IPS`、`WEB_TRUSTED_PROXY_HOSTS` 只能列出明确受信任
  proxy；不允许 wildcard `*`。
- combined Web/MCP ASGI dispatcher 先按 `MCP_PATH` 选 MCP。Web cookie 不会
  认证 MCP，MCP Bearer 不会认证 Web。
- 服务器返回 SPA fallback 时，`/api/*` 未知路由仍返回 JSON 404，不会把 HTML
  当作 API 响应。
