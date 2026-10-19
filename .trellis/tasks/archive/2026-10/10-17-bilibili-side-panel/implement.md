# Implement: Bilibili in the side panel

## Working location and rules

- Same worktree and branch as the side-panel task:
  `.claude/worktrees/side-panel-page-qa`, `feat/side-panel-page-qa`.
- A parallel agent is building 「收藏视频」 and progress (side-panel R8/R9). The
  file ownership is split as follows:
  - **Bilibili agent owns:**
    - `extension/src/page-capture.ts` and its test;
    - `extension/src/protocol.ts` and its test;
    - `extension/src/sidepanel/pageIdentity.ts` and its test;
    - `extension/manifest.json`, `extension/scripts/audit-package.mjs`,
      `extension/scripts/build-targets.mjs`;
    - `app/browser_capture.py`, `app/page_qa/**`, and the related `tests/**`.
  - **It must not edit:**
    - `extension/src/sidepanel/{usePageQa.ts,copy.ts,PageWorkspace.tsx,QaThread.tsx,SidePanel.tsx,workerApi.ts}`;
    - `extension/src/api/client.ts` and `extension/src/api/contracts.ts`;
    - `extension/src/chat/**`.

    If it needs a change in those files, such as new error copy or a stale
    check in `usePageQa`, it lists the exact change in its report and the main
    session integrates it.
  - Generated `openapi.json`/`schema.d.ts`: regenerate only if a schema changes,
    and say so.
- Test environment: dummy DB only, Node 22, tools via `node_modules/.bin`.
- No git commit.

## Checklist

1. **Contract.**
   - Add `bilibili` to `Platform` (server and extension).
   - Validation: host, id, `p`, cover.
   - `timestamp_url` keeps `p` when it is greater than 1.
   - Page Q&A accepts `bilibili`.
   - Tests: capture.v1 accept and reject cases; a page-Q&A bilibili request;
     timestamp URLs.
2. **Recognition.** Update `classifyPageUrl` and `identifyPage` (`p` in the
   key), with tests.
3. **Adapter.**
   - `captureBilibiliPage` as a self-contained MAIN-world function that reads
     page state, then `wbi/v2`, then the subtitle JSON.
   - Wire it into `captureActivePage`.
   - Fixture tests:
     - human, AI and no-subtitle cases;
     - stale state;
     - an untrusted subtitle host;
     - an oversized body;
     - the `runInNewContext` serialisation test;
     - no `auth_key` in the output.
4. **Player.** Add the bilibili branch to `controlPlayer` (bpx player API, then
   `<video>`/`<bwp-video>`), with tests.
5. **Permissions.** Add `https://www.bilibili.com/*` to the manifest (both
   targets) and update the audit expectations.
6. **Gates.**
   - Backend full suite: only the baseline failures.
   - OpenAPI `--check`.
   - Extension tsc ×2, eslint, vitest.
   - Production and local build plus audit. Build the local target last.
7. **Live smoke (user).** A logged-in Bilibili video with AI subtitles:
   - Q&A with citations, and seek;
   - a `?p=2` part;
   - 「收藏视频」 on part 1.

## 完成情况 (2026-10-19)

Status is checked against the code on `dev` (`c6d2044`, `966dfd4`) and the PR #32
release on `main`. Only step 7 is open.

| # | Step | Status | Evidence |
| --- | --- | --- | --- |
| 1 | Contract | done | `app/browser_capture.py` and `extension/src/protocol.ts` accept `bilibili`. `tests/test_bilibili_capture.py` covers accept and reject cases, page-Q&A request and route, and timestamp URLs that keep `p`. |
| 2 | Recognition | done | `extension/src/sidepanel/pageIdentity.test.ts`: 「identifies Bilibili videos by normalized id and part…」 and 「rejects Bilibili pages that are not a video」. |
| 3 | Adapter | done | `page-capture.test.ts` 「Bilibili caption adapter」 (21 tests): human, AI and no-subtitle cases; stale state; untrusted host; redirect outside `hdslb.com`; oversized and streamed bodies; secret-free capture (no `auth_key`); MV3 serialisation test. |
| 4 | Player | done | `page-capture.test.ts` 「Bilibili player control」 (7 tests): bpx API first, then `<video>`/`<bwp-video>`, top frame only. |
| 5 | Permissions | done | `extension/manifest.json` has `https://www.bilibili.com/*`; `audit-package.mjs` host list; `bilibiliPackaging.test.ts`. |
| 6 | Gates | done | The `Extension and API contract` workflow passed on main at `9daf552` (backend contract tests, Alembic heads, OpenAPI `--check`, typecheck, lint, vitest, production build and audit). PR #32 body: backend suite 1521 passed with 6 failures, the same as the `main` baseline; extension 514 vitest; local build and audit pass. The backend suite and the local target were not re-run in this closing pass. |
| 7 | Live smoke (user) | **not run** | Logged-in AI subtitles with Q&A and seek, `?p=2`, 「收藏视频」 on part 1. lane (2026-10-19): only the extension's subtitle summary was tested, and the remaining live checks moved into another task. |
