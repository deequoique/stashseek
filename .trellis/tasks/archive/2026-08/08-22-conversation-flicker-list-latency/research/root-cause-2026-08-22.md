# Conversation flicker and list latency root cause

## Outcome

Both reported symptoms are confirmed by code and local timing evidence. They
share one trigger—conversation-query invalidation—but have two distinct root
causes:

1. the client clears its authoritative-looking pending answer before the
   persisted transcript refetch has supplied the replacement;
2. the list endpoint combines a high remote-database request baseline with an
   N+1 latest-turn query, and the new-conversation mutation awaits that slow
   refetch before leaving its pending state.

## Answer flicker

`web/src/chat/ChatPage.tsx:278-284` correctly replaces streamed deltas with the
terminal `completed.response`. That final response remains only in the
component's pending projection.

When the stream promise resolves, `web/src/chat/ChatPage.tsx:308-321` runs the
mutation success callback. It immediately clears `pendingQuestion`,
`pendingAnswer`, Citations, sections, steps, and plan. Only after clearing does
it invalidate and await both the history and selected transcript queries.

The rendered persisted turns come from `transcript.data` while the streamed
turn is rendered only when `pendingQuestion` is truthy. Therefore the sequence
is:

```text
terminal SSE answer visible
-> success callback clears pending projection
-> old transcript cache still lacks the new turn
-> blank render / answer disappears
-> transcript refetch completes
-> persisted turn appears
```

The existing streaming tests prove that terminal text is paintable, but they
do not hold the transcript refetch open and assert that the terminal answer
stays visible until the persisted replacement arrives.

## New-conversation blocking

`web/src/chat/ChatPage.tsx:195-202` sets the new thread/conversation IDs, then
returns a promise from `await queryClient.invalidateQueries(["conversations"])`.
TanStack Query 5.101.4 awaits mutation `onSuccess` callbacks before dispatching
the mutation's success state (`web/node_modules/.pnpm/@tanstack+query-core@5.101.4/node_modules/@tanstack/query-core/src/mutation.ts:235-251`).

As a result, `newConversation.isPending` stays true for the full list refetch.
That state:

- marks the conversation workspace busy;
- renders “正在准备检索工作区…”;
- disables the new-conversation button;
- disables the textarea and submit button.

The comment at `ChatPage.tsx:197-200` says the generated conversation ID should
allow input before the sidebar refresh completes, but awaiting invalidation
negates that intended behavior.

## List endpoint cost

`app/api/conversation_routes.py:677-728` performs:

1. one query for up to `limit + 1` conversation threads;
2. one additional latest-completed-turn query for every returned thread
   (`conversation_routes.py:713-724`).

For the UI's fixed `limit=30`, this is an N+1 path: one thread query plus up to
30 sequential latest-turn queries. The latest-turn index is useful, but it
does not remove network round trips.

Before the route runs, email session resolution also performs separate session,
user, and channel-identity reads plus a `last_used_at` update/commit
(`app/web_auth.py:369-398`). This matters because the configured development
database is a remote pooled Neon endpoint.

Protected write requests amplify this further. The app-level CSRF boundary
first calls `web_auth.resolve_session()` and `validate_csrf()`
(`app/api/app.py:567-593`); after the middleware succeeds, the route's
`authenticated_session` dependency resolves the same session again
(`app/api/app.py:484-502`). A new-conversation POST therefore repeats the
session/user/identity reads and `last_used_at` write, in addition to the CSRF
session read and the conversation-reset transaction. There is currently no
request-scoped reuse of the already-resolved session.

Local HTTPS timing with a fresh fake-email account and zero conversations:

```text
GET /api/v1/conversations?limit=30
first request:  2.978 s
second request: 3.131 s
items: 0
```

Since the zero-item path executes no per-thread latest-turn reads, roughly
three seconds is already baseline authentication plus the thread query. A real
account then adds up to 30 sequential database round trips, explaining the
reported “超级久” behavior.

## Fix boundaries suggested by evidence

- Keep the terminal pending answer mounted until the selected transcript cache
  contains the persisted replacement; do not introduce a second persisted
  history source.
- Make sidebar refresh non-blocking for new-conversation readiness and update
  the list optimistically or in the background.
- Replace per-thread latest-turn reads with one set-based query that preserves
  tenant filters, ordering, pagination, completed-only semantics, title, and
  preview behavior.
- Decide explicitly whether this task also optimizes the shared email-session
  resolution baseline. Without that work, an empty list can still take about
  three seconds on the current remote development topology, even after the N+1
  query is removed.
- If authentication is included, preserve hash-only tokens, disabled-user and
  disabled-identity checks, expiry/revocation, exact-Origin enforcement and
  double-submit CSRF while removing duplicate/request-chatty database work.

## Browser limitation

The Codex in-app browser still cannot access `https://localhost:8443` because
its admin-enforced local-access policy cannot be verified. No security bypass
was attempted. The diagnosis uses application code, the real local HTTPS/API
stack, Mailpit-backed fake login, and measured request timings.
