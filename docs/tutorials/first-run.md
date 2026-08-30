# 第一次运行：用只读 MCP 连接你的资料库

这是一条面向第一次自托管 StashSeek Chat 的安全路径。完成后，你会得到一个
只在本机监听的 Streamable HTTP MCP 服务，并能在 MCP 客户端中看到只读工具。
这条路径不启用 Redis、MinIO、Celery 或任何保存操作；需要保存视频时，再看
[启用完整资料库](../how-to/run-full-library.md)。

## 完成条件

完成本教程后应当看到：

- `./scripts/stashseek status` 显示 `read` profile 正在运行；
- MCP 客户端可以连接 `http://127.0.0.1:8000/mcp`；
- `tools/list` 显示 `ask_stashseek`、兼容别名 `ask_notebook_agent`、
  `list_saved_items` 和 `get_saved_item`。

## 前置准备

你需要：

- Python 3.11 或更高版本；
- Docker 与 Docker Compose；
- 一个 Zhipu Embedding API key；
- 一个与你选择的 Agent model 对应的模型凭据。

所有凭据只放在本机未提交的 `.env`、进程环境或 secret manager 中。不要把
凭据、MCP raw token 或包含 token 的 URL 写入 Git、截图、普通日志或工单。

## 1. 安装项目

在项目根目录执行：

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
```

启动器会把 profile 选择和本地运行所需的少量 secret 写入被 Git 忽略的
`.env.runtime`。它不会覆盖你维护的 `.env`；环境变量、`.env`、`.env.runtime`、
应用默认值的优先级依次降低。`.env.runtime` 应保持 `0600` 权限。

## 2. 初始化只读 profile

运行初始化向导：

```bash
./scripts/stashseek init --profile read
```

按提示输入 Embedding 和 Agent model 凭据。若你更习惯使用环境变量，也可以在
执行前设置 `ZHIPU_API_KEY`、`AGENT_MODEL` 和对应的 provider key；不要在命令行
历史中直接写入长期 secret。

`read` profile 只需要 PostgreSQL 和 MCP 服务。它不会启动 Redis、MinIO、worker、
Beat 或 LangBot gateway。

## 3. 启动并检查服务

```bash
./scripts/stashseek start
./scripts/stashseek status
```

第一次启动会准备本地 PostgreSQL 并执行迁移，然后在 loopback 地址提供 MCP。
若要看 MCP 进程的有限日志：

```bash
./scripts/stashseek logs mcp --lines 80
```

日志中不应出现 raw token、模型凭据、数据库密码或完整 capability URL。MCP 默认
绑定 `127.0.0.1:8000`；在没有 TLS reverse proxy 和明确边界审核前，不要改成公网
地址，也不要设置 `STASHSEEK_ALLOW_NON_LOOPBACK=true`。

启动器生成的数据库密码等值位于 `.env.runtime`，而独立的 `app.cli` 默认只读取
进程环境和 `.env`。在当前终端定义下面的临时 helper，让 operator 命令沿用启动器
的优先级：当前进程环境 > `.env` > `.env.runtime`。

```bash
stashseek_run() {
  if [ -f .env ]; then
    .venv/bin/dotenv -f .env run --no-override -- \
      .venv/bin/dotenv -f .env.runtime run --no-override -- "$@"
  else
    .venv/bin/dotenv -f .env.runtime run --no-override -- "$@"
  fi
}
```

这个 helper 只存在于当前 shell，不会显示或复制 secret。不要用 `source` 解析未知的
环境文件，也不要把 `.env.runtime` 改名、复制或提交为 `.env`。

## 4. 创建一个只读 MCP grant

先创建一个本地用户：

```bash
stashseek_run .venv/bin/python -m app.cli users create
```

复制命令输出的 `user_id`，再签发只读 grant：

```bash
stashseek_run .venv/bin/python -m app.cli mcp-grant issue \
  --user-id <user-id> \
  --scope read \
  --label first-run
```

命令只在签发或轮换时显示 raw token 一次。立即把它放入 MCP 客户端的私有配置；
不要把它当作用户名、URL query 参数或普通聊天内容。服务端只保存 token 的 hash。

## 5. 连接 MCP 客户端

在支持 Streamable HTTP 的 MCP 客户端中添加一个服务器：

```text
URL:   http://127.0.0.1:8000/mcp
Auth:  Authorization: Bearer <raw-token>
```

客户端的配置界面名称可能不同。如果客户端只支持 stdio，可以关闭由启动器管理的
MCP 进程，然后使用同一个 grant 运行：

```bash
export MCP_TOKEN='<raw-token>'
stashseek_run .venv/bin/python -m app.cli mcp-server --transport stdio
unset MCP_TOKEN
```

stdio 的 stdout 只能承载 MCP 协议数据；不要把调试信息或 shell 提示符混入 stdout。
将 `MCP_TOKEN` 作为进程环境传入，不要写入 `.env.example` 或提交到仓库。

连接后依次执行 `initialize`、`tools/list`，确认出现四个只读工具（其中一个是兼容别名）。空资料库也
是有效的第一次结果：调用 `list_saved_items` 应返回空列表或等价的“暂无资料”结果，
而不是让客户端获得另一个用户的内容。

## 下一步

- 要保存视频、运行后台整理并使用管理工具，按[启用完整资料库](../how-to/run-full-library.md)。
- 要在浏览器中登录、搜索和查看时间戳依据，按[使用 Web 资料库](../how-to/use-web-library.md)。
- 要了解 grant、scope 和 HTTP/stdio 选项，查看 [MCP 连接指南](../how-to/connect-mcp-client.md)
  与 [MCP 参考](../reference/mcp.md)。

停止本机运行时使用：

```bash
./scripts/stashseek stop
```
