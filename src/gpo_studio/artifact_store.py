"""Content-addressed artifact store for scripts and companion files.

Artifacts are stored by SHA-256 content hash in a dedicated SQLite database.
Each artifact keeps an immutable audit trail (provenance) and is scanned for
malware signatures and exposed secrets before it can be approved for
publication.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import re
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

from .model import NotFoundError, StudioError

ArtifactType = Literal["script", "companion", "signature"]
ArtifactStatus = Literal["pending", "scanned", "quarantined", "approved", "rejected", "deleted"]
ScanResult = Literal["clean", "malicious", "suspicious", "unscanned", "error"]

MAX_ARTIFACT_SIZE = 10 * 1024 * 1024  # 10 MB
MAX_ARTIFACTS = 10000

ALLOWED_EXTENSIONS = frozenset({
    ".ps1", ".psm1", ".psd1", ".bat", ".cmd", ".vbs", ".js",
    ".msi", ".msp", ".exe", ".dll", ".cab", ".zip",
    ".txt", ".xml", ".json", ".reg", ".inf", ".adm", ".admx",
})

FORBIDDEN_EXTENSIONS = frozenset({
    ".scr", ".pif", ".com", ".hta", ".wsf", ".wsh",
})

# The EICAR test string is the industry-standard harmless marker used to verify
# antivirus/malware detection without shipping real malware.
_EICAR = (
    b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
)
_KNOWN_MALWARE_SIGNATURES = (_EICAR,)

_MIME_TYPE_MAP: dict[str, str] = {
    ".ps1": "text/x-powershell",
    ".psm1": "text/x-powershell",
    ".psd1": "text/x-powershell",
    ".bat": "text/x-batch",
    ".cmd": "text/x-batch",
    ".vbs": "text/x-vbscript",
    ".js": "text/javascript",
    ".msi": "application/x-msi",
    ".msp": "application/x-msp",
    ".exe": "application/x-msdownload",
    ".dll": "application/x-msdownload",
    ".cab": "application/vnd.ms-cab-compressed",
    ".zip": "application/zip",
    ".txt": "text/plain",
    ".xml": "application/xml",
    ".json": "application/json",
    ".reg": "text/x-regedit",
    ".inf": "text/plain",
    ".adm": "text/plain",
    ".admx": "application/xml",
}

_EXECUTABLE_EXTENSIONS = frozenset({".exe", ".dll"})


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class ArtifactError(StudioError):
    """Artifact validation, storage, or lifecycle error."""


@dataclass(frozen=True, slots=True)
class ArtifactMetadata:
    artifact_id: str
    artifact_type: ArtifactType
    original_name: str
    content_hash: str
    size: int
    mime_type: str = ""
    signer: str = ""
    scan_result: ScanResult = "unscanned"
    scan_detail: str = ""
    source: str = ""
    owner: str = ""
    status: ArtifactStatus = "pending"
    created_at: str = ""
    expires_at: str = ""
    license: str = ""
    labels: tuple[str, ...] = ()

    @property
    def is_immutable(self) -> bool:
        """Artifacts are immutable once approved."""
        return self.status in ("approved", "rejected", "deleted")


@dataclass(frozen=True, slots=True)
class Artifact:
    metadata: ArtifactMetadata
    content: bytes = b""


@dataclass(frozen=True, slots=True)
class SecretFinding:
    line_number: int
    pattern: str
    severity: Literal["high", "medium"]
    context: str = ""


ProvenanceAction = Literal[
    "stored", "scanned", "quarantined", "approved", "rejected", "deleted", "accessed"
]


@dataclass(frozen=True, slots=True)
class ProvenanceEntry:
    artifact_id: str
    action: ProvenanceAction
    actor: str
    timestamp: str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class PublicationCheck:
    artifact_id: str
    is_safe: bool
    reasons: tuple[str, ...] = field(default_factory=tuple)


def _is_text_content(content: bytes) -> bool:
    """Return True if *content* looks like text rather than binary."""
    if b"\x00" in content:
        return False
    try:
        content.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def _redact_context(line: str, start: int, end: int) -> str:
    """Return the line with the matched portion masked."""
    return f"{line[:start]}***REDACTED***{line[end:]}"


_SECRET_PATTERNS: tuple[tuple[str, Literal["high", "medium"], re.Pattern[bytes]], ...] = (
    ("aws_access_key_id", "high", re.compile(rb"AKIA[A-Z0-9]{16}")),
    (
        "api_key",
        "medium",
        re.compile(rb"(?:api[_-]?key|apikey)\s*[=:]\s*['\"]?[^\s'\"]+['\"]?", re.IGNORECASE),
    ),
    (
        "password",
        "medium",
        re.compile(rb"(?:password|passwd|pwd)\s*[=:]\s*['\"]?[^\s'\"]+['\"]?", re.IGNORECASE),
    ),
    (
        "private_key",
        "high",
        re.compile(rb"-----BEGIN (?:RSA|EC|DSA|OPENSSH) PRIVATE KEY-----"),
    ),
    (
        "connection_string",
        "high",
        re.compile(
            rb"(?:server|data source|host|uid|user id|pwd|password|pass)\s*[=:]\s*[^;\s]+",
            re.IGNORECASE,
        ),
    ),
    (
        "base64_block",
        "medium",
        re.compile(rb"[A-Za-z0-9+/]{100,}={0,2}"),
    ),
)


def detect_secrets(content: bytes, max_lines: int = 10000) -> tuple[SecretFinding, ...]:
    """Scan text content for potential secrets.

    Returns an empty tuple for binary content. Only the first *max_lines* lines
    are inspected.
    """
    if not _is_text_content(content):
        return ()

    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return ()

    lines = text.splitlines()[:max_lines]
    findings: list[SecretFinding] = []
    for line_number, line in enumerate(lines, start=1):
        line_bytes = line.encode("utf-8")
        for pattern_name, severity, compiled in _SECRET_PATTERNS:
            for match in compiled.finditer(line_bytes):
                start, end = match.span()
                context = _redact_context(line, start, end)
                findings.append(
                    SecretFinding(
                        line_number=line_number,
                        pattern=pattern_name,
                        severity=severity,
                        context=context[:200],
                    )
                )
    return tuple(findings)


def _detect_malware(content: bytes) -> str:
    """Return a non-empty reason if a known malware signature is found."""
    for signature in _KNOWN_MALWARE_SIGNATURES:
        if signature in content:
            return "known malware signature detected"
    return ""


def _mime_type_for(path: str) -> str:
    suffix = Path(path).suffix.lower()
    return _MIME_TYPE_MAP.get(suffix, mimetypes.guess_type(path, strict=False)[0] or "")


def _status_from_scan(scan_result: ScanResult) -> ArtifactStatus:
    if scan_result in ("malicious", "error"):
        return "pending"
    return "scanned"


class ArtifactStore:
    """Content-addressed artifact store backed by SQLite."""

    def __init__(self, db_path: str) -> None:
        """Initialize the store, creating tables if needed."""
        self.db_path = db_path
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(db_path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with self._lock:
            self._connection.execute("PRAGMA journal_mode = WAL")
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA busy_timeout = 5000")
            self._connection.execute("PRAGMA synchronous = NORMAL")
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    artifact_type TEXT NOT NULL,
                    original_name TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    mime_type TEXT NOT NULL DEFAULT '',
                    signer TEXT NOT NULL DEFAULT '',
                    scan_result TEXT NOT NULL DEFAULT 'unscanned',
                    scan_detail TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT '',
                    owner TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL DEFAULT '',
                    expires_at TEXT NOT NULL DEFAULT '',
                    license TEXT NOT NULL DEFAULT '',
                    labels TEXT NOT NULL DEFAULT '[]',
                    content BLOB NOT NULL
                )
                """
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS artifact_provenance (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id) ON DELETE CASCADE,
                    action TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    detail TEXT NOT NULL DEFAULT ''
                )
                """
            )
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_artifact_provenance_id
                    ON artifact_provenance(artifact_id)
                """
            )
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_artifacts_type_status
                    ON artifacts(artifact_type, status)
                """
            )

    def compute_hash(self, content: bytes) -> str:
        """Compute SHA-256 hex digest of content."""
        return hashlib.sha256(content).hexdigest()

    def _count_artifacts(self) -> int:
        row = self._connection.execute(
            "SELECT COUNT(*) AS n FROM artifacts"
        ).fetchone()
        return int(row["n"]) if row else 0

    def _load_metadata(self, row: sqlite3.Row) -> ArtifactMetadata:
        labels_json = row["labels"]
        try:
            labels = tuple(json.loads(labels_json))
        except json.JSONDecodeError:
            labels = ()
        return ArtifactMetadata(
            artifact_id=row["artifact_id"],
            artifact_type=row["artifact_type"],
            original_name=row["original_name"],
            content_hash=row["content_hash"],
            size=int(row["size"]),
            mime_type=row["mime_type"],
            signer=row["signer"],
            scan_result=row["scan_result"],
            scan_detail=row["scan_detail"],
            source=row["source"],
            owner=row["owner"],
            status=row["status"],
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            license=row["license"],
            labels=labels,
        )

    def _get_row(self, artifact_id: str) -> sqlite3.Row | None:
        row = self._connection.execute(
            "SELECT * FROM artifacts WHERE artifact_id = ?", (artifact_id,)
        ).fetchone()
        return cast(sqlite3.Row | None, row)

    def _record_provenance(
        self,
        artifact_id: str,
        action: Literal[
            "stored", "scanned", "quarantined", "approved", "rejected", "deleted", "accessed"
        ],
        actor: str,
        detail: str = "",
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO artifact_provenance(artifact_id, action, actor, timestamp, detail)
            VALUES(?,?,?,?,?)
            """,
            (artifact_id, action, actor, _now(), detail),
        )

    def store_artifact(
        self,
        content: bytes,
        original_name: str,
        artifact_type: ArtifactType = "script",
        *,
        owner: str = "",
        source: str = "",
        license_id: str = "",
        labels: tuple[str, ...] = (),
    ) -> ArtifactMetadata:
        """Store an artifact and return its metadata."""
        if len(content) > MAX_ARTIFACT_SIZE:
            raise ArtifactError(
                f"Artifact exceeds maximum size of {MAX_ARTIFACT_SIZE} bytes"
            )

        suffix = Path(original_name).suffix.lower()
        if suffix in FORBIDDEN_EXTENSIONS:
            raise ArtifactError(f"Forbidden file extension: {suffix}")
        if suffix not in ALLOWED_EXTENSIONS:
            raise ArtifactError(f"Extension not allowed: {suffix}")

        malware_reason = _detect_malware(content)
        if malware_reason:
            raise ArtifactError(f"Malware detected: {malware_reason}")

        content_hash = self.compute_hash(content)
        is_text = _is_text_content(content)
        secret_findings = detect_secrets(content) if is_text else ()

        if secret_findings:
            scan_result: ScanResult = "suspicious"
            scan_detail = f"{len(secret_findings)} potential secret(s) detected"
        else:
            # Binary content can't contain text secrets, and the malware
            # check already ran on all content, so binary artifacts that
            # pass are scanned/clean.
            scan_result = "clean"
            scan_detail = ""

        status = _status_from_scan(scan_result)
        timestamp = _now()

        with self._lock:
            existing = self._get_row(content_hash)
            if existing is not None:
                return self._load_metadata(existing)

            if self._count_artifacts() >= MAX_ARTIFACTS:
                raise ArtifactError(
                    f"Artifact store is full (maximum {MAX_ARTIFACTS} artifacts)"
                )

            self._connection.execute(
                """
                INSERT INTO artifacts(
                    artifact_id, artifact_type, original_name, content_hash, size,
                    mime_type, signer, scan_result, scan_detail, source, owner,
                    status, created_at, expires_at, license, labels, content
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    content_hash,
                    artifact_type,
                    original_name,
                    content_hash,
                    len(content),
                    _mime_type_for(original_name),
                    "",
                    scan_result,
                    scan_detail,
                    source,
                    owner,
                    status,
                    timestamp,
                    "",
                    license_id,
                    json.dumps(list(labels), sort_keys=True),
                    content,
                ),
            )
            self._record_provenance(content_hash, "stored", owner or "system", scan_detail)
            self._connection.commit()
            row = self._get_row(content_hash)
            assert row is not None
            return self._load_metadata(row)

    def get_artifact(self, artifact_id: str, include_content: bool = False) -> Artifact | None:
        """Retrieve an artifact by its content hash."""
        with self._lock:
            row = self._get_row(artifact_id)
            if row is None:
                return None
            metadata = self._load_metadata(row)
            content = row["content"] if include_content else b""
            return Artifact(metadata=metadata, content=content)

    def _transition_status(
        self,
        artifact_id: str,
        target_status: ArtifactStatus,
        actor: str,
        reason: str,
        action: Literal[
            "stored", "scanned", "quarantined", "approved", "rejected", "deleted", "accessed"
        ],
        allowed_sources: tuple[ArtifactStatus, ...],
    ) -> None:
        with self._lock:
            metadata = self._require_metadata(artifact_id)
            if metadata.status not in allowed_sources:
                raise ArtifactError(
                    f"Cannot {action} artifact with status {metadata.status}"
                )
            self._connection.execute(
                "UPDATE artifacts SET status = ? WHERE artifact_id = ?",
                (target_status, artifact_id),
            )
            self._record_provenance(artifact_id, action, actor, reason)
            self._connection.commit()

    def _require_metadata(self, artifact_id: str) -> ArtifactMetadata:
        row = self._get_row(artifact_id)
        if row is None:
            raise NotFoundError(f"Artifact {artifact_id} was not found")
        return self._load_metadata(row)

    def delete_artifact(self, artifact_id: str) -> None:
        """Mark an artifact as deleted (soft delete)."""
        with self._lock:
            metadata = self._require_metadata(artifact_id)
            if metadata.status == "deleted":
                return
            self._connection.execute(
                "UPDATE artifacts SET status = ? WHERE artifact_id = ?",
                ("deleted", artifact_id),
            )
            self._record_provenance(artifact_id, "deleted", "system", "soft delete")
            self._connection.commit()

    def quarantine_artifact(self, artifact_id: str, reason: str) -> None:
        """Move an artifact to quarantine."""
        self._transition_status(
            artifact_id,
            "quarantined",
            "system",
            reason,
            "quarantined",
            ("pending", "scanned"),
        )

    def approve_artifact(self, artifact_id: str, approver: str) -> None:
        """Approve an artifact for use."""
        with self._lock:
            metadata = self._require_metadata(artifact_id)
            if metadata.status != "scanned":
                raise ArtifactError(
                    f"Cannot approve artifact with status {metadata.status}"
                )
            if metadata.scan_result != "clean":
                raise ArtifactError(
                    f"Cannot approve artifact with scan result {metadata.scan_result}"
                )
            self._connection.execute(
                "UPDATE artifacts SET status = ? WHERE artifact_id = ?",
                ("approved", artifact_id),
            )
            self._record_provenance(artifact_id, "approved", approver, "approved for use")
            self._connection.commit()

    def reject_artifact(self, artifact_id: str, reason: str) -> None:
        """Reject an artifact."""
        self._transition_status(
            artifact_id,
            "rejected",
            "system",
            reason,
            "rejected",
            ("pending", "scanned"),
        )

    def list_artifacts(
        self,
        artifact_type: ArtifactType | None = None,
        status: ArtifactStatus | None = None,
        owner: str | None = None,
        label: str | None = None,
    ) -> list[ArtifactMetadata]:
        """List artifacts with optional filters."""
        conditions: list[str] = []
        params: list[Any] = []
        if artifact_type is not None:
            conditions.append("artifact_type = ?")
            params.append(artifact_type)
        if status is not None:
            conditions.append("status = ?")
            params.append(status)
        if owner is not None:
            conditions.append("owner = ?")
            params.append(owner)
        if label is not None:
            conditions.append("labels LIKE ?")
            params.append(f'%"{label}"%')

        where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""
        sql = f"SELECT * FROM artifacts {where_clause} ORDER BY created_at DESC"
        with self._lock:
            rows = self._connection.execute(sql, params).fetchall()
            return [self._load_metadata(row) for row in rows]

    def search_artifacts(self, query: str) -> list[ArtifactMetadata]:
        """Search artifacts by name, owner, or labels."""
        pattern = f"%{query}%"
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM artifacts
                WHERE original_name LIKE ?
                   OR owner LIKE ?
                   OR labels LIKE ?
                ORDER BY created_at DESC
                """,
                (pattern, pattern, pattern),
            ).fetchall()
            return [self._load_metadata(row) for row in rows]

def record_provenance(store: ArtifactStore, entry: ProvenanceEntry) -> None:
    """Record a provenance entry for an artifact."""
    with store._lock:
        store._require_metadata(entry.artifact_id)
        store._connection.execute(
            """
            INSERT INTO artifact_provenance(artifact_id, action, actor, timestamp, detail)
            VALUES(?,?,?,?,?)
            """,
            (entry.artifact_id, entry.action, entry.actor, entry.timestamp, entry.detail),
        )
        store._connection.commit()


def get_provenance(store: ArtifactStore, artifact_id: str) -> list[ProvenanceEntry]:
    """Get the full provenance chain for an artifact."""
    with store._lock:
        rows = store._connection.execute(
            """
            SELECT action, actor, timestamp, detail
            FROM artifact_provenance
            WHERE artifact_id = ?
            ORDER BY id ASC
            """,
            (artifact_id,),
        ).fetchall()
        return [
            ProvenanceEntry(
                artifact_id=artifact_id,
                action=row["action"],
                actor=row["actor"],
                timestamp=row["timestamp"],
                detail=row["detail"],
            )
            for row in rows
        ]


def _is_expired(metadata: ArtifactMetadata) -> bool:
    if not metadata.expires_at:
        return False
    try:
        expires = datetime.fromisoformat(metadata.expires_at)
    except ValueError:
        return False
    return datetime.now(UTC) > expires


def check_publication_safety(store: ArtifactStore, artifact_id: str) -> PublicationCheck:
    """Check if an artifact is safe to publish."""
    artifact = store.get_artifact(artifact_id, include_content=True)
    if artifact is None:
        return PublicationCheck(
            artifact_id=artifact_id,
            is_safe=False,
            reasons=("Artifact not found",),
        )

    metadata = artifact.metadata
    reasons: list[str] = []

    if metadata.status != "approved":
        reasons.append(f"status is {metadata.status}, not approved")
    if metadata.scan_result != "clean":
        reasons.append(f"scan_result is {metadata.scan_result}, not clean")

    # Re-scan text content for secrets; binary files rely on their scan_result.
    if _is_text_content(artifact.content) and detect_secrets(artifact.content):
        reasons.append("content contains potential secrets")

    if _is_expired(metadata):
        reasons.append("artifact has expired")

    suffix = Path(metadata.original_name).suffix.lower()
    if suffix in _EXECUTABLE_EXTENSIONS and not metadata.signer:
        reasons.append("executable is not signed")

    computed_hash = store.compute_hash(artifact.content)
    if computed_hash != metadata.content_hash:
        reasons.append("content integrity check failed")

    if reasons:
        return PublicationCheck(
            artifact_id=artifact_id,
            is_safe=False,
            reasons=tuple(reasons),
        )
    return PublicationCheck(
        artifact_id=artifact_id,
        is_safe=True,
        reasons=(),
    )
