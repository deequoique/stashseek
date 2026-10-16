# Backend authorization regression evidence

## Isolated setup

Tests use an in-memory SQLite database, synthetic Session and CSRF values,
the in-process Redis fake, and a fixed explicit `now`. No live provider,
private configuration, or external database is used.

## Before the source fix

Command:

```bash
POSTGRES_PASSWORD=stashseek-local-test AGENT_MODEL=test .venv/bin/python -m pytest tests/test_web_session_cache.py -q
```

Result: **12 failed, 29 passed**. Each of the 12 failures counts four SELECTs
instead of the required single joined authorization query:

1. the authoritative WebSession/AppUser/ChannelIdentity join;
2. a WebSession reload;
3. an AppUser reload;
4. a ChannelIdentity reload.

The recent-last-used read-only path reproduces this under both
`expire_on_commit=False` and `True`. The stale-last-used write path reproduces
it when `expire_on_commit=True`. Rollback expires ORM rows regardless of the
commit-expiration setting; commit expires them when that setting is enabled.

## Regression matrix

- Four cache modes: absent, TTL zero, cold miss, Redis outage.
- Both transaction branches: `last_used_at` one second old (no UPDATE),
  and 61 seconds old (one bounded UPDATE).
- Both Session factory expiration settings.
- Every matrix case requires exactly one joined SELECT and the identical
  immutable authorized Session projection after transaction completion.
- CSRF accepts only the matching value, and the persisted last-used timestamp
  confirms the existing throttle.
- Seven independent admission failures remain rejected with one SELECT during
  Redis outage: revoked Session, expiry boundary, disabled user, disabled
  identity, wrong channel, wrong account, and inconsistent tenant ownership.
- Existing tests continue to cover zero-query warm hits, outage/recovery,
  malformed cached projections, generation races and post-commit invalidation.

## After the source fix

The same command passes: **41 passed in 0.24 seconds**. All 16 matrix cases
perform exactly one joined SELECT. The 12 previously failing cases drop from
four SELECTs to one (three database round trips removed, a 75% reduction in
authorization SELECT count). The seven security-rejection cases and all
existing cache tests pass.

These local query counts measure eliminated database round trips; they do not
claim production latency or bypass the remaining network/compute startup costs.
