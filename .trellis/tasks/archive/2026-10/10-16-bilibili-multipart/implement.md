# Multipart foundation execution plan

Execution complete: product code and independent review passed, followed by native
parent browser acceptance. Work commit: `788b2ae`, dependent on SSO `2bb6dbc`.
The checklist below is the original plan, not the current task status. Exact
test counts and remaining pre-existing failures are in the parent verification.md.

## Start gate

- [x] User confirmed favorites plus independent multipart support; ten cap and
  all configured quotas retained.
- [x] Parent adopted stable CID, nullable source columns, 30-minute signed refs,
  default-false overlap consent and owned browser source resolver architecture.
- [x] Complete parent/child design and contexts reviewed; explicit final approval
  recorded before main-session task activation. This artifact does not start work.
- [x] Snapshot/preserve all existing SSO/BV/chat WIP. No commits, shared/production
  migration or deployment without applicable authorization.
- [ ] Read relevant full retrieval/citation sections manually: context injection
  warns that `agent-retrieval-convergence.md` exceeds its 32 KiB injection cap.

## Ordered implementation

1. **Schema**: Add nullable source columns/complete-source checks/known-CID partial
   uniqueness in one revision from current head. Extend typed source results with
   defaults. Verify model/DDL parity and unchanged NULL legacy rows; no backfill.
2. **Discovery/refs**: Fixed-host bounded part-map service and local 20-row paging,
   shared Redis tenant30/min/global2 leases, 15-second/1-MiB/1000-part limits.
   Implement purpose-separated HKDF/HMAC refs and tenant verification context.
   Test malformed/tampered/expired/other-account inputs and fail-closed gates.
3. **Canonical API/admission**: Implement named preview/import DTOs/routes from
   design, runtime injection and safe errors. Reuse Session/Origin/CSRF/save-enabled,
   stable idempotency and existing prepared admission/quotas/dispatch publishing.
   Keep ordinary URL preparation local P1. No separate job/queue authority.
4. **Legacy/dedupe**: Known-CID lookup wins; unknown legacy consent defaults false.
   Acknowledge only per-entry checkbox consent before separate save; preserve all
   old history/artifacts/notes. Cover overlap appearing after preview, simultaneous
   same-CID save, distinct parts, replay and rollback in isolated PostgreSQL.
5. **Worker hard CID proof**: Controlled repository-owned isolated yt-dlp Python
   API adapter/registered extractor captures the actual CID used for subtitle/
   playinfo extraction. Require equality to pin; absent/conflicting proof fails
   closed even if pre/post maps agree. Set writesubtitles=True and use
   extract_info(download=False), testing no files/media download and no raw logs.
   No-caption also attests CID. Keep no-playlist/bounds, use pre/post maps as extra
   guards and only then write metadata/raw/segments. Cover cached initial-state
   mismatch with unchanged page maps, reorder/removal, retries/base-URL fallback.
6. **Source/retrieval**: Owned-public-ID GET resolves current CID→P and fixed
   bounded-time redirect. Preserve provider-direct P/time generic URLs and trusted
   exact-part scope; deny LLM-authored routes and cross-tenant/deleted sources.
7. **Complete browser projection**: Apply server-issued links to detail/chapters/
   transcript/current JSON/SSE and reopened durable history with bounded batched
   owned lookups. Keep generic MCP/channel sources absolute/provider-direct.
8. **Reusable UI**: Native feature-local picker/direct-P resolver, generated API
   wrappers/AbortSignal/stable attempt key, ≤10 parts, immutable submitted snapshot,
   explicit legacy warning/checkbox/cancel/skip and private cache teardown. Expose
   shared rows/selection contract for favorites child, without folder discovery.
9. **Gates/handoff**: Generate canonical OpenAPI/types, focused backend/full
   frontend gates, isolated PG migrations/races and real-browser fake-transport
   flows. Update relevant specs during authorized finish and report exact evidence
   plus live-protocol limits. Preserve demo restrictions and unrelated WIP.

## Required evidence

| Area | Checks |
| --- | --- |
| Schema | One head; NULL legacy preserved; complete/positive source checks; isolated-PG uniqueness, concurrent dedupe and rollback. |
| HTTP/gate | Fixed host/path; no credentials/redirects; total deadline, streamed bytes, count/paging; Redis unavailable/cancel/crash behavior, global-two lease cap. |
| Ref/API | Strict bounded version/schema/TTL/MAC/tenant context; anonymous/Origin/CSRF/>10 blocked before writes; stable ordered partial/replay results; overlap default false. |
| Worker | Actual extractor CID==pin, including no-caption; cached initial-state mismatch rejected despite unchanged pre/post maps; missing proof fails; no file/media download; no mutation on mismatch; one-P/retry/reorder/identical-text isolation. |
| Browser | Detail/chapters/transcript/current JSON/SSE/history all use owned source opening; correct P/local time after reorder; safe deleted/foreign-owned/unavailable behavior. |
| Generic | MCP/Agent/channel provider-direct links retained; no relative-URL leakage or LLM source-scope expansion; legacy sources honest. |
| UI | Native picker/explicit selection/≤10, part counts, expiry/re-preview, default-off legacy checkbox/cancel/skip, partial outcomes, abort/account isolation and demo read-only. |

Use injected clock/transport/Redis and isolated PostgreSQL. Reuse relevant existing
Bilibili/submission/retry/transcript/retrieval/Agent/history/API tests alongside
new focused multipart tests. No real ingestion or unbounded list traversal. Do
not fix unrelated pre-existing test/WIP issues inside this scope.

The child is complete only after current and history navigation, source integrity,
generated contracts and shared picker are ready for `10-16-bilibili-favorites-preview`.
That sibling must reuse these authorities. No code/tests/migration/start/deployment
is performed by this planning artifact.
