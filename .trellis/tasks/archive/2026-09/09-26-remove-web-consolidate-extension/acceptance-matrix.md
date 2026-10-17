# Migration acceptance matrix

| Capability | Previous evidence | Target | Verification |
| --- | --- | --- | --- |
| Email login/logout | web/src/auth, app/api/email_auth_routes.py | Extension session endpoints + workspace | Cross-origin direct flow, invalid/replayed proof, logout/expiry |
| Capture | extension/src/page-capture.ts + worker.ts | User-invoked capture popup | Exact permission audit, fixtures, capture bearer scope |
| Library | web/src/library | Extension workspace | Search/filter/sort/page, batch partial outcomes, lifecycle |
| Item management | library/detail components | Extension workspace | Notes, archive/restore/retry, available_actions |
| Transcript/detail | web/src/videos | Extension workspace | Bounded transcript pagination and timestamp links |
| Chat/history | web/src/chat + api/client.ts | Extension workspace | SSE timeline/citations, history/load/delete/reset |
| Account linking | web/src/account/AccountLinkPage.tsx | Extension workspace | Tenant merge invalidates sessions and cached private state |
| Devices | BrowserCompanionPage.tsx | Extension workspace | Tenant ownership, own disconnect/server revoke |
| Client routing | BrowserRouter | Extension HashRouter | Open/reload/deep links without hosted SPA |
| Distribution | Web static bundle + extension popup | Extension dist only | Local + production package audits, no remote executable code |
| Server | app/api/runtime.py + combined runtime | API-only + MCP/channel runtime | Non-API paths no SPA; API/MCP transport isolation |
| CI/deploy | web-auth-contract workflow + scripts | Preserve gated deploy with extension checks | Existing SSH safety/concurrency/migration boundaries |
| Documentation | README/docs/runbooks/spec | Actual implemented settings and installation | No nonexistent env flags, broken links or hosted UI instructions |

## Known initial defects to resolve
- Capture-only grant library access unintentionally widens existing credentials.
- Auth challenge still expects a Web Origin; extension Origin does not match.
- window.prompt login cannot survive expected popup lifecycle and misses native form UX.
- Deleted CI file also contained the production deployment gate.
- Bulk docs replacement corrupted DISPATCH env var names and invented EXTENSION_API_ONLY.
- Former Web sources/tests are recoverable from Git HEAD and should be migrated, not dropped.

## Validation log

- Backend/API/deployment focused suites: `39 passed` (latest run), including the capture-grant
  library isolation regression.
- Python compile and `git diff --check`: passed.
- OpenAPI export/check: passed; extension auth challenge/verify/session/logout paths are present.
- Extension API client focused TypeScript check: passed.
- Full extension install/typecheck/lint/test/build/audit: configured in CI with Node 22 and the
  regenerated lockfile, but not executable in this environment because only Node 20 is installed
  and its cached extension dependencies do not include React/jsdom.
- GPT-6 Sol final review: no remaining product P0/P1 after auth bootstrap, OpenAPI, middleware,
  and CI-order fixes. Multi-worker extension sessions remain an explicit limitation: use sticky
  routing or a shared session store.

## Validation log
Previous 18 backend + 98 capture tests passed but did not cover migrated end-to-end functionality. They are baseline only, not task acceptance.
