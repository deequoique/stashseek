# Current production evidence

## Observation window

- Date: 2026-08-10
- Production host: OVHcloud `ubuntu@51.79.159.110`, hostname
  `vps-d2a069a1`, as confirmed by the user and a read-only identity check
- Method: read-only SSH service and journal inspection, followed by one bounded
  metadata-only YouTube probe
- Privacy controls: no environment file, database row, raw journal message,
  URL from a user submission, cookie, token, or signed subtitle URL was read or
  printed

## Results

All three relevant services reported `active`:

- combined Notebook Agent service
- ingestion worker
- Celery Beat

The same read-only host check confirmed OpenSSH permits TCP forwarding while
`GatewayPorts no` prevents a requested remote forward from becoming a public
listener by default. This supports a loopback-only reverse-tunnel recovery
without changing the public firewall. No package, service, SSH, tunnel, or
network configuration was changed during discovery.

The worker journal contained 17 occurrences of the normalized
`transient_fetch_failed` code in this distribution:

| UTC minute | Count |
| --- | ---: |
| 08:00 | 1 |
| 08:03 | 2 |
| 08:04 | 3 |
| 08:05 | 3 |
| 08:09 | 2 |
| 08:10 | 2 |
| 08:11 | 1 |
| 08:13 | 3 |

The count represents logged retry failures, not unique videos or users. The
pattern is consistent with retry amplification but cannot reconstruct unique
jobs because the privacy-safe task boundary intentionally replaces the
provider's raw error.

One metadata-only probe used:

- the public yt-dlp test video, not a user-submitted URL;
- `--ignore-config`;
- the same `youtube:player_client=android_vr` extractor argument as the
  production connector;
- `--skip-download` and a 30-second socket timeout;
- classification-only output.

The probe exited with status 1 and matched 429 / `Too Many Requests`.

## What this proves

- The worker process is up; this incident is not explained by the worker being
  stopped.
- A public video metadata request through the production host's current egress
  and player-client profile is actively rate-limited.
- Same-worker retries do not change the outbound path and did not restore the
  sampled requests during the observation window.

## What this does not prove

- That the public IP alone is the only input to YouTube's decision. The player
  client, TLS/HTTP fingerprint, request history, ASN, region, or a combination
  may contribute.
- That every YouTube video, endpoint, player client, or subtitle CDN request
  fails identically.
- The restriction duration or whether an idle cooldown alone would clear it.
- That changing the IP once will be stable under production request volume.
- That cookie-authenticated, token-assisted, alternate-egress, client-side, or
  third-party acquisition is acceptable for this product.

## Repository anchors

- `app/connectors/youtube.py:1`: no persisted platform cookies.
- `app/connectors/youtube.py:80`: fixed yt-dlp command and `android_vr` client.
- `app/connectors/youtube.py:101`: 429 classification and broad fallback.
- `app/ingest/tasks.py:390`: five Celery retries with exponential backoff.
- `app/ingest/tasks.py:795`: retries re-enter the same processing path.
- `app/ingest/tasks.py:810`: provider detail is replaced by the normalized
  privacy-safe failure code.
- `.trellis/spec/backend/youtube-connector.md:54`: current connector error
  contract.
