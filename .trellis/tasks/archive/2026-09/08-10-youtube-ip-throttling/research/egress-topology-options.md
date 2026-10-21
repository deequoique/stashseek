# YouTube egress topology options

> Decision status (2026-08-10): reopened as a bounded acquisition adapter after
> the user confirmed the same subtitle path works from a personal-computer
> egress. A proxy is still not accepted as the sole durable product contract;
> see `local-acquisition-and-datacenter-proxy.md` for the active comparison.

## Required traffic boundary

The complete Celery worker must not receive a global proxy. It performs more
than YouTube acquisition and may contact the embedding provider, PostgreSQL,
Redis, MinIO, and other services. The smallest safe boundary is the two child
processes owned by `YouTubeConnector`:

```text
Celery worker
  |-- yt-dlp metadata child ----------- controlled YouTube proxy
  |-- bounded subtitle child ---------- controlled YouTube proxy
  |-- embedding / DB / Redis / MinIO -- existing direct paths
```

The connector should read application-specific settings such as:

```text
YOUTUBE_PROXY_URL
YOUTUBE_PROXY_RESERVE_URL
YOUTUBE_REQUEST_MIN_INTERVAL_SECONDS
YOUTUBE_RATE_LIMIT_COOLDOWN_SECONDS
```

The names are a planning contract, not an implemented interface. Standard
worker-global `HTTP_PROXY`, `HTTPS_PROXY`, and `ALL_PROXY` settings are rejected
because their process-wide effect is broader than the incident.

For each YouTube child, application composition creates a copy of the existing
environment, preserves `SSL_CERT_FILE` and `REQUESTS_CA_BUNDLE`, adds the
selected proxy only to that child, and redacts the setting from all errors and
diagnostics. Tests use injected runners/openers and never require a live proxy.

## Option A: managed static proxy

```text
production worker
  -> child-scoped HTTPS/SOCKS proxy setting
  -> vendor's dedicated static egress IP
  -> YouTube / Googlevideo
```

Advantages:

- fastest to provision when a vendor account and suitable region are
  available;
- does not require running a second application host;
- a primary and cold-reserve endpoint can be obtained without deploying the
  entire Notebook Agent stack.

Controls required:

- dedicated or contractually non-shared static egress, not a public proxy or
  per-request rotating pool;
- one accountable vendor and explicit budget/expiry owner;
- proxy credential stored in the root-owned production secret file;
- destination and bandwidth policy where the vendor supports it;
- the vendor's data-handling, logging, region, and abuse-response terms
  reviewed before production user URLs pass through it;
- TLS remains end-to-end; no vendor certificate or HTTPS interception.

The main implementation difficulty is keeping proxy credentials out of
subprocess argv. A loopback sidecar can hold the upstream credential and expose
a credential-free local proxy to the two children; alternatively the
application can inject a secret child-only environment after proving the
locked yt-dlp and Python opener both honor it without logging it.

## Option B: self-owned private egress relay

```text
production host
  | YouTube child requests only
  v
private WireGuard tunnel
  v
small VPS in a different provider/ASN/region
  | destination-restricted CONNECT proxy
  v
YouTube / Googlevideo
```

The relay runs only WireGuard and a minimal forward proxy. It does not run the
Notebook Agent worker and receives none of the application secrets. The proxy
listens only on the WireGuard address, accepts only the production tunnel peer,
allows only the verified YouTube/Googlevideo destination suffixes, blocks all
other destinations and ports, and performs no TLS interception. The relay's
public firewall exposes only the private-tunnel handshake and normal SSH
administration from an allowlisted operator source.

The application sees a credential-free private proxy address because peer
authentication and authorization happen at the tunnel and firewall layers.
This avoids proxy credentials in yt-dlp argv or child environments.

Advantages:

- strongest ownership of the IP, logs, firewall, and lifecycle;
- narrow destination allowlist and no third-party proxy credential;
- inexpensive at transcript-only bandwidth and reusable as a cold standby.

Costs and risks:

- provisioning, hardening, patching, monitoring, and backup ownership;
- a new data-center IP or ASN may already be restricted by YouTube;
- WireGuard plus proxy configuration takes longer than entering a managed
  proxy endpoint;
- the relay must have an explicit teardown and key-rotation procedure.

## Failover behavior

The primary and reserve are not a round-robin pool. A 429 opens the
provider-wide circuit and stops same-path retries. After the cooldown, one
bounded public test-video probe may use the primary. The reserve is promoted
only after a classified primary egress failure and its own bounded validation;
normal user jobs never spray simultaneous attempts across both IPs.

Proxy-unreachable, proxy-authentication, 429, YouTube bot challenge, 403, and
subtitle-fetch failures remain distinct privacy-safe codes. Raw proxy URLs,
credentials, signed subtitle URLs, and yt-dlp stderr never enter Celery task
results, database failure reasons, health responses, or normal logs.

## Recommended selection

Because the user selected fastest recovery, a reputable managed dedicated
static proxy is the shortest emergency path if one can be purchased
immediately with acceptable data-handling terms. A self-owned private relay is
the preferred durable egress and is also a reasonable first choice when an
additional VPS can be provisioned within the same day.

In both cases, the repository change is nearly the same: implement the narrow
child-scoped proxy contract, pacing, classification, circuit breaker, bounded
probe, and rollback. Only the operator-owned endpoint behind the contract
differs.
