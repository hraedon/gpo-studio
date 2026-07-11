from __future__ import annotations

from dataclasses import replace

import pytest

from gpo_studio.payload import (
    Approval,
    PayloadError,
    PreconditionFingerprint,
    PublisherJob,
    RegistryOperation,
    Signature,
    TargetSpec,
    canonical_payload,
    payload_digest,
    validate_job,
    verify_payload_digest,
)


def _sample_job() -> PublisherJob:
    return PublisherJob(
        schema_version=1,
        job_id="job-001",
        created_at="2026-07-11T20:00:00Z",
        target=TargetSpec(
            publisher_audience="publisher-a",
            forest="example.test",
            domain="example.test",
            intent="existing",
            gpo_guid="11111111-2222-3333-4444-555555555555",
        ),
        precondition=PreconditionFingerprint(
            collected_at="2026-07-11T19:55:00Z",
            semantic_sha256="0" * 64,
        ),
        operations=(
            RegistryOperation(
                operation_id="op-1",
                reason="Enable synthetic control",
                side="computer",
                key=r"Software\Policies\Example",
                name="Enabled",
                registry_type="DWord",
                value=1,
            ),
        ),
    )


def test_canonical_payload_excludes_approval() -> None:
    job = _sample_job()
    job_with_approval = replace(
        job,
        approval=Approval(
            payload_sha256="0" * 64,
            policy_version="v1",
            not_before="2026-07-12T01:00:00Z",
            expires_at="2026-07-12T02:00:00Z",
        ),
    )
    assert b"approval" not in canonical_payload(job_with_approval)
    assert canonical_payload(job_with_approval) == canonical_payload(job)


def test_payload_digest_stable() -> None:
    job = _sample_job()
    assert payload_digest(job) == payload_digest(job)


def test_payload_digest_changes_on_operation_change() -> None:
    job = _sample_job()
    modified = replace(
        job,
        operations=(
            RegistryOperation(
                operation_id="op-1",
                reason="Enable synthetic control",
                side="computer",
                key=r"Software\Policies\Example",
                name="Enabled",
                registry_type="DWord",
                value=2,
            ),
        ),
    )
    assert payload_digest(job) != payload_digest(modified)


def test_payload_digest_changes_on_target_change() -> None:
    job = _sample_job()
    modified = replace(
        job,
        target=TargetSpec(
            publisher_audience="publisher-b",
            forest="other.test",
            domain="other.test",
            intent="existing",
            gpo_guid="11111111-2222-3333-4444-555555555555",
        ),
    )
    assert payload_digest(job) != payload_digest(modified)


def test_verify_signature_matches() -> None:
    job = _sample_job()
    digest = payload_digest(job)
    job_with_approval = replace(
        job,
        approval=Approval(
            payload_sha256=digest,
            policy_version="v1",
            not_before="2026-07-12T01:00:00Z",
            expires_at="2026-07-12T02:00:00Z",
            signatures=(Signature(key_id="key-1", approver_subject="approver-1"),),
        ),
    )
    assert verify_payload_digest(job_with_approval) is True


def test_verify_signature_tampered() -> None:
    job = _sample_job()
    job_with_approval = replace(
        job,
        approval=Approval(
            payload_sha256="f" * 64,
            policy_version="v1",
            not_before="2026-07-12T01:00:00Z",
            expires_at="2026-07-12T02:00:00Z",
        ),
    )
    assert verify_payload_digest(job_with_approval) is False


def test_verify_signature_no_approval() -> None:
    job = _sample_job()
    assert verify_payload_digest(job) is False


def test_validate_job_rejects_empty_operations() -> None:
    job = PublisherJob(
        schema_version=1,
        job_id="job-001",
        created_at="2026-07-11T20:00:00Z",
        target=TargetSpec(
            publisher_audience="publisher-a",
            forest="example.test",
            domain="example.test",
            intent="create_new",
        ),
        precondition=PreconditionFingerprint(
            collected_at="2026-07-11T19:55:00Z",
            semantic_sha256="0" * 64,
        ),
        operations=(),
    )
    with pytest.raises(PayloadError, match="operations must be non-empty"):
        validate_job(job)


def test_validate_job_rejects_qword_as_int() -> None:
    job = PublisherJob(
        schema_version=1,
        job_id="job-001",
        created_at="2026-07-11T20:00:00Z",
        target=TargetSpec(
            publisher_audience="publisher-a",
            forest="example.test",
            domain="example.test",
            intent="create_new",
        ),
        precondition=PreconditionFingerprint(
            collected_at="2026-07-11T19:55:00Z",
            semantic_sha256="0" * 64,
        ),
        operations=(
            RegistryOperation(
                operation_id="op-1",
                reason="test",
                side="computer",
                key="Software\\Policies\\Example",
                name="LargeValue",
                registry_type="QWord",
                value=18446744073709551615,
            ),
        ),
    )
    with pytest.raises(PayloadError, match="QWord values must be strings"):
        validate_job(job)


def test_validate_job_rejects_existing_without_guid() -> None:
    job = PublisherJob(
        schema_version=1,
        job_id="job-001",
        created_at="2026-07-11T20:00:00Z",
        target=TargetSpec(
            publisher_audience="publisher-a",
            forest="example.test",
            domain="example.test",
            intent="existing",
            gpo_guid="",
        ),
        precondition=PreconditionFingerprint(
            collected_at="2026-07-11T19:55:00Z",
            semantic_sha256="0" * 64,
        ),
        operations=(
            RegistryOperation(
                operation_id="op-1",
                reason="test",
                side="computer",
                key="Software\\Policies\\Example",
                name="Enabled",
                registry_type="DWord",
                value=1,
            ),
        ),
    )
    with pytest.raises(PayloadError, match="gpo_guid"):
        validate_job(job)


def test_canonical_json_key_ordering() -> None:
    job = _sample_job()
    payload = canonical_payload(job)
    assert payload.index(b'"created_at"') < payload.index(b'"job_id"')
    assert payload.index(b'"job_id"') < payload.index(b'"operations"')


def test_qword_as_string_in_canonical() -> None:
    job = PublisherJob(
        schema_version=1,
        job_id="job-001",
        created_at="2026-07-11T20:00:00Z",
        target=TargetSpec(
            publisher_audience="publisher-a",
            forest="example.test",
            domain="example.test",
            intent="create_new",
        ),
        precondition=PreconditionFingerprint(
            collected_at="2026-07-11T19:55:00Z",
            semantic_sha256="0" * 64,
        ),
        operations=(
            RegistryOperation(
                operation_id="op-1",
                reason="test",
                side="computer",
                key="Software\\Policies\\Example",
                name="LargeValue",
                registry_type="QWord",
                value="18446744073709551615",
            ),
        ),
    )
    payload = canonical_payload(job)
    assert b'"18446744073709551615"' in payload
