# 技术设计：回答内嵌引用按钮

需求见 `prd.md`（用户 2026-10-20 确认：悬停显示标题、时间点和字幕摘录；复制保留编号）。

## 1. 边界

| 层 | 文件 | 改什么 |
|---|---|---|
| 网页接口投影 | 新增 `app/web/citation_markers.py`；`app/api/conversation_routes.py` | 把 `[S<segment_id>]` 改写成按引用列表位置编号的 `[^n]`，三条路径：非流式 `project_answer`、流式 `section_started`/`text_delta`/`completed`、历史 `ConversationTurnResponse` |
| 前端渲染 | `extension/src/chat/MarkdownAnswer.tsx`、新增 `extension/src/chat/citationChips.tsx`（remark 插件 + `CitationChip`）、`extension/src/chat/ChatPage.tsx`、`extension/src/styles.css` | 把 `[^n]` 渲染成句尾小按钮、悬停提示、收起开关；删除 `CitationList` 与依据区块 |
| 线上网页 | `web/src/chat/*`、`web/src/styles.css`（main） | 同样的改动移植到 release 分支 |

不动的：`Citation`、`ConversationCitationResponse` 的字段；Agent、Composer、`[S…]` 标记的生成与校验；持久化的 `assistant_text` 和 `sources`；MCP、LangBot 的文本；视频页问答（`QaThread`）。

## 2. 契约

### 2.1 编号规则

- 编号 `n` 等于该引用在**同一条回答的公开引用列表**里的位置（从 1 起）。列表就是接口返回的 `citations`，编号不单独传字段。
- 非流式和历史回答：列表顺序 = 现有投影顺序（`browser_citations` / `_safe_citations` 过滤后的顺序）。被过滤掉的引用（链接不安全、字段缺失）在正文里对应的标记直接删除。
- 流式回答：列表按 section 顺序拼接，每个 section 内按 `section_started.citations` 的顺序；`completed.response.citations` 就是这个拼接结果，后面追加正文没引用到的剩余引用（如有）。浏览器在收到每个 `section_started` 时把它的引用追加到本轮列表，就能立刻知道任何 `[^n]` 对应谁。
- 同一片段多次出现用同一个编号。正文里出现、但不在列表里的 `[S…]`（理论上不会有）删除。
- 线上标记形态是 `[^n]`。选它而不是 `[n]`：模型正文可能自然写出 `[1]`，而 `[^` 不会被 react-markdown（无 remark-gfm）当成脚注，也不会被当成链接；旧插件把它当纯文字显示。

### 2.2 后端

```python
# app/web/citation_markers.py
MARKER_RE = re.compile(r"\[S(\d+)\]")
def renumber_markers(text: str, ordinals: Mapping[int, int]) -> str
    # [S<id>] -> [^n]，id 不在 ordinals 里的标记整个删除（含前导空格）
class MarkerNumbering:
    """流式用：按 section 顺序累计编号。"""
    def extend(self, citations: Sequence[Citation]) -> list[int]   # 追加一个 section 的引用，返回它们的编号
    def rewrite(self, delta: str) -> str                           # 对一个 text_delta 应用 renumber_markers
    def ordered(self, all_citations: Sequence[Citation]) -> list[Citation]  # 已编号的在前（按编号），其余按原顺序追加
```

接入点（`app/api/conversation_routes.py`）：

- `project_answer(answer, session)`：先 `citations = browser_citations(...)`（得到保留下来的引用及其原始对象），据此建 `ordinals = {segment_id: n}`，`text = renumber_markers(answer.text, ordinals)`。`browser_citations` 需要返回"保留了哪些输入"的信息（改为返回 `(responses, kept_citations)` 或在内部按位置对齐），这样编号只给真正返回给浏览器的引用。
- 流式：在 `events()` 里持有一个 `MarkerNumbering`。`section_started`：先投影该 section 的引用（`project_citations`），对保留下来的引用 `extend`，公开事件的 `citations` 顺序不变。`text_delta`：`text = numbering.rewrite(internal.text)`。非 section 的兼容路径（`projected.text` 一次性整段）：用 `project_answer` 的结果。`completed`：`response` 由 `project_answer` 生成，但引用列表用 `numbering.ordered(...)` 保证与流式编号一致，正文用同一份 `ordinals` 改写。
- 历史 `ConversationTurnResponse`：`_safe_citations(sources)` 过滤后，按保留顺序建 `ordinals`（`text_source_payloads` 里有 `segment_id`），`assistant_text = renumber_markers(turn.assistant_text, ordinals)`。"来源："区块里的 `[S…]` 同样被改写，浏览器照旧按 `\n\n来源：\n` 剥掉，不受影响。
- `_StreamingTextGuard` 已保证一个 `text_delta` 里不会出现被截断的 `[`，所以逐 delta 替换是安全的；不改它。
- 不新增日志字段；编号是纯投影，不持久化。

### 2.3 前端

- `citationChips.tsx`
  - `remarkCitationChips`：遍历 mdast 的 `text` 节点（手写递归，不加依赖），按 `/\[\^(\d+)\]/g` 切分，命中部分替换成 `{ type: "link", url: "#cite-n", data: { hProperties: { "data-cite": n } }, children: [{ type: "text", value: String(n) }] }`。`inlineCode`/`code` 节点不处理。
  - `MarkdownAnswer` 接收 `citations: ConversationCitation[]`（可空）。`components.a`：有 `data-cite` 且 `n <= citations.length` 时渲染 `<CitationChip ordinal citation />`；否则保持现在的降级（`<span>`）。模型正文里的其它链接仍被压成文字，不变。
  - `CitationChip`：`<a class="chat-cite" href={citation.url} target="_blank" rel="noreferrer" aria-label="引用 n：《标题》 53:45" aria-describedby=tooltipId>n</a>` + `<span role="tooltip" id=tooltipId class="chat-cite__tip">标题 · 53:45（标题行引用为"标题与简介"） · 摘录前 120 字…</span>`。提示用 CSS 在 `:hover`/`:focus-visible` 时显示；触屏没有悬停，点击即跳转。连续多个 chip 之间 `margin-left: 0.15em`，`white-space: nowrap` 防止一句话被拆散。
  - 未被正文引用的引用（编号大于正文出现的最大编号的那些）：在回答末尾渲染一行 `另见 [n][n+1]…`，用同一个 `CitationChip`。
- `ChatPage.tsx`
  - 删除 `CitationList`、`chat-evidence` 区块、`chat-evidence__heading`、`展开字幕依据`；流式 section 下面的 `CitationList` 也删。
  - 标题行的 `依据 N` 改成 `<button type="button" aria-pressed={hidden} class="chat-cite-toggle">引用 N</button>`，切换该回答的 `data-cites-hidden`，CSS `[data-cites-hidden="true"] .chat-cite { display: none }`（文字不动，复制时仍带编号）。状态存在组件 `useState<Set<string>>`，key 用现有的 turn key；流式中的回答同样有开关。无引用时不渲染按钮。
  - 流式：`pendingSections` 累积出本轮 `citations`（按 `section_started` 顺序拼接）；`MarkdownAnswer` 对每个 section 传整轮列表。`completed` 后用 `response.citations` 替换，顺序一致所以编号不跳。
  - `pendingAnswer`（兼容路径的一次性整段）同样用 `pendingCitations`。
- `styles.css`：删 `.chat-citations*`、`.chat-citation-*`，加 `.chat-cite`、`.chat-cite__tip`、`.chat-cite-toggle`、`.chat-cite-more`（另见行）。色板沿用现有变量（`--green`、`--muted`）。

### 2.4 接口类型

`ConversationCitationResponse` 和流式事件字段都不变，所以 `extension/src/api/schema.d.ts` 不需要重新生成。

## 3. 数据流

```text
Agent/Composer ──[S1691] 文本 + Citation 列表──► 网页路由投影
   非流式/历史：ordinals = 公开列表顺序 ──► text: "...[^1]...[^2]"，citations: [c1, c2]
   流式：section_started(citations) ──► numbering.extend ──► text_delta: rewrite ──► completed: ordered()
                                                         │
浏览器 ◄── [^n] ──► remarkCitationChips ──► <a data-cite=n> ──► CitationChip(citations[n-1]) ──► 悬停提示 / 点击跳转
```

## 4. 取舍

- **编号在服务器做，不暴露内部 ID**：浏览器只需要列表顺序。代价是三条投影路径都要改；用一个纯函数和一个小状态类收口。
- **编号按列表位置而不是首次出现顺序**：流式时每个 section 的引用在 `section_started` 就确定，浏览器不用等正文；历史记录只有文本和列表，也能用同一规则。代价是正文里编号不一定递增出现（例如先 [^3] 后 [^1]），可接受。
- **`[^n]` 作为线上标记**：避免和模型可能写出的 `[1]` 混淆，又不触发 markdown 语义。旧插件显示为 `[^1]` 纯文字，直到更新。
- **remark 插件而不是预处理成 markdown 链接**：预处理会把 `[^n]` 变成链接语法，和"模型正文不得含链接"的校验语义打架，也容易被代码块里的文字误伤；插件只碰文本节点。
- **悬停提示用 CSS 而不是浮层库**：零依赖，键盘可达；触屏直接跳转，摘录看不到是已知取舍。
- **"另见"行**：保证列表删除后没有引用被藏起来；多数回答不会出现。

## 5. 兼容与回滚

- 接口字段不变；旧插件看到 `[^n]` 纯文字和照常的 `citations`，功能可用、观感略差。
- 回滚 = 回代码；没有数据变更。
- 线上网页：dev 提交后，开 `release/inline-citation-chips` 从 main 切出，把 `extension/src/chat/{MarkdownAnswer,citationChips,ChatPage,answerText}.tsx`、`styles.css` 的改动移植到 `web/src/`（main 的 `ChatPage.tsx` 比 dev 旧，`assistantTextWithoutSourceList` 还在文件内；按 diff 手工移植），同一分支带上后端投影改动，PR 合并 main 后自动部署，Production gate 由你放行。

## 6. 风险

- 流式编号与 `completed` 列表不一致：用同一个 `MarkerNumbering` 实例生成两者，并有测试断言"每个 section 的编号区间连续、completed 列表与之同序"。
- `browser_citations` 过滤掉引用后编号错位：编号只按保留下来的引用算，测试覆盖"中间一条被过滤"。
- 模型正文自带 `[^2]` 字样：只有 `n <= citations.length` 才渲染 chip，否则原样显示；不改校验。
- 测试里大量断言依据列表文案（`ChatPage.test.tsx` 第 1137 行等）：随改动一起更新。
