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
