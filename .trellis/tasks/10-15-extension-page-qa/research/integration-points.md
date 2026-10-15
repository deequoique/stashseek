# Research: Integration points for side-panel page Q&A and the web companion build

- **Query**: How do capture, chat transport, the answer pipeline, persistence, limits, build targets and model config fit a no-ingest side-panel Q&A and a web SPA build of `extension/src`?
- **Scope**: internal (working tree on `dev` with large uncommitted changes, read as-is). No tests or builds were run.
- **Date**: 2026-10-15

Sibling child tasks already exist: `.trellis/tasks/10-15-side-panel-page-qa/prd.md` and `.trellis/tasks/10-15-web-companion-build/prd.md`. Where this file disagrees with them, the reason is noted inline.

---

## 1. Capture reuse (`extension/src/page-capture.ts`, `worker.ts`, `protocol.ts`)

### How capture is invoked today
- The popup sends `chrome.runtime.sendMessage({type: "CAPTURE"})` (`extension/src/popup.ts:50,79`).
- `worker.ts:143-160` dispatches by `Command.type`, a closed union: `STATUS | START_PAIRING | FINISH_PAIRING | START_LOGIN | FINISH_LOGIN | OPEN_WORKSPACE | CAPTURE | LIBRARY | DISCONNECT` (`worker.ts:13`).
- `capture()` (`worker.ts:85-111`) does these steps in order: `chrome.tabs.query({active, currentWindow})` → `captureActivePage(tab.id, tab.url)` (`:90`) → `captureRequest(page)` (`:91`) → POST to `/api/v1/browser-companion/extension/captures` with `Authorization: Bearer <captureToken>` and an `Idempotency-Key` (`:98-102`). Reading the page and submitting it happen in the same function. Nothing currently returns the cues without submitting them.
- `captureActivePage(tabId, url)` (`page-capture.ts:246-294`):
  - Picks an adapter by host. YouTube watch, shorts and youtu.be URLs use `captureYouTubePage`. The three NTU hosts use `captureKalturaPage`.
  - Runs `chrome.scripting.executeScript({target: {tabId, allFrames}, world: "MAIN", func: injection})` (`:266-270`).
  - Sets `allFrames = kalturaHost` (`:263`) and falls back to the top frame only (`:276-280`).
  - Results are normalised and aggregated by `aggregateAttempts` (`:229-244`), which keeps the best capture via `selectBestCapture` (`:199-204`).
  - The function performs no network submission. It is pure page reading plus validation (`isPageCapture`, `:82-147`).
- Both adapters are documented as "self-contained for MV3" (`:296`, `:991`) because Chrome serialises `func`. Today `worker.js` imports the **tsc-emitted, unbundled** `page-capture.js`. `vite.config.ts` only bundles `workspace.html`.

### What it returns (`protocol.ts:1-20`)
```ts
PageCapture = { platform: "youtube"|"ntu_kaltura"; platform_id; canonical_url; page_url;
  metadata: { title, author, duration_sec, language, description, cover_url, tags[], chapters[{title,start_sec,end_sec}] };
  caption: {status:"available"; source:"official_cc"|"auto_caption"; language; cues: {start_sec,end_sec,text}[]}
         | {status:"unavailable"; source:null; language:null; cues:[]} }
```
- `captureRequest()` (`protocol.ts:31-39`) adds `protocol_version: "capture.v1"`, `client_version: "0.1.3"` and `content_hash` (a SHA-256 of `start\tend\ttext` lines, `:27-29`). The server recomputes the hash.
- **Metadata actually populated**:
  - YouTube `baseCapture` sets `description: null, tags: [], chapters: []` on every path. Only `title`, `author`, `duration_sec`, `language` and `cover_url` are filled (`page-capture.ts:559-565`).
  - Kaltura `makeCapture` sets `author: null, description: null, cover_url: null, tags: [], chapters: []` (`:1474-1484`).
  - The Kaltura **no-caption** result has `title: null, duration_sec: null` (`:1487-1498`).
  - So the "no captions → answer from title/description/chapters" path has only title, author and duration on YouTube, and **nothing** on Kaltura, unless the adapters are extended. Reading YouTube `videoDetails.shortDescription` or chapter data is not implemented anywhere.
- Auto-captions are supported. `isAuto` checks `kind === "asr"` or a `vssId` starting with `a.` (`:651`), and the result is labelled `auto_caption` (`:700`).
- Client-side bounds mirror the server: at most 50,000 cues, at most 1,000,000 total cue chars, description ≤ 50,000, chapters ≤ 1,000, tags ≤ 100 (`:101-130`).

### Getting cues without submitting
- **Possible with existing code.** `captureActivePage` is already decoupled from submission. A side panel is an extension page and can call `chrome.scripting.executeScript` directly, because the manifest has `scripting` (`manifest.json:7`). There are two options:
  - (a) import `captureActivePage` into the side-panel bundle;
  - (b) add a worker command such as `READ_PAGE` that returns `PageCapture` instead of POSTing it.
- Caveat for (a), **not verified**: vite/esbuild bundling and minification of the injected `func` must keep it self-contained. Today only the tsc output is injected.
- Saving later with already-read cues has two options:
  - POST `captureRequest(page)` directly to `/api/v1/browser-companion/extension/captures` using `captureToken` from `chrome.storage.local` (`worker.ts:11,80`) plus an `Idempotency-Key`. The worker already has pending-key reuse logic at `:92-96`.
  - Add a worker command that accepts a `PageCapture`. The existing `CAPTURE` re-reads the page.

### Playback time and seeking
- **Nothing exists.** `grep` for `currentTime`, `seekTo` and `sidePanel` across `extension/src` non-test files returns no hits.
- Available building blocks:
  - `executeScript` in the MAIN world. `page-capture.ts:472,495` already touches `movie_player` / `#movie_player` (only for `getPlayerResponse`).
  - The Kaltura video lives in a cross-origin `cdnapisec.kaltura.com` frame (`page-capture.ts:1026-1031`; README "NTULearn 的视频播放器可能位于 Kaltura 官方跨域 frame 中"). Reading or seeking it therefore needs `allFrames: true` (or `frameIds`) and the existing `https://cdnapisec.kaltura.com/*` host permission (`manifest.json:14`).
- Kaltura citation URLs do not carry a timestamp: `timestamp_url` drops the query for `ntu_kaltura` (`app/browser_capture.py:263-266`). Seeking must use `start_sec` through a script, not the URL.
- `extension/src/chrome.d.ts:1-12` is a minimal hand-written typing. It declares `executeScript` with only `target {tabId, allFrames}`, `world: "MAIN"` and `func`. It has no `args`, `frameIds`, `sidePanel`, `tabs.onUpdated` or `tabs.onActivated`. There is no `@types/chrome` in `extension/package.json`.

---

## 2. Chat path (extension → server)

### Client (`extension/src/api/client.ts`, `chat/ChatPage.tsx`)
- Routes:
  - New thread: `POST /api/v1/conversations/{conversationId}/reset` (`client.ts:725-729`). `conversationId` is a client UUID (`ChatPage.tsx:170,214`).
  - Send (streaming): `POST /api/v1/conversations/{conversationId}/messages/stream` with body `{message_id, text}` and header `Accept: text/event-stream` (`client.ts:457-486`).
  - JSON fallback: `POST .../messages` (`client.ts:260-268`). It is used only on `StreamingUnavailableError`, i.e. 404/406/415 or `streaming_disabled` (`:494-503`; `ChatPage.tsx:345-353`).
- SSE event types are a closed client set (`client.ts:277-290`): `started, activity, step_started, step_completed, plan_updated, section_started, text_delta, section_completed, section_aborted, completed, error, cancelled`.
  - Unknown top-level keys are rejected (`STREAM_EVENT_KEYS`, `:312-332,369`).
  - The client enforces strict sequence and lifecycle checks (`:542-689`).
  - Citation objects need only `title`, `excerpt`, `url` (strings) and an optional `start_sec` (`:424-431`).
  - `text_delta` without any section lifecycle is legal for a whole-answer path (`:637-639`).
- After the stream completes, `ChatPage` **refetches the persisted turn** (`/conversations/{thread}/turns`) and shows a sync error if it is not durable (`ChatPage.tsx:366-385`, `hasDurablePendingTurn`). A Q&A turn that is not persisted cannot reuse `ChatPage` unchanged.
- Citation rendering is `<a href={citation.url} target="_blank">` (`ChatPage.tsx:141-158`). There is no seek hook.

### Server
- Schemas live in `app/api/conversation_routes.py`:
  - `MessageInput {message_id ≤128, text ≤16,000}` (`:51,89-93`)
  - `ConversationCitationResponse {title, excerpt, url, start_sec?}` (`:120-128`)
  - `ConversationResponse` (`:146-159`)
  - `ConversationStreamEvent` with closed `type` / `activity` / `step_code` and lifecycle validators (`:186-330`)
  - `_encode_stream_event` (`:396`)
- The stream route `send_message_stream` (`:941-1526`) wraps exactly one `channel.handle_stream(envelope)` call. The internal-to-public event mapping and sequence numbering are written **inline inside the route closure** (`events()`), not as a reusable function. The lifecycle log line contains only ids, types and counts (`:1079-1088`).
- **CSRF header requirement:** `send_message`, `send_message_stream`, `reset` (and delete/link routes) declare `_csrf_token: CsrfHeader`, a *required* `X-CSRF-Token` header (`:76-79,880,915,958,1540,1569,1608`; OpenAPI `schema.d.ts:1792` marks it required).
  - The extension client only sets that header from the `__Host-kb_csrf` cookie (`client.ts:82-83,467-468`). That cookie is not visible to a `chrome-extension://` page.
  - Static reading says extension Bearer calls to these routes would get a 422 `validation_error`. **Not runtime-verified**, and no test covers a valid extension-session Bearer on conversation routes (`tests/test_web_api_app.py:160` only covers an invalid one).

### Auth on these routes (`app/api/app.py`)
- Extension session Bearer:
  - Issued by `POST /api/v1/extension/auth/verify` (`extension_routes.py:148-150`) and stored as `accessToken` (`worker.ts:80`; `client.ts:177-180`).
  - The middleware resolves it for any non-extension-prefix path (`app.py:363-375`).
  - For unsafe methods, a Bearer skips the cookie, Origin and CSRF check (`app.py:403-420`).
  - `authenticated_session` and `authenticated_scope` honour `request.state.extension_user_id` (`app.py:545-600`).
- The token index is **in-process memory**: a dict keyed by hash with expiry pruning (`extension_routes.py:1-7,50-124`). Tokens are lost on restart and not shared across workers.
- The capture Bearer (`captureToken`) is accepted only by `/api/v1/browser-companion/extension/captures` and `/extension/grant` (`browser_companion_routes.py:215-256`). By spec it must never authenticate normal routes (`.trellis/spec/backend/web-browser-runtime.md:122-126`). Page Q&A should therefore use the extension session Bearer.
- Origin gating:
  - `/api/v1/browser-companion/extension/*` and `/api/v1/extension/*` require an allowed `chrome-extension://` Origin (`app.py:340-394`).
  - Any Bearer request coming from a `chrome-extension://` Origin is also checked against `BROWSER_COMPANION_ALLOWED_ORIGINS` (`app.py:343-348,399-402`).
  - CORS headers are emitted only for those requests (`app.py:455-463`).

---

## 3. Server answer pipeline and the seam for a caller-supplied transcript

### Current entry chain
1. Route → `ChannelService.handle_stream` (`app/channels/service.py:99-132`), which holds a per-conversation asyncio lock.
2. → `_handle_locked_stream` (`:134-328`), which resolves or registers identity, gets or creates the thread, loads history (`:254-259`) and builds `AgentRequest`.
3. → `KnowledgeAgent.stream` (`app/agent/orchestrator.py:394+`).
4. → primary PydanticAI agent (`agent_builder.py:54-110`), then `AnswerPipeline.stream_answer` (`answer_pipeline.py:905-1100`).
5. → persistence via `save_completed_turn` (`service.py:309-327`).

- The primary agent instructions **force `search_segments` over the tenant library** for any content question (`agent_builder.py:14-24`), so the normal path cannot answer from a supplied transcript.
- `build_knowledge_agent` (`app/bootstrap.py:82-128`) shows how the model is built: `build_model(settings)` (`provider.py:100-128`), with `stream_model` set only if `model_supports_streaming` (`provider.py:82-97`).

### Reusable tool-free pieces (no retrieval, no DB)
- `build_stream_plan(model)` (`answer_pipeline.py:239-325`): structured `AnswerStreamPlan`, output-validated against `ComposerDeps.citations` (`runtime_state.py:245-261`).
  - Its instructions render **all** allow-listed citations, each excerpt capped at 1200 chars (`:256-261`, `_render_composer_evidence` `:588-613`, `COMPOSER_EVIDENCE_EXCERPT_CHARS = 1200` at `runtime_state.py:32`). This is the step where "transcript in LLM context" would occur.
- `build_section_streamer(model)` (`:328-342`) streams text for one locked section, seeing only that section's citations (`:871-903`).
- `_StreamingTextGuard` (`:713-796`) and `validate_natural_answer` (`answer_validation.py`) reject URLs, HTML, malformed `[S…]` markers and "来源/sources" blocks in model text.
- `ResponseEnvelope.grounded(...).project()` (`response.py:282-290,331-344`) renders the final text. It appends `[S<segment_id>]` markers and a `来源：` block containing each URL plus an excerpt of up to 180 chars (`response.py:175-197`).
- `composer_model_settings` (`provider.py:45-59`) disables thinking for DeepSeek via `extra_body`.
- Limits:
  - ≤ 8 cited segments per answer: `COMPRESSED_EVIDENCE_LIMIT = 8` (`runtime_state.py:33`)
  - ≤ 5 items: `MAX_SOURCE_ITEMS` (`:31`)
  - ≤ 8 sections (`types.py:174,237`)
  - stream event `citation_ids` max 8 (`conversation_routes.py:242`)

### Seam options (described as found; no recommendation implied beyond fit)
- **A. Standalone service plus a new route.** This bypasses `ChannelService` and `KnowledgeAgent`. It reuses `build_model`, `composer_model_settings`, `build_stream_plan`, `build_section_streamer`, `_StreamingTextGuard`, `validate_natural_answer` and the public `ConversationStreamEvent` / `ConversationCitationResponse` / `_encode_stream_event`.
  - Paragraphs become synthetic `Citation(item_id=<constant>, segment_id=<1-based paragraph index>, title, excerpt=paragraph text, url=timestamp_url(platform, canonical_url, start), start_sec)`. `Citation` requires integer `item_id` and `segment_id` (`types.py:41-59`); the url helper is `browser_capture.py:254-267`.
  - Nothing touches the DB, because persistence lives only in `ChannelService` (`service.py:309-327,492-516`).
  - The inline SSE mapping in `send_message_stream` would need to be duplicated or factored out.
- **B. Call `AnswerPipeline.stream_answer(request, deps, diagnostics)` directly** (`answer_pipeline.py:905`).
  - It needs an `AgentRequest` with `tenant`, `thread_db_id` and `thread_public_id` (`types.py:262-284`).
  - It needs an `AgentDeps` whose `services: KnowledgeServices` and `actions: AgentActionRuntime` are required (`runtime_state.py`, `class AgentDeps`). It reads `deps.actions.read_action_results`, `deps.successful_searches`, `deps.pending_read_failures`, `deps.read_recovery_exhausted` and `deps.media_evidence`.
  - With zero citations it returns `answer_unavailable` (`:918-928`), so a metadata-only answer needs another path.
  - If no streaming model is available it raises `ProviderStreamingUnavailable` (`:930-931`); the orchestrator normally handles that with the composer fallback (`build_composer`, `:488`).
- **C. Normal conversation path.** Ruled out for the no-ingest goal:
  - the primary agent searches the library;
  - `MessageInput.text` is capped at 16,000 chars;
  - it persists turns (see §4).
- Composer, plan and section prompts are worded for a "private knowledge base / videos / segment" (`answer_pipeline.py:109-138`). They can be reused, but the wording assumes the library.
- No-captions answer: the SSE contract already allows a whole-answer `text_delta` with no sections, then `completed` with `citations: []` (`client.ts:637-639`; the route's own compatibility path at `conversation_routes.py:1139-1150`). `ResponseEnvelope.canonical(...)` (`response.py:304-314`) produces a citation-free answer.

### Citation contract fit for a "page source"
- The public projection is `{title, excerpt, url, start_sec}` with no item id (`conversation_routes.py:120-128,590-607`). A page citation fits as-is: title = page title, excerpt = paragraph text, url = timestamp URL, start_sec.
- The internal `Citation` has no `platform` / `platform_id` field; it requires integer `item_id` / `segment_id`. Platform identity lives only in the `url`, which `_citation_matches_scope` parses (`runtime_state.py:68-79`).
- `MediaCitation` is unrelated (`types.py:62-99`).

### Chunking and lexical utilities
- `app/ingest/chunker.py::chunk(cues, *, lang, chapters=None, semantic_embedder=None)` (`:260-280`) is pure, in-memory and deterministic when `semantic_embedder=None`.
  - Bounds: en 80–200 words, zh 130–330 CJK chars (`:23-24`); a 120 s hard ceiling (`:25`); 15% whole-cue overlap (`:26`).
  - These constants are module-level and not parameters. **It does not produce ~30 s paragraphs.**
  - Ingest uses it without an embedder as `preflight_chunks` (`app/ingest/tasks.py:360-364`). For long tracks it disables semantic cuts above 512 cues (`MAX_SEMANTIC_BOUNDARY_CUES`, `tasks.py:57,416-425`).
- Input type is `app.connectors.base.Cue(start, end, text)` (`connectors/base.py:15-18`). Capture cues convert via `normalized_cues(caption)` (`browser_capture.py:203-204`).
- **Chapter key mismatch:** `chunk()` reads `chapter["start_time"]` / `chapter.get("end_time")` (`chunker.py:269`), the yt-dlp shape. Capture chapters use `start_sec` / `end_sec` (`browser_capture.py:66-69,249`). Passing capture chapters would raise `KeyError`.
- **No in-process BM25.** `app/retrieval/search.py::bm25_search` (`:190-223`) is Postgres `ts_rank_cd` / `websearch_to_tsquery` for English and `pg_trgm similarity` for Chinese over `Segment` rows. There is no `rank_bm25` or `jieba` dependency (`pyproject.toml:8-26`; `opencc` is present). `evals/meeting_gold/retrieval_study.py` also wraps the Postgres path.
- No tokenizer or token counter exists. The only estimate is `len(json)//3` (`app/channels/conversations.py:184-186`).

---

## 4. Conversation persistence: where cue text could land

Tables: `conversation_thread` and `conversation_turn` (`app/models.py:416-447`). Turn columns are `user_text`, `assistant_text`, `sources` (JSONB), `model_messages` (JSONB), `answer_status`, `error_code`, `action_results` (JSONB). `pending_channel_action` is used only for save/delete flows.

If a page-Q&A turn went through `ChannelService`:

| # | Location | What would hold cue/paragraph text | Anchor |
|---|---|---|---|
| 1 | `conversation_turn.user_text` | `envelope.text`, i.e. the whole message if cues were packed into it | `conversations.py:169` |
| 2 | `conversation_turn.assistant_text` | Rendered `来源：` block: URL plus excerpt ≤180 chars per cited paragraph | `response.py:189-196`; `service.py:317,505` |
| 3 | `conversation_turn.sources` | Full `Citation.model_dump`, **full excerpt** (library path uses `excerpt=segment.text`, `agent/services.py:70`) | `service.py:318-320,506-509`; `conversations.py:171` |
| 4 | `conversation_turn.model_messages` | Canonical history = question plus the visible final answer, so it includes the source excerpts | `answer_pipeline.py:665-672`; `conversations.py:163-172` |
| 5 | Later turns (replay) | `load_message_history` re-sends #4 to the LLM; `ContextBuilder` re-projects `sources` excerpts as `recent_sources` | `conversations.py:108-144`; `context.py:306-339`; `agent_builder.py:86-108` |
| 6 | History list API | `title = user_text[:80]`, `preview = assistant_text[:160]` | `conversation_routes.py:670-683` |
| 7 | Diagnostics (production) | Content-free: closed enums and counts only; `stage_usage` is token counts | `diagnostics.py:313-390`; `orchestrator.py:937-945` |
| 8 | Diagnostics (development only) | `retrieval_detail` logs `excerpt` / `query` for the four library tool names when `STASHSEEK_LOG_RETRIEVAL_CONTENT=true`; `exception_message` / `provider_response_body` on failures | `diagnostics.py:287-311,368-377` |
| 9 | HTTP layer | Access logs off (`cli.py:136`, `mcp_server.py:1502`); Caddy `log { output discard }` (`deploy/caddy/notebook-agent.caddy`); validation errors return generic 422 without echoing the body (`app.py:493-503`); 500s log only the exception type (`app.py:441-447`); stream lifecycle log has ids and counts only (`conversation_routes.py:1079-1088`) | — |
| 10 | Third party | The LLM provider receives the transcript paragraphs in prompts (inherent to the design) | `answer_pipeline.py:256-261,880-889` |

A path that never calls `ChannelService` / `save_completed_turn` (seam A) avoids rows 1–6. Rows 7–9 carry no content in production. Row 8 applies only in development with the flag set, and only for those tool names.

---

## 5. Request limits and validation

- `RequestBodyLimitMiddleware` (`app/api/app.py:223-287`): every path is limited to `MAX_WEB_REQUEST_BODY_BYTES = 64 * 1024` (`:45`) **except** exactly `/api/v1/browser-companion/extension/captures`, which uses `settings.browser_companion_max_request_bytes` (`:244-248,324-334`). A new page-QA path carrying cues hits the 64 KiB cap unless the middleware is extended.
- `BROWSER_COMPANION_MAX_REQUEST_BYTES` defaults to 5,500,000 (`app/config.py:735-739`) and must be positive (`:900-922`).
- `BrowserCaptureRequest` (`app/browser_capture.py:129-161`):
  - `protocol_version: Literal["capture.v1"]`, `content_hash` (64 hex), plus platform, URL and cover validation.
  - `CaptureCaption.cues` ≤ `MAX_CAPTURE_CUES = 50,000`, ordered, total text ≤ `MAX_CAPTURE_TEXT_CHARS = 1,000,000` (`:20-21,103-126`).
  - `CaptureCue.text` 1–10,000 chars, finite `end ≥ start` (`:50-63`).
  - `CaptureMetadata`: title/author ≤1,000, description ≤ 50,000, tags ≤100×200, chapters ≤1,000 (`:81-100`).
  - The `CaptureCaption` and `CaptureMetadata` sub-models are standalone pydantic models (`extra="forbid"`) and can be reused for a page-QA body without `content_hash`.
- The capture submission service additionally applies `ingest_max_raw_transcript_bytes`, `ingest_max_cues_per_item` and `ingest_max_text_chars_per_item` (`app/api/runtime.py:88-101`).
- The extension-prefix middleware rejects any `Content-Encoding` other than identity (`app.py:386-390`).
- Chat has no per-user rate limit; there is only the per-conversation asyncio lock (`service.py:68,88,117`).

---

## 6. Build targets, side panel, and the web SPA

### Current extension build
- `pnpm build` = `clean` → `tsc -p tsconfig.json` → `vite build` → `copy-static.mjs production` (`extension/package.json:8`).
  - `tsc` emits every `src/**/*.ts(x)` except tests as unbundled ES modules into `dist/` (`tsconfig.json` `outDir: dist`, `rootDir: src`). This produces `worker.js`, `popup.js`, `page-capture.js`, `api-client.js`, and also unused React `.js` files.
  - `vite.config.ts:5-9` has `rollupOptions.input: "workspace.html"`, `outDir: "dist"`, `emptyOutDir: false`. It outputs `dist/workspace.html` plus hashed `dist/assets/*`.
  - `extension/workspace.html` contains `<script type="module" src="/src/main.tsx">`.
  - `extension/index.html` exists but has **no script tag**. It is only used by `src/app/indexHtml.test.ts:9-15` for the favicon check, and `public/favicon.svg` is the only public asset.
- `copy-static.mjs:9-18` copies `popup.html` and `popup.css`, and rewrites `manifest.host_permissions` to the target API origin (`build-targets.mjs:1-19`: production `https://notebookai.deequoique.tech`, local `http://127.0.0.1:8000`).
- `audit-package.mjs:10-33` asserts:
  - permissions are **exactly** `["activeTab","scripting","storage"]` (`:10,21`);
  - hosts are exactly the six media hosts plus the API host (`:11-22`);
  - there is no `content_security_policy` or `externally_connectable` (`:23`);
  - no `.map` or `test` files (`:26`);
  - `api-client.js` contains the selected origin (`:28-29`);
  - no `sourceMappingURL`, `<script`, `eval(` or `new Function(` in any JS (`:30-33`).

### What a side panel needs (from the code above)
- `manifest.json:7` add `"sidePanel"` and a `side_panel.default_path`. `audit-package.mjs:10` `expectedPermissions` must change in step, or the audit throws.
- Add a vite input for the side-panel HTML (`vite.config.ts:6`, single input today). Alternatively copy a static HTML via `copy-static.mjs:9`.
- `manifest.json:18` has `action.default_popup`. Whether a popup coexists with `sidePanel.setPanelBehavior({openPanelOnActionClick})` is Chrome behaviour and was **not verified here** (no web search tool was available).
- `worker.ts:146` currently opens `workspace.html#/chat` in a new tab.
- `chrome.d.ts` lacks `sidePanel` and tab event typings (see §1).

### Web SPA build and serving
- Server-side SPA hosting still exists: `_mount_spa(app, directory)` (`app/api/app.py:758-771`).
  - It requires `<dir>/index.html` (`:760-762`).
  - It mounts `<dir>/assets` at `/assets` (`:763-765`).
  - It returns `index.html` for every non-`api/` path (`:767-771`). Root files such as `/favicon.svg` therefore get `index.html`.
  - It is invoked only when `create_app(static_dir=...)` is non-None (`:643-644`).
- **Production composition always passes `static_dir=None`** (`app/api/runtime.py:165-173`). The `mount_static` parameter (`:57`) is unused. `app/web_api.py:69` is also None.
  - At HEAD it was `static_dir=settings.web_static_dir if serve_static else None`.
  - `web_static_dir` defaults to `"extension/dist"` with the comment "Retained for config-file compatibility; API-only runtime never reads it" (`config.py:698-701`). `web_serve_static` defaults to True (`:695-697`).
  - `.env.example:293-294` sets `WEB_SERVE_STATIC=false`, `WEB_STATIC_DIR=`.
  - `extension/dist` has `workspace.html`, not `index.html`, so pointing `_mount_spa` at it would raise.
- Production process: `notebook-agent.service` runs `app.cli mcp-server` on port 8800 (`deploy/systemd/notebook-agent.service`). `app/web_runtime.py:11-38` composes MCP plus `build_web_app` (an alias of `build_api_app`, `runtime.py:176-177`). Caddy proxies everything to 8800.
- The working-tree diff of that unit **removed** these lines, which were present at HEAD:
  - `WEB_PUBLIC_ORIGIN=https://notebookai.deequoique.tech`
  - `WEB_SERVE_STATIC=true`
  - `WEB_STATIC_DIR=web/dist`
  - `WEB_COOKIE_SECURE`
  - `WEB_FORWARDED_ALLOW_IPS`
- The HEAD deploy script ran `corepack pnpm install/build` in `web/`; the working tree removed it (`git diff deploy/scripts/notebook-agent-deploy`, 4 deleted lines around `:45`).
- Responses get CSP `default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' https://i.ytimg.com https://img.youtube.com https://ntulearnvideo.ntu.edu.sg data:; connect-src 'self'; frame-ancestors 'none'` (`app.py:464-470`). There are no `style={` usages in non-test `extension/src`. `/assets/*` gets `Cache-Control: public, max-age=31536000, immutable` (`app.py:475-476`).

### Extension zip download
- `BROWSER_COMPANION_DOWNLOAD_URL = "/assets/stashseek-chat-browser-companion-production-0.1.3.zip"` (`extension/src/app/browserCompanion.ts:1-2`). It is used by `ShowcasePage.tsx:287`, `LoginPage.tsx:198` and `BrowserCompanionPage.tsx:74`.
- At HEAD the zips were **committed files** in `web/public/assets/` (both `stashseek-chat-…` and `notebook-agent-…` production and local variants), which vite copied into `web/dist/assets/`. These are deleted in the working tree.
- **No script in the repo produces a zip**. `grep` for `.zip` / `make_archive` / `zipfile` finds only an unrelated wheel extraction in `deploy/scripts/bootstrap-production-langbot:66`.
- Inside the extension, that absolute `/assets/...zip` link resolves to `chrome-extension://<id>/assets/…`, which does not exist in `extension/dist`.
- Version `0.1.3` is duplicated in `manifest.json:4`, `package.json:3`, `protocol.ts:3` and `browserCompanion.ts:2`.

### Router and client on an http(s) origin
- `App.tsx` uses `HashRouter` (`:194`), which works under `_mount_spa`'s catch-all.
- `requestJson` (`client.ts:73-115`): when `location.protocol !== "chrome-extension:"` it uses relative paths, `credentials: "same-origin"`, and `X-CSRF-Token` from `__Host-kb_csrf` on unsafe methods (`:81-91`). `streamConversationMessage` also skips the chrome branch when `chrome.storage` is absent (`:471-475`). The data calls therefore already work in cookie+CSRF mode.
- **But the auth calls are extension-only** and would fail on a web origin:
  - `requestEmailChallenge` → `POST /api/v1/extension/auth/challenge` (`client.ts:150-158`)
  - `verifyEmailChallenge` → `POST /api/v1/extension/auth/verify` (`:162-182`). It stores the token only on `chrome-extension:`, and no cookie is set.
  - `getSession` → `GET /api/v1/extension/auth/session` (`:184-188`). This is Bearer-only (`extension_routes.py:152-157`).
  - `logout` → `POST /api/v1/extension/auth/logout` on web (`:190-202`).
  - The middleware treats any `/api/v1/extension/*` request as an extension request and returns 403 `extension_origin_invalid` unless the Origin is an allowed `chrome-extension://` (`app.py:340-342,376-385`). `ProtectedLayout` would loop to `/login` (`App.tsx:106-122`).
  - This contradicts the sibling web PRD's "client.ts:82-95 already uses cookie + CSRF" claim for auth. It is true only for non-auth data calls.
- Cookie web-auth routes **still exist** when `WEB_AUTH_ENABLED=true` (`app/api/email_auth_routes.py:125-300`, mounted at `app.py:532-540`):
  - `POST /api/v1/auth/challenges {email}`
  - `POST /api/v1/auth/verify {email, code}`, which sets `__Host-kb_session` and `__Host-kb_csrf` (`:306-324`)
  - `GET /api/v1/auth/session`
  - `DELETE /api/v1/auth/session`, which needs CSRF
  - At HEAD the old `web/src/api/client.ts` used exactly these routes (`git show HEAD:web/src/api/client.ts:145-172`). The spec also names them as the production browser login (`web-browser-runtime.md:14-25`).
- `expected_origin` for the cookie router and `_validate_protected_mutation` is `settings.web_public_origin`, falling back to the first allowed extension origin (`runtime.py:108-111`). A web SPA needs `WEB_PUBLIC_ORIGIN` set to its https origin. Both `same_origin` (`email_auth_routes.py:153-156`) and CSRF mutations (`app.py:727-730`) compare the Origin exactly.
- Capabilities report `web_login_channels=("email",)` when email is enabled (`runtime.py:112`).

---

## 7. Agent model and limits

- `AGENT_MODEL` defaults to `openai:gpt-5-mini` (`app/config.py:493-496`; `.env.example:54`). Related settings: `AGENT_BASE_URL` (OpenAI-compatible gateway) and `AGENT_API_KEY` (`config.py:497-498`).
- `build_model` (`provider.py:100-128`) supports `deepseek:` and OpenAI-compatible base URLs, and applies a DeepSeek profile for `deepseek-*` names.
- The production model is **not in the repo**; it comes from `/etc/notebook-agent/notebook-agent.env`. Task docs record `deepseek-v4-flash` (`.trellis/tasks/09-04-agent-evaluator-release-gate/research/benchmark-results-2026-10-08.md:13`; `.trellis/tasks/archive/2026-10/10-07-retrieval-agent-budget/prd.md:23`). The context window is **unverified**; the sibling PRD states "128K-class".
- Limits:
  - `AGENT_OUTPUT_TOKEN_LIMIT` 3000 (`config.py:513-515`)
  - `AGENT_COMPOSER_MAX_TOKENS` 1000 (`:516-518`), which must be ≤ output limit × `COMPOSER_VALIDATION_REQUEST_LIMIT=1` (`:49,1098-1103`)
  - `AGENT_TIMEOUT_SECONDS` 45 (`:499-501`)
  - `AGENT_REQUEST_LIMIT` 8 (`:507`)
  - `AGENT_TOOL_CALLS_LIMIT` 10 (`:510`)
  - `AGENT_STREAMING_ENABLED` true (`:522-524`)
  - `CONTEXT_MAX_TURNS` 8 and `CONTEXT_TOKEN_BUDGET` 6000, for history only (`:609-614`)
- The stream plan and section calls use `UsageLimits(request_limit=1, output_tokens_limit=agent_composer_max_tokens)` (`answer_pipeline.py:895-898,946-949`). **No input-token limit** exists anywhere (`grep input_tokens_limit` finds nothing).
- The route transport timeout is `2 × agent_timeout_seconds + 10` (`conversation_routes.py:82-86`).

---

## Related specs
- `.trellis/spec/backend/web-browser-runtime.md`: email cookie auth contract, transport isolation (capture Bearer never authenticates normal routes), and the conversation streaming contract.
- `.trellis/spec/backend/agent-execution-streaming.md`: internal stream events.
- `.trellis/spec/backend/browser-companion-capture.md`: capture.v1 contract.
- `.trellis/spec/backend/logging-guidelines.md`: privacy-safe diagnostics.
- `.trellis/spec/backend/ingestion-chunking-embedding.md`: chunker. This file is untracked/new.

## Caveats / not verified
- Whether extension-Bearer calls to `/conversations/*` actually fail on the required `X-CSRF-Token` header was inferred from code and the OpenAPI schema. It was not executed.
- Chrome `sidePanel` and popup coexistence, the `tab.url` visibility rules without the `tabs` permission, and `executeScript` `args` / `frameIds` behaviour are Chrome platform facts that were not checked against docs (no web tool was available).
- Whether vite-bundled injection functions stay self-contained was not tested.
- The production LLM model and its context window are taken from task docs only.
