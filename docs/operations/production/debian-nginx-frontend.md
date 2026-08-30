# 在 Debian/Ubuntu 与 Nginx 上原子发布独立前端

这份手册适用于一台由 systemd 管理、由 Nginx 提供单一 HTTPS origin 的
Debian/Ubuntu 主机。Nginx 提供 `web/dist`，并把 `/api/*` 转发到 loopback Web
API；Channel Gateway 继续只监听 `127.0.0.1:8765`。

如果你只需要了解 bundled/split 的差异和浏览器安全契约，先阅读
[部署独立前端](../../how-to/deploy-frontend.md)。其他发行版和控制面板需要把本文的
service account、Nginx 路径和 unit 安装位置映射到自己的系统。

```text
Browser -> https://kb.example.com
           |-- /*        -> Nginx -> /opt/notebook-agent/current/web/dist
           `-- /api/*    -> Nginx -> 127.0.0.1:8000

127.0.0.1:8765 -> private Channel Gateway；不要通过 Nginx 公开
```

## 发布前提

仓库提供以下模板：

- `deploy/nginx/notebook-agent-web.conf`：TLS、SPA fallback、API proxy 和缓存策略；
- `deploy/nginx/notebook-agent-web-security-headers.conf`：静态与代理响应共用的浏览器策略；
- `deploy/systemd/notebook-agent-web.service`：API-only Web process；
- `deploy/systemd/notebook-agent-web-migrate.service`：一次性 migration admission。

把 `/etc/notebook-agent/notebook-agent.env` 设为 root-owned `0640`，只允许 service
group 读取。它需要完整的后端 profile，以及至少以下 Web 设置：

```dotenv
WEB_AUTH_ENABLED=true
WEB_PUBLIC_ORIGIN=https://kb.example.com
WEB_AUTH_SECRET=<at-least-32-random-characters>
WEB_COOKIE_SECURE=true
WEB_SERVE_STATIC=false
WEB_HOST=127.0.0.1
WEB_PORT=8000
WEB_FORWARDED_ALLOW_IPS=127.0.0.1
```

不要把证书私钥、数据库 URL、Web auth secret 或真实主机清单写进仓库、Nginx 模板、
systemd unit 或 release manifest。

### 1. 准备 service account 和目录

只在首次部署时以 root 创建目录：

```bash
sudo install -d -o notebook-agent -g notebook-agent -m 0755 \
  /opt/notebook-agent \
  /opt/notebook-agent/repository \
  /opt/notebook-agent/releases
```

把授权仓库放在 `/opt/notebook-agent/repository`，后续 build 使用
`notebook-agent` 用户完成。

### 2. 构建同一个 commit 的前端和 API release

每个 release 使用独立 detached worktree 和虚拟环境，保证 API、SPA 与预期 schema
来自同一个 commit：

```bash
sudo -iu notebook-agent
repo=/opt/notebook-agent/repository
release_sha="$(git -C "${repo}" rev-parse HEAD)"
release_id="$(git -C "${repo}" rev-parse --short=12 "${release_sha}")"
release_dir="/opt/notebook-agent/releases/${release_id}"

install -d -m 0755 /opt/notebook-agent/releases
git -C "${repo}" worktree add --detach "${release_dir}" "${release_sha}"
cd "${release_dir}"
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
corepack pnpm --dir web install --frozen-lockfile
corepack pnpm --dir web check:api
corepack pnpm --dir web test
corepack pnpm --dir web typecheck
corepack pnpm --dir web lint
corepack pnpm --dir web build

schema_head="$(.venv/bin/alembic heads | awk 'NR == 1 {print $1}')"
test -n "${schema_head}"
printf 'COMMIT_SHA=%s\nSCHEMA_HEAD=%s\nFRONTEND_ARTIFACT=%s\nAPI_ENTRYPOINT=%s\n' \
  "${release_sha}" "${schema_head}" 'web/dist' 'app.cli web-server' \
  > release-manifest.env
exit
```

`release-manifest.env` 不包含 secret；它用于审核 release，以及回滚时校验 live schema。
不要在 release 之间共用一个可变 `.venv`。

### 3. 先提供 TLS，再安装站点

Provision or install the TLS certificate before enabling the HTTPS site.
仓库模板不会猜测主机使用 Certbot、云负载均衡还是托管证书。先替换示例域名并确认
本地证书可读：

```bash
sudo test -r /etc/letsencrypt/live/kb.example.com/fullchain.pem
sudo test -r /etc/letsencrypt/live/kb.example.com/privkey.pem
```

证书续期 hook 也必须先运行 `nginx -t`，只有候选配置通过后才能 reload。

### 4. 安装、切换并验证服务

下面的顺序先停止旧 Web process，确认已经 inactive，再原子切换 symlink。migration
失败时新 Web release 保持停止：

```bash
set -euo pipefail

sudo install -m 0644 deploy/nginx/notebook-agent-web-security-headers.conf \
  /etc/nginx/snippets/notebook-agent-web-security-headers.conf
sudo install -m 0644 deploy/nginx/notebook-agent-web.conf \
  /etc/nginx/sites-available/notebook-agent-web.conf
sudo ln -sfn /etc/nginx/sites-available/notebook-agent-web.conf \
  /etc/nginx/sites-enabled/notebook-agent-web.conf
sudo install -m 0644 deploy/systemd/notebook-agent-web.service \
  /etc/systemd/system/notebook-agent-web.service
sudo install -m 0644 deploy/systemd/notebook-agent-web-migrate.service \
  /etc/systemd/system/notebook-agent-web-migrate.service

release_id="$(git -C /opt/notebook-agent/repository rev-parse --short=12 HEAD)"
release_dir="/opt/notebook-agent/releases/${release_id}"
sudo rm -f "${release_dir}/.migration-admitted"
sudo ln -sfn "${release_dir}" /opt/notebook-agent/current.next
sudo systemctl daemon-reload
if ! sudo systemctl stop notebook-agent-web; then
  echo 'failed to stop Web service' >&2
  exit 1
fi
if ! active_state="$(
  sudo systemctl show notebook-agent-web --property=ActiveState --value
)"; then
  echo 'failed to query Web service state' >&2
  exit 1
fi
if [ "${active_state}" != 'inactive' ]; then
  echo "refusing to switch an active Web service; state=${active_state}" >&2
  exit 1
fi
sudo mv -Tf /opt/notebook-agent/current.next /opt/notebook-agent/current

sudo nginx -t
sudo systemd-analyze verify /etc/systemd/system/notebook-agent-web.service
sudo systemd-analyze verify /etc/systemd/system/notebook-agent-web-migrate.service
if ! sudo systemctl start notebook-agent-web-migrate.service; then
  echo 'migration admission failed; Web remains stopped' >&2
  exit 1
fi
sudo test -f /opt/notebook-agent/current/.migration-admitted
if ! sudo systemctl enable notebook-agent-web; then
  echo 'failed to enable Web service' >&2
  exit 1
fi
if ! sudo systemctl restart notebook-agent-web; then
  echo 'failed to start admitted Web release' >&2
  exit 1
fi
curl --fail http://127.0.0.1:8000/api/v1/health
sudo systemctl reload nginx
```

release-local admission marker 在每次 Web start 前都由 unit 检查，因此 migration
failure survives a reboot。不要手工创建 marker，也不要从另一个 release 复制它。

stop、symlink switch、migration 和 restart 是计划内维护窗口。切换前应从上游负载
均衡移除主机或启用维护响应；API 与依赖检查完成前，不要把新 SPA 当作成功 release。

### 5. 从最终 public origin 验收

先检查路由契约：

```text
GET  /                      -> SPA index
GET  /login                 -> SPA index
GET  /library               -> SPA index
GET  /videos/<public-id>    -> SPA index
GET  /api/v1/health         -> 200 JSON
GET  /api/v1/capabilities   -> 200 JSON
GET  /api/v1/does-not-exist -> JSON 404，不能返回 SPA HTML
```

再完成一次真实登录、资料库读取和 CSRF-protected write。确认浏览器只请求同 origin 的
相对 `/api/v1/*` URL，只收到 host-only `__Host-kb_session` 与 `__Host-kb_csrf`，并且
公网只开放 80/443；8000 与 `127.0.0.1:8765` 保持私有。

`/api/v1/health` 只证明 Web process 活着，不是完整 dependency readiness。重新接受
save/retry 前，还要确认 live database revision 等于 `release-manifest.env` 的
`SCHEMA_HEAD`、Redis readiness 和 AOF、MinIO bucket admission，以及 worker 的
`ingest,maintenance` queues 与单一 Beat。

### 6. Roll back the paired release

回滚必须把 API code、Python environment 和 `web/dist` 一起指向上一份 known-good
release。先验证它已有 migration admission，并确认 live schema 与旧 manifest 相同：

```bash
set -euo pipefail

previous_release=/opt/notebook-agent/releases/SET_PREVIOUS_RELEASE_ID
test -r "${previous_release}/release-manifest.env"
test -f "${previous_release}/.migration-admitted"
previous_schema="$(sed -n 's/^SCHEMA_HEAD=//p' \
  "${previous_release}/release-manifest.env")"
live_schema="$(
  sudo systemd-run --quiet --wait --collect --pipe \
    --uid=notebook-agent --gid=notebook-agent \
    --working-directory=/opt/notebook-agent/current \
    --property=EnvironmentFile=/etc/notebook-agent/notebook-agent.env \
    /opt/notebook-agent/current/.venv/bin/alembic current \
    | awk 'NR == 1 {print $1}'
)"
if [ -z "${previous_schema}" ] || [ "${live_schema}" != "${previous_schema}" ]; then
  echo 'schema admission failed; obtain a reviewed compatibility proof' >&2
  exit 1
fi

sudo ln -sfn "${previous_release}" /opt/notebook-agent/current.next
if ! sudo systemctl stop notebook-agent-web; then
  echo 'failed to stop Web service during rollback' >&2
  exit 1
fi
if ! active_state="$(
  sudo systemctl show notebook-agent-web --property=ActiveState --value
)"; then
  echo 'failed to query Web service state during rollback' >&2
  exit 1
fi
if [ "${active_state}" != 'inactive' ]; then
  echo "refusing to switch an active Web service during rollback; state=${active_state}" >&2
  exit 1
fi
sudo mv -Tf /opt/notebook-agent/current.next /opt/notebook-agent/current
if ! sudo systemctl restart notebook-agent-web; then
  echo 'failed to restart Web service during rollback' >&2
  exit 1
fi
sudo nginx -t
sudo systemctl reload nginx
```

这个 stop/switch/restart 是短维护窗口，不是跨进程 transaction。API liveness、依赖
readiness、public smoke 和真实 authenticated flow 全部通过后，才能恢复写入。

默认 schema gate 要求旧 manifest 的 `SCHEMA_HEAD` 与 live revision 相同。不同
revision 必须有单独审查的 forward-compatibility 证明；不要在命令内跳过检查，也
**不要自动执行 Alembic downgrade**。数据库 downgrade 属于独立、经备份和恢复演练
批准的数据操作。

至少保留 current 和 previous 两份 known-good release。清理更旧 worktree 前，先确认
它不是 `/opt/notebook-agent/current` 的目标，再运行：

```bash
git -C /opt/notebook-agent/repository worktree remove \
  /opt/notebook-agent/releases/SET_OLD_RELEASE_ID
```

不要删除 active release，也不要批量删除整个 releases 目录。
