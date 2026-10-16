# Independent multipart implementation review — 2026-10-16

## Result

**Code/protocol dependency gate passes after two narrow fixes.** The favorites
preview child can reuse these foundation services, refs, admission and picker.
This is not a claim that native browser acceptance, the complete parent scope,
production migration or deployment is complete. The root retains those gates.

I read the check-agent protocol, child PRD/design/execution plan, all fourteen
check-context references (including the complete large retrieval contract),
parent planning review and source baseline record, and backend/frontend spec
indexes. Review covered discovery/ref/gate/service, API schemas/routes/runtime,
source model/migration, shared admission, controlled extraction/worker fences,
generic citation identity/time links, every browser source projection and the
part picker/direct-add integration plus their focused tests.

## Findings fixed

1. **Proven legacy retry compatibility:** Admission and schema intentionally
   support an old BV-keyed row with independently known CID. Worker `_part_pin`
   formerly rejected that row because its platform key was not CID-qualified;
   metadata comparison would also reject the extractor's proper CID-qualified
   evidence. Accept only the exact old BV or exact pinned CID key, then compare
   extraction metadata against the expected CID-qualified key. Keep the old
   stored platform/public/item identity unchanged. This does not populate an
   unknown pin or merge records. A retry regression proves unchanged old item
   identity, same immutable CID and current-page resolution.
2. **Truthful preview-cap error:** The picker's generic HTTP 422 branch shadowed
   `bilibili_limit_exceeded`, making a valid but oversized video look like a bad
   URL. Handle the safe limit code first and test its specific feedback.

Reviewer product/test edits are limited to `app/ingest/tasks.py`,
`tests/test_bilibili_multipart_extract.py`, `web/src/library/PartsPicker.tsx`
and `web/src/library/PartsPicker.test.tsx`.

## Source-integrity and boundary review

- Fixed public metadata host/paths/validated numeric parameters, credential-free
  verified TLS, disabled proxy/redirect handling, killable 15-second child and
  pre-parse 1 MiB body cap are preserved. Map shapes, duplicate CID/page bounds,
  explicit P input and local preview pagination fail safely.
- Signed refs use separate HKDF/HMAC purpose, canonical bounded descriptors,
  authenticated-tenant MAC context and a 30-minute expiry. Import has no upstream
  loop and reuses ordinary prepared-batch locks, all quotas, ordered partial
  outcomes, idempotency and bounded dispatch publication; ten remains ten.
- Redis Lua was reviewed for sliding tenant30/min and shared global-two token
  leases, expiring crash cleanup, token-specific release and fail-closed errors.
  It was initially unavailable for live execution; the later six real-server
  tests and runtime evidence below supersede that initial limitation.
- The controlled extractor retains real yt-dlp `_real_extract` and real caption
  lookup, records the actual subtitle-call BV/CID only after it returns, rejects
  conflicts/missing hook evidence (including no-caption), uses metadata-only
  processing and projects bounded inline SRT without provider URLs/logs/files.
  Worker actual-CID equality and pre/post map checks precede source/content
  writes; later row/dispatch locks preserve the pin and current ownership.
- Nullable source fields preserve unknown legacy provenance; PostgreSQL enforces
  complete bounded descriptors and known-CID uniqueness. The additive migration
  refuses provenance-destroying downgrade when known CID rows exist.
- Generic channels keep provider-direct P/local-time URLs. Browser detail,
  chapters, transcript, JSON answers, SSE and reopened history obtain owned
  public-item resolver links through segment provenance and batched tenant joins.
  Neither model resolver strings nor mutable ordinals authorize exact CID scope.
- Picker expansion selects nothing; count is per selected part, default-off
  overlap consent is explicit, uncertain replay retains frozen order/key/payload,
  and disposal aborts and suppresses stale results. Generated contracts and the
  read-only demo capability are preserved.

## Verification performed independently

- **111 passed:** multipart extractor/worker plus tasks, knowledge-service and
  Agent runtime regressions, using inert database/Redis settings.
- **27 passed:** PartPicker plus AddVideosDialog component tests after fixes.
- **14 passed:** real isolated PostgreSQL tests at the root-supplied loopback
  instance. Random schemas cover complete/invalid source constraints, known-CID
  concurrent dedupe, separate part identity/replay, default-false legacy consent,
  owned current-CID source opening, single migration head and additive
  upgrade/downgrade/upgrade. This focused miniature legacy-table roundtrip does
  not claim a full historical production migration chain was executed.
- Frontend typecheck and lint passed; `git diff --check` passed.

Sibling owners already recorded broader frontend/build/generated-contract and
focused backend gates. The root is running the full suite and owns aggregate
results, so I did not repeat the full suite or claim its pending results.

## Outstanding evidence and review limitations

- Native acceptance was initially blocked by the locked desktop. The root later
  obtained IAB access and is performing combined parent/favorites native QA,
  including the selection-toolbar layout fix. Its final evidence owns this gate.
- Real Redis execution is now verified by six owner-run tests on an isolated
  Redis 6.2.11 runtime. I independently reviewed the new fixture/test logic and
  evidence and matched local artifact/binary hashes to the recorded values;
  I did not unnecessarily repeat its 20-second natural-expiry scenario. See
  `redis-live-gate-success.md` and the addendum below.
- The baseline record names `/private/tmp/stashseek-before-favorites-parts-8f79hwna`,
  but that directory no longer exists, confirmed with the root. Review used
  ownership boundaries, current focused diffs and regression tests; it cannot
  claim a byte-for-byte comparison against that snapshot. Existing SSO/BV/chat
  WIP was preserved and no blanket staging, restore, commit or push was done.
- No production/shared migration, real video import, queue publication, account
  modification or deployment was performed by this review.

## Later real-Redis evidence review

The official-source download failures remain a historical record; a separately
owned temporary Maven-bundled Redis runtime subsequently enabled execution.
The success record accurately distinguishes the publisher-matched SHA1 from
locally recorded SHA256 and the binary's observed 6.2.11 version from its older
archive-member label. I independently recomputed the jar SHA1/SHA256 and binary
SHA256 and confirmed all match `redis-test-runtime.json`.

The new opt-in test accepts only an explicit binary path, not any Redis URL.
It spawns a random 127.0.0.1 listener, checks INFO PID against its own child
before configuration/test calls, verifies disabled persistence and always
terminates/reaps only that child. The six actual-Lua scenarios cover tenant
rate/isolation/window cleanup, 40-way rate atomicity, global-two concurrency,
stale-token/exception release, natural 20-second orphan expiry and closed
admission after server failure. No product gate change was required. The
recorded six passing tests satisfy the former real-Lua evidence gap for this
observed runtime; deployment topology/version qualification remains separate.
