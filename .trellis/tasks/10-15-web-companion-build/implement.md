# Implement: web companion build

## Working location

- Use a dedicated worktree from `dev` at or after `c9a4c0e`, on branch
  `feat/web-companion-build`.
  - The `dev` checkout carries unrelated uncommitted work.
  - The ASR session has uncommitted edits in `app/api/runtime.py`,
    `app/config.py`, `deploy/scripts/notebook-agent-deploy`, and
    `deploy/systemd/*` there.
- Merge into `dev` when those files are clean there, or when the user decides.
  Never merge to `main` here.
- Code is written by `trellis-implement` agents with `model: sonnet`. The main
  session reviews, verifies, and commits.
- Test-environment rules: identical to `../10-15-side-panel-page-qa/implement.md`
  (dummy DB or the test branch only; Node 22; tools via `node_modules/.bin`).

## Ordering with the sibling task

- Steps 1–5 do not depend on the side panel.
- Step 6 (zip at 0.2.0) runs after `feat/side-panel-page-qa` is merged into this
  branch or into `dev`.

## Ordered checklist

1. **Runtime split and web auth**
   - Add `src/app/runtime.ts` (`isExtensionRuntime`); route the existing checks
     in `client.ts` through it.
   - Add the web auth transport (design §1); hide the extension-only
     affordances.
   - Tests:
     - client auth calls per runtime (stub `location.protocol`), including
       logout sending CSRF on the web;
     - affordances hidden on the web;
     - extension behavior unchanged.
2. **Web build target**
   - `index.html` script entry, `vite.config.web.ts`, `build:web`,
     `audit:web`, `scripts/check-version.mjs` wired into all builds, and
     `.gitignore` entries for `dist-web/` and `release/`.
   - Gates:
     - `build:web` → `audit:web` passes;
     - `build` + `audit` and `build:local` + `audit:local` still pass;
     - the `dist/` file list is unchanged versus before (diff the listings).
3. **Serving**
   - `runtime.py` `static_dir`; the `web_static_dir` default and comment;
     `.env.example`; `_mount_spa` root-file serving with containment.
   - Tests:
     - `tests/test_web_api_app.py` serves `index.html`, `/favicon.svg` as SVG,
       and `/assets/x`;
     - traversal (`/..%2f..`) → index or 404, never a file outside the build
       directory;
     - API 404 is unchanged;
     - `WEB_SERVE_STATIC=false` → no SPA.
4. **Deploy and docs**
   - Restore the deploy web build step (`extension`, `build:web`), the systemd
     env vars, and the docs listed in design §3.
   - Update `tests/test_linux_frontend_deployment.py`,
     `tests/test_production_caddy_deployment.py`, and `tests/test_web_cli.py`.
   - Reconcile with the ASR session's uncommitted deploy script edits at merge
     time; do not copy them into this branch.
5. **Privacy page**
   - Add `public/privacy.html` and its stylesheet, and the links from the
     showcase, login, and account pages.
   - Draft the Chrome Web Store "single purpose" and data-category text in
     `research/cws-disclosures.md` under the parent task, matching the page.
6. **Download entry** (after the side panel lands)
   - Add `scripts/package-zip.mjs` (a deterministic writer) and include the zip
     in `dist-web/assets/`.
   - Implement the `browserCompanion.ts` URL per runtime and target.
   - Tests:
     - deterministic output (build twice → same SHA-256);
     - the zip contains `manifest.json` version 0.2.0 and no `.map` files;
     - the URL per runtime.
7. **Full gates and smoke**
   - Run the backend suite with the dummy DB; the failure set must not grow.
   - Run every extension gate, including `build:web` / `audit:web`.
   - Run `trellis-check` on the full diff.
   - Local smoke: run the API with `WEB_SERVE_STATIC=true
     WEB_STATIC_DIR=extension/dist-web` on loopback HTTPS (`localhost:8443`, so
     the `__Host-` cookies work).
     - Email login, library, collections, delete/restore, and history,
       including a read-only page thread.
     - Logout.
     - The download zip returns 200 and loads unpacked in Chrome.
     - `/privacy.html` loads without login.
     - No CSP errors in the console.
8. **Phase 3**
   - Spec updates per design §5.
   - Commit only this task's files on the feature branch.
   - Merge into `dev` per "Working location".

## Rollback points

- Each step is its own commit.
- Operational rollback: `WEB_SERVE_STATIC=false`.
