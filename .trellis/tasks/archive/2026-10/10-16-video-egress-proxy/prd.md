# Route Bilibili and all video fetches through the residential egress proxy

## Goal

Every server-side video acquisition goes through one loopback proxy setting,
`US_VIDEO_PROXY_URL`, which means "the US residential egress". That covers metadata,
subtitles, and ASR audio for YouTube and Bilibili. The goal is that Bilibili ingestion works
from the production VPS, and that future "must exit through the US residential proxy" needs
reuse the same setting.

## Background

- Production (OVH VPS, Singapore IP) is blocked by Bilibili. The production
  `BilibiliConnector` fails on public videos `BV1GJ411x7h7` and `BV1uv411q7Mv` with yt-dlp
  `HTTP Error 412: Precondition Failed`, surfacing as `bilibili_fetch_failed` (checked
  2026-10-16).
- A Singapore residential connection succeeds for `BV1uv411q7Mv` and `BV1xx411c7mD`
  (metadata ok), so a residential exit fixes the 412.
- YouTube already goes through `notebook-agent-youtube-egress.service`: tinyproxy on
  `127.0.0.1:18080` → Evomi Core Residential, sticky, US. Production sets
  `YOUTUBE_PROXY_URL=http://127.0.0.1:18080`.
- The only server-side video platforms are YouTube and Bilibili (`app/connectors/`).
  `wechat_mp` is articles, and `ntu_kaltura` is captured in the browser only.
- `BilibiliConnector` (`app/connectors/bilibili.py`) has no proxy support. The dev-only
  `app/asr/audio.py` says "Bilibili never uses a proxy". The tinyproxy filter and
  `scripts/youtube-home-egress` allow only `youtube.com` / `googlevideo.com`.
- Measured Bilibili hosts are `www.bilibili.com`, `api.bilibili.com` and
  `upos-hz-mirrorakam.akamaized.net`, all CONNECT 443. Anonymous subtitles are effectively
  absent. Bilibili audio runs about 140 MB per hour. See `research/bilibili-egress-hosts.md`.
- The touched connectors, filter and helper are identical on main and dev. The dev checkout
  has foreign uncommitted edits in `app/config.py`, `app/ingest/tasks.py`,
  `tests/test_tasks.py`, `.env.example`, `docs/reference/configuration.md` and
  `docs/explanation/ingestion-and-retrieval.md`. See `research/release-path.md`.

## Requirements

- R1 (rename): `US_VIDEO_PROXY_URL` replaces `YOUTUBE_PROXY_URL` everywhere (settings, code,
  tests, docs, specs, `.env.example`). Validation is the same: credential-free loopback HTTP
  URL with an explicit port. If `YOUTUBE_PROXY_URL` is still set, startup fails with a
  rename message. There is no alias and no silent fallback.
- R2 (Bilibili proxy): when the proxy is configured, Bilibili metadata/subtitle yt-dlp
  children get the same child-only proxy environment as YouTube, and nothing else in the
  worker is proxied. A proxied failure is never retried directly.
- R3 (ASR audio): Bilibili ASR audio downloads use the proxy, the same way YouTube's do.
- R4 (classification): Bilibili proxy failures map to `bilibili_proxy_unavailable`,
  `bilibili_proxy_timeout` and `bilibili_proxy_authentication_failed`. HTTP 412 risk control
  maps to `bilibili_rate_limited`. Exception text never contains raw stderr, proxy values or
  URLs. YouTube classifications are unchanged.
- R5 (egress filter): the server tinyproxy filter and the home-egress helper filter allow
  the same seven anchored rules (YouTube 2, Bilibili 5; design §5). The Akamai rule is
  restricted to `upos-` mirrors. `LogLevel Notice` makes filter refusals visible.
- R6 (shared code): the child-proxy environment and proxy-failure classification live in
  one shared helper used by both connectors.
- R7 (docs/ops): the runbook covers all video platforms and adds an upgrade procedure for
  the next dev→main release (filter reinstall, `LogLevel`, env rename before the gate,
  Bilibili canary). Specs are updated (`youtube-connector.md`, `bilibili-connector.md`,
  `asr-transcription.md`).

## Acceptance Criteria

- [ ] AC1 (R1): `Settings` reads `US_VIDEO_PROXY_URL` and rejects every previously
  rejected URL shape with a message naming `US_VIDEO_PROXY_URL`. With `YOUTUBE_PROXY_URL`
  set, even empty, `Settings` raises the rename error. `grep` finds no remaining
  `YOUTUBE_PROXY_URL` / `youtube_proxy_url` outside the rename error, its test, the runbook
  upgrade text and archived tasks.
- [ ] AC2 (R2): with a proxy, `BilibiliConnector` passes an `env` containing upper- and
  lower-case HTTP(S) proxy vars, loopback-only `NO_PROXY`, and no `ALL_PROXY`, while
  `os.environ` stays unchanged. Without a proxy, no `env` kwarg is passed. `_connector()`
  passes `US_VIDEO_PROXY_URL` to both connectors.
- [ ] AC3 (R3): `download_audio` for a Bilibili URL runs yt-dlp with the proxy env when
  configured, and maps Bilibili proxy failures and timeouts to the R4 codes.
- [ ] AC4 (R4): unit tests cover 412 → `bilibili_rate_limited`; proxy refused, 407 and
  timeout → `bilibili_proxy_*`; exactly one proxied attempt with no direct retry; redacted
  exception text. All existing YouTube tests pass unchanged except for the setting rename.
- [ ] AC5 (R5): the server filter file and the helper's embedded filter equal the seven
  design rules. They allow `www.bilibili.com`, `api.bilibili.com`,
  `upos-hz-mirrorakam.akamaized.net`, `upos-sz-mirrorcos.bilivideo.com` and
  `aisubtitle.hdslb.com`. They reject `evil-bilibili.com`, `bilibili.com.evil.net`,
  `example.akamaized.net` and `akamaized.net`. A local tinyproxy run of the example config
  forwards a Bilibili CONNECT 443 with auth and logs refusals at `LogLevel Notice`.
- [ ] AC6 (R6): the helper module exists, and `youtube.py` no longer contains its own copy
  of the proxy env and marker logic.
- [ ] AC7 (R7): the runbook upgrade section, the home-egress runbook, the configuration
  reference, the ingestion explanation, `.env.example` and the three specs reflect the
  rename and Bilibili routing.
- [ ] AC8 (landing): the change lands on `dev` as commits containing only this task's
  hunks. The foreign uncommitted edits in the shared checkout survive untouched. Targeted
  tests and the full suite pass with the test-branch `DATABASE_URL`. Nothing is pushed to
  `main`.

## Decisions

- D-release (user): land on `dev` only, and ship with the next full dev→main merge. At that
  release the operator renames the env variable before approving the Production gate and
  follows the runbook upgrade section.
- D-name (user): `US_VIDEO_PROXY_URL` is the single name for the US residential egress.
  Full rename. The old name is a startup error rather than an alias, so a missed rename fails
  loudly instead of silently going direct.
- D-names (design): the unit, `/etc/notebook-agent/youtube-egress/` paths and AppArmor
  override keep their names, to avoid churn on production paths.
- D-412 (design): HTTP 412 is labelled `bilibili_rate_limited`. The retry policy is
  unchanged (`TransientFetchError` autoretry).

## Out of Scope

- Production rollout in this task, since it rides the next dev→main merge.
- An ASR audio bitrate cap: already done separately in `1a81b4c` (40-72 kbps first; Bilibili
  30216 at ~65 kbps). This task must preserve that selector.
- Per-platform exit countries or per-domain upstreams. Bilibili mainland-only videos stay
  unavailable from a US exit.
- Login/cookie-based Bilibili subtitles, which remain on the browser-companion path.
- Renaming the `youtube-egress` unit, paths or files.
