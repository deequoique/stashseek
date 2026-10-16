"""Small trusted identity boundary shared by external-login adapters."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Protocol


class ProviderError(Exception):
    """Deliberately contains neither provider text nor credential-bearing URLs."""

    def __init__(self) -> None:
        super().__init__("external_provider_unavailable")


@dataclass(frozen=True)
class ProviderAuthorization:
    authorization_url: str = field(repr=False)
    material: dict[str, Any] = field(repr=False)


@dataclass(frozen=True)
class ExternalPrincipal:
    namespace: str
    subject: str = field(repr=False)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.namespace, str)
            or not 1 <= len(self.namespace) <= 512
            or not isinstance(self.subject, str)
            or not 1 <= len(self.subject) <= 512
            or self.subject != self.subject.strip()
        ):
            raise ProviderError()


class ExternalProvider(Protocol):
    name: str
    application_id: str
    configuration_id: str

    def begin(self, *, redirect_uri: str, state: str) -> ProviderAuthorization: ...

    def complete(
        self, *, redirect_uri: str, code: str, material: dict[str, Any]
    ) -> ExternalPrincipal: ...


def configuration_digest(*values: str) -> str:
    """Bind pending proofs to exact private configuration without storing it."""
    encoded = json.dumps(values, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()
