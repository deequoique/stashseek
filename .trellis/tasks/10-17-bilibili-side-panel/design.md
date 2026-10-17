# Design: Bilibili in the side panel

## Facts this design relies on

- **yt-dlp subtitle source.** yt-dlp (`yt_dlp/extractor/bilibili.py`
  `_get_subtitles`) reads subtitles from
  `GET https://api.bilibili.com/x/player/wbi/v2?aid=<aid>&cid=<cid>` (or
  `bvid=&cid=`). This call needs **no WBI signature**.
  - `data.subtitle.subtitles[]` carries `{lan, lan_doc, subtitle_url}`.
  - `subtitle_url` is protocol-relative. AI subtitles live on
    `aisubtitle.hdslb.com`; human ones are on `*.hdslb.com`.
  - The subtitle JSON looks like `{body:[{from, to, content}]}`.
  - AI subtitles need the viewer's login cookies.
  - The same endpoint carries chapters (`view_points`) for some videos.
- **Server connector.** It uses the normalised `BV…` id (or `av…`) as
  `platform_id`, part 1 only; it rejects `?p≠1` (`app/connectors/bilibili.py`
  `match`). It has no cookies, so subtitles that need login become
  `NeedsExtension` (`:317-325`).
- **Timestamp links.** `timestamp_url("bilibili", …)` already exists and keeps
  only `t` (`app/browser_capture.py:276-289`).

## Boundaries

### 1. Extension recognition

- `classifyPageUrl` accepts `https://www.bilibili.com/video/<BV|av id>` with an
  optional `p`.
- `identifyPage` key: `bilibili:<BV id>:p<n>`, with p defaulting to 1. Moving
  to another video or part means a new thread.

### 2. Adapter (self-contained MAIN world, like the YouTube adapter)

1. **Read page state.** Take the page's own state
   (`window.__INITIAL_STATE__`: `bvid`, `aid`, `videoData.{title, desc, owner,
   duration, pages[]}`, current `p`) and validate it against the URL. A stale
   SPA state returns `stale_player_response` (retryable). The `cid` is the
   current part's cid from `pages[p-1].cid`, falling back to
   `videoData.cid` for single-part videos.
2. **Fetch the subtitle list.** Call
   `fetch("https://api.bilibili.com/x/player/wbi/v2?aid=..&cid=..", {credentials:"include"})`
   from the page. Since this is page context, CORS and cookies are Bilibili's
   own, and no extension host permission is needed for the API or CDN hosts.
3. **Pick a subtitle.** Order: human Chinese, any human, AI Chinese
   (`ai-zh`), any AI. Fetch `https:` + `subtitle_url` with these checks:
   - trusted host (`*.hdslb.com`);
   - https, no credentials in the URL;
   - redirects revalidated;
   - bounded body size.
4. **Map the result.**
   - `body[]` becomes cues `{start_sec: from, end_sec: to, text: content}`.
   - `source` is `official_cc` for human subtitles and `auto_caption` for AI.
   - `language` comes from `lan`.
   - No subtitles gives `caption.status = "unavailable"` with metadata (title,
     description, duration, author, chapters from `view_points` when present).
5. **Secrecy.** Signed subtitle URLs (`auth_key`) and cookies never leave the
   page.

### 3. Player control

- `controlPlayer` for `bilibili` targets the top-frame player:
  - prefer `window.player.seek(t)` / `window.player.getCurrentTime()` when the
    bpx player API exists;
  - otherwise use the media element. That is `<video>` or the `<bwp-video>`
    custom element, which also exposes `currentTime`.

### 4. Contract (server and extension)

- **Platform.** `Platform` becomes `"youtube" | "ntu_kaltura" | "bilibili"` in
  `app/browser_capture.py` and `extension/src/protocol.ts`.
- **`platform_id`.** The normalised `BV…` (or `av…`), identical to the
  connector's, so capture and URL import resolve to the same `ContentItem`.
- **`canonical_url`.** `https://www.bilibili.com/video/<id>`.
- **`page_url`.** Keeps `p` when it is greater than 1.
- **Page Q&A.** Accepts `bilibili` for any part.
- **Timestamp links.** For `bilibili`, `timestamp_url` keeps `p` when it is
  greater than 1, plus `t`. Library items are part 1, so their links are
  unchanged.
- **Saving (「收藏视频」).**
  - Part 1 works the same as YouTube: capture.v1 when subtitles were read,
    otherwise automatic URL import.
  - Part 2 and later: capture.v1 only when subtitles were read. If the read
    fails, URL import is not available (the connector rejects `p≠1`), and the
    panel says 「暂不支持收藏这一 P，可以在第 1P 收藏」.
- **Validation.** capture.v1 validation accepts the `bilibili` host
  `www.bilibili.com`, the id regex, and `p`. Cover URLs on
  `i0/i1/i2.hdslb.com` must be https and unsigned, the same as the existing
  cover rules.

### 5. Permissions

- Add exactly `https://www.bilibili.com/*` to `host_permissions`, in both
  targets, and to the audit allow-list. No API or CDN host is needed, because
  fetches run in the page.
- Store justification: "read subtitles of the Bilibili video the user is
  watching, inside the page".

## Decisions taken by default (consistent with YouTube; open to change)

- A caption-less Bilibili page answers from title, description and chapters,
  with the no-captions label.
- Danmaku is never read.

## Risks

- `__INITIAL_STATE__` and the `wbi/v2` response shapes are not under our
  control. Every field read is defensive, and failure maps to a typed error.
- AI subtitles may be missing for logged-out users. The panel then shows the
  same "no subtitles" state, plus a hint to log in to Bilibili.
- Live verification needs the user's logged-in Chrome. Unit tests use
  fixtures.
