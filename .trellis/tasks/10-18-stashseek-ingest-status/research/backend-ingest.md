# Research: backend ingest pipeline (state / error-code writers, ASR path, events, migrations, tests)

- **Query**: Read-only research of the backend ingest pipeline so `design.md` for task `10-18-stashseek-ingest-status` can be written (seven questions, see sections 1-7).
- **Scope**: internal (repo at `dev` 7f5a033, working dir `/Volumes/PeeB/projects/notebook-agent`)
- **Date**: 2026-10-18
- **Method**: static reading and grep only. No pytest, no DB access, no alembic command was run. The Alembic head was computed by a throw-away script that parses `revision`/`down_revision` from `migrations/versions/*.py`.

All paths are relative to the repo root. `tasks.py` = `app/ingest/tasks.py`, `library.py` = `app/web/library.py`, `submission.py` = `app/ingest/submission.py`, `capture_sub.py` = `app/browser_capture_submission.py`.

---

## 0. Pipeline map (orientation)

```
admission (one of 5 dispatch creators)           -> item.state=pending, dispatch.state=pending
  publish fetch_text_task (queue "ingest")        -> dispatch.state=enqueued (task_id set)
fetch_text_task -> process_dispatch               tasks.py:598-603, 1216-1265
  _claim_dispatch   pending|enqueued -> running   tasks.py:1330-1398
  process_item(asr_handoff=True)                  tasks.py:354-560, 1268-1271
     -> terminal item state ready | needs_extension | needs_asr   -> _complete_dispatch (dispatch completed, event completed)
     -> "asr_pending" -> _handoff_dispatch_to_asr (running -> enqueued, task_id NULL) -> publish transcribe_dispatch_task (queue "asr")
     -> exception  -> _mark_dispatch_failed (dispatch failed, item failed, event failed)
transcribe_dispatch_task -> process_dispatch(processor=_asr_processor)   tasks.py:635-653, 606-632
```

Dispatch creators (all insert `IngestDispatch(... state="pending")`): `capture_sub.py:299`, `library.py:455` (retry), `asr_backfill.py:162`, `submission.py:702` (new save / re-save), `submission.py:888` (Agent/MCP `retry_item`).
ContentItem creators: `tasks.py:328` (`create_item`, CLI/sync), `capture_sub.py:265`, `submission.py:689`. There is no raw SQL against `content_item` in `app/` (grep for `content_item` in `app/` only hits `app/models.py` and docstrings/field names in `app/ingest/media.py`).

---

## 1. Every writer of `ContentItem.state / fail_reason / text_source` and `IngestDispatch.state / error_code`

### 1.1 `ContentItem.state` and `ContentItem.fail_reason`

| # | Writer (file:line) | from -> to | `fail_reason` / error set |
|---|---|---|---|
| 1 | `create_item` `tasks.py:328-337` | (new) -> `pending` | none. Soft-deleted restore branch (`:313-327`) does not touch state. |
| 2 | `_submit_reference` `submission.py:680-681` | existing `failed` or `pending` -> `pending` | `fail_reason=None`. Guards: existing state must be in `{pending, failed}` (`:657`), latest dispatch must not be pending/enqueued/running/completed (`:665-673`), quota enforced (`:674-678`). Any other existing state returns `already_exists` (`:657-658`). (A soft-deleted item restored from the recycle bin returns `restored` early unless its state is `failed`/`pending`, `:633`.) |
| 3 | `_submit_reference` `submission.py:689-697` | (new) -> `pending` | none |
| 4 | `retry_item` `submission.py:875-876` | `pending` (with latest dispatch `failed/queue_unavailable`) -> `failed` | `fail_reason="queue_unavailable"` (split-state repair) |
| 5 | `retry_item` `submission.py:886-887` | `failed` -> `pending` | `fail_reason=None`. Requires `item.state == "failed"` (`:877`) and no active dispatch (`:879-880`). |
| 6 | `_repair_retry_split_state` `submission.py:977-979` | `pending` -> `failed` | `queue_unavailable` |
| 7 | `_mark_retry_publish_failed` `submission.py:1003-1005` | `pending` -> `failed` | `queue_unavailable` (same txn as dispatch -> failed, `:1001-1002`) |
| 8 | `ContentLibraryService.retry` `library.py:453-454` | item that projects as `failed` -> `pending` | `fail_reason=None`. Requires `projection.state == "failed"`, not archived, no active dispatch (`:441-444`). `needs_*` items are rejected here (`LibraryConflict("retry_unavailable")`). |
| 9 | `ContentLibraryService._set_dispatch` `library.py:690-696` | `pending` -> `failed` (only if dispatch is `pending`, `error_code == "queue_unavailable"`, item `pending`) | `queue_unavailable` |
| 10 | `_admit` (browser capture) `capture_sub.py:272` | (new) -> `pending` | none |
| 11 | `_admit` `capture_sub.py:285-286` | any non-`ready` existing item -> `pending` | `fail_reason=None`. `ready` returns `already_exists` early (`:248-257`); an active dispatch raises `capture_conflict` (`:240-247`). This is the "re-collect on the video page re-runs the same row" path, and it works for `failed`, `needs_extension`, `needs_asr` (and `no_text`) items. It also overwrites title/author/duration/chapters/cover/`raw_object_key`/`raw_format` from the browser metadata (`:287-298`). |
| 12 | `_mark_failed` `capture_sub.py:415-416` | (whatever) -> `failed` (only if dispatch still `pending`, `:406`) | `fail_reason=code`, code in `{capture_upload_failed (:145), queue_unavailable (:163)}` |
| 13 | `asr_backfill._admit` `asr_backfill.py:160-161` | `needs_asr` -> `pending` | `fail_reason=None`. Operator CLI only (`app/cli.py:19,184-188`). |
| 14 | `asr_backfill._publish_failed` `asr_backfill.py:215-216` | `pending` -> `needs_asr` | none (dispatch gets `queue_unavailable`, see 1.3) |
| 15 | `process_item` `tasks.py:384` | -> `fetching` (browser-capture branch) | none |
| 16 | `process_item` `tasks.py:396-398` | `fetching` -> `fetching`, returns `ASR_PENDING` (caption-less YouTube capture, ASR on) | none |
| 17 | `process_item` `tasks.py:399-402` | -> `needs_asr` (capture says no captions and handoff not eligible: ASR off, `asr_handoff=False`, or platform not in `_ASR_CAPTURE_PLATFORMS = {youtube}` e.g. `ntu_kaltura` and also `bilibili` captures) | no `fail_reason`; `text_source="none"` (`:400`) |
| 18 | `process_item` `tasks.py:435-436` | -> `fetching` (connector branch, after metadata is written) | none |
| 19 | `process_item` `tasks.py:439-442` | -> `needs_extension` (connector returned `NeedsExtension`) | none |
| 20 | `process_item` `tasks.py:452-454` | `fetching` -> `fetching`, returns `ASR_PENDING` (connector `NeedsASR`, `asr_handoff`, not pre-stored, `asr_enabled`, platform in `_ASR_PLATFORMS`) | none |
| 21 | `process_item` `tasks.py:455-457` | -> `needs_asr` (connector `NeedsASR` but no handoff eligibility) | none (`text_source` untouched, stays DB default `none`) |
| 22 | `process_item` `tasks.py:494-495` | -> `chunking` (committed) | none |
| 23 | `process_item` `tasks.py:552-559` | -> `embedding` (line 553) -> `ready` (557), `fail_reason=None` (558), single `db.commit()` at 559. **`embedding` is never committed on its own**; no other session can observe it. | clears `fail_reason` |
| 24 | `_mark_item_deleted` `tasks.py:580-589` | any -> `failed` (worker saw soft-delete / purge claim) | `item_deleted` |
| 25 | `_claim_dispatch` `tasks.py:1362-1366` | any -> `failed` (item deleted at claim time) | `item_deleted` (+ completion event `failed/item_deleted`) |
| 26 | `_complete_dispatch` `tasks.py:1464-1468` | any -> `failed` (item missing/deleted/`process_state=="deleted"`) | `item_deleted` |
| 27 | `_complete_dispatch` `tasks.py:1483-1486` | `fetching` -> `needs_asr` (`converge_item_state`, only used when the ASR handoff publish failed, `tasks.py:1317-1324`) | none |
| 28 | `_mark_dispatch_failed` `tasks.py:1988-1996` | any non-`ready` -> `failed` | `fail_reason=error_code` per mapping `tasks.py:1916-1921`: `AsrError.error_code`, `transient_fetch_failed`, `ingest_too_large`, else `ingestion_failed`. If `item.state == "ready"` and dispatch active, the dispatch is converged to `completed` instead (`:1966-1986`). |
| 29 | `_mark_failed` `tasks.py:2036-2047` (sync `ingest_url` path only) | -> `failed` | `transient_fetch_failed` / `ingest_too_large` / `ingestion_failed`. **Does not map `AsrError`** (sync path never does ASR). |

`no_text` is never written by any code in `app/` (grep `no_text` in `app`, `migrations`: only the enum definition `app/models.py:58-62`, state-filter lists `app/agent/management.py:557-558` and `app/agent/agent_tools/actions.py:125-126`, and the read-side projection `library.py:151` / `library_routes.py:132`; tests only reference it as a fixture value, `tests/test_web_library.py:102`).

`needs_extension` has exactly one producer: a connector returning `NeedsExtension` (`tasks.py:439-442`); the only connector that does is Bilibili (`app/connectors/bilibili.py:355-356` login-only subtitles; `:360-361` track without inline data). `AsrFallbackConnector` passes it through unchanged (`app/asr/connector.py:35-36`).

### 1.2 `ContentItem.text_source` (native PG enum `text_source`: `official_cc, auto_caption, asr_whisper, article_text, none`, `app/models.py:53-56`)

- `tasks.py:400`: `"none"` (only in the capture-unavailable -> `needs_asr` branch).
- `tasks.py:492`: `item.text_source = result.source`, written together with `state="chunking"` (`:494`). Sources: connector `official_cc` / `auto_caption` (`youtube.py:259-279`, `bilibili.py:319-323,367`), browser capture `capture.caption_source` (`tasks.py:415-419`), ASR always `asr_whisper` regardless of provider (`app/asr/transcriber.py:122`; confirmed by `.trellis/spec/backend/asr-transcription.md` "text_source is asr_whisper for every ASR provider").
- Never reset on retry or failure (retry flips only `state` and `fail_reason`: `library.py:453-454`). `article_text` and `no_text`-related values have no writer.
- Server default `none` (`app/models.py:528-530`).

### 1.3 `IngestDispatch.state` / `IngestDispatch.error_code` (both plain `Text`, no CHECK; `app/models.py:742-746`, see also migration `c7e8a91b2d34`)

| Writer (file:line) | from -> to | `error_code` |
|---|---|---|
| all creators (see section 0) | (new) -> `pending` | none |
| `ContentLibraryService._set_dispatch` `library.py:686-688` (called `:485-490` / `:492-497`) | `pending` -> `enqueued` (task_id set) or `failed` | `queue_unavailable` on publish exception |
| `_submit_reference` publish `submission.py:757-761`, `:774` via `_set_dispatch_state` `submission.py:1172-1202` | `pending` -> `failed` / `enqueued` | `queue_unavailable`. **Item stays `pending`** on this path (only the dispatch fails); `retry_item` repairs it later (`:869-876`). |
| `retry_item` publish `submission.py:954`, `_mark_retry_publish_failed` `:1001-1002` | `pending` -> `enqueued` / `failed` | `queue_unavailable` |
| `capture_sub._mark_enqueued` `:388`, `_mark_failed` `:417-418` | `pending` -> `enqueued` / `failed` | `queue_unavailable` or `capture_upload_failed` |
| `asr_backfill._mark_enqueued` `:188`, `_publish_failed` `:212-213` | `pending` -> `enqueued` / `failed` | `queue_unavailable` |
| `channels/identity.py:420-422` (account merge, loser item's `pending`/`enqueued` dispatches; loser item is deleted at `:424`) | -> `failed` | `merged_duplicate` |
| `_claim_dispatch` `tasks.py:1378` | `pending|enqueued` -> `running` (sets `task_id`, `:1379-1380`). Returns `None` (no-op "duplicate") if dispatch/item missing or task_id mismatch (`:1345-1361`). | none |
| `_claim_dispatch` `tasks.py:1363-1364` | -> `failed` (item deleted) | `item_deleted` |
| `_release_dispatch_for_retry` `tasks.py:1423` | `running` -> `enqueued` (task_id retained) on `TransientFetchError` | none |
| `_handoff_dispatch_to_asr` `tasks.py:1304-1306` | `running` -> `enqueued`, `task_id=NULL`, `updated_at=now` | none |
| `_complete_dispatch` `tasks.py:1489-1490` | `running` (or `enqueued` for convergence) -> `completed` | **always `None`** (hard-coded `:1490`, event gets `error_code=None` `:1498`) |
| `_complete_dispatch` `tasks.py:1464-1465` | -> `failed` | `item_deleted` |
| `_mark_dispatch_failed` `tasks.py:1974-1975` | `running|enqueued` -> `completed` (item already `ready`) | `None` |
| `_mark_dispatch_failed` `tasks.py:1988-1989` | -> `failed` | `AsrError.error_code` / `transient_fetch_failed` / `ingest_too_large` / `ingestion_failed` (`:1916-1921`). A second failure hook for an already-`failed` dispatch reuses the original code and event (`:1942-1963`). |

`needs_extension` / `needs_asr` terminal outcomes end the dispatch as `completed` with `error_code=NULL`, and the outbox event as `completed` with `item_state=needs_*`, `error_code=NULL` (`tasks.py:1487-1499`; DB CHECK, section 4).

### 1.4 Read-side precedence (how a failed reason is chosen)

`project_lifecycle` (`library.py:145-183`) order: archived -> `ready` (item.state) -> `needs_action` for `{needs_extension, needs_asr, no_text}` with `error=None` (`:151-153`) -> `failed` if latest dispatch is `failed` OR item is `failed` (`:154-158`; error is `_safe_error(latest.error_code)` when the latest dispatch is failed, else `_safe_error(item.fail_reason)`) -> `processing` if latest dispatch `running` or item in `{fetching, chunking, embedding}` (`:159-165`) -> `queued` if item `pending` and latest `pending|enqueued` (`:166-171`) -> else `failed/missing_dispatch` (`:172-174`).
`retry` action is offered only when `state == "failed"` and latest dispatch is not active (`:177-180`).
So: two stored sources of a failure reason (`dispatch.error_code`, `item.fail_reason`), normally equal; **`needs_*` has no stored reason anywhere** (`fail_reason` is None, `dispatch.error_code` is None).

---

## 2. Error codes: origin, persistence, public survival

### 2.1 Where each code originates and what is actually persisted

| Origin | Specific codes | What reaches `dispatch.error_code` / `item.fail_reason` |
|---|---|---|
| **Connector transient failures** (`TransientFetchError`, a `RuntimeError` subclass, `app/connectors/base.py:10-11`) | YouTube: `youtube_rate_limited` (`youtube.py:93-95`, subtitle child exit code `:179,184-185`), `youtube_proxy_unavailable/_timeout/_authentication_failed` (`youtube.py:96-99,131-137,167-172,180-185` via `app/connectors/proxy_env.py:55-69`), `youtube_fetch_failed` (`:100`), `youtube_fetch_timeout` (`:136`), `subtitle_fetch_timeout` (`:170`), `subtitle_fetch_failed` (`:187`), free-text parse errors (`:40,42,49,60,196,272`). Bilibili: `bilibili_rate_limited` (HTTP 429/412/-352/risk control, `bilibili.py:185-199`), `bilibili_proxy_*` (`:200-203`), `bilibili_fetch_failed` (`:204`), `bilibili_fetch_timeout` / `bilibili_proxy_timeout` (`:231-237`), free-text SRT/JSON errors (`:55,76,78,247,251`). Empty transcript `EmptySuccess(TransientFetchError)` (`app/ingest/validate.py:24,51-57`). | **Only `transient_fetch_failed`, and only after retries are exhausted.** `process_dispatch` catches `TransientFetchError`, releases the dispatch (`_release_dispatch_for_retry`) and re-raises a *new* `TransientFetchError("transient_fetch_failed") from None` (`tasks.py:1233-1239`); the original code string is discarded. Celery autoretry: `fetch_text_task` `autoretry_for=(TransientFetchError,)`, `max_retries=5`, `retry_backoff=8`, `retry_backoff_max=600`, `retry_jitter=True` (`tasks.py:598`; identical on `transcribe_dispatch_task` `:635-645`). After exhaustion `IngestTask.on_failure` (`:592-596`) -> `_mark_dispatch_failed` -> `transient_fetch_failed` (`:1918`; test `tests/test_tasks.py:177-186`). While retrying, item stays `fetching`, dispatch alternates `running`/`enqueued` -> the UI sees `processing`. **`*_rate_limited` / `*_proxy_*` / `*_fetch_failed` never reach the DB, API or completion events.** |
| **ASR audio download transients** (`app/asr/audio.py`) | the same `youtube_*`/`bilibili_*` rate-limit + proxy codes (`:58-74,189,215-221`) | same collapse to `transient_fetch_failed` (they are `TransientFetchError`; `app/asr/transcriber.py:168-173` only records them in a diagnostic) |
| **`IngestLimitExceeded`** (`ValueError`, `error_code="ingest_too_large"`, `app/ingest/validate.py:28-34`) | raised at `tasks.py:461-467` (raw bytes/cues/chars), `:473-474` and `:540-541` (segment count), `:517-518` (embedding char budget), `youtube.py:177`, `bilibili.py:363-364`, `app/asr/transcriber.py:116-118` (canonical transcript too large) | `ingest_too_large` (`tasks.py:1240-1244`, mapping `:1919`). Terminal, not retried. Survives the whitelist. |
| **`AsrError`** (`app/asr/types.py:25-49`, only these six codes) | `asr_not_configured` (`transcriber.py:192-193`), `asr_too_long` (`:194-195`, `duration_sec > asr_max_duration_seconds`, default 14400), `asr_audio_unavailable` (`audio.py:136,149,153,191,222,233,238`; `connector.py:39-40`), `asr_audio_too_large` (`audio.py:194-195,239-241`), `asr_provider_failed` (`transcriber.py:230-231,238-239`), `asr_no_speech` (`transcriber.py:103,241-243`, raised before `guard_transcript` so it is not retried) | the code itself on dispatch, item and event (`tasks.py:1245-1251`, `:1916-1917`). Terminal, never retried. |
| **Any other exception** | `LookupError("content item N not found")` (`tasks.py:359`), `ValueError("unsupported URL")` (`:298`), `ValueError("captured_transcript_invalid")` (`:408`), `TypeError` (`:459`), embedding-count mismatch (`:544`), `EmbeddingError` (`app/ingest/embed.py:26-233`, after the embedder's own 3-attempt retry), `ObjectStoreError`, DB errors | `ingestion_failed` (`tasks.py:1252-1256`, `:1920`). Behaviour of Celery's soft/hard time limit on `transcribe_dispatch_task` (`time_limit=3600, soft_time_limit=3480`, `:643-644`) w.r.t. `on_failure` was **not verified**. |
| **Admission / publish** | `queue_unavailable` | dispatch (and usually item), see 1.1 rows 4,6,7,9,12,14 and 1.3. `quota_exceeded` is **never persisted**: it is an immediate rejection (`library.py:446-452` -> `LibraryConflict("quota_exceeded")` -> HTTP 429 `app/api/library_routes.py:88-93`; capture path `capture_sub.py:132-133`; submission `submission.py:717-722,902-908`). |
| **Browser capture upload** | `capture_upload_failed` | `item.fail_reason`, `dispatch.error_code`, `capture.error_code` (`capture_sub.py:145,410-418`) |
| **Delete race** | `item_deleted` | `item.fail_reason`, `dispatch.error_code`, event `error_code` (`tasks.py:588,1364-1366,1464-1479`) |
| **Account merge** | `merged_duplicate` | dispatch only (`channels/identity.py:420-422`) |
| **Synthetic / read-time only** | `missing_dispatch` (`library.py:172-174`, never stored); `transcript_unavailable`, `transcript_invalid` (`app/web/transcript.py:63-170`, read-time `TranscriptError`, never stored on item/dispatch); `item_missing` (**no writer anywhere in `app/`**, only present in the whitelist `library.py:33`) | n/a |
| **Diagnostics-only** | `asr_unexpected_error` (`transcriber.py:174-175`), `completion_publish_failed`, `notification_enqueue_failed`, `broker_unavailable` (`tasks.py:186-198`) | never stored |

### 2.2 What survives `_safe_error` (`library.py:141-142`) and what collapses

`SAFE_ERROR_CODES` (`library.py:28-40`): `queue_unavailable, ingestion_failed, transient_fetch_failed, item_missing, missing_dispatch, transcript_unavailable, transcript_invalid, ingest_too_large` + the six `asr_*` codes (spread from `ASR_ERROR_CODES`).

- **Survive**: `queue_unavailable`, `transient_fetch_failed`, `ingest_too_large`, `ingestion_failed`, all six `asr_*`, synthetic `missing_dispatch`.
- **Collapse to `ingestion_failed` on read** (`_safe_error` default): `item_deleted`, `capture_upload_failed`, `merged_duplicate`, and anything unknown (e.g. `youtube_rate_limited` / `bilibili_rate_limited` if they were ever stored; they are not, see 2.1).
- `_safe_error` is applied in exactly two places: `project_lifecycle` failed branch (`library.py:154-158`) and `get_dispatch` (`library.py:521`).
- Agent/MCP inventory uses a separate, stricter projection: `_safe_error_code(item)` in `app/agent/management.py:239-246` returns `None` unless state is `failed`, and then **always** `ingestion_failed` (or `purge_failed`); `fail_reason` is never projected (`:244`).
- Completion-event consumers: `notifications.py:172-183` renders fixed Chinese messages keyed only by `(outcome, item_state)`; the event's `error_code` is carried into `NotificationTarget` (`notifications.py:781`) but not rendered.
- Diagnostics allow-lists that silently drop unlisted codes: `tasks.py:186-198` (`_completion_diagnostic`) and `app/diagnostics.py:62-90` (`_ERRORS`).
- The PRD's note "extension has `youtube_rate_limited`/`bilibili_rate_limited` copy but the backend whitelist lacks them" is accurate, and in addition those codes are never persisted today (they collapse to `transient_fetch_failed` before storage).

### 2.3 Outcomes that end as `needs_extension` / `needs_asr` / `no_text` instead of `failed`

- `needs_extension`: Bilibili connector login-only subtitle marker or subtitle without inline data (`bilibili.py:253-254,355-356,360-361`). Applies on both queues. With ASR on, login-only Bilibili subtitles still end `needs_extension` (the connector returns `NeedsExtension`, not `NeedsASR`).
- `needs_asr` producers: `tasks.py:399-402` (capture, no captions, not handoff-eligible: ASR off / sync caller / platform != youtube, i.e. `ntu_kaltura` **and bilibili** captures); `tasks.py:455-457` (connector `NeedsASR` but ASR off, `asr_handoff=False`, platform not in `_ASR_PLATFORMS`, or pre-stored); `tasks.py:616-619` (`_asr_processor` finds `asr_enabled` false at claim time -> plain `process_item`); `tasks.py:1483-1486` + `:1317-1324` (ASR queue publish failed -> converge); `asr_backfill.py:215-216` (backfill publish failed, item restored). Note that none of these leave a reason; the dispatch is `completed`.
- `no_text`: **never written today** (see 1.1).
- Everything else that produces no usable body is `failed` with a code (e.g. no speech -> `asr_no_speech`; empty caption -> retried as `transient_fetch_failed`).

---

## 3. The ASR path

### 3.1 Switches and platform sets

- `settings.asr_enabled` default `False` (`app/config.py:441`, `_env_bool("ASR_ENABLED", False)`); `.env.example:203-208` (`ASR_ENABLED=false`). Extra settings are validated only when enabled (`app/config.py:851-854` requires `DASHSCOPE_API_KEY`; spec `.trellis/spec/backend/asr-transcription.md` "Configuration"). Production value was not checked (no access).
- `_ASR_PLATFORMS = {youtube, bilibili}` (`tasks.py:347`) for the connector path; `_ASR_CAPTURE_PLATFORMS = {youtube}` (`tasks.py:351`) for caption-less browser captures; mirrored in `library.py:60` (`_ASR_CAPTURE_PLATFORMS`) and `asr_backfill.py:30,33`. `download_audio` itself only supports `youtube` and `bilibili` (`app/asr/audio.py:129-153`).
- Queue routing: `transcribe_dispatch_task` -> queue `asr` (`tasks.py:77,97`).

### 3.2 URL-import ASR handoff, end to end

1. Admission creates item `pending` + dispatch `pending` (section 0), publish `fetch_text_task` (queue `ingest`).
2. `process_dispatch` claims (`running`, `tasks.py:1378`) and calls `_dispatch_item_processor` -> `process_item(item_id, asr_handoff=True)` (`:1268-1271`).
3. `process_item` writes metadata and commits `state="fetching"` (`:423-436`), then `connector.fetch_text` returns `NeedsASR` (`youtube.py:256-257`, `bilibili.py:357`). If `asr_handoff and not pre_stored and settings.asr_enabled and platform in _ASR_PLATFORMS`: commit `state="fetching"` and return `"asr_pending"` (`:443-454`). Otherwise `needs_asr` (`:455-457`).
4. `process_dispatch` sees `ASR_PENDING` and calls `_handoff_dispatch_to_asr` (`:1257-1261`): row-lock the dispatch, `running -> enqueued`, `task_id=NULL`, `updated_at=now`, commit (`:1289-1307`); then `publish_asr_dispatch` (`:1309`). Publish failure -> `_complete_dispatch(process_state="needs_asr", from_states=("enqueued",), converge_item_state="needs_asr")` (`:1317-1324`): item `needs_asr`, dispatch `completed`, one event `completed/needs_asr`.
5. ASR worker: `transcribe_dispatch_task` -> `process_dispatch(processor=_asr_processor)` (`:646-653`) -> `_claim_dispatch` (`enqueued -> running`, `task_id` filled) -> `_asr_processor` (`:606-632`): if `asr_enabled` is now false -> plain `process_item` (ends `needs_asr`); else wraps the platform connector in `AsrFallbackConnector` and calls `process_item(item_id, connector=connector)`. Because `connector` is passed, the browser-capture lookup is skipped (`tasks.py:368`), metadata is fetched again (`app/asr/connector.py:27-31`), `item.state="fetching"` is committed again (`:435-436`), `fetch_text` runs again, and if still `NeedsASR` the audio is downloaded and transcribed (`connector.py:33-46`; `transcriber.py:184-246`: Fun-ASR first, whisper fallback; temp object + temp dir cleaned in `finally`).
6. `transcript_to_text_result` builds `TextResult(format="capture_v1", source="asr_whisper")` (`transcriber.py:98-125`); the normal guard/chunk/embed/segment-replace path runs and the item becomes `ready` (`tasks.py:460-559`); `_complete_dispatch` writes the single `completed/ready` event (`:1262-1264`).
7. Capture-origin YouTube without captions follows the same hand-off from the capture branch (`:386-398`) and then uses the connector path on the ASR queue (spec `asr-transcription.md` "Capture-origin items on the asr queue").

### 3.3 Why there is no durable "transcribing" marker for the URL path

- Throughout steps 3-5 the item row is `state="fetching"` (`:435, 452`) and the dispatch is `enqueued` (waiting on the `asr` queue) or `running` (ASR worker). The same pair `(fetching, running)` is also what a normal caption fetch looks like.
- `project_stage` (`library.py:186-227`, docstring `:194-200`) can only emit `transcribing` when `asr_capture` is true and `item.state == "fetching"` (`:218-219`); `asr_capture` comes from `ContentLibraryService._asr_capture_item_ids` (`:579-617`): item `fetching`, platform in `{youtube}`, not archived, and the latest `ready` tenant-matching `BrowserCapture` has `caption_status == "unavailable"`. A URL-submitted item has no `BrowserCapture`, hence `fetching`. Unit evidence: `tests/test_web_library.py:109-111,157-174`.
- No column records "ASR started": `IngestDispatch` columns are `id, public_id, item_id, source_thread_id, request_key, attempt, state, task_id, error_code, created_at, updated_at` (`app/models.py:724-752`); `ContentItem.state` is the native enum without a `transcribing` value (`app/models.py:58-62`); the spec states "Item state during ASR is `fetching`. No new state or migration exists." (`asr-transcription.md`).
- The processor hook receives only an item id: `Callable[[int], str]` (`tasks.py:1220`, `_asr_processor(item_id)` `:606`), so the ASR worker has no dispatch id at the point it could write a marker.

### 3.4 Facts about candidate marker locations (descriptive, not a recommendation)

- **`ingest_dispatch` (new nullable column)**: precedent for adding a nullable column + index to this exact table: migration `a1b2c3d4e5f6_ingest_notification_delivery.py:21-37` (`source_thread_id`). Both handoff writes already touch this row under `SELECT ... FOR UPDATE`: `_handoff_dispatch_to_asr` (`tasks.py:1289-1307`) and `_claim_dispatch` (`:1340-1383`, shared by both queues, no knowledge of which queue). `library._latest_for_items` already loads the latest dispatch for every listed item (`library.py:554-577`), so a dispatch column adds no query.
- **Do not overload `IngestDispatch.state`**: `state` is free `Text` with no CHECK, but the active set `{pending, enqueued, running}` is hard-coded in the partial unique index `uq_ingest_dispatch_item_active` (`app/models.py:760-767`; migration `c7e8a91b2d34`), in `library.ACTIVE_DISPATCH_STATES` (`library.py:27`), `submission.py:296,307,665-673`, `capture_sub.py:243`, `agent/management.py:649,846`, `asr_backfill.py:29`, `channels/identity.py` (`_ACTIVE_DISPATCH_STATES`), and `_claim_dispatch` (`tasks.py:1345-1348`). A new dispatch state would fall outside the one-active-dispatch guarantee.
- **`content_item` (new nullable column)**: written at `process_item` commits (`:396-398`, `:452-454`, `:435`); `fail_reason` is a free `Text` column; adding a `transcribing` value to the PG enum needs `ALTER TYPE ... ADD VALUE` (precedent `b8c9d0e1f2a3_browser_companion_capture.py:22-24`, not reversed on downgrade) and touches every enumeration in section 4.
- Marker lifetime considerations that are visible in code: the ASR worker commits `fetching` again at `tasks.py:435-436` and overwrites it to `chunking` at `:494`; a failure on the ASR queue goes through `_mark_dispatch_failed` (`:1988-1996`); `needs_asr` convergence at `:1483-1486`.

---

## 4. Completion events, notifications, and enumerations

### 4.1 Models (`app/models.py`)

- `IngestCompletionEvent` (`:847-918`), table `ingest_completion_event`: `outcome` Text, `item_state` Text (a snapshot, comment `:870-873`), `error_code` Text null, `publish_state`, claim/enqueue bookkeeping. Constraints:
  - `ck_ingest_completion_event_outcome`: `outcome IN ('completed','failed')` (`:896-899`)
  - `ck_ingest_completion_event_item_state`: `item_state IN ('ready','needs_extension','needs_asr','failed')` (`:900-903`)
  - `ck_ingest_completion_event_completed_error`: `(outcome='completed' AND item_state IN ('ready','needs_extension','needs_asr') AND error_code IS NULL) OR (outcome='failed' AND item_state='failed' AND error_code IS NOT NULL)` (`:908-912`)
  - unique `dispatch_id` (one event per dispatch attempt, `:893-895`); same constraints in migration `f6a7b8c9d0e1_ingest_completion_events.py:56-65`. There is **no CHECK listing allowed `error_code` values**.
- `IngestCompletionDelivery` (`:921-1011`): delivery ledger keyed `(event_id, handler_key)`; its `last_error_code` CHECK (`:970-981`) is a different vocabulary (`outbound_*`, `retry_exhausted`, ...) and is unrelated to ingest error codes.
- Event creation: `_ensure_completion_event` (`tasks.py:1531-1592`) inside the same locked txn as the dispatch terminal transition; consumers: `app/ingest/notifications.py` (`:172-183` message rendering, `:770-783` eligibility/`NotificationTarget`), `app/ingest/notification_repair_index.py`. Spec: `.trellis/spec/backend/ingest-completion-queue.md` ("A normal worker return snapshots ready, needs_extension, or needs_asr with outcome=completed. A terminal failure snapshots failed with a non-null stable error code"; "`ContentItem.ready` wins the crash window").
- `_terminal_item_state` (`tasks.py:1520-1528`) raises `ValueError("completion_item_state_not_terminal")` if the item's state after `process_item` is not in `{ready, needs_extension, needs_asr}`, and `completion_process_state_mismatch` if `process_item`'s return differs from `item.state`. `_complete_dispatch` is called outside the try/except in `process_dispatch` (`:1262-1264`), so such a ValueError propagates to Celery -> `on_failure` -> `_mark_dispatch_failed` -> item `failed/ingestion_failed` (unless item is `ready`).

### 4.2 What would break

**(a) New columns on `content_item`**
- Model and migration must agree: production runs `alembic upgrade head`, `alembic current`, `alembic check` (`deploy/scripts/run-production-migrations:9-11`); `alembic check` fails on model/DB drift. CI also runs `alembic heads` (single head required, `.github/workflows/extension-api-contract.yml`, "Alembic graph"; `CONTRIBUTING.md:41`).
- No raw SQL touches `content_item`; ORM constructors are at `tasks.py:328`, `capture_sub.py:265`, `submission.py:689`. A nullable column or one with a server default is transparent to them.
- Account merge copies only the fields in `_CONTENT_FILL_FIELDS` (`app/channels/identity.py:45-58`) and ranks duplicates by `state == "ready"` (`:493`); new columns are not carried over unless added there.
- Public contract: `LibraryItemDTO` (`library.py:96-118`) -> `LibraryItemResponse` (`app/api/library_schemas.py:52-79`, `Stage`/`Lifecycle` Literals duplicated at `:15-30`, `library.py:41-56`) -> generated `extension/src/api/openapi.json` (`scripts/export_web_openapi.py:19`). CI step `pnpm check:api` fails if the committed file is stale (`CONTRIBUTING.md:53`, workflow step "OpenAPI contract", `.github/workflows/extension-api-contract.yml:47`). The comment at `library_schemas.py:74-77` notes that `default_factory` is used so the generated TS field stays optional.
- Tests: schema-isolated PG tests build tables with `Base.metadata.create_all(tables=[...])` so they pick up new model columns automatically (`tests/test_web_library_postgres.py:22-38`, `tests/test_asr_ingest_flow.py:857-885`, `tests/test_ingest_submission_postgres.py:33-67`). `tests/test_multiuser_integration.py:81-97` (`db_factory`) instead uses the real `public` schema of whatever `DATABASE_URL` points to and needs it migrated to head. Fake-object tests use `SimpleNamespace` items (`tests/test_web_library.py:15-43`, plus `ScalarSession`/`QuerySession`), so a `_dto`/projection that reads a new attribute directly would raise `AttributeError` on those fakes.
- `tests/test_web_auth_migration.py:33` asserts `script.get_heads() == ["d04fdae36884"]`; the dev head is already `c9d0e1f2a3b4` (section 6), and any new migration changes it again.

**(b) Writing error codes for `needs_*` outcomes**
- The event CHECK `ck_ingest_completion_event_completed_error` forbids a non-NULL `error_code` on a `completed` event (`app/models.py:908-912`), and `_complete_dispatch` hard-codes `dispatch.error_code = None` and `error_code=None` for the event (`tasks.py:1490,1498`). A reason for a `needs_*` outcome could be stored on `dispatch.error_code` / `item.fail_reason` without touching the event table (`ingest_dispatch` has no CHECK constraints, `app/models.py:754-774`), but `_complete_dispatch` currently overwrites `dispatch.error_code` with `None`.
- Read side ignores it: `project_lifecycle` returns `error=None` for `needs_action` (`library.py:151-153`); `get_dispatch` would expose `dispatch.error_code` through `_safe_error` (`library.py:521`), i.e. collapse to `ingestion_failed` unless whitelisted; `_safe_error_code(item)` in `app/agent/management.py:239-246` returns `None` for non-`failed`.
- Notification text depends only on `(outcome, item_state)` (`notifications.py:172-183`).
- Diagnostics allow-lists (`tasks.py:186-198`, `app/diagnostics.py:62-90`) drop new codes silently.
- Existing tests that pin the event contract: `tests/test_ingest_completion.py:56` ("failed completion snapshot requires a stable error code"), `:155` (snapshot requires persisted terminal state).

**(c) New item states**
- PG enum: `ingest_state_enum` (`app/models.py:58-62`) and original DDL `migrations/versions/6df2e721d7b2_init_schema.py:40-44`; enum additions use `ALTER TYPE ... ADD VALUE IF NOT EXISTS` and are not removed on downgrade (`b8c9d0e1f2a3_browser_companion_capture.py:22-24`, comment: "The downgrade ... retains the harmless value").
- Event path: CHECK `ck_ingest_completion_event_item_state` (`app/models.py:900-903`); `_terminal_item_state` (`tasks.py:1520-1528`); `_completion_diagnostic` item_state allow-list (`tasks.py:186-187`); `notifications.py:770-771` eligibility list and `:172-183` message set (anything else renders the generic "state unavailable" text).
- Read side: `project_lifecycle` has explicit sets (`library.py:148-175`), an unknown state falls through to `failed/missing_dispatch` unless latest dispatch is `running`/`pending`-`enqueued`; `library_routes._batch_item` maps item states to lifecycle with a dict and returns `None` for unknown (`app/api/library_routes.py:112-139` (mapping dict at `:128-137`)); `BrowserCaptureSubmissionService._result_for_capture` maps `item.state in {ready, needs_asr}` else `queued` (`capture_sub.py:357-358`; surfaced as `BrowserCaptureResponse.lifecycle: str` in `app/api/browser_companion_routes.py:78-84,245`).
- Retrieval gate: only `state == "ready"` is searchable (`app/retrieval/search.py:107,174,206`; `app/agent/services.py:277,355,371,421`; `app/agent/context.py:32,278,394` `_READY_STATES = {"ready"}`).
- The two enumerations the task named: `app/agent/agent_tools/actions.py:124-127` (the `state: Literal[...]` argument of the `list_saved_items` LLM tool; a value outside it is rejected by the tool schema) and `app/agent/management.py:556-560` (`_filters` raises `ManagementError("invalid_filter")` for a value outside the tuple); the filter is applied as `ContentItem.state == state` (`management.py:322`). Output side is a free string: `SavedItem.ingestion_state: str` (`management.py:87`), `SavedItemProjection.ingestion_state: str = Field(max_length=32)` (`app/mcp_server.py:203`), rendered raw in `app/agent/actions.py:801`.
- Both enumerations list `no_text`, which is never written.

---

## 5. Re-processing (full re-run on retry)

- Chunk replacement happens only at the very end, in one transaction: `db.execute(delete(Segment).where(Segment.item_id == item.id))` (`tasks.py:552`), then `item.state="embedding"` (`:553`), new `Segment` rows via `db.add` (`:554-556`, vectors were already computed in memory at `:542`), `item.state="ready"` and `fail_reason=None` (`:557-558`), a single `db.commit()` (`:559`). Segments are never deleted earlier, never on failure. Readers see either the old segments or the new ones, never a gap, because delete and insert commit together.
- The item can still be unsearchable during a re-run for a different reason: every retrieval query requires `ContentItem.state == "ready"` (`app/retrieval/search.py:107,174,206`; `app/agent/services.py:277,355,371,421`). `process_item` commits `fetching` (`:435`) and `chunking` (`:494`) on the item row well before the segment swap, so from the first of those commits until `:559` a re-run item is excluded from search even though old segments still exist.
- When can a previously `ready` item be re-run today? Not via any web/Agent/capture admission: `ContentLibraryService.retry` requires the item to project as `failed` (`library.py:441`, and `ready` wins in `project_lifecycle`, `:148-150`); `retry_item` requires `state == "failed"` (`submission.py:877`); `_submit_reference` returns `already_exists` unless `pending|failed` (`:657-658`); `_admit` returns `already_exists` for `ready` (`capture_sub.py:248-257`). Re-running a `ready` item only happens through the synchronous CLI `ingest_url` (`app/cli.py:111`, `tasks.py:2025-2033`), where `create_item` returns the existing row without resetting its state (`tasks.py:311-327`). For items that were never `ready` (every `failed` / `needs_*` item), no segments exist before the re-run.
- Raw objects: the key is content-addressed (`{user}/{platform}/{platform_id}/{sha256}.{suffix}`, `tasks.py:484`; capture `{user}/{platform}/{platform_id}/{content_hash}.capture.json`, `capture_sub.py:116-121`); a re-run with different content leaves the previous object in place (only the deleted-race path deletes, `tasks.py:507,549`). `content_hash`, `raw_object_key`, `raw_format`, `text_source`, `lang` are overwritten at `:489-493` only when a body is obtained; they are not cleared on retry or failure.
- Media vectors: `MediaAsset` / `MediaEmbedding` models and migration `c9d0e1f2a3b4` exist (`app/models.py:589-716`), but nothing in `app/` writes those tables. `app/ingest/media.py:1-8` states the production media tables are "deliberately implemented in a later Trellis task" and ships an in-memory store only. `process_item` never touches them. Schema-level facts: `media_asset.content_item_id` -> `content_item` `ON DELETE CASCADE` (`models.py:639-644`); `media_asset.segment_id` -> `segment.id` `ON DELETE SET NULL` (`models.py:645-650`), so deleting segments nulls `segment_id` but keeps the asset and its embeddings.
- Retry bookkeeping on the item: `state=pending`, `fail_reason=None` (`library.py:453-454`), new dispatch with `attempt = latest.attempt + 1` and the caller's `request_key` (`:455-463`); replay with the same `request_key` returns the current DTO without a new dispatch (`:422-438`); the partial unique index `uq_ingest_dispatch_item_active` allows one active dispatch per item (`app/models.py:760-767`). Quota: `IngestQuotaPolicy.enforce(... include_new_item_limits=False)` counts active dispatches (tenant/global) and daily dispatch creations (tenant/global) (`submission.py:284-346` and `_enforce_daily_dispatch_limits` `:348-387`); a retry consumes the daily dispatch budget.

---

## 6. Migrations

- Layout: `alembic.ini` (`script_location = %(here)s/migrations`), `migrations/env.py`, `migrations/script.py.mako`, `migrations/README` (generic stub), `migrations/versions/` (14 files). `env.py:26-27` takes the URL from `get_settings().database_url` unless a connection is injected via `config.attributes["connection"]` (used by tests, e.g. `tests/test_migration_roundtrip_postgres.py:43-44`). `app/config.py:39` runs `load_dotenv()` (process env wins over `.env`).
- Naming: `<12-hex revision>_<snake_slug>.py`. Revision ids are either random (`6df2e721d7b2`, `d04fdae36884`, `c7e8a91b2d34`) or hand-patterned (`a1b2c3d4e5f6`, `d3f4a5b6c7d8`, ...). Module docstring `"""<title>\n\nRevision ID: ...\nRevises: ...\nCreate Date: YYYY-MM-DD\n"""`, then typed `revision: str`, `down_revision: Union[str, Sequence[str], None]`, `branch_labels`, `depends_on`; merge migrations have empty `upgrade()/downgrade()` and a tuple `down_revision` (`a7b8c9d0e1f2...py`, `b2c3d4e5f6a7...py`).
- **Current head on `dev`: `c9d0e1f2a3b4`** in `migrations/versions/c9d0e1f2a3b4_media_asset_embeddings.py` (docstring "Create Date: 2026-09-10"; its `down_revision` is `"d04fdae36884"` although the docstring still says "Revises: b8c9d0e1f2a3"). Chain (newest first): `c9d0e1f2a3b4` -> `d04fdae36884` (drop segment HNSW) -> `b8c9d0e1f2a3` (browser companion) -> `f1a2b3c4d5e6` -> `b2c3d4e5f6a7` (merge) -> `a7b8c9d0e1f2` (merge) / `a1b2c3d4e5f6` -> ... -> `6df2e721d7b2` (init). Computed by script: the only revision that is nobody's `down_revision` is `c9d0e1f2a3b4`.
- `origin/main` (local remote-tracking ref) has 13 files and **lacks** `c9d0e1f2a3b4` (`git ls-tree origin/main migrations/versions/`), i.e. main's head is `d04fdae36884`; the media-asset migration is on `dev` only (commit `3b64d34 wip(wemm)...`). A new revision for this task would chain after `c9d0e1f2a3b4` on dev.
- Production applies migrations as a one-shot unit before any app unit starts: `deploy/scripts/run-production-migrations` runs `alembic upgrade head`, `alembic current`, `alembic check` using `MIGRATION_DATABASE_URL` (direct Neon URL); spec `.trellis/spec/backend/database-guidelines.md` ("exactly one Alembic head", migration URL vs pooled runtime URL).
- Past examples:
  - Add column to this very table family: `a1b2c3d4e5f6_ingest_notification_delivery.py:21-37` (`ingest_dispatch.source_thread_id`, nullable, FK `SET NULL` + index; downgrade drops index, FK, column at `:135-155`).
  - Add `content_item` columns with server defaults: `d4e5f6a7b8c9_item_management.py:25-32` (nullable timestamps, `purge_attempts` `server_default="0"` NOT NULL), downgrade with a data guard (`:53-65`); `b8c9d0e1f2a3_browser_companion_capture.py:25-28` (`raw_format` `server_default="json3"` NOT NULL; downgrade `op.drop_column` at `:216`).
  - Add column + backfill + tighten: `d3f4a5b6c7d8_web_auth_library.py:23-45` (nullable column with volatile server default, `UPDATE ... WHERE public_id IS NULL`, then `alter_column(nullable=False)` and unique constraint); `c7e8a91b2d34_agent_save_actions.py:22-50` (add columns with defaults, then `UPDATE conversation_turn SET ... CASE ...` data migration).
  - **Alter a Postgres enum**: only one precedent, `b8c9d0e1f2a3_browser_companion_capture.py:24`: `op.execute("ALTER TYPE platform ADD VALUE IF NOT EXISTS 'ntu_kaltura'")`, comment `:22-23` says the enum addition is forward-compatible and the downgrade retains the value (no enum removal in `downgrade()`).
  - Enum creation originally: `6df2e721d7b2_init_schema.py:30-45` (`postgresql.ENUM(..., create_type=False)`); `ingest_state` values `pending, fetching, needs_extension, needs_asr, chunking, embedding, ready, failed, no_text`.
  - Changing an existing CHECK constraint: **no precedent found** (all `op.drop_constraint` calls in `versions/` drop foreign keys: `a1b2c3d4e5f6...:143`, `f1a2b3c4d5e6...:71`, `d3f4a5b6c7d8...:210`).
- Related test: `tests/test_web_auth_migration.py:20-36` pins the head (`["d04fdae36884"]`, stale on dev); `tests/test_migration_roundtrip_postgres.py` upgrades to `9a6b2c4d8e10` in a freshly `CREATE DATABASE`d database on the configured server (`:19-47`).

---

## 7. Tests

- Location: `tests/` (flat, ~110 files; `pyproject.toml:46-48` `pythonpath=["."]`, `testpaths=["tests"]`). **There is no `conftest.py`** anywhere in the repo (searched excluding node_modules/.venv/worktrees), and no `TEST_DATABASE_URL` variable exists anywhere in `tests/`, `README.md`, `CONTRIBUTING.md`, `.trellis/spec`, `docs/`.
- Ingest/library tests and how they get a DB:
  - Fake-session unit tests (no DB): `tests/test_tasks.py` (worker/`process_item`/retry-exhaustion with fakes), `tests/test_web_library.py` (`SimpleNamespace` items + `ScalarSession`/`QuerySession` fakes; lifecycle/stage tables at `:61-110`), `tests/test_ingest_completion.py`, `tests/test_asr_*.py` (connector/transcriber/audio), `tests/test_validate.py`, `tests/test_browser_capture.py`.
  - Real PostgreSQL in an isolated schema: `tests/test_web_library_postgres.py:22-48` (`library_factory`), `tests/test_asr_ingest_flow.py:857-901` (`pg_factory`, module scope; also ASR end-to-end/handoff/backfill with a `worker_env` fixture that monkeypatches `get_settings`, `publish_asr_dispatch`, `enqueue_ingest_notification`, `:904-920`), `tests/test_ingest_submission_postgres.py:33-70` (`submission_factory`), `tests/test_ingest_notifications_postgres.py`, `tests/test_pending_confirmation_postgres.py`, `tests/test_web_auth_postgres.py`. Pattern: `engine = get_engine()` (`app/db.py:9-11`, reads `get_settings().database_url`), `CREATE SCHEMA "test_<name>_<uuid>"`, `SET LOCAL search_path TO "<schema>", public` (re-applied per transaction through an `after_begin` listener in `pg_factory`), `Base.metadata.create_all(connection, tables=[...], checkfirst=False)`, `DROP SCHEMA ... CASCADE` in teardown; they `pytest.skip` if the schema cannot be created.
  - Real migrated `public` schema with outer-transaction rollback: `tests/test_multiuser_integration.py:81-97` (`db_factory`, skips if `channel_identity` is unreachable).
  - Migration tests creating a separate database: `tests/test_migration_roundtrip_postgres.py`, `tests/test_web_auth_migration.py:39+`.
- Safe way to point pytest at a test database (documented):
  - `CONTRIBUTING.md:39`: tests read the database settings from the environment or `.env`; "Only point them at a local, disposable database, never at a shared or production one." Local disposable option: `docker compose up -d postgres redis` (`CONTRIBUTING.md:26-27`, `docker compose up -d postgres redis`; `docker-compose.yml` `pgvector/pgvector:pg17`, default db `kb`, port 5432). CI uses `DATABASE_URL=postgresql+psycopg://postgres:test@127.0.0.1:5432/kb` (`.github/workflows/extension-api-contract.yml` env).
  - `.trellis/spec/guides/branch-and-delegation-workflow.md:30`: bare `pytest` reaches the production DB; point `DATABASE_URL` at the `.env.neon-test` branch and rewrite the scheme to `postgresql+psycopg://` (the venv has no psycopg2).
  - Mechanism that makes an env override work: `app/config.py:39` `load_dotenv()` does not override variables already in the process environment, and `app/config.py:1251-1252` takes `DATABASE_URL` first. The memory note `pytest-hits-production-db.md` gives a one-liner (strip quotes, rewrite scheme, assert the host is the test branch) and notes there is no conftest guard. (`.env.neon-test` was deliberately not opened during this research.)
  - Also ASR tests set `ASR_ENABLED`-like behaviour through `_settings()` (`tests/test_asr_ingest_flow.py:62-72`, `dataclasses.replace(Settings(), asr_enabled=True, ...)`) rather than the environment, so ASR on/off coverage does not depend on env vars.
- Existing tests that already cover the neighbouring behaviours (useful as templates): ASR handoff and convergence `tests/test_asr_ingest_flow.py:312-560,995-1209`; no-speech stable code and one event `:1166-1209`; transient download releases without event `:1209`; backfill selection/admission `:1290-1540`; stage projection `tests/test_web_library.py:79-215`; error-code allow-list `tests/test_asr_ingest_flow.py:745`.

---

## Related Specs

- `.trellis/spec/backend/asr-transcription.md` — ASR flow contract, eligibility, stable error codes, "Item state during ASR is `fetching`".
- `.trellis/spec/backend/ingest-completion-queue.md` — outbox/event contract (one event per dispatch, snapshot vocabulary).
- `.trellis/spec/backend/database-guidelines.md` — single Alembic head, production migration unit (`upgrade/current/check`).
- `.trellis/spec/backend/knowledge-item-management.md` — retry admission and split-state repair.
- `.trellis/spec/backend/bilibili-connector.md`, `youtube-connector.md` — connector error classifications.
- `.trellis/spec/guides/branch-and-delegation-workflow.md` — test DB rule for agents.

## Caveats / Not Found

- Production `ASR_ENABLED` value and the production Alembic revision were not checked (no access, by instruction).
- Celery behaviour when `time_limit`/`soft_time_limit` fires on `transcribe_dispatch_task` (whether `IngestTask.on_failure` runs and with what exception) was not verified from code or a run.
- No test was executed. The statement that `tests/test_web_auth_migration.py:33` is stale is derived from reading its assertion and the static head computation, not from a test run.
- `origin/main` comparison uses the local remote-tracking ref and may lag the real remote.
- `item_missing` is whitelisted but has no producer in `app/`; the claim "no writer" is from `grep -rn item_missing app`.
- Frontend (extension) behaviour was out of scope; see sibling research `extension-ui.md` / `api-and-retrieval.md` in this directory.
