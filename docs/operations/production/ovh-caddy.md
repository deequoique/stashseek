# OVHcloud + Caddy 生产部署

本运维手册会在 Ubuntu VPS 上，将浏览器与 Streamable HTTP MCP 组合运行时部署到
`https://notebookai.deequoique.tech`。其中的安全规则采用叠加方式，因此即使该主机
日后被共享，同一套流程也不会替换无关的 Caddy 站点或服务。

## 运行时结构

```text
Caddy :443 -> 127.0.0.1:8800
               |-- / and /api/v1/*: SPA + email-authenticated Web API
               `-- /mcp: Bearer-authenticated Streamable HTTP MCP

Celery worker: ingest,maintenance
Celery ASR worker（可选，仅启用 ASR 时安装）: asr，并发 1
Celery Beat: exactly one scheduler
PostgreSQL: external pooled Neon runtime + direct Neon migration URL
Redis/MinIO: Notebook-Agent-only containers published on loopback
```

不要在组合运行时旁边启动 `app.cli web-server`。`WEB_AUTH_ENABLED=true` 时，
`app.cli mcp-server --transport streamable-http` 已经会分发浏览器和 MCP 流量，
同时保持两者的凭据相互隔离。

MCP SDK 会保持 DNS rebinding 防护启用。组合模式下，经过验证的
`WEB_PUBLIC_ORIGIN` 主机会自动获准；不要禁用传输安全，也不要将 Caddy 的上游
`Host` 请求头改写为 loopback 地址。

## 一次性服务器配置

创建专用的 service account 和 release 目录结构。不要复用已有的应用账号或目录。

```bash
sudo useradd --system --home-dir /var/lib/notebook-agent \
  --create-home --shell /usr/sbin/nologin notebook-agent
sudo install -d -o notebook-agent -g notebook-agent -m 0755 \
  /opt/notebook-agent /opt/notebook-agent/repository \
  /opt/notebook-agent/releases /var/lib/notebook-agent
sudo install -d -o root -g notebook-agent -m 0750 /etc/notebook-agent
```

确认不存在已有软件包、网桥、防火墙策略或已发布端口冲突后，才可从发行版仓库安装
Docker。生产 Compose 文件只启动 Redis 和 MinIO；它不会启动仓库中的 PostgreSQL
服务。

以 root 身份创建 `/etc/notebook-agent/dependencies.env`，权限设为 `0600`，并填入
新生成的值：

```dotenv
NOTEBOOK_REDIS_PASSWORD=<random-production-secret>
NOTEBOOK_REDIS_PORT=16379
MINIO_ROOT_USER=<random-production-user>
MINIO_ROOT_PASSWORD=<random-production-secret>
MINIO_BUCKET=kb-raw
NOTEBOOK_MINIO_API_PORT=19000
NOTEBOOK_MINIO_CONSOLE_PORT=19001
```

以 root 身份创建 `/etc/notebook-agent/notebook-agent.env`，权限设为 `0600`。
长期运行的进程只能使用 pooled Neon URL，绝不能继承 direct migration credential。

```dotenv
DATABASE_URL=<pooled-neon-url-with-sslmode-require>
REDIS_URL=redis://:<redis-password>@127.0.0.1:16379/0
MINIO_ENDPOINT_URL=http://127.0.0.1:19000
MINIO_ROOT_USER=<same-private-user>
MINIO_ROOT_PASSWORD=<same-private-secret>
MINIO_BUCKET=kb-raw

ZHIPU_API_KEY=<provider-secret>
EMBEDDING_MODEL=embedding-3
EMBEDDING_DIMENSIONS=1536
AGENT_MODEL=<provider-model>
AGENT_API_KEY=<provider-secret>
AGENT_BASE_URL=<provider-url-if-required>

WEB_AUTH_ENABLED=true
WEB_PUBLIC_ORIGIN=https://notebookai.deequoique.tech
WEB_AUTH_SECRET=<new-at-least-32-character-secret>
WEB_SESSION_CACHE_TTL_SECONDS=60
EMAIL_PROVIDER=smtp
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_STARTTLS=true
SMTP_USERNAME=<gmail-address>
SMTP_PASSWORD=<gmail-app-password>
SMTP_FROM_EMAIL=<gmail-address>
WEB_COOKIE_SECURE=true
WEB_FORWARDED_ALLOW_IPS=127.0.0.1
WEB_TRUSTED_PROXY_HOSTS=notebookai.deequoique.tech

MCP_HOST=127.0.0.1
MCP_PORT=8800
MCP_PATH=/mcp
MCP_URL_TOKEN_MODE=true
```

另行以 root 身份创建 `/etc/notebook-agent/migrations.env`，权限设为 `0600`。
只有 `notebook-agent-migrate.service` 可以读取它：

```dotenv
MIGRATION_DATABASE_URL=<direct-neon-url-with-sslmode-require>
```

绝不要打印、提交、粘贴到 GitHub Actions，或将这些值放在命令行中。在启动 unit
之前，确认所有者为 `root`、组为 `root`，权限为 `0600`。

只安装以下五个 unit，然后针对已安装的文件运行 `systemd-analyze verify`：

```text
notebook-agent-dependencies.service
notebook-agent-migrate.service
notebook-agent-worker.service
notebook-agent-beat.service
notebook-agent.service
```

启用 ASR 转写时才额外安装 `notebook-agent-asr-worker.service`，见下文“ASR 转写”一节。
只启用这些 unit。不要安装旧版的
`notebook-agent-web.service` 或 `notebook-agent-web-migrate.service`；email Web、
API 和 MCP 流量都属于组合运行时。安装 Caddy 站点时，先保存
`/etc/caddy/Caddyfile` 的带时间戳副本和哈希值。加入
`deploy/caddy/notebook-agent.caddy` 的内容，运行 `caddy validate`，然后以优雅方式
reload Caddy。立即对每个已有 hostname 和 upstream 做 smoke test；如果任何检查结果
发生变化，就恢复备份。

## 初始 release 与准入

以 service account 将授权 repository clone 到 `/opt/notebook-agent/repository`。
在 `/opt/notebook-agent/releases/<sha>` 创建精确的 detached `main` release，在其
独立的 `.venv` 中安装 Python 依赖，安装锁定的 Web 依赖，并构建 `web/dist`。

切换前，确认只有一个 Alembic head。切换 `current` 后，启动
`notebook-agent-migrate.service`；它必须完成 `upgrade head`、`current` 和
`check`，并创建 `.migration-admitted`。如果失败，则恢复之前的 release，且不会
启动候选应用。

按以下顺序启动：

```text
notebook-agent-dependencies.service
notebook-agent-migrate.service
notebook-agent-worker.service
notebook-agent-beat.service
notebook-agent.service
```

确认 Redis 和 MinIO 健康，归属本实例的 `kb-raw` bucket 已由一次性
`minio-init` container 准入，worker 能够 pong 并列出两个队列，Beat 进程恰好只有
一个，并且 `/api/v1/health` 在 loopback 和 HTTPS 上都成功。完成一次真实的 Gmail
登录，并确认浏览器 Web Storage 为空。

保存和 item-management 能力没有环境开关。只有全部闸门均为绿色后，才能让组合运行时
接收用户流量，然后重新运行 readiness 检查。

## 动态 evaluator grant

创建专用的 AppUser，并签发一个带标签、有效期 30 天的 `full` grant。使用 UTC
计算过期时间，并且只在获准的私密交接渠道中捕获一次原始 token。以下命令必须在
已经由受限 operator 流程注入 `/etc/notebook-agent/notebook-agent.env` 的环境中运行；
不要把 root-owned 环境文件复制、`source` 到普通 shell，或让命令输出环境内容。

```bash
.venv/bin/python -m app.cli users create
.venv/bin/python -m app.cli mcp-grant issue --user-id <id> --scope full \
  --expires-at <utc-iso-8601> --label dynamic-evaluator-30d \
  --created-by production-bootstrap
```

可以在 `https://notebookai.deequoique.tech/mcp` 上使用
`Authorization: Bearer <token>`，也可以使用仅限 URL 的 evaluator capability
`https://notebookai.deequoique.tech/mcp/c/<token>`。Path-token mode 要求使用
HTTPS，且 Caddy 站点会丢弃 access log；`?token=` 仍然禁止使用。评估结束或怀疑
泄露后，应轮换、禁用或撤销该 grant。

## GitHub 审批与受限部署

必须将 `Production` GitHub Environment 配置为需要人工 reviewer。只配置以下环境
secret：

```text
PRODUCTION_SSH_HOST
PRODUCTION_SSH_USER
PRODUCTION_SSH_PRIVATE_KEY
PRODUCTION_SSH_KNOWN_HOSTS
```

该密钥对应的服务器账号必须有一条使用 `restrict` 的 `authorized_keys` 条目，并
配置一个调用 root-owned
`deploy/scripts/notebook-agent-ssh-dispatch` wrapper 的 forced command。wrapper
会验证精确命令，清除 `SSH_ORIGINAL_COMMAND`，然后使用 sudo 调用 root-owned
`deploy/scripts/notebook-agent-deploy` dispatcher。其 sudo 策略只能允许调用该
dispatcher。不要授予 interactive shell、任意 sudo、forwarding，或访问应用环境
文件的权限。

每次向 `main` push 都会先运行确定性的 CI job。通过的 revision 会等待生产审批，
与其他生产部署串行执行，然后请求 `deploy <40-character-sha>`。dispatcher 只接受
当前的 `origin/main`，保留之前的 release，在启动前执行迁移；如果迁移或健康准入
失败，则恢复之前的 symlink。

## ASR 转写（可选）

默认关闭（`ASR_ENABLED=false`）。关闭时没有字幕的公开 YouTube/Bilibili 条目（包括浏览器
插件采集到 `caption_status=unavailable` 的公开 YouTube 条目）仍以 `needs_asr` 结束，下面
的步骤都不需要。NTULearn/Kaltura 采集条目是私有媒体，无论是否开启 ASR 都保持
`needs_asr`。开启后，同一个 dispatch 会从 `ingest` 队列转入
`asr` 队列：yt-dlp 只下载音频，音频暂存到 MinIO 的 `asr-tmp/` 前缀，Fun-ASR 通过本站
域名下的短期签名地址 `/api/v1/asr-audio/<name>` 回源下载（无需会话，Caddy 站点已丢弃
access log，签名只出现在这个 URL 的 query 里），转写结束后立即删除临时对象。需要合并
到 `main` 并经 `Production` 审批部署之后，由运维单独执行以下步骤。

1. 在主机安装 ffmpeg，并确认它在 service account 的 `PATH` 中：

   ```bash
   sudo apt install ffmpeg
   ffmpeg -version | head -1
   ```

2. 安装并启用独立的 asr worker（并发 1，沙箱与现有 worker 一致）。`deploy/scripts/
   notebook-agent-deploy` 在该 unit 已安装时会把它纳入 stop、restart 和回滚；未安装
   的主机照常部署。此时 `ASR_ENABLED` 仍是 `false`，worker 只是空闲地监听 `asr` 队列：

   ```bash
   sudo install -m 0644 deploy/systemd/notebook-agent-asr-worker.service \
     /etc/systemd/system/
   sudo systemd-analyze verify /etc/systemd/system/notebook-agent-asr-worker.service
   sudo systemctl daemon-reload
   sudo systemctl enable --now notebook-agent-asr-worker.service
   ```

3. 给 MinIO 的临时音频前缀设置兜底过期规则（正常情况下转写结束就会删除，该规则只清理
   进程崩溃留下的对象）。`<alias>` 是已用 `mc alias set` 配置好的本实例 MinIO 别名：

   ```bash
   mc ilm rule add --prefix asr-tmp/ --expire-days 1 <alias>/kb-raw
   mc ilm rule ls <alias>/kb-raw
   ```

4. 前三步都完成后，才在 `/etc/notebook-agent/notebook-agent.env`（仍为 root-owned
   `0600`）追加以下变量；所有 secret 用新生成的值，不要打印、提交或放在命令行。**不要在
   asr worker 运行之前设置 `ASR_ENABLED=true`**：没有消费者时，被转交的条目会一直停在
   `fetching`。

   ```dotenv
   ASR_ENABLED=true
   DASHSCOPE_API_KEY=<fun-asr-secret-singapore-region>
   ASR_PUBLIC_BASE_URL=https://notebookai.deequoique.tech
   ASR_AUDIO_URL_SECRET=<new-at-least-32-character-secret>
   # 可选的 whisper-1 备选；不设置则没有备选。
   OPENAI_API_KEY=<openai-secret>
   ```

   其余 `DASHSCOPE_BASE_URL`、`ASR_*` 上限均有默认值，见
   `docs/reference/configuration.md`。`ASR_ENABLED=true` 时缺少必需项会让应用和 worker
   启动失败，而不是静默降级。

5. 重启组合运行时和所有 worker，使 `ASR_ENABLED=true` 生效，并确认 asr worker 已连上
   队列：

   ```bash
   sudo systemctl restart notebook-agent-worker.service notebook-agent.service \
     notebook-agent-asr-worker.service
   sudo journalctl -u notebook-agent-asr-worker.service -n 20 --no-pager
   ```

6. 上线金丝雀：先 `--dry-run` 查看会处理的条目，再只回填一条，并观察
   `asr_transcription` 诊断日志（在 `/var/log/notebook-agent/` 中检索该事件；它只含
   供应商、模型、时长、估算费用、耗时、是否使用备选和错误码）。命令必须在已经由受限
   operator 流程注入环境文件的环境中运行：

   ```bash
   .venv/bin/python -m app.cli asr-backfill --limit 1 --dry-run
   .venv/bin/python -m app.cli asr-backfill --limit 1
   ```

   条目应经 `fetching` 变为 `ready`，且只产生一条 `completed/ready` 通知。确认后再按
   `--limit` 分批回填其余条目。

注意事项：

- 停止 asr worker 时 Celery 会等待进行中的转写，但 unit 的 `TimeoutStopSec`（60 秒）
  到期后会被强制终止，被中断的 dispatch 会停留在 `running`。部署前先确认没有进行中的
  转写：`celery -A app.ingest.tasks.celery_app inspect active`。
- 回滚：把 `ASR_ENABLED=false` 写回环境文件，并重启组合运行时、`notebook-agent-worker.service`
  和 asr worker。队列里尚未处理的 ASR 任务由重启后的 asr worker 按原有行为收敛为
  `needs_asr`；队列排空后可停止并禁用该 unit。
  已完成的转写（`text_source=asr_whisper`、`raw_format=capture_v1`）保持可检索。
- 公开签名路由会让音频经过应用进程转发，约每音频小时 60MB 出口流量。

## 回滚

回滚只会修改 `/opt/notebook-agent/current`，并重启部署拥有的 worker、Beat、
组合应用、Gateway 和 LangBot unit。它不会运行 `docker compose down`，不会删除
volumes、移除 buckets，也不会降级或删除 Neon 数据。只有在移除 StashSeek Chat
hostname 时才恢复 Caddy 备份；恢复后要验证配置、优雅地 reload，并重新检查所有
既有路由。
