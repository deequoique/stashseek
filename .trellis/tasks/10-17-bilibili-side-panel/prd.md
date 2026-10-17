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

- [ ] On a Bilibili video with AI subtitles (logged in), the panel reads
      subtitles and answers with clickable timestamps that seek the player.
- [ ] Switching parts (`?p=2`) or videos starts a new thread about the new
      content.
- [ ] 「收藏视频」 saves the Bilibili video, and it reaches `ready` (or
      `needs_asr`, per the R8 decision).
- [ ] Server tests cover `bilibili` capture.v1 and page-Q&A validation
      (platform id, hosts, timestamp URL).
- [ ] Extension unit tests cover the adapter (subtitle-list shapes, no
      subtitles, stale part) and the audit expectations. Both builds pass.

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
