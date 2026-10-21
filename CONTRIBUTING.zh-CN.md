# 参与贡献 StashSeek Chat

[English](CONTRIBUTING.md) | [简体中文](CONTRIBUTING.zh-CN.md)

感谢你关注 StashSeek Chat（搜藏助手）。报 bug、改文档、提交代码，都非常欢迎。

## 可以从哪里入手

- **报告 bug**：提 issue 时请写明运行的 commit、运行 profile（`read`、`full` 或 `langbot`）、复现步骤和预期结果。贴日志前请删掉 token、cookie 和私人资料库内容。
- **改进文档**：如果[第一次运行教程](docs/tutorials/first-run.md)或某篇[操作指南](docs/how-to/README.md)和你实际看到的不一样，欢迎直接修正。
- **新增内容来源**：服务端 connector 在 [`app/connectors/`](app/connectors/)。[`youtube.py`](app/connectors/youtube.py) 和 [`bilibili.py`](app/connectors/bilibili.py) 是基于 [`base.py`](app/connectors/base.py) 的现成样例。
- **认领 issue**：标了 [`good first issue`](https://github.com/deequoique/stashseek/labels/good%20first%20issue) 的适合新人上手；[`help wanted`](https://github.com/deequoique/stashseek/labels/help%20wanted) 是欢迎协助的较大事项。

小修小补以外的改动，请先开 issue 或在已有 issue 下留言，先对齐方案，免得白费功夫。

## 开发环境

需要 Python 3.11+、Node.js 22 + pnpm 11，以及 Docker。

### 后端

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'

cp .env.example .env
docker compose up -d postgres redis
```

`.env.example` 里只有占位值。保存视频和自然语言问答还需要 `ZHIPU_API_KEY` 和 Agent 模型凭据，但 CI 跑的那部分测试不需要。要跑起完整服务，请看[第一次运行教程](docs/tutorials/first-run.md)和[启用完整资料库](docs/how-to/run-full-library.md)。

运行测试：

```bash
.venv/bin/python -m pytest -q
```

需要 PostgreSQL 的测试会从环境变量或 `.env` 读取数据库配置，自己创建临时数据库；连不上数据库时会自动跳过。**只把它们指向本地、可随时丢弃的数据库**，不要指向共享或生产数据库。

每个 pull request 上，CI 会运行 [`.github/workflows/`](.github/workflows/) 里的 workflow。每个 workflow 都列出了它跑的 pytest 文件，你可以在本地跑同样的一组。CI 还会运行 `.venv/bin/alembic heads`，它必须只输出一个 head；新增 migration 时，请基于当前 head。

### 浏览器插件

```bash
cd extension
pnpm install
pnpm typecheck && pnpm lint && pnpm test && pnpm build && pnpm run audit
```

在 Chrome 中加载构建产物，或用 `pnpm package:local` 连接本地服务，请看 [extension/README.md](extension/README.md)。

改了 Web API 的话，在 `extension/` 下先运行 `pnpm generate:api`，再运行 `pnpm generate:types`，并提交重新生成的两个文件（`src/api/openapi.json` 和 `src/api/schema.d.ts`）。`pnpm check:api` 发现 `openapi.json` 过期时，CI 会失败；`schema.d.ts` 只有类型检查会间接发现问题，所以每次都要重新生成。

## Pull request

- 从 `dev` 拉分支，pull request 也提到 `dev`。`main` 是发布分支，合入后会部署到生产环境。
- 一个 pull request 只做一件事；行为有变化时，请补充或更新测试。
- commit 信息沿用仓库已有的 [Conventional Commits](https://www.conventionalcommits.org/zh-hans/) 风格，例如 `feat(extension): ...`、`fix(bilibili): ...`、`docs: ...`。
- push 之前，先在本地跑一遍上面的 CI 检查。
- 改了用户可见的行为、配置或命令，请同步更新 `docs/` 下的文档。

## 隐私与密钥

StashSeek Chat 存放的是用户的私人资料库，所以：

- 不要提交 `.env` 文件、API key、cookie 或真实用户内容。
- 测试数据请使用合成数据，或你有权分享的来源。
- 修改内容、凭据或日志的处理方式之前，请先读[隐私与信任设计](docs/explanation/privacy-and-trust.md)。

## 报告安全问题

请不要在公开 issue 里报告漏洞。请在本仓库的 **Security → Report a vulnerability** 私下提交。

## AI 工作流文件

`AGENTS.md` 和 `.trellis/` 是维护者的 AI 辅助开发流程。参与贡献不需要用到它们，不过 AI 编程工具会自动读取。

## 许可证

提交贡献即表示你同意你的贡献以 [GNU Affero General Public License v3.0](LICENSE) 授权，与本项目其余部分相同。
