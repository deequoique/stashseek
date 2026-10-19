# Bilibili support in the side panel

Parent: `10-15-extension-page-qa`. User request from the Chrome smoke on
2026-10-17: on a Bilibili video page the panel does not recognise the video. It
should work like YouTube for page Q&A and for 「收藏视频」.

## Goal

On `https://www.bilibili.com/video/BV…` (and `av…`) pages, the side panel:

- recognises the video;
- reads its subtitles in the user's own browser session, including subtitles
  that need login, such as AI subtitles;
- answers questions with timestamp citations;
- seeks the player when a citation is clicked;
- saves the video to the library through 「收藏视频」.

## Confirmed facts

- The server already ingests Bilibili URLs with a yt-dlp connector
  (`app/connectors/bilibili.py`, commit `cd8db6d`).
- That connector never uses Bilibili cookies. Subtitles that need login return
  `NeedsExtension("Bilibili subtitles require browser login")`
  (`bilibili.py:317-325`), so the design already expects a browser path that
  does not exist yet.
- `platform_enum` already contains `bilibili` (`app/models.py:44`). capture.v1
  and page Q&A accept only `youtube` and `ntu_kaltura`
  (`app/browser_capture.py`, `extension/src/protocol.ts`).
- The extension manifest has no Bilibili host permissions, and its packaging
  audit enforces an exact host list.
- `classifyPageUrl` (`extension/src/page-capture.ts`) returns null for Bilibili,
  which explains the "unsupported page" state.

## Requirements (draft)

- **B1 Recognition.**
  - `classifyPageUrl` and `identifyPage` handle Bilibili video URLs, including
    multi-part `?p=N`.
  - Identity is BV id plus page index.
  - SPA navigation between videos or parts starts a new thread.
- **B2 In-browser subtitle read.** A self-contained MAIN-world adapter:
  - resolves `aid`/`cid` for the current part from page state;
  - reads the subtitle list from Bilibili's player API with the user's cookies;
  - prefers a human subtitle over an AI one, then Chinese;
  - fetches the subtitle JSON from the trusted subtitle host and normalises it
    to cues.
  - Login cookies and signed URLs never leave the page.
  - With no subtitles, it returns `caption.status = "unavailable"`, the same
    contract as YouTube.
- **B3 Platform contract.**
  - `bilibili` is added to capture.v1 and page Q&A on server and extension,
    with platform-id validation and timestamp URLs (`?t=`).
  - The spec is updated.
- **B4 Player control.** PLAYBACK and SEEK work on the Bilibili player, which
  is a `<video>` element in the top frame. This must be confirmed.
- **B5 Permissions.**
  - Add the minimal exact host permissions needed (the video page, the player
    API host, and the subtitle CDN host).
  - Update the audit allow-list.
  - Each host needs a Chrome Web Store justification.
- **B6 「收藏视频」 on Bilibili.** Same behaviour as YouTube R8. A capture with
  subtitles goes through capture.v1. The no-subtitle fallback follows the
  decision taken for R8.

## Acceptance criteria (draft)

Closed 2026-10-19; see 「收尾记录」 at the end of this file. A box is ticked only
where code or tests prove it. The three live checks need a logged-in Chrome and
were not run.

- [ ] On a Bilibili video with AI subtitles (logged in), the panel reads
      subtitles and answers with clickable timestamps that seek the player.
  - **Not verified live.**
  - Fixture tests cover the logic: `page-capture.test.ts` 「reads an AI subtitle
    behind the viewer's login…」, 「prefers the bpx player API for the position
    and the seek」, and `SidePanel.test.tsx` 「side panel citations」.
  - The real `wbi/v2` and `__INITIAL_STATE__` shapes were never checked in a
    logged-in page, which the PRD itself flagged.
- [ ] Switching parts (`?p=2`) or videos starts a new thread about the new
      content.
  - **Not verified live.**
  - Unit-level only: `pageIdentity.test.ts` 「identifies Bilibili videos by
    normalized id and part, so every part has its own thread」;
    `SidePanel.test.tsx` 「re-reads a Bilibili page that still describes the
    previous video or another part」;
    `page-capture.test.ts` 「selects the cid of the requested part」 and
    「rejects the read when the tab navigates to another video or part
    mid-read」.
- [ ] 「收藏视频」 saves the Bilibili video, and it reaches `ready` (or
      `needs_asr`, per the R8 decision).
  - **Not verified live.**
  - Panel behaviour is covered by `SidePanelCollect.test.tsx`
    「收藏视频 on Bilibili」 (7 cases: capture path, URL import of part 1, no
    URL import for part 2 and later).
  - The server URL-import path for Bilibili was canaried in production on
    2026-10-16 (journal Session 24).
- [x] Server tests cover `bilibili` capture.v1 and page-Q&A validation
      (platform id, hosts, timestamp URL).
  - Evidence: `tests/test_bilibili_capture.py`, 14 test functions. They cover accepted
    ids, rejected foreign hosts, ids, parts and signed covers, the page URL
    keeping `p`, timestamp links, the page-Q&A request model and route, and
    citations for part 2.
- [x] Extension unit tests cover the adapter (subtitle-list shapes, no
      subtitles, stale part) and the audit expectations. Both builds pass.
  - Evidence: `extension/src/page-capture.test.ts`, 「Bilibili caption adapter」
    (21 tests) and 「Bilibili player control」 (7);
    `extension/src/bilibiliPackaging.test.ts`;
    `extension/scripts/audit-package.mjs` (exact host list includes
    `https://www.bilibili.com/*`).
  - Gates: workflow `Extension and API contract` passed on main at `9daf552`
    (typecheck, lint, vitest, production build and audit).
  - The local build and audit rest on the PR #32 body (「production and local
    build plus audit all pass」), not on a CI run.

## Resolved in planning (2026-10-17)

- **Hosts.** Subtitles come from
  `api.bilibili.com/x/player/wbi/v2?aid=&cid=`, which needs no WBI signature
  (per yt-dlp), and from subtitle JSON on `*.hdslb.com`.
  - Both are fetched **inside the page**, so the extension needs only
    `https://www.bilibili.com/*`.
  - The exact response shapes still need a live check in a logged-in page.
- **No-subtitle Q&A.** It answers from title, description and chapters with
  the no-captions label, the same as YouTube. This is the default and can be
  changed.
- **Multi-part videos.**
  - Q&A works on any part.
  - 「收藏视频」 works on part 1 through capture or URL import. On part 2 and
    later it works only through capture, because the server connector rejects
    `p≠1`.
- **`platform_id`.** The normalised `BV…` id, identical to the server
  connector's, so both save paths reach the same library item.

## Out of scope

Bilibili danmaku, live streams, bangumi/PGC episodes, and list-page batch
import.

## 收尾记录 (2026-10-19)

- **Shipped.**
  - Subtitle read: `c6d2044`.
  - One-click 「收藏视频」 with Bilibili handling in the panel: `966dfd4`.
  - Both are on `dev` and reached `main` through PR #32 (`release/side-panel-page-qa`,
    extension 0.2.0), merged 2026-10-17. The `Extension and API contract`
    workflow on main at `9daf552` passed.
- **Verified by code and tests:** B1 recognition, B2 adapter, B3 contract, B4
  player control, B5 permissions, B6 saving. Details are in the acceptance
  criteria above and in the 完成情况 table in `implement.md`.
- **Not verified, by decision of lane (2026-10-19):**
  - A logged-in Bilibili video with AI subtitles: Q&A, citations, seek.
  - A `?p=2` part.
  - 「收藏视频」 on part 1 reaching `ready`.
  - lane said they only tested the extension's subtitle summary, and that the
    remaining live checks were moved into another task. They did not name that
    task. `10-18-stashseek-ingest-status` builds on this code (`useCollect.ts`,
    `collectModel.ts`), but it is not confirmed to be the one.
  - The task is closed on that basis. It is not a statement that the live
    checks passed.
- **Parent:** `10-15-extension-page-qa`.
