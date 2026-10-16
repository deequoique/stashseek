# Bilibili Connector Contract

## 1. Scope / Trigger

Apply this contract when changing Bilibili URL admission, yt-dlp metadata or
subtitle ingestion, SRT storage/reading, Bilibili citations, or public Web
capabilities. Browser-companion page capture remains a separate follow-up and
must not be implied by server URL support.

## 2. URL Contract

- Accept only credential-free HTTPS `bilibili.com`/`www.bilibili.com` ordinary
  video paths with a strict 12-character BV ID or numeric av ID.
- Strip tracking queries and fragments from the canonical URL. Reject fragments,
  explicit ports, short links, non-video paths, malformed IDs, and `p` values
  other than the first part. Base multipart URLs intentionally select part 1
  through yt-dlp `--no-playlist`; do not silently expand a collection.
- URL preflight is local and must never make a provider request.

## 3. Connector Contract

- Use the declared yt-dlp runtime with `--ignore-config`, `--no-playlist`,
  `--skip-download`, a socket timeout, and a process wall-clock timeout.
- Never supply, persist, log, or enqueue Bilibili cookies, `SESSDATA`, WBI
  signatures, media URLs, subtitle URLs, response bodies, or raw stderr.
- Map public metadata to `ItemMeta`; accept cover images only from `hdslb.com`
  subdomains, upgrade them to HTTPS, and remove query/fragment data.
- Ignore the `danmaku` XML track. Prefer official SRT before `ai-*` automatic
  SRT. Consume only yt-dlp-projected inline `data`; a URL-only track requires
  browser capture rather than adding a broad server fetch allowlist.
- Parse bounded UTF-8 SRT into finite, ordered cues. Store it as raw format
  `srt`, object suffix `.srt`, and content type `application/x-subrip`.
- No visible subtitle returns `NeedsASR`. An explicit yt-dlp login-only warning
  returns `NeedsExtension`. Because upstream/provider behavior can omit that
  discriminator, real canaries must record the observed state without claiming
  that every login-only video is distinguishable from a no-caption video.
- Stable transient classifications are `bilibili_rate_limited`,
  `bilibili_fetch_timeout`, `bilibili_fetch_failed`, and (only when a proxy is
  configured) `bilibili_proxy_unavailable`, `bilibili_proxy_timeout`, and
  `bilibili_proxy_authentication_failed`; worker retry surfaces continue to
  redact them according to the shared ingestion contract.
- HTTP 412 risk control (`HTTP Error 412` / `Precondition Failed`) is labelled
  `bilibili_rate_limited`, together with 429, `-352` and `风控`. Match the
  `http error 412` phrase, never a bare `412` substring: a BV id may contain it.
  The label does not change the retry policy (`TransientFetchError` autoretry).

## 3a. Egress proxy contract

- The production VPS IP is blocked by Bilibili (public videos return
  `HTTP Error 412: Precondition Failed`). `BilibiliConnector(proxy_url=...)`
  accepts the same credential-free loopback value as `YouTubeConnector`; the
  worker passes `settings.us_video_proxy_url` (`US_VIDEO_PROXY_URL`) to both
  connectors in `_connector()`.
- With a proxy, the yt-dlp metadata child receives `env=` from
  `app/connectors/proxy_env.build_proxy_environment` (a fresh copy per call:
  upper- and lower-case HTTP(S) proxy variables, loopback-only `NO_PROXY`, no
  `ALL_PROXY`, trusted-CA variables preserved, `os.environ` untouched). Without a
  proxy no `env` kwarg is passed and the child inherits the worker environment.
- A proxied failure is never retried directly. Classification order: rate
  limit / risk control, then `classify_proxy_failure(..., prefix="bilibili")`
  (only when a proxy is configured), else `bilibili_fetch_failed`. A timeout is
  `bilibili_proxy_timeout` with a proxy and `bilibili_fetch_timeout` without.
- Exception text never contains raw stderr, proxy values or URLs.
- Subtitles stay inline yt-dlp `data`, so no bounded-fetch child is involved.
- The egress filter must allow `bilibili.com`, `hdslb.com`, `bilivideo.com`,
  `bilivideo.cn` and `^upos-[a-z0-9-]+\.akamaized\.net$` (see
  `youtube-connector.md`); measured CONNECT hosts are `www.bilibili.com`,
  `api.bilibili.com` and the audio CDN mirror, all on port 443. The audio CDN
  host varies with the exit region, so the first production canary must confirm
  it and check the journal for `Proxying refused`. A filter-refused CDN host
  surfaces as `bilibili_proxy_unavailable` (urllib3 wraps the 403 tunnel
  failure as `Unable to connect to proxy`), not as `bilibili_rate_limited`.

## 4. Required Tests

- URL matrix: BV, av, tracking queries, host confusion, credentials, ports,
  fragments, short links, malformed IDs, and multipart `p` values.
- Metadata mapping, cover allowlist, timeout, rate-limit/risk-control
  classification, stderr redaction, and absence of cookie arguments.
- Official vs automatic SRT selection, danmaku exclusion, login-only/no-caption
  states, size limits, malformed/unordered SRT, object key/content type, Web
  transcript projection, and Bilibili timestamp citations.
- API capability/OpenAPI, Web add-dialog copy, platform label, TypeScript,
  lint, frontend tests, full pytest, and read-only real metadata canaries.
- Proxy: the proxied metadata child gets the child-only environment (fresh copy
  per call, parent unchanged), the unproxied child gets no `env`, 412 and risk
  control map to `bilibili_rate_limited`, proxy refused / 407 / timeout map to
  `bilibili_proxy_*` with exactly one attempt and redacted text, a BV id
  containing `412` is not misread as HTTP 412, and a real child against an
  absent loopback proxy fails closed (`tests/test_video_egress_proxy.py`).
