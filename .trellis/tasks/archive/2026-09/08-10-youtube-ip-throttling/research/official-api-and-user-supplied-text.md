# Official API and user-supplied text boundary

Reviewed on 2026-08-10. This note separates official YouTube API capability
from repository capability and from non-official acquisition options.

## Official source capability matrix

Primary sources:

- [YouTube Data API `videos.list`](https://developers.google.com/youtube/v3/docs/videos/list)
- [YouTube Data API `captions.list`](https://developers.google.com/youtube/v3/docs/captions/list)
- [YouTube Data API `captions.download`](https://developers.google.com/youtube/v3/docs/captions/download)
- [YouTube API Services Terms of Service](https://developers.google.com/youtube/terms/api-services-terms-of-service)

| Need | Official operation | Credential and authorization boundary | Documented quota cost | Durable conclusion |
| --- | --- | --- | ---: | --- |
| Public video metadata | `videos.list` | API key is sufficient for public data; private data requires authorization | 1 unit per call | Suitable replacement for metadata-stage yt-dlp, subject to project quota and API lifecycle |
| Caption-track inventory | `captions.list` | OAuth authorization required | 50 units per call | Returns track resources, not caption body; not an arbitrary transcript endpoint |
| Caption body | `captions.download` | OAuth caller must have permission to edit the video | 200 units per call | Suitable for the caller's own/managed videos only |
| Arbitrary public caption body | None | No official general-purpose permission model exists | N/A | Requires user-supplied text or a separately accepted non-official dependency |

`videos.list` can supply fields represented by `snippet`, `contentDetails`,
`status`, and related public resource parts, including title, description,
channel identity, publish time, thumbnails, tags when exposed, duration, and
language hints. It does not expose a general public transcript body or the
chapter structure currently inferred by yt-dlp. The product must tolerate
those fields being unavailable rather than silently falling back to scraping.

Official API use is still an external service dependency: quotas, credentials,
API changes, suspension, and policy obligations remain. It is materially more
stable and operable than making consumer Web/player requests from a data-center
IP, but it is not an unlimited SLA.

## Stable transcript-source options

| Source | Arbitrary public video | Depends on production YouTube egress | Main trade-off | Recommended position |
| --- | --- | --- | --- | --- |
| Owner/manager OAuth + `captions.download` | No | Uses official API, not consumer scraping | Only managed videos; OAuth consent, encrypted token lifecycle, high per-call quota | Official optional path for creators |
| User uploads SRT/VTT/JSON3 | Yes, when the user lawfully has the file | No | Extra user step; parser and upload security | Recommended durable MVP |
| User pastes transcript text | Yes, when the user lawfully has the text | No | Timestamps may be absent; needs bounded text UX | Recommended durable MVP companion |
| Browser extension/local helper | Potentially | No server egress dependency | YouTube page changes, extension auth/security, and policy review | Later convenience layer, not the durability foundation |
| User uploads authorized media for ASR | Yes, subject to rights | No YouTube acquisition dependency | Large files, storage/CPU cost, malware/type validation, long-running jobs | Separate later phase after text input |
| Managed transcript/acquisition vendor | Vendor-dependent | No direct server scraping, but adds vendor dependency | SLA, supported-content gaps, data processing, policy and cost | Deferred; not equivalent to an official API |

If the product requires fully automatic captions for arbitrary public videos,
the official API cannot satisfy it. The remaining choices are a browser-context
capture mechanism, a third-party acquisition service, or continued non-official
server acquisition. None simultaneously guarantees official authorization,
arbitrary-video coverage, and independence from changing YouTube behavior.

## What binding a user's YouTube account does and does not grant

Binding an account through OAuth identifies the user and lets the application
act within the scopes that user consents to. It does not convert every resource
the user can watch in the YouTube website into a resource the user can manage
through the Data API.

The official `captions.download` documentation states both boundaries:

- the request requires OAuth authorization, using a scope such as
  `https://www.googleapis.com/auth/youtube.force-ssl`; and
- the user must have permission to edit the video.

Consequently:

- a channel owner, delegated channel manager, or eligible content-partner user
  can use the official caption path for videos covered by that management
  permission;
- merely being able to watch, subscribe to, like, save, or see the transcript
  of someone else's public video does not grant caption-download permission;
- public visibility and caption edit/download authority are separate;
- OAuth must not be treated as a cookie-export or browser-session mechanism.

`captions.list` also requires OAuth, costs 50 units, and does not return the
caption body. `captions.download` costs another 200 units. The owner workflow
therefore needs quota accounting as well as consent, refresh-token encryption,
revocation, and authorization-failure UX. It is useful as an official creator
workflow, but it cannot be the general arbitrary-public-video workflow.

A browser extension that reads a transcript the user can visibly access would
be a different, non-Data-API acquisition mechanism. Account OAuth neither
enables nor stabilizes that mechanism; it would need its own security, policy,
format, and page-change handling.

## Current repository boundary

- `app/connectors/base.py:36-60` already normalizes transcript content as
  `TextResult` and represents missing acquisition capability as
  `NeedsExtension` or `NeedsASR`.
- `app/ingest/tasks.py:240-309` couples metadata fetch and transcript fetch in
  one worker operation, then applies the reusable validation, chunking,
  tenant-prefixed raw-object storage, and embedding pipeline only after a
  `TextResult` exists.
- `app/web/library.py:118-155` maps `needs_extension` and `needs_asr` to a
  generic browser `needs_action` lifecycle but offers no action for supplying
  content.
- `app/api/library_routes.py:209-333` exposes URL batch save, item mutation,
  retry, dispatch read, and transcript read. It has no multipart/file input,
  transcript-text input, OAuth connection, or media upload endpoint.
- `app/api/library_schemas.py:73-103` confirms that the only ingestion request
  model is a bounded list of URLs plus `why_saved`.
- `web/src/videos/VideoDetailView.tsx:124-130` renders source, retry, archive,
  and restore controls; the empty transcript state has no supply-text control
  (`web/src/videos/VideoDetailView.tsx:184-220`).
- `app/config.py:190-203` bounds normalized transcript bytes, cues, characters,
  segments, and embedding characters. These are reusable post-parse guards,
  not sufficient request-streaming or file-type guards for a new upload API.
- `app/ingest/tasks.py:293-309` uses a tenant-prefixed raw-object key, which is
  a useful isolation pattern for user-supplied transcript objects.

## Design implications to carry forward

1. Split “create/enrich item metadata” from “attach/process transcript” so a
   missing transcript is not a failed metadata job.
2. Keep one normalization boundary: every accepted source becomes bounded
   cues plus explicit source/language/format metadata before chunking.
3. Make the transcript-attachment operation tenant-scoped and idempotent, with
   a compare-and-set or version boundary that prevents late workers and
   concurrent submissions from overwriting accepted content.
4. Validate request length before reading a whole upload, then validate file
   type/encoding/parse limits and the existing normalized-content limits.
5. Preserve raw-source provenance without storing access tokens, OAuth account
   identifiers, original local filenames, or sensitive content in logs.
6. Represent “metadata ready, text needed” as a normal product state with
   concrete actions rather than routing it back into same-source retry.
7. Treat OAuth captions, browser helper, and media-ASR as independent source
   adapters. None should be required for the basic metadata-plus-user-text
   workflow.
