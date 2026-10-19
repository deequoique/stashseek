"""Signed temporary-audio download URLs."""

from urllib.parse import parse_qs, urlsplit

import pytest

from app.asr import signing
from app.asr.signing import (
    ALLOWED_AUDIO_EXTS,
    ASR_AUDIO_PREFIX,
    audio_object_key,
    new_audio_object_name,
    sign_audio_url,
    verify_audio_signature,
)

SECRET = "k" * 32
BASE = "https://stashseek.example.test"
NOW = 1_800_000_000.0
TTL = 7200
NAME = "0123456789abcdef0123456789abcdef.m4a"


def signed(name=NAME, *, secret=SECRET, now=NOW, ttl=TTL):
    url = sign_audio_url(
        base_url=BASE, name=name, secret=secret, ttl_seconds=ttl, now=now
    )
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    return url, parts, query["expires"][0], query["sig"][0]


def verify(*, name=NAME, expires, sig, secret=SECRET, now=NOW, max_ttl=TTL):
    return verify_audio_signature(
        name=name,
        expires=expires,
        sig=sig,
        secret=secret,
        max_ttl_seconds=max_ttl,
        now=now,
    )


def test_new_audio_object_name_is_random_hex_with_allowed_extension():
    first = new_audio_object_name("m4a")
    second = new_audio_object_name("m4a")

    assert first != second
    stem, ext = first.split(".")
    assert ext == "m4a"
    assert len(stem) == 32 and set(stem) <= set("0123456789abcdef")
    for allowed in ALLOWED_AUDIO_EXTS:
        assert new_audio_object_name(allowed).endswith(f".{allowed}")


@pytest.mark.parametrize("ext", ["exe", "", "M4A", ".m4a", "m4a/", "../m4a"])
def test_new_audio_object_name_rejects_unlisted_extension(ext):
    with pytest.raises(ValueError):
        new_audio_object_name(ext)


def test_audio_object_key_adds_private_prefix_after_validation():
    assert audio_object_key(NAME) == f"{ASR_AUDIO_PREFIX}{NAME}"
    assert ASR_AUDIO_PREFIX == "asr-tmp/"


@pytest.mark.parametrize(
    "name",
    [
        "../0123456789abcdef0123456789abcdef.m4a",
        "asr-tmp/0123456789abcdef0123456789abcdef.m4a",
        "0123456789ABCDEF0123456789ABCDEF.m4a",
        "0123456789abcdef0123456789abcde.m4a",
        "0123456789abcdef0123456789abcdef0.m4a",
        "0123456789abcdef0123456789abcdef.exe",
        "0123456789abcdef0123456789abcdef",
        "0123456789abcdef0123456789abcdef.m4a\n",
        "0123456789abcdef0123456789abcdef.m4a/x",
        "",
    ],
)
def test_invalid_names_are_rejected_for_key_and_signing(name):
    with pytest.raises(ValueError):
        audio_object_key(name)
    with pytest.raises(ValueError):
        sign_audio_url(
            base_url=BASE, name=name, secret=SECRET, ttl_seconds=TTL, now=NOW
        )


def test_sign_audio_url_shape():
    url, parts, expires, sig = signed()

    assert url.startswith(f"{BASE}/api/v1/asr-audio/{NAME}?expires=")
    assert parts.path == f"/api/v1/asr-audio/{NAME}"
    assert expires == str(int(NOW + TTL))
    assert len(sig) == 64 and set(sig) <= set("0123456789abcdef")
    assert SECRET not in url
    assert "asr-tmp" not in url


def test_valid_signature_verifies_until_expiry_inclusive():
    _url, _parts, expires, sig = signed()

    assert verify(expires=expires, sig=sig)
    assert verify(expires=expires, sig=sig, now=NOW + TTL - 1)
    assert verify(expires=expires, sig=sig, now=NOW + TTL)


def test_expired_signature_is_rejected():
    _url, _parts, expires, sig = signed()

    assert not verify(expires=expires, sig=sig, now=NOW + TTL + 1)
    assert not verify(expires=expires, sig=sig, now=NOW + TTL + 86_400)


def test_too_far_future_expiry_is_rejected_even_when_correctly_signed():
    # A leaked secret could mint a very long-lived URL; the verifier caps it.
    _url, _parts, expires, sig = signed(ttl=TTL + 3600)

    assert not verify(expires=expires, sig=sig, max_ttl=TTL)
    # The configured cap plus the 60 second skew allowance is still accepted.
    _url, _parts, near_expires, near_sig = signed(ttl=TTL + 60)
    assert verify(expires=near_expires, sig=near_sig, max_ttl=TTL)
    _url, _parts, over_expires, over_sig = signed(ttl=TTL + 61)
    assert not verify(expires=over_expires, sig=over_sig, max_ttl=TTL)


def test_tampered_signature_is_rejected():
    _url, _parts, expires, sig = signed()
    flipped = ("0" if sig[0] != "0" else "1") + sig[1:]

    assert not verify(expires=expires, sig=flipped)
    assert not verify(expires=expires, sig=sig[:-1])
    assert not verify(expires=expires, sig=sig + "0")
    assert not verify(expires=expires, sig="")
    assert not verify(expires=expires, sig=sig.upper())
    assert not verify(expires=expires, sig="é" * 64)


def test_tampered_expiry_or_name_is_rejected():
    _url, _parts, expires, sig = signed()
    other_name = "fedcba9876543210fedcba9876543210.m4a"

    assert not verify(expires=str(int(expires) + 1), sig=sig)
    assert not verify(name=other_name, expires=expires, sig=sig)
    assert not verify(name=NAME.replace(".m4a", ".mp3"), expires=expires, sig=sig)


def test_wrong_secret_is_rejected():
    _url, _parts, expires, sig = signed()

    assert not verify(expires=expires, sig=sig, secret="z" * 32)
    assert not verify(expires=expires, sig=sig, secret="")


@pytest.mark.parametrize(
    "expires",
    ["", "abc", "-1", "1.5", "１８００００００００", "1800000000 ", " 1800000000", "9" * 40, None, 1_800_000_000],
)
def test_malformed_expires_is_rejected_without_raising(expires):
    _url, _parts, _expires, sig = signed()

    assert verify(expires=expires, sig=sig) is False


@pytest.mark.parametrize("name", ["../etc/passwd", "", "x.m4a", None])
def test_bad_name_is_rejected_without_raising(name):
    _url, _parts, expires, sig = signed()

    assert verify(name=name, expires=expires, sig=sig) is False


def test_signature_is_domain_separated_hmac_over_name_and_expiry():
    import hashlib
    import hmac

    _url, _parts, expires, sig = signed()
    expected = hmac.new(
        SECRET.encode(),
        f"asr-audio.v1\n{NAME}\n{expires}".encode(),
        hashlib.sha256,
    ).hexdigest()

    assert sig == expected


def test_verification_uses_constant_time_comparison(monkeypatch):
    calls = []
    real = signing.hmac.compare_digest

    def spy(left, right):
        calls.append((left, right))
        return real(left, right)

    monkeypatch.setattr(signing.hmac, "compare_digest", spy)
    _url, _parts, expires, sig = signed()

    assert verify(expires=expires, sig=sig)
    assert len(calls) == 1


def test_sign_audio_url_rejects_empty_secret_and_non_positive_ttl():
    with pytest.raises(ValueError):
        sign_audio_url(base_url=BASE, name=NAME, secret="", ttl_seconds=TTL, now=NOW)
    with pytest.raises(ValueError):
        sign_audio_url(base_url=BASE, name=NAME, secret=SECRET, ttl_seconds=0, now=NOW)


def test_default_clock_is_wall_time(monkeypatch):
    monkeypatch.setattr(signing.time, "time", lambda: NOW)
    url = sign_audio_url(base_url=BASE, name=NAME, secret=SECRET, ttl_seconds=TTL)
    query = parse_qs(urlsplit(url).query)

    assert query["expires"] == [str(int(NOW + TTL))]
    assert verify_audio_signature(
        name=NAME,
        expires=query["expires"][0],
        sig=query["sig"][0],
        secret=SECRET,
        max_ttl_seconds=TTL,
    )
