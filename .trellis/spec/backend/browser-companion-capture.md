# Browser Companion Capture Contract

## 1. Scope / Trigger

Apply this contract whenever code changes browser-companion pairing, extension authorization, `capture.v1`, canonical transcript storage, `ntu_kaltura`, extension CORS, or the captured-item worker path. The extension is an optional acquisition client. It coexists with `YouTubeConnector`; do not implement a global enable/disable flag that hides either path.

## 2. Signatures

Canonical HTTP and storage signatures:

```text
POST   /api/v1/browser-companion/extension/pairings
GET    /api/v1/browser-companion/extension/pairings/{pairing_id}
POST   /api/v1/browser-companion/extension/pairings/{pairing_id}:exchange
DELETE /api/v1/browser-companion/extension/grant
POST   /api/v1/browser-companion/extension/captures

POST   /api/v1/browser-companion/pairings/{pairing_id}:approve
GET    /api/v1/browser-companion/devices
DELETE /api/v1/browser-companion/devices/{device_id}

ContentItem.raw_format: "json3" | "capture_v1"
BrowserCompanionPairing -> BrowserCompanionGrant -> BrowserCapture -> IngestDispatch
```

`app/browser_capture.py` owns `BrowserCaptureRequest`, platform canonicalization, `capture-transcript.v1`, cue hashing, parsing, and timestamp links. `app/browser_companion.py` owns pairing/grant state. `app/browser_capture_submission.py` owns quota/idempotency/object staging and dispatch admission.

## 3. Contracts

- `capture.v1` contains `protocol_version`, bounded `client_version`, `platform`, validated `platform_id`, secret-free canonical/page URLs, bounded public metadata, caption status/source/language/cues, and the server-defined cue hash.
- Platforms are `youtube`, `ntu_kaltura` and `bilibili` on the server; extension 0.2.1 reads only `youtube` and `bilibili`. Browser capture never sends a signed caption/playback URL for later server fetch.
- `caption.status=unavailable` requires null source/language and empty cues. It stores no transcript/media object and the worker produces `needs_asr`.
- State-changing extension requests require an exact `chrome-extension://<32 a-p characters>` Origin. Chrome MV3 extension pages and service workers omit Origin on cross-origin GET requests, so on `/api/v1/browser-companion/extension/*` and `/api/v1/extension/*` a `GET`/`HEAD` with the Origin header entirely absent may proceed (these routes authenticate only with explicit Bearer credentials, never cookies, and no CORS allow-origin header is emitted for a missing Origin). A present-but-disallowed Origin (including `null`), every unsafe method without Origin, and a bare `OPTIONS` still fail with `extension_origin_invalid`. A development-only `chrome-extension://*` configuration is valid only when the Web server is bound to loopback; production continues to require exact IDs. Configuration is `BROWSER_COMPANION_ALLOWED_ORIGINS`, `BROWSER_COMPANION_PAIRING_TTL_SECONDS`, `BROWSER_COMPANION_GRANT_TTL_SECONDS`, and `BROWSER_COMPANION_MAX_REQUEST_BYTES`.
- Pairing challenges and Bearer tokens are hash-only at rest. A grant has only `capture:write`; it cannot authenticate Web, conversation, management, or MCP routes. Web cookies cannot replace the extension Bearer.
- Same-origin approval/device mutation stays behind existing Web session, exact-Origin, and CSRF validation. Extension disconnect revokes its own token before local deletion; account-device revoke is tenant-scoped.
- Canonical transcript object keys are tenant-prefixed. Celery receives only the dispatch ID; no cue text, third-party token, signed URL, or browser credential enters PostgreSQL rows, broker messages, or logs.
- The browser-capture publish budget starts immediately before broker I/O. Remote database admission and object staging do not consume that budget; `queue_unavailable` represents a bounded publisher failure, not slow PostgreSQL work completed before publication.
- Publisher acknowledgements lock and re-check a dispatch before changing `pending` to `enqueued`/`failed`; they must never overwrite a faster worker's `running` or `completed` state. The same rule applies to ordinary ingestion retries and browser-capture admission.
- **NTULearn/Kaltura capture was retired in extension 0.2.1.** The extension no longer holds `ntulearn*.ntu.edu.sg` or `cdnapisec.kaltura.com` host permissions, ships no Kaltura adapter, and treats those pages as unsupported (`classifyPageUrl` returns null). The server keeps the `ntu_kaltura` platform, its validation, `needs_asr` rule, link builder and existing items unchanged, so older clients and stored data keep working; no migration.
- YouTube caption tracks may expose a signed `/api/timedtext` endpoint whose JSON3 representation is empty or non-JSON even while the player renders captions. After validating the original trusted HTTPS host/path, try JSON3 first, then WebVTT and the unmodified original XML representation. Revalidate every response redirect and never return or log the signed URL/query.
- If every trusted timed-text representation is empty, the page's official same-origin `youtubei/v1/get_transcript` endpoint may be used as a final fallback. Base64url transcript params must decode to the exact current video ID before the request; accept only an exact same-origin endpoint response and project only finite ordered segment cues. Never return or log the public client key, InnerTube context, opaque params, response body or browser credential.
- Bilibili (`https://www.bilibili.com/video/<BV…|av…>`, optional `?p=N`) is read entirely inside the page with the viewer's session, so the manifest needs only the exact `https://www.bilibili.com/*` host permission and never `api.bilibili.com` or `*.hdslb.com`:
  - `platform_id` is the connector's normalized id (`BV` + 10 alphanumerics, or `av` + digits), so capture and URL import reach one `ContentItem`. `canonical_url` is `https://www.bilibili.com/video/<id>` with no query; `page_url` keeps `p` only when it is above 1 (1..9999, single value). Cover URLs must be https, unsigned and on `hdslb.com`/`*.hdslb.com`.
  - The adapter validates the page state (`window.__INITIAL_STATE__.videoData`) against the URL's id; a state describing another video falls back to the public `x/web-interface/view` response for that id, and anything still mismatched is the retryable `stale_player_response`. The part's `cid` is `pages[p-1].cid`. Subtitles come from `GET api.bilibili.com/x/player/wbi/v2?aid=&cid=` (no WBI signature; the viewer's cookies unlock login-only AI subtitles) and the subtitle JSON on `*.hdslb.com` (`{body:[{from,to,content}]}`).
  - Prefer human subtitles over AI (`ai-*`, source `auto_caption`), Chinese over other languages; try at most three tracks. The signed subtitle URL (`auth_key`) is fetched without cookies, only from https `*.hdslb.com` (redirects revalidated, bounded body), and is never returned, stored, or logged. Cookies go only to `api.bilibili.com`. Danmaku is never read.
  - No listed subtitle gives `caption.status="unavailable"` with metadata and `view_points` chapters; a listed but untrusted/unreadable subtitle is a failed read, not an absence. A capture-origin Bilibili item without captions becomes `needs_asr` (no server ASR hand-off, which is YouTube-only).
  - `timestamp_url("bilibili", …)` keeps `p` above 1 plus `t` and drops every other query parameter. Part 1 links are unchanged. The server URL connector still rejects `p≠1`, so a later part can only be saved through capture.
  - Player control targets the top frame: the bpx `window.player.getCurrentTime()`/`seek(t)` when present, else the main player's `<video>`/`<bwp-video>` element.
- Existing `raw_format=json3` objects remain readable. New captured transcripts use `capture_v1` and the same bounded chunk/embed/completion path without a remote platform connector call.
- A retry of a captured item reuses the latest tenant-matching `ready` `BrowserCapture` for that item even though the capture remains linked to the original dispatch attempt. It must not require that original dispatch to remain `running`, and it must never reuse another tenant's capture.
- Captured transcripts above 512 cues skip per-cue semantic-boundary embedding and are chunked deterministically on gap/punctuation boundaries within the 80–200-word (130–330 CJK char) window, falling back to hard cuts, before embedding final searchable chunks. This bounds provider work for multi-hour recordings while preserving the 120-second hard segment boundary. See `ingestion-chunking-embedding.md`.
- A packaged extension selects exactly one StashSeek Chat API origin through its exact manifest host permission. The production artifact allows only `https://stashseek.deequoique.tech/*`; a local qualification artifact allows only loopback `http://127.0.0.1:8000/*`, while the authenticated approval UI remains on `https://localhost:8443`. Never ship both API origins in one manifest, and audit each build target independently. Loopback HTTP avoids relying on browser exceptions for an untrusted local HTTPS issuer without widening access beyond the machine.
- The renamed extension keeps its manifest signing `key`, exact origin binding,
  and extension ID. That is the zip install ID `omogodipchfidpikpeebgmlplpkjnpfm`.
- Since 0.2.1 the manifest host permissions are exactly the YouTube hosts
  (`https://www.youtube.com/*`, `https://youtu.be/*`), `https://www.bilibili.com/*`
  and the selected API origin. The production API origin is
  `https://stashseek.deequoique.tech`. `audit-package.mjs` pins this list.
- The manifest declares `icons` (16/32/48/128, `icons/icon-<n>.png`) and
  `action.default_icon` (16/32). The files live in `extension/public/icons/`
  (generated by `scripts/generate-icons.py`), the build copies them to
  `dist/icons/`, and the audit asserts the declarations and that each file exists.
- `scripts/package-release.mjs` (`pnpm package:release`) builds and audits the
  production target, then writes two deterministic zips (sorted entries, fixed
  timestamp) to the git-ignored `extension/release/`:
  - `stashseek-chat-browser-companion-production-<version>.zip` is the build as
    is, with the manifest `key`, so a zip install keeps its extension ID.
  - `stashseek-chat-chrome-web-store-<version>.zip` differs only in `manifest.json`
    having no `key`, because the store assigns the item ID
    (`jeemllapbelpglmpnhjfamcnoibpeail`) and rejects the field.
  - Both are verified to carry the production API host, no NTULearn/Kaltura or
    loopback host permission, no source map, and every declared icon.
  - `BROWSER_COMPANION_ALLOWED_ORIGINS` lists both extension IDs
    (`deploy/systemd/notebook-agent.service`); one build never serves the other
    API origin.
- Two credentials stay separate:
  - The capture Bearer remains `capture:write` plus self-revoke.
  - Library, chat, and page Q&A use the separate extension **session** Bearer
    (`/api/v1/extension/auth/*`), never the capture Bearer.
- Since 0.2.0 the toolbar action opens a `chrome.sidePanel`
  (`side_panel.default_path = "sidepanel.html"`). There is no
  `action.default_popup`.
  - The manifest permissions are exactly `activeTab, scripting, storage,
    sidePanel`, with `minimum_chrome_version: "114"`.
  - `audit-package.mjs` asserts four things: the permission set, the side panel
    path and file, the absence of a popup, and no inline or remote script in
    `sidepanel.html`.
  - The worker calls `chrome.sidePanel.setPanelBehavior({openPanelOnActionClick: true})`
    on install and on startup.
- Worker commands that pages of the extension may send (a closed union;
  malformed input returns `invalid_command` or `unknown_command`):
  - `READ_PAGE {tabId}` returns the `PageCapture` without submitting anything.
  - `PLAYBACK {tabId}` returns `{playback_sec: number|null}`.
  - `SEEK {tabId, start_sec}` accepts a finite value from 0 to 31,536,000. It
    returns `{seeked:true}` or `seek_failed`.
  - `SAVE_CAPTURE {page}` submits an already-read page through the same capture
    path and pending idempotency-key reuse as `CAPTURE`.
- Player control is a self-contained MAIN-world `controlPlayer(seekSec, kind)`:
  - **YouTube:** `#movie_player.seekTo`, then the `<video>` fallback. A playing
    ad counts as no video.
  - **Bilibili:** the bpx `window.player` API when present, then the main
    player's `<video>`/`<bwp-video>`.
  - Both players live in the top frame, so every probe and seek targets only
    `allFrames: false`.
  - The side panel seeks only while the active tab still shows the video a
    citation belongs to. Otherwise it opens the citation URL in a new tab.
- The YouTube adapter fills `metadata.description` from
  `videoDetails.shortDescription` (≤50,000 chars).
  - It fills `metadata.chapters` from player-bar markers only when
    `ytInitialData.currentVideoEndpoint` names the same video, because SPA
    navigation leaves `ytInitialData` stale.
  - Chapters use `{title, start_sec, end_sec|null}`. Ingest's `chunk()` accepts
    both this shape and yt-dlp `start_time`/`end_time`.
- Extension API fetches are bounded to 10 seconds and map timeout or network
  failures to stable, safe panel errors. A missing, nearby, or ambiguous API
  host permission fails closed before pairing or capture. The panel must never
  stay busy indefinitely on an unreachable deployment.
- Persisted extension pairing/grant state is bound to the selected API origin. Switching between production and local artifacts clears cross-target credentials before any request; legacy state without an origin may be adopted only by the original production target.

## 4. Validation & Error Matrix

| Condition | Stable behavior |
| --- | --- |
| wrong/missing Origin on state-changing extension route | `extension_origin_invalid` / 403 before route execution |
| missing Origin on exact read-only MV3 pairing-status GET | route executes; no CORS allow-origin header is invented |
| missing, expired, revoked, disabled, or wrong-scope grant | safe `extension_*` 401; Web/MCP sessions unchanged |
| unsupported `protocol_version` | `capture_protocol_unsupported` / 422 |
| unknown field, invalid ID/host/timing, signed cover URL | safe validation/capture error; no side effect |
| cue hash mismatch | `capture_content_hash_mismatch` |
| request/raw/cue/text limit exceeded | 413 or `capture_too_large`; no embedding |
| same tenant/idempotency key and same body | return the existing public result |
| same key and different body | `capture_conflict` |
| object put failure | cleanup best effort, capture/dispatch/item fail safely |
| broker publish failure | `queue_unavailable`; a new user action may retry the deterministic object |
| caption unavailable | `needs_asr`, no audio/video upload |

## 5. Good / Base / Bad Cases

- Good: a paired user reads a YouTube caption in the active browser, uploads normalized cues, and the worker reaches `ready` without calling `YouTubeConnector`.
- Good: a paired user reads a Bilibili video page with their own session, uploads only cues and a secret-free `bilibili` reference, and later citations use the platform link builder.
- Base: no extension is installed or paired. Existing server YouTube submission and legacy JSON3 reads behave exactly as before.
- Base: no caption track exists. The item reaches `needs_asr`; no media bytes leave the browser.
- Bad: copying a Web cookie, MCP token, Kaltura KS, SAML response, signed URL, or page exception into the capture payload/log. The strict schema and transport split exist to prevent this.

## 6. Tests Required

- Contract tests: unknown fields, untrusted hosts, platform IDs, finite ordered timings, cue/text/byte limits, cue hash, signed cover URL, legacy JSON3, and `capture_v1` parsing.
- Pairing tests: challenge/verifier, explicit approval, cross-tenant claim, single exchange, expiry, disabled user, hash-at-rest, resolve, self-revoke, account revoke, and replay.
- API tests: exact extension CORS, the narrow missing-Origin MV3 status exception, missing-Origin rejection on every extension mutation, preflight methods/headers, capture Bearer vs Web cookie, Web CSRF, protocol-version error, public-only responses, and request ceiling.
- Submission/worker tests: quota/idempotency, object and broker compensation, available-to-ready, unavailable-to-`needs_asr`, no connector call, no duplicate object put, completion event, and deletion race.
- Migration checks: model/DDL parity, forward-compatible `ntu_kaltura`, `raw_format` default, downgrade retaining the enum value, and exactly one Alembic head.
- Extension gates:
  - strict TypeScript, lint, and fixture adapter tests;
  - production and local builds, each with its own exact permission audit
    (including `sidePanel`, with no popup);
  - API-origin and timeout tests;
  - no source maps or secrets;
  - an unpacked YouTube and Bilibili smoke test;
  - `package:release`, with both zips verified (store zip has no `key`).
- Player-control tests:
  - `controlPlayer` survives serialization into a fresh JS context;
  - probe and seek target only the top frame, and an NTULearn/Kaltura tab is
    `unsupported_page` with nothing injected;
  - `SAVE_CAPTURE` reuses the pending idempotency key without re-reading.
- Chapter test: a capture with `start_sec`/`end_sec` chapters, including a
  null last end, ingests without `KeyError`.

## 7. Wrong vs Correct

### Wrong

```python
# A signed browser URL or Web credential must not be queued for server fetch.
publish({"url": caption_url, "cookie": request.cookies["session"]})
```

### Correct

```python
scope = companion.resolve_grant(capture_bearer)
result = capture_submission.submit(scope, validated_capture, request_key=key)
# The submission service persists/publishes the internal dispatch; the public
# result exposes only capture/item public IDs, platform, status, and lifecycle.
```

The extension consumes third-party authorization locally, the API revalidates normalized content, and the existing worker owns durable ingestion.
