"""Publisher payload canonicalization and signature contract foundation."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Literal

from .canonical import canonical_json_bytes


class PayloadError(ValueError):
    """Invalid publisher payload."""


@dataclass(frozen=True, slots=True)
class RegistryOperation:
    operation_id: str
    reason: str
    side: Literal["computer", "user"]
    key: str
    name: str
    registry_type: Literal["DWord", "QWord", "String", "ExpandString", "Binary", "MultiString"]
    value: str | int | list[str]
    kind: Literal["registry.set", "registry.delete"] = "registry.set"


@dataclass(frozen=True, slots=True)
class TargetSpec:
    publisher_audience: str
    forest: str
    domain: str
    intent: Literal["existing", "create_new"]
    gpo_guid: str = ""
    dc_selector: str = ""


@dataclass(frozen=True, slots=True)
class PreconditionFingerprint:
    collected_at: str
    semantic_sha256: str
    user_version_ad: int = 0
    user_version_sysvol: int = 0
    computer_version_ad: int = 0
    computer_version_sysvol: int = 0
    links_sha256: str = ""
    acl_sha256: str = ""


@dataclass(frozen=True, slots=True)
class Signature:
    key_id: str
    approver_subject: str
    algorithm: Literal["Ed25519"] = "Ed25519"
    value: str = ""


@dataclass(frozen=True, slots=True)
class Approval:
    payload_sha256: str
    policy_version: str
    not_before: str
    expires_at: str
    signatures: tuple[Signature, ...] = ()


@dataclass(frozen=True, slots=True)
class PublisherJob:
    schema_version: int
    job_id: str
    created_at: str
    target: TargetSpec
    precondition: PreconditionFingerprint
    operations: tuple[RegistryOperation, ...]
    approval: Approval | None = None


def _job_to_dict(job: PublisherJob) -> dict[str, Any]:
    return {
        "schema_version": job.schema_version,
        "job_id": job.job_id,
        "created_at": job.created_at,
        "target": {
            "publisher_audience": job.target.publisher_audience,
            "forest": job.target.forest,
            "domain": job.target.domain,
            "intent": job.target.intent,
            "gpo_guid": job.target.gpo_guid,
            "dc_selector": job.target.dc_selector,
        },
        "precondition": {
            "collected_at": job.precondition.collected_at,
            "semantic_sha256": job.precondition.semantic_sha256,
            "user_version_ad": job.precondition.user_version_ad,
            "user_version_sysvol": job.precondition.user_version_sysvol,
            "computer_version_ad": job.precondition.computer_version_ad,
            "computer_version_sysvol": job.precondition.computer_version_sysvol,
            "links_sha256": job.precondition.links_sha256,
            "acl_sha256": job.precondition.acl_sha256,
        },
        "operations": [
            {
                "operation_id": op.operation_id,
                "reason": op.reason,
                "side": op.side,
                "key": op.key,
                "name": op.name,
                "registry_type": op.registry_type,
                "value": op.value,
                "kind": op.kind,
            }
            for op in job.operations
        ],
    }


def canonical_payload(job: PublisherJob) -> bytes:
    """Return RFC 8785 canonical UTF-8 bytes of the job, excluding the approval field."""
    return canonical_json_bytes(_job_to_dict(job))


def payload_digest(job: PublisherJob) -> str:
    """Return the hex SHA-256 digest of canonical_payload(job)."""
    return hashlib.sha256(canonical_payload(job)).hexdigest()


def verify_payload_digest(job: PublisherJob) -> bool:
    """Verify that the approval's payload_sha256 matches the canonical payload digest.

    This checks only the digest bound in the approval against the actual canonical
    payload. It does NOT verify cryptographic signature validity (no key material
    here). Cryptographic Ed25519 verification is deferred to the publisher worker.
    """
    if job.approval is None:
        return False
    return job.approval.payload_sha256 == payload_digest(job)


def validate_job(job: PublisherJob) -> None:
    """Validate a publisher job's structure and invariants."""
    if job.schema_version != 1:
        raise PayloadError(f"Unsupported schema_version: {job.schema_version}")
    if not job.job_id.strip():
        raise PayloadError("job_id must be non-empty")
    if not job.operations:
        raise PayloadError("operations must be non-empty")
    for op in job.operations:
        if not op.operation_id.strip():
            raise PayloadError("operation_id must be non-empty")
        if not op.reason.strip():
            raise PayloadError("reason must be non-empty")
        if op.registry_type == "QWord" and not isinstance(op.value, str):
            raise PayloadError("QWord values must be strings to avoid JSON precision loss")
        if op.registry_type == "MultiString" and not (
            isinstance(op.value, list) and all(isinstance(i, str) for i in op.value)
        ):
            raise PayloadError("MultiString values must be string lists")
    if job.target.intent == "existing" and not job.target.gpo_guid.strip():
        raise PayloadError('intent "existing" requires a non-empty gpo_guid')
    if job.approval is not None and job.approval.payload_sha256 != payload_digest(job):
        raise PayloadError("approval.payload_sha256 does not match canonical payload digest")
