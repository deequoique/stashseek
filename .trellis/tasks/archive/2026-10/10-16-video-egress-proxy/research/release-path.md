# Release path analysis (2026-10-16)

## Branch state

- `dev` is 18 commits ahead of `origin/main`, and `main` has its own release commits
  (PR #25, PR #26). A full `dev` → `main` merge conflicts in `app/ingest/tasks.py` and
  `.trellis/spec/backend/database-guidelines.md`, and it would also ship the unreleased ASR
  pipeline, the extension UI refactor, worker diagnostics and other work.
- PR #26 precedent: a `release/<name>` branch cut from `origin/main` that cherry-picks only
  the task's commits, merged with a merge commit. Every push to `main` triggers
  `deploy-production`, which waits at the owner-only `Production` gate.

## Touched files: main vs dev

| File | main vs dev | Note |
| --- | --- | --- |
| `app/connectors/bilibili.py` | identical | proxy support lands cleanly on both |
| `app/connectors/youtube.py` | identical | shared proxy-env helper can be extracted on both |
| `app/connectors/bounded_fetch.py` | identical | |
| `scripts/youtube-home-egress` | identical | filter update is portable |
| `deploy/youtube-egress/tinyproxy.filter` | identical | |
| `tests/test_youtube.py` | dev +26 lines (AppArmor test, 6270972) | new tests go in a separate test file to avoid conflicts |
| `app/config.py` | dev +124/-2 (ASR settings) | the `youtube_proxy_url` field and validator have identical context |
| `app/ingest/tasks.py` | dev +273/-19 (ASR dispatch) | `_connector()` connector construction has identical context (main L211-221, dev L274-284) |
| `app/asr/audio.py` | dev only | Bilibili audio proxy change exists only on dev |
| runbook / spec | dev ahead by 6270972 (AppArmor) | 6270972 is portable (docs/deploy/tests only) |

## Shared-checkout hazard

- The `dev` working tree currently has uncommitted changes from other sessions in
  `app/config.py` and `app/ingest/tasks.py`. A path-based commit of those files would sweep
  those foreign hunks into this task's commit. `git merge`, `cherry-pick` and `ff-only` into
  the dev checkout also refuse while those files are dirty.
- Implementation therefore runs in a separate git worktree on a feature branch cut from
  `dev`, matching the repo rule that worktrees branch from dev. Landing on `dev` must add only
  this task's hunks. Either wait until those files are clean in the shared checkout, or build
  the dev commit with a temporary index (`GIT_INDEX_FILE` + `git apply --cached` +
  `commit-tree` + `update-ref`), apply the same patch to the working tree, and re-sync only
  this task's paths in the shared index. That last step is allowed only when those paths have
  no staged foreign changes.

## Commit split that keeps main portable (not chosen)

> The user chose D-release B on 2026-10-16: land on dev only and ship with the next full
> dev→main merge. This option is kept for reference only.

1. Portable commit: shared proxy env helper, Bilibili connector proxy plus classification,
   config alias, `_connector()` wiring, filters (server and home helper), tinyproxy
   `LogLevel Notice`, new tests in their own file, runbook and spec.
2. dev-only commit: `app/asr/audio.py` Bilibili audio through the proxy, plus its tests.

A main release then cherry-picks 6270972 (AppArmor docs, not yet on main) and commit 1 onto
`release/video-egress-proxy` from `origin/main`. Commit 2 waits for the ASR release.

## Production rollout (after the Production gate)

- The worker restarts on deploy, so new connector code picks up the proxy. No env edit is
  needed if `YOUTUBE_PROXY_URL` stays accepted.
- On the server the operator must reinstall `/etc/notebook-agent/youtube-egress/tinyproxy.filter`
  from the release and switch `LogLevel` to `Notice` in the credential-bearing config without
  printing it, then restart `notebook-agent-youtube-egress`. The canary is one public
  Bilibili video through `BilibiliConnector` on the server, with a check for
  `Proxying refused on filtered domain` lines in the journal to catch exit-region CDN hosts.
