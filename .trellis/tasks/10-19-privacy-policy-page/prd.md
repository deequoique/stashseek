# Public privacy policy page

Parent: `10-19-chrome-web-store-listing`.

## Goal

The production origin serves a public privacy policy at a stable URL that
needs no login. It describes accurately what the StashSeek Chat extension and
service collect, how the data is used, who processes it, how long it is kept,
and how to delete it, so the store's Privacy-practices disclosures can link
to it and match it.

## Data the code actually handles (repo evidence, 2026-10-19)

- **Account.**
  - The `app_user` email.
  - Login challenges (`web_auth_challenge`, `web_login_challenge`) and
    sessions (`web_session`). Extension sessions live in memory only.
  - Linked chat identities (`channel_identity`, `channel_link_token`).
  - MCP grants (`mcp_access_grant`).
- **Extension device pairing.** `browser_companion_pairing` and
  `browser_companion_grant` (device label, client version, hashed tokens).
- **Library.** Saved items (`content_item`), which hold:
  - metadata, `why_saved`, collections and tags;
  - transcripts in object storage, chunks and vectors (`segment`);
  - capture records (`browser_capture`), ingest jobs (`ingest_dispatch`) and
    media assets.
- **Conversations.**
  - `conversation_thread` and `conversation_turn`: the question, the answer
    and cited excerpts.
  - Page Q&A stores only the question, the answer, and cited excerpts of 180
    characters or fewer. Captions are never stored.
- **What the extension reads.** On YouTube and Bilibili pages, only when the
  user opens the panel or acts:
  - captions or subtitles and public video metadata;
  - the playback position.

  It never reads or sends cookies, passwords, audio or video. Signed URLs stay
  in the page.
- **Third-party processors.** The production env is not in the repo, so this
  list must be confirmed before publishing:
  - LLM: DeepSeek (`api.deepseek.com`), which receives the question and
    transcript excerpts during a request.
  - Embeddings: Zhipu (`open.bigmodel.cn`).
  - ASR when enabled: Alibaba Cloud DashScope / Fun-ASR, with OpenAI
    `whisper-1` as fallback; it receives temporary audio of the saved video.
  - Database: Neon Postgres.
  - Hosting: OVH VPS.
  - Email: SMTP provider for login codes.
- **Retention.**
  - Deleted items stay in the recycle bin for `TRASH_RETENTION_DAYS`
    (default 30) and are then purged.
  - Login challenge and session retention is bounded by config.
  - Production logs are content-free (`logging-guidelines.md`).

## Requirements

- **P1.** A public, script-free page at `https://stashseek.deequoique.tech/privacy`.
  - It is served by the API server itself, so it works on both `dev`
    (API-only) and `main` (serves `web/dist`).
  - It is registered before any SPA catch-all.
  - It passes the existing CSP: an external stylesheet only, no inline style
    or script.
- **P2.** Bilingual (中文 / English) content with these sections:
  - who we are and contact;
  - what we collect;
  - what the extension reads and when;
  - how data is used;
  - processors and transfers;
  - retention and deletion;
  - no sale and no ads, plus the Limited Use statement;
  - children;
  - changes;
  - effective date.
- **P3.** The content matches the code facts above. A test pins the key
  claims that the code guarantees: no cookies or audio/video uploaded, cues
  not stored, the 180-char excerpt limit, and the recycle-bin days taken from
  config rather than hard-coded.
- **P4.** Links to the page from the extension's side-panel footer and login
  screen, and from the web showcase/login pages that exist on `main`. This is
  a follow-up if the web side is not touched in this task.
- **P5.** It lands on `dev` first, then is released to `main` (dev stays
  ahead of main).

## Acceptance criteria

- [ ] `GET /privacy` returns 200 `text/html` without login. It loads its CSS
      with no CSP violations, and works with `WEB_SERVE_STATIC` true or false.
- [ ] Both languages are present and the effective date is shown.
- [ ] The tests pass: the route, the CSP headers, and the pinned claims.
- [ ] The production URL is live after release.

## Decisions (user, 2026-10-19)

- **Operator.** An individual developer, shown as 「StashSeek Chat 开发者 /
  StashSeek Chat developer」 with no company name, plus a dedicated contact
  email.
  - The address is `deequoique@gmail.com` (user, 2026-10-19).
  - It is rendered from config (`PRIVACY_CONTACT_EMAIL`), so changing it needs
    no code change.
  - The page must not render without it in production. In dev it shows a clear
    placeholder.
- **Processors.** Named **by category only**: 大模型问答服务商, 文本向量服务商,
  语音转写服务商 (only when transcription is used), 云数据库, 服务器托管,
  邮件发送服务商. Each category says what data it receives. Providers are not
  named.

## Technical notes

- **Route and files.**
  - A FastAPI route `GET /privacy` serves `app/static/privacy/index.html`.
  - `GET /privacy/privacy.css` serves the stylesheet. External CSS satisfies
    `style-src 'self'`.
  - The routes are registered in `create_app` before `_mount_spa`, so they
    win over the SPA catch-all on `main`.
  - On `dev`, which is API-only, nothing else is mounted.
- **Config-driven values.**
  - `PRIVACY_CONTACT_EMAIL` defaults to `deequoique@gmail.com`.
  - The recycle-bin days come from `TRASH_RETENTION_DAYS`.
  - Both are rendered via simple, escaped string substitution. There are no
    templates with logic and no user input.
- **Release path.** The change lands on `dev` first, then reaches `main`
  through a release branch, the same as PR #32.
