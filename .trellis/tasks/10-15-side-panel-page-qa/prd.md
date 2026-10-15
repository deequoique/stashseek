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

## Acceptance criteria

- [ ] Clicking the icon opens the side panel. There is no popup. Login, connect,
      and save work from the panel.
- [ ] On a captioned YouTube video of 20 min or less, the first `text_delta`
      arrives in ≤ 5 s locally (measured, model call included). No
      `content_item`, `segment`, object-store, or broker write happens.
- [ ] Clicking a cited timestamp seeks the YouTube player (and the Kaltura
      player) to within 1 s.
- [ ] After SPA navigation to another video, the next question is answered about
      the new video in a new thread.
- [ ] Above the full-context threshold, an answer is still produced and cites
      paragraphs near the playback position when the question refers to "刚才".
- [ ] A caption-less YouTube video gets a labelled metadata-only answer. A
      caption-less Kaltura video shows the unavailable state, with no request.
- [ ] The Q&A turn appears in conversation history (extension and web) with the
      question, the answer, and citations. Stored excerpts are ≤180 chars. Tests
      assert that no cue text beyond those excerpts is in `conversation_turn`
      and that no log record contains cue text.
- [ ] Save after Q&A creates exactly one capture and reaches `ready`. Repeating
      it returns the existing result.
- [ ] An extension Bearer request to the existing conversation routes succeeds
      without `X-CSRF-Token`. A cookie request without it still fails.
- [ ] Contract tests cover the page-Q&A route:
  - oversized body;
  - unknown fields;
  - a capture Bearer (rejected);
  - a missing or invalid extension Origin;
  - an unsupported platform;
  - the rate limit.
- [ ] Extension checks pass: unit tests, typecheck, lint, both builds, and both
      audits (with `sidePanel` in the expected permissions).

## Out of scope

- Batch or list-page import
- In-browser ASR
- Map-reduce full-video summaries
- Bilibili
- Continuing a page thread from the web (shown read-only there)
- Chrome Web Store submission
