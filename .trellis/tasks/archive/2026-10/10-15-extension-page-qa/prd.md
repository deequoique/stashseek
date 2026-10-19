# Extension page Q&A and web companion

## Goal

Let a user ask questions about the video they are watching, from a browser side
panel, within seconds and without first saving the video to their library. Keep
the web UI as a secondary surface for managing the library, built from the same
codebase as the extension, with a download entry for the current extension build.

The extension is the primary, incremental surface; the web is the companion.

## Background and decisions (2026-10-07 → 2026-10-15 discussion)

- **Compliance boundary:** everything is user-initiated, runs in the user's own
  browser, and lands in that user's own tenant. Rejected: server-side crawling of
  trending videos, and any cross-tenant shared transcript/embedding cache. Sharing
  user A's captured transcript with user B is redistribution, and client-submitted
  cues can poison other tenants. capture.v1 only proves integrity, not
  authenticity.
- **Page Q&A does not ingest.** Saving a video means persist → chunk → per-cue
  embedding → index → queue, and that pipeline is the slow part. Q&A needs only
  the caption text, which the extension already reads in-browser.
- **Web stays** (user decision, 2026-10-15). The pending `dev` migration moved
  the React app from `web/` into `extension/src`, and its API client already
  supports both modes: a Bearer token inside the extension, and cookie + CSRF on
  an http(s) origin (`extension/src/api/client.ts:82-95`). So the web is a second
  build target of that shared code, not a revived `web/` copy.
- **Prerequisite, done:** the "extension becomes the main UI" migration was
  committed on `dev` as `c9a4c0e`, with only its own paths and hunks. It was
  verified with the backend suite on a dummy DB (same 5 pre-existing failures as
  before) and with the extension typecheck, lint, 236 tests, build, and audit.
  Both child tasks build on it.
- **Side panel replaces the popup** (user decision, 2026-10-15).
- **Page-Q&A history:** the question, the answer, and ≤180-char cited excerpts
  are persisted; cues never are (user decision, 2026-10-15).
- **Working location:** each child is implemented in its own worktree from
  `dev`. Another session is concurrently editing `app/api/app.py`,
  `app/api/runtime.py`, `app/config.py`, and the deploy script in the `dev`
  checkout.

## Child tasks

| Child | Deliverable |
| --- | --- |
| `10-15-side-panel-page-qa` | Side panel and instant, ingestion-free Q&A over the current video (extension + API) |
| `10-15-web-companion-build` | Web SPA build of the shared UI served by the API server, with an updated extension download entry |

Ordering: `web-companion-build` must ship the extension zip produced after
`side-panel-page-qa` lands, so its final packaging step runs last.

## Cross-child acceptance criteria

Checked 2026-10-19. A box is ticked only where code or tests prove it.

- [x] The production extension zip that the web download entry links is the
      build containing the side panel. Version numbers in `extension/package.json`,
      `manifest.json`, and the download URL match.
  - `extension/manifest.json` and `extension/package.json` are `0.2.0`;
    `extension/src/app/browserCompanion.ts` links
    `stashseek-chat-browser-companion-production-0.2.0.zip`;
    `sidepanel/manifest.test.ts` 「keeps the manifest, package and client
    versions in step」 asserts all of it.
  - `main` carries `web/public/assets/stashseek-chat-browser-companion-production-0.2.0.zip`
    (release `7076283`, PR #32).
  - Note: on `main` the download entry belongs to the old `web/` SPA. Serving the
    shared UI as the web companion is child `10-15-web-companion-build`, which has
    not started.
- [ ] No cue text from page Q&A appears in any database row, object-store key,
      broker message, or log line. Verified by test and by a log grep during
      smoke.
  - The test half holds: `tests/test_page_qa_api.py`
    `test_end_to_end_turn_is_persisted_in_history_and_leaks_no_cue_text`;
    `tests/test_page_qa_service.py`
    `test_cue_text_never_reaches_logs_or_any_persisted_column_except_short_excerpts`.
    `app/page_qa/` has no ingest, storage or queue dependency.
  - **The log grep during a live smoke was not recorded.**
- [x] Extension production and local builds each pass their per-target permission
      audit after `sidePanel` is added.
  - The `Extension and API contract` workflow passed on main at `9daf552`
    (production build and audit).
  - The local target rests on the PR #32 body, not on a CI run.
- [x] Specs updated: `browser-companion-capture.md` drops the stale "popup cannot
      read the library or initiate chat" claim and documents the page-Q&A contract.
  - `browser-companion-capture.md` now describes the side panel and states
    there is no `action.default_popup`. The page-Q&A route contract is in
    `web-browser-runtime.md`; the transcript rule is in `logging-guidelines.md`
    (all in `7076283`).

## Status of the children (2026-10-19)

| Child | Status |
| --- | --- |
| `10-15-side-panel-page-qa` | Closed. Shipped in PR #32; live checks not run. |
| `10-17-bilibili-side-panel` | Closed. Shipped in PR #32; live checks not run. |
| `10-15-web-companion-build` | Not started (planning). Detached on 2026-10-19 when the parent was archived at lane's request; it continues as a standalone task. |

lane (2026-10-19) said they only tested the extension's subtitle summary, and
that the remaining live checks moved into another task. They did not name that
task.

## Out of scope

- Batch import of every video on a list page (a later task; must be user-confirmed,
  capped, and rate-limited).
- Cross-tenant content cache; server-side crawling.
- In-browser ASR ("transcribe while watching") and full-video map-reduce summaries.
  Both are phase 2, pending `needs_asr` rate and real question data.
- Chrome Web Store submission itself (listing, dashboard key/ID switch, trader
  declaration). It is a follow-up task. Known constraints:
  - The store assigns its own item ID. The self-generated manifest `key` (ID
    `omogodipchfidpikpeebgmlplpkjnpfm`) will not match it. After the first
    draft upload:
    1. Copy the dashboard public key into `manifest.json`.
    2. Add the new `chrome-extension://<store-id>` to
       `BROWSER_COMPANION_ALLOWED_ORIGINS`.
    3. Keep the old ID during the transition for zip installs.
  - Upload only the production build; a manifest carries exactly one API origin.
  - The privacy policy (web W5) must match the Privacy-practices disclosures.

## 收尾记录 (2026-10-19)

- Archived at lane's request, with two of three children closed (see the table
  above). The cross-child criterion about the log grep during a live smoke was
  not met, and the live checks of both closed children were moved into another
  task that lane did not name.
- `10-15-web-companion-build` is not part of this closure. Archiving cleared its
  `parent` field; its own PRD still says "Parent: `10-15-extension-page-qa`",
  which now points at the archive.
