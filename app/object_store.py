"""Small shared boundary for bounded raw-object reads and writes."""

from __future__ import annotations

import math
import os
from collections.abc import Iterator
from typing import Any

import boto3
from boto3.s3.transfer import TransferConfig
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

from app.config import get_settings


class ObjectStoreError(RuntimeError):
    """A private object-store failure that callers must map to a safe code."""


class ObjectNotFound(ObjectStoreError):
    pass


# Same class under the name used by the streaming API contract.
ObjectNotFoundError = ObjectNotFound


class ObjectTooLarge(ObjectStoreError):
    pass


_NOT_FOUND_CODES = {"404", "NoSuchKey", "NotFound"}
# Multipart transfer settings bound upload memory to roughly
# chunk size * concurrency regardless of the file size.
_UPLOAD_TRANSFER_CONFIG = TransferConfig(
    multipart_threshold=8 * 1024 * 1024,
    multipart_chunksize=8 * 1024 * 1024,
    max_concurrency=4,
    use_threads=True,
)


class ObjectStream:
    """One open object body that is read incrementally and never buffered."""

    def __init__(self, body: Any, *, content_length: int, content_type: str) -> None:
        self._body = body
        self.content_length = content_length
        self.content_type = content_type
        self._closed = False

    def iter_chunks(self, chunk_size: int = 65536) -> Iterator[bytes]:
        """Yield at most ``content_length`` bytes, then close the body."""

        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        sent = 0
        try:
            while True:
                chunk = self._body.read(chunk_size)
                if not chunk:
                    break
                sent += len(chunk)
                if sent > self.content_length:
                    raise ObjectStoreError("object stream exceeded declared length")
                yield bytes(chunk)
        except ObjectStoreError:
            raise
        except Exception as exc:
            raise ObjectStoreError("object store unavailable") from exc
        finally:
            self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        close = getattr(self._body, "close", None)
        if callable(close):
            close()


class RawObjectStore:
    def __init__(self, *, client: Any | None = None, bucket: str | None = None) -> None:
        settings = get_settings() if client is None or bucket is None else None
        self.bucket = bucket or settings.minio_bucket
        timeout = max(
            1.0,
            float(settings.trash_purge_object_timeout_seconds)
            if settings is not None
            else 5.0,
        )
        self._client_kwargs = (
            {
                "endpoint_url": settings.minio_endpoint_url,
                "aws_access_key_id": settings.minio_access_key,
                "aws_secret_access_key": settings.minio_secret_key,
            }
            if client is None
            else None
        )
        self._base_config = BotoConfig(
            connect_timeout=timeout,
            read_timeout=timeout,
            retries={"total_max_attempts": 1, "mode": "standard"},
        )
        self.client = client or boto3.client(
            "s3",
            config=self._base_config,
            **self._client_kwargs,
        )

    def put(self, key: str, body: bytes, content_type: str) -> None:
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except ClientError:
            self.client.create_bucket(Bucket=self.bucket)
        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=body,
            ContentType=content_type,
        )

    def put_file(self, key: str, path: str | os.PathLike, content_type: str) -> int:
        """Upload a local file with multipart streaming and return its size."""

        try:
            size = os.path.getsize(path)
            try:
                self.client.head_bucket(Bucket=self.bucket)
            except ClientError:
                self.client.create_bucket(Bucket=self.bucket)
            self.client.upload_file(
                Filename=os.fspath(path),
                Bucket=self.bucket,
                Key=key,
                ExtraArgs={"ContentType": content_type},
                Config=_UPLOAD_TRANSFER_CONFIG,
            )
        except Exception as exc:
            raise ObjectStoreError("object upload failed") from exc
        return size

    def open_stream(self, key: str, *, max_bytes: int) -> ObjectStream:
        """Open one object for incremental reading.

        Raises ``ObjectNotFoundError`` when the key is absent and ``ValueError``
        when the object is larger than ``max_bytes``; the body is closed first.
        """

        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
            body = response["Body"]
            length = response.get("ContentLength")
            content_type = response.get("ContentType")
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code in _NOT_FOUND_CODES:
                raise ObjectNotFound("object not found") from exc
            raise ObjectStoreError("object store unavailable") from exc
        except Exception as exc:
            raise ObjectStoreError("object store unavailable") from exc
        stream = ObjectStream(
            body,
            content_length=length if isinstance(length, int) else 0,
            content_type=(
                content_type
                if isinstance(content_type, str) and content_type
                else "application/octet-stream"
            ),
        )
        if not isinstance(length, int) or length < 0:
            stream.close()
            raise ObjectStoreError("object size unavailable")
        if length > max_bytes:
            stream.close()
            raise ValueError("object exceeds byte limit")
        return stream

    def get(self, key: str, *, max_bytes: int) -> bytes:
        """Read at most ``max_bytes`` and reject oversized objects before/while reading."""

        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        try:
            metadata = self.client.head_object(Bucket=self.bucket, Key=key)
            length = metadata.get("ContentLength")
            if not isinstance(length, int) or length < 0:
                raise ObjectStoreError("object size unavailable")
            if length > max_bytes:
                raise ObjectTooLarge("object exceeds byte limit")
            response = self.client.get_object(Bucket=self.bucket, Key=key)
            stream = response["Body"]
            try:
                body = stream.read(max_bytes + 1)
            finally:
                close = getattr(stream, "close", None)
                if close is not None:
                    close()
            if len(body) > max_bytes:
                raise ObjectTooLarge("object exceeds byte limit")
            return bytes(body)
        except (ObjectStoreError, ObjectTooLarge):
            raise
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code in {"404", "NoSuchKey", "NotFound"}:
                raise ObjectNotFound("object not found") from exc
            raise ObjectStoreError("object store unavailable") from exc
        except Exception as exc:
            raise ObjectStoreError("object store unavailable") from exc

    def _client_for_timeout(self, timeout_seconds: float | None) -> tuple[Any, bool]:
        if timeout_seconds is None or self._client_kwargs is None:
            return self.client, False
        try:
            timeout = float(timeout_seconds)
        except (TypeError, ValueError):
            raise TimeoutError("object_delete_timeout") from None
        if not math.isfinite(timeout) or timeout <= 0:
            raise TimeoutError("object_delete_timeout")
        overhead_reserve = min(0.05, timeout * 0.1)
        available = timeout - overhead_reserve
        minimum_stage = 0.001
        if available < minimum_stage * 2:
            raise TimeoutError("object_delete_timeout")
        connect_timeout = max(minimum_stage, available * 0.25)
        read_timeout = available - connect_timeout
        if read_timeout < minimum_stage:
            read_timeout = minimum_stage
            connect_timeout = available - read_timeout
        if connect_timeout <= 0 or read_timeout <= 0:
            raise TimeoutError("object_delete_timeout")
        config = self._base_config.merge(
            BotoConfig(
                connect_timeout=connect_timeout,
                read_timeout=read_timeout,
            )
        )
        return boto3.client("s3", config=config, **self._client_kwargs), True

    def delete_object(
        self, key: str, *, timeout_seconds: float | None = None
    ) -> None:
        """Idempotently delete one raw object without exposing its private key."""

        client, temporary = self._client_for_timeout(timeout_seconds)
        try:
            try:
                client.delete_object(Bucket=self.bucket, Key=key)
            except ClientError as exc:
                code = str((exc.response or {}).get("Error", {}).get("Code", ""))
                if code in {"NoSuchKey", "NoSuchBucket", "404", "NotFound"}:
                    return
                raise ObjectStoreError("object delete failed") from None
        finally:
            if temporary:
                close = getattr(client, "close", None)
                if callable(close):
                    close()

    def delete(self, key: str, *, timeout_seconds: float | None = None) -> None:
        self.delete_object(key, timeout_seconds=timeout_seconds)
