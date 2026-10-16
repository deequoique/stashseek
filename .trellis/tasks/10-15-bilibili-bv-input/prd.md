# Direct Bilibili BV Input

## Goal

Let a user add a Bilibili video by entering its BV ID directly rather than finding
and copying the full video URL.

## Confirmed Scope

The user asked for direct BV recognition and selected “new small task, then modify”
on 2026-10-15. The intended entry is the existing Add Videos input. Existing valid
YouTube and Bilibili video links continue to work, including mixed multi-line batches.

At task start, `app/ingest/submission.py:409` rejected a bare BV because URL
scheme/host checks preceded Bilibili admission; that base BV behavior is now implemented. The connector in
`app/connectors/bilibili.py:135` already admits a 12-character BV ID within a strict
HTTPS video URL and preserves the suffix case. `web/src/library/AddVideosDialog.tsx`
already sends plain input strings in the existing `urls` array, so no API shape
change is necessary.

## Requirements

- R1: A standalone valid BV ID is accepted as a Bilibili video reference and is
  treated as the corresponding `https://www.bilibili.com/video/<BV-ID>` URL.
- R2: Trim surrounding whitespace; recognize either-case BV prefix consistently
  with existing full URLs; preserve all remaining characters exactly.
- R3: BV ID input and its full video URL refer to the same stored video and use
  existing duplicate handling, without creating a separate item.
- R4: Support a batch mixing BV IDs and existing supported links, retaining the
  current per-line ordering, maximum of ten items, validation results and quotas.
- R5: Field guidance and errors acknowledge BV IDs. Canonicalization remains local;
  recognizing an ID does not assert the video exists or captions are available.
- R6: Do not extract a bare BV substring from arbitrary prose, foreign-host URLs
  or malformed input. Preserve existing host/path/credentials/port/multipart guards.
- R7: Accept the complete Bilibili copied share text supplied by the user, including
  its Chinese bracketed title and tracking URL. Automatically remove surrounding
  text, extract the actual video URL, and reuse canonical Bilibili normalization.
- R8: The Add Videos interface must handle a share title and URL on the same or
  separate lines, show extracted URLs rather than title-only rows, and retain the
  existing ten-video limit, mixed BV/link support and duplicate handling. Backend
  direct submission may accept one URL embedded in a share entry; multiple URLs
  inside one backend entry must not silently select the first.

## Acceptance Criteria

- [x] AC1: `BV1xx411c7mD`, `bv1xx411c7mD` and whitespace-padded versions resolve to
  platform `bilibili`, ID `BV1xx411c7mD` and the same canonical HTTPS video URL.
- [x] AC2: Invalid bare-BV lengths/punctuation, prose containing only a bare BV substring,
  and foreign URLs containing a BV are rejected without remote preflight requests.
  A real Bilibili URL embedded in copied share text follows AC5–AC7.
- [x] AC3: Add Videos offers direct-BV guidance and passes BV strings/mixed lines
  through the existing submit path with no request-contract change.
- [x] AC4: BV ID/full URL duplicate handling and existing YouTube/Bilibili/ten-item
  validation checks continue to pass.

## Out of Scope and Constraints

No short-link resolution, bare-BV substring extraction from prose, bare av IDs, new video
platforms or changes to subtitle fetching. Do not enable writes in the read-only
local demo or change login, provider registrations, ingestion deployment settings
or production services. Existing dirty auth/history/UI work must be retained.

The demo currently disables adding videos; recognition can be verified through
focused parsing/service/UI tests and an isolated fake submission boundary without
claiming live ingestion. Normal production saving still requires the configured
backend and queue.

## Share-Text Acceptance Extension

On 2026-10-15 the user supplied the full copied Bilibili share text, clarified
“上面的全部 包括文字”, and explicitly requested “自动去除文字提取链接”. This updates
the same in-progress task; it supersedes the earlier exclusion of share-text
extraction and is authorization to implement that concrete behavior. No renewed
product choice is needed and no commit/deployment was authorized.

- [x] AC5: The complete supplied one-line share text resolves to Bilibili ID
  `BV15pao6iEZo` and `https://www.bilibili.com/video/BV15pao6iEZo`, without a title row
  or a format error.
- [x] AC6: Split-title/link and repeated share blocks are extracted in order;
  standalone BV IDs and mixed links still work, and more than ten extracted videos
  are blocked rather than truncated.
- [x] AC7: Malformed/foreign/credentialed/non-first-part/fragment URLs are not
  repaired into an allowed video; no remote preflight fetch is added.

Example (tracking value redacted because it is unnecessary for the test):

```text
【一个小改装作坊，如何凭一台”红猪“改写奔驰赛车历史？】 https://www.bilibili.com/video/BV15pao6iEZo/?share_source=copy_web&vd_source=test
```

## Planning Status

Lightweight task: PRD-only. Evidence inspection and convergence are complete.
The user approved the final scope with “好的” on 2026-10-15; implementation is
authorized and the task is now in progress. No deployment or unrelated changes
are included.
