"""Credential-free protocol tests, including real SDK/JOSE verification."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.external_providers.base import ProviderError
from app.external_providers.http import ProviderHTTP
from app.external_providers.microsoft import MicrosoftProvider
from app.external_providers.qq import QQProvider, _qq_response
from app.external_providers.wechat import WeChatWebsiteProvider

CALLBACK = "https://stashseek.test/api/v1/auth/external/test/callback"


def _http(host, handler):
    return ProviderHTTP(frozenset({host}), transport=httpx.MockTransport(handler))


def test_qq_identity_scope_and_safe_requests(caplog):
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path.endswith("/token"):
            assert request.url.params["redirect_uri"] == CALLBACK
            return httpx.Response(200, json={"access_token": "private-access", "refresh_token": "discard-me"})
        return httpx.Response(200, text='callback( {"client_id":"123","openid":"User-ID"} );')

    provider = QQProvider("123", "private-app-key", http=_http("graph.qq.com", handler))
    auth = provider.begin(redirect_uri=CALLBACK, state="browser-state")
    assert parse_qs(urlsplit(auth.authorization_url).query)["state"] == ["browser-state"]
    with caplog.at_level(logging.DEBUG):
        identity = provider.complete(redirect_uri=CALLBACK, code="private-code", material={})
    assert (identity.namespace, identity.subject) == ("https://graph.qq.com", "User-ID")
    assert len(requests) == 2
    assert all(secret not in caplog.text for secret in ("private-access", "private-app-key", "private-code"))
    assert not hasattr(provider, "access_token")


@pytest.mark.parametrize("response", [
    '{"client_id":"other","openid":"same"}',
    '{"client_id":"123","openid":"same","openid":"different"}',
    'evil({"client_id":"123","openid":"same"})',
    'callback({"client_id":"123","openid":"same"}); doSomething()',
])
def test_qq_rejects_wrong_client_or_ambiguous_envelope(response):
    def handler(request):
        return httpx.Response(200, text=(
            "access_token=token&expires_in=60" if request.url.path.endswith("/token") else response
        ))
    provider = QQProvider("123", "secret", http=_http("graph.qq.com", handler))
    with pytest.raises(ProviderError, match="^external_provider_unavailable$"):
        provider.complete(redirect_uri=CALLBACK, code="code", material={})


@pytest.mark.parametrize("response", [
    "access_token=one&access_token=two",
    "access_token=one&access_token=",
    "access_token=&access_token=two",
])
def test_qq_rejects_duplicate_form_fields(response):
    with pytest.raises(ProviderError):
        _qq_response(response)


def test_wechat_website_subject_is_scoped_and_not_unionid():
    def handler(request):
        if request.url.path.endswith("access_token"):
            return httpx.Response(200, json={
                "access_token": "access", "openid": "openid", "scope": "snsapi_login",
                "unionid": "shared-not-an-owner",
            })
        return httpx.Response(200, json={"openid": "openid", "unionid": "shared-not-an-owner"})
    provider = WeChatWebsiteProvider("wx-app", "secret", http=_http("api.weixin.qq.com", handler))
    auth = provider.begin(redirect_uri=CALLBACK, state="state")
    assert urlsplit(auth.authorization_url).path == "/connect/qrconnect"
    assert parse_qs(urlsplit(auth.authorization_url).query)["scope"] == ["snsapi_login"]
    result = provider.complete(redirect_uri=CALLBACK, code="code", material={})
    assert (result.namespace, result.subject) == ("https://open.weixin.qq.com", "openid")


@pytest.mark.parametrize("token,identity", [
    ({"errcode": 40029, "errmsg": "untrusted detail"}, {}),
    ({"access_token": "a", "openid": "x", "scope": "snsapi_base"}, {"openid": "x"}),
    ({"access_token": "a", "openid": "x", "scope": "snsapi_login"}, {"openid": "y"}),
])
def test_wechat_rejects_invalid_token_permission_or_subject(token, identity):
    http = _http("api.weixin.qq.com", lambda request: httpx.Response(
        200, json=token if request.url.path.endswith("access_token") else identity,
    ))
    with pytest.raises(ProviderError):
        WeChatWebsiteProvider("wx-app", "secret", http=http).complete(
            redirect_uri=CALLBACK, code="code", material={},
        )


@pytest.fixture(scope="module")
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _microsoft(signing_key, *, claims_patch=None, bad_signature=False, rotate=False, tenant="common",
               key_issuer="https://login.microsoftonline.com/{tenantid}/v2.0"):
    current_tenant = "00112233-4455-6677-8899-aabbccddeeff"
    issuer = f"https://login.microsoftonline.com/{current_tenant}/v2.0"
    public = jwt.algorithms.RSAAlgorithm.to_jwk(signing_key.public_key(), as_dict=True)
    public.update(kid="current", use="sig")
    if key_issuer is not None:
        public["issuer"] = key_issuer
    state = {"jwks_requests": 0}

    def handler(request):
        if request.url.path.endswith("openid-configuration"):
            return httpx.Response(200, json={
                "authorization_endpoint": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
                "token_endpoint": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
                "issuer": "https://login.microsoftonline.com/{tenantid}/v2.0",
                "jwks_uri": "https://login.microsoftonline.com/common/discovery/v2.0/keys",
            })
        if request.url.path.endswith("/keys"):
            state["jwks_requests"] += 1
            keys = [dict(public, kid="previous")] if rotate and state["jwks_requests"] == 1 else [public]
            return httpx.Response(200, json={"keys": keys})
        assert request.url.path.endswith("/token")
        form = parse_qs(request.content.decode())
        verifier = form["code_verifier"][0]
        import base64
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        assert challenge == state["challenge"]
        now = int(time.time())
        claims = dict(iss=issuer, aud="client-id", sub="stable-subject", tid=current_tenant,
                      nonce=state["nonce"], iat=now, nbf=now, exp=now + 3600,
                      email="not-an-ownership-proof@example.test")
        claims.update(claims_patch or {})
        if claims.get("sub") is None:
            del claims["sub"]
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048) if bad_signature else signing_key
        raw = jwt.encode(claims, key, algorithm="RS256", headers={"kid": "current"})
        return httpx.Response(200, json={"id_token": raw, "token_type": "Bearer", "expires_in": 3600})

    provider = MicrosoftProvider("client-id", "client-secret", tenant=tenant,
                                 http=_http("login.microsoftonline.com", handler))
    auth = provider.begin(redirect_uri=CALLBACK, state="our-state")
    params = parse_qs(urlsplit(auth.authorization_url).query)
    state.update(nonce=params["nonce"][0], challenge=params["code_challenge"][0])
    assert "offline_access" not in params["scope"][0].split()
    assert "openid" in params["scope"][0].split()
    return provider, auth, state


def test_microsoft_real_sdk_pkce_and_signed_token(signing_key):
    provider, auth, state = _microsoft(signing_key)
    identity = provider.complete(redirect_uri=CALLBACK, code="our-code", material=auth.material)
    assert identity.subject == "stable-subject"
    assert identity.namespace.endswith("/00112233-4455-6677-8899-aabbccddeeff/v2.0")
    assert state["jwks_requests"] == 1


@pytest.mark.parametrize("patch", [
    {"aud": "another-app"}, {"iss": "https://attacker.invalid"}, {"nonce": "wrong"},
    {"exp": 1}, {"nbf": int(time.time()) + 3600}, {"sub": None},
    {"tid": "https://attacker.invalid/metadata"},
])
def test_microsoft_rejects_invalid_signed_claims(signing_key, patch):
    provider, auth, _ = _microsoft(signing_key, claims_patch=patch)
    with pytest.raises(ProviderError, match="^external_provider_unavailable$"):
        provider.complete(redirect_uri=CALLBACK, code="code", material=auth.material)


def test_microsoft_rejects_wrong_signature(signing_key):
    provider, auth, _ = _microsoft(signing_key, bad_signature=True)
    with pytest.raises(ProviderError):
        provider.complete(redirect_uri=CALLBACK, code="code", material=auth.material)


def test_microsoft_refreshes_public_keys_once_for_unknown_kid(signing_key):
    provider, auth, state = _microsoft(signing_key, rotate=True)
    assert provider.complete(redirect_uri=CALLBACK, code="code", material=auth.material).subject == "stable-subject"
    assert state["jwks_requests"] == 2


@pytest.mark.parametrize("key_issuer", [
    None, 7,
    "https://login.microsoftonline.com/9188040d-6c67-4c5b-b112-36a304b66dad/v2.0",
    "https://attacker.invalid/{tenantid}/v2.0",
])
def test_microsoft_requires_signing_key_issuer_scope(signing_key, key_issuer):
    provider, auth, _ = _microsoft(signing_key, key_issuer=key_issuer)
    with pytest.raises(ProviderError, match="^external_provider_unavailable$"):
        provider.complete(redirect_uri=CALLBACK, code="code", material=auth.material)


def test_provider_http_bounds_hosts_redirects_and_response_size():
    calls = []
    http = _http("graph.qq.com", lambda request: (calls.append(request) or httpx.Response(302, headers={"Location": "https://attacker.invalid"})))
    with pytest.raises(ProviderError):
        http.get("https://graph.qq.com/oauth2.0/me")
    assert len(calls) == 1
    with pytest.raises(ProviderError):
        http.get("https://attacker.invalid/")
    assert len(calls) == 1
    huge = _http("graph.qq.com", lambda request: httpx.Response(200, content=b"x" * (ProviderHTTP.MAX_BYTES + 1)))
    with pytest.raises(ProviderError):
        huge.get("https://graph.qq.com/oauth2.0/me")
