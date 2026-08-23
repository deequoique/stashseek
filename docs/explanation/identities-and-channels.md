# 身份、租户与渠道

Notebook Agent 不把 Telegram 用户名、邮箱、MCP token 或 CLI 参数直接当作
资料库 owner。它们先被解析为一个稳定的 `ChannelIdentity`，再映射到内部
`AppUser`，最后形成只在本次请求有效的 `TenantContext`。

## 三层模型

```text
外部凭据/平台身份
        ↓ resolve / verify / grant
ChannelIdentity(channel, account_id, external_user_id)
        ↓ app_user_id
AppUser（租户根）
        ↓ request-scoped projection
TenantContext
```

- `AppUser` 是租户根；资料项、对话、dispatch、MCP grant、Web session 和
  browser device 都通过它归属。
- `ChannelIdentity` 的三元组 `(channel, account_id, external_user_id)` 唯一，
  同一个 AppUser 可以拥有多个渠道身份。
- `TenantContext` 携带 `app_user_id`、identity id 和完整 channel namespace。
  下游 service 使用它做租户 predicates，不重建旧的 Telegram/WeChat identity。

## 当前入口

| 入口 | identity 形态 | 建立方式 | 主要凭据 |
| --- | --- | --- | --- |
| Web email | `web/web/<canonical email>` | email code 首次验证时显式创建 | `__Host-kb_session` + CSRF |
| Telegram/WeChat | `channel/account_id/external_user_id` | trusted channel envelope 首次到达时注册或解析 | gateway HMAC + plugin/platform session |
| MCP | `mcp/mcp/<grant_id>` | operator 给已有 AppUser 签发 grant 时显式创建 | raw grant Bearer（hash-at-rest） |
| CLI | `cli/local/<user_id>` | `ask` 前为指定 AppUser 显式确保 | 本地 operator 进程 |
| browser companion | 不作为普通消息渠道 | pairing/grant 绑定 AppUser | `capture:write` Bearer |

MCP 和 CLI 不会因为调用方在参数中写了任意 user id 就自注册或换租户；
MCP token 解析和 CLI identity 绑定都是显式 operator 路径。Web email session
的 joined resolver 只接受固定 `web/web` namespace。

## ChannelEnvelope 和 ChannelService

LangBot plugin、CLI、MCP 和 Web conversation 最终都进入类似的 envelope：

```text
channel, account_id, external_user_id,
conversation_id, message_id, text, request_id
```

`ChannelService` 先按 channel/account/external user/conversation 加锁，解析或
校验 identity，再处理 deterministic commands，最后把消息交给 Agent。这样
同一对话的重复 `message_id` 能返回已有 turn，而不会重复执行模型或 action。

Channel namespace 不能省略：两个平台可能都使用同一个数字 ID，但
`telegram/<bot-a>/123` 与 `wechat/<bot-b>/123` 不是同一 identity；同一个平台的
不同 bot/account 也不应自动合并。

## Web 登录的两条历史路径

生产 Web 使用 email OTP：服务端按 canonical email 创建/解析 `web/web` identity，
然后发放 opaque Web session。登录响应只显示 `authenticated`、login channel
和 expiry，不公开 AppUser、tenant、identity 或 session id。

迁移/开发兼容路径可由 Telegram/WeChat 的 `/web-login <code>` 批准一个短期
browser challenge。这个 challenge 记录 target channel 和 approved identity，
消费后发放同类 Web session；它不会复制一个新的 `web/web/<id>` 身份，也不会
让 Web app 自己注册出另一个租户。

## MCP grant 如何固定身份

一个 AppUser 可以有多个 grant，例如一个 `read` grant 给桌面 client，一个
`full` grant 给受控自动化。每个 grant 都有独立 `grant_id`、scope、expiry、
revoked/disabled 状态和 `mcp/mcp/<grant_id>` identity，但它们映射到同一个
AppUser。解析顺序是：

```text
token hash → active grant → scope → mcp identity → AppUser → TenantContext
```

因此工具 schema 不需要、也不允许 `app_user_id`；撤销一个 grant 不会误伤同
用户的其他 grant，禁用 AppUser 则会使所有对应凭据失效。

## 跨渠道绑定

跨渠道绑定是确定性的 identity 操作，不调用 Agent。用户在当前支持渠道生成
绑定码（例如 `/link telegram` 或 `/link wechat`），Notebook Agent 保存
`secrets.token_urlsafe(32)` 的 SHA-256 hash、目标渠道和过期时间；默认 TTL 是
10 分钟，token 最多消费一次。Web API 的 link route 同样只允许目标
`telegram`/`wechat`，并在成功消费后删除当前 Web session。

目标渠道提交 token 时，系统按以下顺序检查：token 存在/未过期/未使用、目标
渠道匹配、source/target identity 和 AppUser 都启用；检查通过后在一个数据库
事务中合并租户。token creator 的 AppUser 保留，目标 identity、conversation、
turn、非重复 content、其他 link token 和凭据记录迁移到它。被吸收租户的 Web
session 和 MCP grant 会被撤销，避免旧 credential 继续作为来源身份。

绑定不会把不同渠道的 conversation history 交错合并：历史仍按各自的 channel
thread 保持可区分。重复视频只保留一条，保存时间取最早值，备注保留 distinct
非空内容，watch position 取更完整/更晚状态，ready 和更完整的 ingestion data
优先。running ingestion 或会被 retire 的 duplicate delivery 会返回
`link_merge_busy`，token 不会被消费，便于稍后重试。

## 为什么不使用显示名称

显示名、昵称、邮箱本地部分和聊天 conversation label 都可变或可重复，不能
作为 ownership key。唯一身份依赖受信任 transport 提供的 channel、account 和
external id；日志和 smoke evidence 仍应删除这些外部标识的完整值。`/whoami`
只返回内部编号用于 operator/用户确认，不改变授权模型。
