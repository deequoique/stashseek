# Research: Public Bilibili favorites backend integration

- **Query**: Reuse current browser/API/ingestion composition for bounded public-list preview and explicit import; compare a small MVP with automatic large-list import.
- **Scope**: Internal, read-only source inspection. The sample-access evidence is supplied by the parent task; no additional external requests, database connections or runtime changes were made.
- **Date**: 2026-10-16
- **Status**: Planning only. The parent subsequently confirmed the user's choice of option A: paged preview and at most ten selected videos per import. Other proposed routes, DTOs, numeric discovery bounds and flows below are recommendations, not implemented features or further user-approved decisions. The parent explicitly supplied this task path; this sub-agent's session-local `task.py current --source` reports no active task, so no task state was changed.

## Findings

### Existing files and reusable boundaries

| File | Verified behavior |
| --- | --- |
| `app/api/app.py:315`, `:523`, `:548`, `:606` | One canonical FastAPI app, request-scoped Session resolution, authenticated tenant scope and protected-mutation Origin/CSRF checks. Regular request bodies are capped at 64 KiB (`:48`, `:324`). API responses use the fixed safe error envelope and `no-store`. |
| `app/api/runtime.py:46`, `:76`, `:145` | Production composition builds the existing submission service with injected settings/session factory/publisher and mounts it through `WebApiServices`. New discovery should be another explicit injected service here, not a second app or Session authority. |
| `app/api/library_schemas.py:73` | `BatchSaveRequest.urls` is 1–10 strings, each 1–2048 characters; optional `why_saved` uses the existing shared length limit. No favorites or import-job DTO exists. |
| `app/api/library_routes.py:69`, `:210` | Existing `POST /api/v1/library/items:batch` requires authenticated scope, CSRF and `Idempotency-Key`; the raw key is SHA-256 hashed and namespaced by server-resolved tenant. `save_enabled` is authoritative. |
| `app/ingest/submission.py:474`, `:563`, `:610` | Local canonicalization, bounded batch preparation, then independent per-entry admission transactions and results. No collection expansion. |
| `app/ingest/submission.py:231`, `:1240` | Shared PostgreSQL admission policy and production builder enforce tenant/global limits for Web and channel submission. Reuse it unchanged for selected favorites. |
| `app/models.py:625`, `:700` | Unique tenant/platform/platform-ID item key; dispatch request/item uniqueness and active-dispatch uniqueness. Import identity is a canonical video ID, not its source favorites URL. |
| `app/ingest/tasks.py:71`, `:485`, `:495` | Redis-backed Celery ingestion already exists. Published ingestion messages contain only the durable internal dispatch ID; worker transient retries are separate from HTTP publishing. |
| `app/web/library.py:110`, `:315`; `web/src/library/LibraryPage.tsx:58` | Existing item lifecycle/available-actions and tenant-owned retry; the library polls visible queued/processing items every 4 seconds. This is item progress, not a durable whole-import report. |
| `app/connectors/bilibili.py:135`, `:188` | The connector is video-only and uses `--no-playlist`; it must not become an unbounded favorites expander. |

No favorites discovery route/service or durable import-job model was found under `app/` or `tests/` during this inspection.

### Current numeric limits and their implications

These are repository **defaults**, not verified deployed values (`app/config.py:295–329`, `:571`). Do not inspect secret environment files to infer production overrides.

| Setting/boundary | Default | Consequence |
| --- | ---: | --- |
| Batch request | 10 entries | Keep this request bound; a 20-entry preview does not imply a 20-entry import request. |
| `INGEST_MAX_ACTIVE_PER_USER` | 10 | With no existing active work, at most ten new active dispatches can be admitted at once. Existing work reduces this capacity. |
| `INGEST_DAILY_NEW_ITEM_LIMIT` | 50 | At most fifty new item rows per user per UTC day at this setting, subject to other limits. Creation/publication failure can still leave a stored row counted here. |
| `INGEST_MAX_ITEMS_PER_USER` | 1000 | Existing total rows also constrain a new import. |
| `INGEST_MAX_ACTIVE_GLOBAL` | 100 | Concurrent work from other users can reject an otherwise valid selection. |
| `INGEST_DAILY_NEW_ITEM_LIMIT_GLOBAL` | 300 | Global daily admission can bind before a user's own daily quota. |
| Daily dispatches/user; global | 100; 1000 | Deliberate new attempts and retries also consume dispatch budget; retries are not unlimited free work. |
| `WEB_PUBLISH_BUDGET_SECONDS` | 5 seconds | A whole existing HTTP batch shares one publishing deadline, not five seconds for every item. It is not a total database/request wall-clock bound. |
| `BROKER_PUBLISH_TIMEOUT_SECONDS`; max retries | 5 seconds; 1 | Native bounded publisher options clamp each broker operation to remaining budget (`tasks.py:144`). Worker transient retry/backoff remains independent. |

Quota admission takes a global advisory lock where needed, then a tenant row lock in stable order (`submission.py:279`). It counts pending/enqueued/running dispatches; daily counters use UTC. Existing duplicates/replays are checked before new-work quota enforcement. One batch may therefore return a mixture of `queued`, `already_exists`, `quota_exceeded` and queue failures; it is not all-or-nothing.

For the provided eight-entry `study` sample, the entire selection fits the ten-entry request cap. It only fits active/day/global quotas if the current account and installation have enough remaining capacity. This is not a promise that all eight can always be admitted.

### Idempotency, uncertain responses and retry

- Admission commits each item/dispatch before broker publication (`submission.py:728` onward). A later queue failure does not undo previously admitted entries. Preserve per-entry `item_public_id`/status and never label the whole request as having saved nothing.
- An identical request key for an already-created item replays its matching dispatch without republishing. A same-key `queue_unavailable` replays that failure (`submission.py:1133`); it is not a retry command. A deliberate retry needs a new operation key and current server-provided retry eligibility, preferably through the existing item retry route.
- Browser `submitVideoBatch` currently generates a fresh random key on every invocation (`web/src/api/client.ts:789`). A future transport retry of an unknown-result chunk should retain its original key and exact ordered payload. That requires a small explicit client/helper contract if implemented; the existing wrapper does not supply it today.
- The server's key is scoped to tenant/item dispatch, not an immutable batch-body ledger. Do not claim that reusing a key with changed selection reconstructs an original batch report.
- Item-level uniqueness prevents duplicate rows when a preview is refreshed and the user submits the same video again. It does not preserve the selection or a whole-import progress report across browser refresh.

### Minimum safe discovery boundary (proposal)

Use an authenticated same-origin preview endpoint and a dedicated public-list service. A read-only `GET` with bounded collection ID/page inputs is viable; a `POST` preview is also viable if body-based input is preferred, but must reuse existing exact-Origin/Session-CSRF protection. Neither may create items, dispatches or an import job. Apply `Cache-Control: no-store`; account/session switching must clear its frontend state through the existing private query ownership.

1. Parse an explicitly supported collection-link form locally. Reject credentials, explicit ports, fragments, host confusion, duplicate/ambiguous collection IDs and malformed identifiers. Treat page/list IDs as identifiers, not caller-controlled fetch destinations.
2. Construct the outbound URL from the fixed HTTPS `api.bilibili.com` host and fixed list path; add only validated server-owned parameters. Do not request the supplied page URL, follow arbitrary redirects or fetch a provider-returned `next` URL. Keep TLS verification; send no Cookie/authentication/SESSDATA/WBI data.
3. Fetch **one page per preview request**. Suggested initial page size is 20, with a 1 MiB body bound and a 15-second whole-call deadline matching the successful canary. These are suggested new discovery bounds, independent of the existing 30-second video metadata timeout. Stream/limit bytes before JSON parsing; do not rely solely on Content-Length. A socket read timeout alone is not a whole-response deadline against slow incremental responses.
4. Enforce bounded page integer, response count, strings and decoded fields. Use provider `has_more` as a hint, not permission for an unbounded loop. A reported total is not a snapshot guarantee. If design adds a preview-session/page-count cap, display the cap and remaining unvisited list explicitly instead of silently truncating.
5. Project only safe video preview fields and canonical IDs/URLs. Validate every returned BV through existing local normalization. Treat removed/private/non-video/invalid entries as unavailable rows or counted skips; never turn arbitrary provider URLs into importable destinations. Titles are plain escaped text. Covers need the existing `hdslb.com` allowlist and length bounds or can be omitted for the smallest MVP.
6. Add a **separate** tenant/IP request-rate/concurrency budget for discovery. Ingestion quotas do not limit previews. Existing Redis is available as infrastructure, but login rate-limit buckets are login-purpose-specific and should not be repurposed. A short-lived bounded Redis cache/limiter is an optional implementation choice; it does not require a migration or make Redis the durable import authority.
7. Return fixed safe upstream-unavailable/rate-limited/timeout/private-or-unavailable/invalid-response errors without raw provider bodies, exception text, URLs or credentials. Never turn an upstream failure into an empty successful list.

`app/external_providers/http.py:40` demonstrates fixed-host TLS, no redirects, streamed byte caps and opaque errors, but it is SSO-owned with provider-specific errors, 10-second timeouts and a 128 KiB limit. Reuse the principles or a small appropriately scoped transport; do not couple favorites discovery to OAuth services. `app/connectors/bounded_fetch.py` is a YouTube-subtitle helper with a different host policy and is not a ready-made safe public-list fetcher.

Do **not** invoke the whole-list yt-dlp IDs path and then limit printed rows. The parent canary notes that IDs discovery may already have fetched the entire collection before output filtering. One paged resource-list request is the bounded unit.

## Minimal options (recommendations, not decisions)

| Option | New backend work | Persistence/queue changes | Honest limits |
| --- | --- | --- | --- |
| **A: Preview 20 per page, select at most 10, explicit import; repeat another batch** | Dedicated authenticated bounded preview route/service/DTO, composition injection and tests. Selected canonical videos use the existing batch endpoint. | **No new durable job, table, migration or Celery task needed.** Existing Redis may support preview abuse control. | Best smallest MVP. No automatic whole-folder import; preview pagination is user-triggered. Refresh may lose selection/report while stored items remain. |
| B: Select a larger bounded set, browser orchestrates sequential chunks of at most 10 | Same preview plus explicit client chunk keys/progress/cancel/resume logic; optional richer safe capacity/result feedback. Existing submit endpoint remains usable. | No durable job required **only if the UI does not promise continued work or exact recovery after refresh/closing**. | Stop on quota/queue failures; don't blindly fire all batches or parallelize around the active cap. A generic `quota_exceeded` currently cannot distinguish temporary active capacity from daily/storage exhaustion, so indefinite automatic waiting/retry is unsafe. |
| C: Automatic whole-list import with reliable refresh/server recovery | Bounded paged coordinator, durable job/items ledger, ownership, quota/backpressure policy, progress/status/cancel/retry contracts, and retention. | Likely requires new schema/migration and coordinator execution/recovery; existing per-video dispatches alone are not a whole-import ledger. | Larger scope. Necessary only if that durable automation behavior is explicitly chosen; preview alone does not justify it. |

**User-selected option: A**, as reported by the parent after this research file was first saved. It covers preview/selection and this eight-video sample while respecting current ten-entry and quota boundaries. This is a product-scope choice, not authorization to start implementation; detailed design and final approval still belong to the parent task.

### Why ten, and what a multipart Bilibili video means today

The ten-video selection bound is the existing per-request API/service limit, aligned with the default ten active dispatches per user; it is **not** a discovered Bilibili favorites API limit and is not an intrinsic maximum collection size. The UI can page through a larger public list and let the user deliberately submit another batch later. Existing active work and daily/global/storage limits can still cause partial admission even when a selection has ten or fewer entries. Increasing the number alone would not increase capacity or fix quota/backpressure.

Multipart facts from the current implementation:

- `BilibiliConnector.match` (`app/connectors/bilibili.py:135–161`) accepts `p` omitted or exactly one `p=1`; `p=2`, duplicate selectors and empty `p` are rejected. A base multipart video URL therefore selects the first part; it does not mean every part.
- `_run_metadata` (`bilibili.py:188` onward) passes yt-dlp `--no-playlist`. Favorites discovery should keep using its separate bounded list endpoint; it must not remove that safety flag to implement multipart expansion accidentally.
- The connector returns only the BV/av ID as `platform_id`, and canonical URLs omit a part selector (`bilibili.py:159–177`). `ContentItem` uniqueness is `(user_id, platform, platform_id)` (`app/models.py:625`). There is no per-part `cid`/part index in this identity. Submitting different `p` variants as distinct strings cannot safely create distinct part items under the current identity.
- The ingestion transcript/cue model and source jumps are for the admitted item. Bilibili timestamp links currently append `t` to the canonical video URL (`app/browser_capture.py:254` onward); they do not bind a selected part/cid. A part-specific subtitle starts at its own time zero, so dropping the part while retaining timestamps can jump into the wrong part.

Supporting an entire multipart set is consequently a separate design concern, not a batch-size adjustment. It would require deciding whether one BV is an aggregate item or each `(BV, cid/part)` is its own item; proving stable part discovery and ID semantics; updating item dedupe and stored source URLs; binding each subtitle/segment/timestamp to its correct part or designing an explicit aggregate timeline; defining quota/selection/progress counts per BV versus per part; and testing existing BV history/citations and migration compatibility. No such feature is implemented or approved by the current choice of public-favorites preview/import.

For this first version, disclose the current first-part limitation truthfully instead of implying all parts were imported. Whether favorites preview should detect a multipart indicator or display a general note is still a design/UX proposal; do not add per-video metadata fetches to every preview row just to promise that detection without a new bounded fetch budget.

### Proposed preview DTO and pagination contract

A small DTO could contain `collection {id, title, reported_total}`, `page`, `page_size`, `has_more`, and bounded `items [{bvid, canonical_url, title, selectable, unavailable_reason?}]`. A collection-public-page URL is optional display metadata, never the next fetch destination. New DTOs/routes must be generated through the canonical OpenAPI exporter and TypeScript aliases. Exact field names and safe error enums remain design work.

For explicit page navigation, immutable provider snapshots are not available from current evidence: collection changes can shift entries, causing repeats or omissions between requests. Deduplicate displayed/selected BV IDs, retain order, show truthful fetched/selected counts, and say the list may change. “Select all” must mean a clearly bounded loaded scope unless a deliberate bounded full traversal is separately designed. Do not report “all imported” from an unseen reported total.

Partial progress for A needs only the existing ordered batch result plus normal library/item lifecycle. A successful `queued` result means accepted for ingestion, not subtitle-ready or import-complete. Unavailable upstream rows need not enter ingestion. For B/C, additionally define operation/chunk identity, accepted/skipped/failed/not-attempted counts, exact retry targets, stop reasons, cancellation of future work versus already-enqueued ingestion, and refresh/account-switch behavior.

## Related specs and validation suggestions

- `.trellis/spec/backend/web-browser-runtime.md`: canonical app/Session/CSRF/safe DTO ownership.
- `.trellis/spec/backend/bilibili-connector.md`: video-only local admission, no cookies, no playlist expansion, no subtitle-success promise.
- `.trellis/spec/backend/ingest-completion-queue.md`: durable ID-only queue envelopes; preview does not need this completion outbox or a new subscriber.
- `.trellis/spec/backend/database-guidelines.md`: any later migration requires isolated database verification; none is needed for option A.
- `.trellis/spec/frontend/type-safety.md`, `quality-guidelines.md`: server-defined generated contracts, existing tenant cache lifetime and honest partial outcomes.

Future tests should cover strict collection URL/ID parsing, exact outbound host/path/parameters, redirects rejected, deadline/bytes/page limits, malformed/private/rate-limit responses, unavailable entries, no DB/queue side effects during preview, anonymous/CSRF/account boundaries, duplicate selection, more-than-ten blocking, queue failure/quota partial results, and stable idempotency on uncertain request retry. Existing ingestion/BV/URL tests remain regressions. No tests or live fetches were run for this research-only assignment.

## Caveats / Not Found

- The parent `research/public-sample-access.md` records one successful anonymous first page for the supplied public URL: `study`, eight entries, `has_more=false`. It is a point-in-time host/network canary, not an official API support guarantee or a guarantee of production egress access/public accessibility for other folders.
- No external documentation was fetched here; the parent owns official/maintainer source verification. Read-only sample evidence is referenced, not repeated as this agent's independent network observation.
- A private folder, a deleted entry, platform blocking and missing subtitles are distinct situations; current provider-list response semantics still need the parent's source/canary matrix before finalizing error mapping.
- No current setting explicitly controls favorites preview. Any proposed enable flag, timeout/body/page cap or rate-limit setting is new design work, not an existing feature. Existing `save_enabled=false` on the local demo must remain authoritative; do not turn on writes for a canary.
