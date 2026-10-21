# 执行计划：回答内嵌引用按钮

需求见 `prd.md`，设计见 `design.md`。实现由 `trellis-implement` 子代理（model sonnet）完成，检查由 `trellis-check` 完成，派发提示第一行写 `Active task: .trellis/tasks/10-20-inline-citation-chips`。后端和前端文件不重叠，门 1 和门 2 可以并行派发。

## 0. 前置

- [x] 后端测试只连 Neon 测试分支：

```bash
export DATABASE_URL="$(grep '^DATABASE_URL=' .env.neon-test | cut -d= -f2- | tr -d "\"'" | sed -E 's#^postgres(ql)?(\+[a-z0-9]+)?://#postgresql+psycopg://#')"
case "$DATABASE_URL" in postgresql+psycopg://*@ep-falling-fog-*) ;; *) echo "not the test branch"; exit 1;; esac
```

- [x] 前端工具链：插件的 pnpm 需要 Node 22（默认 nvm 的 v20 会让 pnpm 11 崩溃），用 `PATH` 里的 v22 加 `corepack pnpm`；脚本名以 `extension/package.json` 的 `scripts` 为准（测试、类型检查、`check:api`）。
- [x] 先读 `extension/src/chat/ChatPage.test.tsx` 里所有断言依据列表文案的用例（"展开字幕依据"、"回答来源"、"依据 N"），列出要改的清单。

## 1. 后端：标记编号投影（门 1）

- [x] 新建 `app/web/citation_markers.py`：`MARKER_RE`、`renumber_markers(text, ordinals)`、`MarkerNumbering`（`extend`/`rewrite`/`ordered`），契约见 `design.md` 2.2。纯函数，不依赖数据库。
- [x] 新建 `tests/test_citation_markers.py`：单标记、多标记、同一片段重复、不在列表的标记被删（连同前导空格）、`[S` 出现在"来源："区块里也被改写、`MarkerNumbering` 跨两个 section 编号连续、`ordered` 把未引用的引用放在最后并保持原顺序。
- [x] `app/api/conversation_routes.py`：
  - `browser_citations` 改为同时返回保留下来的原始 `Citation`（或返回位置对齐的列表），`project_answer` 用保留顺序建 `ordinals` 并改写 `text`；
  - 流式 `events()` 持有 `MarkerNumbering`：`section_started` 投影后 `extend`，`text_delta` 用 `rewrite`，兼容路径的一次性整段和 `completed.response` 用同一份编号与 `ordered` 列表；
  - 历史 `ConversationTurnResponse`：`_safe_citations` 返回保留顺序对应的 `segment_id`，`assistant_text` 用 `renumber_markers` 改写。
  - MCP、LangBot 路径一行不改。
- [x] `tests/test_conversation_streaming.py` 新增：两个 section 各两条引用时 `text_delta` 里是 `[^1]…[^4]`、`completed.response.citations` 与 section 顺序一致；某条引用被 `browser_citations` 过滤时编号仍连续且对应正确；兼容路径（无 section）整段改写；历史接口（`tests/test_web_api.py` 或最贴近的现有用例）返回的 `assistant_text` 不含 `[S`。

**门 1**：

```bash
.venv/bin/python -m pytest -q tests/test_citation_markers.py tests/test_conversation_streaming.py tests/test_web_api.py tests/test_agent_runtime.py
```

`tests/test_web_api*.py` 里依赖本地 `.env` 的既有失败不算；只看本次新增和改动的用例。

## 2. 前端：引用按钮与收起（门 2）

- [x] 新建 `extension/src/chat/citationChips.tsx`：`remarkCitationChips` 插件（手写 mdast 遍历，不加依赖；跳过 `code`/`inlineCode`）、`CitationChip` 组件（链接 + CSS 悬停提示：标题 · 时间点或"标题与简介" · 摘录前 120 字；`aria-label`、`aria-describedby`）、`UnreferencedChips`（"另见"行）。
- [x] `extension/src/chat/MarkdownAnswer.tsx`：接收 `citations` prop，`remarkPlugins={[remarkCitationChips]}`，`components.a` 对 `data-cite` 渲染 `CitationChip`（`n <= citations.length` 才渲染，否则原样文字），其它链接保持压成文字。
- [x] `extension/src/chat/MarkdownAnswer.test.tsx`：`[^2]` 渲染为可点击链接、`href` 等于第二条引用的 `url`、`aria-label` 含标题与时间；超出列表长度原样显示；代码块里的 `[^1]` 不处理；模型链接仍被压成文字；标题行引用（`start_sec` 为 null）提示文案是"标题与简介"且链接不带时间。
- [x] `extension/src/chat/ChatPage.tsx`：删除 `CitationList`、`chat-evidence` 区块和流式 section 下的列表；标题行 `依据 N` 改为 `引用 N` 开关（`aria-pressed`，切换 `data-cites-hidden`），无引用不渲染；流式按 `section_started` 顺序累积整轮引用并传给每个 `MarkdownAnswer`；`completed` 后用 `response.citations` 替换；`pendingAnswer` 用 `pendingCitations`；回答末尾渲染"另见"行（仅当有未引用的引用）。
- [x] `extension/src/styles.css`：删 `.chat-citations*`、`.chat-citation-*`；加 `.chat-cite`、`.chat-cite__tip`、`.chat-cite-toggle`、`.chat-cite-more`，用现有色板变量；`[data-cites-hidden="true"] .chat-cite { display: none }`。
- [x] `extension/src/chat/ChatPage.test.tsx`：更新第 0 步清单里的用例；新增：开关隐藏/恢复 chip、默认显示、无引用无开关；流式两段后 chip 对应正确的引用；历史回答的 chip 可点击。
- [x] 手机宽度下连续三个 chip 不拆句（用 `white-space: nowrap` 的断言或快照）。

**门 2**：在 `extension/` 下用 Node 22 + `corepack pnpm` 跑 `test`（vitest）、`typecheck`、`lint`（eslint，零警告）和 `check:api`（接口类型不变，应无 diff）。

## 3. 可选：本地预览核对

- [ ] 如果 `.claude/launch.json` 里有可用的网页开发服务器，用内置浏览器打开聊天页，发一个有引用的提问，核对：chip 在句尾、悬停出现提示、点击新标签页跳转、开关收起、没有依据列表。截图存到 `research/`。 —— 未做：本地 Caddy 证书不被内置浏览器信任，冒烟改为脚本走 API；渲染由 vitest 覆盖

## 4. 规范、检查与提交

- [x] `.trellis/spec/backend/agent-retrieval-convergence.md`：在 Web 投影部分加"浏览器端标记编号"契约（`[^n]` 规则、三条路径、过滤对齐、流式一致性、MCP 不变）。
- [x] `.trellis/spec/frontend/component-guidelines.md`：加 `CitationChip` 的可达性与 remark 插件约定（只碰文本节点、跳过代码）。
- [x] 运行 `trellis-check`。
- [x] 按路径提交到 dev：`git add`/`git commit --` 用数组展开路径（zsh 不自动分词），路径限定在 `app/web/citation_markers.py app/api/conversation_routes.py tests/test_citation_markers.py tests/test_conversation_streaming.py tests/test_web_api.py extension/src/chat extension/src/styles.css .trellis/spec/backend/agent-retrieval-convergence.md .trellis/spec/frontend/component-guidelines.md .trellis/tasks/10-20-inline-citation-chips`。

## 5. 发布到线上网页（2026-10-21 起由父任务 `10-21-release-readiness` 承接：dev 整体合 main，不再单独移植到 `web/`）

- [ ] 从 main 切 `release/inline-citation-chips`，把后端投影改动（`app/web/citation_markers.py`、`conversation_routes.py` 及测试）和前端改动移植到 `web/src/chat/`、`web/src/styles.css`（main 的 `ChatPage.tsx` 比 dev 旧，按 diff 手工移植，不要整文件覆盖）。 —— 不再适用：改为 dev 整体合 main（PR #36），`web/` 已删除
- [x] 开 PR 合并 main，部署由 Production gate 放行；上线后在浏览器核对 `stashseek.deequoique.tech` 的聊天页。 —— PR #36 → main `4eefa34`，用户放行部署；浏览器核对只到构建产物层面，见 AC5
- [x] 这一步和 `10-20-cn-recall-fusion` 的发布可以合在同一个 release 分支，顺序：先检索修复，后引用按钮。 —— 两者同在 PR #36，dev 上提交顺序 `58326da` → `26d4267`

## 风险文件与回滚点

| 文件 | 风险 | 回滚 |
|---|---|---|
| `app/api/conversation_routes.py` 流式分支 | 编号与 `completed` 列表不一致会让 chip 指错 | 回代码；无数据变更 |
| `extension/src/chat/ChatPage.tsx` | 现有 28 个用例大量依赖依据列表文案 | 按第 0 步清单逐个更新 |
| 线上 `web/` 移植 | main 与 dev 的 `ChatPage.tsx` 有差异 | 只移植本任务 diff，PR 里对照两份文件 |

## 开工前检查

- `prd.md` 无阻塞问题（两个待确认问题已由用户决定：悬停显示摘录；复制保留编号）。
- `design.md`、`implement.md` 已被用户确认。
- `implement.jsonl`、`check.jsonl` 有真实条目。
- `python3 ./.trellis/scripts/task.py start .trellis/tasks/10-20-inline-citation-chips` 只在用户确认后执行。
