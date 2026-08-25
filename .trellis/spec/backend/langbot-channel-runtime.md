# LangBot Channel Runtime Contract

## Scenario: Required bridge plugin readiness and fail-closed channels

### 1. Scope / Trigger

This contract applies when a LangBot 4.10.6 adapter forwards private-channel
messages to StashSeek Chat through the stable plugin id
`notebook-agent/notebook-knowledge-agent`.
The plugin runtime connects asynchronously, while enabled adapters can receive
backlogged platform messages immediately. A process-level startup delay cannot
prove the bridge is ready and cannot protect a later runtime disconnect.

### 2. Signatures

The fixed upstream patch is:

```text
integrations/langbot-4.10.6-redact-monitoring.patch
```

It adds these LangBot `plugin` configuration fields:

```yaml
plugin:
  required_plugins: ["notebook-agent/notebook-knowledge-agent"]
  required_plugins_ready_timeout_seconds: 30
```

Relevant patched methods are:

```python
async def PluginRuntimeConnector.initialize_plugins() -> None
def PluginRuntimeConnector.required_plugins_for_pipeline(
    bound_plugins: list[str] | None,
) -> tuple[str, ...]
def PluginRuntimeConnector.validate_required_plugin_event(
    event_ctx: EventContext,
    bound_plugins: list[str] | None,
) -> None
async def RuntimePipeline._reply_fail_closed(query: Query, failure: Exception) -> None
```

### 3. Contracts

- `required_plugins` contains unique `author/name` refs. Empty preserves
  upstream LangBot behavior; StashSeek Chat deployments must not leave it empty.
- LangBot derives the plugin id from manifest `metadata.author/name`. Keep
  `author: notebook-agent` and `name: notebook-knowledge-agent` stable during
  product renames; only human-facing label and description may change.
- `required_plugins_ready_timeout_seconds` is positive. It is a deadline, not
  a sleep duration: adapters start as soon as every required plugin reports
  `status == "initialized"`.
- A bridge pipeline sets `enable_all_plugins=false` and explicitly binds the
  same required bridge ref. Telegram, WeChat, and other enabled adapters remain
  concurrent; there is no current-channel switch.
- The worker reads its own installed-plugin `.env`, mode `0600`, containing
  `CHANNEL_GATEWAY_SECRET`, loopback `CHANNEL_GATEWAY_URL`, and explicit
  `KB_BOT_CHANNELS`. Do not duplicate those values in LangBot core config/logs.
- A required bridge event is valid only when its manifest appears in
  `emitted_plugins` and it calls `prevent_default()`. The bridge replies via
  `EventContext.reply(...)` before that early return. LangBot plugin runtime
  serializes each emitted `PluginContainer` as a dict whose
  `plugin["manifest"]` is a `ComponentManifest` dump; the original
  `manifest.yaml` metadata is therefore at
  `plugin["manifest"]["manifest"]["metadata"]`. Required-plugin validation
  must decode this nested shape defensively and must not assume a flat
  `plugin["manifest"]["metadata"]` mapping.
- The bridge keeps a bounded, short-lived in-memory claim keyed by bot UUID and
  platform message ID. A duplicate delivery may still reach LangBot, but only
  the first claim may POST to the gateway and reply to the platform. The key is
  never logged or persisted; it only collapses a redelivery burst.
- Set `SSL_CERT_FILE` and `REQUESTS_CA_BUNDLE` to certifi's CA path on macOS
  when OpenClaw/iLink reports a certificate verification failure.

### 4. Validation & Error Matrix

| Condition | Required behavior | Adapter / default Agent result |
| --- | --- | --- |
| Required plugin config malformed or timeout | LangBot startup fails before `PlatformManager.run()` | No adapter starts |
| Runtime connected; plugin not `initialized` | Keep polling until deadline | No adapter starts |
| Required bridge emitted and prevented default | Return from pipeline after bridge reply | Normal StashSeek Chat response |
| Duplicate delivery with the same bot/message correlation | Suppress the later bridge reply | Exactly one final platform reply |
| Runtime disconnected, missing emitted bridge, or no `prevent_default()` | `_reply_fail_closed()` returns fixed availability text | `MessageProcessor` is not called |
| Pipeline has no explicit required bridge binding | Preserve upstream plugin semantics | Unchanged |
| stdio runtime disconnect | Stop/restart LangBot safely | Never enable Local Agent fallback |

Errors and diagnostics may contain only internal query/bot identifiers, plugin
refs, state, error class, and duration. They must not include message text,
nickname, external sender ID, `message_preview`, tokens, HMAC secrets, or QR
codes.

### 5. Good / Base / Bad Cases

- Good: `required_plugins` explicitly lists the bridge; both Telegram and
  WeChat adapters start only after the `initialized` marker; a bridge event
  appears in `emitted_plugins` and prevents default processing.
- Base: no required plugins are configured for a non-Notebook LangBot install;
  the patch preserves the original startup behavior.
- Bad: add `sleep 5` to a shell script, bind the bridge through
  `enable_all_plugins`, or configure a Local Agent model to hide a bridge
  failure. Each leaves a route to incorrect/unsafe default processing.

### 6. Tests Required

Run the fixed-wheel check with a verified official wheel:

```bash
LANGBOT_4_10_6_WHEEL=/path/to/langbot-4.10.6-py3-none-any.whl \
  .venv/bin/pytest -q tests/test_langbot_startup_patch.py
```

Assertions:

- patch dry-run and actual application succeed against SHA-256
  `ee950fd6a687cb8c7cfe646d2b9a92cfbf09b3ddfbaf8f43ea0613905d3ffbff`;
- changed LangBot files compile;
- startup waits for actual `initialized` status and has a deadline;
- a required pipeline validates `emitted_plugins` and `prevent_default()` before
  a default stage can execute;
- adapter/monitoring/processor/diagnostic paths are redacted;
- `tests/test_langbot_bridge_plugin.py` confirms early-event replies use
  `EventContext.reply(...)` and a duplicate fake delivery produces one reply.

After deployment, verify the log marker
`Required plugins initialized; message adapters may start.` occurs before the
5300 WebUI/adapter startup, then run human Telegram E2E and WeChat private
smoke. Automated checks never substitute for those real platform tests.

### 7. Wrong vs Correct

#### Wrong

```bash
# A guessed delay can be too short, too long, and cannot handle a later crash.
sleep 5
start-langbot-adapters
```

```python
# An empty event context falls through to LangBot Local Agent.
event_ctx = await connector.emit_event(event, bound_plugins)
await execute_default_pipeline(query)
```

#### Correct

```python
# Start adapters only after runtime state proves every required plugin is ready.
await connector.initialize_plugins()
await platform_manager.run()

event_ctx = await connector.emit_event(event, bound_plugins)
connector.validate_required_plugin_event(event_ctx, bound_plugins)
if event_ctx.is_prevented_default():
    return
```

For a runtime/error contract violation, reply with the fixed availability text
and return before `MessageProcessor`; do not log or forward the incoming event
as a diagnostic payload.
