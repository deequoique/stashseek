# Consolidate the product in the browser extension

## Goal
Replace the standalone Web UI with an installable extension containing every existing end-user capability. Keep the server API, ingestion, Agent, MCP and optional channel services.

## Requirements
- Extension-owned login, logout and account linking/device management without a hosted approval page.
- Preserve library search/filter/sort/pagination, batch save, notes, retry/archive/restore, video details and bounded transcript with source timestamps.
- Preserve conversations, history/reset/delete, streaming execution timeline, answers and citations.
- Preserve user-invoked YouTube/NTULearn capture and its privacy boundary.
- Prefer migrating existing tested React UI into an extension-owned full-page workspace, with a small capture popup, over rebuilding and losing features.
- Retire standalone web package/hosting only after equivalent UI is available; keep CI/deployment checks and API OpenAPI generation functioning.
- No content deletion, live deployment, or unrelated changes. Existing dirty work must be preserved.

## Acceptance
- [x] All listed features available inside extension; no hosted Web page dependency.
- [x] Auth and capture grants remain separated; legacy capture-only grants gain no private-library privilege.
- [x] Tenant isolation, revocation, expiry, origin policy and session cache teardown tested.
- [ ] Extension tests/typecheck/lint, both target builds and permission audits pass in this checkout; CI is configured for Node 22 and the migrated lockfile.
- [x] Relevant backend/auth/library/conversation/capture/runtime tests pass.
- [x] CI, runbooks, configuration and generated contracts describe real implemented behavior.
- [x] Independent GPT-6 Sol review completed; actionable findings fixed and rechecked.
