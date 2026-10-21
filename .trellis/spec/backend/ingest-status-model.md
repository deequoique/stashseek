# Ingest Status Model

> Origin: task `10-18-stashseek-ingest-status` (StashSeek phase 1, task 1), 2026-10-19.
> Consumers: enrichment (classification, topics, key points), content search,
> the side panel, the library and the detail page.

## Scenario: Per-stage ingest status, failure reasons and retry

### 1. Scope / Trigger

Read this whenever you:

- add or change an ingest stage;
- write a new failure code;
- change which items may be retried;
- add a client that shows ingest progress.

The contract spans the database, the worker, the Web API, the generated OpenAPI types and the extension UI.

Principles:

- **The body stage is projected, never stored.** It is derived from `content_item.state` plus the latest `ingest_dispatch`. Retrieval only returns `state == 'ready'`, so a second copy of the body status could disagree with search.
- **Only post-body stages are stored**, one row per stage in `content_item_stage`.
- **Existing enums stay closed.** Do not change the `lifecycle` and `stage` values, the `ingest_state` enum, the `ingest_dispatch.state` values, or the completion-event CHECK constraints. Old extension builds crash on an unknown `lifecycle`.

### 2. Signatures

**Database (migration `cff884c74a1e`)**

```text
content_item_stage(
  item_id BIGINT FK content_item.id ON DELETE CASCADE,
  stage TEXT CHECK (stage <> 'body'),
  status TEXT CHECK (status IN ('pending','running','done','failed','skipped')),
  error_code TEXT, CHECK ((status = 'failed') = (error_code IS NOT NULL)),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (item_id, stage))
ingest_dispatch.asr_handoff_at TIMESTAMPTZ NULL
```

**Python**

```python
# app/ingest/stages.py
KNOWN_STAGES = ("body", "classify", "summary", "movie")   # order + validity only
ENRICHMENT_STAGES: tuple[str, ...] = ()                    # stages the API reports
def mark_stage(db, item_id, stage, status, *, error_code=None) -> None  # upsert, no commit

# app/ingest/reasons.py
reason_for(code) -> IngestReason(code, category, retry_useful)  # unknown -> ingestion_failed/error
is_known_reason(code) -> bool

# app/web/library.py
project_reason(item, latest) -> IngestReason | None
project_body_stage(item, latest, *, asr_capture=False) -> IngestStageDTO
project_stages(item, latest, rows, *, enabled, asr_capture=False) -> tuple[IngestStageDTO, ...]
ContentLibraryService(..., enrichment_stages=None)          # None -> ENRICHMENT_STAGES
ContentLibraryService.availability_counts(scope) -> AvailabilityCounts
```

**Extension**: everything lives in `extension/src/ingest/`:

- `ingestCopy.ts`: all status copy;
- `ingestModel.ts`: `visibleStages`, `reasonOf`, `isUnfinished`, `allStagesSettled`;
- `IngestProgress.tsx`: prototype P01b/D02;
- `IngestUnfinished.tsx`: prototype P01c.

### 3. Contracts

**`LibraryItemResponse` gains three fields.** All are optional with defaults:

- `stages: IngestStageResponse[]`
  - `key: str` is a plain string, never an enum.
  - `status` is one of `pending | running | done | failed | skipped`.
  - `detail` is one of `queued | fetching | transcribing | indexing | null`. Only the body stage has it.
  - `reason` is set only when the stage failed.
- `reason: IngestReasonResponse | null`
  - Fields are `code`, `category` and `retry_useful`.
  - It is equal to the body stage's reason.
- `body_searchable: bool`
  - It equals `item.state == 'ready'`.
  - Archival does not change it.

**`LibraryPageResponse` gains two counts.** They cover items that are neither archived nor deleted:

- `unsearchable_total` counts `needs_action` and `failed` items.
- `processing_total` counts `queued` and `processing` items.

**`CapabilitiesResponse.transcription_platforms`** lists the platforms the server transcribes. It is `sorted(ASR_PLATFORMS)` when `ASR_ENABLED`, otherwise empty.

**Body stage projection**

| lifecycle | status | detail |
|---|---|---|
| queued | pending | queued |
| processing | running | `transcribing` if `item.state == 'fetching'` and (`latest.asr_handoff_at` is set or the latest ready capture had no captions); `indexing` if chunking/embedding; else `fetching` |
| ready | done | — |
| needs_action / failed | failed | — (reason set) |

Archived items are projected from their underlying state.

**Enrichment stage projection.** Only stages listed in `enabled` are reported:

- a stored row is used as is;
- with no row, the stage is `skipped` if the body failed, otherwise `pending`.

**Reason registry** (`app/ingest/reasons.py`):

| category | codes | retry_useful |
|---|---|---|
| no_speech | asr_no_speech | no |
| no_transcription | asr_disabled, asr_unavailable, asr_not_configured | yes |
| no_transcription | asr_unsupported_platform | no |
| fetch_failed | transient_fetch_failed, asr_audio_unavailable | yes |
| asr_failed | asr_provider_failed, asr_handoff_failed | yes |
| quota | quota_exceeded (**reserved, never written yet**) | yes |
| too_large | ingest_too_large, asr_too_long, asr_audio_too_large | no |
| login_required | bilibili_login_required, bilibili_capture_required, browser_capture_required | — (no server retry) |
| error | ingestion_failed (fallback), queue_unavailable, missing_dispatch, item_missing, item_deleted, capture_upload_failed | yes |

**Where reasons are stored**:

- `item.fail_reason` holds the code of the last unsuccessful attempt. For `needs_*` it is written by `process_item`, `_complete_dispatch(converge_reason=...)` and `asr_backfill`.
- It is cleared on `ready` and on every re-admission.
- Completion events are unchanged: a `completed` event keeps `error_code IS NULL`.
- Legacy `needs_*` rows with no reason read as `asr_unavailable`, or `bilibili_login_required` for `needs_extension`.

**Retry rule.** A single rule is used in three places:

- `project_lifecycle` (`available_actions`);
- `ContentLibraryService.retry`;
- `IngestSubmissionService.retry_item` for Agent/MCP, which allows the item states `failed`, `needs_asr` and `no_text`.

The rule: the lifecycle is `failed` or `needs_action`; no dispatch is active; the item is neither archived nor deleted; and the reason category is not `login_required`. A `ready` item is never retried, even when an enrichment stage failed. Per-stage retry is still undecided (as of 2026-10-19).

### 4. Validation & Error Matrix

| Condition | Result |
|---|---|
| `mark_stage` with stage `body` or one not in `KNOWN_STAGES` | `ValueError("unknown_stage")` |
| `mark_stage` with a status outside the five values | `ValueError("invalid_stage_status")` |
| `failed` without a code, or with a code not in the registry | `ValueError(...)`; nothing reaches the DB |
| any non-failed status with a code | `ValueError("stage_error_code_requires_failed_status")` |
| retry on ready, archived, in-flight or login_required item | Web 409 `retry_unavailable`; Agent/MCP `retry_not_allowed` |
| retry while over quota | Web 429 `quota_exceeded` (existing gate); the item keeps its state and reason |
| unknown code in `fail_reason`/`error_code` | public `reason.code = "ingestion_failed"`, category `error` |
| client sees unknown `lifecycle`, stage key or category | label falls back to "整理中"; unknown stage hidden; unknown category shows the `error` copy |

### 5. Good / Base / Bad Cases

- **Good.** A caption-less YouTube video is saved with ASR on. The body stage goes `pending/queued`, then `running/fetching`, then `running/transcribing`, then `running/indexing`, then `done`. `body_searchable` is true, and the panel shows "已整理，可检索".
- **Base.** The same video with ASR off: the item is `needs_action`, `reason.code = asr_disabled`, and `available_actions` contains `retry`. Retrying after ASR is enabled transcribes it.
- **Bad.** An enrichment stage fails and the code marks the item failed or unsearchable. This must never happen: the item stays `ready` and searchable, and only the region shows "未分类" or "暂无内容要点".

### 6. Tests Required

**Backend tests.**

- `tests/test_ingest_reasons.py`:
  - every `SAFE_ERROR_CODES` code and every new code maps to a category;
  - unknown codes become `ingestion_failed`.
- `tests/test_ingest_stages.py`: the `mark_stage` validation matrix and the upsert.
- `tests/test_web_library.py`:
  - `stages`, `reason`, `body_searchable` and `available_actions` per lifecycle;
  - legacy NULL reasons;
  - with `enrichment_stages=("classify","summary")`: a failed stage keeps the item `ready` and searchable; a failed body gives `skipped`; an extra unknown stage changes nothing;
  - the counts and tenant isolation.
- `tests/test_asr_ingest_flow.py`:
  - the reason for each `needs_*` branch, with ASR on and off;
  - `asr_handoff_at` is set on handoff;
  - `asr_handoff_failed` is written on publish failure;
  - a Bilibili capture without captions is handed off.
- `tests/test_ingest_submission.py`: the Agent/MCP retry rule.
- `tests/test_web_api_public.py`: the pinned capabilities JSON includes `transcription_platforms`.
- Migration tests: one head; upgrade and downgrade on the test DB.

**Extension tests.**

- `src/ingest/*.test.ts(x)`: copy coverage, unknown-value fallbacks, hidden `skipped` stages, a server without `stages`.
- `collectModel.test.ts`: polling stops for a new collect only when all stages settle, and for an `already_exists` entry as soon as the body is done.
- `SidePanelIngestStatus.test.tsx`: in-panel retry, re-collect after login, title search.
- `VideoDetailView.test.tsx`: the unfinished card, placeholders, the 429 copy.

### 7. Wrong vs Correct

#### Wrong

```python
# Store the body status in content_item_stage and keep it in sync with item.state.
mark_stage(db, item.id, "body", "done")
# Mark the whole item failed when classification fails.
item.state = "failed"
```

Search keys on `item.state`, so a duplicated body status drifts. Failing the item hides a searchable body.

#### Correct

```python
# Enrichment writes only its own row; the body stage is projected on read.
mark_stage(db, item.id, "classify", "failed", error_code="ingestion_failed")
# item.state stays "ready": lifecycle ready, body_searchable True, region shows 未分类.
```

## Adding a stage (checklist)

1. Add the key to `KNOWN_STAGES` (order matters), `STAGE_LABEL`/`STAGE_PLACEHOLDER`/`KNOWN_STAGE_KEYS` in `extension/src/ingest/ingestCopy.ts`.
2. Register any new failure code in `app/ingest/reasons.py`.
3. Write rows with `mark_stage` from the worker, as `pending`, then `running`, then `done` or `failed`.
4. Add the key to `ENRICHMENT_STAGES` only when the writer ships.
5. Decide how existing `ready` items without rows should read. They show `pending` forever unless you backfill them or write `skipped`. The side panel only waits for later stages on new collects.
6. No schema change and no OpenAPI enum change are needed.
