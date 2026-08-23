# Hook Guidelines

> How React and TanStack Query hooks are used.

---

## Overview

TanStack Query owns remote server state. React `useState` owns short-lived form and disclosure state. Effects are reserved for browser APIs and transitions that cannot be expressed as render state.

---

## Custom Hook Patterns

The current MVP does not add custom hooks merely to hide a few lines. Extract a hook only when it expresses a stable product behavior used by more than one component or needs isolated lifecycle testing.

Pure logic should remain a pure function. `shouldPollLibrary()` is a function, not a hook, because it only derives a boolean from lifecycle data.

---

## Data Fetching

- Use `useQuery` for session, list, and detail reads.
- Use `useInfiniteQuery` for cursor-based transcript pages.
- Use `useMutation` for add, archive, restore, retry, edit, email challenge,
  email verification, and logout.
- Query keys begin with the resource boundary: `session`, `library`, `library-item`, `transcript`, or `login-challenge`.
- Invalidate `library` after any item mutation. Update the exact detail cache from the mutation response.
- Poll only when a visible item is `queued` or `processing`. Terminal states stop polling.
- API errors do not trigger unbounded retries. Only a `session_invalid` 401
  immediately clears and replaces the private query client.
- A mutation may await the exact detail query required for a safe local-state
  handoff. Run related list invalidation in the background when its latency
  does not determine primary-action readiness.
- After resource creation returns enough canonical IDs, seed its exact detail
  cache with a safe empty projection before background list convergence; do not
  block the composer on an avoidable initial detail GET.

---

## Naming Conventions

- Hook names begin with `use` only when they call hooks.
- Query results are named after the resource (`library`, `item`, `transcript`, `session`).
- Mutation variables describe the action rather than exposing HTTP method details.

---

## Common Mistakes

- Polling every item independently instead of refreshing one library page.
- Polling ready, failed, needs-action, or archived items.
- Keeping a browser login secret in an effect-independent global or Web Storage.
- Using an effect to duplicate query-derived state.
- Applying the global unauthorized handler to a recoverable
  `verification_failed` response.
