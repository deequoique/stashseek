# 生产环境 LangBot：仅限 Telegram 的私有运行方式

本运维手册适用于 OVH 生产环境中的
`notebookai.deequoique.tech` 部署。LangBot 是内部管理入口，不是第二个公开应用。
Caddy 必须继续只代理位于 `127.0.0.1:8800` 的 StashSeek Chat 组合服务。

## 运行时边界

- Channel Gateway 只监听 `127.0.0.1:8765`。
- 打过补丁的 LangBot 4.10.6 只监听 `127.0.0.1:5300`。
- LangBot 以 `notebook-langbot` 身份运行，不会加载 StashSeek Chat 的数据库、
  model、email、Redis、MinIO、Web Auth 或 MCP 环境文件。
- 必需的 bridge plugin 只会在权限为 `0600` 的私有 `.env` 中保存自己的
  gateway secret、loopback Gateway URL 和受信任的 bot UUID mapping。
- 只创建 Telegram adapter。不要安装、创建、扫描或启用 WeChat/OpenClaw adapter。

## 一次性引导

只有在独立验证官方 `langbot-4.10.6-py3-none-any.whl` 的预期 SHA-256 后，才能使用
它。将 wheel 保存在 Git repository 之外。引导脚本会重复该验证，应用带版本的 patch，
编译所有发生变化的 Python 文件，安装必需的 bridge，生成相互独立的 private key，
禁用 Box/marketplace/telemetry，并让 bot mapping 保持为空：

```bash
sudo /usr/local/sbin/bootstrap-production-langbot \
  /root/private-artifacts/langbot-4.10.6-py3-none-any.whl
```

不要向此脚本传入 Telegram token，也不要将 token 放进环境文件、命令行、GitHub
secret、终端记录或截图中。

启用 unit 前，验证其解析后的配置，并确认没有引入公开监听器：

```bash
sudo systemd-analyze verify \
  /etc/systemd/system/notebook-agent-gateway.service \
  /etc/systemd/system/notebook-agent-langbot.service
sudo systemctl daemon-reload
sudo systemctl enable --now notebook-agent-gateway.service
sudo systemctl enable --now notebook-agent-langbot.service
sudo ss -ltnp
```

监听器清单必须显示 `8765` 和 `5300` 绑定在 `127.0.0.1` 上，绝不能绑定到
`0.0.0.0`、`::` 或服务器的公网地址。还要从远程机器验证，服务器公网地址
无法连接到这两个端口。

## 私有管理访问

从运维人员的工作站打开隧道：

```bash
ssh -N -L 5300:127.0.0.1:5300 ubuntu@51.79.159.110
```

保持 SSH 会话打开，在本地访问
`http://127.0.0.1:5300`。这里有意不配置 LangBot hostname、Caddy 路由或公网
防火墙放行。

在私有 UI 中：

1. 如果是首次启动，创建 administrator/login state。
2. 不要安装其他 plugin；Notebook Knowledge Agent bridge 已经安装，并且必须报告
   `initialized`。
3. 只创建一个 Telegram bot adapter，并在私有 UI 中输入 Telegram Bot Token。绝不要
   将它粘贴到聊天或 shell 命令中。
4. 创建一个 **Enable all plugins disabled** 的 pipeline，并明确只绑定
   `notebook-agent/notebook-knowledge-agent`。
5. 将 Telegram adapter 绑定到这个仅含 bridge 的 pipeline。不要配置 Local Agent
   fallback。
6. 记录 adapter 的 LangBot bot UUID。它不是 Telegram token、chat ID、user ID 或
   bot username。

由于 `KB_BOT_CHANNELS={}`，bridge 初始时会拒绝所有 bot。Telegram adapter 创建后，
只映射它的 UUID，然后重启 LangBot：

```bash
sudo /usr/local/sbin/configure-production-telegram \
  00000000-0000-0000-0000-000000000000
```

将示例 UUID 替换为实际的 LangBot bot UUID。该 helper 只更新 bridge mapping，在不显示
任何 secret 的情况下保留所有 secret，并将唯一允许的 channel 设为 `telegram`。

## 验收检查

只运行已脱敏的 health/readiness 检查：

```bash
curl --fail http://127.0.0.1:8765/health
curl --fail http://127.0.0.1:5300/healthz
sudo journalctl -u notebook-agent-langbot.service --since today --no-pager \
  | grep -F 'Required plugins initialized; message adapters may start.'
```

然后向 Telegram bot 发送一条普通的人类消息，并确认 StashSeek Chat 恰好返回一条最终
回复。确认不存在 WeChat adapter；查看日志时只能检查内部状态或错误类别，不要查看
token、消息文本、用户名、外部发送者 ID 或消息预览。

## Release 与回滚行为

生产 release 的关闭顺序是 LangBot、Gateway、组合应用、worker，然后是 Beat。启动顺序
是 dependencies、migration、worker/Beat/application、Gateway，然后是 LangBot。只有在
application、Gateway、LangBot 进程健康检查以及 required-bridge marker 全部通过后，
release 才会被接受。

回滚只切换不可变的 StashSeek Chat release，并重启其所属 unit。它会保留 LangBot 的
SQLite/configuration、Telegram adapter、bridge `.env`、Redis/MinIO volumes 以及远程
Neon 数据。
