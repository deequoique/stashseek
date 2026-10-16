# Implement: US_VIDEO_PROXY_URL for all video fetches

## 0. Workspace (before any edit)

- Work in a separate worktree cut from `dev` (repo rule; the shared checkout has foreign
  uncommitted edits in `app/config.py`, `app/ingest/tasks.py`, `tests/test_tasks.py`,
  `.env.example`, `docs/reference/configuration.md`, `docs/explanation/ingestion-and-retrieval.md`):
  `git worktree add -b feat/video-egress-proxy <path> dev`.
- Implementation is dispatched to `trellis-implement` with `model: sonnet`; review/check by
  `trellis-check`; the main session reviews, verifies, and lands the commit.
- Test DB rule for every pytest run (main session and sub-agents):

  ```bash
  export DATABASE_URL="$(grep '^DATABASE_URL=' /Volumes/PeeB/projects/notebook-agent/.env.neon-test | cut -d= -f2- | tr -d "\"'" | sed -E 's#^postgres(ql)?(\+[a-z0-9]+)?://#postgresql+psycopg://#')"
  case "$DATABASE_URL" in postgresql+psycopg://*@ep-falling-fog-*) ;; *) echo "refusing: not the test branch"; exit 1;; esac
  ```

## 1. Ordered checklist

1. **Shared helper**: create `app/connectors/proxy_env.py` with
   `build_proxy_environment` and `classify_proxy_failure` (design §2). Move the logic and
   marker tuples out of `app/connectors/youtube.py`; `YouTubeConnector` delegates with
   `prefix="youtube"`. No behavior change.
2. **Config rename**: `app/config.py` field `us_video_proxy_url` (env
   `US_VIDEO_PROXY_URL`), validator `_validate_us_video_proxy_url`, and the
   `YOUTUBE_PROXY_URL`-present startup error (design §1).
3. **Bilibili connector**: `proxy_url` parameter, child `env`, classification incl. 412 and
   proxy codes, proxied timeout code (design §3).
4. **Wiring**: `app/ingest/tasks.py` `_connector()` passes `settings.us_video_proxy_url`
   to both connectors; `app/asr/audio.py` reads the new field and proxies Bilibili audio
   (design §4).
5. **Filters/logging**: update `deploy/youtube-egress/tinyproxy.filter` and the
   heredoc filter in `scripts/youtube-home-egress` to the seven rules; `LogLevel Notice` in
   `deploy/youtube-egress/tinyproxy.conf.example` and the helper (design §5).
6. **Tests**:
   - Rename `youtube_proxy_url` → `us_video_proxy_url` in `tests/test_ingest_submission.py`,
     `tests/test_tasks.py`, `tests/test_asr_audio_download.py`, `tests/test_asr_transcriber.py`
     (and error-message matches → `US_VIDEO_PROXY_URL`).
   - New `tests/test_video_egress_proxy.py`:
     - `Settings` reads `US_VIDEO_PROXY_URL`; rejects the same invalid shapes as before;
       raises the rename error when `YOUTUBE_PROXY_URL` is set (also when empty).
     - `build_proxy_environment` parity with the previous YouTube behavior (upper/lower
       vars, ALL_PROXY removed, loopback NO_PROXY, `os.environ` untouched).
     - Bilibili: proxied metadata run receives the env; unproxied run passes no `env`;
       412 → `bilibili_rate_limited`; proxy refused/407/timeout → `bilibili_proxy_*`;
       no unproxied retry after a proxied failure; exception text has no stderr/proxy value.
     - `_connector()` gives the proxy to both connectors.
     - Server filter lines == helper filter lines == the seven design rules; `LogLevel
       Notice` in both; ERE sanity: allows `www.bilibili.com`, `api.bilibili.com`,
       `upos-hz-mirrorakam.akamaized.net`, `upos-sz-mirrorcos.bilivideo.com`,
       `aisubtitle.hdslb.com`; rejects `evil-bilibili.com`, `bilibili.com.evil.net`,
       `example.akamaized.net`, `akamaized.net`.
   - `tests/test_asr_audio_download.py`: Bilibili download gets the proxy env when set,
     and Bilibili proxy failure/timeout codes.
   - Update `tests/test_youtube.py::test_residential_egress_filter_matches_home_egress_hosts`
     for the seven-rule list.
7. **Docs/specs** per design §6 (runbook upgrade section; home-egress runbook;
   configuration reference; ingestion explanation; `.env.example`; tinyproxy example
   comment; `youtube-connector.md`, `bilibili-connector.md`, `asr-transcription.md`).

## 2. Validation (run in the worktree)

```bash
uv run pytest -q tests/test_video_egress_proxy.py tests/test_bilibili_url.py tests/test_youtube.py \
  tests/test_ingest_submission.py tests/test_tasks.py tests/test_asr_audio_download.py \
  tests/test_asr_transcriber.py tests/test_production_caddy_deployment.py
uv run pytest -q                      # full suite with the test-branch DATABASE_URL above
uv run ruff check app tests           # only new findings matter; pre-existing ones noted
sh -n scripts/youtube-home-egress
grep -rn "YOUTUBE_PROXY_URL\|youtube_proxy_url" app tests docs scripts deploy .env.example .trellis/spec
#   → only the rename-error message/test and runbook upgrade text may remain
```

Runtime check (main session): local tinyproxy with the new example config + fake
upstream (as in the YouTube egress verification): Bilibili and YouTube hosts forwarded with
auth; look-alike hosts and non-443 refused; refusals visible at `LogLevel Notice`.
Optional live check from the Mac: `BilibiliConnector(proxy_url=<local logging tinyproxy>)`
fetch_meta on `BV1uv411q7Mv`.

## 3. Landing on dev (shared checkout)

1. Commit in the worktree (two commits are fine: code+tests, docs/specs). Rebase onto the
   latest `dev`.
2. If none of the touched files are dirty in the shared checkout → `git -C <shared> merge
   --ff-only feat/video-egress-proxy`.
3. Otherwise, without touching foreign edits: for each feature commit, build the dev commit
   on a temporary index (`GIT_INDEX_FILE=$(mktemp)`; `git read-tree HEAD`;
   `git apply --cached <patch>`; `git write-tree`; `git commit-tree -p HEAD`), then
   `git update-ref refs/heads/dev <new> <old>`, apply the same patch to the shared working
   tree (`git apply --3way` if needed), and re-sync only this task's paths in the shared
   index with `git reset -q -- <paths>` — **only if** `git diff --cached --name-only` shows
   none of those paths staged by another session; else stop and ask.
4. Verify: `git log -1 --stat dev` lists only this task's files; `git status` in the shared
   checkout shows the foreign edits unchanged; rerun the targeted tests from the shared
   checkout.
5. Do not push or merge to main (D-release).

## 4. Risky files / rollback points

- `app/config.py`, `app/ingest/tasks.py` (foreign-dirty in shared checkout) — land via §3.3.
- `app/connectors/youtube.py` refactor — guarded by existing YouTube tests passing unchanged.
- Rollback: `git revert` the landed commits on dev; nothing touches production until the
  next dev→main release.

## 5. Before `task.py start`

- [ ] User approved the final planning summary.
- [ ] `implement.jsonl` / `check.jsonl` curated with real entries.
