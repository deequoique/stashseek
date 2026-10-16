# Design: route all server-side video fetches through `US_VIDEO_PROXY_URL`

## Overview

The worker gets one loopback proxy setting, `US_VIDEO_PROXY_URL`, defined as "the US
residential egress". Every server-side video fetch uses it: YouTube metadata/subtitles,
Bilibili metadata/subtitles, and the ASR audio download for both platforms. Only yt-dlp and
bounded-fetch **child processes** get the proxy environment; the worker process and every
other dependency stay direct. The existing tinyproxy unit (`notebook-agent-youtube-egress`)
keeps its name and gains the Bilibili hosts in its filter.

## 1. Configuration (`app/config.py`)

- Rename the field `youtube_proxy_url` → `us_video_proxy_url`, read from
  `US_VIDEO_PROXY_URL`.
- Rename `_validate_youtube_proxy_url` → `_validate_us_video_proxy_url`. The rules are
  unchanged: `http` scheme, host `127.0.0.1`/`localhost`, an explicit port 1-65535, no
  credentials/path/query/fragment/whitespace. Error text names `US_VIDEO_PROXY_URL`.
- **Old name fails loudly.** If `YOUTUBE_PROXY_URL` is present in the environment,
  `Settings` raises
  `ValueError("YOUTUBE_PROXY_URL was renamed to US_VIDEO_PROXY_URL; move the value and remove the old variable")`.
  An empty value counts as present. This stops a release from silently dropping the proxy
  and going direct. On deploy, the worker refuses to start and the deploy health check rolls
  back instead of quietly degrading.
- No alias and no fallback. The user asked for a full rename ("全改成").

## 2. Shared child-proxy helper (`app/connectors/proxy_env.py`, new)

The logic currently private to `YouTubeConnector` moves into the new module, and both
connectors call it:

```python
def build_proxy_environment(proxy_url: str | None) -> dict[str, str] | None
    # copy of os.environ; drop ALL_PROXY/all_proxy; set HTTP(S)_PROXY upper+lower;
    # NO_PROXY/no_proxy = "127.0.0.1,localhost,::1"; never mutates os.environ
def classify_proxy_failure(stderr: object, *, prefix: str) -> str | None
    # "<prefix>_proxy_authentication_failed" | "<prefix>_proxy_timeout"
    # | "<prefix>_proxy_unavailable" | None (marker lists moved verbatim from youtube.py)
```

YouTube behavior and classification strings stay byte-for-byte the same (`youtube_*`).
The existing YouTube tests must pass unchanged, apart from the keyword rename where
`Settings` is involved.

## 3. Bilibili connector (`app/connectors/bilibili.py`)

- `BilibiliConnector(..., proxy_url: str | None = None)`. When set, `_run_metadata` passes
  `env=build_proxy_environment(proxy_url)` (a fresh copy per call). When unset, nothing
  changes: no `env` kwarg is passed.
- Fail closed: a proxied failure is never retried without the proxy.
- Classification order in `_classify_failure`:
  1. rate limit / risk control: existing markers plus `412` / `precondition failed` →
     `bilibili_rate_limited`;
  2. with a proxy configured, `classify_proxy_failure(..., prefix="bilibili")`;
  3. otherwise `bilibili_fetch_failed`.
  A timeout with a proxy configured → `bilibili_proxy_timeout`; without one, it stays
  `bilibili_fetch_timeout`.
- Raw stderr, proxy values and URLs never appear in exception text, the same as today.
- Subtitles stay inline (yt-dlp `data`), so Bilibili needs no bounded-fetch child.

## 4. Wiring

- `app/ingest/tasks.py` `_connector()`: pass `proxy_url=settings.us_video_proxy_url` to
  **both** `BilibiliConnector` and `YouTubeConnector`.
- `app/ingest/submission.py`: `YouTubeConnector()` is used only for `match()`. No change.
- `app/asr/audio.py` (dev only): read `settings.us_video_proxy_url`. The Bilibili branch
  builds `BilibiliConnector(proxy_url=...)` and uses its child environment the same way the
  YouTube branch does. `_timeout_code` and `_raise_for_failure` gain the Bilibili proxy
  codes. The docstring line "Bilibili never uses a proxy" is removed. `_child_environment_without_secrets`
  still strips provider keys from whichever base environment is used.

## 5. Egress filter and logging

- Server filter `deploy/youtube-egress/tinyproxy.filter` and the home helper's embedded
  filter (`scripts/youtube-home-egress`) use the same seven anchored ERE rules:

  ```text
  (^|\.)youtube\.com$
  (^|\.)googlevideo\.com$
  (^|\.)bilibili\.com$
  (^|\.)hdslb\.com$
  (^|\.)bilivideo\.com$
  (^|\.)bilivideo\.cn$
  ^upos-[a-z0-9-]+\.akamaized\.net$
  ```

  The first three Bilibili hosts are measured (see `research/bilibili-egress-hosts.md`).
  `hdslb.com` covers anonymous subtitle JSON; `bilivideo.*` covers non-Akamai audio CDN
  regions. The Akamai rule is restricted to Bilibili's `upos-` mirror prefix, so other
  Akamai tenants are not opened.
- `ConnectPort 443` and `FilterDefaultDeny Yes` stay as they are.
- `deploy/youtube-egress/tinyproxy.conf.example` switches `LogLevel Warning` →
  `LogLevel Notice`, which logs filter refusals (`Proxying refused on filtered domain "…"`)
  without logging allowed connections. The home helper gets the same level.
- The unit, `/etc/notebook-agent/youtube-egress/` paths and AppArmor override keep their
  names (D-names). The runbook states that the "youtube-egress" unit serves every video
  platform.

## 6. Documentation and specs

- Runbook `docs/operations/production/youtube-residential-egress.md`: retitle the content
  to cover all video platforms, rename the variable, and add an **"升级到
  US_VIDEO_PROXY_URL / B 站"** section for the next dev→main release:
  1. reinstall the filter from the release;
  2. `sed` `LogLevel Warning` → `Notice` in the credential config without printing it;
  3. restart the egress unit;
  4. in `notebook-agent.env` replace `YOUTUBE_PROXY_URL=` with `US_VIDEO_PROXY_URL=`
     **before** approving the Production gate;
  5. after deploy, run a Bilibili canary through `BilibiliConnector` and grep the journal
     for filter refusals.
- `docs/operations/production/youtube-home-egress.md`, `docs/reference/configuration.md`,
  `docs/explanation/ingestion-and-retrieval.md`, `.env.example`, and the comment in the
  tinyproxy example: rename the variable and say it covers YouTube and Bilibili.
- Specs: `youtube-connector.md` gets the rename and the shared-helper note. `bilibili-connector.md`
  gets a proxy contract (env, classification, fail-closed, 412). `asr-transcription.md`
  drops "Bilibili never uses a proxy".
- Archived Trellis tasks are history and are not edited.

## 7. Compatibility and rollout

- Production keeps running the old code with `YOUTUBE_PROXY_URL` until the next full
  dev→main release (D-release). At that release the operator must rename the env variable
  before the gate. Otherwise the new worker fails fast with the rename message and the
  deploy rolls back. That is loud and safe, and needs no data migration.
- Rollback: redeploy the previous release and restore `YOUTUBE_PROXY_URL`. Restoring the
  previous filter file is optional, because extra allowed hosts are harmless to the old code.
- Proxy bandwidth: commit `1a81b4c` (another session) already caps ASR audio at 40-72 kbps
  (Bilibili format 30216, ~65 kbps), so proxied Bilibili ASR is ~30 MB/h. Keep that selector.

## 8. Key decisions

| ID | Decision | Source |
| --- | --- | --- |
| D-release | dev only; ship with the next full dev→main merge | user |
| D-name | `US_VIDEO_PROXY_URL`, full rename; the old name is a startup error | user (rename), design (fail loudly) |
| D-names | keep `youtube-egress` unit/paths/AppArmor names | design, avoids touching prod paths |
| D-412 | HTTP 412 → `bilibili_rate_limited` (label only; retry policy unchanged) | design |
| D-bitrate | ASR bitrate cap already landed in 1a81b4c; keep `AUDIO_FORMAT_SELECTOR` as is | other session |
| D-country | one US exit for all platforms; no per-domain upstream | design, follow-up |
