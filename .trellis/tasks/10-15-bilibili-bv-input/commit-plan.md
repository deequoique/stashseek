# Proposed Local Commit — Approval Pending

The user expanded the original BV-only scope to include automatic link extraction
from full Bilibili share text. This current nine-file plan supersedes the previous
seven-file proposal. No commit, push or deployment has been authorized/performed.

## Proposed commit

`feat(ingest): accept BV IDs and copied Bilibili shares`

- `app/ingest/submission.py`
- `tests/test_bilibili_url.py`
- `tests/test_ingest_submission.py`
- `web/src/library/AddVideosDialog.tsx`
- `web/src/library/AddVideosDialog.test.tsx`
- `web/src/library/videoInput.ts`
- `web/src/library/videoInput.test.ts`
- `.trellis/spec/backend/bilibili-connector.md`
- `docs/how-to/use-web-library.md`

The change accepts complete BV IDs, extracts actual URLs from copied sharing text,
retains server URL admission/canonicalization and duplicate handling, and updates
Add Videos paste/Enter/submit behavior, tests and documentation. The helper and
its tests are new; the other seven paths were clean before the original BV task.

BV task planning, verification and screenshots under
`.trellis/tasks/10-15-bilibili-bv-input/` remain separate bookkeeping for
`/trellis:finish-work`; they are not silently included in this product commit.

## Existing dirty paths outside this commit

All retained and excluded:

- `.env.example`
- `.trellis/spec/backend/channel-identity-linking.md`
- `.trellis/spec/backend/index.md`
- `.trellis/spec/backend/web-browser-runtime.md`
- `.trellis/spec/frontend/component-guidelines.md`
- `.trellis/spec/frontend/hook-guidelines.md`
- `.trellis/spec/frontend/index.md`
- `.trellis/spec/frontend/state-management.md`
- `app/api/app.py`
- `app/api/auth_schemas.py`
- `app/api/conversation_routes.py`
- `app/api/email_auth_routes.py`
- `app/api/runtime.py`
- `app/channels/identity.py`
- `app/cli.py`
- `app/config.py`
- `app/models.py`
- `app/web/auth.py`
- `app/web_auth.py`
- `docs/README.md`
- `docs/how-to/README.md`
- `docs/reference/README.md`
- `docs/reference/configuration.md`
- `docs/reference/web-api.md`
- `pyproject.toml`
- `scripts/export_web_openapi.py`
- `tests/test_conversation_list_queries.py`
- `tests/test_web_api_public.py`
- `tests/test_web_api_runtime.py`
- `tests/test_web_auth_migration.py`
- `tests/test_web_auth_postgres.py`
- `tests/test_web_session_cache.py`
- `uv.lock`
- `web/src/api/client.test.ts`
- `web/src/api/client.ts`
- `web/src/api/contracts.ts`
- `web/src/api/openapi.json`
- `web/src/api/schema.d.ts`
- `web/src/app/App.test.ts`
- `web/src/app/App.tsx`
- `web/src/app/AppShell.test.tsx`
- `web/src/app/AppShell.tsx`
- `web/src/auth/LoginPage.test.tsx`
- `web/src/auth/LoginPage.tsx`
- `web/src/chat/ChatPage.test.tsx`
- `web/src/chat/ChatPage.tsx`
- `web/src/library/LibraryPage.test.tsx`
- `web/src/library/LibraryPage.tsx`
- `web/src/styles.css`
- `.trellis/spec/backend/external-login.md`
- `.trellis/spec/frontend/external-login.md`
- `.trellis/tasks/10-14-email-login-delivery/`
- `.trellis/tasks/10-14-history-loading-performance/`
- `.trellis/tasks/10-14-verify-local-runtime/`
- `.trellis/tasks/10-15-external-login-foundation/`
- `.trellis/tasks/10-15-microsoft-login/`
- `.trellis/tasks/10-15-multi-provider-login/`
- `.trellis/tasks/10-15-qq-website-login/`
- `.trellis/tasks/10-15-wechat-website-login/`
- `app/api/external_auth_routes.py`
- `app/external_auth.py`
- `app/external_providers/`
- `docs/how-to/configure-external-login.md`
- `migrations/versions/e2f3a4b5c6d7_external_login_foundation.py`
- `tests/test_external_login.py`
- `tests/test_external_login_models.py`
- `tests/test_external_login_postgres.py`
- `tests/test_external_login_routes.py`
- `tests/test_external_providers.py`
- `web/src/auth/ExternalLoginMethods.tsx`
- `web/src/auth/ExternalLoginPage.test.tsx`
- `web/src/auth/ExternalLoginPage.tsx`
- `web/src/chat/conversationQueries.test.ts`
- `web/src/chat/conversationQueries.ts`

## Approval

A later explicit approval must refer to this final nine-file scope before making
the local commit. The user's share-text correction authorized implementation, not
Git operations. No remote push or deployment is included.
