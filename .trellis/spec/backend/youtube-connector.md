# YouTube Connector Contract

## Scenario: Original-language subtitle selection and yt-dlp runtime

### 1. Scope / Trigger

Trigger: the YouTube ingestion connector consumes externally shaped yt-dlp
metadata, depends on yt-dlp's optional impersonation runtime, and launches a
bounded subtitle subprocess. A translated `zh-Hans` track was selected for an
English video because selection preferred Chinese before the video's original
language. Worker-owned YouTube requests also failed when trusted CA
initialization happened only at the later embedding stage.

### 2. Signatures

```python
def _select_track(self, data: dict) -> tuple[str, str] | None: ...
def fetch_text(self, platform_id: str) -> TextResult | NeedsASR: ...
def _connector(url: str) -> YouTubeConnector: ...
```

Dependency contract in `pyproject.toml`:

```toml
"yt-dlp[default,curl-cffi]>=2026.7,<2026.9"
```

Environment contract:

```text
TLS_CA_BUNDLE          optional explicit readable PEM bundle
SSL_CERT_FILE          inherited by yt-dlp and subtitle children
REQUESTS_CA_BUNDLE     inherited by requests/provider clients and children
```

### 3. Contracts

- Input metadata uses `language`, `subtitles`, and `automatic_captions` from yt-dlp.
- `_select_track()` returns `(source, exact_track_key)` where `source` is `official_cc` or `auto_caption`; `None` means no caption tracks are available.
- Normalize language comparison with lowercase and `_` → `-`; retain the original track key when invoking yt-dlp.
- Rank candidates by original-language match, then source (`official_cc` before `auto_caption`), then matching precision, then metadata order.
- If the original language is unavailable or missing, fall back deterministically: `*-orig`, English, Chinese, then any track.
- `fetch_text()` maps no selection to `NeedsASR`, keeps empty-response validation, and reports the selected track's base language in `TextResult.lang`.
- The ingestion worker must call
  `configure_trusted_ca(settings.tls_ca_bundle)` before constructing the real
  `YouTubeConnector`. This exports the verified bundle to `SSL_CERT_FILE` and
  `REQUESTS_CA_BUNDLE` before any metadata or subtitle request.
- yt-dlp and the bounded subtitle process inherit the worker environment; do
  not pass a replacement `env` unless it preserves both standard CA variables.
  Certificate and hostname verification remain enabled. The later embedding
  composition still receives its explicit verified `SSLContext` independently.
- The local runtime must install the declared yt-dlp extras. `curl_cffi` must import and `python -m yt_dlp --list-impersonate-targets` must list a target before treating an impersonation warning as a YouTube issue.

### 4. Validation & Error Matrix

| Condition | Required outcome |
| --- | --- |
| Original-language track exists | Select it before any translated track. |
| Official and automatic original tracks both exist | Select the official track. |
| Only an automatic original track and an official translation exist | Select the automatic original track. |
| Metadata language missing | Prefer an explicit `*-orig` track. |
| No subtitle maps contain tracks | Return `NeedsASR`. |
| Explicit worker CA is missing or unreadable | Raise `TLSConfigurationError` before connector construction or any child process. |
| yt-dlp reports 429 | Raise `TransientFetchError`; do not mark ready. |
| json3 body is empty or has no text cues | Existing transcript guard fails ingestion. |

### 5. Good / Base / Bad Cases

- Good: `language='en-US'`, automatic tracks `zh-Hans` and `en-orig` → `('auto_caption', 'en-orig')`.
- Good: the worker resolves CA before connector construction and a real ingest
  reaches `ready` without a temporary `SSL_CERT_FILE` or
  `REQUESTS_CA_BUNDLE` workaround.
- Base: `language='en'`, official `en` and automatic `en-orig` → `('official_cc', 'en')`.
- Bad: choose `zh-Hans` just because it appears first or because it is official
  when an English original track exists; or initialize CA only when building
  the embedder after the YouTube fetch has already run.

### 6. Tests Required

- Unit-test each ranking case above through `YouTubeConnector._select_track()`.
- Test `fetch_text()` returns `NeedsASR` when both maps are empty.
- Test `_connector()` resolves CA before construction and fails closed for an
  invalid explicit bundle.
- Test the bounded subtitle child observes the resolved `SSL_CERT_FILE` and
  `REQUESTS_CA_BUNDLE` through real process inheritance.
- Keep tests for json3 parsing, URL matching, 429 classification, and the verified YouTube player client.
- After a dependency update, run the full pytest suite and a real ingest. Database acceptance must assert: ready state, null failure reason, non-empty raw object key/content hash, at least one segment, valid timings, and `vector_dims(embedding) = 1536` for all segments.

### 7. Wrong vs Correct

#### Wrong

```python
lang = next((key for key in languages if key.startswith("zh")), None)
lang = lang or next((key for key in languages if key.startswith("en")), None)
```

This ignores the video's original language and can request an unnecessary translated track.

#### Correct

```python
# Rank original-language candidates first; only then prefer official captions.
source, lang, _ = min(candidates, key=lambda candidate: candidate[2])
```

The ranking key encodes language group, source priority, match precision, and stable source order.

For worker composition, this is also wrong:

```python
connector = YouTubeConnector(...)
# Too late: metadata/subtitle HTTPS may already have failed.
trusted = configure_trusted_ca(settings.tls_ca_bundle)
```

The correct ordering is:

```python
configure_trusted_ca(settings.tls_ca_bundle)
connector = YouTubeConnector(...)
connector.match(url)
```

This ordering configures both the current process and all subsequently spawned
YouTube children without weakening TLS verification.

## Scenario: On-demand loopback home egress

### 1. Scope / Trigger

Trigger: YouTube throttles the production server IP while public metadata and
subtitle acquisition still works from an operator's Mac. For short-term
personal use, only the two YouTube connector subprocesses may use an on-demand
home egress. The rest of the worker and every other service must retain its
existing direct route.

### 2. Signatures

Application environment:

```text
YOUTUBE_PROXY_URL=http://127.0.0.1:18080  # optional
```

Connector construction:

```python
YouTubeConnector(
    max_transcript_bytes=settings.ingest_max_raw_transcript_bytes,
    fetch_timeout_seconds=settings.youtube_fetch_timeout_seconds,
    proxy_url=settings.youtube_proxy_url,
)
```

Foreground Mac command:

```sh
./scripts/youtube-home-egress SSH_TARGET LOCAL_PROXY_PORT REMOTE_PROXY_PORT
```

The production invocation is:

```sh
./scripts/youtube-home-egress ubuntu@51.79.159.110 18080 18080
```

### 3. Contracts

- `YOUTUBE_PROXY_URL` is absent for direct/rollback behavior. When present it
  must use scheme `http`, host `127.0.0.1` or `localhost`, and an explicit port
  from 1 through 65535. Credentials, path (including `/`), query, fragment,
  whitespace, external hosts, and IPv6 are rejected during `Settings`
  construction.
- `_connector()` configures the trusted CA before constructing
  `YouTubeConnector`, then passes the validated proxy value.
- A proxied connector copies the current process environment without mutating
  `os.environ`, preserves `SSL_CERT_FILE` and `REQUESTS_CA_BUNDLE`, sets both
  uppercase and lowercase HTTP/HTTPS proxy variables, removes ambient
  `ALL_PROXY` variants, and replaces `NO_PROXY` variants with loopback-only
  values.
- The yt-dlp metadata child and bounded subtitle child receive separate copies
  of the same environment values. Neither child may be retried without that
  environment when the configured proxy is unavailable.
- Raw yt-dlp stderr, proxy values, signed subtitle URLs, response bodies, and
  user content never appear in connector exception text. Stable internal
  classifications are `youtube_rate_limited`, `youtube_proxy_unavailable`,
  `youtube_proxy_timeout`, `youtube_proxy_authentication_failed`,
  `youtube_fetch_failed`, and `subtitle_fetch_failed`.
- The bounded subtitle child retains its HTTPS destination allowlist, safe
  header allowlist, streaming size limit, timeouts, and verified TLS. Exit
  codes 10 through 15 distinguish size, generic fetch, rate limit, proxy
  unavailable, proxy timeout, and proxy authentication without returning raw
  error text.
- The Mac helper owns tinyproxy and reverse SSH in one foreground lifecycle.
  It preflights both loopback ports before either long-running child, binds
  tinyproxy only to `127.0.0.1`, allows only HTTPS CONNECT 443, uses
  `FilterType ere` plus `FilterDefaultDeny Yes` for YouTube/Googlevideo hosts,
  and binds the SSH reverse listener to server `127.0.0.1`.
- The helper never installs tinyproxy, changes macOS system proxy settings,
  creates a LaunchAgent/service, opens a router/public port, or persists
  configuration. Ctrl-C or either child exit terminates its sibling and
  removes private temporary state.

### 4. Validation & Error Matrix

| Condition | Required outcome |
| --- | --- |
| Proxy setting absent | Preserve existing direct subprocess inheritance. |
| Malformed, credential-bearing, or non-loopback proxy URL | `Settings` raises `ValueError` before worker use. |
| Metadata or subtitle provider response is 429 | Raise `TransientFetchError("youtube_rate_limited")`. |
| Configured proxy refuses/fails connection | Raise `TransientFetchError("youtube_proxy_unavailable")`; do not make a direct attempt. |
| Configured proxy exceeds the socket or wall-clock budget | Raise `TransientFetchError("youtube_proxy_timeout")`; do not make a direct attempt. |
| Proxy responds with 407 | Raise `TransientFetchError("youtube_proxy_authentication_failed")`. |
| Bounded subtitle exceeds the byte limit | Raise `IngestLimitExceeded` exactly as the direct path does. |
| tinyproxy/ssh/python missing or either loopback port occupied | Helper exits before opening a long-running tunnel. |
| tinyproxy or SSH exits after readiness | Helper stops its sibling, removes temporary state, and exits non-zero. |

### 5. Good / Base / Bad Cases

- Good: `http://127.0.0.1:18080`, both child environments contain the same
  proxy and verified CA values, and stopping the tunnel produces
  `youtube_proxy_unavailable` with one proxied attempt.
- Base: `YOUTUBE_PROXY_URL` is absent, so existing local direct behavior and
  subprocess inheritance remain unchanged.
- Bad: set worker-global proxy variables, allow `http://proxy.example`, leave
  `.youtube.com` in ambient `NO_PROXY`, retry after proxy failure without an
  `env`, bind tinyproxy/SSH to `0.0.0.0`, or include raw stderr in the raised
  exception.

### 6. Tests Required

- Validate both accepted loopback forms and every rejected URL component.
- Assert yt-dlp and bounded subtitle runner calls receive identical proxy and
  CA values, ambient `ALL_PROXY` and YouTube `NO_PROXY` cannot bypass routing,
  and the parent environment is unchanged.
- Assert metadata and subtitle timeout/rate-limit/proxy error paths emit the
  stable classifications and make no unproxied retry.
- Run a real bounded child against an absent loopback proxy to prove failure is
  closed before any provider connection.
- Syntax-check the helper and statically assert loopback listeners, CONNECT
  443, default-deny filters, fail-fast SSH keepalives, cleanup traps, and no
  automatic Homebrew/service configuration.
- Before production activation, canary metadata and a non-empty bounded
  subtitle through server loopback, verify the listener is not public, and
  submit at most one user item after the public canary succeeds.

### 7. Wrong vs Correct

#### Wrong

```python
os.environ["HTTPS_PROXY"] = settings.youtube_proxy_url
result = subprocess.run(yt_dlp_args)
if result.returncode:
    result = subprocess.run(yt_dlp_args, env_without_proxy)
```

This proxies every later worker dependency, mutates shared process state, and
leaks to the production IP when the tunnel is unavailable.

#### Correct

```python
child_env = os.environ.copy()
child_env.update({
    "HTTP_PROXY": proxy_url,
    "HTTPS_PROXY": proxy_url,
    "NO_PROXY": "127.0.0.1,localhost,::1",
})
result = subprocess.run(yt_dlp_args, env=child_env)
if result.returncode:
    raise TransientFetchError(classify_without_raw_stderr(result.stderr))
```

The real implementation also supplies lowercase proxy variables, preserves
both trusted CA variables, removes ambient `ALL_PROXY`, and gives an equivalent
environment copy to the bounded subtitle child.

## Scenario: Server-local residential egress

### 1. Scope / Trigger

Trigger: the production IP stays blocked and the home egress cannot run
unattended. A paid sticky residential proxy replaces the Mac tunnel behind the
same server loopback port. The application contract above is unchanged; only
the process listening on server `127.0.0.1:18080` differs.

### 2. Signatures

```text
deploy/youtube-egress/tinyproxy.conf.example   -> /etc/notebook-agent/youtube-egress/tinyproxy.conf (root 0600)
deploy/youtube-egress/tinyproxy.filter         -> /etc/notebook-agent/youtube-egress/tinyproxy.filter (root 0600)
deploy/systemd/notebook-agent-youtube-egress.service
YOUTUBE_PROXY_URL=http://127.0.0.1:18080       # unchanged, credential-free
```

### 3. Contracts

- Provider credentials exist only in the tinyproxy `Upstream http user:pass@host:port`
  line. They never enter `notebook-agent.env`, `YOUTUBE_PROXY_URL`, PostgreSQL,
  logs, or command lines; the loopback/credential-free `Settings` validation is
  not relaxed.
- The unit runs tinyproxy in the foreground as `DynamicUser=yes` and receives
  both files through `LoadCredential`; the config's `Filter` path is the fixed
  `/run/credentials/notebook-agent-youtube-egress.service/filter`.
- tinyproxy listens and allows only `127.0.0.1:18080`, permits only CONNECT 443,
  and default-denies every host except `youtube.com` / `googlevideo.com`
  (anchored ERE, same patterns as the home helper). `FilterExtended Yes` is used
  because Ubuntu 22.04 ships tinyproxy 1.11.0 without `FilterType`.
- The upstream must be a sticky session: signed subtitle/audio URLs are bound
  to the metadata request's exit IP, and metadata and subtitle fetches run in
  separate children.
- Home egress and residential egress are mutually exclusive on port 18080; the
  home helper's server-port preflight fails closed while the unit is active.
- The release deploy script does not manage this unit; it is operator-installed
  infrastructure like `notebook-agent-asr-worker.service`.

### 4. Validation & Error Matrix

| Condition | Required outcome |
| --- | --- |
| CONNECT to a non-YouTube host, look-alike host, or port other than 443 | tinyproxy 403; nothing reaches the paid upstream. |
| Wrong credentials or exhausted balance (upstream 407) | `youtube_proxy_authentication_failed`; no direct retry. |
| Unit stopped | `youtube_proxy_unavailable`; no direct retry. |
| Sticky exit IP flagged | `youtube_rate_limited` / `youtube_fetch_failed`; operator switches sticky port and restarts only the egress unit. |

### 5. Good / Base / Bad Cases

- Good: unit active, `ss` shows only `127.0.0.1:18080`, `example.com` via the
  proxy returns 403, a public video yields metadata and non-empty subtitles.
- Base: unit absent or stopped and `YOUTUBE_PROXY_URL` unset; direct behavior.
- Bad: credentials in `YOUTUBE_PROXY_URL` or the worker env file, tinyproxy on
  `0.0.0.0`, the distro `tinyproxy.service` left running, or a per-request
  rotating upstream.

### 6. Tests Required

- Statically assert the example config is loopback-only, CONNECT-443-only,
  default-deny, uses the credentials-directory filter path, and carries only a
  placeholder `Upstream` credential.
- Assert the filter matches the home helper's host patterns.
- Assert the unit uses `DynamicUser`, `LoadCredential` for both files, runs
  tinyproxy in the foreground, and does not load the application env file.
