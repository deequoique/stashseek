# Serve shared UI as web companion with extension download entry

Parent: `10-15-extension-page-qa`. Builds on commit `c9a4c0e`.

**Ordering:** W1–W3 and W5 can proceed in parallel with
`10-15-side-panel-page-qa`. W4's zip must be produced from the build that
contains the side panel (version 0.2.0), so W4 runs last.

## Goal

Users can manage their library from a normal web page at the production origin:

- browse and search;
- collections;
- delete and restore;
- conversation history, including read-only page-Q&A threads;
- account and devices.

The web page is the same React code as the extension workspace, built as a
second target. It offers a download of the current extension build. The
extension stays the primary surface; the web is the companion (user decision,
2026-10-15).

## Confirmed facts

- Production `main` still serves the old `web/` SPA plus committed zips in
  `web/public/assets/`.
- `c9a4c0e` deletes `web/`, moves the UI into `extension/src`, and makes the
  server API-only:
  - `app/api/runtime.py:165-173` passes `static_dir=None`;
  - the deploy script no longer builds web;
  - the systemd unit dropped `WEB_PUBLIC_ORIGIN`, `WEB_SERVE_STATIC`,
    `WEB_STATIC_DIR`, `WEB_COOKIE_SECURE`, and `WEB_FORWARDED_ALLOW_IPS`.
- `_mount_spa` (`app/api/app.py:758-771`) still exists. It needs
  `<dir>/index.html` and serves `index.html` for every non-API path, including
  root files such as `/favicon.svg`.
- `extension/src/api/client.ts` uses cookie + CSRF for **data** calls on an
  http(s) origin (`:73-115`). Its **auth** calls are extension-only
  (`/api/v1/extension/auth/*`, `:150-202`), and the middleware rejects those
  from a web origin with `extension_origin_invalid`. The cookie routes
  `/api/v1/auth/{challenges,verify,session}` still exist
  (`app/api/email_auth_routes.py:125-324`).
- No script produces the extension zip. The download constant
  (`extension/src/app/browserCompanion.ts:1-2`) is a root-relative `/assets/…`
  URL, which inside the extension resolves to a non-existent
  `chrome-extension://` path.

## Requirements

- **W1 Web build target.** It emits a standalone SPA (`index.html` + `assets/`)
  into its own output directory. It must not contain `manifest.json`,
  `worker.js`, the side panel, or any extension-only page. The extension build
  outputs and audits are unchanged by this target.
- **W2 Serving.**
  - When `WEB_SERVE_STATIC` is true, the API server serves the web build at the
    production origin, with the API under `/api`.
  - Root static files (favicon) are served as files, not the SPA index.
  - The deploy script builds the web target.
  - The systemd unit and docs restore `WEB_PUBLIC_ORIGIN`, the cookie, and the
    forwarded settings. This also resolves the `WEB_PUBLIC_ORIGIN` deploy risk
    noted in `c9a4c0e`.
  - Caddy is unchanged.
- **W3 Web mode auth and affordances.**
  - On an http(s) origin, the client uses the cookie email routes (challenge,
    verify, session, logout with CSRF).
  - Extension-only affordances are hidden on the web: capture, side panel,
    device pairing initiation, and "open workspace".
  - Page-Q&A threads render read-only.
- **W4 Download entry.**
  - A repo script builds the production extension and zips `dist/`
    deterministically. The file name is
    `stashseek-chat-browser-companion-production-<version>.zip`.
  - The web build includes that zip under its assets. Version, file name,
    `manifest.json` version, and the download URL agree, from one source of
    truth.
  - Stale zips are not served.
  - Inside the extension, the download links point to the absolute production
    URL or are hidden.
- **W5 Privacy policy page.** A public page on the web (no login) describes:
  - what the extension reads (captions and public video metadata of pages the
    user acts on);
  - what is stored (the library, Q&A question/answer and short excerpts, the
    account email);
  - what is sent to the LLM provider (transcript text during Q&A);
  - retention and deletion;
  - contact.

  It is written so the Chrome Web Store data disclosures can match it.

## Acceptance criteria

- [ ] The web build produces `index.html` + `assets/` with no extension files.
      The extension production and local builds and audits still pass.
- [ ] Locally, the API serves the SPA and `/favicon.svg` (as SVG). Email login
      sets the cookie session. Library, collections, delete/restore,
      conversation history, and account pages work. Logout clears the session.
- [ ] The download button returns the 0.2.0 production zip with HTTP 200, and
      the zip's `manifest.json` version is 0.2.0. Inside the extension, the
      download link does not 404.
- [ ] The privacy policy page loads without login.
- [ ] Deploy script, systemd unit, and docs build and serve the web target.
      `tests/test_production_caddy_deployment.py`,
      `tests/test_linux_frontend_deployment.py`, and `tests/test_web_cli.py`
      reflect the layout.
- [ ] CSP (`app/api/app.py:464-470`) holds for the web build, with no inline
      script or style violations in the browser console.

## Out of scope

- Reviving `web/`
- A separate web design
- Continuing page threads from the web
- Chrome Web Store submission itself (listing, dashboard key / extension ID
  switch, trader declaration)
