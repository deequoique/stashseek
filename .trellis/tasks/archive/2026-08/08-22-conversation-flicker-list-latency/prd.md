# 修复会话答案闪烁与列表加载延迟

## Goal

让浏览器会话体验连续、快速：Agent 流式回答完成后最终答案始终可见；新建对话立即进入可输入状态；会话列表和邮箱鉴权不再被远程数据库串行往返放大。

## Background

- 本地应用入口为 `https://localhost:8443`，用户观察到回答完成后答案短暂消失，以及点击“新建检索”时 `GET /api/v1/conversations?limit=30` 长时间等待。
- 答案闪烁来自临时流式投影与持久化 transcript 的错误交接：`web/src/chat/ChatPage.tsx:308-321` 先清空 pending answer，再等待 transcript refetch。
- 新建对话被列表刷新阻塞：`web/src/chat/ChatPage.tsx:195-202` 的 mutation `onSuccess` 等待 conversation-list invalidation，因此 `newConversation.isPending` 持续为真并禁用输入。
- 列表接口存在 N+1：`app/api/conversation_routes.py:705-724` 先查会话页，再为每个会话单独查询最新 completed turn；`limit=30` 时最多增加 30 次串行数据库往返。
- Mailpit 假邮箱账户在 0 条会话时实测列表请求为 `2.978s` 和 `3.131s`。邮箱 Session 解析会分别查询 session、用户、渠道身份并写 `last_used_at`；受保护写请求还在 CSRF 边界和路由依赖中重复解析同一 Session。

## Requirements

- **R1 — Answer continuity:** 流式 terminal response 到持久化 transcript 的交接不得产生空白帧、消息消失或重复答案。
- **R2 — Non-blocking new conversation:** reset 成功后立即启用新会话输入；sidebar 刷新在后台收敛，不决定 mutation readiness。
- **R3 — Consistent history:** 会话列表最终与服务端一致，保留新建会话、标题、预览、更新时间、排序、分页和租户隔离。
- **R4 — Set-based list query:** `GET /api/v1/conversations?limit=30` 的最新 turn 投影必须使用集合查询；查询数不得随页面会话数线性增长。
- **R5 — Opaque Session:** 保留 `__Host-kb_session` / `__Host-kb_csrf`、hash-only token、服务端租户绑定、过期/撤销、用户与身份禁用、精确 Origin 和双提交 CSRF；不引入 JWT。
- **R6 — Request-scoped auth reuse:** Session、用户和渠道身份使用一次权威解析；同一请求的 CSRF 边界与路由依赖必须复用结果。
- **R7 — Redis read-through cache:** PostgreSQL 保持权威，生产已有 Redis 仅缓存有界 Session 投影；缓存 key 不含原始 token，TTL 不超过 Session 有效期，Redis 故障时回退 PostgreSQL且不得放行未验证 Session。
- **R8 — Safe invalidation:** 登录态撤销、退出、用户禁用、身份合并以及 Redis 故障恢复不得复用过期缓存；`last_used_at` 不再每请求同步写入。
- **R9 — Existing Agent contract:** 保留安全流式事件、Citation 投影、单次 Agent 执行、message 幂等和单次持久化语义。

## Acceptance Criteria

- [x] **AC1 (R1, R9):** 正常回答、Citation-first 回答和工具调用回答完成时，terminal answer 连续保留，持久化 refetch 延迟期间不消失，收敛后只显示一份。
- [x] **AC2 (R2, R3):** reset 返回后，即使 conversation-list promise 仍未完成，输入框也可用；sidebar 最终出现并选中新会话。
- [x] **AC3 (R4):** 无 cursor 的 30 条会话页使用常数次列表 SQL；cursor 页最多只增加常数次 cursor ownership 查询，标题、预览、completed-only 最新 turn 与排序保持一致。
- [x] **AC4 (R5, R6):** 一个受保护请求只产生一次 Session 权威解析；GET、受保护 POST、CSRF 失败、过期、撤销、用户禁用和身份禁用测试保持 fail-closed。
- [x] **AC5 (R7, R8):** Redis 命中、未命中、损坏值、超时/断线、进程启动代际切换、退出/撤销/禁用/合并后的缓存失效均有测试；缓存或日志中不出现原始 Session/CSRF token。
- [x] **AC6 (R4-R8):** 当前远程开发拓扑下，预热 Session 缓存后的 `limit=30` 列表中位耗时目标低于 1 秒；若环境波动无法稳定满足，必须提供固定 SQL/Redis 往返计数及前后实测证据，不得用减少安全检查伪造改善。
- [x] **AC7:** 前端组件测试、后端鉴权/会话测试、Python 全套、TypeScript、ESLint、Vite build、OpenAPI stale check 和 `git diff --check` 通过，或清楚隔离既有环境失败。

## Key Decisions

- PostgreSQL 是 Session 唯一权威来源；Redis 是 read-through 热缓存，不是新的身份数据库。
- 不采用 JWT、refresh token、独立认证网关或第二个 HTTP 中间件。
- 先完成单次数据库解析和请求内复用，再在同一边界加入 Redis 缓存。
- 复用生产已有 loopback Redis、`redis==8.1.0` 和现有 `secure_web_boundary`；开发环境允许缓存不可用时安全走数据库。
- Redis 使用全局缓存代际作为 MVP 失效边界：进程启动、凭据撤销/禁用/合并及故障恢复提升代际，使旧条目不可命中；短 TTL 作为第二道保护。

## Out of Scope

- 改变 Agent 回答内容、检索策略、流式步骤文案或 Citation 安全规则。
- 重做会话导航、引入新持久化系统、JWT/OAuth refresh-token 流程或新的 Redis/数据库容器。
- 缓存会话列表或 transcript 内容；本任务只缓存鉴权 Session 投影。
- 通过关闭流式输出、取消列表最终刷新、减少租户检查或放松 CSRF 来换取速度。

## Risks and Deferred Items

- 全局 Redis 代际会使一次登出/禁用令所有 Session 缓存变冷；当前规模优先安全与简单性，未来只有在测得 cache churn 后才考虑用户级代际。
- 本地内置浏览器受管理策略限制，无法访问 `https://localhost:8443`；浏览器自动化不可用时使用组件测试、真实 HTTPS/API 计时和用户侧 smoke 作为验收证据。
