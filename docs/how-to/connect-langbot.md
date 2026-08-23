# 接入 LangBot

LangBot 是可选的渠道适配器。它把 Telegram/微信私聊事件转成 Notebook Agent
channel envelope；用户、租户、对话、检索和权限仍由 Notebook Agent 管理。没有
LangBot 时，Web、MCP 和浏览器伴侣仍可独立运行。

## 前置条件

- Notebook Agent 已运行 `full` profile，且 loopback gateway 健康；
- 已准备外部 LangBot 4.10.6 和 Telegram/微信 adapter；
- 已阅读插件的[安装与安全说明](../../integrations/langbot_kb_plugin/README.md)；
- 生产环境采用固定版本的 `integrations/langbot-4.10.6-redact-monitoring.patch`。

LangBot 4.10.6 的生产补丁不是可选的 sleep；它会等待 bridge plugin 真正变成
`initialized`，并在 bridge 缺失时 fail closed，防止消息落入 Local Agent。

## 1. 配置 Notebook Agent gateway

在 Notebook Agent 私有 `.env` 或 secret manager 中设置：

```dotenv
CHANNEL_GATEWAY_SECRET=<至少 32 个字符的随机值>
CHANNEL_GATEWAY_HOST=127.0.0.1
CHANNEL_GATEWAY_PORT=8765
```

启动并检查 gateway：

```bash
.venv/bin/python -m app.cli gateway-server
curl --fail http://127.0.0.1:8765/health
```

gateway 只监听 loopback。不要把 8765 直接放进 Caddy/Nginx，也不要为了绕过 401
关闭 HMAC、时钟或 nonce 校验。

## 2. 安装 bridge plugin

将 `integrations/langbot_kb_plugin/` 安装到 LangBot 的 plugin workspace，然后把
示例环境文件复制到实际安装目录：

```bash
cp integrations/langbot_kb_plugin/.env.example \
  /path/to/langbot/data/plugins/notebook-agent__notebook-knowledge-agent/.env
chmod 600 \
  /path/to/langbot/data/plugins/notebook-agent__notebook-knowledge-agent/.env
```

填写与 Notebook Agent 根配置一致的 gateway secret，并明确映射 bot UUID：

```dotenv
CHANNEL_GATEWAY_SECRET=<与根环境完全相同的值>
CHANNEL_GATEWAY_URL=http://127.0.0.1:8765/v1/messages
KB_BOT_CHANNELS={"telegram-bot-uuid":"telegram","wechat-bot-uuid":"wechat"}
```

`KB_BOT_CHANNELS` 的 key 是 LangBot bot UUID，不是 Telegram user ID、微信昵称或
`AppUser.id`。不要把 provider key、数据库 DSN 或真实 bot token 放进 plugin manifest、
仓库或普通 LangBot 配置日志。

## 3. 应用版本补丁并绑定 required plugin

先对**固定的 LangBot 4.10.6 源码或官方 wheel**做 dry-run，再正式应用：

```bash
patch_file=/path/to/notebook-agent/integrations/langbot-4.10.6-redact-monitoring.patch
patch --dry-run -p1 < "$patch_file"
patch -p1 < "$patch_file"
```

在 LangBot 配置中声明并绑定 bridge：

```yaml
plugin:
  required_plugins:
    - notebook-agent/notebook-knowledge-agent
  required_plugins_ready_timeout_seconds: 30
```

bridge pipeline 应设置 `enable_all_plugins=false`，并显式绑定同一个 required ref。
Telegram 与微信 adapter 可以同时启用；不要通过 `enable_all_plugins` 隐式匹配，
也不要配置 Local Agent 作为 Notebook Agent 不可用时的回退。

## 4. 按 readiness 顺序启动

1. 启动 Notebook Agent gateway，确认 `GET /health` 为 200。
2. 启动 LangBot core/plugin runtime。
3. 等待日志出现 `Required plugins initialized; message adapters may start.`。
4. 再确认 LangBot `healthz` 和各 adapter readiness；不要只用固定等待秒数代替。
5. 最后做 Telegram/微信私聊 `/whoami` 和一条已有知识的问答 smoke。

如果 bridge 未初始化、运行时断开或事件没有 `prevent_default()`，patched LangBot
应返回固定的渠道暂不可用提示，并且不调用 Local Agent。不要先启动 adapter 再
“等它自己恢复”。生产 Telegram-only、Caddy 和 host-specific 细节见
[production runbook](../operations/production/langbot-telegram.md)。

## 5. 绑定 Web 与渠道身份

绑定码是短期、单次、带目标渠道的 token：

- Web 发起：在账号绑定页面选择 Telegram/微信，生成一次性 `/link <raw-token>` 指令，
  只发给目标 bot；
- 渠道发起：在当前 Telegram/微信会话发送 `/link web`，再把返回的 code 粘贴到 Web；
- 渠道互绑：来源渠道发送 `/link telegram` 或 `/link wechat`，目标渠道发送返回的
  `/link <raw-token>` 消费。

成功后两个渠道共享同一私人资料库，但对话历史仍按渠道分开保存。若目标账户有正在
处理的 ingestion，服务会返回 `link_merge_busy` 且不消费 code；处理完成后用同一个
code 重试。不要在聊天中转发绑定码或截图；它只应出现在目标私聊和当前已登录 Web
页面之间。

跨渠道身份模型与错误码见[身份与渠道说明](../explanation/identities-and-channels.md)。
