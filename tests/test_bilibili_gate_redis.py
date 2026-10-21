"""Real Lua on a separately owned ephemeral Redis, never a configured instance."""

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import socket
import subprocess
from threading import Barrier, Event, Lock
import time
from types import SimpleNamespace

import pytest
import redis

from app.bilibili.gate import RedisPublicGate
from app.bilibili.public import BilibiliPublicError


@pytest.fixture
def server(tmp_path):
    binary = os.environ.get("STASHSEEK_TEST_REDIS_BINARY")
    if not binary:
        pytest.skip("explicit trusted test Redis binary required; no external URL accepted")
    executable = Path(binary).resolve()
    if not executable.is_file():
        pytest.fail("explicit test Redis binary is unavailable")
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    log = tmp_path / "redis.log"
    process = subprocess.Popen(
        [str(executable), "--bind", "127.0.0.1", "--port", str(port),
         "--save", "", "--appendonly", "no", "--daemonize", "no",
         "--protected-mode", "yes", "--dir", str(tmp_path),
         "--dbfilename", "unused.rdb", "--maxmemory", "64mb",
         "--maxmemory-policy", "noeviction", "--logfile", str(log)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        umask=0o077,
    )
    client = redis.Redis(host="127.0.0.1", port=port, db=0,
                         socket_connect_timeout=0.2, socket_timeout=0.2)
    try:
        deadline = time.monotonic() + 5
        while True:
            if process.poll() is not None:
                pytest.fail("owned test Redis exited before readiness")
            try:
                # A port race cannot grant ownership of an existing Redis.
                assert client.info("server")["process_id"] == process.pid
                break
            except redis.RedisError:
                if time.monotonic() >= deadline:
                    pytest.fail("owned test Redis readiness deadline exceeded")
                time.sleep(0.02)
        assert client.config_get("bind") == {"bind": "127.0.0.1"}
        assert client.config_get("save") == {"save": ""}
        assert client.config_get("appendonly") == {"appendonly": "no"}
        yield SimpleNamespace(client=client, process=process, directory=tmp_path, port=port)
    finally:
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        client.close()
        assert not list(tmp_path.glob("*.rdb"))
        assert not list(tmp_path.glob("*.aof"))


def test_real_redis_tenant_thirty_per_minute_isolated_and_expired_window_recovers(server):
    gate = RedisPublicGate(server.client, "s" * 32)
    for _ in range(30):
        gate.admit(987654)
    with pytest.raises(BilibiliPublicError, match="bilibili_rate_limited"):
        gate.admit(987654)
    gate.admit(987655)
    keys = server.client.keys("stashseek:bilibili-public:v1:tenant:*")
    assert len(keys) == 2 and not any(b"987654" in key for key in keys)
    assert sorted(server.client.zcard(key) for key in keys) == [1, 30]
    full = next(key for key in keys if server.client.zcard(key) == 30)
    now = server.client.time()
    milliseconds = now[0] * 1000 + now[1] // 1000
    for member in server.client.zrange(full, 0, -1):
        server.client.zadd(full, {member: milliseconds - 60001})
    gate.admit(987654)
    assert server.client.zcard(full) == 1
    assert 0 < server.client.pttl(full) <= 60000


def test_real_redis_parallel_rate_admission_is_atomic(server):
    start = Barrier(40)
    def attempt(_):
        gate = RedisPublicGate(server.client, "s" * 32)
        start.wait(timeout=5)
        try:
            gate.admit(11)
            return "allowed"
        except BilibiliPublicError as exc:
            return exc.error_code
    with ThreadPoolExecutor(max_workers=40) as pool:
        outcomes = list(pool.map(attempt, range(40)))
    assert outcomes.count("allowed") == 30
    assert outcomes.count("bilibili_rate_limited") == 10


def test_real_redis_global_two_across_gate_instances_is_atomic(server):
    start, lock, done, release = Barrier(6), Lock(), Event(), Event()
    admitted, denied = [], []
    def attempt(index):
        # Different tenant-MAC secrets still share the same upstream budget.
        gate = RedisPublicGate(server.client, str(index) * 32)
        start.wait(timeout=5)
        try:
            with gate.upstream():
                with lock:
                    admitted.append(index)
                    if len(admitted) + len(denied) == 6:
                        done.set()
                assert release.wait(timeout=5)
            return "allowed"
        except BilibiliPublicError as exc:
            with lock:
                denied.append(index)
                if len(admitted) + len(denied) == 6:
                    done.set()
            return exc.error_code
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(attempt, index) for index in range(6)]
        try:
            assert done.wait(timeout=5)
            assert len(admitted) == 2 and len(denied) == 4
            assert server.client.zcard(RedisPublicGate._GLOBAL) == 2
        finally:
            release.set()
        outcomes = [future.result(timeout=5) for future in futures]
    assert outcomes.count("allowed") == 2 and outcomes.count("bilibili_busy") == 4
    assert server.client.zcard(RedisPublicGate._GLOBAL) == 0


def test_real_redis_release_is_token_fenced_and_exception_releases(server):
    first = RedisPublicGate(server.client, "s" * 32)
    second = RedisPublicGate(server.client, "s" * 32)
    old = first.upstream()
    old.__enter__()
    stale_token = server.client.zrange(first._GLOBAL, 0, -1)[0]
    now = server.client.time()
    server.client.zadd(first._GLOBAL, {stale_token: now[0] * 1000 - 1000})
    with second.upstream():
        current = server.client.zrange(first._GLOBAL, 0, -1)
        assert len(current) == 1 and current[0] != stale_token
        old.__exit__(None, None, None)
        assert server.client.zrange(first._GLOBAL, 0, -1) == current
    with pytest.raises(RuntimeError, match="cancelled"):
        with first.upstream():
            raise RuntimeError("cancelled")
    assert server.client.zcard(first._GLOBAL) == 0


def test_real_redis_orphan_leases_expire_after_actual_twenty_seconds(server):
    client, key = server.client, RedisPublicGate._GLOBAL
    assert client.eval(RedisPublicGate._LEASE, 1, key, "orphan-a") == 1
    assert client.eval(RedisPublicGate._LEASE, 1, key, "orphan-b") == 1
    assert 19000 <= client.pttl(key) <= 20000
    gate = RedisPublicGate(client, "s" * 32)
    with pytest.raises(BilibiliPublicError, match="bilibili_busy"):
        with gate.upstream():
            pytest.fail("orphan leases should initially occupy both slots")
    deadline = time.monotonic() + 22
    while client.exists(key):
        assert time.monotonic() < deadline
        time.sleep(0.1)
    with gate.upstream():
        assert client.zcard(key) == 1
    assert client.zcard(key) == 0


def test_real_redis_server_failure_is_fail_closed(server):
    server.process.terminate()
    server.process.wait(timeout=5)
    gate = RedisPublicGate(server.client, "s" * 32)
    with pytest.raises(BilibiliPublicError, match="bilibili_unavailable"):
        gate.admit(11)
    with pytest.raises(BilibiliPublicError, match="bilibili_unavailable"):
        with gate.upstream():
            pytest.fail("unavailable Redis must not permit an upstream call")
