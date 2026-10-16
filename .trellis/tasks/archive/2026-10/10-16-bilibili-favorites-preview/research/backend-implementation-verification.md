# Favorites backend handoff — 2026-10-16

Implemented strict collection-alias parsing, one-page fixed-operation public
discovery, bounded DTO projection with unavailable rows/capped status, shared
gate service, canonical protected POST and capability composition. The existing
parts row now exposes the exact signed Unix-second expires_at; the UI never has
to parse an opaque ref. No new schema, ingestion limit, quota or queue authority.

New tests/test_bilibili_favorites.py: **52 passed** before independent review.
It covers aliases/attacks, sample-shaped eight rows, empty/private/malformed/
unavailable entries, at most 20 rows, page500/cap, fixed child operation/headers/
proxy exclusion, no database/per-video parts/queue work and canonical API
Session/Origin/CSRF/strict request behavior.

Combined favorites + parts core/API + legacy Bilibili URL/submission + Web
public/runtime regression: **305 passed** using inert configuration. The later
independent check added provider HTTP429/-352 mapping tests; its separate
verification owns that updated count.

The root's read-only new-client canary confirmed collection study: reported 8,
returned 8, available 8, no further page. Evidence is the parent's
research/favorites-canary-20261016.json. It establishes current host reachability
and parser compatibility, not subtitles, production egress or actual importing.

Updated executable backend spec bilibili-parts.md with all seven required
sections, backend index and ordinary connector/P1 scope, preserving prior BV/
share and SSO WIP. Updated use-web-library.md with explicit parts/favorites
steps, default-off legacy overlap, expiry/uncertainty, caption limits and the
separate production migration/release boundary. git diff --check passed.

Browser implementation/generated contracts and final independent check belong
to their agents. Native UI acceptance remains pending device access. No real
imports, shared/production database migration, deployment or Git commit.
