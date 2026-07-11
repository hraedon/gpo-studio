"""Identity abstraction for the actor performing a workspace mutation.

In v0.1 the actor is claimed (untrusted) from the request body. This module
establishes the interface that trusted authentication middleware will satisfy
in multi-user deployments. It does not change v0.1 behaviour: the API still
accepts the actor string from the request body and wraps it in a
``ClaimedIdentity``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@runtime_checkable
class Identity(Protocol):
    """Identity of the actor performing a mutation.

    In v0.1, identity is claimed (untrusted) from the request body.
    Future deployments will provide trusted identity from authentication
    middleware (OIDC, integrated Windows auth, etc.).
    """

    @property
    def actor(self) -> str:
        """Human-readable actor identifier for audit records."""
        ...

    @property
    def is_trusted(self) -> bool:
        """True when identity is derived from trusted authentication.

        v0.1 always returns False (claimed identity from request body).
        """
        ...

    @property
    def source(self) -> str:
        """Description of how identity was established.

        Examples: "request-body", "oidc", "windows-auth".
        """
        ...


@dataclass(frozen=True, slots=True)
class ClaimedIdentity:
    """Untrusted identity claimed from the request body (v0.1 local mode)."""

    _actor: str
    _source: str = "request-body"

    @property
    def actor(self) -> str:
        return self._actor

    @property
    def is_trusted(self) -> bool:
        return False

    @property
    def source(self) -> str:
        return self._source


def claimed_identity(actor: str) -> ClaimedIdentity:
    """Create an untrusted claimed identity for v0.1 local mode."""
    if not actor or not actor.strip():
        raise ValueError("actor must be a non-empty string")
    return ClaimedIdentity(_actor=actor.strip())
