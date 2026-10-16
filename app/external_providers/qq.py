"""QQ Connect website sign-in with an application-scoped OpenID."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, urlencode

from app.external_providers.base import (
    ExternalPrincipal, ProviderAuthorization, ProviderError, configuration_digest,
)
from app.external_providers.http import ProviderHTTP, object_json


def _qq_response(text: str) -> dict[str, Any]:
    """Parse only documented JSON, form, or literal callback(JSON) envelopes."""
    value = text.strip()
    if value.startswith("{"):
        return object_json(value)
    if value.startswith("callback"):
        match = re.fullmatch(r"callback\(\s*(\{.*\})\s*\)\s*;?", value, re.DOTALL)
        if match is None:
            raise ProviderError()
        return object_json(match[1])
    try:
        fields = parse_qs(value, strict_parsing=True, max_num_fields=16, keep_blank_values=True)
    except ValueError:
        raise ProviderError() from None
    if not fields or any(len(values) != 1 for values in fields.values()):
        raise ProviderError()
    return {key: values[0] for key, values in fields.items()}


class QQProvider:
    name = "qq"
    NAMESPACE = "https://graph.qq.com"

    def __init__(self, app_id: str, app_key: str, *, http: ProviderHTTP | None = None) -> None:
        if not app_id or not app_key:
            raise ValueError("QQ application configuration is incomplete")
        self.application_id = app_id
        self.configuration_id = configuration_digest(self.name, app_id, app_key)
        self._secret = app_key
        self._http = http or ProviderHTTP(frozenset({"graph.qq.com"}))

    def begin(self, *, redirect_uri: str, state: str) -> ProviderAuthorization:
        query = urlencode({
            "response_type": "code", "client_id": self.application_id,
            "redirect_uri": redirect_uri, "state": state, "scope": "get_user_info",
        })
        return ProviderAuthorization(f"{self.NAMESPACE}/oauth2.0/authorize?{query}", {})

    def complete(
        self, *, redirect_uri: str, code: str, material: dict[str, Any]
    ) -> ExternalPrincipal:
        try:
            token = _qq_response(self._http.get(
                f"{self.NAMESPACE}/oauth2.0/token", params={
                    "grant_type": "authorization_code", "client_id": self.application_id,
                    "client_secret": self._secret, "code": code,
                    "redirect_uri": redirect_uri, "fmt": "json",
                },
            ).text)
            access = token.get("access_token")
            if "error" in token or not isinstance(access, str) or not 1 <= len(access) <= 8192:
                raise ProviderError()
            identity = _qq_response(self._http.get(
                f"{self.NAMESPACE}/oauth2.0/me",
                params={"access_token": access, "fmt": "json"},
            ).text)
            subject = identity.get("openid")
            if (
                "error" in identity
                or identity.get("client_id") != self.application_id
                or not isinstance(subject, str)
                or not 1 <= len(subject) <= 128
            ):
                raise ProviderError()
            return ExternalPrincipal(self.NAMESPACE, subject)
        except ProviderError:
            raise
        except Exception:
            raise ProviderError() from None
