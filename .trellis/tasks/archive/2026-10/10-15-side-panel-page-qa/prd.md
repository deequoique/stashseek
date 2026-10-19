# Side panel instant Q&A over the current video

Parent: `10-15-extension-page-qa`. Builds on commit `c9a4c0e` (extension becomes
the main UI).

## Goal

Clicking the extension icon opens a side panel next to the video. The user asks
a question about the YouTube or NTULearn/Kaltura video in that tab and gets a
streamed answer with clickable timestamps within seconds, without first saving,
chunking, or embedding the video. The question and answer are kept in the user's
conversation history. The video can then be saved to the library with one click,
reusing the captions already read.

## Decisions (user, 2026-10-15)

- **D1 Entry:** the side panel replaces the popup. The toolbar icon opens the
  panel, and login, device connection, save, and Q&A all live there.
- **D2 History:** a page-Q&A turn is persisted with the question, the answer,
  and citations whose excerpts are short (≤180 chars). Caption cues and the
  transcript are never persisted.
- **D3 Compliance boundary:** user-initiated only. Reads happen in the user's
  browser, results stay in the user's own tenant, and no audio or video upload
  happens. See the parent PRD.

## Requirements

### R0 Extension session works on conversation routes

- The conversation routes declare a required `X-CSRF-Token` header
  (`app/api/conversation_routes.py:76-79,915,958`), and a `chrome-extension://`
  page cannot read the CSRF cookie. Requests authenticated by an extension
  session Bearer must pass without that header. Cookie requests still require
  it.
- The extension's history and chat views need this, and page Q&A reuses the
  same history routes.

### R1 Side panel shell

- The manifest gets `sidePanel` and `side_panel.default_path`, and
  `action.default_popup` is removed. The worker sets
  `openPanelOnActionClick: true`.
- The panel hosts the current popup flows: login, device connection, save, and
  "open workspace". It also hosts the Q&A view.
- The panel tracks the active tab, including YouTube SPA navigation. It shows the
  current video's title and caption status before any question. On an
  unsupported page it shows a clear "打开 YouTube 或 NTULearn 视频后提问" state.

### R2 Instant Q&A without ingestion

- The first question reads captions through the existing adapters
  (`captureActivePage`, `extension/src/page-capture.ts:246-294`) without
  submitting a capture.
- It POSTs these to a new page-Q&A stream route:
  - the question;
  - the cues;
  - public metadata;
  - the current playback position.
- The server answers from the supplied transcript in LLM context, with no
  library retrieval and no tools.
- The response streams over the existing public stream event contract.
- Citations use the existing public shape `{title, excerpt, url, start_sec}`.
  Clicking one seeks the player in that tab. Kaltura requires the player frame
  (`allFrames`), because `timestamp_url` drops time for `ntu_kaltura`.
- Follow-up questions in the same panel session reuse the cues held in panel
  memory. The video changes when the tab's `platform_id` changes. The panel then
  starts a new thread and re-reads captions.

### R3 Long transcripts

- Merge cues into ~30 s paragraphs, each carrying one start timestamp.
- **Small transcripts:** if the estimated tokens are at or below
  `PAGE_QA_FULL_CONTEXT_TOKENS` (configurable, conservative default because the
  production model's window is unverified), the whole transcript goes into
  context.
- **Large transcripts:** otherwise the context is built from:
  - paragraphs within ±5 min of the playback position;
  - the top paragraphs from an in-process BM25 (no new dependency, no Postgres);
  - if the question and transcript differ in language, one LLM keyword rewrite
    into the transcript language first.
- All of this happens in memory.

### R4 No captions

- YouTube metadata adds `description` (from `videoDetails.shortDescription`) and,
  best-effort, chapters. Neither is read today (`page-capture.ts:559-565`).
- With no captions, YouTube answers from title, description, and chapters, and
  the answer carries a "该视频没有字幕，以下仅基于标题和简介" label.
- Kaltura has no metadata in that case (`page-capture.ts:1487-1498`). The panel
  says Q&A is unavailable and asks nothing of the server.

### R5 Save after Q&A

- "保存到资料库" submits the cues already read through the existing capture.v1
  route. It uses the capture Bearer, the same idempotency-key reuse, and the same
  quota. Nothing is re-read unless the video changed.

### R6 Privacy, security, limits

- Cues reach the LLM provider only; that is inherent. They never reach:
  - database rows;
  - object storage;
  - broker messages;
  - diagnostics;
  - logs, including development retrieval-content logging.
- The page-Q&A route authenticates with the extension session Bearer, or the web
  cookie with CSRF. The `capture:write` Bearer is rejected.
- The route has its own body ceiling, exempt from the 64 KiB default
  (`app/api/app.py:45,244-248`) and bounded like capture.v1. It reuses
  `CaptureCaption` and `CaptureMetadata` validation
  (`app/browser_capture.py:50-126`), and has a per-user rate limit.
- Transcript text is untrusted. The page-Q&A model call has no tools, the system
  prompt fences the transcript as data, and output passes the existing
  natural-answer validation (no URLs or HTML in model text).


### R7 YouTube captions need the player's proof-of-origin token (found in Chrome smoke, 2026-10-17)

- Caption `baseUrl`s from the player response carry no `pot`. Fetching them
  returns HTTP 200 with an **empty body** in every format (JSON3, VTT, XML), so
  the panel showed `caption_parse_failed` on every YouTube video. This was
  verified on `aircAruvnKk`: the plain URL returned 0 bytes. The player's own
  request (with `pot`/`potc` plus client params) returned 304 events, and the
  same `pot` also worked for another language track.
- The adapter must get a `pot`-bearing timedtext URL **for the current video
  id**, in this order:
  1. from the page's existing resource entries;
  2. otherwise by briefly enabling captions through the player API, observing
     the request, and restoring the user's previous caption state (this works
     while paused).

  It then applies `pot`, `potc` and the client params to the chosen track and
  reads JSON3.
- `pot` and every signed query value stay inside the page. They are never
  returned, submitted or logged.
- An unstarted player that issues no caption request maps to a retryable
  `caption_player_not_ready`, with copy asking the user to play the video for a
  few seconds and retry.

### R8 「收藏视频」 button (user request, 2026-10-17)

- The panel shows a primary 「收藏视频」 action on every supported video page.
  It saves the video to the user's library independently of Q&A. It does not
  require the user to ask a question first, and it reuses captions already read
  when available.
- **Decision (user, 2026-10-17): capture first, then fall back to server URL
  import.**
  - When captions are readable in the browser, save through capture.v1
    (`SAVE_CAPTURE`). This is fast and also covers subtitles that need login.
  - When the read fails, the video has no captions, or no capture device is
    paired, submit the canonical video URL through the existing library batch
    import (`submitVideoBatch`, extension session Bearer). The server connector
    then fetches the video, with ASR when enabled.
  - Result copy covers four cases: queued, already in the library, needs speech
    transcription, and failed with a reason. It also links to the item in the
    workspace.
- 「收藏视频」 replaces 「保存到资料库」 as the primary action at the top of the
  page view.
- **The fallback is automatic** (user clarification, 2026-10-17). One click on
  「收藏视频」 is the whole user action. The panel decides the path by itself:
  capture.v1 or server URL import, plus reading captions if they were not read
  yet. It never asks the user to open or paste a link.

### R9 Import progress with "what the agent is doing" and an ETA (user request, 2026-10-17)

- **Presentation** (user clarification, 2026-10-17): progress appears **inside the
  conversation thread** as a tool-call-style block, like Claude's tool use. It
  is not a separate card area.
  - Clicking 「收藏视频」 appends a 「收藏视频」 action entry to the panel's thread.
  - The entry uses the same visual language as the workspace
    `chat/ExecutionTimeline.tsx`: a running spinner on the current step, a
    check on finished steps, failed and skipped states, and the ability to
    collapse once done.
  - Steps:
    - 已提交 → 获取视频信息与字幕 (URL path) or 已读取页面字幕 (capture path)
    - → 语音转写中 (only when ASR runs)
    - → 切分段落
    - → 生成检索向量
    - → 完成，可以在资料库里搜索和提问
  - Under the current step the entry shows elapsed time and
    「预计还需约 N 分钟」, updated live until a terminal state: ready, needs
    action, or failed.
- **Q&A turns use the same block.** Before the streamed answer, a short
  timeline shows the client-side steps:
  - 读取视频字幕 (N 段, language);
  - 播放位置 mm:ss;
  - 生成回答 (running until the first delta, then done).

  No server change is needed for these steps.
- The public `lifecycle` (queued/processing/...) is too coarse for this. The
  library item projection gains a read-only, closed `stage` field derived from
  the internal ingest state (fetching, transcribing, chunking, embedding, ...),
  plus the time the current stage started. No lifecycle enum change.
  - Coordinate with `10-16-extension-asr-copy`, which wanted an ASR hint on the
    same projection. `stage = transcribing` covers it, so only one field is
    added.
- The ETA is a heuristic on the client. It uses video duration, the path
  (captions vs ASR) and the current stage, with conservative per-stage rates
  kept in one table so they can be calibrated later.
  - When it cannot estimate, it shows elapsed time only and never makes up a
    precise number.
  - When a stage overruns its estimate, the copy changes to 「比预计久，仍在处理」.
- Polling uses the existing item read with the extension session. Every 2–3 s
  while the card is visible it fetches; it stops at a terminal state or when
  the panel closes. Reopening the panel on the same video resumes the card for
  an unfinished item.

## Acceptance criteria

Closed 2026-10-19; see 「收尾记录」 at the end of this file. A box is ticked only
where code or tests prove it. Live checks need Chrome, and lane only tested the
extension's subtitle summary, so they stay open.

- [x] Clicking the icon opens the side panel. There is no popup. Login, connect,
      and save work from the panel.
  - Manifest: `extension/manifest.json` has `side_panel.default_path` and no
    `default_popup`. `sidepanel/manifest.test.ts` 「declares the side panel and
    no popup」; `audit-package.mjs` asserts it.
  - `worker.test.ts` asserts `setPanelBehavior({openPanelOnActionClick: true})`
    on install and startup.
  - `SidePanel.test.tsx` 「side panel startup and login」;
    `SidePanelCollect.test.tsx` 「connects the device from the panel and then
    saves through the capture path」.
- [ ] On a captioned YouTube video of 20 min or less, the first `text_delta`
      arrives in ≤ 5 s locally (measured, model call included). No
      `content_item`, `segment`, object-store, or broker write happens.
  - **Timing not verified.** The PR #32 body says ≈1–2 s, but lane says only the
    subtitle summary was tested, so that figure is a self-report that was not
    independently confirmed.
  - The no-write half holds by construction. `app/page_qa/` imports only
    `ConversationThread` and `ConversationTurn` from `app.models`, and has no
    ingest, storage or queue dependency. `tests/test_page_qa_api.py`
    `test_end_to_end_turn_is_persisted_in_history_and_leaks_no_cue_text` runs the
    real service and persistence.
- [ ] Clicking a cited timestamp seeks the YouTube player (and the Kaltura
      player) to within 1 s.
  - **Not verified on a real player.**
  - The `SEEK` command is unit-tested. `page-capture.test.ts` 「seeks YouTube
    through movie_player.seekTo with seek-ahead」, 「finds the Kaltura player
    frame first and then seeks only that frame」; `worker.test.ts` seek cases;
    `SidePanel.test.tsx` 「side panel citations」.
  - Kaltura/NTULearn: lane did not test it (the implement.md step 8 marks it as
    a user check).
- [x] After SPA navigation to another video, the next question is answered about
      the new video in a new thread.
  - `SidePanel.test.tsx` 「starts a new thread and re-reads after SPA navigation
    to another video」, 「keeps the thread when the user leaves for another tab
    and comes back to the same video」; `pageIdentity.test.ts`.
  - Logic-level proof only. It was not observed in a live tab.
- [x] Above the full-context threshold, an answer is still produced and cites
      paragraphs near the playback position when the question refers to "刚才".
  - `tests/test_page_qa_transcript.py` 「window_mode_keeps_playback_window_and_bm25_hits_ordered_by_time」
    and the budget tests.
  - `tests/test_page_qa_service.py`
    `test_large_transcripts_use_a_window_plus_bm25_with_keyword_rewrite` and
    `test_rewrite_failure_falls_back_to_the_original_question`.
  - A real >2 h video was not tried.
- [x] A caption-less YouTube video gets a labelled metadata-only answer. A
      caption-less Kaltura video shows the unavailable state, with no request.
  - `tests/test_page_qa_service.py`
    `test_no_captions_answers_from_metadata_with_the_fixed_label`.
  - `SidePanel.test.tsx` 「blocks questions on a caption-less Kaltura video
    without calling the server」, 「allows a metadata-only question on a
    caption-less YouTube video」.
- [x] The Q&A turn appears in conversation history (extension and web) with the
      question, the answer, and citations. Stored excerpts are ≤180 chars. Tests
      assert that no cue text beyond those excerpts is in `conversation_turn`
      and that no log record contains cue text.
  - `tests/test_page_qa_api.py`
    `test_end_to_end_turn_is_persisted_in_history_and_leaks_no_cue_text`.
  - `tests/test_page_qa_service.py`
    `test_turn_is_saved_with_question_answer_and_short_excerpts_only`,
    `test_cue_text_never_reaches_logs_or_any_persisted_column_except_short_excerpts`,
    `test_failure_and_rewrite_error_paths_log_only_closed_fields`.
  - `chat/ChatPage.test.tsx` 「shows a selected page thread read-only with a way
    back to the video」, 「badges page threads in the history list…」.
  - The log grep during a live smoke (implement.md step 8) was not recorded.
- [x] Save after Q&A creates exactly one capture and reaches `ready`. Repeating
      it returns the existing result.
  - `SidePanelCollect.test.tsx` 「saves the page that was already read, follows
    the item through its stages, and ends ready」, 「tells a repeated save of a
    video that is already ready」.
  - Server-side idempotency of capture.v1 is existing behaviour, covered by the
    `Idempotency-Key` cases in `tests/test_browser_companion_api.py` (part of the
    CI backend contract tests).
  - A live save-then-repeat was not recorded.
- [x] An extension Bearer request to the existing conversation routes succeeds
      without `X-CSRF-Token`. A cookie request without it still fails.
  - `tests/test_conversation_extension_transport.py`
    `test_extension_bearer_needs_no_csrf_header`,
    `test_cookie_without_csrf_header_is_rejected_by_the_app_boundary`,
    `test_cookie_with_wrong_csrf_header_is_rejected`,
    `test_cookie_with_matching_csrf_header_succeeds`,
    `test_csrf_header_is_documented_but_optional_on_conversation_routes`.
- [x] Contract tests cover the page-Q&A route:
  - oversized body: `test_oversized_body_is_rejected_with_the_page_qa_ceiling_not_the_default`,
    `test_chunked_oversized_body_is_rejected_before_parsing`;
  - unknown fields: the parametrized `test_invalid_bodies_are_rejected_with_the_safe_validation_error`
    (`extra_field`, `content_hash`, and more);
  - a capture Bearer (rejected): `test_capture_bearer_is_rejected_and_never_reaches_the_service`;
  - a missing or invalid extension Origin:
    `test_disallowed_extension_origin_is_forbidden_even_with_a_valid_session`,
    `test_cookie_session_without_an_origin_is_rejected_even_with_a_matching_csrf_token`,
    `test_bearer_without_an_origin_still_needs_a_valid_extension_session`;
  - an unsupported platform: the same parametrized test (`platform="vimeo"`);
  - the rate limit: `test_per_user_rate_limit_returns_429_before_any_work`,
    `test_validation_failures_do_not_consume_the_rate_limit`.
  - All in `tests/test_page_qa_api.py`. These backend tests are not in the CI
    workflow; they rest on the PR #32 body (1521 passed, 6 failures equal to the
    `main` baseline) and were not re-run in this closing pass.
- [x] Extension checks pass: unit tests, typecheck, lint, both builds, and both
      audits (with `sidePanel` in the expected permissions).
  - The `Extension and API contract` workflow passed on main at `9daf552`
    (typecheck, lint, vitest, production build and audit).
  - The local build and audit rest on the PR #32 body, not on CI.

## Acceptance criteria (added 2026-10-17)

- [ ] On a captioned YouTube video, the panel reads captions with the player's `pot`. Verified in Chrome on at least two videos, and the user's caption on/off state is unchanged afterwards.
  - **Not verified live.** The unit tests below prove the logic only.
- [x] Unit tests cover:
  - reusing an existing `pot` entry, and ignoring entries for another video id:
    `page-capture.test.ts` 「reuses a matching pot request already in the
    resource timeline without touching the captions」 (the entries include
    another video's `pot`);
  - the toggle-and-restore path: 「restores the viewer's original caption track
    when captions were on」, 「fails with caption_player_not_ready when the
    player never requests captions, and still restores them」;
  - `pot` absent from every returned capture or error: 「consumes a signed VTT
    locally and never returns the signed URL」, 「ignores untrusted caption
    resources and keeps secrets out of captures」, 「survives the MV3 function
    serialization boundary on the pot path」.
- [x] 「收藏视频」 saves the current video from the panel without asking a question first.
  - With readable captions it uses capture.v1.
  - On a read failure or a caption-less video it falls back to URL import, and the item appears in the library.
  - Tests cover both paths and the 「already in the library」 result.
  - `SidePanelCollect.test.tsx`: 「收藏视频 on the capture path」, 「收藏视频 falls
    back to the server URL import by itself」, 「URL import results」 (「links to
    an item that is already in the library」). The item reaching the library on
    a real server was not recorded.
- [x] The fallback needs no extra user action beyond one click.
  - `SidePanelCollect.test.tsx` 「imports by URL when the page cannot be read,
    with no other user action」, 「imports by URL when the video has no
    captions…」.
- [x] While importing, a tool-call-style 「收藏视频」 block in the thread shows the current step, completed steps, elapsed time and an ETA.
  - `chat/StepTimeline.test.tsx`, `sidepanel/steps.test.ts`,
    `SidePanelCollect.test.tsx` 「the thread」.
- [x] Q&A turns show the client-side steps above the answer.
  - It updates until ready, needs action, or failed.
  - Server tests cover the `stage` projection for each ingest state.
  - Extension tests cover the step mapping, the ETA heuristic (including the overrun copy), and polling start/stop.
  - Q&A steps: `SidePanel.test.tsx` 「shows the client-side steps above the
    answer and folds them away once the answer is done」, 「keeps 生成回答
    running until the first text arrives…」.
  - Server `stage`: `tests/test_web_library.py::test_stage_projects_every_ingest_state`,
    `test_stage_never_changes_the_public_lifecycle`;
    `tests/test_library_extension_transport.py::test_extension_bearer_reads_an_item_with_its_processing_stage`;
    `tests/test_web_library_postgres.py` (Postgres-backed).
  - Step mapping, ETA and polling: `sidepanel/steps.test.ts`,
    `sidepanel/eta.test.ts` 「reports an overrun once a stage exceeds 1.5 times
    its estimate」, `sidepanel/itemPoller.test.ts`,
    `SidePanelCollect.test.tsx` 「progress polling」.

## Out of scope

- Batch or list-page import
- In-browser ASR
- Map-reduce full-video summaries
- Bilibili, which moved to child task `10-17-bilibili-side-panel` (2026-10-17)
- Continuing a page thread from the web (shown read-only there)
- Chrome Web Store submission

## 收尾记录 (2026-10-19)

- **Shipped.**
  - The side panel, instant page Q&A, `pot` caption read, one-click 「收藏视频」
    with in-thread progress, and Bilibili (child `10-17-bilibili-side-panel`)
    are on `dev` (`9420cea`, `07fe844`, `c6d2044`, `966dfd4`, plus earlier
    commits on `feat/side-panel-page-qa`).
  - They reached `main` through PR #32 (`release/side-panel-page-qa`, extension
    0.2.0), merged 2026-10-17. The `Extension and API contract` workflow on main
    at `9daf552` passed.
  - Spec updates listed in design §8 shipped with it: `browser-companion-capture.md`,
    `web-browser-runtime.md`, `logging-guidelines.md`.
- **Not verified, by decision of lane (2026-10-19):**
  - First-delta timing ≤ 5 s.
  - Citation seek on a real YouTube and Kaltura player.
  - `pot` read on at least two videos with the caption state unchanged.
  - NTULearn/Kaltura captioned lecture Q&A and seek.
  - The log grep for a cue phrase, and a live save-then-repeat.
  - lane said they only tested the extension's subtitle summary, and that the
    remaining live checks were moved into another task. They did not name that
    task. The PR #32 body lists a local smoke (YouTube and Bilibili Q&A,
    「收藏视频」 progress, citation seek, first delta ≈1–2 s). It is a
    self-report that was not independently confirmed here.
  - The task is closed on that basis. It is not a statement that the live
    checks passed.
- **Parent:** `10-15-extension-page-qa`.
