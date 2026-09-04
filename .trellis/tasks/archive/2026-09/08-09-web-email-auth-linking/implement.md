# Web 邮箱登录与跨渠道绑定：实施计划

## 实施顺序

1. 增加 Web 认证配置与依赖校验。
   - 在 `Settings`、`.env.example` 和部署文档加入 Web API、session、验证码、可替换邮件
     provider、SMTP/Resend 和 reverse proxy timeout 配置。
   - 校验前缀不冲突、公开 origin 为 HTTPS、认证 secret 最小长度、session/验证码/限流参数为
     正数，以及按 `EMAIL_PROVIDER` 校验 SMTP 或 Resend 配置；生产启用 Web 认证时未显式
     选择完整 provider 必须 fail closed。

2. 新增持久化模型与 Alembic migration。
   - 在 `app/models.py` 增加 `WebAuthChallenge`、`WebSession`、索引和 FK。
   - 编写单一 head migration；同步 `vercel.json` `EXPECTED_DATABASE_REVISION`。
   - 为 SQLite 测试保留与现有 BigInteger 主键相容的显式 id 策略；真实迁移验证使用隔离
     PostgreSQL，不能对共享数据库执行 destructive downgrade。

3. 实现认证领域服务与邮件/限流适配器。
   - 新建 `app/web_auth.py`，集中邮箱规范化、验证码生成/HMAC 校验、challenge 生命周期、
     session 创建/解析/撤销与统一安全错误。
   - 新建 `EmailSender`、`InMemoryEmailSender`、`SmtpEmailSender` 和保留的
     `ResendEmailSender`；SMTP 使用标准库 STARTTLS，provider 选择仅在 composition root，
     所有生产 sender 使用受限超时和脱敏错误投影。
   - 新建 `LoginRateLimiter`、Redis 实现和内存 fake；邮箱/IP key 必须先 HMAC，Redis 不可用
     fail closed。

4. 扩展身份归并与禁用撤销。
   - 将 `web` 加入 link 分类、创建和消费规则，保持 Telegram/WeChat 文案与行为兼容。
   - 在 `_merge_tenants` 中锁定并撤销目标 Web sessions/MCP grants，迁移已撤销的 grant
     审计记录；来源凭证保持不变。
   - 将现有账户 disable 路径接入通用 Web session revoke helper。

5. 实现 Web API 与组合 ASGI 入口。
   - 新建 FastAPI 路由、请求/响应模型、Cookie/session dependency、Origin 校验和安全错误映射。
   - 已认证请求由服务端构造 Web `ChannelEnvelope` 后调用 `ChannelService`，绝不接受 caller
     指定的 tenant/identity。
   - 组合 MCP ASGI app 与 Web API，保留 MCP `/mcp` 路由、官方 SDK transport 和 bearer
     middleware；更新 CLI 启动路径与部署说明。

6. 编写与执行测试。
   - 认证领域：验证码 HMAC、TTL、重放、5 次上限、重新发送失效旧 code、session 固定 30 天、
     当前设备退出、禁用全量撤销。
   - 限流与邮件：60 秒、邮箱/IP 窗口、Redis 故障 fail closed、InMemory Sender、SMTP fake、
     Resend HTTP fake、超时与脱敏错误；自动化测试不得访问 Gmail 或其它真实邮件网络。
   - HTTP：Cookie 属性、无 CORS、Origin 拒绝、session tenant 固定、消息幂等和 Web `/new`。
   - 归并：四个 Web/Telegram/WeChat 方向、目标 sessions/grants 撤销、来源凭证存活、
     token/ingestion 回归。
   - MCP：官方 client 的 transport 和授权回归，确认组合入口未改变 `/mcp`。

## 重点风险与审查点

- 不能让 FastAPI auth middleware 包裹 MCP app，也不能让 MCP bearer 被识别为 Web session。
- 所有 raw session token、验证码、link token、SMTP/Gmail/Resend 认证材料和 provider body
  都不得持久化或日志输出。
- Web 消息必须经过 `ChannelService`，不能为 Web 写一条平行 Agent/retrieval 调用路径。
- 合并必须在一个事务中完成；`running` ingestion 失败时 token、凭证和 tenant 归属均不变。
- 对反向代理的 60 秒 read timeout、HTTPS、Cookie passthrough 和受信任 forwarding headers
  编写部署清单，不能只在应用代码中假设成立。

## 验证命令

```bash
pytest -q tests/test_web_auth.py tests/test_web_api.py tests/test_multiuser_integration.py tests/test_mcp_server.py
pytest -q
alembic heads
```

发布前还需由指定迁移操作者使用 direct Neon URL 执行 `alembic upgrade head`，检查
`alembic current`，并确认反向代理对 Web 对话请求的 upstream read timeout 至少为 60 秒。

## 回滚点

- 代码问题：停止公开 Web 路由或回滚应用版本；MCP 与 LangBot 保持原有入口。
- 邮件/Redis 配置问题：Web 登录 fail closed，不影响已有 Telegram、WeChat 或 MCP grant。
- 数据库迁移：不自动 downgrade 生产数据库；按 migration runbook 审核并执行恢复。
