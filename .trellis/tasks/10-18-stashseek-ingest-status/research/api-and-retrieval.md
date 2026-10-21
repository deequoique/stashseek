# Research: library API, contract generation, retrieval filtering, quota (for ingest-status design.md)

- **Query**: Read-only research of the library API, contract generation, retrieval filtering and quota so `design.md` can be written (task `10-18-stashseek-ingest-status`).
- **Scope**: internal (dev branch, HEAD `7f5a033`)
- **Date**: 2026-10-18
- **Method**: Read/Grep only. No pytest, no DB access, no file outside this `research/` directory touched.

All paths are relative to repo root `/Volumes/PeeB/projects/notebook-agent`.

---

## 1. Library service and DTOs (`app/web/library.py`)

### 1.1 Types

| Symbol | Lines | Fields / values |
|---|---|---|
| `ACTIVE_DISPATCH_STATES` | `library.py:27` | `{"pending","enqueued","running"}` |
| `SAFE_ERROR_CODES` | `library.py:28-40` | `queue_unavailable, ingestion_failed, transient_fetch_failed, item_missing, missing_dispatch, transcript_unavailable, transcript_invalid, ingest_too_large` + `sorted(ASR_ERROR_CODES)` (`asr_too_long, asr_audio_unavailable, asr_audio_too_large, asr_provider_failed, asr_no_speech, asr_not_configured`, defined `app/asr/types.py:25-34`) |
| `Lifecycle` Literal | `library.py:41-43` | `archived, ready, needs_action, failed, processing, queued` |
| `Stage` Literal | `library.py:46-56` | `queued, fetching, transcribing, chunking, embedding, ready, needs_action, failed, archived` |
| `_ASR_CAPTURE_PLATFORMS` | `library.py:60` | `{"youtube"}` (local mirror of `app/ingest/tasks.py:351`) |
| `LifecycleProjection` | `library.py:82-86` | `state: Lifecycle`, `error_code: str|None`, `available_actions: tuple[str,...]` |
| `StageProjection` | `library.py:89-92` | `stage: Stage`, `updated_at: datetime|None` |
| `LibraryItemDTO` (frozen dataclass) | `library.py:95-118` | `public_id, platform, kind, url, title, author, published_at, duration_sec, lang, description, tags, chapters, cover_url, saved_at, why_saved, text_source, lifecycle, error_code, available_actions, latest_dispatch_public_id` (all required) + defaults `stage="queued"`, `stage_updated_at=None` |
| `LibraryPage` | `library.py:121-127` | `items, total, page, page_size, is_true_first_empty` (no defaults; tests construct it positionally, e.g. `tests/test_web_api_app.py:82`) |
| `DispatchDTO` | `library.py:130-138` | `public_id, item_public_id, attempt, state, error_code, created_at, updated_at` |

There is no per-stage status, no "body searchable" flag, and no count of non-searchable items anywhere today (`grep -i searchable` over `app/`, `extension/src`, `tests/` finds nothing relevant).

### 1.2 `project_lifecycle` (`library.py:145-183`) - exact precedence

1. `item.archived_at is not None` -> `archived`, error None, actions `("restore",)` only (`:146-147`).
2. `item.state == "ready"` -> `ready`, error None (`:148-150`).
3. `item.state in {needs_extension, needs_asr, no_text}` -> `needs_action`, **error None** (`:151-153`).
4. `latest dispatch failed` OR `item.state == "failed"` -> `failed`; error = `_safe_error(latest.error_code if latest failed else item.fail_reason)` (`:154-158`). `_safe_error` (`:141-142`) maps anything outside `SAFE_ERROR_CODES` to `ingestion_failed` (so `item_deleted`, `capture_upload_failed`, etc. surface as `ingestion_failed`).
5. `latest.state == "running"` or `item.state in {fetching, chunking, embedding}` -> `processing` (`:159-165`).
6. `item.state == "pending"` and latest dispatch `pending|enqueued` -> `queued` (`:166-171`).
7. Else -> `failed` with `missing_dispatch` (`:172-174`).

`available_actions` (`:176-183`): always starts with `["edit_why_saved","archive"]`; adds `"retry"` only when `state == "failed"` and (no latest dispatch or latest not in ACTIVE_DISPATCH_STATES); adds `"open_source"` if `item.url`. `needs_action` therefore never has `retry`. `_dto` strips `"retry"` when `save_enabled` is false (`:628-636`). `available_actions` is an untyped `tuple[str,...]` in DTO and `tuple[str,...]` in the response schema (not a Literal), so new action strings are schema-compatible.

`project_stage` (`:186-227`): `stage` follows lifecycle 1:1 except `processing`, which splits on `item.state` (`chunking`/`embedding`), `asr_capture and state == "fetching"` -> `transcribing`, else `fetching`. `updated_at` = latest dispatch `updated_at` (or `archived_at` for archived) (`:225-227`). The docstring (`:194-207`) states the URL-import ASR path "leaves no durable marker and is reported as `fetching`".

### 1.3 `_asr_capture_item_ids` (`library.py:579-617`)

Static method. Candidates = items with `state == "fetching"` AND `platform in _ASR_CAPTURE_PLATFORMS` AND `archived_at is None` (`:588-594`). One query over `BrowserCapture(item_id IN candidates, app_user_id == user_id, state == "ready")` ordered newest first (`:597-609`); an item is in the result iff its newest ready capture has `caption_status == "unavailable"` (`:610-617`). Skipped entirely when no candidate (`:595-596`). It is the only "transcribing" signal; it does not cover URL-import ASR (Bilibili/YouTube connector `NeedsASR`).

### 1.4 `_dto` / `get_item` / `list_items`

- `_dto` (`:619-660`): calls `project_lifecycle` + `project_stage`, builds `LibraryItemDTO`; `text_source` copied from `item.text_source`; `stage`/`stage_updated_at` from projection.
- `get_item` (`:354-359`): `_owned_item` (tenant + `public_id` + `deleted_at IS NULL`, else `LibraryNotFound` -> 404; `:526-538`), `_latest_dispatch` (`:540-552`), `_asr_capture_item_ids`, `_dto`.
- `list_items` (`:255-352`):
  - Params: `search, collection, lifecycle, include_archived=False, sort in {saved_desc,saved_asc,title_asc}, page>=1, page_size 1..100` (clamped `:267-268`). Invalid `lifecycle|sort|collection` raise `LibraryConflict("invalid_lifecycle"|"invalid_sort"|"invalid_collection")` (`:269-282`).
  - SQL base: `ContentItem.user_id == scope.app_user_id AND deleted_at IS NULL` (`:294-297`). `lifecycle == "archived"` -> `archived_at IS NOT NULL`; else if not `include_archived` -> `archived_at IS NULL` (`:298-301`).
  - Search (`:302-310`): `%{search.strip()}%` ILIKE on `title`, `author`, `why_saved` only (OR). Not on `url`/`platform_id`. `%`/`_` in user input are not escaped.
  - Collection (`:311-317`): Postgres `~*` regex on `why_saved` for a `#tag` token.
  - Sort (`:318-326`): `saved_at`/`id` or `lower(coalesce(title,''))`/`id`.
  - **Lifecycle filter is NOT in SQL.** All matching rows are loaded (`:327`), dispatches batch-loaded (`_latest_for_items`, `:328-332`, impl `:554-577`), ASR ids batch-loaded (`:333`), every row is projected, and then `projected = [v for v in projected if v.lifecycle == lifecycle]` (`:342-343`). `total = len(projected)` (post-filter) and pagination is a Python slice (`:344-352`). So `needs_action`/`failed` filtering is purely the Python projection; there is no SQL predicate for `state IN (...)`. There is also no `state` index on `content_item` (indexes `app/models.py:555,576`: `(user_id, saved_at desc, id desc) WHERE deleted_at IS NULL` and `(user_id, archived_at, saved_at desc)`).
  - `is_true_first_empty` = count of **all** non-deleted items (including archived) == 0 (`:285-293,351`).
  - No facet/aggregate count anywhere in the response besides `total`.

### 1.5 `retry` rules (`library.py:395-498`)

Order of checks:
1. `not self._save_enabled` -> `LibraryConflict("save_disabled")` (`:403-404`).
2. Blank request key -> `ValueError` (`:405-406`); non-positive budget -> `ValueError` (`:407-408`).
3. `quota_policy.acquire_locks` false -> `LibraryNotFound` (`:415-418`).
4. `_owned_item(..., lock=True)` (FOR UPDATE; deleted/foreign -> `LibraryNotFound` 404) (`:419`).
5. `item.archived_at is not None` -> `LibraryConflict("retry_unavailable")` - **before** the idempotency replay check (`:420-421`).
6. Replay: a dispatch with same `(item_id, request_key)` -> returns current DTO with no new dispatch (`:422-438`).
7. `projection.state != "failed"` or latest dispatch active -> `LibraryConflict("retry_unavailable")` (`:439-444`). **This is the single gate that excludes `needs_action`, `processing`, `queued`, `ready`.**
8. `quota_policy.enforce(db, user, include_new_item_limits=False)`; `IngestQuotaExceeded` -> `LibraryConflict("quota_exceeded")` (`:445-452`). Raised before any mutation, so item state/reason are untouched.
9. Mutation: `item.state = "pending"`, `item.fail_reason = None`, new `IngestDispatch(attempt = latest.attempt+1 or 1, state="pending", request_key)`; commit (`:453-465`). Segments/raw object are not touched here (old segments are deleted later at `app/ingest/tasks.py:552`).
10. Publish outside the transaction with the remaining Web budget (`:466-483`). On publish exception, `_set_dispatch(..., "failed", error_code="queue_unavailable")` (`:484-490`) which also flips `item.state="failed"`, `fail_reason="queue_unavailable"` if still `pending` (`:690-696`). The route still returns HTTP 200 with the failed item (not an error response). On success dispatch -> `enqueued` (`:491-497`). Returns `get_item` (`:498`).

Concurrency: single active dispatch per item is enforced by partial unique index `uq_ingest_dispatch_item_active` on `ingest_dispatch(item_id) WHERE state IN ('pending','enqueued','running')` (`app/models.py:761-768`); idempotency by unique `(request_key, item_id)` (`:757-760`).

Other retry-like paths that exist today (relevant because they have their own gates):
- `IngestSubmissionService.retry_item` (`app/ingest/submission.py:786-955`, alias `:1011`): used by Agent action `retry_item_ingestion` (`app/agent/actions.py:428-461`) and MCP (`app/mcp_server.py:1031-1050`). Gate `if item.state != "failed": -> retry_not_allowed` (`submission.py:877-878`); archived/deleted -> `retry_not_allowed/item_not_found` (`:840-845`); active dispatch -> `retry_not_allowed` (`:879-880`); quota -> `quota_exceeded` status (`:900-908`). It has its own `state == "failed"` check independent of `ContentLibraryService.retry`.
- Re-save the same URL (`submission.py:571-784`): existing item with `state not in {"pending","failed"}` returns `already_exists` without any dispatch (`:657-658`), so a `needs_*` item re-saved by URL is NOT retried; `failed` (or `pending` with no active dispatch) gets a new dispatch after `enforce(include_new_item_limits=False)` (`:659-682`). Archived existing item returns `already_exists` immediately (`:643-644`).
- Re-save from the video page (capture path, `app/browser_capture_submission.py:190-329`): for an existing item it un-archives (`:239`), raises `capture_conflict` if a dispatch is active (`:240-247`), returns `already_exists/ready` only if `item.state == "ready"` (`:248-257`), otherwise enforces quota (`include_new_item_limits=is_new`, `:258-262`), overwrites metadata/raw key, sets `state="pending"`, `fail_reason=None`, adds `attempt+1` dispatch and a new `BrowserCapture` (`:277-329`). So any non-ready, non-active state (needs_*, failed) can be re-driven by a fresh capture today. This is the path the PRD relies on for "login to Bilibili then re-collect".
- Operator `asr-backfill` (`app/ingest/asr_backfill.py:94-216`): re-dispatches `needs_asr` for platform `youtube|bilibili` (`:40,73-74`), excluding capture-origin items unless YouTube with latest ready capture `caption_status == 'unavailable'` (`:53-84`). On publish failure it returns the item to `needs_asr` (`:216`), unlike `ContentLibraryService._set_dispatch` which makes it `failed/queue_unavailable`.

### 1.6 What the worker does when a `needs_*` item is re-dispatched (affects retry usefulness)

(`app/ingest/tasks.py`)
- Capture lookup: `connector is None` -> newest `ready` `BrowserCapture` for the item/tenant (`:368-381`).
- Capture with `caption_status == "unavailable"`: ASR hand-off only if `asr_handoff and settings.asr_enabled and item.platform in _ASR_CAPTURE_PLATFORMS` (= `{"youtube"}`, `:351,386-398`); otherwise `item.state = "needs_asr"`, `text_source="none"` (`:399-402`). **A Bilibili item whose newest ready capture is "unavailable" can never reach ASR on retry even with ASR on, because the capture branch precedes the connector.**
- No capture: connector `fetch_text`; `NeedsExtension` -> `needs_extension` (`:439-442`); `NeedsASR` -> ASR hand-off only if `asr_handoff and not pre_stored and asr_enabled and platform in {youtube,bilibili}` (`:347,443-454`), else `needs_asr` (`:455-457`).
- Bilibili connector yields `NeedsExtension` when login-only subtitles exist (`app/connectors/bilibili.py:355-356`), else `NeedsASR` (`:357`). YouTube yields `NeedsASR` (`app/connectors/youtube.py:255-257`). `ntu_kaltura` has no `NeedsASR` source (capture only).
- ASR hand-off keeps item `fetching` (`:396,452`); dispatch goes `running -> enqueued` on the `asr` queue (`:1274-1327`); publish failure converges to `completed/needs_asr` (`:1317-1324`).
- Terminal ASR errors (`AsrError`) are never retried; `_mark_dispatch_failed` writes `dispatch.error_code = exc.error_code`, `item.state="failed"`, `item.fail_reason = error_code` (`:1245-1251,1916-1921,1987-1993`).
- Mapping in `_mark_dispatch_failed` (`:1916-1921`): `AsrError` -> its code; `TransientFetchError` -> `transient_fetch_failed`; `IngestLimitExceeded` -> `ingest_too_large`; anything else -> `ingestion_failed`. **Connector codes such as `youtube_rate_limited` / `bilibili_rate_limited` / `*_proxy_*` are exception messages (`app/connectors/youtube.py:95,179-182`, `bilibili.py:199`, `app/asr/audio.py:57-72`) and never reach `dispatch.error_code`/`fail_reason`: `process_dispatch` re-raises `TransientFetchError("transient_fetch_failed")` (`:1233-1239`) and the Celery failure hook maps by type.** So a rate-limited fetch is persisted as `transient_fetch_failed`, which is already in `SAFE_ERROR_CODES`.
- When a worker ends in `needs_*`, the dispatch is `completed` with `error_code = None` (`:1488-1490`); `_terminal_item_state` only allows `ready|needs_extension|needs_asr` (`:1520-1528`). **Nothing durable records WHY an item is `needs_extension`/`needs_asr` (ASR off vs unsupported platform vs capture-unavailable vs Bilibili login).** The only discriminators are `item.platform`, newest ready `BrowserCapture.caption_status` / `caption_source`, `text_source` (`"none"` set at `:400`), and the current `settings.asr_enabled`.
- `no_text` is in the enum (`app/models.py:58-62`) and mapped to `needs_action` (`library.py:151`, `library_routes.py:132`) but **no code path ever sets it** (`grep no_text app/` shows only enum/projection/filter lists).
- Segments are deleted and re-inserted in the same transaction/commit that sets `state="ready"` (`:552-560`). There is no intermediate "chunks exist but not ready" committed state.

---

## 2. HTTP layer

### 2.1 Routes (`app/api/library_routes.py`, router prefix `/api/v1`, `:168-173`)

| Route | Lines | Service call | Notes |
|---|---|---|---|
| `GET /library/items` | `:179-210` | `library.list_items` | query: `search<=200`, `collection 1..20`, `lifecycle` Literal(6 values), `include_archived`, `sort` Literal, `page>=1`, `page_size 1..100`; returns `LibraryPageResponse` |
| `POST /library/items:batch` | `:212-234` | `submission.submit_urls` | `Idempotency-Key` header required; `require_save_enabled` (503); documented responses 503 |
| `GET /library/items/{id}` | `:236-242` | `library.get_item` | |
| `PATCH /library/items/{id}` | `:244-254` | `library.update_why_saved` | body `{why_saved}`; archived -> 409 `item_archived`; too long -> 409 `why_saved_too_long` (`library.py:367-376`) |
| `POST ...:archive` / `:restore` | `:256-278` | `archive` / `restore` | idempotent state flips (`library.py:379-393`) |
| `POST ...:retry` | `:280-303` | `library.retry` | `Idempotency-Key` required; documented responses: 429, 503 (409 is NOT documented in `responses=`) |
| `GET /ingest-dispatches/{id}` | `:305-316` | `library.get_dispatch` | `DispatchResponse` (`library.py:500-524`) |
| `GET /library/items/{id}/transcript` | `:318-336` | `transcript.get` | requires `item.state == "ready"` and `raw_object_key`, else `transcript_unavailable` (`app/web/transcript.py:134-135`) |

`scope_dependency` = `authenticated_scope` (`app/api/app.py:618-637`): extension Bearer session -> `UserScope(extension_user_id)`, else cookie session. Every service query filters `ContentItem.user_id == scope.app_user_id`.

Browser-companion routes (`app/api/browser_companion_routes.py`): `/extension/library` and `/extension/library/{id}` are stubs that always raise `extension_scope_invalid` (`:266-283`); workspace reads use the canonical library routes with the extension session Bearer (comment `:273-274`).

### 2.2 Response schemas (`app/api/library_schemas.py`)

- `BrowserModel`: `extra="forbid", from_attributes=True` (`:29-30`). The route builds `LibraryItemResponse.model_validate(dto)` (`library_routes.py:108-109`); response models read attributes off the DTO, so a DTO field with no response counterpart is silently ignored, and a response field missing from the DTO falls back to its default.
- `LibraryItemResponse` (`:52-79`): all 20 DTO fields, then `summary: str | None = None` (`:73`, **not on the DTO**, always null today, placeholder), `stage: Stage = Field(default_factory=lambda: "queued")` (`:78`), `stage_updated_at: datetime | None = None` (`:79`). The comment at `:74-77` explains the `default_factory`: it keeps `stage` out of the schema `default` key, so openapi-typescript emits `stage?:` (optional) like `page_qa`.
- `lifecycle: Lifecycle`, `error_code: str | None`, `available_actions: tuple[str, ...]` are plain; `error_code` is a free string (not an enum) so new reason codes do not change the generated TS type.
- `Lifecycle` and `Stage` Literals are duplicated in `library_schemas.py:13-26` and `library.py:41-56` and both are closed enums in OpenAPI/TS (`schema.d.ts:1096-1112`).
- `LibraryPageResponse` (`:82-87`): `items, total, page, page_size, is_true_first_empty`.
- `BatchItemResponse` (`:98-112`): `status` Literal(`queued, already_exists, unsupported_url, invalid_url, queue_unavailable, create_failed, quota_exceeded`), `item_public_id`, `lifecycle: Lifecycle | None`, `safe_error_code`.
- `ErrorResponse` (`:33-35`): `{code, message}`.
- Test fakes build items with `SimpleNamespace` lacking `stage` etc. (`tests/test_web_api_app.py:51-73`), which is why every added response field needs a default.
- Contract tests on the optional-field convention: `tests/test_library_extension_transport.py:202-224` (asserts `stage`/`stage_updated_at` are in `properties`, not in `required`, and the exact `stage` enum set); `tests/test_web_api_library.py:223-247` (forbidden-substring scan of OpenAPI).

### 2.3 Error responses

- `_safe_service_errors` (`library_routes.py:80-105`): `LibraryNotFound` -> 404 `not_found`; `LibraryConflict("save_disabled")` -> 503; `LibraryConflict("quota_exceeded")` -> **429** with `Retry-After: 60`; any other `LibraryConflict` code (`retry_unavailable`, `item_archived`, `invalid_lifecycle`, ...) -> **409** with that `detail`; `BatchValidationError` -> 422; `TranscriptError` -> 404/422/409.
- Final JSON is produced by `safe_http_error` (`app/api/app.py:519-527`): the `detail` is passed through only if it is in `_SAFE_HTTP_CODES` (`app/api/app.py:46-98`, includes `retry_unavailable :55`, `quota_exceeded :56`, `item_archived :54`, `ingest_too_large :63`, `transcript_unavailable :60`); otherwise it becomes `request_failed` (or `not_found` for 404). `_error_response` (`:820-835`) then needs the code in `_SAFE_MESSAGES` (`:178-...`, `retry_unavailable: "当前状态不能重试" :186`, `quota_exceeded: "已达到当前保存额度，请稍后重试" :187`) or it is coerced to `request_failed`. **Any new HTTP error code must be added to both `_SAFE_HTTP_CODES` and `_SAFE_MESSAGES`.**
- 422 on request validation: `SafeValidationRoute` (`library_routes.py:51-69`) and `safe_validation_error` (`app.py:529-539`) both return `{code:"validation_error", message:"请求参数无效"}`.
- Client side: `requestJson` throws `ApiError(status, code, message)` from the JSON body (`extension/src/api/client.ts:98-112`). `VideoDetailPage` does not branch on retry error codes (it shows a generic "操作未完成，请稍后重试。", `extension/src/videos/VideoDetailView.tsx:167`).
- Capture route quota: `BrowserCaptureSubmissionError("quota_exceeded")` (`browser_capture_submission.py:132-133`) -> HTTP 429 via `_safe_error` (`browser_companion_routes.py:299-300`), no `Retry-After`. Batch save conveys quota per item as 200 + `status="quota_exceeded"` (`submission.py:716-722`; schema `library_schemas.py:108`).

### 2.4 Capture/import response lifecycle mapping

- `_batch_item` (`library_routes.py:112-152`): internal `restored` -> `already_exists`; `purge_in_progress` -> `create_failed`; `queued` -> lifecycle `queued`; `queue_unavailable|create_failed` -> lifecycle `failed`; `already_exists` -> `archived` if `value.archived` else map `{ready: ready, needs_extension|needs_asr|no_text: needs_action, fetching|chunking|embedding: processing, pending: queued, failed: failed}` (`:127-138`); other statuses -> `None`. This mapping uses the raw 9-state `item.state` and does **not** call `project_lifecycle`, so it ignores dispatch state and `fail_reason`.
- `SaveItemResult.status` values (`app/ingest/submission.py:185-205`): `queued, already_exists, restored, purge_in_progress, retry_not_allowed, unsupported_url, invalid_url, queue_unavailable, create_failed, quota_exceeded`; `already_exists` is produced by `_already_exists` (`:1089-1101`) and carries `state=content.state`, `archived`.
- Extension capture response `BrowserCaptureResponse` (`browser_companion_routes.py:84-91`): `capture_id, item_public_id, platform, status: str, lifecycle: str, safe_error_code`. `lifecycle` is a free string; the service emits `"queued"` (`browser_capture_submission.py:166-172`), `"ready"` (`:248-257`) or, on replay, `item.state if in {"ready","needs_asr"} else "queued"` (`:358`) - i.e. the raw value `needs_asr` (not `needs_action`) can appear here.

---

## 3. Contract generation

- `extension/package.json:19-20`: `"check:api": "node workspace/openapi.mjs check"`, `"generate:api": "node workspace/openapi.mjs generate"`.
- `extension/workspace/openapi.mjs`: spawns `<repo>/.venv/bin/python scripts/export_web_openapi.py [--check]` with `PYTHONPATH=<repo>` (`openapi.mjs:6-34`).
- `scripts/export_web_openapi.py`: builds `create_app(services=WebApiServices(<placeholder objects>), expected_origin="https://contract.invalid", web_login_channels=("email",))` with no DB/broker (`:23-50`), takes `app.openapi()`, appends a few extra schemas (`ChallengeCreate*`, `ChallengeStatusResponse`, `ConversationStreamEvent`, `:51-65`), and renders `json.dumps(..., indent=2, sort_keys=True)`. `--check` compares the rendered text to `extension/src/api/openapi.json` byte-for-byte and `SystemExit("... is stale; regenerate it")` on drift (`:72-77`). Output: `extension/src/api/openapi.json` (`:21`).
- **`schema.d.ts` has no package.json script.** It is generated by `openapi-typescript` (devDependency `7.13.0`, `package.json:37`; binary in `extension/node_modules/.bin/openapi-typescript`), header "auto-generated by openapi-typescript" (`schema.d.ts:1-4`). Spec text only says "Regenerate `openapi.json` and `schema.d.ts` together" (`.trellis/spec/frontend/type-safety.md:55-61`); recent commit `07fe844` touched `openapi.json` (+27) and `schema.d.ts` (+7) together. The exact openapi-typescript command line is not recorded in any script or doc I found.
- **Drift protection**: CI job "OpenAPI contract" runs `pnpm check:api` (`.github/workflows/extension-api-contract.yml:46-48`) and then `pnpm typecheck && pnpm lint && pnpm test && pnpm build && pnpm run audit` (`:49-51`); `CONTRIBUTING.md:53` repeats this. There is **no test or CI step that checks `schema.d.ts` against `openapi.json`**; a stale `schema.d.ts` is only caught indirectly by `tsc` if code uses a field that is absent. Also no Python test reads `extension/src/api/*` (grep of `tests/` for `src/api|export_web_openapi|rendered_schema` is empty). Backend tests do assert exact OpenAPI/response content in places: `/api/v1/capabilities` exact-equality JSON (`tests/test_web_api_public.py:31-42`), `stage` enum set (`tests/test_library_extension_transport.py:202-224`).
- `extension/src/api/contracts.ts`: `import type { components } from "./schema"` (`:1`), `type Schemas = components["schemas"]` (`:3`), then aliases: `Capabilities = Schemas["CapabilitiesResponse"]` (`:10`), `BatchSubmitItem/Response` (`:25-27`), `LibraryItem = Schemas["LibraryItemResponse"]` (`:30`), `LibraryLifecycle = LibraryItem["lifecycle"]` (`:31`), `LibraryItemSummary = Pick<LibraryItem, "public_id"|"lifecycle"|"title"|"author"|"cover_url"|"duration_sec"|"saved_at"|"why_saved"|"url"|"error_code"|"available_actions">` (`:33-45`, does not include `stage`). Frontend spec forbids a second handwritten `LibraryItem`/lifecycle union and direct edits to `schema.d.ts` (`type-safety.md`, "Forbidden Patterns").
- Extension is type-only against the schema (no runtime validator): spec says "The client does not maintain a second handwritten Zod response schema" (`type-safety.md:40`). Old extension builds therefore ignore unknown JSON fields at runtime, but index closed maps with server values, e.g. `lifecycleCopy[item.lifecycle].label` with no fallback (`extension/src/library/lifecycle.ts:3-14`; `extension/src/videos/VideoDetailView.tsx:154`) and `STAGES.has(v.stage)` guarded parsing for stored collect entries (`extension/src/sidepanel/collectModel.ts:205`).
- Old-client behaviours worth knowing: "retry" button shown iff `available_actions` contains `"retry"` (`VideoDetailView.tsx:163`, `collectModel.ts:151`); `item.stage ?? stageFromLifecycle(...)` (`collectModel.ts:145`); needs_action outcome text is a constant (`extension/src/sidepanel/steps.ts:36,116-118`); detail page shows rate-limit messages by comparing `item.error_code === "youtube_rate_limited"|"bilibili_rate_limited"` (`VideoDetailView.tsx:168-169`), codes the backend never persists (see 1.6).

---

## 4. Retrieval filtering

### 4.1 Content search is gated on `ContentItem.state == 'ready'` (not on chunk presence)

All text retrieval paths share the same four predicates `user_id == tenant`, `deleted_at IS NULL`, `archived_at IS NULL`, `state == "ready"`:

| Path | Where |
|---|---|
| Eligible item ids for vector search | `app/retrieval/search.py:100-117` (`_eligible_item_ids_stmt`, predicates `:104-109`) |
| Vector hydrate (defense in depth) | `app/retrieval/search.py:170-180` (`state == "ready"` at `:174`) |
| BM25 (English tsquery and zh trigram) | `app/retrieval/search.py:200-223` (predicates `:202-207`; zh branch additionally `ContentItem.lang LIKE 'zh%'` `:219`) |
| Agent hydration of ranked segments | `app/agent/services.py:271-282` (`state == "ready"` at `:277`) |
| `get_neighbors` | `app/agent/services.py:342-376` (anchor `:355`, neighbors `:371`) |
| `open_at` | `app/agent/services.py:409-424` (`:421`) |
| Prior-turn source/inventory context | `app/agent/context.py:32` (`_READY_STATES = {"ready"}`), `:272-280`, `:390-396` (`_inventory_row_ready` also requires no `safe_error_code`) |
| Web transcript | `app/web/transcript.py:133-135` (`state != "ready" or not raw_object_key` -> `transcript_unavailable`) |

Spec backing: "Active Agent inventory, vector/BM25 hydration, neighbors, detail, and open-at paths require both [archived_at, deleted_at] to be null" (`.trellis/spec/backend/knowledge-item-management.md:55-61`); "Re-apply the same tenant/lifecycle predicates at hydration time" (`.trellis/spec/backend/agent-retrieval-convergence.md:576-578`).

### 4.2 Could a failed / needs_* item be returned by content search?

- **No via text retrieval**: every segment query above requires `state == "ready"`, so `failed`, `needs_extension`, `needs_asr`, `no_text`, `pending`, `fetching`, `chunking`, `embedding` are all excluded even if stale `Segment` rows exist. Segment rows are only written in the same commit that sets `ready` (`tasks.py:552-560`), but an item can be `ready` -> later forced to `failed` (e.g. `_mark_item_deleted`, `tasks.py:580-589`) while old segments remain; the `state == "ready"` predicate is what hides them.
- **Metadata-only exception**: The agent knowledge service's `get_item` (`app/agent/services.py:378-407`) filters tenant/deleted/archived (+ reference scope) but only enforces `state == "ready"` when `self._reference_scope is not None` (`:391-395`). Without a reference scope it can return `ItemDetails` (title, author, description, url, platform, duration; no body text) for a non-ready item.
- Media (image/video) retrieval: `app/retrieval/media.py` defines only a `MediaRepository` Protocol ("No database implementation is supplied here", `:55-62`); the state gates (`deleted`, `archived`, `ready`) live in `app/agent/media.py:65-86`; feature flag `MULTIMODAL_RETRIEVAL_ENABLED` default false (`app/config.py:365-367`).
- No search path matches on `Segment` for items with empty/missing text; there is no "empty body counts as a hit" path. Title/author/`why_saved` are **not** searched by retrieval (only by the library list ILIKE, 1.4).

### 4.3 archived / deleted exclusion

`archived_at IS NULL` and `deleted_at IS NULL` in every predicate in 4.1 (e.g. `search.py:105-106`, `services.py:275-276`). Library list: `deleted_at IS NULL` always; `archived_at IS NULL` unless `include_archived` or `lifecycle=archived` (`library.py:294-301`). Per-item read/mutate: `_owned_item` requires `deleted_at IS NULL` (`library.py:526-538`).

### 4.4 Facts relevant to a "body searchable" flag / count

- "Searchable" == retrievable == `state == 'ready'` (+ not archived/deleted) in every code path; `item.state` is the same column `project_lifecycle` reads (`library.py:148`). `text_source` is `"none"` by default (`app/models.py:528-530`), set to `none` explicitly at `tasks.py:400` and to the real source at `tasks.py:492`; for failed items it may hold a stale value, so it is not a reliable signal.
- Non-ready non-failure states that are neither "searchable" nor "stuck": `pending, fetching, chunking, embedding` (in progress).
- No existing aggregate/count query for per-user items by state; `list_items` only exposes `total` after lifecycle filter and `is_true_first_empty`.
- Existing indexes: `ix_content_item_user_saved_at (user_id, saved_at desc, id desc) WHERE deleted_at IS NULL` and `ix_content_item_user_archived_saved_at (user_id, archived_at, saved_at desc)` (`app/models.py:555-580`); no index on `state`.

---

## 5. Agent / MCP surfaces that expose ingest state

All three expose the **raw 9-value internal `ingest_state`** (via `item.state`), not the Web lifecycle/stage, and a coarse error code.

- `app/agent/agent_tools/actions.py:119-142` (`list_saved_items` tool): `state` filter is a closed `Literal` of the 9 enum values (`:124-127`), also validated again in `KnowledgeItemManagementService._filters` (`app/agent/management.py:555-561`).
- `app/agent/management.py:73-91` `SavedItem`: `ingestion_state: str`, `safe_error_code: str | None`; projection at `:586-588`: `ingestion_state=item.state`, `safe_error_code=_safe_error_code(item)`. `_safe_error_code` (`:239-245`): `purge_failed` if `purge_error_code`, else `ingestion_failed` iff `item.state == "failed"`, else `None` ("fail_reason is provider/exception text and is never projected"). So `needs_*` items show `ingestion_state="needs_asr"` with no error code; finer reasons are never exposed here.
- `app/mcp_server.py:190-205` `SavedItemProjection`: `ingestion_state: str (max 32)`, `safe_error_code: str|None (max 64)`; `list_saved_items` tool takes `state: str|None (max 32)` (`:1124-1145`); `retry_item_ingestion` tool description "Retry one failed ingestion dispatch" (`:1204`), calls `submission.retry_item` (`:1044`).
- Rendering: `app/agent/actions.py:792-830` prints `状态：{ingestion_state}` verbatim for channels; `_batch_outcome` (`:708-745`) has a fixed `safe_messages` dict keyed by `SaveItemResult.status` and indexes it with `safe_messages[value.status]` (a `KeyError` for any new status value). `retry_item_ingestion` (`:428-461`): `queued` -> ok, anything else -> failed with `result.safe_error_code or "retry_not_allowed"`.
- Agent context reuse: `app/agent/context.py:390-396` treats an inventory row as ready only if `ingestion_state in {"ready"}` and no `safe_error_code`.
- Completion notifications to channels (Telegram/WeChat): fixed messages by terminal `item_state` in `{ready, needs_extension, needs_asr, failed}` (`app/ingest/notifications.py:172-181`); DB CHECK on `ingest_completion_event.item_state IN ('ready','needs_extension','needs_asr','failed')` and the completed/failed/error_code coupling (`app/models.py:901-913`); `_completion_diagnostic` has its own error-code allow-list (`app/ingest/tasks.py:188-198`).

Compatibility implications (facts only):
- Adding fields to `LibraryItemResponse`/`LibraryItemDTO`, new `available_actions` strings, or new `error_code` strings does not touch Agent/MCP: they read `ContentItem` directly and project only `state` / `failed`-boolean.
- Adding a new **internal `ingest_state` enum value** would touch: PG enum (`app/models.py:58-62`), the two `Literal`/set lists (`actions.py:124-127`, `management.py:555-561`), MCP `state` arg (free str), `project_lifecycle` (`library.py:151-174`, otherwise falls to `failed/missing_dispatch` `:172-174`), `_batch_item` map (`library_routes.py:127-138`, unmapped -> `lifecycle=None`), `_terminal_item_state` and the completion-event CHECK constraints.
- Allowing Web retry of `needs_*` does not change `IngestSubmissionService.retry_item` (separate `state != "failed"` gate, `submission.py:877`), so Agent/MCP retry would keep rejecting `needs_*` unless that gate is also changed.

---

## 6. Quota

### 6.1 Definition and limits

- `IngestQuotaExceeded(Exception)` (`app/ingest/submission.py:216-217`): "Internal admission rejection with no private capacity details" - carries no message, so the caller cannot tell which limit tripped.
- `IngestQuotaPolicy` (frozen dataclass, `submission.py:220-394`): 7 optional limits - `max_active_per_tenant`, `daily_new_item_limit`, `max_items_per_tenant`, `max_active_global`, `daily_new_item_limit_global`, `daily_dispatch_limit_per_tenant`, `daily_dispatch_limit_global` (`:224-230`); `enabled` if any is non-None (`:254-267`); `acquire_locks` (global advisory lock when global limits set + `SELECT ... FOR UPDATE` on `AppUser`, `:269-282`); `enforce(db, user, include_new_item_limits)` (`:284-346`) raising `IngestQuotaExceeded`.
  - Always checked: global active dispatches `>= max_active_global` (`:293-300`), tenant active dispatches `>= max_active_per_tenant` (`:301-311`), and the daily dispatch limits (`:348-386`; day boundary is UTC midnight).
  - Only when `include_new_item_limits=True` (new item creation): `daily_new_item_limit_global`, `daily_new_item_limit`, `max_items_per_tenant` (`:315-346`; counts all of the tenant's rows incl. archived/deleted-in-trash).
- Production defaults (`app/config.py:386-411`): `INGEST_MAX_ACTIVE_PER_USER=10`, `INGEST_DAILY_NEW_ITEM_LIMIT=50`, `INGEST_MAX_ITEMS_PER_USER=1000`, `INGEST_MAX_ACTIVE_GLOBAL=100`, `INGEST_DAILY_NEW_ITEM_LIMIT_GLOBAL=300`, `INGEST_DAILY_DISPATCH_LIMIT_PER_USER=100`, `INGEST_DAILY_DISPATCH_LIMIT_GLOBAL=1000`. Built by `build_ingest_submission_service` (`submission.py:1205-1225`); the same policy object is shared with the library service and the capture service in the Web runtime (`app/api/runtime.py:74,92,152-156`). `ContentLibraryService` defaults to a disabled policy (`IngestQuotaPolicy()`, `library.py:241`) when none is injected.
- Per-item size limits (not quotas): `INGEST_MAX_RAW_TRANSCRIPT_BYTES`, `..._CUES_PER_ITEM`, `..._TEXT_CHARS_PER_ITEM`, `..._SEGMENTS_PER_ITEM`, `..._EMBEDDING_CHARS_PER_ITEM` (`config.py:413-427`) -> `IngestLimitExceeded` -> `ingest_too_large`.

### 6.2 Where enforced

| Entry | Call | Result |
|---|---|---|
| Save by URL, new item | `_enforce_ingest_quota(include_new_item_limits=True)` `submission.py:683-688` | `SaveItemResult.status="quota_exceeded"` (200 in batch response) `:716-722` |
| Save by URL, existing `pending|failed` item | `include_new_item_limits=False` `:674-678` | same |
| Capture (extension) | `self._quota_policy.enforce(..., include_new_item_limits=is_new)` `browser_capture_submission.py:258-262` | `BrowserCaptureSubmissionError("quota_exceeded")` -> HTTP 429 (`:132-133`; `browser_companion_routes.py:299-300`) |
| Web retry | `library.py:445-452` (`include_new_item_limits=False`) | `LibraryConflict("quota_exceeded")` -> HTTP 429 + `Retry-After: 60` (`library_routes.py:89-94`) |
| Agent/MCP retry | `submission.py:881-885` | `SaveItemResult.status="quota_exceeded"` `:900-908` |
| `asr_backfill` (operator) | **not enforced** (`asr_backfill.py:_admit`) | n/a |

The check is a synchronous admission gate: it raises before any item/dispatch write, so nothing is recorded on the item (confirms PRD "不会在收藏上记录原因").

How it reaches the client: `quota_exceeded` string with message `"已达到当前保存额度，请稍后重试"` (`app/api/app.py:187`) for HTTP 429; per-item `status="quota_exceeded"` in batch; extension maps both to the same UI outcome "未能收藏：收藏数量已达上限" (`extension/src/sidepanel/useCollect.ts:209,247-249`, `steps.ts:87`; `extension/src/library/AddVideosDialog.tsx:28,36`). Agent text `"已达到当前保存额度，请稍后再试或归档旧内容。"` (`app/agent/actions.py:733`). The code also appears in `app/diagnostics.py:67` and `app/agent/response.py:34` allow-lists. Quota is **never** a persisted item/dispatch `error_code` today.

### 6.3 Provider-side quota (ASR / embedding)

None. No per-user or per-day ASR/embedding counter, budget, or error code exists. What exists:
- ASR operational caps: `ASR_MAX_DURATION_SECONDS` (default 14400) -> `asr_too_long` (`app/asr/transcriber.py:195`), `ASR_MAX_AUDIO_BYTES` (default 524,288,000) -> `asr_audio_too_large`, timeouts (`app/config.py:470-483`).
- A hard-coded diagnostic cost table `_COST_PER_SECOND` (`transcriber.py:49,269-271`) used only for log records.
- Provider failures collapse to `asr_provider_failed` after Fun-ASR (`AsrProviderUnavailable|AsrProtocolError`) then Whisper both fail (`transcriber.py:216-241`, raises at `:231,239`), regardless of the provider reason (auth/arrears/429). `asr_not_configured` when neither provider client exists (`:193`).
- Embedding: `ingest_max_embedding_chars_per_item` only (`config.py:425-427`, enforced `tasks.py:512-520,540-546`); embedding provider errors surface as generic `ingestion_failed`.
- `SAFE_ERROR_CODES` has no quota/rate code (`library.py:28-40`).

---

## 7. Is there an endpoint telling the extension whether ASR is on / which platforms?

**No.** The only relevant public endpoint is `GET /api/v1/capabilities` (`app/api/app.py:545-555`), response model `CapabilitiesResponse` (`:117-140`): `supported_platforms` (static default tuple `youtube, bilibili, ntu_kaltura`, not config-driven, and means "can be saved", not "can be transcribed"), `browser_companion`, `web_login_channels`, `save_enabled`, `max_save_batch_size`, `transcript_pagination`, `archive`, `summary_generation` (default False), `chat`, `page_qa`. No ASR flag, no per-platform ASR matrix.
- The route handler passes only `web_login_channels`, `save_enabled`, `page_qa` (`:550-555`); it does not read `settings`. `page_qa` is the precedent for a runtime-derived flag (`page_qa_enabled = bool(services is not None and services.include_page_qa_routes)`, `:345`, set from `settings.page_qa_enabled` in `app/api/runtime.py:164`); declared with `Field(default_factory=lambda: False)` to keep the TS property optional (`:138-140`).
- `tests/test_web_api_public.py:31-42` asserts the exact capabilities JSON; adding a field to `CapabilitiesResponse` requires updating it. `tests/test_page_qa_api.py:484-485` checks `page_qa`.
- Extension already calls capabilities in the side panel: `deps.getCapabilities: () => Promise<{ page_qa?: boolean }>` (`extension/src/sidepanel/deps.ts:32`), consumed at `extension/src/sidepanel/SidePanel.tsx:55-57` (only `page_qa`); mocked as `{ page_qa: ... }` in `extension/src/test/sidepanelEnvironment.ts:202`.
- ASR enable flag lives only in settings (`app/config.py:441`, default `False`; key/URL validation only when enabled, `_validate_asr` `:850-889`, gate `:1093`) and is read by: `process_item` (`tasks.py:389,447`), `_asr_processor` (`:616`), the signed audio route registration (`app/api/app.py:700-714`), and the CLI (`app/cli.py:186`). `app/api/runtime.py`, `app/browser_companion.py`, `app/web_runtime.py` contain no ASR status exposure. ASR platform sets are code constants: `{"youtube","bilibili"}` for connector hand-off (`tasks.py:347`), `{"youtube"}` for caption-less browser captures (`tasks.py:351`, mirrored `library.py:60`), same `("youtube","bilibili")` for backfill (`asr_backfill.py:40`).
- `app/mcp_readiness.py` is MCP-mutation readiness only (no ASR).

---

## 8. Page Q&A (`app/page_qa/`)

- **Never reads library data.** Imports from `app.models` are only `ConversationThread, ConversationTurn` (`app/page_qa/service.py:75`); DB use is thread/turn lookup and persistence (`service.py:682-740`); no `ContentItem`, `Segment`, `IngestDispatch` anywhere in the package (grep of `app/page_qa/*.py`). Module docstring: "never calls `ChannelService` / `KnowledgeAgent` (no library retrieval, no tools) and it never writes cue text to the database, object storage, a broker or a log" (`service.py:1-9`); synthetic `PAGE_QA_ITEM_ID = 0` "page paragraphs are not library items" (`:94`). Route docstring: "Nothing is ingested" (`app/page_qa/routes.py:116-119`).
- Request: `PageQaMessageInput{message_id, question, playback_sec, page: PageQaPage}` with `PageQaPage{platform, platform_id, canonical_url, page_url, metadata, caption}` (`service.py:132-175`). With `caption.status == "unavailable"` only `youtube`/`bilibili` pages with usable metadata are accepted (`:151-160`). Answer statuses: `page_grounded`, `page_no_captions`, `page_unavailable` (`app/page_qa/__init__.py:19-21`).
- **No lookup-by-platform-id endpoint exists.** The library list has no `platform`/`platform_id`/`url` filter (`library_routes.py:179-210`; ILIKE only on title/author/why_saved, `library.py:302-310`); `GET /library/items/{public_id}` needs the server's public id (`:236-242`). Routes in the library router: list, batch save, get, patch, archive, restore, retry, dispatch, transcript only.
- How the side panel learns "already saved" today: only as a response to a save attempt - `already_exists` from `POST /library/items:batch` (`useCollect.ts:243-245`) or from the capture endpoint (`useCollect.ts:203`, `collectModel.ts:135`), after which it follows `item_public_id` by polling `GET /library/items/{id}` (`useCollect.ts:110` `fetch: () => deps().getItem(itemId)`; `deps.ts:36,134` `getItem: getLibraryItem`; poller in `extension/src/sidepanel/itemPoller.ts`). Between page loads it only remembers a per-video entry in `chrome.storage.session` keyed by `videoKey` (`extension/src/sidepanel/deps.ts:105-122`, key shapes `youtube:<id>` / `bilibili:<id>:p<n>` in `pageIdentity.ts:30-37,47-51`); that store is cleared when the browser closes.
- Current no-caption copy in the panel: "该视频没有字幕，提问将仅基于标题和简介回答" (`extension/src/sidepanel/PageWorkspace.tsx:37`).

---

## Related specs

- `.trellis/spec/backend/knowledge-item-management.md` (archive/delete/retry rules, lines 55-90)
- `.trellis/spec/backend/agent-retrieval-convergence.md` (tenant/lifecycle predicates at hydration, ~570-580)
- `.trellis/spec/backend/asr-transcription.md` (ASR states, error codes, allow-lists; "Item state during ASR is `fetching`. No new state or migration exists")
- `.trellis/spec/backend/ingest-completion-queue.md`, `.trellis/spec/backend/browser-companion-capture.md`
- `.trellis/spec/frontend/type-safety.md`, `.trellis/spec/frontend/quality-guidelines.md` (contract regeneration + `pnpm check:api`)

## Caveats / Not found

- Whether production has `ASR_ENABLED=true` was not checked (code default `False`, `app/config.py:441`).
- The exact `openapi-typescript` invocation used to produce `schema.d.ts` is not recorded in any script/doc found; only the dependency version and the "regenerate together" rule.
- Latest Alembic head not determined (my quick parse of `migrations/versions` was inconclusive; run `alembic heads` - it reads scripts only - before writing the migration).
- I did not run any tests; line numbers are against the working tree at dev `7f5a033` (untracked files unrelated to this research).
- `.claude/worktrees/side-panel-page-qa/` contains an older copy of the repo; all citations are to the main checkout.
