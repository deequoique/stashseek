"""Authenticated loopback HTTP bridge for out-of-process channel plugins."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import threading
import time
from dataclasses import replace
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable
from uuid import uuid4

from app.agent.types import AgentAnswer
from app.bootstrap import build_channel_service
from app.channels.types import ChannelEnvelope
from app.config import Settings
from app.diagnostics import configure_runtime_logging


MAX_BODY_BYTES = 64 * 1024


class RequestVerifier:
    """HMAC authentication with timestamp and in-process replay protection."""

    def __init__(self, secret: str, *, max_age_seconds: int = 60) -> None:
        if len(secret) < 32:
            raise ValueError("CHANNEL_GATEWAY_SECRET must be at least 32 characters")
        self._secret = secret.encode()
        self._max_age = max_age_seconds
        self._nonces: dict[str, int] = {}
        self._lock = threading.Lock()

    def verify(self, body: bytes, timestamp: str, nonce: str, signature: str) -> bool:
        try:
            numeric_time = int(timestamp)
        except (TypeError, ValueError):
            return False
        now = int(time.time())
        if abs(now - numeric_time) > self._max_age or not nonce or len(nonce) > 128:
            return False
        signed = timestamp.encode() + b"\n" + nonce.encode() + b"\n" + body
        expected = hmac.new(self._secret, signed, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature or ""):
            return False
        with self._lock:
            self._nonces = {
                key: value
                for key, value in self._nonces.items()
                if now - value <= self._max_age
            }
            if nonce in self._nonces:
                return False
            self._nonces[nonce] = numeric_time
        return True


def signature(secret: str, body: bytes, timestamp: str, nonce: str) -> str:
    signed = timestamp.encode() + b"\n" + nonce.encode() + b"\n" + body
    return hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()


def _handler(
    verifier: RequestVerifier,
    dispatch: Callable[[ChannelEnvelope], AgentAnswer],
):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path != "/health":
                self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                return
            self._json(HTTPStatus.OK, {"status": "ok"})

        def do_POST(self) -> None:
            if self.path != "/v1/messages":
                self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if length <= 0 or length > MAX_BODY_BYTES:
                self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid_body"})
                return
            body = self.rfile.read(length)
            if not verifier.verify(
                body,
                self.headers.get("X-KB-Timestamp", ""),
                self.headers.get("X-KB-Nonce", ""),
                self.headers.get("X-KB-Signature", ""),
            ):
                self._json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
                return
            try:
                # The HMAC authenticates the bridge. Its trace token is only an
                # opaque correlation field; request IDs remain gateway-owned.
                envelope = replace(
                    ChannelEnvelope(**json.loads(body)), request_id=uuid4().hex
                )
                answer = dispatch(envelope)
            except (TypeError, ValueError, json.JSONDecodeError):
                self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid_envelope"})
                return
            except Exception:
                self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "unavailable"})
                return
            self._json(HTTPStatus.OK, answer.model_dump(mode="json"))

        def _json(self, status: HTTPStatus, payload: dict) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(status.value)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args) -> None:
            # Do not copy private message bodies or identity mappings into logs.
            return

    return Handler


def serve(settings: Settings) -> None:
    if settings.channel_gateway_host not in {"127.0.0.1", "::1", "localhost"}:
        raise RuntimeError("channel gateway must bind to loopback")
    if not settings.channel_gateway_secret:
        raise RuntimeError("CHANNEL_GATEWAY_SECRET is required")
    configure_runtime_logging(
        log_dir=settings.stashseek_log_dir,
        max_bytes=settings.stashseek_log_max_bytes,
        backup_count=settings.stashseek_log_backup_count,
    )

    service = build_channel_service(settings)
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()

    def dispatch(envelope: ChannelEnvelope) -> AgentAnswer:
        future = asyncio.run_coroutine_threadsafe(service.handle(envelope), loop)
        return future.result(timeout=settings.agent_timeout_seconds + 10)

    verifier = RequestVerifier(settings.channel_gateway_secret)
    server = ThreadingHTTPServer(
        (settings.channel_gateway_host, settings.channel_gateway_port),
        _handler(verifier, dispatch),
    )
    try:
        server.serve_forever()
    finally:
        server.server_close()
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5)
