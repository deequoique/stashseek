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

## 完成情况 (2026-10-19)

Status is checked against the code on `dev` and the PR #32 release on `main`
(`7076283`, merged as `9daf552`). Steps 1–7 and 9 are done; step 8 is partly open.

| # | Step | Status | Evidence |
| --- | --- | --- | --- |
| 1 | R0 Bearer without the CSRF header | done | `tests/test_conversation_extension_transport.py` (Bearer without header OK; cookie without or with a wrong header rejected; correct header OK; header optional in the schema). |
| 2 | `transcript.py` and `bm25.py` | done | `tests/test_page_qa_transcript.py` (19 tests: 30 s / 4 s boundaries, CJK and Latin tokens, playback window, cap, determinism, `start_sec` chapters). |
| 3 | `service.py` | done | `tests/test_page_qa_service.py` (23 tests) and `tests/test_page_qa_guard.py`: marker split across chunks, more than 8 markers, idempotent `message_id`, no cue text in rows or `caplog`. |
| 4 | Route and wiring | done | `tests/test_page_qa_api.py` (24 tests): oversized body, unknown field, capture Bearer, bad Origin, unsupported platform, rate limit, flag off, cookie with CSRF. OpenAPI artifacts exported and checked by `pnpm check:api` in CI. |
| 5 | Worker and page functions | done | `page-capture.test.ts` and `worker.test.ts`: `READ_PAGE`, `PLAYBACK`, `SEEK` (YouTube, Kaltura frames), `SAVE_CAPTURE`, YouTube description and chapters, `pot` read. |
| 6 | Side panel | done | Manifest, audit and `sidepanel/manifest.test.ts`; `SidePanel.test.tsx` (login, page states, Q&A, citations); `ChatPage.test.tsx` read-only page threads; `SidePanelCollect.test.tsx`, `steps.test.ts`, `eta.test.ts`, `itemPoller.test.ts` for R8/R9. Version 0.2.0 kept in step (`manifest.test.ts`). |
| 7 | Full gates | done | The `Extension and API contract` workflow passed on main at `9daf552`. PR #32 body: backend suite 1521 passed with 6 failures equal to the `main` baseline; extension 514 vitest; local build and audit pass. The backend suite and the local target were not re-run in this closing pass. `trellis-check` on the full diff is not recorded. |
| 8 | Local smoke | **partly not run** | The PR #32 body lists a smoke (YouTube and Bilibili Q&A, 「收藏视频」 progress, citation seek, first delta ≈1–2 s), as a self-report that was not independently confirmed. Not run or not recorded: SPA navigation, a >2 h video, a caption-less video, save-then-repeat, the log grep, and the NTULearn/Kaltura part that was assigned to the user. lane (2026-10-19): only the extension's subtitle summary was tested, and the remaining live checks moved into another task. |
| 9 | Phase 3 | done | Specs `browser-companion-capture.md`, `web-browser-runtime.md` and `logging-guidelines.md` were updated in `7076283`. The feature commits are on `dev` and reached `main` through PR #32. |
