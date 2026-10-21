"""Server-only website sign-in adapters; no mailbox or profile synchronization."""

from app.external_providers.base import (
    ExternalPrincipal,
    ExternalProvider,
    ProviderAuthorization,
    ProviderError,
)
from app.external_providers.registry import build_external_providers

__all__ = [
    "ExternalPrincipal", "ExternalProvider", "ProviderAuthorization", "ProviderError",
    "build_external_providers",
]
