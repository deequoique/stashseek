# Frontend implementation and validation

## Result

- All seven route pages use lazy dynamic imports. Public routes have local
  Suspense boundaries; the protected Outlet boundary sits below the eager
  Session/capabilities queries, so chunk loading cannot delay authorization.
- Public capabilities load alongside Session verification. Successful login
  preserves only that public DTO and its original updatedAt in a fresh private
  QueryClient, then navigates without awaiting history.
- Verified sessions with chat enabled warm only the first 30-item history page
  and its latest transcript. Shared options reuse the same query keys and a
  bounded 60-second freshness interval; route reads join pending warming.
- History/turn/session/capability reads consume AbortSignal. Session teardown
  cancels warming; the captured-session check stops late history responses from
  starting another private read. Cancelled old 401 responses cannot invoke the
  current global Session handler, including cancellation while parsing a body.
- Successful login clears the disposed client before activation. A generation
  key remounts query observers when the QueryClient rotates: TanStack observers
  retain the client supplied at creation, so changing provider identity alone
  is insufficient for a clean handoff. No private data crosses rotation.
- Empty-history creation, disabled/error states, explicit send/reset/delete
  invalidation, and durable SSE transcript convergence remain intact.

## Bundle evidence

Raw file bytes and gzip level 9 were measured with the same Python gzip
compressor before and after. These are local production build artifacts, not
network timings.

| Asset | Before raw / gzip bytes | After raw / gzip bytes |
| --- | --- | --- |
| Main entry JavaScript | 501,320 / 152,216 | 281,439 / 87,656 |
| Shared JSX runtime | Included in entry | 8,557 / 3,275 |
| Showcase route | Included in entry | 22,432 / 7,870 |
| Login route | Included in entry | 9,789 / 3,670 |
| Chat route including Markdown | Included in entry | 137,478 / 41,192 |
| CSS | 86,635 / 17,643 | 86,635 / 17,643 |

The main entry is 43.9% smaller raw and 42.4% smaller gzip. Vite's own gzip
report uses its default compression settings and reports main entry
281.44 kB / 89.00 kB, versus the recorded baseline 501.32 kB / 154.63 kB.
The browser also loads the shared runtime and the selected page's small chunk;
protected page code, especially Chat's Markdown bundle, is deferred on public
entry. Image assets and CSS were not changed.

## Validation

- Complete `pnpm test`: **19 files, 155 tests passed**.
- `pnpm typecheck`: passed.
- `pnpm lint`: passed.
- `pnpm build`: passed, with separate route chunks.
- `pnpm check:api`: passed against the main agent's regenerated contract files.
- `git diff --check`: passed.

New regression coverage proves public capabilities start while Session is
held, private history waits for Session proof, login reaches the library while
history is held, only one capabilities request survives login handoff, no
redundant Session GET follows verification, and chat navigation joins the
already pending history/transcript reads. It also covers warm-cache rendering,
disabled/empty/failure/stale history, cancellation during history and transcript,
public-only cache copying, and a delayed old Session 401 after a new login.

One existing unauthenticated-route assertion was changed to query the current
DOM inside waitFor because a lazy login/cache remount can replace a node between
findByRole resolving and an assertion. The final condition is unchanged.

Browser smoke and backend verification are owned by the main agent. No packages,
API schema source, image assets, production configuration, or commits changed
in this frontend subtask.
