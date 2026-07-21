"""Template repository management for ADMX/ADML sources.

Provides named, versioned template sources with per-file ingest resilience,
collision detection, content hashing, and target-lock support. The web process
never writes to SYSVOL or any domain path; ingest is read-only.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from .admx import AdmxCatalogue, AdmxError, build_catalogue

SourceKind = Literal["local", "central-store", "vendor-pack", "curated"]


class TemplateError(ValueError):
    """Template repository operation failed."""


@dataclass(frozen=True, slots=True)
class TemplateFile:
    relative_path: str
    sha256: str
    size: int


@dataclass(frozen=True, slots=True)
class TemplateSource:
    name: str
    kind: SourceKind
    path: str
    files: tuple[TemplateFile, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class IngestError:
    relative_path: str
    message: str
    kind: Literal["missing_adml", "parse_error", "io_error"] = "parse_error"


@dataclass(frozen=True, slots=True)
class IngestResult:
    source: TemplateSource
    catalogue: AdmxCatalogue
    errors: tuple[IngestError, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class NamespaceCollision:
    namespace: str
    sources: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FileCollision:
    relative_path: str
    sources: tuple[str, ...]
    hashes_differ: bool


@dataclass(frozen=True, slots=True)
class PolicyDrift:
    qualified_id: str
    sources: tuple[str, ...]
    reason: str


@dataclass(frozen=True, slots=True)
class MissingAdml:
    admx_path: str
    source: str


@dataclass(frozen=True, slots=True)
class CollisionReport:
    namespace_collisions: tuple[NamespaceCollision, ...] = field(default_factory=tuple)
    file_collisions: tuple[FileCollision, ...] = field(default_factory=tuple)
    policy_drift: tuple[PolicyDrift, ...] = field(default_factory=tuple)
    missing_adml: tuple[MissingAdml, ...] = field(default_factory=tuple)

    @property
    def has_issues(self) -> bool:
        return bool(
            self.namespace_collisions
            or self.file_collisions
            or self.policy_drift
            or self.missing_adml
        )


@dataclass(frozen=True, slots=True)
class TemplateLock:
    source_hashes: tuple[tuple[str, str], ...]
    """(source_name, aggregate_sha256) pairs frozen at configuration time."""


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hash_files(files: tuple[TemplateFile, ...]) -> str:
    h = hashlib.sha256()
    for f in sorted(files, key=lambda x: x.relative_path):
        h.update(f.relative_path.encode())
        h.update(f.sha256.encode())
    return h.hexdigest()


def detect_central_store(root: Path) -> Path | None:
    """Detect the PolicyDefinitions directory from a SYSVOL-like layout.

    Checks (in order):
    1. root/PolicyDefinitions/ (direct child — domain SYSVOL copy)
    2. root itself if it contains .admx files (flat layout)
    3. root/<domain>/PolicyDefinitions/ (one level of domain subdirectory)
    """
    direct = root / "PolicyDefinitions"
    if direct.is_dir() and any(direct.glob("*.admx")):
        return direct
    if any(root.glob("*.admx")):
        return root
    for child in sorted(root.iterdir()) if root.is_dir() else []:
        if child.is_dir():
            candidate = child / "PolicyDefinitions"
            if candidate.is_dir() and any(candidate.glob("*.admx")):
                return candidate
    return None


_MAX_TEMPLATE_FILE_SIZE = 10 * 1024 * 1024


def _scan_directory(directory: Path, source_name: str) -> tuple[TemplateFile, ...]:
    files: list[TemplateFile] = []
    for path in sorted(directory.glob("*.admx")):
        try:
            size = path.stat().st_size
            if size > _MAX_TEMPLATE_FILE_SIZE:
                continue
            data = path.read_bytes()
        except OSError:
            continue
        files.append(TemplateFile(
            relative_path=str(path.relative_to(directory)),
            sha256=_hash_bytes(data),
            size=len(data),
        ))
    adml_paths = sorted(directory.glob("*.adml"))
    for child in sorted(directory.iterdir()) if directory.is_dir() else []:
        if child.is_dir():
            adml_paths.extend(sorted(child.glob("*.adml")))
    for path in adml_paths:
        try:
            size = path.stat().st_size
            if size > _MAX_TEMPLATE_FILE_SIZE:
                continue
            data = path.read_bytes()
        except OSError:
            continue
        files.append(TemplateFile(
            relative_path=str(path.relative_to(directory)),
            sha256=_hash_bytes(data),
            size=len(data),
        ))
    return tuple(files)


def ingest_source(
    name: str,
    kind: SourceKind,
    directory: Path,
) -> IngestResult:
    """Ingest a template source directory with per-file resilience.

    Malformed ADMX/ADML files are skipped and reported in errors; they do
    not abort the entire ingest.
    """
    if not directory.is_dir():
        raise TemplateError(f"Template directory does not exist: {directory}")

    files = _scan_directory(directory, name)
    source = TemplateSource(name=name, kind=kind, path=str(directory), files=files)

    from .admx import (
        Category,
        NamespaceDeclaration,
        PolicyDefinition,
        SupportedOnDefinition,
        find_adml,
    )

    policies: list[PolicyDefinition] = []
    categories: list[Category] = []
    supported_on: list[SupportedOnDefinition] = []
    targets: list[NamespaceDeclaration] = []
    usings: list[NamespaceDeclaration] = []
    errors: list[IngestError] = []

    for admx_path in sorted(directory.glob("*.admx")):
        rel = str(admx_path.relative_to(directory))
        adml_path = find_adml(admx_path)
        if adml_path is None:
            errors.append(IngestError(
                relative_path=rel, message="No matching ADML found", kind="missing_adml"
            ))
            continue
        try:
            admx_data = admx_path.read_bytes()
            adml_data = adml_path.read_bytes()
            catalogue = build_catalogue(admx_data, adml_data)
        except (AdmxError, ValueError) as exc:
            errors.append(IngestError(relative_path=rel, message=str(exc), kind="parse_error"))
            continue
        except OSError as exc:
            errors.append(IngestError(relative_path=rel, message=str(exc), kind="io_error"))
            continue
        policies.extend(catalogue.policies)
        categories.extend(catalogue.categories)
        supported_on.extend(catalogue.supported_on)
        targets.extend(catalogue.target_namespaces)
        usings.extend(catalogue.used_namespaces)

    merged = AdmxCatalogue(
        policies=tuple(policies),
        categories=tuple(categories),
        supported_on=tuple(supported_on),
        target_namespaces=tuple(targets),
        used_namespaces=tuple(usings),
    )
    return IngestResult(source=source, catalogue=merged, errors=tuple(errors))


def detect_collisions(sources: tuple[IngestResult, ...]) -> CollisionReport:
    """Detect collisions across multiple ingested template sources."""
    namespace_map: dict[str, list[str]] = {}
    file_map: dict[str, list[tuple[str, str]]] = {}
    policy_map: dict[str, list[tuple[str, str, str]]] = {}
    missing: list[MissingAdml] = []

    for result in sources:
        src_name = result.source.name

        for ns_decl in result.catalogue.target_namespaces:
            namespace_map.setdefault(ns_decl.namespace, []).append(src_name)

        for tf in result.source.files:
            file_map.setdefault(tf.relative_path, []).append((src_name, tf.sha256))

        for policy in result.catalogue.policies:
            qid = policy.qualified_id
            el_count = len(policy.enabled_list.items) if policy.enabled_list else 0
            semantic = (
                f"{policy.key}|{policy.value_name}|{policy.namespace}|"
                f"{policy.class_}|{policy.enabled_value}|{policy.disabled_value}|"
                f"{len(policy.elements)}|{el_count}"
            )
            policy_map.setdefault(qid, []).append((src_name, "", _hash_bytes(
                semantic.encode()
            )))

        for err in result.errors:
            if err.kind == "missing_adml":
                missing.append(MissingAdml(admx_path=err.relative_path, source=src_name))

    ns_collisions: list[NamespaceCollision] = []
    for ns, srcs in sorted(namespace_map.items()):
        if len(srcs) > 1:
            ns_collisions.append(NamespaceCollision(namespace=ns, sources=tuple(sorted(set(srcs)))))

    file_collisions: list[FileCollision] = []
    for rel_path, entries in sorted(file_map.items()):
        if len(entries) > 1:
            hashes = {h for _, h in entries}
            file_collisions.append(FileCollision(
                relative_path=rel_path,
                sources=tuple(sorted(set(s for s, _ in entries))),
                hashes_differ=len(hashes) > 1,
            ))

    drift: list[PolicyDrift] = []
    for qid, policy_entries in sorted(policy_map.items()):
        if len(policy_entries) > 1:
            hashes = {h for _, _, h in policy_entries}
            if len(hashes) > 1:
                drift.append(PolicyDrift(
                    qualified_id=qid,
                    sources=tuple(sorted(set(s for s, _, _ in policy_entries))),
                    reason="Registry mapping differs across sources",
                ))

    return CollisionReport(
        namespace_collisions=tuple(ns_collisions),
        file_collisions=tuple(file_collisions),
        policy_drift=tuple(drift),
        missing_adml=tuple(missing),
    )


def build_lock(sources: tuple[IngestResult, ...]) -> TemplateLock:
    """Create a template lock from the current state of ingested sources."""
    pairs: list[tuple[str, str]] = []
    for result in sources:
        aggregate = _hash_files(result.source.files)
        pairs.append((result.source.name, aggregate))
    return TemplateLock(source_hashes=tuple(sorted(pairs)))


def validate_lock(lock: TemplateLock, sources: tuple[IngestResult, ...]) -> list[str]:
    """Validate a template lock against current sources. Returns list of violations."""
    violations: list[str] = []
    current = {r.source.name: _hash_files(r.source.files) for r in sources}
    for source_name, locked_hash in lock.source_hashes:
        current_hash = current.get(source_name)
        if current_hash is None:
            violations.append(f"Source '{source_name}' is no longer available")
        elif current_hash != locked_hash:
            violations.append(f"Source '{source_name}' has changed since lock")
    for name in current:
        if not any(n == name for n, _ in lock.source_hashes):
            violations.append(f"Source '{name}' was added after lock")
    return violations


def merge_catalogues(sources: tuple[IngestResult, ...]) -> AdmxCatalogue:
    """Merge catalogues from multiple sources into one."""
    from .admx import (
        Category,
        NamespaceDeclaration,
        PolicyDefinition,
        SupportedOnDefinition,
    )

    policies: list[PolicyDefinition] = []
    categories: list[Category] = []
    supported_on: list[SupportedOnDefinition] = []
    targets: list[NamespaceDeclaration] = []
    usings: list[NamespaceDeclaration] = []
    for result in sources:
        policies.extend(result.catalogue.policies)
        categories.extend(result.catalogue.categories)
        supported_on.extend(result.catalogue.supported_on)
        targets.extend(result.catalogue.target_namespaces)
        usings.extend(result.catalogue.used_namespaces)
    return AdmxCatalogue(
        policies=tuple(policies),
        categories=tuple(categories),
        supported_on=tuple(supported_on),
        target_namespaces=tuple(targets),
        used_namespaces=tuple(usings),
    )
