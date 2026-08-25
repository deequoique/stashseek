# Runtime Logging and Cross-Process Diagnostics

## Scenario: privacy-safe StashSeek Chat and LangBot request tracing

### 1. Scope / Trigger

Use this contract whenever changing runtime logging, request diagnostics, the LangBot bridge envelope, gateway/CLI
startup, retrieval detail logging, or deployment log paths. StashSeek Chat and LangBot are separate processes with
separate log owners; correlation is by a random trace ID, never by sharing a file or logging user content.

The production invariant is fail-closed observability: logging may expose fixed stages, counters, stable outcomes and
exception class names, but not questions, history, prompts, model output, tool payloads, evidence, URLs, external
identities, credentials or exception messages. Explicit local development mode may additionally expose allow-listed
retrieval details and complete provider HTTP error message/model/response body for local diagnosis.

### 2. Signatures

Runtime configuration:

```dotenv
STASHSEEK_ENV=production
STASHSEEK_LOG_DIR=.runtime/logs
STASHSEEK_LOG_MAX_BYTES=10485760
STASHSEEK_LOG_BACKUP_COUNT=5
STASHSEEK_LOG_RETRIEVAL_CONTENT=false
```

Logging and correlation boundaries:

```python
def configure_runtime_logging(
    *, log_dir: str, max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
) -> bool: ...

def new_trace_id() -> str: ...  # 32 lowercase hexadecimal characters

@dataclass
class RequestDiagnostics:
    @classmethod
    def start(
        cls, request_id: str, tenant_id: int, trace_id: str | None = None,
        *, allow_retrieval_content: bool = False,
        environment: str = "production",
    ) -> "RequestDiagnostics": ...

    def event(
        self, stage: str, *, http_status: int | None = None, ...
    ) -> None: ...
    def retrieval_detail(self, *, tool_name: str, call_index: int, ...) -> None: ...

class ChannelEnvelope:
    trace_id: str | None  # when present, exactly `[0-9a-f]{32}`
```

`app.cli` and `app.channels.http_gateway` must call `configure_runtime_logging()` explicitly. Do not rely on a host
process or test framework to install handlers or set `notebook_agent.runtime` to `INFO`.

### 3. Contracts

- StashSeek Chat emits the same compact JSON event to stdout and the stable
  `notebook-agent-YYYY-MM-DD.log` compatibility filename. Local development
  defaults to `.runtime/logs`; Linux deployment uses `/var/log/notebook-agent` with systemd
  `LogsDirectory=notebook-agent` and keeps `ProtectSystem=strict`.
- `DailySizeRotatingFileHandler` rotates on both date and
  `STASHSEEK_LOG_MAX_BYTES`, retaining at most `STASHSEEK_LOG_BACKUP_COUNT`
  backups. Former `NOTEBOOK_AGENT_*` keys remain source-aware fallbacks.
  Repeated configuration is idempotent and must not duplicate handlers.
- LangBot core keeps its own stdout and `data/logs/langbot-YYYY-MM-DD.log`. The bridge writes a smaller allow-listed
  JSON event to plugin stderr only. Neither process writes the other's file.
- The bridge creates a new `uuid4().hex` trace ID before the signed loopback POST. The gateway validates the fixed
  lowercase-hex shape after authentication and still creates its own authoritative request ID.
- Trace IDs are correlation metadata only. They never participate in tenant selection, authorization, deduplication,
  idempotency, conversation identity or public response payloads.
- `RequestDiagnostics.event()` accepts explicit allow-listed enum and numeric fields. Exceptions are projected to
  class names only. Unknown strings are omitted or replaced with fixed safe values; arbitrary `extra` dictionaries are
  forbidden.
- Bounded-autonomy diagnostics may add only fixed recovery category/action/
  outcome values, safe numeric recovery counts, a boolean Todo-used flag, and
  the allow-listed `todo_write` tool name. Todo titles/items, exact-read
  fingerprints, retry arguments, error-envelope payloads, and model drafts are
  forbidden in every environment.
- Provider HTTP failures always additionally project `http_status` only when it is a non-boolean integer in the
  inclusive range 100–599. In production, response bodies, exception messages, request schemas and provider payloads
  remain forbidden. In explicit development mode, the same event additionally carries the complete exception message,
  provider model and response body. Diagnostics do not explicitly add request authorization headers or API keys.
- `retrieval_detail()` emits only when both `environment == "development"` and
  `allow_retrieval_content is True`. Its explicit fields may include query, limit/radius, segment/item ID, title,
  author/description, URL, score, excerpt and start/anchor. It must never accept raw model or tool payloads.
- Production, bridge and LangBot logs never contain question/history text, prompts, model output, tool arguments or
  results, evidence/content IDs, URLs, vectors, provider payloads, channel identities, secrets or exception messages.
  Development retrieval logging does not relax the ban for history, prompts, model/action output, vectors, external
  identities or secrets, except that an observed provider HTTP error's own message/model/response body is recorded in
  full for local diagnosis.
- Logging sink failure is non-fatal: preserve stdout when file setup fails, emit the stable
  `file_logging_unavailable` diagnostic with exception class only, and do not change Agent results or reply count.

### 4. Validation & Error Matrix

| Condition | Required behavior |
| --- | --- |
| default or production configuration | retrieval content disabled; stdout and bounded daily file contain only safe fields |
| `development` plus retrieval-content flag | explicit retrieval-detail fields may be emitted to local stdout/file |
| production plus retrieval-content flag | settings validation fails before startup; no content logging begins |
| invalid environment enum | settings validation fails; do not infer environment from path, TTY or hostname |
| invalid/uppercase/oversized trace ID | `ChannelEnvelope` validation fails; never reuse it as request ID |
| missing trace ID at a trusted local boundary | create a new random trace ID without changing tenant/business identity |
| request/tool/output usage limit | emit fixed `limit_kind`, safe counts and exception class; omit exception message |
| bounded read/answer recovery | emit only fixed category/action/outcome and numeric count; omit fingerprint, arguments, Todo and evidence |
| unknown usage-limit text | classify as `unknown`; never copy the source string into logs |
| provider returns HTTP 4xx/5xx in production | include safe `http_status`, phase and exception class; omit body/message/schema |
| provider returns HTTP 4xx/5xx in development | include status/phase/class plus complete exception message, model and response body |
| log directory/file cannot be opened | keep stdout, emit safe `file_logging_unavailable`, continue serving requests |
| repeated logger configuration | reuse the installed handlers; do not duplicate each event |
| bridge request succeeds/fails | stderr event contains fixed stage/channel/outcome, trace ID, duration and optional error class only |

### 5. Good / Base / Bad Cases

- Good: a bridge `forward` event and StashSeek Chat `accepted`, route, model, tool, retrieval and final-answer events
  share one random trace ID; operators can join them without seeing the user's message or platform identity.
- Base: a CLI request has no bridge trace, so the trusted application boundary creates one; stdout and the local daily
  file show the same safe event.
- Bad: log a prompt/tool payload to diagnose a failure, use a platform message ID as the trace ID, let StashSeek Chat
  append to LangBot's file, infer development mode from `.runtime`, serialize `str(exception)`, or disable business
  failure handling because the file sink is unavailable.

### 6. Tests Required

- Configure logging without pytest/caplog changing the logger level; assert one safe event appears in stdout and the
  expected daily file.
- Test date rollover, size rollover, backup retention, configuration idempotency, invalid bounds and unwritable-file
  fallback. Assert logging failures do not change the business result.
- Send a fake bridge event through the signed gateway; assert a valid random trace ID correlates both processes, while
  malformed and unsigned envelopes fail closed and never affect tenant/request identity.
- Scan stdout, StashSeek Chat files and captured bridge stderr with sensitive sentinels for question/history,
  retrieval text, prompts, model/tool/action/provider payloads, evidence, URLs, identities, secrets, vectors and
  exception messages. Assert every sentinel is absent in production.
- In explicit development mode, assert allowed query/result fields and full provider error message/model/body appear in
  stdout and file while unrelated history, authorization and secret sentinels remain absent.
- Cover request/tool/output/unknown limit classification, safe counters, fixed error codes, phase/outcome allow-lists
  and environment-specific exception projection. Construct `ModelHTTPError` values with body sentinels and assert only
  a valid 100–599 `http_status` survives in production while development preserves message/model/body.
- Keep channel, tenant isolation, conversation persistence, action/confirmation, idempotency and single-reply
  regressions green. Use static deployment assertions plus a Linux smoke for journal and `/var/log/notebook-agent`.

### 7. Wrong vs Correct

#### Wrong

```python
logger.exception("agent failed for %s with tool result %r", question, payload)
trace_id = envelope.platform_message_id
```

This exposes private content and turns external identity into cross-process correlation state.

#### Correct

```python
diagnostics.event(
    "agent_failed",
    error_code="runtime_error",
    exception=exc,          # serializer keeps the class name only
    http_status=exc.status_code if isinstance(exc, ModelHTTPError) else None,
    agent_phase="retrieval",
)
trace_id = uuid.uuid4().hex
```

The process that observes a stage records only its own fixed execution facts. Content diagnostics, when explicitly
enabled for local development, go through `retrieval_detail()` rather than a general-purpose logging escape hatch.
