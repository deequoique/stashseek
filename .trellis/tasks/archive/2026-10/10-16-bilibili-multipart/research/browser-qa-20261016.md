# Multipart browser QA — 2026-10-16

## Actual browser gate: blocked, not passed

The first CUA inventory returned no applications or browsers and reported:

> The Mac is locked and automatic unlock could not unlock it.

The documented independent fallback was attempted with
`cua.createBrowserTab("iab", "about:blank", { visible: true })`. It returned
`Browser is not available: iab`. No tab was created and the user's existing tab
was neither selected nor changed. No alternative UI automation was used.

Consequently there are **no browser screenshots or claims of native-dialog,
desktop/mobile geometry, Escape/focus, browser-console, or Web Storage QA** for
this run. These checks remain pending until CUA has an available browser after
the user unlocks the Mac.

## Reproducible fixture prepared and checked

- `artifacts/browser_qa_api.py` is a disposable, loopback-only API. Its data and
  operation replay cache exist only in RAM. It imports canonical Pydantic part
  request/response DTOs, `parse_video_input` and `PartRefSigner`; it does not use a
  database, broker, mail service, provider HTTP, or real user credentials.
- `artifacts/browser_qa_vite.mjs` starts the real frontend separately on 5181 and
  proxies API requests to the fixture on 8021. It does not replace the user's
  5173/8000 demo or modify its `save_enabled=false` behavior.
- All fixture videos are labelled `【测试数据】`. The fixture exposes 29 parts in
  pages of 20 and 9, one unavailable row, one legacy-consent row, deterministic
  partial statuses, and a first uncertain result whose identical-key replay can
  be confirmed. The source endpoint displays the expected provider destination
  as JSON; it intentionally performs no actual redirect or Bilibili request.
- Fixture authentication and admission are **not** production authentication or
  queue tests. This transport exists only to exercise UI interaction once a
  browser is available. Canonical backend route/security tests are separate.

Both temporary servers were started and an HTTP-only contract smoke passed:

```json
{
  "fixture_contract_smoke": "passed",
  "signed_part_refs": true,
  "preview_pages": [20, 9],
  "partial_statuses": ["already_exists", "quota_exceeded", "legacy_identity_unknown"],
  "uncertain_replay": [503, 200],
  "overlimit_http": 422,
  "batch_calls": 3,
  "unique_operations": 2,
  "source_expected_part": 6,
  "source_time": 185
}
```

The frontend entry HTML was reachable through the isolated Vite server. This
HTTP check is **not** real-browser rendering evidence. Both owned server sessions
were stopped afterwards (exit 130); no QA servers or tabs were left running.

## Pending real-browser steps

Start these in separate terminals from the repository root:

```sh
.venv/bin/python .trellis/tasks/10-16-bilibili-multipart/artifacts/browser_qa_api.py --port 8021
cd web
pnpm exec vite --config ../.trellis/tasks/10-16-bilibili-multipart/artifacts/browser_qa_vite.mjs
```

Use a newly created QA tab at `http://127.0.0.1:5181/library`:

1. Open 添加视频 → 选择 B 站分 P. Verify native modal/focus/Escape and no
   selection on preview of `BV1xx411c7mD`.
2. Select parts across both pages; verify the stable selection summary, disabled
   eleventh choice, unavailable P4, and default-off per-entry consent for P3.
3. Add P1/P2/P3 to inspect duplicate/quota/legacy or consent results. Confirm
   frozen row mapping and no claim of subtitle readiness.
4. Put `unknown` in the optional note before submitting another selection.
   Inspect the uncertainty message and confirm the replay uses the same operation
   and payload. Use fixture `/api/v1/qa/stats` only as disposable QA evidence.
5. Try duplicate clicks, close/Escape while pending, and a fresh reopened view;
   verify no stale selection or late result appears.
6. Open `qa-verified-part` detail. Inspect server-issued source/chapters/transcript
   links and local times. Fixture source JSON should report current P6, preserving
   time, without navigating to a real provider.
7. Inspect 390×844 and desktop geometry, console/page errors, and empty private
   Web Storage. Save screenshots and stop both servers; close only the QA tab.

## Already completed automated browser-layer gates

- Focused backend: **58 passed**, covering actual owned joins, foreign/deleted/
  mismatched provenance, legacy behavior, current JSON, SSE section/final,
  reopened history batched projection, transcript links, and existing regressions.
- Frontend: **238 passed**; typecheck, lint, build and canonical generated API
  stale checks passed. These tests do not replace the pending real-browser gate.
