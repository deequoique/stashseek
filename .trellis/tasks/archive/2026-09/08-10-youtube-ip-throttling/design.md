# Home-egress YouTube acquisition design

## Architecture and trust boundaries

```text
Mac terminal 1
  tinyproxy 127.0.0.1:18080
       |
       | outbound HTTPS CONNECT only
       v
  home ISP egress --> YouTube / Googlevideo
       ^
       |
Mac terminal 2
  ssh -R 127.0.0.1:18080:127.0.0.1:18080
       |
       | authenticated outbound SSH to port 22
       v
production 51.79.159.110
  127.0.0.1:18080 (SSH-owned reverse listener)
       ^
       |
  yt-dlp child + bounded subtitle child
       ^
       |
  Notebook Agent Celery worker

DB / Redis / MinIO / embedding / email / Web / MCP ---- existing direct routes
```

The port number is a planning default and must pass an availability preflight
on both machines before use. The reverse listener is loopback-only; the
production server's confirmed `GatewayPorts no` supplies a second fail-safe
against accidental public binding.

The SSH identity authenticates the tunnel. tinyproxy has no credential because
both ends are loopback-only and the only path between them is the authenticated
SSH connection. TLS remains end-to-end between the connector and YouTube;
tinyproxy transports CONNECT and does not terminate HTTPS.

## Application configuration

Add one optional setting:

```text
YOUTUBE_PROXY_URL=http://127.0.0.1:18080
```

Validation for this temporary MVP accepts only:

- scheme `http`;
- hostname exactly `127.0.0.1` or `localhost`;
- an explicit valid TCP port;
- no username, password, path, query, or fragment.

This prevents the short-term feature from silently becoming a generic
credential-bearing external proxy contract. An absent setting preserves the
existing direct behavior for local development and rollback. A present setting
is mandatory for every YouTube network operation; connection failure never
falls through to direct access.

## Connector composition

`_connector()` resolves the trusted CA first, then passes the validated proxy
setting into `YouTubeConnector`. The connector constructs a fresh child
environment from the worker environment for each YouTube subprocess:

1. copy the environment so the parent is never mutated;
2. preserve `SSL_CERT_FILE` and `REQUESTS_CA_BUNDLE` exactly;
3. set child-only HTTP/HTTPS proxy variables to the validated loopback URL;
4. set a bounded `NO_PROXY` containing only loopback names so an ambient
   YouTube bypass cannot defeat the configured route;
5. pass the same child environment to yt-dlp and bounded subtitle subprocess.

Tests inspect injected runner calls and a real bounded child to prove both
operations receive the same proxy and CA boundary. The connector never includes
the proxy value or raw child stderr in exception text.

The bounded-fetch child retains its HTTPS host allowlist, header allowlist,
size limit, timeout, streaming read, and TLS verification. Proxy configuration
changes the transport path only; it does not weaken destination or content
validation.

## Mac helper lifecycle

Add a foreground operator helper under `scripts/` rather than a persistent
macOS service. It performs:

1. validate the SSH target and selected ports;
2. require existing `ssh` and `tinyproxy` binaries;
3. create a private temporary directory with a generated tinyproxy
   configuration, PID file, and minimal log;
4. bind tinyproxy to Mac `127.0.0.1:18080`, allow only local input, CONNECT 443,
   and the supported destination filters;
5. start SSH with `ExitOnForwardFailure=yes`, bounded server-alive keepalives,
   and remote loopback forwarding to the Mac proxy;
6. keep both children in the foreground ownership tree;
7. on signal or child exit, stop the sibling, remove temporary state, and exit
   non-zero when startup/runtime failed.

The helper must not install packages, edit system proxy settings, create login
items, modify SSH configuration, or persist credentials. Homebrew installation
of tinyproxy is an explicit one-time operator action outside the helper.

## Failure and retry semantics

| Condition | Required behavior |
| --- | --- |
| tinyproxy missing | helper preflight fails before starting SSH |
| remote port occupied or forwarding rejected | SSH exits immediately; helper stops tinyproxy |
| Mac sleeps, changes network, or closes helper | tunnel disappears; configured child fails closed |
| proxy connection refused/timeout | stable internal `youtube_proxy_unavailable`; no direct retry path |
| provider returns 429 through home egress | retain a rate-limit classification; operator stops submissions and waits rather than changing IP per request |
| subtitle URL/body invalid or too large | retain existing bounded-fetch and ingest-limit behavior |
| worker restarted while tunnel is live | next job reconnects through server loopback without tunnel reconfiguration |

Existing Celery backoff remains for transient failures in this short-term MVP.
Operational guidance limits the canary and normal use to one submitted video at
a time. A Redis-backed provider-wide circuit breaker and multi-worker rate
limiter remain deferred because the selected scope is low-volume, on-demand
personal use.

## Deployment and rollback

Application rollout changes only connector/configuration code and focused
documentation/tests. Production activation then adds the loopback URL to the
existing root-owned environment file and restarts only
`notebook-agent-worker` after the Mac helper has passed preflight.

Validation first uses a public canary and records only coarse status and safe
counts. A user item is attempted only after metadata and subtitle body both
succeed through the tunnel.

Rollback removes/disables `YOUTUBE_PROXY_URL`, restarts only the owned worker,
and stops the Mac helper. No schema downgrade, data deletion, Caddy reload, or
dependency restart is part of rollback. The known direct production path may
still receive 429 after rollback; rollback restores behavior, not provider
availability.

## Trade-offs

- This is cheaper and more likely to work than another datacenter IP because it
  uses the already verified home egress.
- Availability depends on the Mac, its network, and a foreground terminal.
- The production server can send YouTube destination metadata through the
  user's home connection; TLS keeps request contents encrypted from tinyproxy,
  while normal DNS/SNI/IP metadata remains observable to the home ISP.
- The SSH account is broader than a purpose-built tunnel identity. That is
  accepted for the short-lived personal MVP; a restricted key and persistent
  agent would be required before any long-term deployment.
