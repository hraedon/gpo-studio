"""Tests for the cairn provenance-signature module (Plan 022, WI-005)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from gpo_studio.evidence import canonical_pack_hash, load_pack
from gpo_studio.provenance import (
    ProvenanceError,
    ProvenanceSignature,
    TrustAnchor,
    generate_keypair,
    load_trust_anchor,
    parse_signature,
    save_trust_anchor,
    serialize_signature,
    sign_pack,
    verify_provenance_signature,
)

_RECORD_REGISTRY = {
    "capability": "registry-policy",
    "cse_guid": "{35378EAC-683F-11D2-A89A-00C04FBBCFA2}",
    "side": "both",
    "action": "set",
    "outcome": "pass",
    "classification": "verified-rw",
    "evidence_kind": "windows-side",
    "tool": "Set-GPRegistryValue",
    "ms_doc": "https://learn.microsoft.com/windows/win32/api/_grouppolicy/",
    "evidence_hash": "sha256:abc",
    "notes": "",
}


def _pack_dict() -> dict:
    return {
        "schema_version": 1,
        "pack_id": "provenance-test-pack",
        "generated_at": "2026-07-25T00:00:00Z",
        "source_commit": "e5e4c90",
        "operator": "gpstudio-lab",
        "redaction_verified": True,
        "licensing_complete": True,
        "estate": {
            "os": "Windows Server 2025",
            "build": "26100",
            "role": "DC",
            "forest": "ad.hraedon.com",
            "domain": "ad.hraedon.com",
            "dc": "mvmdc03",
            "gpmc_version": "10.0.26100",
        },
        "records": [_RECORD_REGISTRY],
        "content": [
            {
                "content_id": "admx-set",
                "classification": "hash-reference",
                "sha256": "sha256:deadbeef",
                "source_build": "26100",
                "regeneration_path": "%SystemRoot%\\PolicyDefinitions",
                "license_note": "Microsoft-copyrighted; referenced, not redistributed",
            }
        ],
    }


def _load_cli():
    project_root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location(
        "generate_public_matrix",
        project_root / "scripts" / "generate_public_matrix.py",
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["generate_public_matrix"] = mod
    spec.loader.exec_module(mod)
    return mod


# --- keypair and basic signing ------------------------------------------------


def test_generate_keypair_returns_32_byte_raw_keys() -> None:
    private_key, public_key = generate_keypair()
    assert len(private_key) == 32
    assert len(public_key) == 32


def test_sign_and_verify_round_trip() -> None:
    private_key, public_key = generate_keypair()
    pack_hash = b"hash-of-a-pack-000000000000000000000000000000"
    sig = sign_pack(pack_hash, private_key, key_id="test-key")
    anchor = TrustAnchor(key_id="test-key", public_key=public_key, algorithm="Ed25519")
    assert verify_provenance_signature(pack_hash, sig, anchor) is True


def test_tampered_pack_hash_fails_verification() -> None:
    private_key, public_key = generate_keypair()
    pack_hash = b"hash-of-a-pack-000000000000000000000000000000"
    sig = sign_pack(pack_hash, private_key, key_id="test-key")
    anchor = TrustAnchor(key_id="test-key", public_key=public_key, algorithm="Ed25519")
    tampered = b"tampered-hash-000000000000000000000000000000"
    assert verify_provenance_signature(tampered, sig, anchor) is False


def test_wrong_key_fails_verification() -> None:
    private_key, public_key = generate_keypair()
    _, other_public_key = generate_keypair()
    pack_hash = b"hash-of-a-pack-000000000000000000000000000000"
    sig = sign_pack(pack_hash, private_key, key_id="test-key")
    anchor = TrustAnchor(key_id="test-key", public_key=other_public_key, algorithm="Ed25519")
    assert verify_provenance_signature(pack_hash, sig, anchor) is False


def test_mismatched_key_id_fails_verification() -> None:
    private_key, public_key = generate_keypair()
    pack_hash = b"hash-of-a-pack-000000000000000000000000000000"
    sig = sign_pack(pack_hash, private_key, key_id="test-key")
    anchor = TrustAnchor(key_id="other-key", public_key=public_key, algorithm="Ed25519")
    assert verify_provenance_signature(pack_hash, sig, anchor) is False


# --- trust-anchor serialization ---------------------------------------------


def test_trust_anchor_load_save_round_trip(tmp_path: Path) -> None:
    _, public_key = generate_keypair()
    anchor = TrustAnchor(key_id="cairn-test", public_key=public_key, algorithm="Ed25519")
    path = tmp_path / "anchor.json"
    save_trust_anchor(anchor, path)
    loaded = load_trust_anchor(path)
    assert loaded == anchor


def test_load_trust_anchor_rejects_short_public_key(tmp_path: Path) -> None:
    path = tmp_path / "anchor.json"
    path.write_text(
        json.dumps({"key_id": "bad", "algorithm": "Ed25519", "public_key": "dGVzdA=="}),
        encoding="utf-8",
    )
    with pytest.raises(ProvenanceError, match="public key must be 32 bytes"):
        load_trust_anchor(path)


def test_load_trust_anchor_rejects_unsupported_algorithm(tmp_path: Path) -> None:
    path = tmp_path / "anchor.json"
    path.write_text(
        json.dumps(
            {
                "key_id": "bad",
                "algorithm": "RSA-2048",
                "public_key": "dGVzdA==",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ProvenanceError, match="unsupported trust anchor algorithm"):
        load_trust_anchor(path)


# --- sidecar signature serialization ----------------------------------------


def test_sidecar_signature_serialize_parse_round_trip() -> None:
    sig = ProvenanceSignature(
        key_id="cairn-test",
        algorithm="Ed25519",
        signature=b"\x00" * 64,
    )
    data = serialize_signature(sig)
    parsed = parse_signature(data)
    assert parsed == sig


def test_parse_signature_rejects_invalid_algorithm() -> None:
    data = json.dumps(
        {
            "key_id": "cairn-test",
            "algorithm": "RSA-2048",
            "signature": "dGVzdA==",
        }
    ).encode("utf-8")
    with pytest.raises(ProvenanceError, match="unsupported signature algorithm"):
        parse_signature(data)


def test_parse_signature_rejects_invalid_base64() -> None:
    data = json.dumps(
        {
            "key_id": "cairn-test",
            "algorithm": "Ed25519",
            "signature": "not-valid-base64!",
        }
    ).encode("utf-8")
    with pytest.raises(ProvenanceError, match="invalid base64 signature"):
        parse_signature(data)


def test_parse_signature_rejects_missing_key_id() -> None:
    data = json.dumps(
        {
            "algorithm": "Ed25519",
            "signature": "dGVzdA==",
        }
    ).encode("utf-8")
    with pytest.raises(ProvenanceError, match="signature key_id"):
        parse_signature(data)


# --- CLI integration --------------------------------------------------------


def _sign_pack_file(pack_path: Path, private_key: bytes, key_id: str) -> Path:
    pack = load_pack(pack_path)
    pack_hash = bytes.fromhex(canonical_pack_hash(pack))
    sig = sign_pack(pack_hash, private_key, key_id)
    sig_path = pack_path.with_suffix(pack_path.suffix + ".sig")
    sig_path.write_bytes(serialize_signature(sig))
    return sig_path


def _setup_signed_pack(tmp_path: Path, key_id: str = "cairn-test"):
    private_key, public_key = generate_keypair()
    pack_path = tmp_path / "pack.json"
    pack_path.write_text(json.dumps(_pack_dict()), encoding="utf-8")
    sig_path = _sign_pack_file(pack_path, private_key, key_id)
    anchor = TrustAnchor(key_id=key_id, public_key=public_key, algorithm="Ed25519")
    anchor_path = tmp_path / "anchor.json"
    save_trust_anchor(anchor, anchor_path)
    return pack_path, anchor_path, sig_path


def test_cli_matrix_signed_pack_passes(tmp_path: Path, capsys) -> None:
    cli = _load_cli()
    pack_path, anchor_path, sig_path = _setup_signed_pack(tmp_path)
    rc = cli.main(
        [
            "--pack",
            str(pack_path),
            "--trust-anchor",
            str(anchor_path),
            "--signature",
            str(sig_path),
            "--matrix",
        ]
    )
    captured = capsys.readouterr()
    assert rc == 0, captured.err
    assert "DRAFT" not in captured.out
    assert "registry-policy" in captured.out


def test_cli_matrix_unsigned_pack_refused(tmp_path: Path, capsys) -> None:
    cli = _load_cli()
    pack_path = tmp_path / "pack.json"
    pack_path.write_text(json.dumps(_pack_dict()), encoding="utf-8")
    anchor_path = tmp_path / "anchor.json"
    _, public_key = generate_keypair()
    save_trust_anchor(
        TrustAnchor(key_id="cairn-test", public_key=public_key, algorithm="Ed25519"),
        anchor_path,
    )
    rc = cli.main(
        [
            "--pack",
            str(pack_path),
            "--trust-anchor",
            str(anchor_path),
            "--matrix",
        ]
    )
    captured = capsys.readouterr()
    assert rc == 1, captured.out
    assert "refusing" in captured.err.lower()


def test_cli_matrix_tampered_pack_refused(tmp_path: Path, capsys) -> None:
    cli = _load_cli()
    pack_path, anchor_path, sig_path = _setup_signed_pack(tmp_path)
    # Tamper with the pack after signing.
    raw = json.loads(pack_path.read_text(encoding="utf-8"))
    raw["records"][0]["notes"] = "tampered"
    pack_path.write_text(json.dumps(raw), encoding="utf-8")
    rc = cli.main(
        [
            "--pack",
            str(pack_path),
            "--trust-anchor",
            str(anchor_path),
            "--signature",
            str(sig_path),
            "--matrix",
        ]
    )
    captured = capsys.readouterr()
    assert rc == 1, captured.out
    assert "invalid provenance signature" in captured.err


def test_cli_matrix_allow_unsigned_emits_draft(tmp_path: Path, capsys) -> None:
    cli = _load_cli()
    pack_path = tmp_path / "pack.json"
    pack_path.write_text(json.dumps(_pack_dict()), encoding="utf-8")
    rc = cli.main(["--pack", str(pack_path), "--matrix", "--allow-unsigned"])
    captured = capsys.readouterr()
    assert rc == 0, captured.err
    assert "DRAFT" in captured.out


def test_cli_hash_still_works_without_trust_anchor(tmp_path: Path, capsys) -> None:
    cli = _load_cli()
    pack_path = tmp_path / "pack.json"
    pack_path.write_text(json.dumps(_pack_dict()), encoding="utf-8")
    rc = cli.main(["--pack", str(pack_path), "--hash"])
    captured = capsys.readouterr()
    assert rc == 0, captured.err
    assert len(captured.out.strip()) == 64
