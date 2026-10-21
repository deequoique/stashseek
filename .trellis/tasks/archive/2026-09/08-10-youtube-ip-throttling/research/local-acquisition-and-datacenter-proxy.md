# Local acquisition node and dedicated datacenter proxy

Reviewed on 2026-08-10 after the user reported that the same subtitle workflow
succeeds from their personal computer while the production host receives HTTP
429, and supplied a screenshot of a dedicated datacenter proxy order.

## What the new evidence proves

The successful personal-computer path proves that the video and caption are
not universally unavailable and that a different egress/request context can
currently acquire them. It is consistent with YouTube treating the production
data-center IP, ASN, request volume, or request profile differently.

It does not prove that every alternate IP works, that the home IP will always
work, or that a second data-center ASN will be treated like a residential ISP.
The observation is nevertheless strong evidence that acquisition can be moved
behind a replaceable source boundary instead of treating the production
worker's egress as fixed.

## Screenshot classification

The screenshot shows:

- a **Datacenter Dedicated** product, not a residential or static-ISP product;
- one dedicated Singapore proxy for 30 days;
- a displayed base rate of USD 1.80 per proxy and order total of USD 2.43;
- “unlimited usage” subject to a Fair Use Policy.

“Dedicated” means the endpoint is not intentionally shared with other
customers. It does not mean the IP belongs to a residential/consumer ISP, has
a clean YouTube history, or is exempt from ASN-level enforcement. The low
price makes it suitable for a bounded canary, not evidence of a durable SLA.
The screenshot does not identify the provider, ASN, proxy protocol, YouTube
allowance, IP replacement terms, logging policy, or fair-use thresholds.

The second screenshot identifies an IPRoyal residential-pool offer with:

- 64M+ residential endpoints across 195+ locations;
- rotating and sticky session modes;
- a 2 GB starter tier at USD 5.31/GB, or USD 10.62 before any tax/fee not shown;
- a pay-as-you-go selector whose price is not visible in the screenshot;
- non-expiring purchased traffic according to the displayed feature list.

This is a metered residential pool, not proof of one permanently assigned
residential IP. Residential/ISP ASNs are generally closer to the already
working personal-computer path and may have a higher initial YouTube success
rate than a datacenter endpoint. However, endpoint quality varies across the
pool, and session continuity depends on the provider's sticky-session contract.

For this connector, sticky mode is mandatory within one acquisition: yt-dlp
first resolves metadata and a signed subtitle URL, then a separate child
downloads the subtitle body. Rotating the IP between those steps can invalidate
the signed request or create a different enforcement result. Rotation should
never happen per HTTP request or per retry attempt.

Because the product fetches metadata and caption text rather than video media,
2 GB is plausibly enough for a low-volume pilot, but the actual billed bytes per
item must be measured. The screenshot alone does not establish how connection
overhead, failed responses, or retransfers are metered.

## Option comparison

| Acquisition source | Similarity to the currently working path | Availability | Main risk | Planning position |
| --- | --- | --- | --- | --- |
| One dedicated datacenter proxy | Low to medium: changes IP/ASN but remains data-center egress | Vendor endpoint can be always on | New IP or entire ASN may already be/soon become limited; vendor sees destination metadata | Cheap, reversible canary or emergency adapter |
| Static ISP/residential proxy | Higher | Vendor endpoint can be always on | Higher cost; vendor provenance, policy, privacy, and replacement quality | Better commercial fit than datacenter proxy if terms are acceptable |
| Personal computer as a private acquisition worker | Highest: uses the already successful egress and runtime | Depends on device, home ISP, power, and network | Device uptime, secure updates, revocation, and queue ownership | Recommended personal-use primary if an always-on device is available |
| Official metadata plus user-supplied transcript | Does not reproduce the automatic caption path | Server-side path is highly operable | Extra user action for arbitrary public captions | Durable fallback and metadata foundation |

Per-request rotating proxy pools are not a recommended option. They make
behavior hard to attribute, can mix user jobs across unknown egress owners,
and can turn one save into request spray instead of fixing pacing.

## Two materially different meanings of “data-source proxy”

### Narrow network proxy

```text
production Celery worker
  |-- yt-dlp metadata/caption resolution --+
  |-- bounded subtitle body download -------+--> one static proxy --> YouTube
  |-- DB / Redis / MinIO / embedding ------------> existing direct paths
```

This is compatible with the current connector shape, but both YouTube child
processes must use the same selected egress. `app/connectors/youtube.py:80-106`
does not currently pass a proxy to yt-dlp, and
`app/connectors/youtube.py:108-131` launches a separate bounded fetch whose
`urllib` path is defined in `app/connectors/bounded_fetch.py:47-81`. Adding only
yt-dlp `--proxy` would leave the second request on the throttled production
egress.

The complete worker must not receive global `HTTP_PROXY`, `HTTPS_PROXY`, or
`ALL_PROXY` settings because it also contacts embedding, PostgreSQL, Redis,
MinIO, email, Web, and MCP dependencies. The credential should be held by a
loopback sidecar or a YouTube-child-only secret environment, never in logs,
task payloads, health output, or user-facing errors.

### Private acquisition worker

```text
production server -- durable job with video ID --> broker/API
home acquisition agent -- outbound authenticated pull --> job
home acquisition agent -- yt-dlp + bounded caption --> normalized bounded result
production server -- validate/chunk/store/embed --> tenant-owned ready item
```

This is not an open forward proxy. The home device opens an outbound-only,
mutually authenticated connection, receives only bounded acquisition jobs, and
returns metadata plus normalized caption bytes. It needs no inbound public port
and receives no database, MinIO, embedding, email, or application-wide secret.
The server repeats tenant ownership and content-limit checks before publishing
the result.

For public captions, account cookies should remain disabled. A private or
account-required flow would be a separate explicit feature because using a
personal YouTube session adds account-ban, rotation, and credential-exposure
risk.

## Canary contract for the screenshot product

Before treating one cheap datacenter endpoint as usable, a bounded trial must
verify all of the following:

1. HTTPS CONNECT or SOCKS5 works without TLS interception.
2. The provider contract permits YouTube and transcript-only automation, and
   states fair-use, logging, IP replacement, refund, and abuse-response terms.
3. One public canary obtains both metadata and the final subtitle body through
   the same IP without account cookies.
4. Requests are paced at least 5–10 seconds apart and a 429 opens a global
   cooldown instead of retrying each job through the same endpoint.
5. A privacy-safe soak, not one success, demonstrates stability across the
   expected daily job volume and several days.
6. Proxy-authentication, proxy-unreachable, 429, bot challenge, 403, and
   subtitle-fetch failures remain separately observable.
7. The endpoint can be disabled without affecting official metadata, database,
   embedding, or user-supplied transcript paths.

Buying several endpoints or enabling rotation before this single-endpoint
canary would add cost and obscure the result.

## Short-term personal-use ladder

When long-term multi-user availability is not required, the smallest practical
sequence is:

1. **Known-working home egress, USD 0 proxy spend.** Keep the personal computer
   online only while processing videos. Connect it through an outbound-only
   private tunnel or acquisition agent; do not expose an unauthenticated home
   proxy to the Internet.
2. **One dedicated datacenter canary, screenshot total USD 2.43/30 days.** This
   is the cheapest unattended experiment. If it immediately receives 429,
   bot challenge, or 403, stop rather than buying a rotating block of similar
   IPs.
3. **Residential sticky pilot, screenshot starter USD 10.62 for 2 GB.** Use one
   sticky session for the complete metadata-plus-subtitle job and pace jobs.
   It is the higher-probability commercial fallback, not a permanent identity.
4. **Manual transcript upload/paste.** Keep this no-egress fallback available
   so a single important item never requires another infrastructure purchase.

For a short-lived personal deployment, this avoids OAuth, browser-extension,
multi-provider failover, and a full home-agent control plane. The minimal
repository change can expose one application-specific YouTube proxy boundary
that routes both child processes, plus pacing, safe failure classification, and
a kill switch. The endpoint behind it may be the home tunnel, one datacenter
canary, or one residential sticky session.

## Recommended durable shape

Keep a provider-neutral acquisition interface so no single egress mechanism is
the product contract:

1. official YouTube Data API for public metadata;
2. one controlled automatic-caption adapter, preferably an always-on private
   home acquisition worker for a personal deployment;
3. upload/paste as the deterministic fallback;
4. an optional static commercial proxy adapter for emergency availability,
   with the dedicated datacenter product accepted only after its canary passes.

This preserves the convenient automatic path while making proxy replacement or
removal an operational choice rather than a rewrite of the ingestion model.

## Selected short-term path

The user selected their Mac as the acquisition egress. The correct production
server is `ubuntu@51.79.159.110` (`vps-d2a069a1`), not the earlier unrelated
host inspected while resolving server identity. The production SSH daemon
allows TCP forwarding and has `GatewayPorts no`; the selected topology is a
Mac-loopback tinyproxy forwarded to server loopback by an outbound reverse SSH
session.

Tailscale is not used. The MVP is manual and foreground-only: it works while
the Mac proxy and SSH process are alive, fails closed when absent, and does not
attempt to provide long-term unattended availability.
