from __future__ import annotations

from pathlib import Path

import pytest

from gpo_studio.artifact_store import (
    MAX_ARTIFACT_SIZE,
    ArtifactError,
    ArtifactMetadata,
    ArtifactStore,
    ProvenanceEntry,
    check_publication_safety,
    detect_secrets,
    get_provenance,
    record_provenance,
)


@pytest.fixture
def store(tmp_path: Path) -> ArtifactStore:
    db_path = tmp_path / "artifacts.db"
    return ArtifactStore(str(db_path))


def _script(name: str = "test.ps1", content: str = "Write-Host 'hello'\n") -> tuple[bytes, str]:
    return content.encode("utf-8"), name


class TestArtifactModel:
    def test_metadata_defaults_and_immutability(self) -> None:
        meta = ArtifactMetadata(
            artifact_id="a" * 64,
            artifact_type="script",
            original_name="x.ps1",
            content_hash="a" * 64,
            size=12,
        )
        assert meta.status == "pending"
        assert not meta.is_immutable

        approved = ArtifactMetadata(
            artifact_id="b" * 64,
            artifact_type="script",
            original_name="y.ps1",
            content_hash="b" * 64,
            size=12,
            status="approved",
        )
        assert approved.is_immutable


class TestStoreAndRetrieve:
    def test_round_trip(self, store: ArtifactStore) -> None:
        content, name = _script("deploy.ps1")
        meta = store.store_artifact(content, name)
        assert meta.original_name == "deploy.ps1"

        artifact = store.get_artifact(meta.artifact_id, include_content=True)
        assert artifact is not None
        assert artifact.content == content
        assert artifact.metadata.artifact_id == meta.artifact_id

    def test_get_without_content(self, store: ArtifactStore) -> None:
        content, name = _script()
        meta = store.store_artifact(content, name)
        artifact = store.get_artifact(meta.artifact_id, include_content=False)
        assert artifact is not None
        assert artifact.content == b""
        assert artifact.metadata.artifact_id == meta.artifact_id

    def test_get_missing_returns_none(self, store: ArtifactStore) -> None:
        assert store.get_artifact("missing") is None


class TestDeduplication:
    def test_same_content_same_id(self, store: ArtifactStore) -> None:
        content = b"same bytes"
        meta1 = store.store_artifact(content, "first.ps1")
        meta2 = store.store_artifact(content, "second.ps1")
        assert meta1.artifact_id == meta2.artifact_id
        assert meta1.original_name == "first.ps1"
        assert meta2.original_name == "first.ps1"  # existing metadata returned


def test_persistence_across_connections(tmp_path: Path) -> None:
    db = str(tmp_path / "artifacts.db")
    store1 = ArtifactStore(db)
    meta = store1.store_artifact(b"hello", "test.ps1")
    # store1 goes out of scope / connection closes
    del store1
    store2 = ArtifactStore(db)
    result = store2.get_artifact(meta.artifact_id, include_content=True)
    assert result is not None
    assert result.content == b"hello"


class TestValidation:
    def test_size_limit(self, store: ArtifactStore) -> None:
        too_large = b"x" * (MAX_ARTIFACT_SIZE + 1)
        with pytest.raises(ArtifactError, match="exceeds maximum size"):
            store.store_artifact(too_large, "big.ps1")

    def test_allowed_extension(self, store: ArtifactStore) -> None:
        content = b"# empty"
        for ext in (".ps1", ".reg", ".json"):
            meta = store.store_artifact(content, f"file{ext}")
            assert meta.artifact_id == store.compute_hash(content)

    def test_forbidden_extension(self, store: ArtifactStore) -> None:
        content = b""
        with pytest.raises(ArtifactError, match="Forbidden file extension"):
            store.store_artifact(content, "bad.hta")

    def test_unknown_extension(self, store: ArtifactStore) -> None:
        with pytest.raises(ArtifactError, match="Extension not allowed"):
            store.store_artifact(b"data", "bad.py")

    def test_malware_signature_rejected(self, store: ArtifactStore) -> None:
        eicar = (
            b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
        )
        with pytest.raises(ArtifactError, match="Malware detected"):
            store.store_artifact(eicar, "malware.ps1")


class TestSecretDetection:
    def test_aws_access_key(self) -> None:
        content = b"aws_access_key_id = AKIAIOSFODNN7EXAMPLE\n"
        findings = detect_secrets(content)
        assert len(findings) == 1
        assert findings[0].pattern == "aws_access_key_id"
        assert findings[0].severity == "high"

    def test_api_key(self) -> None:
        content = b"api_key = 'super-secret-value'\n"
        findings = detect_secrets(content)
        assert any(f.pattern == "api_key" for f in findings)

    def test_password(self) -> None:
        content = b"password = P@ssw0rd123\n"
        findings = detect_secrets(content)
        assert any(f.pattern == "password" for f in findings)

    def test_private_key(self) -> None:
        content = b"-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----\n"
        findings = detect_secrets(content)
        assert any(f.pattern == "private_key" for f in findings)
        assert all(f.severity == "high" for f in findings if f.pattern == "private_key")

    def test_clean_file(self) -> None:
        content = b"Write-Host 'hello world'\n"
        assert detect_secrets(content) == ()

    def test_binary_content_skipped(self) -> None:
        content = bytes(range(256))
        assert detect_secrets(content) == ()


class TestLifecycleWorkflows:
    def test_approve_clean_artifact(self, store: ArtifactStore) -> None:
        content = b"Write-Host 'clean'\n"
        meta = store.store_artifact(content, "clean.ps1")
        assert meta.status == "scanned"
        assert meta.scan_result == "clean"

        store.approve_artifact(meta.artifact_id, "alice")
        updated = store.get_artifact(meta.artifact_id)
        assert updated is not None
        assert updated.metadata.status == "approved"
        assert updated.metadata.is_immutable

    def test_approve_requires_clean_scan(self, store: ArtifactStore) -> None:
        content = b"api_key = 'secret'\n"
        meta = store.store_artifact(content, "leaky.ps1")
        assert meta.scan_result == "suspicious"

        with pytest.raises(ArtifactError, match="scan result"):
            store.approve_artifact(meta.artifact_id, "alice")

    def test_quarantine_and_approve_workflow(self, store: ArtifactStore) -> None:
        content = b"Write-Host 'ok'\n"
        meta = store.store_artifact(content, "ok.ps1")
        store.quarantine_artifact(meta.artifact_id, "manual review")
        updated = store.get_artifact(meta.artifact_id)
        assert updated is not None
        assert updated.metadata.status == "quarantined"
        assert not updated.metadata.is_immutable

    def test_reject_workflow(self, store: ArtifactStore) -> None:
        content = b"Write-Host 'nope'\n"
        meta = store.store_artifact(content, "nope.ps1")
        store.reject_artifact(meta.artifact_id, "does not meet standards")
        updated = store.get_artifact(meta.artifact_id)
        assert updated is not None
        assert updated.metadata.status == "rejected"
        assert updated.metadata.is_immutable

        with pytest.raises(ArtifactError):
            store.approve_artifact(meta.artifact_id, "alice")

    def test_soft_delete(self, store: ArtifactStore) -> None:
        content, name = _script()
        meta = store.store_artifact(content, name)
        store.delete_artifact(meta.artifact_id)
        deleted = store.get_artifact(meta.artifact_id)
        assert deleted is not None
        assert deleted.metadata.status == "deleted"


class TestListAndSearch:
    def test_list_by_type(self, store: ArtifactStore) -> None:
        script = store.store_artifact(b"a", "a.ps1", artifact_type="script")
        store.store_artifact(b"b", "b.txt", artifact_type="companion")
        scripts = store.list_artifacts(artifact_type="script")
        assert len(scripts) == 1
        assert scripts[0].artifact_id == script.artifact_id

    def test_list_by_status(self, store: ArtifactStore) -> None:
        meta = store.store_artifact(b"x", "x.ps1")
        store.approve_artifact(meta.artifact_id, "alice")
        approved = store.list_artifacts(status="approved")
        assert len(approved) == 1

    def test_list_by_owner_and_label(self, store: ArtifactStore) -> None:
        store.store_artifact(b"x", "x.ps1", owner="bob", labels=("deploy", "prod"))
        bob_artifacts = store.list_artifacts(owner="bob")
        assert len(bob_artifacts) == 1
        prod_artifacts = store.list_artifacts(label="prod")
        assert len(prod_artifacts) == 1

    def test_search_by_name(self, store: ArtifactStore) -> None:
        store.store_artifact(b"a", "frontend.ps1")
        store.store_artifact(b"b", "backend.ps1")
        results = store.search_artifacts("front")
        assert len(results) == 1
        assert results[0].original_name == "frontend.ps1"


class TestProvenance:
    def test_store_records_provenance(self, store: ArtifactStore) -> None:
        meta = store.store_artifact(b"x", "x.ps1", owner="alice")
        chain = get_provenance(store, meta.artifact_id)
        assert len(chain) == 1
        assert chain[0].action == "stored"
        assert chain[0].actor == "alice"

    def test_record_and_retrieve_provenance(self, store: ArtifactStore) -> None:
        meta = store.store_artifact(b"x", "x.ps1")
        entry = ProvenanceEntry(
            artifact_id=meta.artifact_id,
            action="accessed",
            actor="bob",
            timestamp="2026-01-01T00:00:00+00:00",
            detail="manual access",
        )
        record_provenance(store, entry)
        chain = get_provenance(store, meta.artifact_id)
        assert any(e.action == "accessed" and e.actor == "bob" for e in chain)

    def test_provenance_chain(self, store: ArtifactStore) -> None:
        meta = store.store_artifact(b"x", "x.ps1")
        store.approve_artifact(meta.artifact_id, "alice")
        store.delete_artifact(meta.artifact_id)
        chain = get_provenance(store, meta.artifact_id)
        assert [e.action for e in chain] == ["stored", "approved", "deleted"]


class TestPublicationSafety:
    def test_approved_clean_is_safe(self, store: ArtifactStore) -> None:
        meta = store.store_artifact(b"Write-Host 'ok'\n", "ok.ps1")
        store.approve_artifact(meta.artifact_id, "alice")
        check = check_publication_safety(store, meta.artifact_id)
        assert check.is_safe
        assert check.reasons == ()

    def test_unapproved_binary_is_unsafe(self, store: ArtifactStore) -> None:
        # Binary files are scanned/clean but not approved, so not safe to publish.
        meta = store.store_artifact(b"\x00\x01\x02\x03", "prog.exe")
        check = check_publication_safety(store, meta.artifact_id)
        assert not check.is_safe
        assert any("not approved" in r.lower() for r in check.reasons)

    def test_expired_is_unsafe(self, store: ArtifactStore) -> None:
        from dataclasses import replace

        meta = store.store_artifact(b"Write-Host 'ok'\n", "ok.ps1")
        store.approve_artifact(meta.artifact_id, "alice")
        # Simulate expiration by updating the stored metadata directly.
        expired_meta = replace(meta, expires_at="2000-01-01T00:00:00+00:00")
        store._connection.execute(
            "UPDATE artifacts SET expires_at = ? WHERE artifact_id = ?",
            (expired_meta.expires_at, meta.artifact_id),
        )
        check = check_publication_safety(store, meta.artifact_id)
        assert not check.is_safe
        assert any("expired" in r.lower() for r in check.reasons)

    def test_unsigned_executable_is_unsafe(self, store: ArtifactStore) -> None:
        # An .exe without a signer is not safe to publish, even when approved.
        meta = store.store_artifact(b"MZ\x00\x00fake", "tool.exe")
        store.approve_artifact(meta.artifact_id, "alice")
        check = check_publication_safety(store, meta.artifact_id)
        assert not check.is_safe
        assert any("not signed" in r.lower() for r in check.reasons)


class TestBinaryHandling:
    def test_binary_skip_secret_scan(self, store: ArtifactStore) -> None:
        content = bytes(range(256))
        meta = store.store_artifact(content, "tool.exe")
        # Binary content skips secret scanning but is still scanned for
        # malware.  A binary artifact that passes the malware check is
        # scanned/clean and eligible for approval.
        assert meta.status == "scanned"
        assert meta.scan_result == "clean"

    def test_binary_artifact_can_be_approved(self, store: ArtifactStore) -> None:
        content = b"\x00\x01\x02"
        meta = store.store_artifact(content, "binary.dll")
        assert meta.status == "scanned"
        assert meta.scan_result == "clean"
        store.approve_artifact(meta.artifact_id, "alice")
        updated = store.get_artifact(meta.artifact_id)
        assert updated is not None
        assert updated.metadata.status == "approved"
