# Design: Favorites and Independent Bilibili Parts

Planning only. The child designs specify canonical DTOs and implementation
contracts; this parent owns cross-child boundaries and compatibility.

## Architecture and Order

Build the multipart foundation first, verify it independently, then implement
public-favorites preview/UI. Use the canonical FastAPI app/production composition,
existing Session/Origin/CSRF, server tenant, shared admission locks, per-entry
results and durable ID-only ingestion queue. No durable import job or coordinator.
Keep the ten-entry limit, all quotas, worker concurrency and demo save capability.

Ordinary direct BV/share admission remains local-only and compatible. Collection
and part discovery are explicit operations; never enable playlist expansion in
the existing connector or hide many parts behind one admitted item.

## Discovery and Safe Transport

Accept HTTPS known space/favlist or medialist/detail/ml collection URLs, strictly
parse one numeric folder ID and construct fixed HTTPS api.bilibili.com requests.
Reject credentials, explicit ports, fragments, ambiguous IDs and host confusion.
Never fetch a supplied URL, provider next URL or arbitrary redirect.

Collection resource/list uses ps=20 and pn=1..500, one page per user request.
Part pagelist uses validated BV, a full descriptor cap of 1,000 parts, then local
slices of 20. Reached limits are visible and never described as a complete scan.
Each provider operation has a 15-second total deadline, streamed 1 MiB bound,
verified TLS, no redirects, no credentials and bounded decoded strings/counts.
Socket timeout alone is insufficient. Do not use yt-dlp resource/ids whole-folder
fetch followed by output truncation. Do not log raw bodies or part refs.

Preview/source opening use a separate tenant gate: 30 operations/minute, at most
two concurrent public-metadata upstream reads globally, bounded existing Redis state and fail-closed gate
errors. No login-bucket reuse, new polling job or provider call per retrieval hit.
Map provider/private/malformed/empty/rate/timeout states to fixed safe errors.
One successful sample is not a stable official API/production-network guarantee.

## Canonical API and References

Foundation contracts:
- POST /api/v1/bilibili/parts:preview {url,page=1}; fixed 20-row projection,
  part_key, part_ref, observed P, title/duration, expiry and legacy warning.
- POST /api/v1/bilibili/parts:batch {selections:[{part_ref,
  allow_legacy_overlap=false}],why_saved?}; at most ten, Session/Origin/CSRF,
  Idempotency-Key and save_enabled; ordered safe per-entry results.
- GET /api/v1/library/items/{item_public_id}/source?t=...; owned read-only
  bounded timestamp, fixed validated Bilibili 303 or safe unavailable error.

Favorites child adds POST /api/v1/bilibili/favorites:preview {url,page=1}, fixed
20-row collection pages. POST previews also use Origin/CSRF; no item/queue effects.
Use canonical Pydantic, inert OpenAPI exporter and generated TypeScript aliases.
No copied handwritten response unions or hidden provider-authority claims.

part_key is public BV:CID for stable in-memory UI selection, not authority.
part_ref authenticates bounded descriptor/version/expiry with purpose-separated
HKDF/HMAC from configured WEB_AUTH_SECRET, TTL 30 minutes and tenant verification
context. Do not serialize internal tenant IDs. Verify size/tampering/expiry and
account binding; no token grants login or quota bypass. Client CID/title/page
alone cannot authorize selected source identity.

## Storage and Legacy Policy

New explicit identity is stable (BV,CID); P is mutable navigation metadata.
Use strict internal BV_cidCID IDs plus nullable typed Bilibili BV/CID/page columns
on ContentItem and a tenant-scoped partial unique key for known BV/CID. Preserve
existing platform-ID uniqueness. One additive Alembic revision follows the actual
single head, including existing uncommitted SSO migration. Tests use isolated
PostgreSQL only. No startup/request migration or production DB access.

A part remains an ordinary item/dispatch/raw transcript/segment set. CID-qualified
raw paths keep even equal-text parts distinct; subtitle seconds remain P-local.
Never put source identity in tags, chapters, cue hashes or raw filenames alone.

Old BV/P1 rows keep IDs, text, history and NULL source fields. Current P1 metadata
cannot prove historical CID. Known matching CID dedupes; unknown legacy P1 is
never silently bound/merged/relabelled. UI warns before an explicitly accepted
new CID record may overlap old P1; default overlap admission is false. New content
is freshly fetched rather than copied from old subtitles. Generic BV input keeps
legacy dedupe, without claiming to know every explicit CID-qualified record.
Automatic historical consolidation is deferred.

## Source Fence and Navigation

Verify selected refs and commit immutable CID at admission before queue delay.
Shared submission retains all quota, duplicate, publishing-budget and partial
outcome behavior. A first worker fetch cannot guess the previewed CID.

For each known part, resolve CID to current P outside DB locks and fetch only
that P using a bounded, isolated yt-dlp adapter with --no-playlist semantics.
The adapter must capture and verify the actual CID selected by the extractor
for metadata and subtitle retrieval, from the same extraction snapshot. Missing
or mismatched actual-CID evidence fails closed; an ordinary stdout BV_pN ID is
not proof. Explicitly enable subtitle extraction while keeping media/file
downloads disabled. Verify the maintained dependency hook and no-caption path
with fixtures and a bounded metadata/subtitle canary before calling this ready.

Pre/post pagelist checks additionally require that the fetched P still maps to
the pin, but are not sufficient identity proof: cached page HTML and fresh list
responses can disagree. Only after actual-CID and mapping validation recheck
claim/deletion/purge/source ownership and apply metadata/raw pointers/segments.
Retry can resolve the same CID at a new P but cannot replace the CID. No early
wrong-metadata commit. Do not modify installed site-packages, invent an unverified
upstream endpoint or promise atomic provider snapshots/unavailable captions.

Backend/frontend provider link builders retain only validated p and t. For known
parts, browser transcript/chapter/current-answer/history source actions use the
owned source-opening route, which resolves CID at click time and redirects to
fixed HTTPS www.bilibili.com/video/BV?p=N&t=S. If content disappeared or cannot be
verified, show unavailable rather than fallback to P1 or stale ordinal.

Project browser source links from trusted owned public IDs using bounded/batched
lookups. Agent/MCP citations keep provider URLs, without unusable browser-relative
links. Browser projection deliberately rewrites trusted source actions; LLM text
cannot invent or authorize a resolver. Exact-reference scoping uses server-known
part/item identity rather than claiming local p parsing yields a CID. Legacy
unpinned sources retain direct-link/provenance limitations.

## Interaction and Failure Model

Reuse native Add Videos shell, separate favorites/parts view, existing local tag
picker and note rules. Default no selection; expand on demand; preserve chosen
part keys across loaded pages. Bulk actions apply only to displayed resolved
parts, never silent first-ten truncation. Show selected-P count/off-page summary.
Changing source/account clears state. Freeze payload/index mapping and immediately
block duplicate clicks. Part refs/results/keys remain memory-only.

Unknown transport outcomes use an exact frozen payload and in-memory key for
an explicit replay, not a new random attempt. Changed selections or deliberate
failed-item retries get a new operation. Do not blanket-retry accepted siblings.
Show queued/duplicate/failed/unknown truthfully, invalidate library and reuse its
normal visible-item polling. Closing/refresh does not roll back accepted work or
promise durable whole-import recovery.

## Rollout and Rollback

Production release is separate authorization. Migrate additively before candidate
code through normal one-shot admission. Keep source columns/part data on rollback;
never drop populated source identity or merge rows. Disable new routes/UI and use
a worker compatible with existing queued part IDs until drained. Preserve earlier
SSO/chat/history/BV WIP with scoped edits and explicit regression verification.

No product questions remain. Provider error/descriptor matrices and route wiring
still require implementation tests and bounded metadata canaries; technical
findings cannot silently weaken source identity or expand user scope.
