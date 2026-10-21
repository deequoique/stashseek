"""Public discovery/proof/gate contracts; no provider, queue or configured DB."""

import json
import subprocess

import pytest
from pydantic import ValidationError

from app.api.bilibili_schemas import BilibiliPartsBatchRequest
from app.bilibili.gate import RedisPublicGate
from app.bilibili.public import BilibiliPart, BilibiliPublicClient, BilibiliPublicError, BoundedPublicHTTP, parse_object, parse_video_input
from app.bilibili.refs import PartRefError, PartRefSigner
from app.bilibili.service import BilibiliService
from app.channels.types import UserScope
from app.ingest.submission import BatchSaveResult, ItemReference, prepare_submission


BV = "BV1xx411c7mD"


class HTTP:
    def __init__(self, value):
        self.value, self.calls = value, []

    def get_json(self, endpoint, params):
        self.calls.append((endpoint, params))
        return self.value


def metadata(parts=2):
    return {"code": 0, "data": {"bvid": BV, "title": "Title", "pages": [
        {"cid": 100 + p, "page": p, "part": f"Part {p}", "duration": p * 20}
        for p in range(1, parts + 1)
    ]}}


@pytest.mark.parametrize("value,expected", [(BV, (BV, None)), ("bv" + BV[2:], (BV, None)), (f"https://www.bilibili.com/video/{BV}/?p=2&share_source=copy_web", (BV, 2))])
def test_explicit_preview_input(value, expected):
    assert parse_video_input(value) == expected


@pytest.mark.parametrize("suffix", ["?p=0", "?p=01", "?p=2&p=3", "?p=", "#", "?p=1001", "/evil", "?p=2%26p=3"])
def test_preview_rejects_unsafe_part_input(suffix):
    with pytest.raises(BilibiliPublicError, match="bilibili_invalid_url"):
        parse_video_input(f"https://www.bilibili.com/video/{BV}" + suffix)


@pytest.mark.parametrize("value", [f"http://www.bilibili.com/video/{BV}", f"https://www.bilibili.com:443/video/{BV}", f"https://user@www.bilibili.com/video/{BV}", f"https://evil.test/video/{BV}", f"标题 https://www.bilibili.com/video/{BV}", f"https://space.bilibili.com/1/favlist?fid=2"])
def test_preview_never_accepts_supplied_host_or_share_prose(value):
    with pytest.raises(BilibiliPublicError):
        parse_video_input(value)


def test_get_parts_projects_only_fixed_bounded_metadata():
    http = HTTP(metadata())
    result = BilibiliPublicClient(http).get_parts(BV)
    assert http.calls == [("parts", {"bvid": BV})]
    assert result.resolve_part("102") == BilibiliPart(BV, "102", 2, "Part 2", 40)
    with pytest.raises(BilibiliPublicError, match="source_unavailable"):
        result.resolve_part("999")


@pytest.mark.parametrize("change", [
    lambda x: x.update(code=-403), lambda x: x["data"].update(bvid="BV1QHAzzHEv8"),
    lambda x: x["data"]["pages"][0].update(cid=True),
    lambda x: x["data"]["pages"][0].update(cid="001"),
    lambda x: x["data"]["pages"][1].update(cid=101),
    lambda x: x["data"]["pages"][0].update(page=True),
    lambda x: x["data"]["pages"][0].update(page=2),
    lambda x: x["data"]["pages"][0].update(duration=1.2),
    lambda x: x["data"].update(pages=[]),
])
def test_unverifiable_map_has_no_selectable_fallback(change):
    value = metadata()
    change(value)
    with pytest.raises(BilibiliPublicError):
        BilibiliPublicClient(HTTP(value)).get_parts(BV)


def test_large_map_or_body_not_silently_truncated():
    with pytest.raises(BilibiliPublicError, match="bilibili_limit_exceeded"):
        BilibiliPublicClient(HTTP(metadata(1001))).get_parts(BV)
    with pytest.raises(BilibiliPublicError, match="bilibili_limit_exceeded"):
        parse_object(b" " * (1024 * 1024 + 1))
    for raw in (b'{}{}', b'{"code":0,"code":1}', b'[]'):
        with pytest.raises(BilibiliPublicError):
            parse_object(raw)


def test_public_child_has_total_timeout_and_redacted_failure(monkeypatch):
    calls = []
    def timeout(command, **kwargs):
        calls.append((command, kwargs))
        raise subprocess.TimeoutExpired(command, kwargs["timeout"], output=b"private provider body")
    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(BilibiliPublicError) as caught:
        BoundedPublicHTTP().get_json("parts", {"bvid": BV})
    assert str(caught.value) == "bilibili_unavailable"
    assert calls[0][0][-2:] == ["-m", "app.bilibili.fetch_public"]
    assert calls[0][1]["timeout"] == 15
    assert calls[0][1]["stderr"] is subprocess.DEVNULL


def test_refs_opaque_tenant_bound_bounded_and_expire():
    clock = [1000]
    signer = PartRefSigner("s" * 32, clock=lambda: clock[0])
    part = BilibiliPart(BV, "102", 2)
    proof = signer.issue(987654, part)
    assert len(proof) <= 1024
    import base64
    descriptor = json.loads(base64.urlsafe_b64decode(proof.split(".")[0] + "=="))
    assert set(descriptor) == {"v", "bvid", "cid", "page", "expires_at"}
    assert "987654" not in json.dumps(descriptor)
    assert signer.verify(987654, proof) == part
    for tenant, ref in [(12, proof), (987654, proof + "x"), (987654, "x" * 1025), (987654, proof.replace(".", ".."))]:
        with pytest.raises(PartRefError, match="part_ref_invalid"):
            signer.verify(tenant, ref)
    clock[0] += 1800
    with pytest.raises(PartRefError, match="part_ref_expired"):
        signer.verify(987654, proof)


def test_signing_key_purpose_differs_from_raw_secret():
    signer = PartRefSigner("s" * 32, clock=lambda: 1000)
    import base64, hashlib, hmac
    proof = signer.issue(1, BilibiliPart(BV, "101", 1))
    payload, mac = proof.split(".")
    assert base64.urlsafe_b64decode(mac + "==") != hmac.digest(b"s" * 32, base64.urlsafe_b64decode(payload + "=="), hashlib.sha256)


def test_public_reference_expiry_exactly_matches_signed_descriptor():
    signer = PartRefSigner("s" * 32, clock=lambda: 1000)
    proof, expiry = signer.issue_with_expiry(1, BilibiliPart(BV, "101", 1))
    assert expiry == 2800
    assert signer.verify(1, proof).cid == "101"
    import base64
    descriptor = json.loads(base64.urlsafe_b64decode(proof.split(".")[0] + "=="))
    assert descriptor["expires_at"] == expiry


def test_import_prepares_ordered_failures_and_typed_sources_without_refetch():
    signer = PartRefSigner("s" * 32, clock=lambda: 1000)
    scope = UserScope(11)
    captured = []
    class Submission:
        def submit_prepared(self, tenant, prepared, **kwargs):
            captured.append((tenant, prepared, kwargs))
            return BatchSaveResult(())
    service = BilibiliService(None, Submission(), None, signer, None)
    body = BilibiliPartsBatchRequest(selections=[{"part_ref": "tamper"}, {"part_ref": signer.issue(11, BilibiliPart(BV, "102", 2)), "allow_legacy_overlap": True}])
    service.submit_parts(scope, body.selections, why_saved="#study", request_key="stable", publish_budget_seconds=2)
    tenant, prepared, options = captured[0]
    assert tenant == scope and options["request_key"] == "stable"
    assert prepared.items[0].failure.status == "part_ref_invalid"
    assert prepared.items[1].reference == ItemReference("bilibili", BV + "_cid102", f"https://www.bilibili.com/video/{BV}?p=2", BV, "102", 2, True)
    assert prepare_submission([BV]).items[0].reference.bilibili_cid is None


@pytest.mark.parametrize("consent", [1, 0, "true", "false", None])
def test_strict_consent_and_selection_limit(consent):
    with pytest.raises(ValidationError):
        BilibiliPartsBatchRequest(selections=[{"part_ref": "ref", "allow_legacy_overlap": consent}])


def test_batch_extras_and_eleven_rejected_before_service():
    for body in ({"selections": [{"part_ref": "ref", "cid": "101"}]}, {"selections": [{"part_ref": "ref"}] * 11}, {"selections": []}):
        with pytest.raises(ValidationError):
            BilibiliPartsBatchRequest.model_validate(body)


class Redis:
    def __init__(self):
        self.rate, self.leases, self.keys = 0, set(), []
    def eval(self, script, count, key, token):
        self.keys.append(key)
        if script == RedisPublicGate._RATE:
            if self.rate >= 30:
                return 0
            self.rate += 1
            return 1
        if script == RedisPublicGate._LEASE:
            if len(self.leases) == 2:
                return 0
            self.leases.add(token)
            return 1
        assert script == RedisPublicGate._RELEASE
        self.leases.discard(token)
        return 1


def test_shared_gate_rate_and_token_release_on_error():
    redis = Redis()
    first, second = RedisPublicGate(redis, "s" * 32), RedisPublicGate(redis, "s" * 32)
    for _ in range(30):
        first.admit(987654)
    with pytest.raises(BilibiliPublicError, match="bilibili_rate_limited"):
        second.admit(987654)
    assert not any("987654" in key for key in redis.keys)
    with first.upstream(), second.upstream():
        with pytest.raises(BilibiliPublicError, match="bilibili_busy"):
            first.upstream().__enter__()
    assert not redis.leases
    with pytest.raises(RuntimeError):
        with first.upstream():
            raise RuntimeError("cancelled")
    assert not redis.leases


def test_gate_unavailable_fails_closed():
    class Broken:
        def eval(self, *args):
            raise RuntimeError("private redis connection")
    gate = RedisPublicGate(Broken(), "s" * 32)
    for run in (lambda: gate.admit(1), lambda: gate.upstream().__enter__()):
        with pytest.raises(BilibiliPublicError) as caught:
            run()
        assert str(caught.value) == "bilibili_unavailable"
