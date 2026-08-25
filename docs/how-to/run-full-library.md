# 启用完整资料库

## 目标

把已经能运行只读 MCP 的 StashSeek Chat 切换为完整 profile，使用户可以保存
YouTube/Bilibili 普通视频、等待后台整理，并使用条目管理工具。完整 profile 会增加
Redis、MinIO、一个 Celery worker、一个 Beat scheduler 和 loopback-only LangBot
gateway；LangBot 仍然是可选的外部渠道，不是保存功能的前置条件。

## 前置条件

先完成[第一次运行](../tutorials/first-run.md)，并确认数据库迁移已经成功。完整 profile
还需要本地 Docker Compose，或已经准备好的外部 Redis、S3-compatible object store
和 PostgreSQL。外部服务由启动器做 readiness 检查，但不会被它停止或修改。

## 1. 切换 profile

在项目根目录执行：

```bash
./scripts/stashseek init --force --profile full
```

初始化会保留现有本地数据库 volume 所需的凭据，并更新 launcher-owned 的
`.env.runtime`。确认该文件仅当前用户可读：

```bash
stat -f '%Sp %N' .env.runtime 2>/dev/null || stat -c '%A %n' .env.runtime
```

期望权限为 `0600`。不要把 `CHANNEL_GATEWAY_SECRET`、数据库 URL、MinIO 密码或
模型凭据复制进截图、Issue 或共享 shell history。

## 2. 启动并检查后台能力

```bash
./scripts/stashseek start
./scripts/stashseek status
```

`full` 启动顺序包含依赖检查、单一 Alembic head、worker、Beat、MCP 和 gateway。
worker 必须同时监听 `ingest` 与 `maintenance`；同一个 supervisor 只允许一个 Beat。

出现问题时按组件查看有限日志：

```bash
./scripts/stashseek logs worker --lines 100
./scripts/stashseek logs beat --lines 100
./scripts/stashseek logs mcp --lines 100
./scripts/stashseek logs gateway --lines 100
```

不要用 `killall`、按进程名杀进程或直接运行 `docker compose down`。启动器只管理
它明确拥有的子进程和本地 Compose 资源；外部 PostgreSQL、Redis、object store
不会被生命周期命令停止。

## 3. 签发完整权限

read grant 不会因为 profile 改变而自动获得写权限。为需要保存或管理条目的用户单独
签发 `full` grant。launcher-managed 运行时先定义[首次运行教程中的
`notebook_run` helper](../tutorials/first-run.md)：

```bash
notebook_run .venv/bin/python -m app.cli mcp-grant issue \
  --user-id <user-id> \
  --scope full \
  --label full-library
```

raw token 只显示一次。将它放入受信任的 MCP 客户端私有配置；不要放进 URL query，
也不要把它交给不需要写权限的客户端。需要撤销或轮换时使用：

```bash
notebook_run .venv/bin/python -m app.cli mcp-grant rotate <grant-id>
notebook_run .venv/bin/python -m app.cli mcp-grant revoke <grant-id>
```

`full` grant 只有在 PostgreSQL、broker、object store、maintenance 配置和 worker
均通过 bounded readiness 后才会发现 mutation tools。客户端仍只看到三个工具时，
先检查 `status` 和 worker queues；不要绕过 readiness 检查。

## 4. 保存第一条视频

### 从 Web 保存

按[使用 Web 资料库](use-web-library.md)打开“添加视频”，一次最多提交 10 个
YouTube 或 Bilibili 普通视频链接。提交后先看到 `等待整理`，worker 完成后才会变成
`可阅读`。服务器不能读取的受限 YouTube 或 NTULearn/Kaltura 页面可以改用
[浏览器伴侣](use-browser-companion.md)。浏览器伴侣目前不支持 Bilibili；登录后或
URL-only 的 Bilibili 字幕应保留 `needs_extension` 状态，等待受支持的导入路径，
不要反复重试或尝试绕过平台权限。

### 从 MCP 保存

在拥有 `full` scope 且 mutation readiness 为 ready 的客户端中调用
`submit_knowledge_urls`。让客户端显示服务端返回的 canonical lifecycle，再用
`list_saved_items` 或 Web 资料库检查结果。删除操作需要服务端生成并持久化确认标记；
不要自行构造确认码或把模型文本当成已完成的删除。

## 5. 验证完整链路

用 Web 或 MCP 检查：

1. 条目先处于 `queued`/`processing`，而不是立即假设内容已经可检索；
2. ready 条目能返回标题、作者、字幕分页和时间戳；
3. Agent 问答只引用当前租户已保存内容，并给出可跳回原视频的时间点；
4. worker/Beat 日志不包含字幕正文、raw token、签名 URL 或 provider 响应体。

完整 ingestion 运行时的额度、对象存储和 Celery 调优请看[配置参考](../reference/configuration.md)。
