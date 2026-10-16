# Real Redis gate verification attempt — 2026-10-16

Historical failed download attempts are retained below. A later bounded
Maven-published runtime succeeded; see [real-server verification](./redis-live-gate-success.md).
Real Lua validation is no longer outstanding on the tested temporary runtime.

## Result

**Historical attempt: official source downloads timed out from this host.**
The later six real-server tests in [redis-live-gate-success.md](./redis-live-gate-success.md)
supersede the initial execution gap. This file preserves the failed download
attempts; it is not the final Redis verification result. Existing injected gate
tests and static review are still distinguished from actual-Lua execution.

No Redis process was started, no Redis connection was made, no dependency was
installed, and no existing service, database or product configuration changed.
The attempts used newly created temporary directories only.

## Fixed source and integrity plan

The official [redis-hashes registry](https://github.com/redis/redis-hashes)
was opened with the web tool and its Redis 7.4.8 entry verified before download:

```text
redis-7.4.8.tar.gz
sha256 f6773cb7d63be236c59c2917a82f1f08e47b77d89b2f0c9f53becb22b8ea4172
https://download.redis.io/releases/redis-7.4.8.tar.gz
```

`make` and `cc` are available; redis-server, valkey-server and Docker were not
available. Planned compilation/execution was restricted to the temporary source
tree and an ephemeral 127.0.0.1 listener with no persistence. SHA256 verification
had to succeed before extracting or executing any downloaded source.

## Bounded attempts

1. Read the official hash file from raw.githubusercontent.com using verified
   certifi TLS and a 30-second socket timeout. The request failed with a read
   timeout before retrieving hashes/source. No archive extraction occurred.
2. Try the fixed official archive URL directly using curl with a five-second
   connect timeout and forty-second total cap. The request returned exit 28
   after approximately five seconds. Its new directory was
   `/private/tmp/stashseek-redis-gate-r7p5alx3`; no source was verified or executed.

Both network operations used the normal escalated-tool path; neither produced an
automatic approval rejection. The remaining blocker is host network access,
not missing user authorization. No production environment values were inspected
or printed.

## Scenarios requested after this failed attempt

The subsequent success record executes the unchanged `RedisPublicGate` Lua on an independently owned temporary
server to prove 30/min tenant admission and isolation, global-two concurrency
across service instances, token-fenced release, expired-member/key recovery,
exception/cancellation release and closed admission on server failure. Do not
point these destructive fixture scenarios at an existing Redis instance.
