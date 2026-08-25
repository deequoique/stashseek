# CLI 参考

仓库没有安装到系统 PATH 的独立 `kb` 可执行文件；以下应用命令都通过
`.venv/bin/python -m app.cli` 调用。单机生命周期命令是项目根目录的
`./scripts/stashseek`。

下面的 `app.cli` 行展示命令语法，假定所需配置已经在进程环境或根 `.env` 中。
launcher 生成的 `.env.runtime` 不会由 `app.cli` 自动读取；这类环境请使用
[首次运行教程中的 `notebook_run` helper](../tutorials/first-run.md)，在命令前加
`notebook_run`，以保持进程环境 > `.env` > `.env.runtime` 的优先级。

## launcher：`scripts/stashseek`

```text
./scripts/stashseek init --profile {read,full,langbot} [--force]
./scripts/stashseek start [--profile {read,full,langbot}] [--foreground]
./scripts/stashseek stop
./scripts/stashseek restart [--profile {read,full,langbot}] [--foreground]
./scripts/stashseek status
./scripts/stashseek logs [supervisor|mcp|gateway|worker|beat] [--follow] [--lines N]
```

`init` 创建或（带 `--force`）替换 launcher-owned `.env.runtime`。它不会覆盖
`.env`；切换 profile 时会尽量保留现有本地数据库、MinIO、Agent 和 gateway
secret。非交互调用至少需要预先提供 `ZHIPU_API_KEY`，没有外部数据库时还需要
`POSTGRES_PASSWORD`。

`start` 默认后台启动 supervisor；`--foreground` 让 supervisor 保持在当前
进程中，适合容器或另一个 service manager。`status` 的 exit code 为：运行且
所有检查 ready 时 `0`，停止或有检查 unavailable 时 `1`。日志行数必须在
`1..10000`；`logs` 的默认组件是 `supervisor`。

launcher 的 `stop` 只向当前 reservation 记录中的 supervisor 发信号，并验证
进程身份；不要用它管理外部 PostgreSQL、Redis、MinIO 或其他应用。

## 应用 CLI

### 导入、检索和问答

```text
.venv/bin/python -m app.cli ingest URL --user-id USER_ID [--why-saved TEXT]
.venv/bin/python -m app.cli search QUERY --user-id USER_ID [-k N]
.venv/bin/python -m app.cli ask QUESTION --user-id USER_ID [--thread THREAD]
```

- `ingest` 执行一个 URL 的同步导入路径并打印 `item=<id> state=<state>`；
  它需要当前连接器和 embedding/object store 能用，不是后台 queue 的替代
  部署接口。
- `search` 分别打印 BM25/trigram 与 vector 结果；默认 `-k 10`，需要
  embedding provider。
- `ask` 通过 `ChannelService` 执行一轮租户绑定的 Agent 问答；默认 thread
  是 `default`。命令会为该用户确保 `cli/local/<user-id>` identity；失败
  时以非零状态退出。

问题文本和回答可能包含私人资料，不要把 CLI 输出粘贴到公共日志或 issue。

### 用户与 identity 管理

```text
.venv/bin/python -m app.cli users create
.venv/bin/python -m app.cli users show --user-id USER_ID
.venv/bin/python -m app.cli users disable --user-id USER_ID
.venv/bin/python -m app.cli users enable --user-id USER_ID
.venv/bin/python -m app.cli users rebind-identity \
  --identity-id ID --user-id USER_ID
```

`create` 打印新 `user=<id>`；`disable` 会同时撤销该用户的 Web session；
`enable` 恢复用户。`rebind-identity` 是直接修改租户归属的 operator 命令，
应先核对 identity，避免把外部平台身份误绑定到错误用户。

### 服务进程

```text
.venv/bin/python -m app.cli gateway-server
.venv/bin/python -m app.cli web-server
.venv/bin/python -m app.cli mcp-server --transport stdio
.venv/bin/python -m app.cli mcp-server --transport streamable-http
```

`gateway-server` 必须使用 loopback 和 `CHANNEL_GATEWAY_SECRET`。`web-server`
读取 `WEB_*` 配置，并由 `WEB_SERVE_STATIC` 决定是否提供 `web/dist`。MCP
支持的 transport 只有 `stdio` 和 `streamable-http`；stdio 的 stdout 必须
保持 MCP protocol bytes，诊断写 stderr/private log。

### MCP grant 管理

```text
.venv/bin/python -m app.cli mcp-grant issue \
  --user-id USER_ID [--scope read|full] [--expires-at ISO-8601] \
  [--label LABEL] [--created-by TEXT]
.venv/bin/python -m app.cli mcp-grant list \
  [--user-id USER_ID] [--limit 1..100] [--offset 0..1000000]
.venv/bin/python -m app.cli mcp-grant show GRANT_ID
.venv/bin/python -m app.cli mcp-grant rotate GRANT_ID [--expires-at ISO-8601]
.venv/bin/python -m app.cli mcp-grant revoke GRANT_ID
.venv/bin/python -m app.cli mcp-grant disable GRANT_ID
```

`mcp-grants` 是同一命令的 alias。`issue` 和 `rotate` 输出 raw token 一次；
token 只保存 SHA-256 hash，`list`、`show`、`revoke`、`disable` 和日志不含
token。`--expires-at` 必须包含 timezone；不提供时 grant 默认不过期，直到
rotate、revoke、disable、用户禁用或 identity 禁用。

`read` grant 发现 `ask_stashseek`、兼容别名 `ask_notebook_agent`、`list_saved_items`、
`get_saved_item`。`full` 仍要通过 database、Redis、MinIO、maintenance 和
Celery worker readiness 才会发现写入 tools；签发 full grant 不会绕过这些
检查。

## 环境与退出行为

应用 CLI 从环境读取配置；它不会为 operator 自动生成 `.env.runtime`。需要
profile 组合、迁移门禁、子进程归属和安全停止时，用 launcher。任何带 user id、
identity id、item id 或 grant id 的命令都应由 operator 在私有终端执行；这些
内部编号不是跨租户授权凭据。
