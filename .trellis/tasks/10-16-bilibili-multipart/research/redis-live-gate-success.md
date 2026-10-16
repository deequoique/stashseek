# Real Redis Lua gate verification — 2026-10-16

**Executed: six real-server tests passed in 27.95 seconds.** This supersedes the
earlier outstanding result in redis-live-gate-attempt.md. No product code,
production dependency, existing Redis instance or user data was changed.

## Trusted temporary runtime

The [embedded-redis maintainer](https://github.com/codemonstur/embedded-redis)
documents Maven coordinates com.github.codemonstur:embedded-redis:1.4.3 and
bundled macOS arm64 Redis executables. Its README links those builds to
packages.redis.io. Downloading that fixed release through verified HTTPS from
Maven Central succeeded after the official Redis archive host had timed out.

- Artifact: [embedded-redis-1.4.3.jar](https://repo.maven.apache.org/maven2/com/github/codemonstur/embedded-redis/1.4.3/embedded-redis-1.4.3.jar)
- Repository SHA1: [published checksum](https://repo.maven.apache.org/maven2/com/github/codemonstur/embedded-redis/1.4.3/embedded-redis-1.4.3.jar.sha1),
  1c0273801089295096f588ecd226bfa12cb59a0e, matched downloaded bytes.
- No SHA256 sidecar was published (404). Local SHA256 was additionally recorded:
  ffbc63265c60d25dee453d6f656b527d98200d98657c6a0ab4dec4ea2c3a5cd6.
  Do not call this a matched publisher SHA256 or signature verification.
- Only member redis-server-6.2.6-v5-darwin-arm64 was extracted; no Java code or
  unrelated package executable ran. Binary SHA256:
  c523aec5a9b6f565cff0271ef67f5c51fa83e1180e3e954ab5251b3cf6282b76.
- The bundle member name differs from the executable's actual report:
  **Redis 6.2.11**, commit 720ea82e, build 24766f51e4115c72. Evidence uses the
  observed version, not the filename's older label.
- file/otool confirmed an arm64 Mach-O with system/installed OpenSSL3 libraries;
  the version probe ran successfully. There was no dependency installation or
  certificate-verification bypass.

Archive, binary and detailed hashes remain in the newly created directory
/private/tmp/stashseek-redis-maven-j4nu22fi. Machine-readable evidence is
redis-test-runtime.json in this research directory.

## Real execution boundary

tests/test_bilibili_gate_redis.py accepts only an explicitly provided binary
path, never an existing Redis URL. Every fixture launches its own random
127.0.0.1 port in a new temporary directory, verifies INFO process_id equals its
child PID, and verifies bind, save-empty and appendonly-no configuration.
Persistence is disabled and memory capped. Teardown waits for termination,
kills only its owned child if necessary, and asserts no RDB/AOF artifacts.
No test process remained after the run.

Command:

~~~text
STASHSEEK_TEST_REDIS_BINARY=/private/tmp/stashseek-redis-maven-j4nu22fi/redis-server \
  .venv/bin/python -m pytest -q tests/test_bilibili_gate_redis.py --tb=short
~~~

## Assertions executed

1. Exactly 30 tenant admissions succeed; the 31st receives the fixed rate
   error. Another tenant remains isolated. Expired window members are removed
   using Redis TIME, with bounded key TTL and no cleartext tenant key.
2. Forty concurrent Lua admissions allow exactly 30 and reject ten.
3. Six concurrent callers across separate gate instances admit exactly two
   upstream leases; four receive bilibili_busy. Released leases leave zero slots.
4. A stale token cannot release a replacement lease. Exception/cancellation-style
   unwinding releases its own lease.
5. Two orphan leases occupy both slots, have the unchanged 20-second TTL, and
   **naturally expire after actual wall-clock waiting**. A new lease then succeeds.
6. Stopping the owned server makes both tenant and upstream admission fail
   closed with bilibili_unavailable.

The first run stopped at a fixture assertion because redis-py CONFIG GET returns
strings, not bytes. All owned instances were stopped; correcting that fixture
assertion produced the passing run. No product change was needed.

This proves these Lua contracts on the observed temporary Redis 6.2.11 runtime.
It does not establish deployment topology, production Redis health or a
production-version matrix. Native browser acceptance is a separate gate.
