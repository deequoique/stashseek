# Channel save/link route incident analysis — 2026-08-18

## Observed routes

1. A reply referring to a prior Bilibili video received an unconstrained zero-search natural answer that claimed inventory state and offered to save, without creating pending state.
2. The first short “保存” produced `answer_unavailable`: the zero-search natural-answer validator rejected the model draft, most likely because it repeated a historical URL.
3. A later model explanation incorrectly claimed no safety validation had run; failed no-action answers are not persisted and the model does not receive validator details.
4. The second “保存” reached `save_videos` with a history-derived URL. Exact current-message batch validation rejected it and returned canonical `invalid_url`.
5. Explicit save with a `b23.tv` URL reached the terminal save path and returned a batch `unsupported_url`; the short domain is intentionally outside the Bilibili parser allowlist.
6. “什么 bilibili 链接是受支持的” used the zero-search natural-answer path. Capability examples naturally contain a URL, but the validator rejects every model-authored URL and maps all reasons to the same public safety fallback.

## Code evidence

- `integrations/langbot_kb_plugin/components/event_listener/knowledge_agent.py` forwards `str(event.message_chain)` as text and has no explicit reply-reference contract.
- `app/agent/orchestrator.py` chooses terminal Action first; zero-search natural text is passed to `validate_natural_answer`, whose failure maps to `answer_unavailable`.
- `app/agent/answer_validation.py` rejects URL patterns, source blocks and invalid Citation markers without a stable reason code.
- `app/agent/actions.py` matches mutation URLs against URLs parsed from the current question and emits canonical ActionOutcome text.
- `app/connectors/bilibili.py` accepts only official HTTPS `/video/BV…` or `/video/av…` paths; tests intentionally reject `b23.tv`.
- `app/ingest/tasks.py::_connector()` still constructs only `YouTubeConnector`, so Bilibili URL recognition is ahead of end-to-end worker readiness.

## Root causes

- Supported-link examples and untrusted model sources share one coarse “URL present” signal.
- Conversational confirmation language is not coupled to durable pending action state.
- The bridge flattens the platform message chain, so current-message parsing cannot reliably distinguish user prose from structured quoted text.
- Bilibili URL recognition and ingestion execution are incomplete at different layers: admission recognizes official URLs while the worker selector constructs only `YouTubeConnector`.
- Error codes and safe messages are independently hard-coded across Agent actions/orchestration, channel adapters, API/MCP projection, submission, notification and worker modules. Some values such as `invalid_url` and `answer_unavailable` hide multiple distinct causes, preventing precise recovery and diagnostics.

## Constraints retained

- Do not allow model history to authorize mutations. Structured platform `quoted_text` is accepted as current-turn input after length, URL, host and batch validation.
- Do not broadly allow URLs in natural answers.
- Do not log rejected drafts, message content or URLs.
- A Bot save offer must create durable pending state before the confirmation question is returned.
- Bilibili support must include a real worker canary reaching `ready`; URL parser success alone is insufficient.
- Do not mix normal lifecycle/disposition strings into the centralized error catalog; centralization applies to stable error identities and their safe projection metadata.
