"""Shared fail-closed Redis discovery admission and token-fenced call leases."""

from contextlib import contextmanager
import hashlib
import hmac
from uuid import uuid4

from app.bilibili.public import BilibiliPublicError


class RedisPublicGate:
    _RATE = """
local t = redis.call('TIME'); local now = tonumber(t[1])*1000 + math.floor(tonumber(t[2])/1000)
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now-60000)
if redis.call('ZCARD', KEYS[1]) >= 30 then return 0 end
redis.call('ZADD', KEYS[1], now, ARGV[1]); redis.call('PEXPIRE', KEYS[1], 60000)
return 1
"""
    _LEASE = """
local t = redis.call('TIME'); local now = tonumber(t[1])*1000 + math.floor(tonumber(t[2])/1000)
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now)
if redis.call('ZCARD', KEYS[1]) >= 2 then return 0 end
redis.call('ZADD', KEYS[1], now+20000, ARGV[1]); redis.call('PEXPIRE', KEYS[1], 20000)
return 1
"""
    _RELEASE = "return redis.call('ZREM', KEYS[1], ARGV[1])"
    _GLOBAL = "stashseek:bilibili-public:v1:upstream"

    def __init__(self, redis_client, secret: str) -> None:
        self._redis = redis_client
        self._secret = secret.encode()

    def admit(self, tenant_id: int) -> None:
        digest = hmac.new(self._secret, b"bilibili-rate/v1\0" + str(tenant_id).encode(), hashlib.sha256).hexdigest()
        try:
            allowed = self._redis.eval(self._RATE, 1, "stashseek:bilibili-public:v1:tenant:" + digest, uuid4().hex)
        except Exception:
            raise BilibiliPublicError("bilibili_unavailable") from None
        if allowed != 1:
            raise BilibiliPublicError("bilibili_rate_limited")

    @contextmanager
    def upstream(self):
        token = uuid4().hex
        try:
            allowed = self._redis.eval(self._LEASE, 1, self._GLOBAL, token)
        except Exception:
            raise BilibiliPublicError() from None
        if allowed != 1:
            raise BilibiliPublicError("bilibili_busy")
        try:
            yield
        finally:
            # A crash/outage cannot leak a permanent slot. Another token's
            # lease can never be released by this caller.
            try:
                self._redis.eval(self._RELEASE, 1, self._GLOBAL, token)
            except Exception:
                pass
