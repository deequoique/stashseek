# ASR Transcription Pipeline

## Scope / Trigger

Apply this contract whenever you change any of the following:

- `app/asr/`
- the `NeedsASR` handling, `asr_pending` handoff or `transcribe_dispatch_task` in `app/ingest/tasks.py`
- the public `/api/v1/asr-audio/{name}` route
- `app/ingest/asr_backfill.py`
- the `asr` worker unit

Origin: task `10-15-asr-transcription-pipeline`. The 2026-10-15 decisions were: Fun-ASR as the default provider, `whisper-1` as the fallback, the Singapore region, a signed audio URL on the app domain, and ffmpeg in production.

## Flow

```text
fetch_text_task (queue ingest)
  -> process_item(asr_handoff=True): connector returns NeedsASR
       and ASR_ENABLED and platform in {youtube, bilibili} and not a browser capture
     -> item stays "fetching"; return "asr_pending"
  -> _handoff_dispatch_to_asr: lock; running -> enqueued, task_id=NULL; commit
     -> publish transcribe_dispatch_task (queue asr)
        publish failure -> _complete_dispatch(from_states=("enqueued",),
                           converge_item_state="needs_asr") => one completed/needs_asr event
transcribe_dispatch_task (queue asr, concurrency 1, time_limit 3600 / soft 3480)
  -> process_dispatch(processor=_asr_processor)
     -> process_item(connector=AsrFallbackConnector(inner, AsrTranscriber))
        download audio -> put_file asr-tmp/<uuid>.<ext> -> signed URL -> Fun-ASR
        (unavailable/protocol error -> whisper-1) -> finally delete object + temp dir
        -> TextResult(format="capture_v1", source="asr_whisper") -> existing chunk/embed -> ready
  -> one completed/ready event + notification
```

## Contracts

- **One dispatch, one event.**
  - The handoff never calls `_complete_dispatch`.
  - The `asr` task completes the same dispatch.
  - A dispatch that has handed off must never emit a `needs_asr` event, except on the publish-failure convergence path.
- **Eligibility.** The handoff happens only on the connector-path `NeedsASR` branch. Browser-capture items, including `ntu_kaltura` and YouTube captures with `caption_status="unavailable"`, keep `needs_asr`. Direct `process_item` callers without `asr_handoff=True`, such as the CLI `ingest`, keep the old behavior.
- **Item state during ASR** is `fetching`. No new state or migration exists.
- **`text_source`** is `asr_whisper` for every ASR provider, until a migration adds a dedicated value. The real provider appears only in the `asr_transcription` diagnostic.
- **Stable error codes** come from `app.asr.types.ASR_ERROR_CODES`:
  - `asr_too_long`
  - `asr_audio_unavailable`
  - `asr_audio_too_large`
  - `asr_provider_failed`
  - `asr_no_speech`
  - `asr_not_configured`

  `AsrError` is terminal and never retried. It maps to `dispatch.error_code`, the item's `fail_reason` and the completion event. These codes are allow-listed in `app/web/library.py` `SAFE_ERROR_CODES`, `app/diagnostics.py` and `_completion_diagnostic`.
- **Empty transcripts** raise `AsrError("asr_no_speech")` before `guard_transcript`. `EmptySuccess` is a transient error, so it would otherwise retry and re-bill.
- **Fallback** to whisper only on `AsrProviderUnavailable` (missing key, 401/403, 429/5xx after retries, timeout, subtask failure, staging failure) or `AsrProtocolError`. A valid empty Fun-ASR result is not a fallback trigger.
- **Fun-ASR request:**
  - endpoint: `POST {DASHSCOPE_BASE_URL}/api/v1/services/audio/asr/transcription`
  - headers: `X-DashScope-Async: enable`
  - body: always includes `"parameters": {}` and never includes `language_hints` (only the first hint is used, which breaks Chinese-English code-switching)
  - polling: `GET /api/v1/tasks/{task_id}`
  - times: sentence `begin_time` and `end_time` are milliseconds
- **Public audio route** `GET|HEAD /api/v1/asr-audio/{name}?expires=&sig=`:
  - **Signature:** HMAC-SHA256 over `asr-audio.v1\n{name}\n{expires}`, compared with `hmac.compare_digest`.
  - **Name:** must match `^[0-9a-f]{32}\.<allowed ext>$`.
  - **Expiry:** `expires` must be ≤ now + TTL + 60 s.
  - **Failures:** every failure returns the same 404 `not_found`.
  - **Response:** streamed with `Cache-Control: no-store`.
  - **Registration:** only when `ASR_ENABLED` is set and a secret is configured, and before the SPA fallback.
- **Privacy.** Logs, exceptions and diagnostics never contain:
  - signed URLs or `transcription_url`
  - googlevideo URLs or API keys
  - transcript text
  - raw yt-dlp or ffmpeg stderr
  - provider bodies
- **Child processes.** YouTube children get the child-only proxy and CA environment. The yt-dlp audio child (YouTube and Bilibili) receives a copy of that environment without secret-named variables (`*API_KEY*`, `*SECRET*`, `*PASSWORD*`, `*TOKEN*`, `DATABASE_URL`, `REDIS_URL`). ffmpeg gets a minimal allow-listed environment.
- **Temporary audio.** Delete it in `finally`. Operations add the MinIO rule `asr-tmp/` expire 1 day as a backstop.

## Configuration

Validation happens only when `ASR_ENABLED=true`:

- `DASHSCOPE_API_KEY` is required.
- `DASHSCOPE_BASE_URL` must be https and defaults to `https://dashscope-intl.aliyuncs.com` (Singapore).
- `ASR_PUBLIC_BASE_URL` must be a bare https origin.
- `ASR_AUDIO_URL_SECRET` must be at least 32 characters.

`FUN_ASR_MODEL`, `OPENAI_API_KEY` and `ASR_WHISPER_FALLBACK_ENABLED` are optional. Limits and timeouts are documented in `docs/reference/configuration.md`.

## Tests Required

- Fake-transport Fun-ASR tests for request shape, polling, failures and parsing.
- whisper tests for segment offsets, and for audio over 24 MB without ffmpeg.
- Download tests for arguments, environment and error classification.
- Transcriber tests for fallback rules, cleanup and admission.
- Route tests for signature cases, headers and the disabled state.
- `tests/test_asr_ingest_flow.py` for handoff, the single event, publish-failure convergence, error codes and backfill.
- PostgreSQL-backed tests must use the test branch, with the `postgresql+psycopg://` scheme.

## Wrong vs Correct

- Wrong: call `_complete_dispatch` after `asr_pending` → two events, and the user gets the "needs ASR" message.
- Correct: hand off by setting `enqueued` / `task_id=NULL`, and complete only in the `asr` task.
- Wrong: let an empty ASR transcript reach `guard_transcript` → 5 transient retries, each one billed.
- Correct: raise `AsrError("asr_no_speech")` inside the transcriber.
- Wrong: pass `language_hints=["zh","en"]` → only `zh` applies.
- Correct: omit hints and let the model detect the language.
