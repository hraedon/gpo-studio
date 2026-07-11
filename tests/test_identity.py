from __future__ import annotations

import dataclasses

import pytest

from gpo_studio.identity import Identity, claimed_identity


def test_claimed_identity_actor() -> None:
    ident = claimed_identity("alice")
    assert ident.actor == "alice"


def test_claimed_identity_is_trusted_false() -> None:
    ident = claimed_identity("alice")
    assert ident.is_trusted is False


def test_claimed_identity_source() -> None:
    ident = claimed_identity("alice")
    assert ident.source == "request-body"


def test_claimed_identity_rejects_empty() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        claimed_identity("")


def test_claimed_identity_rejects_whitespace() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        claimed_identity("   ")


def test_claimed_identity_strips() -> None:
    ident = claimed_identity("  alice  ")
    assert ident.actor == "alice"


def test_identity_protocol_satisfied() -> None:
    ident = claimed_identity("alice")
    assert isinstance(ident, Identity)


def test_claimed_identity_is_frozen() -> None:
    ident = claimed_identity("alice")
    with pytest.raises(dataclasses.FrozenInstanceError):
        ident._actor = "mallory"  # type: ignore[misc]
