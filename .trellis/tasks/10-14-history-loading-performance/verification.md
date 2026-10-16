# Website and history performance verification

## Outcome

Local source changes are complete. The local demo remains available at
http://127.0.0.1:5173/chat. Production was not deployed or timed.

## Measured evidence

| Boundary | Before | After |
| --- | --- | --- |
| Main production JavaScript entry | 501,320 bytes | 281,439 bytes (43.9% smaller) |
| Entry gzip, identical level-9 compressor | 152,216 bytes | 87,656 bytes (42.4% smaller) |
| Affected authoritative Session paths | 4 SELECTs | 1 joined SELECT |
| Public transcript query | Full turn rows, including internal model_messages | Only public response fields |

The selected route/shared chunks are still loaded as needed. These bundle
measurements do not mean total page-load time improved by the same percentage.
Images and CSS are unchanged. Real remote database wake-up and network latency
remain deployment factors.

## Behavior

- All route pages load on demand; Session/capabilities queries remain outside
  the protected route Suspense boundary.
- Public capabilities load alongside Session verification and carry into a
  fresh login cache without private-data copying or an extra capabilities GET.
- Verified sessions with enabled chat preload only the first history page and
  newest transcript, without blocking navigation. Route queries join pending
  reads and use bounded in-memory freshness.
- Old clients are cleared, read requests consume AbortSignal, and query
  observers remount on client generation changes. Obsolete Session/history
  responses cannot redirect or populate a new account.
- Auth projection is captured before commit/rollback expires ORM rows.
- Public transcript reads exclude internal provider-message history while
  retaining tenant ownership, completed order, citations and action results.

## Automated checks

- Frontend: 19 test files / **155 tests passed**; typecheck, lint, build,
  generated API consistency and whitespace checks passed.
- Focused Python auth/history/email check: **56 passed**.
- Auth query-count regression: **41 passed**; before the source fix, the same
  new matrix had 12 failures proving the four-SELECT regression.
- Full Python suite with process-local test configuration and loopback access:
  **849 passed, 2 failed, 74 skipped** in 11.26 seconds. Both failures are the
  pre-existing August deletion-date fixtures documented by the startup task.
  PostgreSQL integration and optional LangBot artifact checks remain skipped.
- Task context validation passed. Independent full-scope review and adaptive
  cache-generation review found no material issue.
- Generated OpenAPI/TypeScript were synchronized through `pnpm generate:api`;
  the changes are 19 existing HTTP 422 descriptions, with no DTO shape change.

## Browser checks

Verified the actual React frontend against the isolated demo API:

- Library to chat displays history and saved answers/citations.
- Direct `/chat` refresh shows loading then the same complete history.
- Selecting an older conversation displays its correct question, answer and
  three source links.
- Public homepage and login form load successfully through lazy route chunks.
- Browser warning/error queries returned no entries.
- Screenshot: `/private/tmp/stashseek-history-performance.jpg`.

The demo does not provide real email authentication or production persistence.
Login handoff and delayed-request behavior were verified by controlled frontend
integration tests. No live email, model request or private database was used.

## Review and bookkeeping

Updated backend Web runtime and frontend hook/state specs with ORM expiration,
public transcript projection, preload/cancellation and observer remount rules.
Source changes and this task's artifacts are ready for commit confirmation.
Earlier startup verification and superseded email-planning records remain
separate and are excluded from the proposed performance commit.
