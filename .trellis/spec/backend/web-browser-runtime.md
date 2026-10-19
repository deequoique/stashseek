# Web Browser Runtime Contract

## Ownership and composition

- `app/api/app.py` owns the only browser-facing FastAPI application, including
  authentication, library, conversation, and link routes.
- `app/api/runtime.py` is the production dependency-composition root.
  Compatibility modules such as `app/web_api.py` may delegate to this root,
  but must not define another cookie parser, session resolver, CSRF boundary,
  route set, or error envelope.
- The public prefix is fixed at `/api/v1`. Do not advertise a configurable
  prefix that the canonical routers cannot honor.

## Public privacy policy page

- `GET /privacy` (also `/privacy/`, and HEAD) and `GET /privacy/privacy.css` are
  public, login-free routes owned by `app/api/privacy.py`. `create_app`
  registers them before any SPA catch-all, so they work whether or not a
  `static_dir` is mounted.
- The page is bilingual static HTML in `app/static/privacy/` with one external
  stylesheet. No inline `<style>`, `style=`, `<script>` or event handler: the
  CSP is `script-src 'self'; style-src 'self'`.
- Only the contact email (`PRIVACY_CONTACT_EMAIL`), the recycle-bin days
  (`TRASH_RETENTION_DAYS`), the effective date and the service origin are
  substituted, each HTML-escaped. The origin is the request's scheme and Host
  (no hard-coded domain, so a domain transition needs no code change); it is
  display text only. Processors are named by category, never by provider.
- Any change to what the extension reads or the service stores must update the
  policy text and `PRIVACY_EFFECTIVE_DATE` together; `tests/test_privacy_page.py`
  pins the claims the code guarantees.

## Email authentication contract

- Production browser login is email-only and uses:
  `POST /api/v1/auth/challenges`, `POST /api/v1/auth/verify`, and
  `GET|DELETE /api/v1/auth/session`.
- Keep raw session and CSRF credentials only in `__Host-kb_session` and
  `__Host-kb_csrf`. Unsafe browser requests require exact Origin validation and
  double-submit `X-CSRF-Token` validation.
- Browser errors use the bounded `{code, message}` envelope. Challenge
  acceptance must not distinguish existing, unknown, or rate-limited email
  addresses. Session DTOs expose `authenticated`, `login_channel`, and
  `expires_at`, never tenant, user, identity, or session IDs.

## Scenario: Resolve opaque browser Sessions with bounded Redis caching

### 1. Scope / Trigger

Apply whenever changing browser Session resolution, CSRF, logout/revocation,
account disable/merge, Redis composition, or an authenticated route dependency.

### 2. Signatures

```python
resolve_session(raw_token: str) -> AuthenticatedWebSession
validate_csrf_for_session(session: AuthenticatedWebSession, raw_csrf: str) -> None
mark_web_session_cache_invalidation(db: Session) -> None
```

```dotenv
WEB_SESSION_CACHE_TTL_SECONDS=60  # 0 means PostgreSQL-only
```

### 3. Contracts

- PostgreSQL is authoritative. Cold resolution is one joined query over
  `WebSession`, `AppUser`, and `ChannelIdentity`, enforcing expiry, revocation,
  enabled state, ownership, and the exact `web/web` namespace.
- Redis stores a versioned projection under `generation + SHA-256(token)`.
  Never cache or log raw Session/CSRF credentials. TTL is bounded by both the
  configured TTL and Session lifetime.
- One Redis lookup atomically returns generation and projection. Reject
  unknown/extra fields, invalid types or namespace, naive/expired timestamps,
  malformed CSRF hashes, and payloads over 4 KiB.
- Miss/error falls back to PostgreSQL. A cold fill uses compare-generation
  semantics. After Redis outage or failed invalidation, bump generation before
  accepting future hits and discard possibly stale in-flight fills.
- Middleware stores the validated projection on `request.state`; CSRF and route
  dependencies reuse it. Throttle `last_used_at` instead of writing per request.
- Security mutations mark invalidation in their database transaction, but bump
  Redis only after the outermost commit. SAVEPOINT release is not authoritative.

### 4. Validation & Error Matrix

| Condition | Required behavior |
| --- | --- |
| Valid cache hit | Authenticate without database SELECT. |
| Miss/corrupt/timeout | Run joined authoritative query; never authenticate malformed data. |
| Invalid Session/user/identity | Return bounded `session_invalid`; do not fill Redis. |
| CSRF mismatch | Return bounded `csrf_invalid`; do not resolve twice. |
| Generation changes during cold query | Discard fill. |
| Redis invalidation fails | Database remains authoritative; bump before future hits. |
| Nested commit then outer rollback | Do not invalidate; clear marker on outer rollback. |

### 5. Good / Base / Bad Cases

- Good: one request resolves once, validates CSRF from that projection, and the
  route consumes the same projection.
- Base: TTL `0` uses the joined PostgreSQL resolver with identical security.
- Bad: use Redis as identity authority, cache raw tokens, repopulate an old
  generation after logout, or invalidate on SAVEPOINT release.

### 6. Tests Required

- Assert warm hit = one Redis lookup and zero database SELECTs; cold miss = one
  joined SELECT.
- Cover malformed/oversize payload, expiry, outage/recovery, generation race,
  startup bump, logout/revoke/disable/merge, nested transaction, and TTL `0`.
- Count one Session resolution for unsafe routes and email logout; keep CSRF
  failures fail-closed.

### 7. Wrong vs Correct

#### Wrong

```python
session = resolve_session(raw_cookie)
validate_csrf(raw_cookie, raw_csrf)  # resolves again
cache.set(raw_cookie, session)
```

#### Correct

```python
session = resolve_session(raw_cookie)
request.state.authenticated_session = session
validate_csrf_for_session(session, raw_csrf)
cache.put(token_digest, session, generation=observed_generation)
```

## Tenant and transport boundaries

- Authenticated conversation adapters preserve the resolved tenant's complete
  channel namespace: `channel`, `account_id`, and `external_user_id`. Never
  rebuild a legacy Telegram/WeChat identity as `web/web/<id>` and let
  `ChannelService` self-register a different tenant.
- The combined ASGI dispatcher selects MCP by `MCP_PATH` before dispatching to
  the browser application. Browser cookies never authenticate MCP, and MCP
  Bearer credentials never authenticate browser routes.
- Browser-companion capture is a third, isolated transport credential. Exact
  extension-origin routes use a hash-at-rest `capture:write` Bearer; Web
  cookies approve/list/revoke devices but never authenticate capture, and the
  capture Bearer never authenticates normal Web or MCP routes. See
  `browser-companion-capture.md`.
- Production composition must forward the configured channel service into the
  canonical browser app; retained conversation routes must not become a
  permanent 503 surface through omitted wiring.
- CSRF is enforced in the app middleware, not in route signatures.
  - Conversation and library mutations declare `X-CSRF-Token` as an
    **optional** route header.
  - Cookie sessions on unsafe methods still get `403 csrf_invalid` when the
    header is missing or wrong.
  - An extension session Bearer, which a `chrome-extension://` page holds and
    which cannot read the CSRF cookie, needs no header.
  - A required route-level header turns every Bearer mutation into a 422.

## Conversation streaming contract

- The browser SSE route delegates one `ChannelService` execution. Identity
  resolution, message idempotency, and durable turn persistence remain owned by
  that service; the route must not start a second Agent execution or save a
  second response for a compatibility fallback.
- A grounded section is public only after its current-run Citation metadata is
  authorized, then follows `section_started -> text_delta* ->
  section_completed`. Technical interruption may replace completion with
  `section_aborted`; a client disconnect, cancellation, timeout, or incomplete
  section never persists a partial conversation turn.
- Empty or unsupported provider streaming before any section is public falls
  back to one whole-answer `text_delta` plus the final response. If a section
  has already been exposed, the failure is terminal and must not silently
  replay the answer.
- Event fields that do not apply to a lifecycle event are omitted from the JSON
  SSE record when null. Generated TypeScript therefore treats nullable
  event-specific fields such as `section_id`, `status`, and `reason` as
  optional; runtime lifecycle validation remains mandatory.

- Page Q&A threads have an `external_conversation_id` that starts with `page-`.
  - They are read-only on the ordinary conversation routes: `messages`,
    `messages/stream` and `reset` return `409 page_thread_read_only`.
  - Listing, reading turns and deleting still work, and list/turn responses
    carry `page_qa: true`.

## Scenario: Instant page Q&A over a caller-supplied transcript

### 1. Scope / Trigger

- Trigger: the extension side panel asks about the video in the active tab
  without saving it.
- This is a new route and a new cross-layer request/stream contract, covering
  the extension, the API, persistence and the model provider. Code lives in
  `app/page_qa/`.

### 2. Signatures

```text
POST /api/v1/page-qa/conversations/{conversation_id}/messages/stream   (text/event-stream)
conversation_id ~ ^page-[0-9a-f]{32}$          # client-generated, one per video per panel session
PageQaService.stream(*, tenant, conversation_id, payload, request_id) -> AsyncIterator[PageQaTextDelta | PageQaCompleted | PageQaFailed]
```

### 3. Contracts

**Request** (`PageQaMessageInput`, `extra="forbid"`):

```text
{ message_id: 1..128 chars,
  question: non-blank,
  playback_sec: float >= 0 | null,
  page: { platform: "youtube" | "ntu_kaltura" | "bilibili", platform_id, canonical_url, page_url,
          metadata: CaptureMetadata, caption: CaptureCaption } }
```

- The page uses the same reference validation as capture.v1, with no
  `content_hash`. A Bilibili `page_url` may carry `p` above 1; citations for
  that page open the same part (`?p=N&t=<sec>`).

**Stream**

- Only the existing public `ConversationStreamEvent` types are used:
  `started`, `text_delta*`, then either `completed` or `error`.
- `completed.response` carries:
  - `text`: the answer plus the standard `来源：` block, with `[S<n>]` markers;
  - `citations[]`: `{title, excerpt ≤180, url, start_sec}`;
  - `status`: `ok`, or `not_found` when no valid citation exists;
  - `thread_id`.

**Model**

- The model writes `[P<n>]` paragraph markers.
- `PageAnswerGuard` buffers partial markers, drops unknown ones, and maps known
  ones to `[S<n>]`. At most 8 citations are kept.
- The final text passes `validate_natural_answer`.
- One model call is made with no tools: `request_limit=1`,
  `output_tokens_limit=AGENT_OUTPUT_TOKEN_LIMIT`, with a timeout of
  `AGENT_TIMEOUT_SECONDS`.

**Context**

- Cues merge into paragraphs of about 30 s (a gap over 4 s also closes one).
- If the estimate is ≤ `PAGE_QA_FULL_CONTEXT_TOKENS`, every paragraph goes in.
- Otherwise the context contains:
  - the paragraphs within ±300 s of `playback_sec`;
  - the in-process BM25 top 12;
  - the chapter outline;

  all capped at `PAGE_QA_WINDOW_CONTEXT_TOKENS`.
- A cross-script question gets one keyword-rewrite call first.
- Caption-less input is allowed only for YouTube and Bilibili with a title,
  description or chapters. The answer starts with the fixed no-captions label.

**Persistence** (only after a validated answer)

- One turn through `get_or_create_thread` / `save_completed_turn`:
  - `user_text` = the question;
  - `sources` = the citations, each excerpt ≤180 chars;
  - `answer_status` ∈ `page_grounded | page_no_captions | page_unavailable`.
- Failure, timeout, disconnect or cancel leaves no thread and no turn.
- A repeated `message_id` replays the saved turn.
- Never stored: cues, full paragraphs, prompts, or `playback_sec`.

**Auth**

- Allowed: an extension session Bearer, or a Web cookie (middleware CSRF).
- The `capture:write` Bearer gets 401.

**Environment keys**

| Key | Default | Rule |
| --- | --- | --- |
| `PAGE_QA_ENABLED` | true | when false the route is unmounted and capabilities omit `page_qa` |
| `PAGE_QA_MAX_REQUEST_BYTES` | 2,000,000 | must be > 0 and ≤ `BROWSER_COMPANION_MAX_REQUEST_BYTES`; the route's exact path is exempt from the 64 KiB default |
| `PAGE_QA_FULL_CONTEXT_TOKENS` | 24,000 | must be > 0 |
| `PAGE_QA_WINDOW_CONTEXT_TOKENS` | 12,000 | must be > 0 and ≤ `PAGE_QA_FULL_CONTEXT_TOKENS` |
| `PAGE_QA_RATE_LIMIT` | 30 | must be > 0; questions per user per 10 min, an in-process sliding window per worker |

### 4. Validation & Error Matrix

| Condition | Behavior |
| --- | --- |
| body over `PAGE_QA_MAX_REQUEST_BYTES` | 413 before parsing |
| unknown field, bad id, bad platform/reference, caption-less Kaltura, caption-less YouTube/Bilibili without metadata | 422 |
| capture Bearer or no session | 401 |
| `chrome-extension://` Origin not allowed; cookie request with a missing or foreign Origin | 403 |
| over the rate limit | 429 `rate_limited` with `Retry-After` |
| flag off | 404 |
| provider failure, usage limit, or invalid answer | SSE `error` with `answer_unavailable`; nothing persisted |
| timeout | SSE `error` with `timeout`; nothing persisted |
| any other exception | SSE `error` with `request_failed`; the log records the exception class only |

### 5. Good / Base / Bad Cases

- **Good:** a 19-minute captioned video, a Chinese question about English
  content. The answer is in Chinese with `[S<n>]` citations, and the first
  delta arrives in about 2 s.
- **Base:** a 5-hour transcript (about 66K estimated tokens) runs in window
  mode. Asking "刚才…" cites paragraphs within a few minutes of `playback_sec`.
- **Bad:**
  - routing page Q&A through `ChannelService` (it forces a library search and
    persists `model_messages`);
  - storing or logging cues;
  - letting the library chat continue a `page-` thread.

### 6. Tests Required

- Transcript and BM25 units: paragraph boundaries, CJK and Latin tokens, the
  window around playback, the cap, and determinism.
- Guard: a marker split across chunks, unknown markers, more than 8 markers,
  and an answer that fails validation.
- API contract: 413, 422, 401 (capture Bearer), 403 (Origin), 429, 404 (flag
  off), cookie with CSRF, extension Bearer, and the SSE sequence.
- Sentinel tests:
  - A sentinel in the cues appears in no log record (including the
    provider-error and rewrite-failure paths).
  - It appears in no persisted column except a ≤180-char cited excerpt.
  - No `content_item`, `segment`, object-store or broker write happens.
- Read-only `page-` threads on the conversation routes return 409. Listing,
  reading and deleting them still work.

### 7. Wrong vs Correct

#### Wrong

```python
# Packs the transcript into the ordinary chat path: library search is forced,
# the 16K message cap truncates it, and model_messages persist it.
await channel.handle_stream(envelope_with(text=question + "\n" + transcript))
```

#### Correct

```python
async for event in page_service.stream(tenant=identity.tenant, conversation_id=cid,
                                       payload=validated, request_id=request_id):
    ...  # deltas/terminal mapped to public ConversationStreamEvent; turn saved only after validation
```

## OpenAPI and verification

- `scripts/export_web_openapi.py` constructs the real email-enabled production
  route composition with inert dependencies and no provider/network side
  effects. Generated JSON and TypeScript are checked in together.
- Regression tests must cover the production route set, challenge/verification
  safe-error matrix, authenticated library access, CSRF logout, legacy tenant
  affinity through a real `ChannelService`, and combined Web/MCP credential
  isolation.
- Release validation includes the focused credential-free backend workflow,
  the full Python suite with loopback HTTP available, all frontend gates, one
  Alembic head, and a 390×844 real-browser login/logout smoke with empty Web
  Storage and clean page-error/console buffers.
