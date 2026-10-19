# Research: browser-extension UI for ingest status (collect progress, failed view, library, detail, page Q&A)

- **Query**: Read-only research of `extension/` so a design.md can rebuild the side-panel collect progress (prototype P01b/D02) and a "not finished" view (P01c) for side panel + library/detail pages; plus page-Q&A no-caption guidance.
- **Scope**: internal (extension code, prototype, a few server lines for context)
- **Date**: 2026-10-18

All paths are relative to the repo root `/Volumes/PeeB/projects/notebook-agent`. `ext/` below is shorthand for `extension/src/`. Line numbers were read on branch `dev` (HEAD 7f5a033) on 2026-10-18; `git status` shows no uncommitted changes under `extension/`.

---

## 0. Architecture in one paragraph

The extension has two Vite entries (`extension/vite.config.ts:6`: `workspace.html`, `sidepanel.html`).

- The **side panel** (`sidepanel.html` -> `ext/sidepanel/main.tsx` -> `SidePanel.tsx`) has no router. After login it renders a single view, `PageWorkspace.tsx` (`ext/sidepanel/SidePanel.tsx:102-111`).
- The **workspace** (`workspace.html` -> `ext/main.tsx` -> `ext/app/App.tsx`) is a React Router **`HashRouter`** app (`ext/app/App.tsx:194`) with routes `/library`, `/chat`, `/videos/:id`, `/account/link`, `/account/browser-companion` (`ext/app/App.tsx:176-180`).
- The two share `ext/styles.css`. The side panel adds `ext/sidepanel/sidepanel.css` (91 lines), imported at `ext/sidepanel/main.tsx:4-5`.

---

## 1. Side-panel collect flow

### Files

| File | Role |
|---|---|
| `ext/sidepanel/useCollect.ts` (284 l.) | Click handler `collect()`, poll loop `track()`, `resume()` after reopen |
| `ext/sidepanel/collectModel.ts` (211 l.) | `CollectEntry` type, `applyItem()` (folds a server item into the entry), `toStored`/`parseStored` |
| `ext/sidepanel/steps.ts` (244 l.) | `collectSteps()`, `collectSummary()`, `collectProgress()`, `collectResult()`, `collectWorkspacePath()`, `FAILURE_REASON` copy; also `qaSteps()` for Q&A turns |
| `ext/sidepanel/itemPoller.ts` (85 l.) | Generic `startPolling()` |
| `ext/sidepanel/eta.ts` (82 l.) | Client-side ETA heuristics |
| `ext/sidepanel/copy.ts` (116 l.) | `WORKER_COPY` (submission/page-read error copy), `askFailure`, `BILIBILI_NO_CAPTIONS_HINT` |
| `ext/sidepanel/QaThread.tsx` (152 l.) | Renders thread; contains `CollectBlock` (the collect UI) |
| `ext/chat/StepTimeline.tsx` (94 l.) | Shared collapsible step list (`<details class="chat-execution">`) |
| `ext/sidepanel/PageWorkspace.tsx` (178 l.) | The side-panel page: video card, caption line, 「收藏视频」 button, thread, composer |
| `ext/sidepanel/usePageQa.ts` (389 l.) | Session/thread state; owns `useCollect` |
| `ext/sidepanel/deps.ts` (149 l.) | Injectable dependencies incl. `collectStore` (storage.session), `tabs.openUrl`, `workspaceUrl` |

### How the collect block is rendered today

- It is **a tool-call-style collapsible block inside the Q&A thread**, not a page-like card. Chain: `PageWorkspace` -> `<QaThread>` (`PageWorkspace.tsx:133-140`) -> `<ol class="sp-turns">` -> `<li class="sp-turn sp-turn--action">` -> `CollectBlock` (`QaThread.tsx:122-127`).
- `CollectBlock` (`QaThread.tsx:81-107`):
  - `<article class="sp-collect" aria-busy aria-label="收藏视频">` (`:92`).
  - Inside it a `StepTimeline` with `className="sp-timeline"`, `stepsLabel="收藏步骤"` (`:93-100`). `StepTimeline` renders `<details class="chat-execution sp-timeline"><summary><span>{summary}</span><small>展开/收起</small></summary><div class="chat-execution__body"><ol class="chat-execution-steps">...` (`ext/chat/StepTimeline.tsx:63-91`).
  - Each step `<li class="is-{running|completed|failed|skipped}">` containing a dot `<span aria-hidden>` and `<p>{text}</p>`. If a step has `detail`, it is wrapped in `<div><p/>{detail}</div>` (`StepTimeline.tsx:77-86`).
  - Elapsed/ETA line is injected as `detail` on the running step: `<small className="sp-step-detail">{progress}</small>` (`QaThread.tsx:85-87`). Note `sp-step-detail` has no CSS rule of its own; styling comes from `.chat-execution-steps li > div small` (`ext/styles.css:769`).
  - Optional closing line `<p class="sp-collect__result" data-tone="success|action">` (`QaThread.tsx:101`) and link-button `在工作区查看` (`.sp-link.sp-collect__link`, `:102-104`).
  - Collapse behavior: `useAutoCollapse(entry.outcome === "ready" || entry.outcome === "already_exists")` (`QaThread.tsx:88`) collapses once on success. `failed`/`needs_action` blocks stay open (test: `ext/sidepanel/SidePanelCollect.test.tsx:437-452` asserts `details` has `open` after failure).
- The button that starts it is in the video card, **above** the thread: `PageWorkspace.tsx:100-107` (`<button class="button button--primary button--wide sp-collect-button" disabled={collecting}>{collecting ? "收藏中…" : "收藏视频"}`). `collecting = session.thread.some(isActiveCollect)` (`:61`), `isActiveCollect = kind==="collect" && phase!=="done"` (`useCollect.ts:46-48`). After the entry is `done` the button is enabled again (test `SidePanelCollect.test.tsx:433` `expect(collectButton()).toBeEnabled()`), and a click appends a **new** `CollectEntry` (`useCollect.ts:158-172`).

### Step list construction (what text appears)

`collectSteps(entry)` (`steps.ts:71-138`), in order:

1. `phase==="reading"` -> `running: 正在读取页面字幕` (`:75-78`).
2. Read line: capture path -> `completed: 已读取页面字幕（N 段）`; URL path with a `fallback` -> `skipped: <FALLBACK_COPY>` (`:79-80`, `FALLBACK_COPY` at `:9-14`, e.g. `视频没有字幕，改用链接导入`).
3. `phase==="submitting"` -> `running: 正在提交` (`:82-85`).
4. Pre-queue failures end the list: `quota_exceeded` -> `未能收藏：收藏数量已达上限`; `unsupported_url` -> `未能收藏：暂不支持直接导入这个视频`; `unsupported_part` -> `暂不支持收藏这一 P，可以在第 1P 收藏`; `submit_failed` -> `未能收藏：<workerErrorCopy(code)>` (`:87-91`).
5. `completed: 已提交` (`:93`).
6. **Working slots** (`workingSlots`, `:45-57`): URL path starts with `获取视频信息与字幕` (stages queued+fetching); capture path starts with `排队等待处理` (queued+fetching); then `语音转写中` **only if `entry.sawTranscribing`** (`:51`); then `切分段落` (chunking); then `生成检索向量` (embedding). While tracking: completed slots before the current one, then the current slot as `running` (`:102-108`). **Future slots are never emitted**, so there is no "not started" row today.
7. Ending by `outcome` (`:110-135`):
   - `ready` / `already_exists`: all slots completed, then `完成，可以在资料库里搜索和提问` / `已在资料库中` (`:111-115`).
   - `needs_action`: slots completed through the reached one, then failed step `需要处理：没有可用字幕，需要语音转写或从浏览器读取字幕` (`:116-119`; constant `NEEDS_ACTION_REASON` at `:36`). **One generic sentence, no error code involved.**
   - `failed`: failed step `未能完成：<reason>` + `，可以在工作区重试` if `entry.retryable` (`failureText`, `:65-68`). Reason = `FAILURE_REASON[errorCode] ?? "处理出错"` (`:66`).
   - `archived` -> skipped `这个视频已被归档`; `timeout` -> the current slot as `skipped`.
- `FAILURE_REASON` (`steps.ts:17-34`) keys: `queue_unavailable, ingestion_failed, transient_fetch_failed, item_missing, missing_dispatch, transcript_unavailable, transcript_invalid, ingest_too_large, asr_too_long, asr_audio_unavailable, asr_audio_too_large, asr_provider_failed, asr_no_speech, asr_not_configured, youtube_rate_limited, bilibili_rate_limited`. Server whitelist is `SAFE_ERROR_CODES` (`app/web/library.py:28-39`); it does not include the two `*_rate_limited` keys (`youtube_rate_limited`, `bilibili_rate_limited`).
- Summary line when collapsed (`collectSummary`, `steps.ts:141-154`): running step text prefixed with `收藏视频 · `, else `· 已完成 / 已在资料库中 / 需要处理 / 未能完成 / 已归档 / 已提交 / 仍在处理 / 未能提交`.
- Closing lines (`collectResult`, `steps.ts:183-191`): only `ready` (`已收藏到资料库。`), `already_exists`, `submitted`, `timeout`. **None for failed/needs_action.**
- Elapsed/ETA (`collectProgress`, `steps.ts:157-175`): `已用时 {formatElapsed}` + `预计还需…`/`比预计久，仍在处理` (`eta.ts:69-73`) + `连接不稳定，正在重试` when `retrying`. Per-stage seconds table at `eta.ts:10-16`. `QaThread.tsx:62-71` `useClock` re-renders every `clockTickMs` = 1000 ms (`deps.ts:148`).
- Link (`collectWorkspacePath`, `steps.ts:194-207`): for ready/already_exists/needs_action/failed/archived/submitted/timeout -> `/videos/<encodeURIComponent(itemPublicId)>` (or `/library` when id unknown); else null. Clicking calls `onOpenWorkspace(route)` = `openWorkspaceRoute` (`PageWorkspace.tsx:46-48`).

### Server fields the panel consumes

From the library item (`getItem` -> `GET /api/v1/library/items/{id}`, `ext/api/client.ts:812-814`) via `applyItem` (`collectModel.ts:144-166`):

| Field | Used for | Cite |
|---|---|---|
| `lifecycle` | decides the end: `ready`/`needs_action`/`failed`/`archived` -> `phase="done"` + `outcome`; anything else keeps tracking | `collectModel.ts:133-141, 159-164` |
| `stage` | current stage; falls back to `stageFromLifecycle` for old servers (processing -> `chunking` on capture path, `fetching` on URL path) | `collectModel.ts:114-117, 145` |
| `stage_updated_at` | start time of the first stage seen, clamped (not future, <6 h) | `collectModel.ts:119-131, 153` |
| `duration_sec` | ETA input | `collectModel.ts:150` |
| `available_actions` | **only** `includes("retry")` -> `entry.retryable` -> adds the text `，可以在工作区重试` | `collectModel.ts:151`, `steps.ts:67` |
| `error_code` | `entry.errorCode` -> `FAILURE_REASON` lookup (set only when an end `outcome` exists) | `collectModel.ts:159-164` |

Not read by the panel: `title, author, platform, url, why_saved, text_source, tags, summary, latest_dispatch_public_id, cover_url, chapters, kind` (grep of `ext/` found no use in `sidepanel/`). The video title/platform shown in the panel come from the **page read** (`PageWorkspace.tsx:85-86`), not from the server item.

From submission results: capture path `result.item_public_id` and `result.status === "already_exists"` (`useCollect.ts:203`, type `SaveResult` at `workerApi.ts:16-20`); URL path `response.results[0]` with `status` in `queued|already_exists|quota_exceeded|unsupported_url|invalid_url|<other>` and `safe_error_code` (`useCollect.ts:239-256`; schema `BatchItemResponse` at `ext/api/schema.d.ts:558-574`).

### Polling: how it works and when it stops

- `track()` (`useCollect.ts:101-143`) calls `startPolling({fetch: () => deps.getItem(itemId), ...})`. First read is immediate (`itemPoller.ts:74` `schedule(0)`), then every `pollIntervalMs` = **2500 ms** (`deps.ts:144`); after a failed read the delay is `pollBackoffMs` = **10000 ms** (`deps.ts:145`, `itemPoller.ts:67`).
- Reads are skipped while the panel document is hidden (`itemPoller.ts:48-51`, `deps.isVisible` at `deps.ts:137`); a `visibilitychange` event nudges an immediate read (`useCollect.ts:145-156`).
- **Stops when**:
  1. `applyItem` yields `phase==="done"` (i.e. `lifecycle` is `ready`, `needs_action`, `failed` or `archived`), `onResult` returns `"stop"` (`useCollect.ts:109-119`). **The poller stops at the first terminal lifecycle value; it does not wait for stage-level completion.** `outcomeFor` ignores `stage` (`collectModel.ts:133-141`).
  2. HTTP 401 -> `onSessionInvalid()` + stop (`useCollect.ts:121-124`).
  3. HTTP 404 -> `finishEntry(..., "failed", "item_missing")` + stop (`useCollect.ts:125-129`).
  4. Deadline = `entry.startedAt + pollMaxMs` = **30 min** (`deps.ts:146`, `useCollect.ts:139`) -> `finishEntry(entry, "timeout")` (`useCollect.ts:133-136`).
  5. Panel unmount (`useCollect.ts:151-155`) or the user navigates to a different video (`usePageQa.ts:261-262` `stopCollectPolling()`).
- Other errors: set `retrying: true`, keep polling at backoff (`useCollect.ts:130-131`).
- Closing the panel stops only the client-side polling; the server job is unaffected (server side, PRD "现状"). On reopen, `resume()` restarts polling (below).

### What is persisted in `chrome.storage.session`

- Key `collectEntries`, value a map `videoKey -> StoredCollect` (`deps.ts:102-125`). Failures of storage are swallowed (`deps.ts:113-123`).
- `StoredCollect = Omit<CollectEntry, "retrying">` (`collectModel.ts:169-175`). Fields: `id, videoKey, phase, path, fallback, itemPublicId, startedAt, durationSec, segments, captionless, existing, stage, stageSince, workingStage, sawTranscribing, outcome, errorCode, retryable` (`collectModel.ts:33-61`). Never caption text (`collectModel.ts:29-31`, test `collectModel.test.ts:82`).
- **When saved**: on `follow()` after the item id is known (`useCollect.ts:180-185`) and again whenever `stage` string changes during polling (`useCollect.ts:117`).
- **When cleared**: entry done (`useCollect.ts:107, 116`), 404 (`:127-128`), deadline (`:135`).
- **Resume** (`useCollect.ts:265-281`): called when a video session starts (`usePageQa.ts:267`). `parseStored` accepts only `phase==="tracking"` with a valid `path` and an `itemPublicId` (`collectModel.ts:182-193`); older than `pollMaxMs` -> cleared (`useCollect.ts:273-276`); skips if an active collect already exists (`:277`).
- **Consequence for a finished entry**: finished entries are not persisted (store cleared on done). The thread itself is only in React state (`VideoSession.thread`, `usePageQa.ts:39-49`; replaced when a different video opens, `usePageQa.ts:261-266`). So after closing/reopening the panel, a **failed or needs_action collect leaves no block at all**; only an in-progress one is restored (`SidePanelCollect.test.tsx:580-642`).

### Can the side panel retry?

- **No retry call exists in the side panel.** `PanelDeps` (`deps.ts:28-61`) has `submitBatch` and `getItem` but no retry function; `WorkerApi` (`workerApi.ts:23-37`) has no retry command. `retryItem` exists only in `ext/api/client.ts:842-847` (`POST /api/v1/library/items/{id}:retry` with an `Idempotency-Key`) and is used only by `ext/videos/VideoDetailPage.tsx:51`.
- The panel text for a failed item with the `retry` action is `未能完成：<reason>，可以在工作区重试` (`steps.ts:67`), plus the link `在工作区查看` that opens `/videos/<id>` in a new tab (`QaThread.tsx:102-104`, `PageWorkspace.tsx:46-48`).
- The only in-panel re-attempt is clicking 「收藏视频」 again once the entry is `done` (`PageWorkspace.tsx:61, 100-107`). For an existing item the URL path just follows whatever the server returns for `already_exists` (`useCollect.ts:243-244`); the extension code does not distinguish a failed existing item.
- `needs_action` never shows the "retry" hint: `retryable` is only true when the server lists `retry`, and the server lists it only for `failed` (`app/web/library.py:176-180`, quoted: `if state == "failed" and (latest is None or latest.state not in ACTIVE_DISPATCH_STATES): actions.append("retry")`).

### Test coverage of this flow
See section 8.

---

## 2. Library page

### Files
`ext/library/LibraryPage.tsx` (333 l.), `ext/library/VideoCard.tsx` (53 l.), `ext/library/lifecycle.ts` (44 l.), `ext/library/LibraryStates.tsx` (54 l.), `ext/library/collections.ts`, `ext/library/searchSuggestions.ts`, `ext/library/AddVideosDialog.tsx`.

### Item rendering
- Query: `useQuery(["library", query])` with `listLibraryItems(query)`; `retry: 1`; `refetchInterval` 4000 ms while any visible item is `queued`/`processing`, else false (`LibraryPage.tsx:59-64`; `lifecycle.ts:15-19`). A `.live-note` "正在自动更新状态" shows while polling (`LibraryPage.tsx:279`).
- Items are split: `workItems = items.filter(isLibraryWorkItem)` where `isLibraryWorkItem` = lifecycle in `queued, processing, needs_action, failed` (`lifecycle.ts:26-30`); everything else (`ready`, `archived`, and any unknown value) is `readableItems` (`LibraryPage.tsx:110-111`).
  - `readableItems` render in `<section class="library-ready-zone"><div class="video-grid">` (`:280-286`).
  - `workItems` render in `<section class="library-work-zone">` with header `整理状态` / `整理队列` (`:287-313`), then a `<progress>` ("约 N%") computed from `queued`=20 / `processing`=65 averages (`lifecycle.ts:21-44`) shown only when at least one is queued/processing (`LibraryPage.tsx:299-311`).
- `VideoCard` (`VideoCard.tsx:18-53`): `<article class="video-card" data-lifecycle=...>`; link to `/videos/<public_id>` via `RouteLink` (`:25`); cover or placeholder `暂无封面`; duration badge; **status pill** `<span class="status-pill status-pill--{lifecycle}">{lifecycleCopy[lifecycle].label}</span>` (`:32`); date; title (fallback `视频信息尚未准备好`, `:20`); author; collection tags; `why_saved` as blockquote; for `failed` only: `<p class="card-hint">打开详情后可重新整理</p>` (`:39`; test `VideoCard.test.tsx:92-101`). `edit_why_saved` action adds an `编辑归类` link (`:42-50`).
- **No failure reason is shown on cards** (`error_code` is not read in `ext/library/*` - grep). `needs_action` cards show only the pill `字幕不可用`.

### Status labels (`ext/library/lifecycle.ts:3-13`)
| lifecycle | label | description (defined, **not rendered anywhere**; only `.label` is used: `VideoCard.tsx:32`, `VideoDetailView.tsx:154`) |
|---|---|---|
| queued | 等待整理 | 已保存，正在等待系统开始整理。 |
| processing | 正在整理 | 正在获取视频信息和字幕。 |
| ready | 可阅读 | 章节和字幕已准备好。 |
| needs_action | 字幕不可用 | 暂时没有可用字幕，请打开详情查看可用操作。 |
| failed | 整理失败 | 整理未完成，请打开详情查看是否可以重新整理。 |
| archived | 已归档 | 已从默认资料库隐藏，可随时恢复。 |

Pill colors: `.status-pill--ready|processing|queued|failed|needs_action|archived` at `ext/styles.css:541-546`. There is no pill/class for "正文不可检索" or partial states.

### Lifecycle filter
`<select name="lifecycle">` options hard-coded in `LibraryPage.tsx:229-237`: `全部状态 ("") / 可阅读 ready / 等待整理 queued / 正在整理 processing / 字幕不可用 needs_action / 整理失败 failed / 已归档 archived`. The option labels duplicate `lifecycleCopy` labels (they are separate string literals). State is `useState("")` (`:47`); `changeFilter` resets page (`:127-130`); `include_archived: lifecycle === "archived"` (`:54`). Server side the param is a closed `Literal["archived","ready","needs_action","failed","processing","queued"]` (`app/api/library_routes.py:183-185`), so an unknown filter value would be rejected with 422.

### Search box
- `<form class="search-box" role="search">` with `<input id="library-search" name="search" type="search" placeholder="搜索标题、作者或保存说明">` and a submit button `搜索` (`LibraryPage.tsx:181-226`). Suggestions come from the loaded items (`searchSuggestions.ts`).
- API param: `search` (`LibraryQuery.search`, `ext/api/client.ts:793-801`; serialised by `listLibraryItems` skipping `undefined`/`""`/`false`, `:803-810`). Server: `search: str | None = Query(max_length=200)` (`app/api/library_routes.py:181`) -> `ILIKE %term%` on **`title`, `author`, `why_saved`** (`app/web/library.py:302-309`). It does not search transcript text. So the existing search is exactly "title / author / 备注(why_saved)".
- `submitSearch(value)` sets `search` + `searchDraft`, clears the selected collection, resets page, closes suggestions (`LibraryPage.tsx:118-125`). Selecting a collection clears the search (`:132-137`).

### URL / deep-link possibilities
- **`LibraryPage` reads no URL state**: `search`, `lifecycle`, `sort`, `page`, `selectedCollection` are all `useState` (`LibraryPage.tsx:41-49`); `useSearchParams` is used only in `ext/account/BrowserCompanionPage.tsx:3, 26` (grep). The hash route is plain `/library` (`ext/app/App.tsx:176`).
- Query strings inside the hash are a known pattern here: `PAIRING_SEARCH_RE = /^\?pairing=[a-f0-9]{32}$/` (`ext/app/App.tsx:36`), `useSearchParams` under `HashRouter` in `BrowserCompanionPage.tsx:26`. So `workspace.html#/library?search=...` would be parsed by the router (`location.search`), but nothing in `LibraryPage` consumes it today.
- **Login redirect drops it**: unauthenticated visits go `Navigate to="/login"` with `state.returnTo` only for the browser-companion path (`App.tsx:119-122`, `browserCompanionReturnTo` `:38-42`); `loginReturnTo` falls back to `/library` for anything else (`App.tsx:44-52`). A `/library?search=...` link would lose its query if the workspace session is not logged in. (The workspace and side panel authenticate separately; the workspace's own cookie/Bearer session is checked by `ProtectedLayout`, `App.tsx:102-133`.)
- Inside the workspace, `RouteLink` and `useRouteNavigate` wrap `react-router` navigation with view transitions (`ext/app/RouteTransition.tsx:59-67, 69-98`).
- The server search term is a plain substring, `max_length=200` (`library_routes.py:181`). A title-based "仅查标题 / 备注" must therefore supply some keyword text (the prototype uses a keyword, `state.query='访谈'`, `prototype.html:33`).

---

## 3. Detail page

### Files
`ext/videos/VideoDetailPage.tsx` (93 l.; data/mutations) and `ext/videos/VideoDetailView.tsx` (292 l.; presentation).

### Status display
- Hero: `<span class="status-pill status-pill--{lifecycle}">{lifecycleCopy[lifecycle].label}</span>`, then `<h1 class="detail-title">` (fallback `视频信息尚未准备好`, `VideoDetailView.tsx:88`), `<p class="detail-meta">` (author, duration, language) (`:154-160`).
- Actions row `<div class="detail-actions" aria-label="视频操作">` (`:161-166`): `在 {platformLabel} 查看` (external link, always), `重新整理` if `actions.has("retry")`, `归档` if `archive`, `恢复到资料库` if `restore`.
- `重新整理` (`:163`) is `<button class="button button--quiet" disabled={actionPending} onClick={onRetry}>`; `onRetry` -> `action.mutate({type:"retry"})` -> `retryItem(id)` (`VideoDetailPage.tsx:85, 48-53`); on success the item query data is replaced and `["library"]` invalidated (`:54-57`). Test: `VideoDetailView.test.tsx:157-183` (shown only when `available_actions` has `retry`).
- Error display: `actionError` renders `<p class="inline-error" role="alert" aria-label="视频操作失败">操作未完成，请稍后重试。</p>` (`:167`) - a boolean only, so a `quota_exceeded` rejection from `retryItem` is not distinguished. Reason text exists **only** for `youtube_rate_limited` (`:168`) and `bilibili_rate_limited` (`:169`) via `.inline-error`. No reason is displayed for `failed`/`needs_action` with any other `error_code`.
- Polling: item query refetches every 4000 ms only while `queued`/`processing` (`VideoDetailPage.tsx:25-28`). Transcript query is `enabled` only when `lifecycle === "ready"` (`:35`); empty transcript text is `item.lifecycle === "ready" ? "这个视频没有可显示的字幕。" : "整理完成后可查看字幕。"` (`VideoDetailView.tsx:287`).

### Layout order of sections (where category / summary-like blocks sit)
1. `detail-hero` (cover, status pill, title, meta, actions, inline errors) `:149-171`
2. `reason-section#saved-context` "为什么保存" (why_saved + collection tags + edit form) `:173-213`
3. chapters `:215-233`
4. description "视频简介" (rendered when non-empty) `:235-241`
5. **summary** "整理结果 / 摘要" `<section class="detail-section" aria-labelledby="summary-title">` rendered only when `item.summary?.trim()` (`:243-249`) - the only existing "AI result" region; test `VideoDetailView.test.tsx:140-155`
6. transcript "字幕节选" `:251-289`

There is **no category / content-key-points region and no placeholder text** ("未分类", "暂无内容要点") in the detail view today. `item.tags` is in the schema (`schema.d.ts:1118`) but is not used by any component (grep). The existing `.detail-section` / `.eyebrow` / `h2` pattern (`ext/styles.css:600-602`) is what summary uses.

---

## 4. How the side panel opens workspace pages

- Helper: `deps.workspaceUrl(route)` = `chrome.runtime.getURL(\`workspace.html#${route}\`)` (`ext/sidepanel/deps.ts:42-43, 138`). In tests: `chrome-extension://test/workspace.html#${route}` (`ext/test/sidepanelEnvironment.ts:215`).
- Opening: `deps.tabs.openUrl(url)` = `chrome.tabs.create({ url })` (`deps.ts:17, 97-99`) - always a **new tab**.
- Panel call site: `PageWorkspace.tsx:46-48`: `function openWorkspaceRoute(route) { void deps.tabs.openUrl(deps.workspaceUrl(route)); }`, passed as `onOpenWorkspace` to `QaThread` (`:139`) and used by `CollectBlock`'s `在工作区查看` (`QaThread.tsx:102-104`). Routes produced today: `/videos/<encodeURIComponent(id)>` or `/library` (`steps.ts:194-207`).
- A second path, footer button `打开工作区` -> `deps.worker.openWorkspace()` (`PageWorkspace.tsx:174`) -> runtime message `OPEN_WORKSPACE` -> worker opens `workspace.html#/chat` (`ext/worker.ts:242-243`, `workerApi.ts:60`). It does not take a route.
- Test of the opening: `SidePanel.test.tsx:232-240` ("opens the workspace from the panel"); `deps.test.ts:106-126, 153-156`.
- A link to an arbitrary route (e.g. `/library?search=...` or `/videos/<id>`) therefore only needs `openWorkspaceRoute(route)`; there is no side-panel-internal navigation to library/detail (the panel has no router).
- Note the side panel is not given the video-page URL of an *item* (the `url` field of `LibraryItem` is not read by the panel); the page's own URL is known from the active tab: `tab.url` / `session.page.canonical_url` (`useCollect.ts:216-218`, `usePageQa.ts:244-247`).

---

## 5. Page Q&A no-caption UI

- Where the no-caption line is built: `captionLine(session)` (`PageWorkspace.tsx:26-38`). For `caption.status === "unavailable"`: `block ? {tone:"action", text: block} : {tone:"action", text:"该视频没有字幕，提问将仅基于标题和简介回答"}` (`:35-37`; quoted string at `:37`). Rendered as `<div class="sp-status" data-tone="action"><span class="sp-status__dot"/><p role="status">{text}</p></div>` (`:87-92`). Tests rely on that exact string (`SidePanelCollect.test.tsx:187, 201, 205`, `SidePanel.test.tsx:146-153`).
- `questionBlock(page)` (`usePageQa.ts:105-110`): captions available -> null; platform not youtube/bilibili -> `该视频没有可用字幕，暂时无法提问` (`:107`); else if no title/description/chapters -> `该视频没有字幕，也没有可用的标题和简介，暂时无法提问` (`:109`); else null (a metadata-only question is allowed). When non-null the composer is hidden (`PageWorkspace.tsx:145`) and suggestions hidden (`:126`).
- Bilibili extra hint under the status when captions are unavailable: `BILIBILI_NO_CAPTIONS_HINT` (`copy.ts:48`; render `PageWorkspace.tsx:93-95`).
- Per-turn "no caption" step text: `qaSteps` -> `该视频没有字幕，使用标题和简介` (`steps.ts:215-218`).
- The 「收藏视频」 button **already sits right below** the status (`PageWorkspace.tsx:100-107`), inside the same `<section class="sp-video">` (`:84-123`), followed by `连接这台设备…` toggle (`:108-121`) and the privacy sentence `提问时字幕只用于生成回答…点「收藏视频」才会把视频保存到资料库。` (`:122`, class `.sp-privacy`). Free-text hint styles available: `.sp-hint` (`sidepanel.css:29`), `.sp-privacy` (`:33`), `.sp-status[data-tone]` (`:41-46`).
- What triggers collect: `controller.collect` (`PageWorkspace.tsx:104`) = `usePageQa.collect` (`usePageQa.ts:363`) -> `useCollect.collect` (`useCollect.ts:158-263`). For a caption-less page `captions = null` -> `fallback = "no_captions"` (`:176, 189`) -> **URL import** (`:216-262`) -> `deps.submitBatch({urls:[url]})` (`:237`) -> follow item via polling.
- Platform gating that exists in the client: `URL_IMPORT_PLATFORMS = {youtube, bilibili}` (`useCollect.ts:22`); `importUrl` returns null for any other platform (`:69-73`) -> outcome `unsupported_url` -> step `未能收藏：暂不支持直接导入这个视频` (`useCollect.ts:232-235`, `steps.ts:88`). Bilibili part > 1 -> `unsupported_part` (`useCollect.ts:219, 228-231`, `steps.ts:89`). `PLATFORM_LABEL` lists `youtube, ntu_kaltura, bilibili` (`PageWorkspace.tsx:13`); `Platform` type `protocol.ts:2`.
- Server-side ASR platform sets (for reference): `_ASR_PLATFORMS = {"youtube","bilibili"}` (`app/ingest/tasks.py:347`), `_ASR_CAPTURE_PLATFORMS = {"youtube"}` (`:351`, mirrored `app/web/library.py:60`). The client's `URL_IMPORT_PLATFORMS` equals `_ASR_PLATFORMS` today; the two lists are separate constants.
- **No ASR on/off signal reaches the client**: `CapabilitiesResponse` has `archive, browser_companion, chat, max_save_batch_size, page_qa?, save_enabled, summary_generation, supported_platforms, transcript_pagination, web_login_channels` (`ext/api/schema.d.ts:656-700`) - no ASR flag. The panel reads only `page_qa` (`SidePanel.tsx:56-57`; `deps.ts:32`).

---

## 6. Types and contracts; handling of unknown enum values

### Contracts
- `LibraryItem = Schemas["LibraryItemResponse"]`, `LibraryLifecycle = LibraryItem["lifecycle"]`, `LibraryItemSummary = Pick<LibraryItem, public_id|lifecycle|title|author|cover_url|duration_sec|saved_at|why_saved|url|error_code|available_actions>` (`ext/api/contracts.ts:30-46`).
- `LibraryItemResponse` (`ext/api/schema.d.ts:1071-1127`): `author, available_actions: string[], chapters, cover_url, description, duration_sec, error_code: string|null, kind: string, lang, latest_dispatch_public_id, lifecycle: "archived"|"ready"|"needs_action"|"failed"|"processing"|"queued", platform: string, public_id, published_at, saved_at, stage?: "queued"|"fetching"|"transcribing"|"chunking"|"embedding"|"ready"|"needs_action"|"failed"|"archived", stage_updated_at?: string|null, summary?: string|null, tags: string[], text_source: string, title, url, why_saved`.
  - `stage`, `stage_updated_at`, `summary` are optional (`?`) in the generated type; all others are required.
  - There is **no** field today for per-stage status, body-searchable flag, or category.
- `ItemStage = NonNullable<LibraryItem["stage"]>` (`collectModel.ts:4`) so it tracks the generated union; `WorkingStage` is a separate hand-written union (`collectModel.ts:5`); `EtaStage` another (`eta.ts:6`).
- Generation chain: `app/api/*_schemas.py` -> `scripts/export_web_openapi.py` -> `ext/api/openapi.json` -> `openapi-typescript` -> `ext/api/schema.d.ts` (`extension/workspace/openapi.mjs:21-26`; `package.json:19-20` scripts `check:api` / `generate:api`; spec `.trellis/spec/frontend/type-safety.md`: "Editing `schema.d.ts` directly" is forbidden, "Regenerate `openapi.json` and `schema.d.ts` together and require `pnpm check:api` in CI"). CI runs `pnpm check:api` (`.github/workflows/extension-api-contract.yml:46-49`). `generate:api` needs the project `.venv` (`workspace/openapi.mjs:8-15`).

### Runtime parsing: nothing rejects unknown values
- `requestJson` returns `response.json() as Promise<T>` with no validation (`ext/api/client.ts:114-115`); `getLibraryItem` / `listLibraryItems` just return it (`:803-814`). The spec states the client keeps no response schema (`.trellis/spec/frontend/type-safety.md`, "Validation": "The client does not maintain a second handwritten Zod response schema."). So extra keys (e.g. a new `stages` array) and unknown enum strings pass through untouched.
- What each consumer does with an **unknown `lifecycle`** (new value):
  - `lifecycleCopy[item.lifecycle].label` throws `TypeError` at render in `VideoCard.tsx:32` and `VideoDetailView.tsx:154` (no fallback; the repo has no error boundary, grep for `ErrorBoundary` finds none).
  - `isLibraryWorkItem` is false -> it lands in `readableItems` (`LibraryPage.tsx:110`).
  - `outcomeFor` default branch returns `null` (`collectModel.ts:133-141`) -> the side panel keeps polling until the 30-minute deadline, then shows `timeout` (`useCollect.ts:133-136`).
- What each consumer does with an **unknown `stage`** (new value, known `lifecycle`):
  - `applyItem` stores it as-is (`collectModel.ts:145-153`); `isWorkingStage` is false so `workingStage` keeps the last known working stage (`:155-158`); the step list is built from `workingStage` (`steps.ts:97-107`), so the panel keeps showing the previous step; `collectProgress` maps a non-working stage to `"queued"` for the ETA (`steps.ts:161`).
  - **`parseStored` does not reject the entry**: `STAGES` is a whitelist (`collectModel.ts:178`) and an unknown stored stage becomes `stage: null` while the rest of the entry is kept (`:205`; test `collectModel.test.ts:99-103` "drops unknown stage, fallback and working-stage values instead of trusting them"). `parseStored` rebuilds the entry from named keys only, so any **new persisted field** would be dropped on restore unless added there (`:195-210`).
  - `STAGES` (`:178`) lists exactly the 9 current values; adding a stage value without updating it just maps stored value -> null.
- Unknown **error_code**: `FAILURE_REASON[code] ?? "处理出错"` (`steps.ts:66`; test `steps.test.ts:136-139`).
- Unknown **batch `status`**: side panel default branch -> `submit_failed` with `safe_error_code ?? status` (`useCollect.ts:254-255`); `AddVideosDialog` uses `resultCopy[item.status] ?? "未能确认处理结果，请稍后查看资料库"` (`ext/library/AddVideosDialog.tsx:295`) - the one existing "unknown enum falls back" example in the UI.
- Unknown **`available_actions`** entries are harmless (`Set`/`includes` checks only: `VideoDetailView.tsx:96`, `VideoCard.tsx:22`, `collectModel.ts:151`).
- Test fixtures are typed to `LibraryItem`: `ext/test/sidepanelEnvironment.ts:86-117` (`libraryItem()` factory) and `ext/library/LibraryPage.test.tsx:11-31` (`readyItem` has no `stage`). A **required** new response field would fail `pnpm typecheck` (which includes `tsconfig.test.json`, `package.json:15`) until these fixtures are updated; an **optional** field (like `stage?`) would not.
- Server side the lifecycle filter is a closed enum (`library_routes.py:183-185`) and `Lifecycle`/`Stage` are `Literal` types (`app/web/library.py:41-56`).

---

## 7. Prototype vs current

Prototype file: `.trellis/tasks/10-18-stashseek-ingest-status/reference/prototype.html` (69 lines; CSS on line 3-4, screens in `content()` switch, lines 25-37). It is a throwaway low-fidelity prototype ("Throwaway PRD prototype", line 8); each screen is rendered in a `.frame` 420 px wide (`.frame{width:420px;...}` line 3).

### 7.1 Screen `?screen=processing` (P01b) - `prototype.html:32` (content) / `:16` (top card)
Markup, in order:
1. `current('processing')` top card: `<div class="current">` with `<div class="eyebrow">当前视频 · YOUTUBE</div>`, `<h3>如何从访谈中找到真实需求</h3>`, `<div class="muted">研究笔记 · 18:42</div>`, a `.row` holding `<span class="tag">已保存 · 正在整理</span>` and a quiet button `写收藏理由`, plus a hidden `#reason` field "为什么收藏（可选）" (`:16`).
2. `<div class="eyebrow">内容整理</div>`
3. `<h2>已经保存，不必等在这里</h2>`
4. `<p class="muted">现在可以继续看视频，关闭插件也不影响处理。</p>`
5. Three `<div class="selection">` rows: `✓ 保存视频链接`, `◌ 解析字幕与内容`, `○ 分类、主题与对象整理` (third has `muted` class).
6. `<div class="notebar">演示状态控制</div>` + demo buttons `模拟整理完成` (-> `toast('已整理，可检索')` then detail screen) and `模拟解析失败` -> failed screen. (Demo controls only.)
- Completion cue: toast `已整理，可检索` (`.toast`, fixed bottom-center, `background:#253328;color:#fff;padding:10px 18px;border-radius:8px;font-size:13px;max-width:90%`, `role="status"`, removed after 3.5 s; `prototype.html:3` css and `:63` function). After that the prototype navigates to the detail page.

### 7.2 Screen `?screen=movieprocessing` (D02) - `prototype.html:26`
Rendered in the desktop scene (`desktopWrap`, `:53`) with the panel on the right (`.desktopcols{grid-template-columns:1fr 420px}`). Panel body = `filmCurrent()` card (`:50`: `.current` with `.eyebrow 正在观看 · B站 / 当前分P`, `<h3>` title, `<p class="muted">电影手记 · 电影解说 · 示例</p>`, `.actions` button `已收藏 · 查看分析`) then:
- `<h2>已保存，AI正在辅助整理</h2>`
- `<p class="muted">继续看视频即可，不必等在这里。</p>`
- `.selection` rows: `✓ 保存原视频`, `◌ 读取可用字幕与内容`, `○ 分类主题 → 内容分析 → 电影识别` (last muted; one row listing three future stages)
- demo `notebar` + buttons `模拟分析完成`, `模拟识别有歧义`, `模拟解析失败`.

### 7.3 Screen `?screen=failed` (P01c) - `prototype.html:33`
1. `<button class="quiet">← 视频库</button>` (back)
2. `<h2>收藏还在，整理没有完成</h2>`
3. `<div class="current"><h3>如何从访谈中找到真实需求</h3><span class="muted">YouTube · 链接已保存</span></div>` (title card, no eyebrow)
4. `<div class="status error">本次未能获取可用字幕，正文暂不可检索。</div>` (the reason row; the PRD says this wording is only an example)
5. `<p class="muted">可以按标题和备注查找，也可以稍后重试。</p>`
6. `<div class="actions">` with `<button class="primary">重新整理</button>` and `<button>仅查标题 / 备注</button>` (second one sets `state.query='访谈'` and navigates to the results screen).

Related (not in scope tasks but context): P01 home `video()` list items use `.item`, `.tag`; P02a no-results has the button `查看未整理项` -> failed screen (`:35`); P04 detail screen (`:36`) has an `AI整理 · 内容要点` eyebrow with a paragraph and `.chip`s, and `你的收藏理由` section.

### 7.4 Prototype CSS classes used by those screens (`prototype.html:3`)
- Tokens: `--ink:#232927; --muted:#68716c; --line:#d8ded7; --paper:#f5f6f1; --lime:#dff38e; --soft:#eef2e9`.
- `.body{padding:18px 20px}`; `.eyebrow{font-size:11px;letter-spacing:1.2px;color:var(--muted);font-weight:650}`; `h2{font-size:21px;line-height:1.4;margin:12px 0}`; `h3{font-size:15px}`; `.muted{color:var(--muted);font-size:12px}`.
- `.current{background:var(--soft);padding:14px;border-radius:8px;margin-bottom:18px}` (card).
- `.selection{padding:14px 0;border-bottom:1px solid var(--line)}` (step row; the state is a text glyph, no icon/dot element).
- `.status{font-size:12px;background:var(--soft);padding:10px 12px;border-radius:6px;margin:10px 0}`, `.status.warn{background:#faf2df;color:#865b24}`, `.status.error{background:#faeee9;color:#974d3b}`.
- `.actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}`, `.actions button{font-size:12px}`; `button{border:1px solid var(--line);border-radius:7px;padding:7px 12px;background:#fff}`; `.primary{background:var(--lime);border-color:#c3d77c;font-weight:600}`; `.quiet{border:0;background:transparent;padding:5px;color:var(--muted)}`.
- `.tag{background:var(--soft);font-size:11px;padding:2px 6px;border-radius:3px}`.

### 7.5 Existing counterpart in the extension

| Prototype element | Closest existing | Status |
|---|---|---|
| `.eyebrow` small caps label | `.eyebrow` (`ext/styles.css:37`); panel override `.sp-shell .eyebrow` (`sidepanel.css:18`) | exists (accent-dark, uppercase, 0.16em tracking) |
| `h2` big title in panel body | `.sp-shell h1` 1.2rem (`sidepanel.css:17`); global `h1,h2` Georgia serif (`styles.css:31-33`); `.sp-empty h1` 1.05rem (`sidepanel.css:31`) | no `h2` rule in side panel; panel body copy today is `.sp-hint`/`.sp-status` |
| `.muted` explanation paragraph | `.sp-hint` (`sidepanel.css:29`), `.muted` (`styles.css:38`) | exists |
| `.current` card (title + meta) | `.sp-video` bordered card (`sidepanel.css:38-40`: padding .95rem, 1px border, radius .9rem .9rem .9rem .25rem, white 46% bg); library `.video-card`, `.state-card` (`styles.css:517, 549`) | similar card styling exists; no title+`平台 · 链接已保存` card variant |
| `.selection` step rows with ✓ ◌ ○ | `chat-execution-steps li.is-{running,completed,failed,skipped}` dot markers (`styles.css:754-770`): running = lime pulsing dot, completed = filled green, failed = filled accent, skipped = dashed outline | partial: `TimelineStep.outcome` has **no "not started" value** (`StepTimeline.tsx:3`), `collectSteps` never emits future steps (`steps.ts:98-107`); default `li > span` is a hollow green-bordered circle (`styles.css:756`) |
| Title `已经保存，不必等在这里` + `现在可以继续看视频，关闭插件也不影响处理。` | none; the collect block's only heading is the `<summary>` line `收藏视频 · <step>` (`steps.ts:141-154`) | missing |
| `已整理，可检索` | closest: step `完成，可以在资料库里搜索和提问` (`steps.ts:114`) and result `已收藏到资料库。` (`steps.ts:185`) | different wording; no toast component exists in the extension (grep: no toast/`role=status` toast; the panel uses inline `<p role="status">`) |
| `.status.error` reason row | `.inline-error` (`styles.css:185`: 3px left accent border + tinted bg, .9rem); `.sp-status[data-tone="error"]` dot+text (`sidepanel.css:41-45`); `.sp-collect__result[data-tone]` (`:68-70`, tones success/action only); `.library-notice--error` (`styles.css:415-418`) | several error-ish rows exist; none is a pill/row with `正文暂不可检索` |
| `.status.warn` | `.sp-status` default tone = yellow dot (`sidepanel.css:43`), `[data-tone="action"]` color `#7a5a14` (`:70`) | partial |
| Buttons 重新整理 (primary) / 仅查标题 / 备注 (secondary) | `.button--primary` (accent red fill), `.button--quiet` (ink outline), `.button--ghost`, `.text-button` (`styles.css:46-54`); side panel uses `.sp-link` (underlined text button, `sidepanel.css:27`) for secondary links; detail page uses `.button.button--quiet` for 重新整理 (`VideoDetailView.tsx:163`) | exist as classes; side panel has no `.actions` row of two buttons |
| `← 视频库` back control | `RouteLink.back-link` "← 返回资料库" on detail (`VideoDetailPage.tsx:62`); none in side panel | side panel has no navigation |
| `.tag` badge `已保存 · 正在整理` | `.status-pill` (`styles.css:541`), `.collection-tag` (`:534`) | pill exists in workspace only |
| Palette | extension: `--lime:#cfff1a`, `--paper:#fbf8f1`, `--accent:#bb4b2f`, `--green:#315e53` (`styles.css:8-16`); buttons pill-shaped (`border-radius:999px`, `styles.css:46`) | prototype's palette/radii differ from the extension's (prototype is low-fidelity) |

### 7.6 Width
- No fixed width is declared for the panel; the Chrome side-panel width drives layout. `body{min-width:0}` in the panel (`sidepanel.css:2`) overrides the global `min-width:320px` (`styles.css:22-23`). `sidepanel.css` has no width media queries (only `prefers-reduced-motion`, `:89-91`). Layout uses `display:grid`/`flex` column with `gap` and `overflow-wrap:anywhere` on h1 (`:17`).
- Unit tests render under jsdom and do not load CSS (`vitest.config.ts:4-6`; test files import only components), so "works at ~420 px" cannot be asserted by the existing test setup other than via markup/class assertions.
- The workspace library/detail pages use responsive desktop layouts (`.detail-layout{max-width:920px}` `styles.css:590`; `@media` breakpoints at `:799-812, :823`).

---

## 8. Tooling and existing tests

### `extension/package.json` scripts (`package.json:8-21`)
| Script | Command |
|---|---|
| `test` | `vitest run` |
| `test:coverage` | `vitest run --coverage` (coverage `include: ["src/page-capture.ts"]` only, thresholds 80/72/85/90: `vitest.config.ts:7-17`) |
| `build` | `node scripts/clean.mjs && tsc -p tsconfig.json && vite build --config vite.config.ts && node scripts/copy-static.mjs production` |
| `build:local` | same with `copy-static.mjs local` |
| `typecheck` | `tsc -p tsconfig.json --noEmit && tsc -p tsconfig.test.json --noEmit` |
| `lint` | `eslint src --max-warnings 0` |
| `audit` / `package` | `node scripts/audit-package.mjs production` / `pnpm build && pnpm run audit` |
| `check:api` / `generate:api` | `node workspace/openapi.mjs check|generate` |
- Package manager `pnpm@11.16.0` (`package.json:7`). CI line: `pnpm typecheck && pnpm lint && pnpm test && pnpm build && pnpm run audit` (`.github/workflows/extension-api-contract.yml:51`), preceded by `pnpm check:api` (`:48`).
- Test stack: vitest 4.1.10 + jsdom 30 + `@testing-library/react` 16.3.2 + `@testing-library/user-event` 14.6.3 + `@testing-library/jest-dom` (`package.json:22-39`); `vitest.config.ts:4-5` `environment: "jsdom"`, `setupFiles: ./src/test/setup.ts` (cleanup, `matchMedia` stub, `HTMLDialogElement` stub: `src/test/setup.ts:1-26`). `tsconfig.test.json` adds `vitest/globals` types and includes all `src/**` (`tsconfig.test.json:1-10`). React 19.2.8, TanStack Query 5.101.4, react-router 8.3.0.

### Test files that cover the touched components (all colocated `*.test.ts(x)`)
| Area | Test file | What it covers (selected) |
|---|---|---|
| Collect model | `ext/sidepanel/collectModel.test.ts` (110 l.) | `applyItem` stage/time/outcomes, legacy server without `stage` (`:65-72`), persisted round trip, unknown stage dropped (`:99-103`), `finishEntry` |
| Steps/copy | `ext/sidepanel/steps.test.ts` (285 l.) | step lists per path, needs_action copy (`:115-121`), failure reason + retry hint (`:123-134`), unknown code fallback (`:136-139`), archived/timeout, summary/result/workspace path, `collectProgress`, `qaSteps` |
| Whole panel collect flow | `ext/sidepanel/SidePanelCollect.test.tsx` (777 l.) | capture path through stages, URL-import fallbacks incl. caption-less ETA (`:187-201`) and transcription shown once reported (`:203-217`), results of URL import, how an import ends (needs_action `:420-435`, failed with `asr_no_speech` `:437-452`, item missing, 401), polling/backoff/30-min cap/visibility, resume after reopen (`:580-642`), thread order, Bilibili cases |
| Panel shell / Q&A | `ext/sidepanel/SidePanel.test.tsx` (505 l.) | startup/login, page states incl. caption-less YouTube/Kaltura/Bilibili hints (`:136-153, 189-197`), `opens the workspace from the panel` (`:232`), Q&A, citations |
| Polling | `ext/sidepanel/itemPoller.test.ts` (119 l.) | interval, backoff, deadline, hidden, no overlap, stop |
| ETA | `ext/sidepanel/eta.test.ts` (118 l.) | per-stage seconds, paths, overrun, formatting |
| Deps | `ext/sidepanel/deps.test.ts` (163 l.) | chrome tab adapter, `openUrl`, session store, `workspaceUrl` (`:153`), poll constants (`:158`) |
| Timeline component | `ext/chat/StepTimeline.test.tsx` (112 l.) | outcome classes per `li`, named list, markup unchanged without detail (`:25-31`), detail under text, open/close, `useAutoCollapse`. `StepTimeline` is also used by the workspace chat via `ext/chat/ExecutionTimeline.tsx:2, 71`, so its markup is shared. |
| Library | `ext/library/LibraryPage.test.tsx` (264 l.), `VideoCard.test.tsx` (116 l.), `lifecycle.test.ts` (26 l.), `LibraryStates.test.tsx`, `collections.test.ts`, `AddVideosDialog.test.tsx` | work-zone split and progress (`LibraryPage.test.tsx:98-133`), collection filter, search suggestions, lifecycle labels (`lifecycle.test.ts:6-13`), failed-card hint (`VideoCard.test.tsx:92-101`) |
| Detail | `ext/videos/VideoDetailView.test.tsx` (321 l.) | title density, chapters, transcript, summary shown only when present (`:140-155`), retry button only with `retry` action (`:157-183`), transcript/action error states, why-saved editing |
| Test helpers | `ext/test/sidepanelEnvironment.ts` | `makeEnvironment()` fake `PanelDeps`, `libraryItem()` typed fixture (`:86-117`), `batchResponse()`, `memoryCollectStore()`, `noCaptionPage()`, `workspaceUrl` stub (`:215`) |
- There is no test file for `PageWorkspace.tsx`, `QaThread.tsx`, `useCollect.ts` or `usePageQa.ts` on their own; they are exercised through `SidePanel.test.tsx` and `SidePanelCollect.test.tsx`. No test covers `VideoDetailPage.tsx` (grep of `ext/videos/` shows only `VideoDetailView.test.tsx`). No test file imports a CSS file.

### Related specs
- `.trellis/spec/frontend/type-safety.md` - generated types, "never duplicate lifecycle ... unions by hand", regenerate `openapi.json` + `schema.d.ts` together.
- `.trellis/spec/frontend/state-management.md` - local vs URL vs server state; "URL state: current route and public item ID. Owned by React Router" (line 17); no global store.
- `.trellis/spec/frontend/index.md` - "All frontend specifications are written in English. User-facing copy remains concise Chinese." (The index text still describes a `web/` directory; the current code lives in `extension/`.)
- Related tasks that touch the same copy: `.trellis/tasks/10-16-extension-asr-copy/prd.md:7-12` (moves/changes the `needs_asr` copy in `sidepanel/copy.ts` and `library/lifecycle.ts`), `.trellis/tasks/10-17-bilibili-side-panel/` (Bilibili caption reading).

---

## Caveats / not found

- No per-stage status, searchable-body flag, category or key-points field exists in the extension's types or UI today; they are only anticipated in the PRD.
- I did not verify server behavior beyond the quoted lines (`app/web/library.py`, `app/api/library_routes.py`, `app/ingest/tasks.py`). In particular, whether the batch endpoint re-dispatches an existing failed item on `already_exists` was not checked.
- Whether `workspace.html#/library?search=x` parses `search` is inferred from the existing `?pairing=` usage under `HashRouter` (`App.tsx:36`, `BrowserCompanionPage.tsx:26`) and was not run.
- The panel width actually used by Chrome (the PRD says ~420 px) is not set anywhere in the extension code; it was not measured.
- `ext/styles.css` was read selectively (grep for the classes above), not in full.
