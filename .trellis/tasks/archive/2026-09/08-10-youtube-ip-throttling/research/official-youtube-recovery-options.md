# Official YouTube recovery guidance and option comparison

> Decision status (2026-08-10): superseded for the durable target. The user
> chose a long-term design that does not depend on proxies or server-side
> consumer-Web scraping. This file remains incident-response research only;
> see `official-api-and-user-supplied-text.md` for the active direction.

## Scope and sources

Reviewed on 2026-08-10 against the repository's locked yt-dlp `2026.7.4`.

Primary sources:

- [yt-dlp README network/authentication/workaround options](https://github.com/yt-dlp/yt-dlp#usage-and-options)
- [yt-dlp YouTube extractor guidance](https://github.com/yt-dlp/yt-dlp/wiki/Extractors#youtube)
- [yt-dlp PO Token Guide](https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide)
- [YouTube Terms of Service](https://www.youtube.com/static?template=terms)

The yt-dlp project is the primary source for its supported runtime behavior;
it is not YouTube and cannot grant permission to automate YouTube. The policy
notes below are planning constraints, not legal advice.

## Confirmed upstream behavior

### Proxy and pacing controls

yt-dlp supports HTTP, HTTPS, and SOCKS proxies through `--proxy`, explicit
source-address binding, request sleeps, download sleeps, and retry-specific
sleeps. Its YouTube extractor guidance recommends a delay of around 5–10
seconds between downloads after guest-session or account request limits.

The upstream guide estimates the default guest-session rate limit at roughly
300 videos/hour and the account rate at roughly 2,000 videos/hour. These are
upstream estimates, not an SLA and not a safe target for this product.

### Account cookies

yt-dlp warns that using an account can cause it to be banned temporarily or
permanently. It says account cookies are only necessary for content that
requires an account, including private playlists, age-restricted videos, and
members-only content. YouTube frequently rotates cookies used by open browser
tabs, so a server credential requires a deliberate export, isolation,
rotation, revocation, and expiry process. OAuth no longer works for yt-dlp.

This makes cookies a high-risk fallback, not evidence-based treatment for a
public-video metadata 429 on the current IP.

### PO Tokens and the current player client

The upstream guide says a PO Token can be required for Google Video Server,
Player, or subtitle requests depending on the selected client. It currently
lists `android_vr`, the application's fixed client, as not requiring a PO
Token. PO Tokens are externally generated, may be bound to video/session, and
may require per-video generation and periodic refresh.

The production probe failed during metadata acquisition with 429. That is
different from a client-specific 403 or missing format/subtitle response, so a
PO Token provider is not the first emergency action. It remains a later option
if a controlled matrix observes a token-specific failure after egress recovery.

### YouTube policy boundary

YouTube's published Terms prohibit automated access such as scrapers without
prior written permission and prohibit downloading or using content unless the
Service expressly authorizes it or YouTube and applicable rights holders give
permission. Controlled egress, cookies, or PO Tokens do not remove this policy
constraint. The product/operator must own that risk decision independently of
technical success.

## Application-specific routing constraint

The application uses two outbound processes:

1. yt-dlp fetches metadata and returns a signed subtitle URL plus headers.
2. `app.connectors.bounded_fetch` separately downloads that subtitle URL with
   Python `urllib` under host, size, timeout, and header limits.

Adding only yt-dlp `--proxy` can restore metadata while leaving subtitle fetch
on the throttled production egress. Setting a worker-global proxy is also too
broad because it may affect unrelated embedding or other provider traffic.
The narrow recovery contract must inject one YouTube-only proxy environment
into both child processes while preserving the verified CA environment and
keeping credentials out of argv and logs.

## Recovery option comparison

| Option | Recovery speed | Fit for observed public-video 429 | Main risks | Planning position |
| --- | --- | --- | --- | --- |
| Dedicated static controlled egress for both YouTube child processes | Fast | High: directly changes the failing request path | New IP/ASN may also be limited; proxy operator sees target hosts; credential and cost ownership | Recommended primary emergency path |
| Self-owned secondary VPS as a narrow outbound relay | Fast to medium | High | Provisioning and relay hardening take longer; another data-center ASN may still be limited | Preferred when operational control matters more than the last few hours of recovery time |
| Dedicated YouTube account cookies | Fast after export | Low to medium for this incident: does not itself remove current IP 429 | Explicit temporary/permanent ban risk, cookie rotation, account identity and secret exposure | Fallback only; use no personal/shared account |
| Controlled egress plus account cookies from the first probe | Fastest broad-coverage experiment | Medium | Combines IP, account, client, and cookie changes, so root cause is obscured; highest credential/ban risk | Not recommended as the first probe |
| `mweb` plus automatic PO Token provider | Medium | Low for current metadata 429; relevant to later client-specific GVS/Player/subtitle enforcement | Extra plugin/runtime, per-video tokens, changing upstream behavior | Deferred until a token-specific failure is observed |
| Managed transcript/acquisition provider | Medium | Potentially high | Vendor data handling, cost, supported-video gaps, provider policy/SLA | Secondary fallback if direct acquisition remains unstable |
| Client-side/user-assisted capture | Slow | High because it moves acquisition away from server egress | Larger product/UX change, client trust and support burden | Durable fallback, not fastest recovery |

## Recommended emergency sequence

1. Stop same-egress retry amplification with a provider-wide circuit breaker.
2. Provision one accountable static egress plus at most one cold reserve; do
   not use public or per-request rotating proxies.
3. Route only yt-dlp metadata and bounded subtitle fetch through that egress,
   add 5–10 second request pacing, and retain direct embedding/provider traffic.
4. Run a bounded matrix: one public test video for metadata, then subtitle;
   only after success, test a small privacy-safe production batch.
5. Keep the dedicated account disabled for the first egress-only validation.
   Enable an isolated throwaway/dedicated account only if the target content
   genuinely requires login or the matrix shows a separate authenticated path
   is necessary.
6. Treat PO Token work as a separate response to a verified client-specific
   Player/GVS/subtitle enforcement failure, not as a cure for the current 429.

This sequence is slightly slower than changing egress, account, and client at
once, but it is still an emergency path and preserves enough causal evidence
to know which dependency restored service.
