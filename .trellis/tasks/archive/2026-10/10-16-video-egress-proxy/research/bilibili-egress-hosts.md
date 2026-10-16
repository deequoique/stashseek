# Bilibili hosts needed through the egress proxy (measured 2026-10-16)

## Method

- Local tinyproxy 1.11.3 with `LogLevel Info`, no filter and no upstream, run from a
  Singapore residential connection. yt-dlp was pointed at it through `HTTP(S)_PROXY`.
- The commands mirror production exactly:
  - Metadata: `BilibiliConnector._run_metadata` arguments (`--ignore-config
    --socket-timeout 30 --no-playlist --skip-download --dump-single-json <canonical url>`),
    run against `BV1uv411q7Mv` and `BV1NyJ26tEsY`.
  - ASR audio: `app/asr/audio.py` arguments (dev) with
    `-f "bestaudio[ext=m4a]/bestaudio" --max-filesize ...`, run against `BV1uv411q7Mv`.

## Result

| Purpose | CONNECT host:port | Count |
| --- | --- | --- |
| metadata (page + API) | `www.bilibili.com:443` | 3 |
| metadata (WBI/view API) | `api.bilibili.com:443` | 3 |
| ASR audio | `upos-hz-mirrorakam.akamaized.net:443` | 1 |

There were no plain-HTTP requests and no ports other than 443.

## Subtitles

- None of the six sampled public videos (`BV1uv411q7Mv`, `BV1xx411c7mD`, `BV1Ga411A71Z`,
  `BV1c8411Y765`, `BV1GG411Q7bi`, `BV1NyJ26tEsY`) exposed subtitles to an anonymous
  client. Bilibili subtitles are effectively login-only, and the connector already maps that
  case to `NeedsExtension` or `NeedsASR`.
- When an anonymous subtitle does exist, yt-dlp's Bilibili extractor fetches its JSON
  during extraction from `*.hdslb.com` (`aisubtitle.hdslb.com` for AI subtitles,
  `i0.hdslb.com/bfs/subtitle/...` for CC). That was not observed in the samples, but
  `hdslb.com` is cheap to allow and is already the connector's trusted cover host
  (`_safe_cover_url`).

## CDN variance

- The audio CDN host depends on the exit region. A Singapore exit gave the Akamai overseas
  mirror `upos-hz-mirrorakam.akamaized.net`. Other regions commonly use
  `upos-*-mirror*.bilivideo.com` (e.g. `upos-sz-mirrorcos.bilivideo.com`) or
  `*.bilivideo.cn` / `*.mcdn.bilivideo.cn`.
- The production exit is the US (Evomi `_country-US`), so the first production canary must
  confirm the actual audio host.
- Allowing all of `akamaized.net` would open any Akamai-hosted site. Restrict it to the
  Bilibili mirror naming: `^upos-[a-z0-9-]+\.akamaized\.net$`.

## Bandwidth

- `BV1uv411q7Mv` (313 s) produced a 12.3 MB m4a with the current selector, roughly
  140 MB per audio-hour, which is more than twice YouTube's 128 kbps m4a (~58 MB/h).
- Bilibili offers lower audio tiers (about 64/132/192 kbps). A bitrate cap in the selector,
  such as `bestaudio[abr<=140]`, would cut proxy bytes without hurting ASR.

## tinyproxy logging

- `LogLevel Notice` logs only startup and filter refusals, for example
  `Proxying refused on filtered domain "upos-sz-mirrorcos.bilivideo.com"`. Allowed CONNECTs
  are not logged. That makes Notice the right production level: a CDN host missing from the
  filter shows up in `journalctl -u notebook-agent-youtube-egress` without logging every
  request. The current level is `Warning`, which hides refusals.

## Proposed filter additions

```text
(^|\.)bilibili\.com$
(^|\.)hdslb\.com$
(^|\.)bilivideo\.com$
(^|\.)bilivideo\.cn$
^upos-[a-z0-9-]+\.akamaized\.net$
```
