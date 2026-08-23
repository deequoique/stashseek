# 排查问题

先记录 release SHA、schema head、组件状态和稳定错误码；不要为了排查把问题正文、
字幕、raw token、session cookie、DSN、provider body 或完整 URL 写入日志。

使用 launcher-owned `.env.runtime` 时，先定义[首次运行教程中的 `notebook_run`
helper](../tutorials/first-run.md)。本页需要应用配置的 operator 命令以
`notebook_run` 开头；只维护根 `.env` 的手工部署可以去掉该前缀。

## 快速分流

| 症状 | 先检查 | 下一步 |
| --- | --- | --- |
| launcher 无法启动 | `status`、端口、migration | [启动器与 profile](../reference/runtime-profiles.md) |
| MCP 能连但只有只读工具 | grant scope、worker queues、readiness | [MCP 连接指南](connect-mcp-client.md) |
| Web 打开但不能登录 | exact HTTPS origin、email provider、cookie | [Web API 参考](../reference/web-api.md) |
| 视频一直“正在整理” | worker、Redis、MinIO、dispatch 状态 | 见下方 ingestion 小节 |
| 浏览器伴侣不能配对 | API origin、extension origin、pairing TTL | [浏览器伴侣](use-browser-companion.md) |
| LangBot 回复“渠道暂不可用” | gateway、required plugin、adapter readiness | [LangBot](connect-langbot.md) |

## 1. launcher、端口和配置

```bash
./scripts/notebook-agent status
./scripts/notebook-agent logs supervisor --lines 120
./scripts/notebook-agent logs mcp --lines 120
```

检查 `MCP_HOST/MCP_PORT`、`CHANNEL_GATEWAY_HOST/PORT` 是否与已有服务冲突。生产
默认只允许 loopback；非 loopback 绑定需要显式 `NOTEBOOK_AGENT_ALLOW_NON_LOOPBACK=true`
和已审核的 TLS/代理边界。不要通过更换端口来掩盖同一服务重复启动，也不要按进程名
批量 kill。

如果 status 提示 configuration mismatch，说明当前命令的环境变量指向了与 supervisor
记录不同的数据库、broker、object store、Compose project 或 listener。恢复同一份环境
后再检查，不要让 status 误探测另一套数据。

## 2. migration 或依赖不 ready

```bash
.venv/bin/alembic current
.venv/bin/alembic check
docker compose ps
```

若使用 Celery：

```bash
.venv/bin/celery -A app.ingest.tasks.celery_app inspect ping
.venv/bin/celery -A app.ingest.tasks.celery_app inspect active_queues
```

worker 必须返回 `pong` 并监听 `ingest`、`maintenance`。MinIO 不只要 liveness，还要
通过 ready 与目标 bucket admission；Redis 要确认持久化写入策略。外部 PostgreSQL、
Redis、MinIO 不由 launcher 停止，修复 provider/网络后再重新执行 readiness。

## 3. MCP 认证和工具发现

检查 grant 元数据，不要打印 token：

```bash
notebook_run .venv/bin/python -m app.cli mcp-grant show <grant-id>
notebook_run .venv/bin/python -m app.cli mcp-grant list --limit 100
```

排查顺序：

1. token 是否完整、未过期、未 revoke/disable/rotate；
2. 客户端发送 `Authorization: Bearer <token>`，而不是 Web cookie；
3. stdio 是否把 token 放在 `MCP_TOKEN`，且 stdout 没有 wrapper 日志；
4. HTTP 的 `MCP_PATH` 是否为不带 query/fragment/trailing slash 的绝对路径；
5. 如果启用 URL token mode，是否为 HTTPS `/mcp/c/<token>`，且没有 `?token=`；
6. `read` scope 是否误调用了 mutation；`full` 是否因 worker/readiness 不可用而隐藏写工具。

疑似 token 暴露时立即 `rotate` 或 `revoke`，清理代理/access/analytics 中的 capability
URI，并重新配置客户端。不要把 401 改成匿名访问或固定管理员租户。

## 4. Web 登录、CSRF 和空页面

```bash
curl --fail https://<public-origin>/api/v1/health
curl --fail https://<public-origin>/api/v1/capabilities
```

确认：

- `WEB_AUTH_ENABLED=true` 时 `WEB_PUBLIC_ORIGIN` 是精确 HTTPS origin，无路径/query/
  末尾斜杠；
- `WEB_AUTH_SECRET` 至少 32 字符，生产 `EMAIL_PROVIDER` 和邮件凭据齐全；
- `WEB_COOKIE_SECURE=true`，浏览器使用同一个 public origin；
- reverse proxy 保留 `Origin`、`Sec-Fetch-Site`、Cookie、`Set-Cookie` 和
  `X-CSRF-Token`，没有 wildcard CORS；
- API-only split 部署的未知 `/api/*` 返回 JSON 404，而不是 SPA HTML。

401/403 时先重新登录、清理旧 session 并确认浏览器时间；不要复制 cookie 或关闭
CSRF。登录挑战故意不区分邮箱是否存在；不要把“已接受”误读成账号一定存在。

## 5. 视频入队但迟迟不 ready

先区分 `ContentItem.ready` 与 dispatch 的 `pending/enqueued/running/completed/failed`。
用 Web 的状态筛选或受控日志查看稳定错误码，不要重复发送同一 URL：

```bash
./scripts/notebook-agent logs worker --lines 160
./scripts/notebook-agent logs beat --lines 160
```

检查 Redis broker、MinIO bucket、worker queues、schema head、embedding key/endpoint/
dimensions。YouTube/Bilibili provider 的 rate limit、timeout、no-caption 和 queue failure
是不同状态；修复 TLS CA、provider 或队列后，再用 Web 的“重试”或 MCP 的明确 retry
操作。不要关闭证书/hostname 校验，不要把原始字幕或 provider body 加进日志。

服务器端只支持当前 connector 声明的来源。Bilibili 的登录后或 URL-only 字幕目前
没有可用的浏览器捕获 adapter，应保留 `needs_extension` 状态并等待受支持的导入路径，
不要转到当前不支持 Bilibili 的浏览器伴侣。NTULearn 及受限 YouTube 可以使用
[浏览器伴侣](use-browser-companion.md)，而不是无限重试服务器抓取。

## 6. 完成通知没有送达

视频已经 ready、但原聊天入口没有收到完成通知时，先确认 Beat 与监听
`maintenance` 的 worker 都在运行。每次 poller tick 会写入
`notification_poller_heartbeat`；它只包含计数、耗时和 backlog age，不含用户、标题、
URL 或消息正文：

```bash
./scripts/notebook-agent logs worker --lines 200 \
  | grep '"event":"notification_poller_heartbeat"' \
  | tail -n 5
```

`oldest_eligible_backlog_age_seconds=0` 表示没有可领取事件。长时间没有 heartbeat 时，
先检查唯一 Beat、worker 的 `maintenance` queue 和 PostgreSQL；heartbeat 不是 MCP
readiness，不要通过重启 MCP 处理。

当前通知由 PostgreSQL completion event 与 delivery ledger 驱动。使用受保护的 operator
连接查询计数和时间年龄；不要读取或导出通知正文、目标地址或用户信息。下面的 `300`
应与 `INGEST_NOTIFICATION_CLAIM_TIMEOUT_SECONDS` 一致：

```bash
docker compose exec -T postgres psql \
  -U "${POSTGRES_USER:-postgres}" -d "${POSTGRES_DB:-kb}" \
  -v claim_timeout_seconds="${INGEST_NOTIFICATION_CLAIM_TIMEOUT_SECONDS:-300}" \
  -c "
SELECT count(*) AS eligible_count,
       COALESCE(EXTRACT(EPOCH FROM (clock_timestamp() - min(e.created_at)))::bigint, 0)
         AS oldest_eligible_backlog_age_seconds
FROM ingest_completion_event AS e
LEFT JOIN ingest_completion_delivery AS d
  ON d.event_id = e.id
 AND d.handler_key = 'source-channel.notification.v1'
WHERE d.id IS NULL
   OR (d.status = 'failed' AND d.next_attempt_at IS NOT NULL
       AND d.next_attempt_at <= now())
   OR (d.status = 'claimed'
       AND (d.claimed_at IS NULL OR d.claimed_at <= now()
            - (:'claim_timeout_seconds' || ' seconds')::interval));

SELECT count(*) FILTER (WHERE status = 'failed') AS failed_count,
       count(*) FILTER (WHERE status = 'failed' AND disposition = 'retry_exhausted')
         AS retry_exhausted_count
FROM ingest_completion_delivery
WHERE handler_key = 'source-channel.notification.v1';
"
```

修复 LangBot API key、网络或目标配置后，可以通过已实现的 Python hook 手工 redrive
一条失败记录；当前没有公开 CLI 或 HTTP endpoint。先从受保护查询中确认内部 event id：

```bash
export EVENT_ID=123
notebook_run .venv/bin/python - <<'PY'
import os

from app.ingest.notifications import redrive_failed_ingest_notification

event_id = int(os.environ["EVENT_ID"])
if not redrive_failed_ingest_notification(event_id):
    raise SystemExit("no failed source-channel delivery for event")
print("notification_redrive_queued")
PY
unset EVENT_ID
```

hook 只把失败 delivery 重新设为下一次 Beat tick 可领取，不会重跑 ingestion，也不会
在当前命令中直接发送 HTTP。确认下一条 heartbeat 和 failed-ledger 计数后再结束事件。

旧的 Redis `ingest-completion` queue 已退役。可以只检查遗留 backlog 数量，但绝不能
让 worker 监听、消费或重放它，也不能恢复旧 completion producer/consumer：

```bash
docker compose exec -T redis redis-cli LLEN ingest-completion
```

暂停 poller 时只暂停它的 Beat entry，并保留 PostgreSQL event 与 delivery ledger；
不要清空 ledger 来制造“已送达”状态。

## 7. 浏览器伴侣配对或捕获失败

确认扩展 build 与服务 origin 匹配：production 只允许其固定 HTTPS host，local 只允许
`http://127.0.0.1:8000`。后端 `BROWSER_COMPANION_ALLOWED_ORIGINS` 必须是精确的
`chrome-extension://<32 位扩展 ID>`；生产不要使用 wildcard。

重新配对前：

1. Web 账号页撤销旧 device；
2. 扩展弹窗断开连接并重新加载 unpacked extension；
3. 从扩展发起新 pairing，在同一 Web origin 点击“允许连接”；
4. 重新打开视频页面后再捕获。

扩展只能上传规范化 caption cue；若日志、请求 body 或 URL 出现 Cookie、SAML、Kaltura
KS、signed URL 或 video/audio bytes，应立即撤销 grant 并停止该 build。

## 8. LangBot 渠道不可用

检查 loopback gateway：

```bash
curl --fail http://127.0.0.1:8765/health
```

再检查 LangBot 日志中是否出现：

```text
Required plugins initialized; message adapters may start.
```

若没有，检查固定 4.10.6 patch、plugin manifest、required bridge ref、plugin 私有
`.env` 的 `0600` 权限和 gateway secret/URL。不要用 `sleep` 替代 readiness，也不要
让 Local Agent 作为 bridge 失败时的 fallback。adapter 断线应单独恢复；不要为了修复
微信而停止 Telegram、gateway 或共享资料库。

## 9. 何时停止并升级处理

出现 tenant mismatch、未知数据被删除、migration 多 head、对象和数据库不一致、
production capability URL 泄露、或回滚需要 downgrade 时，先隔离写入、保留备份和
有限诊断，再交给拥有数据库/object-store/secret 权限的 operator。不要在请求路径手工
删除 rows、清空队列、重建 bucket 或重置身份。
