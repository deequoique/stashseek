# Web 邮箱登录与跨渠道绑定

## 目标

让用户能在未来的 Web 客户端中通过邮箱证明身份、维持 Web 会话、与
Telegram/WeChat 共用同一个按租户隔离的 Notebook Agent，并在三端之间完成身份绑定。

## 已确认事实

- `app_user` 是唯一的租户根；Telegram、WeChat 和 MCP 分别通过可信身份或 grant
  归属到它。
- `ChannelService` 接收框架无关的 `ChannelEnvelope`，是命令处理、对话持久化、
  Agent 调用和按租户检索的共同入口。
- Telegram 与 WeChat 经由仅监听 loopback、使用 HMAC 认证的 LangBot bridge 接入。
  它是内部传输层，不能承担浏览器认证。
- 现有 `/link` token 是短期、仅存哈希、限定目标渠道且只能使用一次的凭证；归并
  tenant 时会合并内容，但保留各渠道独立的对话历史。目前白名单仅包含 Telegram
  和 WeChat。
- MCP bearer grant 是 MCP 客户端的集成能力凭证，而不是浏览器会话凭证。本任务无需
  为 Web 暴露 MCP 的内容管理工具。

## 范围

### 本期包含

- 仅实现后端、数据库迁移、运行配置和自动化测试；前端后续按稳定 HTTP API 接入。
- Web v1 的邮箱认证、会话生命周期、Agent 对话，以及 Web 与 Telegram/WeChat 的双向绑定。
- 邮箱认证采用 **6 位一次性验证码**。
- 邮件投递依赖可替换的 `EmailSender` provider 接口。`EMAIL_PROVIDER=smtp` 使用 SMTP
  适配器，当前本地开发可用 Gmail SMTP；`EMAIL_PROVIDER=resend` 保留 Resend HTTPS
  适配器。开发/测试未设置 provider 时使用内存 Fake Sender；生产启用 Web 认证时必须
  显式配置完整、可用的 provider，不能回退为内存 sender。生产邮件服务商暂不做最终决定。
- Web API 与 Streamable HTTP MCP server 部署在同一个长驻 Python 进程，通过同一个
  HTTPS 反向代理对外提供服务；Web 与 MCP 的路由和认证边界保持独立。Web API 不部署到
  Vercel Serverless。
- 未来 Web 前端与 Web API 同源部署。Web session Cookie 必须使用 `Secure`、`HttpOnly`
  和 `SameSite=Lax`；不开放 CORS，CSRF 防护按同源方案实现。

### 本期不包含

- Web 内容管理 HTTP API、密码认证、第三方 OAuth、账号拆分、多人工作区和前端代码。

## 需求

### 身份与认证

- 系统必须继续以 `AppUser` 为唯一账户/租户根。邮箱只是已验证的 Web identity，
  绝不能成为调用方可指定的 tenant 标识。
- 系统必须创建长度固定、过期、单次消费的 6 位邮箱验证码 challenge；数据库只能保存
  验证所需的规范化邮箱与安全验证码材料，日志中不得出现原始验证码、链接 token 或
  session token。
- challenge 有效期为 10 分钟，单个 challenge 最多校验 5 次；重新发送必须生成新
  challenge，并重新计算其 5 次校验次数。
- 同一邮箱发送验证码后 60 秒内不得再次发送、15 分钟内最多发送 3 次；同一 IP 每小时
  最多发送 10 次。所有限流和校验失败必须返回不可据此枚举邮箱存在与否的公开响应。
- 认证领域服务必须只依赖 `EmailSender` 抽象；任一 provider 的投递失败必须映射为有界、
  安全的错误，不能返回或记录服务商响应正文、验证码或认证密钥。
- `EMAIL_PROVIDER=smtp` 必须使用由运行配置注入的 host、port、STARTTLS、username、
  password 与 from address；Gmail App Password 仅允许存在于本地/开发 secret，绝不能写入
  代码、测试、日志、Git 或任务文档。
- `EMAIL_PROVIDER=resend` 时才要求 `RESEND_API_KEY` 与经过验证的发件地址；生产启用
  Web 认证而未显式配置完整、可用 provider 时必须 fail closed，不能降级为向日志输出验证码。
- 已验证的新邮箱必须创建一个 `AppUser`、邮箱 identity 和 Web identity；已验证的
  已有邮箱必须回到原 tenant，不能额外创建账户。
- 验证成功必须创建可撤销、会过期的服务端 Web session。每个受保护的 Web 请求必须
  从 session 解析 tenant，不能接受 body、path 或 query 中传入的 tenant 标识。
- Web session 支持多设备同时登录；每个 session 固定有效期为 30 天，不因访问续期，
  首期不限制设备数量。
- 用户退出登录只撤销当前 session，不影响其他设备。账号禁用时必须撤销该 tenant 全部
  Web sessions；首期不实现“退出所有设备”UI。
- 禁用账户或撤销 session 后，后续 Web 访问必须被拒绝。

### Web 对话

- 已认证的 Web 消息接口必须由服务端构造 `channel="web"` 的 `ChannelEnvelope`，
  并复用 `ChannelService`。
- 接口必须接收有长度上限的客户端 conversation id 和 message id，保持现有的消息
  幂等行为，并返回标准 `AgentAnswer` 投影。
- identity 绑定到同一个 `AppUser` 后，Web、Telegram 和 WeChat 必须从同一私有知识库
  检索、使用相同 Agent 配置；对话历史仍按渠道独立保存。

### 跨平台绑定

- 已登录的 Web identity 必须能创建目标为 Telegram 或 WeChat 的 link token；目标渠道
  通过 `/link <code>` 消费。
- 已登录的 Telegram 或 WeChat identity 必须能创建目标为 Web 的 token；已认证的 Web
  session 通过 HTTP API 消费。
- token 校验、锁定、过期、单次使用、ingestion 忙碌后的重试、内容去重和“来源 tenant
  获胜”的规则必须与现有渠道行为一致。
- tenant 归并成功时，目标 tenant 的 Web sessions 和 MCP grants 必须被显式撤销，而不是
  静默转移；来源 tenant 的凭证保持有效。

### 公开后端边界

- 公开 HTTP API 必须负责邮箱验证码、浏览器 session、CSRF/origin 防护和 Web API 的错误
  契约。
- 非安全 HTTP 方法必须校验同源 `Origin`（并以严格的同源策略处理缺失/不匹配请求）；
  不得配置宽泛 CORS 或接受跨源携带 Cookie 的调用。
- 现有 LangBot HMAC loopback gateway 不得接受公网浏览器流量，也不得用于认证 Web session。
- MCP transport 必须继续可用并复用同一领域服务；Web 客户端不得仅为使用应用而获得 MCP
  bearer grant。
- Web API 的 Agent 请求最长允许 45 秒；反向代理的 read timeout 等设置必须覆盖该时长
  并留出网络余量，不能由代理提前断开正常请求。

## 验收标准

- [ ] 未注册邮箱可完成 6 位验证码验证，且只获得一个活跃 `AppUser`、邮箱/Web identity
  和 Web session。
- [ ] 已验证邮箱登录后回到原 tenant，不会创建第二个 `AppUser`。
- [ ] 过期、重放、格式错误或触发限流的验证码不能创建 session，且公开响应不能泄露
  邮箱是否存在。
- [ ] 验证码有效期、5 次单 challenge 校验上限、60 秒重发间隔、每邮箱 15 分钟 3 次和
  每 IP 每小时 10 次发送上限均有确定性测试；重新发送会创建新 challenge。
- [ ] 已认证请求不能通过修改输入访问其他 tenant；过期、撤销或已禁用的 session 会被拒绝。
- [ ] 多设备 session 可并存且各自在创建后 30 天固定失效；退出当前设备不会影响其他
  session，账号禁用会撤销该 tenant 的全部 Web sessions。
- [ ] Web 消息进入与渠道消息相同的 `ChannelService`/Agent/检索路径，并且只能访问 session
  tenant 的知识。
- [ ] Web -> Telegram、Web -> WeChat、Telegram -> Web、WeChat -> Web 四种绑定方向都能
  将 identity 合并到一个 tenant，同时保持渠道对话历史独立。
- [ ] token 重放、目标不匹配、过期或目标 ingestion 运行中时保持现有安全失败语义；被阻塞的
  归并不能消费 token。
- [ ] 成功归并会撤销目标 Web session 和目标 MCP grant；来源 session 与 grant 仍可使用。
- [ ] Telegram、WeChat 与 MCP 的现有行为拥有回归测试保护。
- [ ] 内存 Fake Sender 可在测试中验证邮件投递内容；SMTP 与 Resend provider 的请求、
  超时和安全错误映射拥有独立 fake 测试，测试过程不访问真实邮件服务。
- [ ] `EMAIL_PROVIDER=smtp` 使用 STARTTLS SMTP sender；开发/测试未配置 provider 时仍使用
  内存 sender，生产启用 Web 认证但没有明确且完整 provider 配置时 fail closed。
- [ ] Resend adapter 使用 HTTPS API 和配置的发件地址，且不会在错误响应、日志或测试快照
  中泄露 API Key、验证码或 provider 响应正文。
- [ ] Web 与 MCP 同进程运行时分别维持 session 与 bearer grant 认证边界；代理超时配置
  覆盖 45 秒 Agent 请求并拥有部署文档和配置测试保护。
- [ ] session Cookie 同时具有 `Secure`、`HttpOnly`、`SameSite=Lax` 属性；跨源或缺失/
  不匹配 Origin 的状态变更请求被拒绝，响应不包含 CORS 放行头。

## 备注

- 这是复杂后端任务；开始实现前必须完成 `design.md` 与 `implement.md`，并由用户审阅
  最终规划。
