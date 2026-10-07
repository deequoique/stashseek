# 配置参考

StashSeek Chat 从进程环境读取配置，也会由 `python-dotenv` 读取项目根目录
`.env`。单机 launcher 另外维护 `.env.runtime`。实际生效顺序是：

```text
进程环境 > .env > .env.runtime > Settings 默认值
```

把真实值放在未提交的 `.env`、进程环境或 secret manager 中。不要把密码、
API key、完整 DSN、MCP URL capability、浏览器 Bearer 或 HMAC secret 写入
`.env.example`、截图、工单或普通日志。

## 运行模式和私有文件

| 变量/文件 | 默认值 | 作用 |
| --- | --- | --- |
| `STASHSEEK_PROFILE` | `read`（未指定 launcher profile 时） | `read`、`full` 或 `langbot`；仅 launcher 使用 |
| `STASHSEEK_ENV` | `production` | 只能是 `development` 或 `production` |
| `STASHSEEK_LOG_DIR` | `.runtime/logs` | 私有日志目录；生产 systemd 通常设为 `/var/log/notebook-agent` |
| `STASHSEEK_LOG_MAX_BYTES` | `10485760` | 单个日志文件轮转上限 |
| `STASHSEEK_LOG_BACKUP_COUNT` | `5` | 轮转文件数量 |
| `STASHSEEK_LOG_RETRIEVAL_CONTENT` | `false` | 仅允许在 `development` 打开；生产配置会拒绝 |
| `.env.runtime` | — | launcher 生成，必须是 gitignored 且 mode `0600` |
| `MCP_TOKEN` | 未设置 | 仅传给一个 stdio MCP 子进程；不要写进 `.env.example` |

修改环境变量后，已有进程不会自动重新读取；重启对应的 app、worker、Beat
或 CLI 进程。修改数据库、Redis 或 MinIO 自身凭据还需要按对应服务的轮换
流程处理。

旧版部署仍可使用 `NOTEBOOK_AGENT_PROFILE`、`NOTEBOOK_AGENT_ENV` 和
`NOTEBOOK_AGENT_LOG_*`；它们是兼容回退键。当新旧键同时设置时，
`STASHSEEK_*` 始终优先。生产 systemd unit 名称、日志目录和其他既有
`notebook-agent` 运维路径属于稳定兼容契约，本次不会迁移。

## PostgreSQL

每个 profile 都需要 PostgreSQL。设置完整的 `DATABASE_URL` 时，它优先于
下面的分项变量。

| 变量 | 默认值 | secret | 说明 |
| --- | --- | --- | --- |
| `DATABASE_URL` | 未设置 | 是 | SQLAlchemy/运行时连接 URL；通常包含密码 |
| `MIGRATION_DATABASE_URL` | 未设置 | 是 | 仅一次性 Alembic 迁移使用；Neon pooled URL 必须配同 host family/database 的 direct URL |
| `POSTGRES_USER` | `postgres` | 否 | 本地 Compose 和 URL fallback |
| `POSTGRES_PASSWORD` | 无（launcher 可生成） | 是 | 未设置 `DATABASE_URL` 时必需 |
| `POSTGRES_DB` | `kb` | 否 | 本地数据库名 |
| `POSTGRES_HOST` | `localhost` | 否 | URL fallback；loopback 才可能由 launcher 管理 |
| `POSTGRES_PORT` | `5432` | 否 | URL fallback |

生产应用不能把 migration-only URL 传给长期运行的子进程。数据库迁移必须
通过单一 Alembic head；不要用生产数据上的自动 downgrade 代替兼容性迁移。

## Embedding、模型和 TLS

| 变量 | 默认值 | secret | 说明 |
| --- | --- | --- | --- |
| `ZHIPU_API_KEY` | 无 | 是 | `embedding-3` 的 query/ingestion embedding；launcher `init` 必需 |
| `EMBEDDING_MODEL` | `embedding-3` | 否 | embedding 模型名 |
| `EMBEDDING_ENDPOINT` | `https://open.bigmodel.cn/api/paas/v4/embeddings` | 否 | embedding endpoint |
| `EMBEDDING_DIMENSIONS` | `1536` | 否 | 必须与 PostgreSQL `vector(1536)` schema 对齐 |
| `EMBEDDING_BATCH_SIZE` | `64` | 否 | 每次 embedding 请求的输入数上限 |
| `AGENT_MODEL` | `openai:gpt-5-mini` | 否 | PydanticAI model 字符串 |
| `AGENT_API_KEY` | 未设置 | 是 | provider 需要时使用；也可使用 provider 原生环境变量 |
| `AGENT_BASE_URL` | 未设置 | 否/视 URL 而定 | OpenAI-compatible endpoint |
| `TLS_CA_BUNDLE` | 未设置 | 否 | 额外 PEM CA bundle；证书和 hostname 校验不会关闭 |
| `SSL_CERT_FILE` / `REQUESTS_CA_BUNDLE` | 系统环境 | 可能 | 子进程继承的标准 CA 配置 |

没有 `ZHIPU_API_KEY` 时，确定性的 inventory/identity 操作仍可运行；普通
知识问答和需要 embedding 的后台导入会失败闭合，而不是悄悄返回无依据答案。

## Agent 与上下文预算

这些值是安全上限，不是提高回答质量的替代品。

| 变量 | 默认值 | 作用 |
| --- | --- | --- |
| `AGENT_TIMEOUT_SECONDS` | `45` | 一个 Agent stage 的 wall-clock 上限 |
| `AGENT_TOOL_TIMEOUT_SECONDS` | `15` | Agent tool 调用上限 |
| `AGENT_REQUEST_LIMIT` | `8` | primary Agent 请求上限 |
| `AGENT_TOOL_CALLS_LIMIT` | `10` | tool call 总上限 |
| `AGENT_OUTPUT_TOKEN_LIMIT` | `3000` | stage 输出 token 上限（检索阶段开启思考时峰值约 2000，见 10-07-retrieval-agent-budget） |
| `AGENT_COMPOSER_MAX_TOKENS` | `1000` | evidence-backed answer Composer 每次尝试的 provider cap |
| `AGENT_STREAMING_ENABLED` | `true` | Web SSE 是否可用；关闭后使用 JSON 兼容路径 |
| `CONTEXT_MAX_TURNS` | `8` | 对话上下文 turn 数上限 |
| `CONTEXT_TOKEN_BUDGET` | `6000` | 对话历史 token budget |

检索 Agent 的正常收敛预算由运行时代码固定为总 5 次 retrieval、2 次 search、
3 次 neighbor expansion；Composer 最多针对同一份可信证据尝试 3 次。答案
Composer 没有检索或 action tool。

## Redis、MinIO 与导入队列

`full`、`langbot` 和保存/后台导入需要以下配置。Redis 是 Celery broker/result
backend；MinIO 保存受租户前缀保护的原始字幕对象。

| 变量 | 默认值 | secret | 作用 |
| --- | --- | --- | --- |
| `REDIS_URL` | 由 `REDIS_HOST/PORT/DB` 组成 | 通常是 | 远程 broker URL；设置后 launcher 不管理对应 Redis |
| `REDIS_HOST` | `localhost` | 否 | URL fallback |
| `REDIS_PORT` | `6379` | 否 | URL fallback |
| `REDIS_DB` | `0` | 否 | URL fallback |
| `MINIO_ENDPOINT_URL` | `http://localhost:9000` | 否 | S3-compatible endpoint |
| `MINIO_ROOT_USER` | 无（本地 launcher 可生成） | 是 | object store 凭据 |
| `MINIO_ROOT_PASSWORD` | 无（本地 launcher 可生成） | 是 | object store 凭据 |
| `MINIO_BUCKET` | `kb-raw` | 否 | 原始字幕 bucket |
| `MINIO_API_PORT` | `9000` | 否 | 本地 Compose 的 MinIO API host mapping；应用使用 `MINIO_ENDPOINT_URL` |
| `MINIO_CONSOLE_PORT` | `9001` | 否 | 本地 Compose 的 MinIO console host mapping |
| `BROKER_PUBLISH_TIMEOUT_SECONDS` | `5` | 否 | 一次 publish 的 bounded budget |
| `BROKER_PUBLISH_MAX_RETRIES` | `1` | 否 | broker publish 重试次数 |
| `INGEST_MAX_ACTIVE_PER_USER` | `10` | 否 | 每租户 active dispatch 上限 |
| `INGEST_DAILY_NEW_ITEM_LIMIT` | `50` | 否 | 每租户每日新条目上限 |
| `INGEST_MAX_ITEMS_PER_USER` | `1000` | 否 | 每租户条目总量上限 |
| `INGEST_MAX_ACTIVE_GLOBAL` | `100` | 否 | 全局 active dispatch 上限 |
| `INGEST_DAILY_NEW_ITEM_LIMIT_GLOBAL` | `300` | 否 | 全局每日新条目上限 |
| `INGEST_DAILY_DISPATCH_LIMIT_PER_USER` | `100` | 否 | 每租户每日 dispatch 上限 |
| `INGEST_DAILY_DISPATCH_LIMIT_GLOBAL` | `1000` | 否 | 全局每日 dispatch 上限 |
| `INGEST_MAX_RAW_TRANSCRIPT_BYTES` | `5000000` | 否 | 单条原始字幕大小上限 |
| `INGEST_MAX_CUES_PER_ITEM` | `50000` | 否 | 单条字幕 cue 上限 |
| `INGEST_MAX_TEXT_CHARS_PER_ITEM` | `1000000` | 否 | 单条文本字符上限 |
| `INGEST_MAX_SEGMENTS_PER_ITEM` | `5000` | 否 | 最终 searchable segment 上限 |
| `INGEST_MAX_EMBEDDING_CHARS_PER_ITEM` | `2000000` | 否 | 单条 embedding 文本预算 |

completion 与 notification delivery/repair 的维护参数也由 worker/Beat 读取：

| 变量 | 默认值 | 作用 |
| --- | --- | --- |
| `INGEST_NOTIFICATION_INTERVAL_SECONDS` | `600` | 事件入队失败时的 PostgreSQL completion notification repair 周期；正常投递不等待此 sweep |
| `INGEST_NOTIFICATION_BATCH_SIZE` | `20` | 每次 poller claim 数量 |
| `INGEST_NOTIFICATION_CLAIM_TIMEOUT_SECONDS` | `300` | poller claim 超时 |
| `INGEST_NOTIFICATION_MAX_DURATION_SECONDS` | `8` | 一次 repair sweep 总预算，必须小于 interval |
| `INGEST_NOTIFICATION_MAX_ATTEMPTS` | `5` | notification delivery attempt 上限 |
| `INGEST_NOTIFICATION_RETRY_BASE_SECONDS` | `5` | retry backoff 起点 |
| `INGEST_NOTIFICATION_RETRY_MAX_SECONDS` | `300` | retry backoff 上限 |
| `INGEST_COMPLETION_INTERVAL_SECONDS` | `60` | 旧 completion publisher 的 rollback/schema compatibility 配置 |
| `INGEST_COMPLETION_BATCH_SIZE` | `20` | 旧 completion publisher batch 大小 |
| `INGEST_COMPLETION_CLAIM_TIMEOUT_SECONDS` | `300` | 旧 completion publisher claim 超时 |
| `INGEST_COMPLETION_MAX_DURATION_SECONDS` | `30` | 旧 completion publisher sweep budget |

`INGEST_COMPLETION_*` 保留用于 rollback/schema compatibility；当前权威的
notification delivery 由 terminal completion event 通过 Celery 事件入队，使用
`INGEST_NOTIFICATION_*` 的 delivery ledger。`INGEST_NOTIFICATION_INTERVAL_SECONDS`
只控制有界 repair sweep，不要重新启用已退役的 `ingest-completion` queue。

worker 必须同时监听 `ingest` 和 `maintenance`。Beat 负责周期性 purge、
completion notification 和维护任务；不要让新的部署监听已经退役的
`ingest-completion` queue。

## MCP transport

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `MCP_HOST` | `127.0.0.1` | Streamable HTTP 监听地址；生产通常保持 loopback |
| `MCP_PORT` | `8000` | Streamable HTTP 端口 |
| `MCP_PATH` | `/mcp` | 非根绝对路径；无 query、fragment、末尾 `/` |
| `MCP_URL_TOKEN_MODE` | `false` | 为仅支持 URL 的 client 开启 HTTPS `/mcp/c/<token>` 兼容路径 |
| `STASHSEEK_ALLOW_NON_LOOPBACK` | 未设置/false | launcher 非 loopback MCP 绑定的显式确认 |

普通 HTTP client 应使用 `Authorization: Bearer <token>`。`?token=` 永远不
接受；path token 会出现在代理和基础设施 URL 中，必须把它当作 secret 并在
怀疑泄漏后 rotate/revoke。`MCP_TOKEN` 只属于 stdio 子进程环境。

## Web 与登录

生产 canonical Web runtime 使用 email OTP；开发/迁移兼容路径仍支持渠道批准
登录。二者都使用同一类 opaque session 和 CSRF cookie，但配置入口不同。

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `WEB_AUTH_ENABLED` | `false` | 生产必须为 `true` |
| `WEB_PUBLIC_ORIGIN` | 未设置 | 开启 email Web auth 时必需的精确 HTTPS origin，无末尾 `/` |
| `WEB_AUTH_SECRET` | 未设置 | 至少 32 字符；用于登录码/会话密钥材料 |
| `WEB_COOKIE_SECURE` | `true` | 必须保持 true，以满足 `__Host-` cookie 合约 |
| `WEB_API_PREFIX` | `/api/v1` | 固定值，不能自定义 |
| `EMAIL_PROVIDER` | 未设置 | 生产 Web auth 需 `resend` 或 `smtp` |
| `RESEND_API_KEY` / `RESEND_FROM_EMAIL` | 未设置 | `EMAIL_PROVIDER=resend` 时必需 |
| `SMTP_HOST` / `SMTP_PORT` | 未设置 / `587` | `EMAIL_PROVIDER=smtp` 时使用 |
| `SMTP_USERNAME` / `SMTP_PASSWORD` / `SMTP_FROM_EMAIL` | 未设置 | SMTP 凭据和发件人 |
| `SMTP_STARTTLS` | `true` | SMTP STARTTLS 开关 |
| `WEB_SESSION_TTL_SECONDS` | `2592000` | email session TTL |
| `WEB_SESSION_CACHE_TTL_SECONDS` | `60` | Redis session projection TTL；`0` 表示只查 PostgreSQL |
| `WEB_AUTH_CODE_TTL_SECONDS` | `600` | email code TTL |
| `WEB_AUTH_MAX_ATTEMPTS` | `5` | email code 尝试上限 |
| `WEB_AUTH_RESEND_SECONDS` | `60` | 同一邮箱 resend 间隔 |
| `WEB_AUTH_EMAIL_WINDOW_SECONDS` / `WEB_AUTH_EMAIL_MAX_SENDS` | `900` / `3` | 邮箱发送限流窗口/上限 |
| `WEB_AUTH_IP_WINDOW_SECONDS` / `WEB_AUTH_IP_MAX_SENDS` | `3600` / `10` | requester IP 限流窗口/上限 |
| `RESEND_TIMEOUT_SECONDS` | `10` | `EMAIL_PROVIDER=resend` 的网络 timeout |
| `SMTP_TIMEOUT_SECONDS` | `10` | `EMAIL_PROVIDER=smtp` 的网络 timeout |
| `WEB_TRUSTED_PROXY_HOSTS` | 空 | 允许提供 `X-Forwarded-For` 的明确 proxy peer |
| `WEB_HOST` / `WEB_PORT` | `127.0.0.1` / `8000` | 独立 `web-server` listener |
| `WEB_SERVE_STATIC` | `true` | 是否从 `WEB_STATIC_DIR` 提供 SPA |
| `WEB_STATIC_DIR` | `web/dist` | SPA 构建目录 |
| `WEB_FORWARDED_ALLOW_IPS` | `127.0.0.1` | uvicorn 可信 forwarded-header 来源；不能是 `*` |
| `WEB_PUBLISH_BUDGET_SECONDS` | `5` | Web 保存/重试 publish budget |
| `CHANNEL_LINK_TTL_SECONDS` | `600` | Web compatibility link token 的有效期 |

`WEB_ORIGIN` 是旧的 channel-approved Web 登录和兼容构建入口；生产的
`app.api.runtime.build_web_app` 使用 `WEB_PUBLIC_ORIGIN` + email auth。两者都
必须是精确 origin，不能包含 credentials、path、query、fragment 或末尾 `/`；
不要把旧配置误当成生产 email auth 的替代品。

## 浏览器伴侣

浏览器伴侣凭据只授予 `capture:write`，不能读取 Web、MCP 或资料库管理接口。

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `BROWSER_COMPANION_ALLOWED_ORIGINS` | 空 | 逗号分隔的精确 `chrome-extension://<32-char-id>`；生产不要使用 wildcard |
| `BROWSER_COMPANION_PAIRING_TTL_SECONDS` | `600` | pairing challenge TTL |
| `BROWSER_COMPANION_GRANT_TTL_SECONDS` | `7776000` | capture grant TTL（90 日） |
| `BROWSER_COMPANION_MAX_REQUEST_BYTES` | `5500000` | capture body 上限 |

开发时的 `chrome-extension://*` 仅在 `STASHSEEK_ENV=development` 且 Web
只绑定 loopback 时允许。当前 extension 的平台范围是 YouTube 和
NTULearn/Kaltura；插件在浏览器本地读取已授权字幕，服务端只收到规范化 cue、
公开 metadata 和无秘密 canonical reference。

## Channel Gateway 与 LangBot

Gateway 是可选的 out-of-process bridge，必须绑定 loopback。根 `.env` 只放
StashSeek Chat 端配置；已安装 LangBot plugin 目录的私有 `.env` 另放 plugin
端配置。

| 变量 | 默认值 | 放置位置 |
| --- | --- | --- |
| `CHANNEL_GATEWAY_SECRET` | 无（launcher 可生成） | 根 `.env` 和 plugin 私有 `.env` 的相同 secret |
| `CHANNEL_GATEWAY_HOST` | `127.0.0.1` | 根 `.env`；必须 loopback |
| `CHANNEL_GATEWAY_PORT` | `8765` | 根 `.env` |
| `LANGBOT_OUTBOUND_BASE_URL` | `http://127.0.0.1:5300` | 根 `.env`；非 loopback 必须 HTTPS |
| `LANGBOT_OUTBOUND_API_KEY` | 未设置 | 根 `.env`，只用于通知 poller |
| `LANGBOT_OUTBOUND_TIMEOUT_SECONDS` | `10` | 根 `.env` |
| `CHANNEL_GATEWAY_URL` | 未设置 | LangBot plugin 私有 `.env`，通常 `/v1/messages` |
| `KB_BOT_CHANNELS` | 未设置 | LangBot plugin 私有 `.env`；bot UUID → `telegram`/`wechat` |

LangBot 4.10.6 部署还需要 patched runtime 的 required plugin readiness；
一个 shell `sleep` 不能替代该检查。不要把 bot UUID 映射、Telegram/微信外部
用户 ID 或 plugin secret 复制到 root `.env` 或日志。

## 连接器专属限制

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `YOUTUBE_FETCH_TIMEOUT_SECONDS` | `30` | yt-dlp metadata/subtitle bounded timeout |
| `YOUTUBE_PROXY_URL` | 未设置 | 可选；只允许无凭据 loopback HTTP URL 且必须有显式端口，例如 `http://127.0.0.1:18080` |
| `BILIBILI_FETCH_TIMEOUT_SECONDS` | `30` | Bilibili yt-dlp timeout |
| `TRASH_RETENTION_DAYS` | `30` | 回收站保留时长 |
| `TRASH_PURGE_INTERVAL_SECONDS` | `3600` | purge 周期 |
| `TRASH_PURGE_BATCH_SIZE` | `20` | 一次 purge 数量，最大 100 |
| `TRASH_PURGE_CLAIM_TIMEOUT_SECONDS` | `1800` | purge claim 超时 |
| `TRASH_PURGE_MAX_DURATION_SECONDS` | `30` | 一次 purge 总预算 |
| `TRASH_PURGE_OBJECT_TIMEOUT_SECONDS` | `10` | 单个 object 删除预算 |

YouTube proxy 只注入 yt-dlp 和 bounded subtitle 子进程，并移除 ambient
`ALL_PROXY`；proxy 失败不会回退到直连。Bilibili 服务端只消费 yt-dlp
投影的 inline SRT；URL-only 或登录字幕进入 `needs_extension`，不会扩大
服务端 cookie/字幕 URL 获取范围。
