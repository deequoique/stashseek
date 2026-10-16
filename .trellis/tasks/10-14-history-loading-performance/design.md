# Website and history performance design

## Evidence

- App eagerly imports all route components. Baseline production main JavaScript
  is 501.32 kB (154.63 kB gzip), including chat Markdown code on landing/login.
- Protected routes wait for Session, then ChatPage waits for capabilities,
  conversation list, selection effect and transcript in sequence.
- Successful login seeds a new private Session cache but no history preload.
- Read-only Session resolution rolls back before reading ORM objects. Rollback
  expires them and a reproduced synthetic query count is four SELECTs instead
  of the one joined query promised by the existing auth contract.
- Conversation list already uses a bounded tenant-page projection. Transcript
  retrieval still fetches the unused model_messages JSONB column for every turn.

## Approach

1. Build the immutable authorized Session projection before transaction end
   can expire ORM objects. Preserve all expiry, disable, revoke, tenant and
   cache-generation checks and bounded last-used writes.
2. Select only public transcript fields (or load_only with raiseload) after the
   existing tenant-owned thread lookup. Preserve DTO shape/order and completed
   filtering; no speculative index or migration.
3. Split route components with lazy dynamic imports and an accessible Suspense
   fallback so public routes do not download chat, account and library code.
4. Add a small shared query-options/preload owner for capabilities and history.
   Warm a newly authenticated private QueryClient in the background; never
   wait for history before login navigation. Preload history and the first
   transcript only after a verified session and enabled chat. Reuse the same
   query keys/options in ChatPage so preloading deduplicates normal reads.
5. Remove unnecessary sequential waits using already loaded public capabilities
   when activating a fresh private Session; copy only public capabilities,
   never cached private data across accounts. Start private history only once
   authenticated. Retain failure/disabled/empty-state honesty.
6. Use bounded in-memory freshness for history/transcripts, with existing explicit
   invalidation after send/reset/delete. Do not persist private data or tokens.

## Limits and safety

No production credentials, server deployment or real database timing are
available. Validate bundle bytes, query counts, controlled delayed promises,
browser behavior, and existing regression tests. No background polling or
database keepalive is introduced. Cold remote database startup and actual
network latency remain external to these changes.
