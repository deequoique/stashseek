# Local capture failure diagnosis and repair

Date: 2026-08-14 Asia/Singapore

## Observed item

- Public item: `4272180151114ecea25ac69bebd89bc7`
- Platform/media: `ntu_kaltura` / `52_6ce010d`
- Capture: `caption_status=unavailable`, no raw transcript object
- Terminal state: `failed`, stable error `queue_unavailable`
- Submitted title: `Media Gallery`

No transcript body, signed URL, credential, internal user ID, or storage key was inspected or recorded.

## Queue root cause

The Web capture route supplies a five-second broker publish budget. `BrowserCaptureSubmissionService` previously started that deadline before remote PostgreSQL admission. A read-only `select 1` against the configured development PostgreSQL took about 5.48 seconds, while Redis had remained up for more than three hours with zero rejected connections and the worker responded to `ping` on both required queues. The item moved from creation to `queue_unavailable` in about 6.9 seconds.

The repair makes the budget apply only to the actual Celery/Redis publication. Database admission and object staging retain their own request/database behavior and can no longer consume the broker budget or produce this false queue classification.

## Kaltura root cause and repair

The initial adapter ran on NTULearn frames covered by the original host permissions. NTULearn embeds its concrete player in Kaltura-owned cross-origin frames, so the top-level Media Gallery shell could expose an entry ID without exposing the player text tracks or caption resources. The fallback therefore submitted a generic title with `caption_status=unavailable`.

The repair adds the Kaltura-owned frame permission and ranks concrete player results over the generic shell. Inside the user-invoked player frame it first reads browser-native `TextTrack` cues, then falls back to trusted NTU/Kaltura VTT, SRT, DFXP, or TTML resources already authorized by the page. Signed asset URLs are consumed only in that frame and are absent from the normalized capture result and fixture assertions.

## Verification

- Backend focused capture/worker suite: 40 passed.
- Extension: 3 tests passed; strict TypeScript, ESLint, build, and package audit passed.
- Web: 17 files / 112 tests passed; TypeScript, ESLint, production build, and OpenAPI stale check passed.
- Local unpacked and downloadable artifacts are version `0.1.3`.

Live NTULearn verification remains a user-controlled manual canary because it uses the user's institutional browser session.
