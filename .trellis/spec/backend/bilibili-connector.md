# Bilibili Connector Contract

## 1. Scope / Trigger

Apply this contract when changing Bilibili URL admission, yt-dlp metadata or
subtitle ingestion, SRT storage/reading, Bilibili citations, or public Web
capabilities. Browser-companion page capture remains a separate follow-up and
must not be implied by server URL support.

Explicit part selection and public favorites follow the separate
[Bilibili Explicit Parts and Favorites](./bilibili-parts.md) contract. The
ordinary compatibility path below never silently expands those selections.

## 2. URL Contract

- The ordinary direct-input connector accepts only credential-free HTTPS `bilibili.com`/`www.bilibili.com` ordinary
  video paths with a strict 12-character BV ID or numeric av ID.
- Strip tracking queries and fragments from the canonical URL. Reject fragments,
  explicit ports, short links, non-video paths, malformed IDs, and `p` values
  other than the first part. Base multipart URLs intentionally select part 1
  through yt-dlp `--no-playlist`; do not silently expand a collection.
- Ordinary direct-input URL preflight is local and must never make a provider request.
- Submission also accepts a complete bare BV ID through
  `normalize_item_reference`; it expands the ID into the canonical HTTPS URL
  before applying the same connector checks. The connector itself stays URL-only.
- Submission can extract one actual Bilibili URL from surrounding share text.
  The Add Videos client splits pasted share blocks into separate URL entries;
  a single backend entry with multiple URL candidates is ambiguous and rejected.

## 3. Connector Contract

- Use the declared yt-dlp runtime with `--ignore-config`, `--no-playlist`,
  `--skip-download`, a socket timeout, and a process wall-clock timeout.
- Never supply, persist, log, or enqueue Bilibili cookies, `SESSDATA`, WBI
  signatures, media URLs, subtitle URLs, response bodies, or raw stderr.
- Map public metadata to `ItemMeta`; accept cover images only from `hdslb.com`
  subdomains, upgrade them to HTTPS, and remove query/fragment data.
- Ignore the `danmaku` XML track. Prefer official SRT before `ai-*` automatic
  SRT. Consume only yt-dlp-projected inline `data`; a URL-only track requires
  browser capture rather than adding a broad server fetch allowlist.
- Parse bounded UTF-8 SRT into finite, ordered cues. Store it as raw format
  `srt`, object suffix `.srt`, and content type `application/x-subrip`.
- No visible subtitle returns `NeedsASR`. An explicit yt-dlp login-only warning
  returns `NeedsExtension`. Because upstream/provider behavior can omit that
  discriminator, real canaries must record the observed state without claiming
  that every login-only video is distinguishable from a no-caption video.
- Stable transient classifications are `bilibili_rate_limited`,
  `bilibili_fetch_timeout`, and `bilibili_fetch_failed`; worker retry surfaces
  continue to redact them according to the shared ingestion contract.

## 4. Required Tests

- URL matrix: BV, av, tracking queries, host confusion, credentials, ports,
  fragments, short links, malformed IDs, and multipart `p` values.
- Metadata mapping, cover allowlist, timeout, rate-limit/risk-control
  classification, stderr redaction, and absence of cookie arguments.
- Official vs automatic SRT selection, danmaku exclusion, login-only/no-caption
  states, size limits, malformed/unordered SRT, object key/content type, Web
  transcript projection, and Bilibili timestamp citations.
- API capability/OpenAPI, Web add-dialog copy, platform label, TypeScript,
  lint, frontend tests, full pytest, and read-only real metadata canaries.

## 5. Scenario: Standalone BV and Copied Share Submission

### Scope / Trigger

Apply this contract when changing Add Videos input admission or submission
normalization. A standalone BV ID, full URL and copied Bilibili share text must
identify the same tenant-owned item without a remote preflight request.

### Signatures

- `normalize_item_reference(url: str) -> ItemReference` in
  `app/ingest/submission.py` accepts supported URLs, complete bare BV IDs or
  a single Bilibili URL embedded in share text.
- `parseVideoInput(value: string): string[]` in
  `web/src/library/videoInput.ts` extracts display/submission entries for the
  native paste event, Enter and direct form submission.
- `POST /api/v1/library/items:batch` keeps its existing `urls: list[str]` request
  and per-item result contract. No new endpoint or generated-schema change.

### Contracts

- Strip surrounding whitespace, then full-match `[bB][vV][0-9A-Za-z]{10}`.
  Normalize only the two-letter prefix to `BV`, preserving suffix case.
- Expand to `https://www.bilibili.com/video/<id>` and reuse existing URL guards.
  Never scan arbitrary prose or foreign URLs for a bare BV substring.
- For share text, consume complete URI candidates, including unsupported schemes
  and protocol-relative URLs. Reject multiple candidates in one backend entry;
  never promote a Bilibili substring inside a foreign URI. Preserve credentials,
  ports, fragments, `p` values and extra path text for the existing URL checks.
  Malformed URL parsing becomes `invalid_url`; Bilibili fragments, including an
  empty `#`, are rejected. Tracking queries disappear only after valid admission.
- Keep URI-internal Chinese punctuation and angle brackets until validation.
  Only recognized terminal Chinese share punctuation may be trimmed. Cutting
  at `，` inside `?note=，&p=2` would silently discard the forbidden part selector.
  A second URI joined after Chinese share punctuation is ambiguous and rejected.
  A nested URL inside an ordinary tracking-query value remains part of the outer
  URI; preserve existing Bilibili/YouTube canonicalization for those parameters.
- Frontend removes same-line sharing prose and standalone `【title】` lines,
  extracts URL candidates in order, and leaves other standalone BV/invalid rows
  intact. It does not implement server URL admission or canonicalization.
  Pasting merges the clipboard with the textarea's selected range before parsing;
  it preserves content outside the selection and all existing tags. Typing does
  not commit partial URLs on every change; Enter and submit use the same parser.
- Retain the ten-entry limit and deduplicate by tenant, platform and platform ID.
  A same-request replay can remain `queued`; a later request can return
  `already_exists`. Neither should create or publish a duplicate dispatch.

### Validation & Error Matrix

| Input | Result |
| --- | --- |
| Complete BV, including lowercase prefix or outer whitespace | Canonical Bilibili reference |
| One Bilibili URL with same-line or multiline sharing text | Validate the candidate and return its canonical reference |
| Multiple URI candidates in one backend entry | `invalid_url`; no first-URL selection or batch expansion |
| Short/long BV, punctuation, non-ASCII suffix, prose with only a bare BV, or bare av | Existing `invalid_url` result |
| Foreign-host URL or rejected Bilibili URL shape | Existing `unsupported_url` result |
| Mixed valid and invalid rows | Preserve per-item ordering and existing partial results |

### Good / Base / Bad Cases

- Good: ` bv1xx411c7mD ` and its full URL resolve to `BV1xx411c7mD`.
- Good: `【一个小改装作坊，如何凭一台”红猪“改写奔驰赛车历史？】 https://www.bilibili.com/video/BV15pao6iEZo/?share_source=copy_web`
  resolves to `https://www.bilibili.com/video/BV15pao6iEZo` without a title row.
- Base: YouTube and existing Bilibili URLs retain their current behavior.
- Bad: `保存 BV1xx411c7mD` or `https://foreign.test/BV1xx411c7mD` cannot
  become a Bilibili reference merely by containing a BV string.

### Tests Required

- `tests/test_bilibili_url.py`: exact ID/case/whitespace matrix; unsafe and
  malformed inputs; mixed preflight preserves order without metadata/text fetches
  or subprocess execution.
- `tests/test_ingest_submission.py`: both BV-first and URL-first batches, plus
  later requests, share one item/dispatch and publish once.
- `web/src/library/AddVideosDialog.test.tsx`: direct unconfirmed BV draft and
  mixed multiline submission use the existing payload without suffix case changes;
  real clipboard sharing text on the same/separate lines, repeated shares,
  selection merging, direct submit and more than ten extracted entries.
- `web/src/library/videoInput.test.ts`: ordinary prose removed only from URL
  rows, independent invalid/BV rows retained, complete foreign and protocol-relative
  URI candidates retained, and URL admission left to the server.
- Backend share tests assert canonical tracking removal, no fetch, ambiguity
  rejection, nested foreign-URI rejection, and unchanged unsafe URL guards.
  Include internal punctuation followed by `p=2`/fragments, empty fragments and
  a second URL joined after a tracking query; none may become a valid prefix.

### Wrong vs Correct

Wrong: search the input with `re.search` and lowercase the entire match.

Correct: full-match a complete BV ID, or extract a complete URI candidate from
share text, then run the existing safe URL normalization. Keep unsafe components
until validation; stripping `p=2` or a fragment before admission would wrongly
change the referenced video. Acceptance means a recognized reference, not proof
of video existence or subtitle availability.
