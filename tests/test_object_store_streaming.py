"""Streaming file upload and incremental download on RawObjectStore."""

import inspect
import io
from types import SimpleNamespace

import boto3
import pytest
from boto3.s3.transfer import TransferConfig
from botocore.exceptions import ClientError

from app.object_store import (
    ObjectNotFound,
    ObjectNotFoundError,
    ObjectStoreError,
    ObjectStream,
    RawObjectStore,
)


def client_error(code, operation="GetObject"):
    return ClientError({"Error": {"Code": code, "Message": "private provider text"}}, operation)


class FakeBody:
    def __init__(self, data: bytes, *, fail_after: int | None = None):
        self._io = io.BytesIO(data)
        self.read_sizes: list[int] = []
        self.closed = False
        self.close_calls = 0
        self._fail_after = fail_after
        self._read = 0

    def read(self, size=-1):
        self.read_sizes.append(size)
        if self._fail_after is not None and self._read >= self._fail_after:
            raise ConnectionResetError("private socket detail")
        self._read += 1
        return self._io.read(size)

    def close(self):
        self.closed = True
        self.close_calls += 1


class FakeS3:
    def __init__(self):
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.bodies: list[FakeBody] = []
        self.upload_calls: list[dict] = []
        self.put_object_calls = 0
        self.head_bucket_error: ClientError | None = None
        self.created_buckets: list[str] = []
        self.get_error: Exception | None = None
        self.upload_error: Exception | None = None
        self.body_factory = FakeBody
        self.response_overrides: dict = {}

    def head_bucket(self, **kwargs):
        if self.head_bucket_error is not None:
            raise self.head_bucket_error

    def create_bucket(self, **kwargs):
        self.created_buckets.append(kwargs["Bucket"])

    def put_object(self, **kwargs):
        self.put_object_calls += 1

    def upload_file(self, **kwargs):
        if self.upload_error is not None:
            raise self.upload_error
        self.upload_calls.append(kwargs)

    def get_object(self, *, Bucket, Key):
        if self.get_error is not None:
            raise self.get_error
        if Key not in self.objects:
            raise client_error("NoSuchKey")
        data, content_type = self.objects[Key]
        body = self.body_factory(data)
        self.bodies.append(body)
        response = {
            "Body": body,
            "ContentLength": len(data),
            "ContentType": content_type,
        }
        response.update(self.response_overrides)
        return response


def make_store(client=None):
    client = client or FakeS3()
    return RawObjectStore(client=client, bucket="raw"), client


# --- put_file ---------------------------------------------------------------


def test_put_file_uploads_by_path_with_multipart_transfer_config(tmp_path):
    audio = tmp_path / "audio.m4a"
    audio.write_bytes(b"x" * 1234)
    store, client = make_store()

    size = store.put_file("asr-tmp/a.m4a", audio, "audio/mp4")

    assert size == 1234
    (call,) = client.upload_calls
    assert call["Filename"] == str(audio)
    assert call["Bucket"] == "raw"
    assert call["Key"] == "asr-tmp/a.m4a"
    assert call["ExtraArgs"] == {"ContentType": "audio/mp4"}
    config = call["Config"]
    assert isinstance(config, TransferConfig)
    assert config.multipart_threshold == 8 * 1024 * 1024
    assert config.multipart_chunksize == 8 * 1024 * 1024
    # Streaming upload: the bytes are never handed over in memory.
    assert client.put_object_calls == 0


def test_put_file_accepts_str_paths_and_creates_a_missing_bucket(tmp_path):
    audio = tmp_path / "audio.webm"
    audio.write_bytes(b"abc")
    store, client = make_store()
    client.head_bucket_error = client_error("404", "HeadBucket")

    assert store.put_file("asr-tmp/b.webm", str(audio), "audio/webm") == 3
    assert client.created_buckets == ["raw"]
    assert len(client.upload_calls) == 1


def test_put_file_failure_is_a_safe_object_store_error(tmp_path):
    audio = tmp_path / "audio.m4a"
    audio.write_bytes(b"abc")
    store, client = make_store()
    client.upload_error = client_error("AccessDenied", "PutObject")

    with pytest.raises(ObjectStoreError) as error:
        store.put_file("asr-tmp/secret-key.m4a", audio, "audio/mp4")

    assert str(error.value) == "object upload failed"
    assert "secret-key" not in str(error.value)
    assert "private provider text" not in str(error.value)


def test_put_file_missing_local_file_is_a_safe_object_store_error(tmp_path):
    store, client = make_store()

    with pytest.raises(ObjectStoreError):
        store.put_file("asr-tmp/a.m4a", tmp_path / "missing.m4a", "audio/mp4")
    assert client.upload_calls == []


def test_real_boto3_client_accepts_the_upload_file_keywords_used():
    client = boto3.client(
        "s3",
        endpoint_url="http://127.0.0.1:1",
        aws_access_key_id="access",
        aws_secret_access_key="secret",
        region_name="us-east-1",
    )

    parameters = inspect.signature(client.upload_file).parameters
    for name in ("Filename", "Bucket", "Key", "ExtraArgs", "Config"):
        assert name in parameters


# --- open_stream ------------------------------------------------------------


def test_open_stream_returns_metadata_without_reading_the_body():
    store, client = make_store()
    client.objects["asr-tmp/a.m4a"] = (b"audio-bytes", "audio/mp4")

    stream = store.open_stream("asr-tmp/a.m4a", max_bytes=100)

    assert isinstance(stream, ObjectStream)
    assert stream.content_length == 11
    assert stream.content_type == "audio/mp4"
    assert client.bodies[0].read_sizes == []
    assert client.bodies[0].closed is False
    stream.close()


def test_iter_chunks_reads_incrementally_and_closes_at_end():
    data = bytes(range(256)) * 1000  # 256,000 bytes
    store, client = make_store()
    client.objects["k"] = (data, "audio/mp4")

    stream = store.open_stream("k", max_bytes=len(data))
    chunks = list(stream.iter_chunks(65536))

    assert b"".join(chunks) == data
    assert all(len(chunk) <= 65536 for chunk in chunks)
    assert len(chunks) == 4
    body = client.bodies[0]
    assert set(body.read_sizes) == {65536}
    assert body.closed is True


def test_iter_chunks_default_chunk_size_is_64_kib():
    store, client = make_store()
    client.objects["k"] = (b"x" * 70_000, "audio/mp4")

    list(store.open_stream("k", max_bytes=100_000).iter_chunks())

    assert set(client.bodies[0].read_sizes) == {65536}


def test_abandoned_stream_is_closed_exactly_once():
    store, client = make_store()
    client.objects["k"] = (b"x" * 200_000, "audio/mp4")

    stream = store.open_stream("k", max_bytes=200_000)
    iterator = stream.iter_chunks(65536)
    next(iterator)
    iterator.close()
    stream.close()

    assert client.bodies[0].closed is True
    assert client.bodies[0].close_calls == 1


def test_close_without_reading_is_idempotent():
    store, client = make_store()
    client.objects["k"] = (b"abc", "audio/mp4")

    stream = store.open_stream("k", max_bytes=10)
    stream.close()
    stream.close()

    assert client.bodies[0].close_calls == 1


def test_open_stream_missing_object_raises_object_not_found():
    store, _client = make_store()

    with pytest.raises(ObjectNotFoundError) as error:
        store.open_stream("asr-tmp/missing.m4a", max_bytes=10)

    assert ObjectNotFoundError is ObjectNotFound
    assert "missing" not in str(error.value)


@pytest.mark.parametrize("code", ["404", "NoSuchKey", "NotFound"])
def test_open_stream_recognizes_every_not_found_code(code):
    store, client = make_store()
    client.get_error = client_error(code)

    with pytest.raises(ObjectNotFoundError):
        store.open_stream("k", max_bytes=10)


def test_open_stream_oversized_object_raises_value_error_and_closes_body():
    store, client = make_store()
    client.objects["k"] = (b"x" * 11, "audio/mp4")

    with pytest.raises(ValueError):
        store.open_stream("k", max_bytes=10)

    assert client.bodies[0].closed is True
    assert client.bodies[0].read_sizes == []


def test_open_stream_accepts_object_exactly_at_the_limit():
    store, client = make_store()
    client.objects["k"] = (b"x" * 10, "audio/mp4")

    stream = store.open_stream("k", max_bytes=10)

    assert b"".join(stream.iter_chunks(4)) == b"x" * 10


@pytest.mark.parametrize("limit", [0, -1])
def test_open_stream_rejects_non_positive_limit_before_any_request(limit):
    client = SimpleNamespace(
        get_object=lambda **_kw: pytest.fail("must not call the object store")
    )
    store = RawObjectStore(client=client, bucket="raw")

    with pytest.raises(ValueError):
        store.open_stream("k", max_bytes=limit)


def test_open_stream_other_client_errors_are_safe_store_errors():
    store, client = make_store()
    client.get_error = client_error("AccessDenied")

    with pytest.raises(ObjectStoreError) as error:
        store.open_stream("k", max_bytes=10)

    assert not isinstance(error.value, ObjectNotFound)
    assert str(error.value) == "object store unavailable"
    assert "private provider text" not in str(error.value)


def test_open_stream_unexpected_errors_are_safe_store_errors():
    store, client = make_store()
    client.get_error = ConnectionResetError("private socket detail")

    with pytest.raises(ObjectStoreError) as error:
        store.open_stream("k", max_bytes=10)

    assert "private socket detail" not in str(error.value)


@pytest.mark.parametrize("length", [None, -1, "5"])
def test_open_stream_without_a_valid_content_length_fails_and_closes(length):
    store, client = make_store()
    client.objects["k"] = (b"abc", "audio/mp4")
    client.response_overrides = {"ContentLength": length}

    with pytest.raises(ObjectStoreError):
        store.open_stream("k", max_bytes=10)

    assert client.bodies[0].closed is True


def test_open_stream_defaults_missing_content_type():
    store, client = make_store()
    client.objects["k"] = (b"abc", "audio/mp4")
    client.response_overrides = {"ContentType": None}

    assert store.open_stream("k", max_bytes=10).content_type == "application/octet-stream"


def test_iter_chunks_rejects_a_body_longer_than_declared_and_closes():
    store, client = make_store()
    client.objects["k"] = (b"x" * 100, "audio/mp4")
    client.response_overrides = {"ContentLength": 50}

    stream = store.open_stream("k", max_bytes=100)
    with pytest.raises(ObjectStoreError):
        list(stream.iter_chunks(20))

    assert client.bodies[0].closed is True


def test_iter_chunks_mid_stream_failure_is_safe_and_closes():
    store, client = make_store()
    client.objects["k"] = (b"x" * 100, "audio/mp4")
    client.body_factory = lambda data: FakeBody(data, fail_after=1)

    stream = store.open_stream("k", max_bytes=100)
    with pytest.raises(ObjectStoreError) as error:
        list(stream.iter_chunks(10))

    assert "private socket detail" not in str(error.value)
    assert client.bodies[0].closed is True


def test_iter_chunks_rejects_non_positive_chunk_size():
    store, client = make_store()
    client.objects["k"] = (b"abc", "audio/mp4")
    stream = store.open_stream("k", max_bytes=10)

    with pytest.raises(ValueError):
        next(stream.iter_chunks(0))
    stream.close()
