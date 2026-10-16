# Read-only public collection canary — 2026-10-16 (Asia/Shanghai)

User-supplied input:
`https://space.bilibili.com/400561576/favlist?fid=4157547776&ftype=create`

## Result

The web text-fetch tool could not access the JavaScript-heavy collection page.
That was a tool limitation, not evidence that the collection was private.
A separate unauthenticated HTTPS GET to the fixed Bilibili host and resource-list
path succeeded:

`https://api.bilibili.com/x/v3/fav/resource/list?media_id=4157547776&pn=1&ps=20`

- HTTP status: 200; provider code: 0.
- Collection title: `study`.
- Reported media count: 8; returned list count: 8; `has_more=false`.
- Bounded read: first page only, at most 20 entries and 1 MiB response body,
  15-second timeout, verified TLS, no Cookie/authentication header.
- No imports, video fetches, queue publication, database connection or user-account
  changes. Only public metadata was read; no full response is stored here.

This demonstrates reachability from the current host/network at the check time.
It does not promise that every public folder or production egress IP will work,
that this upstream endpoint is a documented supported platform API, or that
all entries have server-readable subtitles.

## Source evidence

The locally installed maintained yt-dlp 2026.08.19 extractor
`BilibiliFavoritesListIE` uses this resource-list path for metadata and the
resource-ids path for the entire list. Its supported URL forms include the
`space.bilibili.com/<uid>/favlist?fid=<id>` form supplied by the user.

Primary source:
https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/extractor/bilibili.py

Do not run the existing whole-list IDs path unbounded merely because CLI
playlist-entry output is limited. Discovery must enforce body/page/time caps
before treating a favorites list as a safe input.
