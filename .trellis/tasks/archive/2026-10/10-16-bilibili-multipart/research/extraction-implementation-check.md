# Extraction implementation self-check

2026-10-16. This is implementation self-check evidence, not the independent
Trellis review or deployment approval. Existing unrelated WIP is preserved.

## Implemented boundary

- `app/connectors/bilibili_extract.py` registers only `PinnedBiliBiliIE` with
  `auto_init=False`; the isolated entry point also sets `YTDLP_NO_PLUGINS=1`
  because the installed `YoutubeDL` API otherwise loads plugin modules even
  without auto initialization. CLI config/cookies/cache are not adopted.
- Actual `_get_subtitles(video_id,cid)` calls retain the installed base
  implementation. The hook verifies BV/CID before calling it and records proof
  only after it returns. No-caption still requires that actual hook. Play-info
  observations also cannot disagree. Missing proof/dependency-hook changes,
  playlists, URL redirects and fragment/multi-video outputs fail closed.
- `writesubtitles=True`, `extract_info(download=False,process=False)` enables
  real caption discovery without subtitle/media-file processing. Only bounded
  projected metadata and inline SRT cross the child boundary. Provider output,
  media/subtitle URLs and raw logs do not. Parent process enforces a wall
  deadline plus a shared stdout/stderr byte cap and kills/reaps on failure.
- Explicit `fetch_part` produces source-typed `ItemMeta` evidence and canonical
  `BV?p=current` URLs. Its title uses the CID-aware part descriptor, not the
  extractor's independently cached ordinal title.
- Worker resolves the immutable CID before extraction, checks actual metadata
  evidence and canonical URL, then rechecks the current map. All checks precede
  metadata/raw/segment writes. Row/dispatch locks revalidate owner, pin,
  deletion/purge and task ownership before writes. Pin is never rebound on
  retry. Source failures use permanent fixed `source_changed` or
  `source_unavailable` codes; ordinary timeouts keep existing transient behavior.
- Legacy ordinary URL ingestion retains its previous local P1 semantics and
  nullable evidence. Generic timestamps preserve a single validated P and local
  time. Trusted exact-CID citation matching uses private server-hydrated item
  identity; ordinals/model-authored browser resolver URLs cannot grant scope.

## Offline evidence

Command (dummy environment values, no database connections):

```sh
DATABASE_URL='postgresql+psycopg://unused/unused' REDIS_URL='redis://localhost/0' \
  .venv/bin/python -m pytest -q \
  tests/test_bilibili_multipart_extract.py tests/test_bilibili_url.py \
  tests/test_tasks.py tests/test_browser_capture.py \
  tests/test_knowledge_services.py tests/test_agent_runtime.py
```

Result: **262 passed in 3.20s**. `git diff --check` passed.

The fixture preserves real installed `_real_extract` and base `_get_subtitles`,
replaces provider I/O only and asserts any unexpected fetch. Cases include P2
inline SRT/no-caption; unchanged public page maps but mismatched webpage CID;
missing real subtitle hook; metadata-only options and no created files/media
processing; both child output-stream bounds and wall deadline; missing/conflicting
proof; base-BV URL erasure; postmap reorder/removal; deletion, changed pin or lost
dispatch ownership before writes; retry resolving the same CID at a new ordinal;
equal caption bytes in separate CID artifact paths; generic P/time preservation;
trusted exact-CID scope versus forged source/resolver metadata.

An initial run without dummy configuration values failed because required
`POSTGRES_PASSWORD` was absent. After explicitly setting a dummy `DATABASE_URL`
and `REDIS_URL`, the above tests pass without any external service. No production
configuration/secrets were loaded or printed.

This self-check did not execute a live extraction/ingestion, database migration,
real media/subtitle download, deployment or Git mutation. Parent owns live
read-only map evidence, isolated PostgreSQL integration and the independent review.
