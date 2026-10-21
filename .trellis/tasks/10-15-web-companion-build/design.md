# Design: web companion build

Evidence: `../10-15-extension-page-qa/research/integration-points.md` §6.
The pre-migration web reference is `git show c9a4c0e^:web/...`.

## 1. One codebase, two runtimes

- Runtime detection stays as it is today: `location.protocol ===
  "chrome-extension:"`, wrapped in a single exported `isExtensionRuntime()` in
  `src/app/runtime.ts`. Every existing ad-hoc check in `client.ts` uses it.
- Auth transport by runtime:

  | Call | Extension (unchanged) | Web (restored from `c9a4c0e^:web/src/api/client.ts`) |
  | --- | --- | --- |
  | challenge | `POST /api/v1/extension/auth/challenge` | `POST /api/v1/auth/challenges {email}` |
  | verify | `POST /api/v1/extension/auth/verify` | `POST /api/v1/auth/verify {email, code}` (sets `__Host-kb_session` / `__Host-kb_csrf`) |
  | session | `GET /api/v1/extension/auth/session` | `GET /api/v1/auth/session` |
  | logout | `POST /api/v1/extension/auth/logout` | `DELETE /api/v1/auth/session` + `X-CSRF-Token` |

  The response shapes are normalized to the one `SessionInfo` type the UI
  already consumes. Data calls are unchanged.
- Affordances hidden when `!isExtensionRuntime()`:
  - "保存当前视频" / capture;
  - pairing initiation;
  - "打开工作区";
  - side panel hints.

  The account → browser companion page on the web shows the device list,
  revoke, and the download button.

## 2. Build target

- `extension/index.html` becomes the web entry: add
  `<script type="module" src="/src/main.tsx">` and keep the favicon link that
  `indexHtml.test.ts` checks.
- `vite.config.web.ts`: input `index.html`, `outDir: "dist-web"`,
  `emptyOutDir: true`, `publicDir: "public"`. No `manifest.json`, `worker.js`,
  `sidepanel.html`, or `workspace.html` in the output. Router: `HashRouter`, the
  same as the extension (it works under the SPA catch-all).
- Scripts in `extension/package.json`:
  - `build:web` = version check → `vite build --config vite.config.web.ts` →
    `package:zip` → copy the zip into `dist-web/assets/` → `audit:web`.
  - `audit:web` asserts `index.html` exists. It also asserts that none of these
    exist: `manifest.json`, `worker.js`, `page-capture.js`, `.map` files, or
    zips other than the current version.
- `scripts/package-zip.mjs`:
  1. Runs the production `build` + `audit`.
  2. Writes `release/stashseek-chat-browser-companion-production-<version>.zip`
     from `dist/`.
  - The zip uses a tiny deterministic writer: sorted entries, fixed mtime,
    deflate via `node:zlib`, and `zlib.crc32` (Node ≥22). There is no `zip` CLI
    dependency on the deploy host.
  - `release/` is git-ignored.
- `scripts/check-version.mjs` checks that one version source of truth
  (`package.json`) matches `manifest.json`, `src/protocol.ts` `client_version`,
  and `src/app/browserCompanion.ts`. Any mismatch fails `build`, `build:local`,
  and `build:web`.
- Download URL (`browserCompanion.ts`):
  - **Web:** root-relative `/assets/<zip>`.
  - **Extension:** `${apiOrigin}/assets/<zip>` for the production target. It is
    hidden for the local target, because the loopback API does not serve the
    web.

## 3. Serving

- `app/api/runtime.py`: `static_dir = settings.web_static_dir if
  settings.web_serve_static else None`. This restores the pre-migration
  behavior, and `mount_static` is honored again.
- `web_static_dir` defaults to `"extension/dist-web"`. Fix the config comment.
  `WEB_STATIC_DIR` / `WEB_SERVE_STATIC` go back into `.env.example` with the
  real meaning.
- `_mount_spa`:
  - Before the catch-all, serve an existing regular file directly from the root
    of the build directory (e.g. `favicon.svg`, `privacy.html`), with a
    resolved-path containment check against traversal.
  - Then fall back to `index.html` for non-API paths.
  - `/assets` stays a `StaticFiles` mount (immutable cache headers already
    apply).
- CSP stays as is. The web build must not use inline scripts or styles, and
  vite output already complies. Verify in the browser console.
- Deploy (`deploy/scripts/notebook-agent-deploy`): restore the pnpm install and
  build step, now `--chdir="$release_dir/extension"` running `corepack pnpm
  install --frozen-lockfile` and `corepack pnpm build:web`. The host Node/pnpm
  versions are the ones the old web build used.
  - Note: the ASR session has uncommitted edits in this script on `dev`; see
    `implement.md`.
- systemd `notebook-agent.service`: restore `WEB_PUBLIC_ORIGIN`,
  `WEB_SERVE_STATIC=true`, `WEB_STATIC_DIR=extension/dist-web`,
  `WEB_COOKIE_SECURE`, and `WEB_FORWARDED_ALLOW_IPS` from `c9a4c0e^`.
- Docs: `docs/how-to/deploy-production.md`,
  `docs/operations/production/ovh-caddy.md`, `docs/reference/configuration.md`,
  `docs/how-to/use-browser-companion.md` (download from the web page),
  `README*`.

## 4. Privacy policy

- `extension/public/privacy.html`: a static, script-free, bilingual (zh/en)
  page using a stylesheet under `public/` (CSP allows `'self'` styles). It is
  served at `/privacy.html` and linked from the showcase footer, the login page,
  and the account page.
- Content follows PRD W5 and must match the data the code actually handles:
  - the email;
  - library items and captions saved by the user;
  - page-Q&A questions, answers, and ≤180-char excerpts;
  - transcript text sent to the LLM provider for the duration of a request;
  - no audio or video;
  - no sale or transfer;
  - deletion via the recycle bin purge and account deletion contact.

  The store "single purpose" and data categories are drafted in the same pass
  so they stay consistent: personally identifiable info (email),
  authentication info, website content (captions).

## 5. Compatibility and rollback

- Rollback: set `WEB_SERVE_STATIC=false`, which makes the server API-only again
  (the `c9a4c0e` behavior). The extension is unaffected.
- The 0.1.3 zip links on the old production page disappear on the next deploy.
  The new page serves 0.2.0 only.
- Specs to update in Phase 3:
  - `web-browser-runtime.md`: web target, auth transport split, root files.
  - `deployment-lifecycle.md`: the web build in deploy.
  - Frontend `directory-structure.md`: the two runtimes and `isExtensionRuntime`.
