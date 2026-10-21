# release/2026-10-dev-sync 合并记录（2026-10-21）

## 基本信息

- 分支：`release/2026-10-dev-sync`，worktree：`.claude/worktrees/release-dev-sync`（未推送，未开 PR；已 `--unset-upstream`，避免误推到 `origin/main`）。
- 合并提交：`457e6e7`，父提交 `185b73c`（origin/main，PR #35）与 `90c55a4`（本地 dev）。
- 注意：合并的是**本地 dev**（`90c55a4`）。`origin/dev` 还在 `fe8155a`，比本地 dev 少 17 个提交，release 推送后 `origin/dev` 仍需另行同步。
- 与 prd.md 的干跑相比多 1 个冲突：共 34 个。多出来的是 `web/src/styles.css`（main 改过、dev 已删，属于 `web/src/*` 一类），所以 `web/src/*` 实为 12 个。

## 逐文件取法

| 文件 | 类型 | 取法 |
|---|---|---|
| `web/src/*` 12 个（`account/BrowserCompanionPage(.test).tsx`、`app/AppShell(.test).tsx`、`app/browserCompanion.ts`、`auth/LoginPage.test.tsx`、`library/AddVideosDialog.tsx`、`library/LibraryStates.tsx`、`showcase/ShowcasePage(.test).tsx`、`styles.css`、`videos/VideoDetailView.tsx`） | modify/delete | 取 dev 的删除（`git rm`）。逐个核对 main 在这些文件里的改动：`04caf99`（dev 提交，后被 main 以 `4642d24` 移植）已经在 `extension/src` 里；唯一缺口是 NTULearn 文案，见下 |
| `web/src/app/browserCompanion.test.ts` | main 新增（无冲突，git 自动保留） | `git rm`；内容与 `extension/src/app/browserCompanion.test.ts` 逐字相同 |
| `.github/workflows/web-auth-contract.yml` | modify/delete | 取 dev 的删除（父任务已决定不恢复）。它的 `deploy-production` 作业在 dev 的 `extension-api-contract.yml` 里已有同名作业，合并后推 main 仍只请求一次 Production 放行 |
| `.github/workflows/extension-api-contract.yml` | 内容 | 以 dev 为准（含 `deploy-production`，没有"Verification only"注释）；后端 pytest 步骤改用 main 的更宽名单（多 `test_extension_session_origin`、`test_bilibili_capture`、`test_conversation_extension_transport`、`test_library_extension_transport`、`test_conversation_page_thread_read_only`、`test_page_qa_*`、`test_privacy_page`）。名单是 dev 5 个文件的超集，在干净环境里跑过，见下面的验证结果 |
| `deploy/systemd/notebook-agent.service` | 内容 | 以 dev 为准：`WEB_SERVE_STATIC=true`、`WEB_STATIC_DIR=extension/dist`、`WEB_COOKIE_SECURE=true`、`WEB_FORWARDED_ALLOW_IPS=127.0.0.1`；不写 `WEB_PUBLIC_ORIGIN`（dev 的 `tests/test_linux_frontend_deployment.py` 断言它不在 unit 里）；`BROWSER_COMPANION_ALLOWED_ORIGINS` 两边本来一样（`omogodipchfidpikpeebgmlplpkjnpfm,jeemllapbelpglmpnhjfamcnoibpeail`），并集等于它 |
| `extension/manifest.json`、`extension/package.json` | 内容 | 以 dev 为准，版本取 main 的 `0.2.2`。`manifest.json` 合并后与 origin/main 逐字相同；`package.json` 只比 main 多 dev 的两个脚本（`build:local-https`、`generate:types`）。`pnpm-lock.yaml` 没有冲突，`pnpm install --frozen-lockfile` 通过 |
| `extension/src/protocol.ts` | 内容 | dev 版 + `CLIENT_VERSION = "0.2.2"` |
| `extension/src/app/browserCompanion.ts` | 内容 | dev 版（含 `BROWSER_COMPANION_STORE_URL`、`isExtensionContext`）+ 下载地址 `…production-0.2.2.zip` |
| `extension/src/auth/LoginPage.test.tsx`、`extension/src/showcase/ShowcasePage.test.tsx`、`extension/src/account/BrowserCompanionPage.test.tsx` | 内容 / 无冲突 | dev 版 + 下载地址 `0.2.2`（`BrowserCompanionPage.test.tsx` 无冲突，但里面写死 0.2.1，同样改为 0.2.2，否则与 `manifest.test.ts` 的版本一致性断言冲突） |
| `extension/src/sidepanel/manifest.test.ts` | 内容 | dev 版 + 版本断言 `0.2.2` |
| `extension/src/sidepanel/PageWorkspace.tsx`、`SidePanelCollect.test.tsx`、`deps.ts`、`extension/src/videos/VideoDetailView.tsx` | 内容 | 取 dev 版。main 侧的改动都已在 dev：`PrivacyLink`/`privacyUrl`、Kaltura 用例删除、`noCaptionPage()` 无参、"课程视频"标签（dev 在 `ingest/ingestCopy.ts` 的 `platformLabel`） |
| `extension/src/library/AddVideosDialog.tsx` | 手工并入 | 无冲突，但 main 的"去掉 NTULearn 文案"没进 dev（dev 的 `598337a` 重写了这行，保留了 "NTULearn 或"）。把提示改为"支持 BV 号及 YouTube、Bilibili 普通视频链接。服务器暂时无法读取的视频，可以在视频页面打开搜藏助手侧边栏收藏，详见浏览器伴侣。"，保留 dev 的 BV 号 |
| `.trellis/spec/backend/index.md` | 内容 | dev 行（含 External Website Login）+ main 的措辞：Browser Companion Capture 行写 "YouTube/Bilibili capture ingestion"（扩展 0.2.1 起不再读 NTU/Kaltura，服务端仍保留该平台） |
| `.trellis/spec/frontend/index.md` | 内容 | dev 文本，浏览器伴侣采集来源改为 "from YouTube and Bilibili"（去掉 NTULearn/Kaltura） |
| `.trellis/spec/backend/web-browser-runtime.md`、`.trellis/spec/backend/browser-companion-capture.md` | 内容 | 取 dev 侧（dev 新增的段落；main 侧为空） |
| `README.md`、`README.zh-CN.md`、`docs/how-to/README.md`、`docs/how-to/run-full-library.md` | 内容 | 取 dev 侧。dev 已经重写了这些段落，且不再提 NTULearn；main 的那处改动（NTU → Bilibili）已被覆盖 |

## 为什么不能用整文件 `--theirs`

对文件用 `git checkout --theirs` 会丢掉 git 自动合并进来的 main 独有改动。本次对每个整文件取 dev 的文件，都用 `git merge-file -p --diff3`（base `1c5ea70`、ours `origin/main`、theirs `dev`）重算了三方合并，再与我的结果 diff：差异只出现在冲突标记区内，说明没有丢 main 的非冲突改动。docs 类文件改用逐冲突块选择。

## 合并后核对

- `git diff dev HEAD --stat`：13 个文件、+22/−12，全部是上表的预期差异（工作流名单、两处 spec 措辞、`AddVideosDialog` 一行、六处 0.2.1→0.2.2、`manifest.json`/`package.json` 版本、离线 zip 新增）。
- `git diff origin/main HEAD --shortstat`：810 个文件，+105389/−25924（主要是 dev 的 84+ 个提交）。main 侧被删除的非 `web/` 文件：`web-auth-contract.yml`、`deploy/nginx/*` 两个、`docs/how-to/deploy-frontend.md`、`docs/how-to/use-web-library.md`、`docs/operations/production/debian-nginx-frontend.md`——都是 dev 的有意删除。
- 仓库里没有 `web/` 目录（`git ls-files | grep -c '^web/'` 为 0）。
- `.github/workflows/` 只剩 `extension-api-contract.yml`。

## 需要主会话决定的点

1. **离线插件 zip 无处可放。** main 把 `stashseek-chat-browser-companion-production-0.2.2.zip`（975813 字节，即商店 0.2.2 对应的生产包）提交在 `web/public/assets/`，网页版的"获取浏览器插件""下载离线安装包"链接（`/assets/…-0.2.2.zip`，登录页、首页、浏览器伴侣页三处）靠它。dev 删掉 `web/` 后没有任何构建步骤再产出 `dist/assets/` 下的这个文件，而 dev 的 `tests/test_linux_frontend_deployment.py::test_extension_is_the_distributed_client` 要求 `web/` 不存在。
   - 本次处理：`git mv` 到 `deploy/offline-extension/`，`web/` 彻底消失；**但现在没有任何东西把它放进 `extension/dist/assets/`，部署后这三处链接会 404（StaticFiles 返回 404，不会回落到 SPA）。**
   - 不能放进 `extension/public/assets/`：`package-release.mjs` 会把整个 `dist/` 打进商店包和离线包，等于把一个 zip 嵌进另一个 zip（还会随版本不断变大）。
   - 建议（需要你同意，它改动生产部署脚本）：`deploy/scripts/notebook-agent-deploy` 在 `corepack pnpm build` 之后、`.release-built` 之前加两行 `mkdir -p "$release_dir/extension/dist/assets"` 与 `cp "$release_dir"/deploy/offline-extension/*.zip "$release_dir/extension/dist/assets/"`，并在 `tests/test_linux_frontend_deployment.py` 里补一条顺序断言。备选：把这个入口也改成只走商店，删掉离线 zip 与三处链接。
2. **扩展源码版本与商店 0.2.2 不是同一份代码。** 合并后 `extension/` 源码标 `0.2.2`，但里面已经包含 dev 的内联引用、收藏夹/分P 等改动，与商店发布的 0.2.2 包不一致。本次不发新插件版本（prd），所以只要不再从这份源码打 `package:release`，没有问题；一旦要打，需要先升版本号。
3. 工作流里 `web-auth-contract.yml` 消失后，仓库没有分支保护或 rulesets（`gh api` 返回 404 / `[]`），所以不会因为"必需检查找不到"而无法合并。
4. 不是本次合并引入、但顺手看到的 dev 侧遗留：`docs/operations/production/README.md` 第 8 行仍链接已删除的 `debian-nginx-frontend.md`；`.trellis/spec/frontend/index.md` 第 9 行仍写"under `web/`"。没改，留给文档清理。

## 验证结果

以下全部在 release worktree 的合并提交 `457e6e7`（或其内容相同的状态）上跑。

| 命令 | 退出码 / 结果 |
|---|---|
| `.venv/bin/alembic heads`（Python 3.11.4，`pip install -e '.[dev]'`） | 0，`f8a9b0c1d2e3 (head)` 单头 |
| CI 的后端 pytest 名单（合并后的 15 个文件） | 0，282 passed |
| `pytest --collect-only`（全量） | 2891 collected，无收集错误 |
| 全量 pytest（干净检出，见下） | 2701 passed / 184 skipped / 6 failed；dev `90c55a4` 同样环境结果完全相同（6 个失败用例一致），所以合并没有引入失败 |
| `corepack pnpm install --frozen-lockfile`（Node 22.20.0，pnpm 11.16.0） | 0 |
| `pnpm check:api` / `typecheck` / `lint` / `test` / `build` / `run audit` | 全部 0；vitest 49 个文件、778 passed、8 skipped；`dist/` 含 `index.html`、`workspace.html`、`sidepanel.html` |

全量 pytest 里 6 个既有失败（dev 上同样失败，与本次合并无关，也不在 CI 名单里）：
`test_diagnostics::test_settings_reject_production_or_unknown_retrieval_content`（DID NOT RAISE）、
`test_item_management_tools` 两个用例（`purge_in_progress != restored`）、
`test_meeting_gold_rq1_stats::test_wilson_ci_degenerate_cases`（浮点 2e-17 != 0.0）、
`test_provider_and_explicit_user::test_action_services_are_always_composed`（缺 `OPENAI_API_KEY`）、
`test_web_email_auth::test_production_email_composition_mounts_browser_compatibility_paths`（OpenAPI 里出现 `item_id`）。

### 一个会误导"干净环境"的坑

`app/config.py` 用无参的 `load_dotenv()`，python-dotenv 会从 `app/` 所在目录**逐级向上**找 `.env`。release worktree 放在主检出的 `.claude/worktrees/` 下面，所以它会读到主检出的生产 `.env`（里面有生产 `DATABASE_URL`、SMTP、各家 API key）。worktree 里没有 `.env` 并不等于干净。

- 本次在 worktree 里跑的 alembic、CI 名单、全量 pytest 都先用 `env -i` 清空环境并显式给了一个无监听的 `DATABASE_URL=…127.0.0.1:5432…`、`REDIS_URL=…:6390…`（进程环境优先于 `.env`），所以没有连生产库；但 `.env` 里的其他键仍被加载，造成 3 个多余失败（通配 `chrome-extension://*` origin）。
- 真正干净的全量结果来自放在会话临时目录里的 detached 检出（`git worktree add --detach <scratchpad>/relclean 457e6e7`，用同一个 venv + `PYTHONPATH`），跑完已删除。
- 之后任何要在这个 worktree 里跑 pytest 或应用代码的步骤，都要显式覆盖 `DATABASE_URL`（见 MEMORY：pytest 会连生产库），或者用上面的临时检出。
