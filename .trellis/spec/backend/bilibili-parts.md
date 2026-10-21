# Bilibili Explicit Parts and Public Favorites

## 1. Scope / Trigger

Use for part/favorites preview, signed import, CID provenance and part source
navigation. Ordinary BV/share input remains the local P1 compatibility path in
[Bilibili Connector](./bilibili-connector.md). These are local implemented
contracts, not a production release or completed native-browser/full historic
database migration claim.

## 2. Signatures

~~~python
BilibiliPublicClient.get_parts(bvid: str) -> BilibiliPartMap
BilibiliPartMap.resolve_part(cid: str) -> BilibiliPart
BilibiliPublicClient.get_favorites(
    collection_id: str, *, page: int = 1,
) -> BilibiliFavoritesPage
parse_video_input(value: str) -> tuple[str, int | None]
parse_favorites_input(value: str) -> str
PartRefSigner.issue_with_expiry(tenant_id: int, part: BilibiliPart) -> tuple[str, int]
PartRefSigner.issue(tenant_id: int, part: BilibiliPart) -> str
PartRefSigner.verify(tenant_id: int, ref: str) -> BilibiliPart
BilibiliService.preview_parts(scope, url: str, *, page=None) -> dict
BilibiliService.preview_favorites(scope, url: str, *, page=1) -> dict
BilibiliService.submit_parts(
    scope, selections, *, why_saved, request_key, publish_budget_seconds,
) -> BatchSaveResult
BilibiliService.open_source(scope, item_public_id: str, *, timestamp=0) -> str
IngestSubmissionService.submit_prepared(
    tenant, prepared: PreparedBatch, *, why_saved, request_key,
    publish_budget_seconds=None, source_thread_id=None,
) -> BatchSaveResult
BilibiliConnector.fetch_part(
    bvid: str, cid: str, page: int, *, video_title=None, part_title=None,
) -> tuple[ItemMeta, TextResult | NeedsExtension | NeedsASR]
~~~

Canonical routes:

~~~text
POST /api/v1/bilibili/favorites:preview
POST /api/v1/bilibili/parts:preview
POST /api/v1/bilibili/parts:batch
GET  /api/v1/library/items/{item_public_id}/source?t=0
~~~

ContentItem adds nullable bilibili_bvid VARCHAR(12), bilibili_cid VARCHAR(20),
bilibili_page INTEGER. Revision f8a9b0c1d2e3 follows SSO e2f3a4b5c6d7.
Check ck_content_item_bilibili_source allows all NULL or complete Bilibili-only
strict BV/positive canonical decimal CID/page1..1000. Partial unique
uq_content_item_user_bilibili_cid covers (user_id,bilibili_bvid,bilibili_cid)
where CID is known; retain tenant/platform/platform-ID uniqueness.

## 3. Contracts

### Canonical browser and DTO boundary

- Canonical app/runtime own Session, exact Origin/CSRF, no-store and safe errors.
  Reject request extras. Preview creates no items/dispatches.
- Every route is behind `BILIBILI_PUBLIC_ENABLED` (default false). While off,
  `bilibili_parts_enabled`/`bilibili_favorites_enabled` are false and
  favorites:preview, parts:preview, parts:batch and the owned `source` redirect
  answer `503 bilibili_disabled` without touching the service. The feature needs
  server egress to api.bilibili.com; datacenter IPs are not assumed routable.
  While off, library item URLs, transcript blocks and chat citations keep the
  provider `bilibili.com` address instead of the disabled `source` resolver, so
  no browser is handed a link that can only fail.
- An explicit part never falls back to browser capture. When its captions are
  unavailable it ends in `needs_action` with `part_transcription_unsupported`
  instead of an ASR hand-off: the ASR downloader addresses a BV URL, not a
  pinned CID, so transcribing it could attach another part's audio.
- PartsPreviewRequest: url1..2048, complete BV or strict HTTPS Bilibili video URL,
  absent/exactly one p1..1000. Reject credentials/ports/fragments/prose/duplicate
  selectors/collection URLs. Optional page is local page1..50 of 20 parts;
  omitted page contains requested p, otherwise page one. Select nothing by default.
- PartsPreviewResponse projects bvid/title/page/page_size20/total_parts<=1000/
  has_more/requested_page and <=20 parts. Rows contain part_key=BV:CID, part_ref,
  exact signed expires_at, current page, title/duration_sec, selectable/reason
  and has_unverified_legacy_item. UI consumes expiry, never decodes opaque refs.
- PartsBatchRequest selections1..10 has part_ref and strict Boolean
  allow_legacy_overlap=false, plus existing why_saved bounds. Require save-enabled
  and Idempotency-Key. Verify typed refs then reuse prepared admission, configured
  global→tenant locks, quotas, transactions, dispatch-ID publishing and total budget.
  Each P is one quota/item/dispatch unit; no existing limit increases.
- Preserve ordered result_id/input_index/partial results; add part_ref_invalid,
  part_ref_expired, legacy_identity_unknown. Invalid peers cannot write or block
  valid peers. Queued does not promise subtitles. Freeze ordered refs/consent/
  destination/key; unknown retry uses the exact same in-memory payload/key.
  Expiry needs re-preview; no durable refresh continuation is promised.

### Favorites and bounded public discovery

- FavoritesPreviewRequest is {url,page=1}, url<=2048 and strict page1..500.
  Parse only HTTPS space.bilibili.com/<positive uid>/favlist?fid=<positive id>,
  or bilibili.com/www.bilibili.com/medialist/detail/ml<positive id>. IDs are
  canonical positive decimal <=20 digits. Reject credentials/ports/fragments,
  duplicate/unknown query fields, short links and other list kinds. Space permits
  optional ftype=create/known ignored sharing fields; detail/ml cannot add fid/
  ftype. Parsing returns an ID, never a fetch destination.
- FavoritesPreviewResponse: collection_id/title/reported_total/page/page_size20/
  has_more/capped/items<=20. FavoriteVideo: entry_key, nullable bvid/canonical_url/
  title, available/reason (deleted/not_video/invalid_video/null). Parent
  availability allows parts expansion, never an import proof or P1 fallback.
- Fixed upstream paths are api.bilibili.com/x/web-interface/view for parts and
  /x/v3/fav/resource/list with validated media_id/pn/ps20 for favorites. One
  favorites page/request, no whole-list IDs/provider next URLs/per-parent eager
  parts. Page500 with further data sets capped=true. Validate envelope/count/
  Boolean has_more/rows and matching parts BV/sequential pages/unique CIDs.
  Preserve unavailable rows; provider URLs never become video references.
  True empty differs from private/malformed/failed. Favorites service does no DB/
  queue work. These observed endpoints have no official stability guarantee.
- Repository-owned killable subprocess: 15-second whole-call deadline, 1 MiB
  cap before JSON parsing, configured verified TLS, no inherited proxy/redirects/
  cookies/auth. Oversized/>1000-part data fails explicitly, never silent truncation.
- Shared RedisPublicGate: tenant30/min browser parts/favorites/source admission,
  global two browser-discovery upstream leases, atomic Redis TIME/Lua, unique
  20-second expiring tokens released only by their owner. Redis outage fails
  closed. Worker low-level public calls have no browser tenant admission.

### Signed source identity and extraction

- WEB_AUTH_SECRET>=32 derives HKDF-SHA256 info=stashseek/bilibili-part-ref/v1;
  HMAC-SHA256 signs canonical {v:1,bvid,cid,page,expires_at} for 30 minutes.
  Tenant appears only in MAC context. Reject extra/noncanonical/version/purpose/
  bounds/tamper/expiry/other-tenant errors. Refs grant no auth/quota bypass.
- Keep refs/selection only in private component/query memory, never storage,
  URLs/history/logs. bilibili_parts_enabled/bilibili_favorites_enabled report
  composed support, not current provider/Redis health. Demo writes stay disabled.
  Diagnostics omit raw URLs/titles/refs/provider bodies/credentials/exception text.
- New explicit key is server-owned BV_cid<CID>; immutable identity is (BV,CID),
  page is mutable navigation. Known CID reuses its item, including proven legacy
  BV keys, preserving old key/public ID/history. Unknown legacy stays NULL:
  today's P1 is not historical proof. Reject overlap by default and require a
  default-off per-entry checkbox before separate saving.
- HTTP verifies refs locally. Worker resolves pinned CID's current P, then
  repository-owned isolated PinnedBiliBiliIE records actual subtitle-call BV/CID
  after the real _get_subtitles returns and rejects conflicting playinfo CID.
  Actual proof is mandatory even without captions. Requested CID/CLI ordinal/
  matching pre/post maps are not substitutes.
- Use writesubtitles=True with extract_info(download=False,process=False), no
  playlist/config/plugins/cookies/cache/files/media downloads. Project only
  bounded allowed inline SRT/metadata; retain raw/cue/segment/embedding limits.
- After proof, re-read CID→P and require fetched P consistency; re-lock deletion/
  purge/dispatch ownership/unchanged pin before content writes. Retry preserves
  pin. Mismatch cannot overwrite old metadata/raw/segments, delete old segments
  or start embedding. Equal-text parts stay distinct under tenant/platform/
  stored-platform-ID/raw-hash namespaces and part-local cue time zero.

### Source navigation and compatibility

- Owned source GET takes integer t0..2147483647. Check Session/owned nondeleted
  item/pin, resolve current P from a short-lived cached part map, recheck
  ownership/pin, then 303 only to fixed
  https://www.bilibili.com/video/<BV>?p=<current>&t=<bounded>. Never P1-fallback.
  Provider throttling/busy during discovery or extraction is retryable
  (`TransientFetchError`), not a permanent source failure. If discovery fails
  during a click, redirect to the item's last attested page instead of showing
  the API's JSON error body.
- Browser detail/chapters/transcript/current JSON/SSE/reopened history consume
  server-issued paths. Batched owned segment provenance authorizes citation/
  history mapping, never URL host/LLM resolver text. Unprovable old absolute
  sources retain honest legacy links; explicit foreign/deleted provenance is omitted.
- Generic Agent/MCP/channel links stay provider-direct with validated P/local t;
  no browser resolver leakage. Exact part scope uses trusted item identity.
- Migration never backfills CID; downgrade refuses provenance removal after
  pinned rows exist. Local work does not authorize shared/production migration,
  deployment, sample import or destructive rollback.

## 4. Validation & Error Matrix

| Condition | Required outcome |
| --- | --- |
| Anonymous/wrong Origin/mismatched CSRF | Existing safe Session/CSRF failure. |
| Invalid URL/selector/page/extras | Safe 422 before arbitrary fetch/writes. |
| Private / missing or malformed folder | Explicit private / unavailable; never empty success. |
| >1 MiB, >1000 parts or >20 favorites rows | bilibili_limit_exceeded. |
| Page500 with more data | capped=true; no eager page501. |
| Rate/lease/Redis failure | bilibili_rate_limited/bilibili_busy/bilibili_unavailable. |
| Provider HTTP429 or observed risk-control code -352 | Same safe bilibili_rate_limited, with no provider body/text. |
| Tampered/other-tenant/expired proof | Per-entry part_ref_invalid/part_ref_expired; no writes. |
| >10 or non-Boolean consent | Reject before writes. |
| Known CID / unknown legacy without consent | Reuse / legacy_identity_unknown. |
| Actual CID absent/conflicting, including no-caption | source_unavailable/source_changed; no content mutation. |
| Reorder during extraction/postcheck | Safe failure; later retry resolves same CID. |
| Missing/deleted/foreign source | Error without wrong-part redirect. |
| Quota/queue/peer failure | Ordered partial result, no ready-subtitle promise. |

## 5. Good / Base / Bad Cases

- Good: CID102 moves P2→P3 before ingest. Resolve/prove CID102 at P3 and resolve
  it again on source opening.
- Good: reuse independently pinned legacy BV-key item without renaming history.
- Good: preview eight public parent rows, expand only requested parts, select none.
- Base: ordinary BV/share remains local P1 with old IDs/history.
- Bad: infer old CID from today's map, silently select all P, follow provider
  next URLs, or redirect to P1 after saved CID disappears.

## 6. Tests Required

- tests/test_bilibili_parts_core.py and tests/test_bilibili_parts_api.py:
  strict bounds, whole-call timeout, MAC/tenant/
  expiry/exact public expiry, consent, local P1, canonical auth/CSRF/partial/303.
- tests/test_bilibili_parts_postgres.py: isolated schemas, nullable additive up-down-up, constraints,
  concurrent known-CID dedupe, legacy consent/reuse, source reorder. Selected
  tables do not prove full historic pgvector migration history.
- tests/test_bilibili_multipart_extract.py: actual mismatch despite stable maps, missing/no-caption
  proof, no files/media, retry/reorder/proven legacy keys, preserved content and
  equal-text part isolation.
- tests/test_bilibili_favorites.py: aliases/attacks, eight-row/unavailable/empty/private, fixed operation,
  page500/cap, no DB/parts/queue and canonical auth/CSRF.
- Browser current/SSE/history/transcript and generic Agent/MCP regressions:
  owned batched provenance and no resolver leakage. Picker: explicit≤10,
  cross-page state, frozen retries, expiry and default-off overlap.
- Run generated-contract/frontend gates. Separately record real Redis Lua and
  native modal/mobile/source evidence; mocks/public canaries cannot prove those.

## 7. Wrong vs Correct

### Wrong

~~~python
part = current_map.parts[requested_page - 1]
meta.source_cid = expected_cid  # request is not extraction proof
item.bilibili_cid = part.cid    # rebinds historical content
item.url = meta.url             # writes before identity checks
~~~

### Correct

~~~python
part = before.resolve_part(pinned_cid)
meta, text = connector.fetch_part(pinned_bvid, pinned_cid, part.page)
# fetch_part requires the controlled extractor's actual subtitle CID.
if (meta.bilibili_bvid, meta.bilibili_cid) != (pinned_bvid, pinned_cid):
    raise SourceChangedError()
if after.resolve_part(pinned_cid).page != part.page:
    raise SourceChangedError()
# Re-lock owned dispatch/item and recheck pin before content writes.
~~~
