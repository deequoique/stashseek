# State Management

> How browser state is divided by responsibility.

---

## Overview

There is no Redux, Zustand, persistent query cache, or browser-auth store. The server session cookie is the authentication source of truth, TanStack Query caches server DTOs in memory, and component state owns unsaved interaction details.

---

## State Categories

- **Server state:** session, capabilities, library pages, item detail, transcript pages, and mutation results. Owned by TanStack Query.
- **Local interaction state:** open dialog, draft URLs, save reason, current filters, edit mode, and form errors. Owned by `useState`.
- **URL state:** current route and public item ID. Owned by React Router.
- **Security state:** session and CSRF raw tokens. Owned by `Secure` cookies; the session cookie is `HttpOnly`.
- **Ephemeral login state:** email address, verification code, and current
  email/code step. Held only in `LoginPage` memory until navigation.
- **Ephemeral conversation stream state:** pending answer sections keyed by
  `section_id`, including their status, temporary text, Citation DTOs, and
  streaming/completed/aborted phase. This state is never written to storage or
  treated as conversation history.

---

## When to Use Global State

Do not introduce application-global state for the current MVP. A new global store requires a demonstrated state owner that is neither server state, URL state, nor one component subtree.

The query client is global infrastructure, not the source of truth. Rotate it
after successful verification and seed only the returned canonical session.
Clear and replace it on confirmed logout or a `session_invalid` 401 before
another user can authenticate in the same browser. Operation-specific 401s,
such as an invalid verification code, stay in the owning form and must not
trigger global session teardown.

---

## Server State

- The API decides tenant scope from the server session.
- The API decides lifecycle and `available_actions`.
- The client never persists queries to `localStorage`, `sessionStorage`, IndexedDB, or a service worker.
- Add-video partial results remain in the open dialog while the library query is invalidated.
- Transcript cursors are opaque and only passed back to the API.
- Query cache contents are private tenant data and receive explicit teardown semantics.

## Conversation streaming

- `section_started` creates or replaces one pending section only after the
  server has authorized its current-run Citation metadata. `text_delta` appends
  only to the matching open section; duplicate or out-of-order events are
  rejected by the stream client.
- `section_completed` marks the section temporary state complete. A final
  `completed.response` is authoritative and replaces the pending projection,
  including Citation metadata and server formatting.
- `section_aborted`, cancellation, timeout, disconnect, or failed terminal
  state removes the unfinished section and never promotes its partial text to
  history.

## Scenario: Hand a terminal stream projection to durable transcript state

### 1. Scope / Trigger

Apply when changing stream terminal handling, transcript refetch/retry,
new-conversation reset, or conversation-list invalidation.

### 2. Signatures

```typescript
hasDurablePendingTurn(
  transcript: ConversationTurns | undefined,
  question: string | null,
  answer: string,
  baselineTurnCount: number,
): boolean
```

### 3. Contracts

- `completed.response` stays rendered until transcript query data contains a
  new durable turn after the send-time baseline.
- Durable answer matching is exact or differs only by the explicit appended
  `\n\n来源：\n` source-list shape. Arbitrary prefix matches are invalid.
- Repeat questions cannot match an older turn: inspect only turns added after
  the send-time baseline count.
- Refetch failure/non-convergence preserves the terminal projection and shows
  retry; it never creates a blank frame.
- Reset success seeds an empty transcript and safe sidebar placeholder, then
  refreshes the sidebar in the background. Sidebar latency cannot block input.

### 4. Validation & Error Matrix

| Condition | Required behavior |
| --- | --- |
| Transcript refetch pending | Keep one terminal pending answer visible. |
| Durable turn arrives | Clear pending projection; render one durable turn. |
| Old identical turn exists | Do not treat it as convergence. |
| New answer only shares prefix | Preserve pending projection and show retry. |
| Refetch fails | Preserve terminal answer and expose retry. |
| Sidebar refetch pending after reset | Composer remains usable. |

### 5. Good / Base / Bad Cases

- Good: await only exact transcript convergence for handoff; refresh list in
  background.
- Base: whole-answer non-SSE responses follow the same handoff.
- Bad: clear pending before refetch, match historical equal text, or await
  sidebar invalidation inside mutation success.

### 6. Tests Required

- Hold transcript refetch unresolved and assert the answer never disappears;
  release it and assert exactly one answer.
- Cover repeated question, ordinary prefix collision, explicit source-list
  append, sync failure/retry, Citation/step projections, and held sidebar reset.

### 7. Wrong vs Correct

#### Wrong

```typescript
setPendingAnswer("");
await queryClient.invalidateQueries({ queryKey: ["conversation", threadId] });
```

#### Correct

```typescript
setPendingAnswer(response.text);
await queryClient.refetchQueries({ queryKey: ["conversation", threadId], exact: true });
if (hasDurablePendingTurn(latest, question, response.text, baseline)) clearPending();
```

---

## Common Mistakes

- Storing the session token, CSRF token, or challenge browser secret in Web Storage.
- Reusing cached library data after logout.
- Deriving retry/archive permissions from UI assumptions.
- Persisting a transcript cursor after the user leaves the detail session.
- Creating a second global source of truth for lifecycle labels.
- Treating every 401 as an expired browser session and destroying a recoverable
  login flow.
