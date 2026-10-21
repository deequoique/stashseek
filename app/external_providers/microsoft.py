"""Microsoft confidential-client code flow and explicit ID-token validation."""

from __future__ import annotations

import hmac
import re
import threading
import time
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import jwt

from app.external_providers.base import (
    ExternalPrincipal, ProviderAuthorization, ProviderError, configuration_digest,
)
from app.external_providers.http import ProviderHTTP, object_json

_PERSONAL_TENANT = "9188040d-6c67-4c5b-b112-36a304b66dad"
_HOST = "https://login.microsoftonline.com"


class MicrosoftProvider:
    name = "microsoft"

    def __init__(
        self, client_id: str, client_secret: str, *, tenant: str = "common",
        http: ProviderHTTP | None = None,
        application_factory: Callable[..., Any] | None = None,
    ) -> None:
        if not client_id or not client_secret:
            raise ValueError("Microsoft application configuration is incomplete")
        if tenant not in {"common", "organizations", "consumers"}:
            try:
                tenant = str(UUID(tenant))
            except (ValueError, TypeError, AttributeError):
                raise ValueError("Unsupported Microsoft tenant configuration") from None
        self.application_id = client_id
        self.configuration_id = configuration_digest(self.name, client_id, client_secret, tenant)
        self._secret, self._tenant = client_secret, tenant
        self._http = http or ProviderHTTP(frozenset({"login.microsoftonline.com"}))
        self._application_factory = application_factory
        self._keys: dict[str, Any] | None = None
        self._keys_until = 0.0
        self._keys_lock = threading.Lock()

    def _application(self):
        if self._application_factory is None:
            from msal import ConfidentialClientApplication
            factory = ConfidentialClientApplication
        else:
            factory = self._application_factory
        # A fresh SDK token cache per flow operation prevents login-only tokens
        # from becoming retained application credentials.
        return factory(
            self.application_id, client_credential=self._secret,
            authority=f"{_HOST}/{self._tenant}", http_client=self._http,
            instance_discovery=False, exclude_scopes=["offline_access"],
            enable_pii_log=False, timeout=10,
        )

    def begin(self, *, redirect_uri: str, state: str) -> ProviderAuthorization:
        try:
            flow = self._application().initiate_auth_code_flow(
                scopes=[], redirect_uri=redirect_uri, state=state, response_mode="query",
            )
            url = flow.get("auth_uri")
            if not isinstance(url, str):
                raise ProviderError()
            parsed = urlsplit(url)
            params = parse_qs(parsed.query)
            nonce = params.get("nonce", [])
            if (
                parsed.scheme != "https" or parsed.hostname != "login.microsoftonline.com"
                or parsed.port not in (None, 443) or parsed.username or parsed.password
                or params.get("state") != [state]
                or params.get("response_type") != ["code"]
                or params.get("code_challenge_method") != ["S256"]
                or len(nonce) != 1 or not nonce[0]
            ):
                raise ProviderError()
            return ProviderAuthorization(url, {"flow": flow, "expected_nonce": nonce[0]})
        except ProviderError:
            raise
        except Exception:
            raise ProviderError() from None

    def _jwks(self, *, force: bool = False) -> dict[str, Any]:
        with self._keys_lock:
            if force or self._keys is None or time.monotonic() >= self._keys_until:
                value = object_json(self._http.get(f"{_HOST}/common/discovery/v2.0/keys").text)
                if not isinstance(value.get("keys"), list) or not 1 <= len(value["keys"]) <= 64:
                    raise ProviderError()
                self._keys, self._keys_until = value, time.monotonic() + 3600
            return self._keys

    def _validate(self, raw: str, expected_nonce: str) -> ExternalPrincipal:
        if not isinstance(raw, str) or not 1 <= len(raw) <= 32 * 1024:
            raise ProviderError()
        try:
            header = jwt.get_unverified_header(raw)
            if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
                raise ProviderError()
            if not 1 <= len(header["kid"]) <= 200:
                raise ProviderError()
            # This unverified claim selects no URL/owner: it only constructs an
            # expected issuer beneath a fixed authority, then signature/claims
            # are verified before using the subject.
            hint = jwt.decode(raw, options={"verify_signature": False})
            tenant = str(UUID(hint.get("tid", "")))
            if (
                self._tenant == "organizations" and tenant == _PERSONAL_TENANT
                or self._tenant == "consumers" and tenant != _PERSONAL_TENANT
                or self._tenant not in {"common", "organizations", "consumers"} and tenant != self._tenant
            ):
                raise ProviderError()
            issuer = f"{_HOST}/{tenant}/v2.0"
            candidates: list[dict[str, Any]] = []
            for force in (False, True):
                candidates = [key for key in self._jwks(force=force)["keys"] if (
                    isinstance(key, dict) and key.get("kid") == header["kid"]
                    and key.get("kty") == "RSA" and key.get("use", "sig") == "sig"
                    and key.get("alg", "RS256") == "RS256"
                    and isinstance(key.get("issuer"), str)
                    and key["issuer"].replace("{tenantid}", tenant) == issuer
                )]
                if candidates:
                    break
            if len(candidates) != 1:
                raise ProviderError()
            claims = jwt.decode(
                raw, jwt.PyJWK.from_dict(candidates[0], algorithm="RS256").key,
                algorithms=["RS256"], audience=self.application_id, issuer=issuer,
                leeway=60, options={
                    "require": ["iss", "aud", "sub", "tid", "exp", "iat", "nbf", "nonce"],
                    "strict_aud": True,
                },
            )
            nonce = claims.get("nonce")
            if (
                claims.get("tid") != tenant
                or not isinstance(nonce, str)
                or not hmac.compare_digest(nonce, expected_nonce)
            ):
                raise ProviderError()
            return ExternalPrincipal(issuer, claims["sub"])
        except ProviderError:
            raise
        except Exception:
            raise ProviderError() from None

    def complete(
        self, *, redirect_uri: str, code: str, material: dict[str, Any]
    ) -> ExternalPrincipal:
        try:
            flow, nonce = material.get("flow"), material.get("expected_nonce")
            if (
                not isinstance(flow, dict) or flow.get("redirect_uri") != redirect_uri
                or not isinstance(flow.get("state"), str)
                or not isinstance(nonce, str) or not 1 <= len(nonce) <= 512
            ):
                raise ProviderError()
            result = self._application().acquire_token_by_auth_code_flow(
                flow, {"code": code, "state": flow["state"]},
            )
            if "error" in result:
                raise ProviderError()
            return self._validate(result.get("id_token"), nonce)
        except ProviderError:
            raise
        except Exception:
            raise ProviderError() from None
