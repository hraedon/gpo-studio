"""Detached provenance signatures for evidence packs (cairn, Plan 022).

A ``ProvenanceSignature`` is a detached Ed25519 signature over the SHA-256 of an
evidence pack's canonical JSON bytes (``canonical_pack_hash`` in
``gpo_studio.evidence``). The signature attests *who gathered* the pack and that
it is unmodified. It does **not** attest that the pack's classification claims
true; that is the separate, mechanical derivation gate in
``gpo_studio.evidence.derive_claims``.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, assert_never

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

Algorithm = Literal["Ed25519"]


class ProvenanceError(ValueError):
    """Invalid provenance signature, trust anchor, or signing operation."""


@dataclass(frozen=True, slots=True)
class ProvenanceSignature:
    """A detached provenance signature over an evidence pack hash."""

    key_id: str
    algorithm: Algorithm
    signature: bytes


@dataclass(frozen=True, slots=True)
class TrustAnchor:
    """An offline trust anchor: a public key, its identifier, and algorithm."""

    key_id: str
    public_key: bytes
    algorithm: Algorithm


def _as_mapping(raw: object, source: str) -> Mapping[str, Any]:
    if not isinstance(raw, dict):
        raise ProvenanceError(f"{source} must be a JSON object")
    return raw


def _required_str(data: Mapping[str, Any], key: str, label: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ProvenanceError(f"{label} must be a non-empty string")
    return value


def _public_key_class(algorithm: Algorithm) -> type[Ed25519PublicKey]:
    match algorithm:
        case "Ed25519":
            return Ed25519PublicKey
        case _:
            assert_never(algorithm)


def generate_keypair() -> tuple[bytes, bytes]:
    """Generate an Ed25519 keypair and return ``(private_key, public_key)`` raw bytes."""
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    private_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return private_bytes, public_bytes


def sign_pack(pack_hash: bytes, private_key: bytes, key_id: str) -> ProvenanceSignature:
    """Sign ``pack_hash`` with the Ed25519 private key and return a sidecar signature."""
    if len(private_key) != 32:
        raise ProvenanceError("private key must be 32 raw bytes")
    signer = Ed25519PrivateKey.from_private_bytes(private_key)
    signature = signer.sign(pack_hash)
    return ProvenanceSignature(key_id=key_id, algorithm="Ed25519", signature=signature)


def verify_provenance_signature(
    pack_hash: bytes, signature: ProvenanceSignature, anchor: TrustAnchor
) -> bool:
    """Verify ``signature`` over ``pack_hash`` against ``anchor``."""
    if signature.key_id != anchor.key_id:
        return False
    if signature.algorithm != anchor.algorithm:
        return False
    if signature.algorithm != "Ed25519":
        return False
    public_key_class = _public_key_class(signature.algorithm)
    try:
        public_key = public_key_class.from_public_bytes(anchor.public_key)
        public_key.verify(signature.signature, pack_hash)
    except InvalidSignature:
        return False
    return True


def serialize_signature(sig: ProvenanceSignature) -> bytes:
    """Serialize a provenance signature to the sidecar JSON format."""
    payload = {
        "key_id": sig.key_id,
        "algorithm": sig.algorithm,
        "signature": base64.b64encode(sig.signature).decode("ascii"),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def parse_signature(data: bytes) -> ProvenanceSignature:
    """Parse a sidecar JSON signature into a ``ProvenanceSignature``."""
    try:
        raw = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProvenanceError(f"invalid signature sidecar: {exc}") from exc

    mapping = _as_mapping(raw, "signature sidecar")
    key_id = _required_str(mapping, "key_id", "signature key_id")
    algorithm = mapping.get("algorithm")
    if algorithm != "Ed25519":
        raise ProvenanceError(f"unsupported signature algorithm: {algorithm}")
    encoded = _required_str(mapping, "signature", "signature value")
    try:
        signature = base64.b64decode(encoded, validate=True)
    except ValueError as exc:
        raise ProvenanceError(f"invalid base64 signature: {exc}") from exc
    return ProvenanceSignature(key_id=key_id, algorithm="Ed25519", signature=signature)


def save_trust_anchor(anchor: TrustAnchor, path: Path) -> None:
    """Write a trust anchor to a JSON file."""
    payload = {
        "key_id": anchor.key_id,
        "algorithm": anchor.algorithm,
        "public_key": base64.b64encode(anchor.public_key).decode("ascii"),
    }
    path.write_text(json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8")


def load_trust_anchor(path: Path) -> TrustAnchor:
    """Load a trust anchor from a JSON file."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProvenanceError(f"cannot load trust anchor {path}: {exc}") from exc

    mapping = _as_mapping(raw, "trust anchor")
    key_id = _required_str(mapping, "key_id", "trust anchor key_id")
    algorithm = mapping.get("algorithm")
    if algorithm != "Ed25519":
        raise ProvenanceError(f"unsupported trust anchor algorithm: {algorithm}")
    encoded = _required_str(mapping, "public_key", "trust anchor public_key")
    try:
        public_key = base64.b64decode(encoded, validate=True)
    except ValueError as exc:
        raise ProvenanceError(f"invalid base64 public key: {exc}") from exc
    if len(public_key) != 32:
        raise ProvenanceError("trust anchor public key must be 32 bytes")
    return TrustAnchor(key_id=key_id, public_key=public_key, algorithm="Ed25519")
