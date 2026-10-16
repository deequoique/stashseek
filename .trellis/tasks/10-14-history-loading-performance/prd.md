# Speed up website startup and authenticated conversation history

## Goal

Reduce initial frontend payload, remove avoidable login/history request waterfalls and repeated auth queries, and slim conversation transcript reads while preserving tenant security.

## Requirements

- Speed up opening the website and loading conversation history after login.
- Reduce unnecessary initial route code and serialized request waits.
- Reduce repeated authoritative Session queries and unused transcript payload.
- Keep existing authentication, tenant isolation, cache rotation, citations,
  streaming, history consistency, errors and accessibility intact.
- Do not alter production configuration, data, database schema or deployment.
- Provide measurable local evidence and distinguish it from real production timing.

## Acceptance Criteria

- [x] Public entry no longer eagerly downloads all protected route code.
- [x] Login navigation does not wait for history; authenticated history preloads
  and subsequent route reads deduplicate using the active private cache.
- [x] Read-only/cached-outage auth paths perform one joined authorization SELECT.
- [x] Transcript SQL omits model_messages without extra lazy-load queries.
- [x] Relevant Python and complete frontend gates pass; pre-existing failures are isolated.
- [x] Browser smoke and before/after bundle/query evidence are recorded.

## Notes

- User already selected Trellis tracking in this chat and now explicitly requested
  performance fixes. Email work was superseded before any code changes.
- Trellis helper skills are absent; use documented commands and scoped specs.
