# Implement: side panel instant Q&A

## Working location

- Implement in a dedicated worktree branched from `dev` at or after `c9a4c0e`,
  on branch `feat/side-panel-page-qa`.
  - The `dev` checkout carries unrelated uncommitted work.
  - Another session is actively editing `app/api/app.py`, `app/api/runtime.py`,
    and `app/config.py` there, and this task must touch all three.
- Merge back into `dev` only once those files have no uncommitted changes in the
  `dev` checkout, or when the user decides how to reconcile. Never merge to
  `main` from this task.
- Code is written by `trellis-implement` agents with `model: sonnet`. The main
  session reviews, verifies, and commits.

## Test-environment rules (pass to every sub-agent)

- **Postgres.** Never run pytest against `.env`'s production `DATABASE_URL`.
  - Unit and API tests: use the dummy environment
    `DATABASE_URL=postgresql+psycopg://nobody:x@127.0.0.1:1/none
    REDIS_URL=redis://127.0.0.1:1/0 STASHSEEK_ENV=development
    MINIO_ENDPOINT_URL=http://127.0.0.1:1`, run from a directory without
    `.env`.
  - Postgres-backed tests: export `DATABASE_URL` from `.env.neon-test` and
    assert the host is `ep-falling-fog-azrht8ir-pooler…` first.
- **Extension.** Use Node 22 (`source ~/.nvm/nvm.sh && nvm use 22`) and call the
  tools from `extension/node_modules/.bin` (`tsc`, `eslint`, `vitest`, `vite`),
  then run the `scripts/*.mjs` steps. The corepack `pnpm` shim fails under this
  setup. In a fresh worktree, symlink `extension/node_modules` from the main
  checkout if no dependency changes.

## Ordered checklist

1. **R0: Bearer without the CSRF header on conversation routes**
   - Make `CsrfHeader` optional in `app/api/conversation_routes.py`; enforcement
     stays in the middleware.
   - Add tests for the four Bearer/cookie × header cases (design §2).
   - Regenerate `extension/src/api/openapi.json` (`node workspace/openapi.mjs
     generate`). Check that the diff is only the header's optionality.
   - Gate: `tests/test_web_api_app.py`, `tests/test_extension_auth.py`, and the
     conversation route tests pass; `check:api` passes.
2. **Server core: `app/page_qa/transcript.py` and `bm25.py`**
   - Paragraph merging, token estimate, full/window selection, BM25.
   - Pure unit tests:
     - paragraph boundaries (30 s / 4 s gap);
     - CJK and Latin tokenization;
     - window inclusion around `playback_sec`;
     - the cap;
     - determinism;
     - chapters shaped `start_sec` (not `start_time`).
3. **Server service: `app/page_qa/service.py`**
   - Prompt build, history (≤4 prior turns), model call with the fake model used
     by the existing answer-pipeline tests, streaming marker guard, citation
     projection, no-captions mode, the cross-language rewrite in window mode,
     and the per-thread lock.
   - Persistence via `get_or_create_thread` / `save_completed_turn` with the
     `page_*` answer statuses.
   - Tests: marker split across chunks, unknown marker dropped, >8 markers,
     `validate_natural_answer` failure → `error` event, and idempotent
     `message_id`.
   - Sentinel test: no cue text in rows (except ≤180-char cited excerpts) and
     none in `caplog`.
4. **Route and wiring: `app/page_qa/routes.py`**
   - Mount the router when `PAGE_QA_ENABLED`; add settings
     (`PAGE_QA_ENABLED`, `PAGE_QA_MAX_REQUEST_BYTES`,
     `PAGE_QA_FULL_CONTEXT_TOKENS`, `PAGE_QA_WINDOW_CONTEXT_TOKENS`,
     `PAGE_QA_RATE_LIMIT`) with validation.
   - Exempt the path in `RequestBodyLimitMiddleware`; add the `page_qa`
     capability; add the `page_qa` boolean on the conversation list and detail
     responses.
   - Update `.env.example` and `docs/reference/configuration.md`.
   - Contract tests: oversized body, unknown field, capture Bearer 401, bad
     Origin 403, unsupported platform 422, rate limit 429, flag off → 404,
     cookie + CSRF OK.
   - Regenerate `openapi.json` and `schema.d.ts`.
5. **Extension worker and page functions**
   - `READ_PAGE`, `PLAYBACK`, `SEEK`, and `SAVE_CAPTURE`.
   - YouTube `description` and best-effort chapters.
   - `chrome.d.ts` typings.
   - Unit tests with the existing `executeScript` stubs (`page-capture.test.ts`
     pattern): playback and seek for YouTube and Kaltura frames; description and
     chapter extraction bounded; `SAVE_CAPTURE` key reuse.
6. **Side panel**
   - Manifest (`sidePanel`, `side_panel`, no popup); `audit-package.mjs`; the
     second vite input; delete the popup files and their copy step;
     `setPanelBehavior`.
   - `src/sidepanel/`: auth/connect/page states, tab tracking, Q&A view, and
     the shared SSE consumer extracted from `client.ts`.
   - Read-only `page_qa` threads in `ChatPage`.
   - Version bump to 0.2.0 everywhere listed in design §7.
   - Tests:
     - panel state machine;
     - video change → new thread;
     - citation click → `SEEK`;
     - shared SSE consumer, with the existing client stream tests still
       passing;
     - read-only page thread.
7. **Full gates**
   - Backend: run the whole suite with the dummy DB. The failure set must not
     grow beyond the known pre-existing failures; record the baseline first in
     the worktree.
   - Extension: typecheck, lint, vitest, `build` + `audit`, and `build:local` +
     `audit:local`.
   - Run `trellis-check` on the full diff.
8. **Local smoke** (main session; the user runs the NTULearn part)
   - Local API on `127.0.0.1:8000` with `PAGE_QA_ENABLED=true`; load the local
     build unpacked.
   - YouTube: a ≤20-min captioned video; record time to the first `text_delta`
     (target ≤5 s).
   - Click a citation → the player seeks.
   - SPA navigation → new thread.
   - A >2 h video → window mode answer for "刚才说的是什么".
   - A caption-less video → labelled answer.
   - Save → one capture reaching `ready`; repeat → existing result.
   - Grep the API logs for a cue phrase → no hit.
   - NTULearn/Kaltura (user): captioned lecture Q&A + seek.
9. **Phase 3**
   - Spec updates listed in design §8.
   - Commit only this task's files on the feature branch.
   - Merge into `dev` per "Working location".

## Rollback points

- Each checklist step is its own commit on the feature branch.
- Server rollback: `PAGE_QA_ENABLED=false`.
- R0 can stand alone, since it only relaxes a header for Bearer requests.
