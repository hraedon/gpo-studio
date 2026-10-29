"""Template repository management for ADMX/ADML sources.

Provides named, versioned template sources with per-file ingest resilience,
collision detection, content hashing, and target-lock support. The web process
never writes to SYSVOL or any domain path; ingest is read-only.
"""

from __future__ import annotations

import contextlib
import errno
import fnmatch
import hashlib
import os
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, assert_never

from .admx import (
    AdmxCatalogue,
    AdmxError,
    build_catalogue,
    extract_namespaces,
)
from .evidence import ContentClassification
from .safe_io import SafeOpenError, iter_directory, open_directory

SourceKind = Literal["local", "central-store", "vendor-pack", "curated"]


class TemplateError(ValueError):
    """Template repository operation failed."""


@dataclass(frozen=True, slots=True)
class TemplateFile:
    relative_path: str
    sha256: str
    size: int
    content_classification: ContentClassification
    license_note: str


@dataclass(frozen=True, slots=True)
class TemplateSource:
    name: str
    kind: SourceKind
    path: str
    content_classification: ContentClassification
    license_note: str
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


_MICROSOFT_FILE_NAMES: frozenset[str] = frozenset({
    "windows.admx",
    "windows.adml",
    "win10.admx",
    "win10.adml",
    "win11.admx",
    "win11.adml",
    "inetres.admx",
    "inetres.adml",
    "wuau.admx",
    "wuau.adml",
    "defender.admx",
    "defender.adml",
    "onedrive.admx",
    "onedrive.adml",
    "office.admx",
    "office.adml",
    "office16.admx",
    "office16.adml",
})


_RESTRICTIVENESS: dict[ContentClassification, int] = {
    "excluded": 0,
    "hash-reference": 1,
    "in-repo": 2,
}


_MICROSOFT_COPYRIGHT_RE = re.compile(
    r"microsoft.*(?:copyright|\(c\)|©)|(?:copyright|\(c\)|©).*microsoft",
    re.IGNORECASE,
)


def _has_microsoft_copyright(data: bytes) -> bool:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return bool(_MICROSOFT_COPYRIGHT_RE.search(text))


def _is_synthetic_namespace(namespace: str) -> bool:
    return namespace.startswith(
        ("Synthetic.Test.", "Test.", "Local.")
    ) or namespace.startswith("http://studio.local/")


def classify_template_file(
    relative_path: str,
    admx_data: bytes | None,
    adml_data: bytes | None,
) -> tuple[ContentClassification, str]:
    """Mechanically classify the license posture of an ADMX/ADML pair.

    Microsoft-copyrighted material is never classified as ``in-repo``.
    Vendor and unmarked third-party content defaults to ``hash-reference`` so
    only the SHA-256 is retained. Clearly synthetic or test namespaces may be
    ``in-repo``.
    """
    filename = Path(relative_path).name.lower()
    ms_reasons: list[str] = []
    if filename in _MICROSOFT_FILE_NAMES:
        ms_reasons.append(f"known Microsoft file name {filename}")

    targets: tuple[str, ...] = ()
    usings: tuple[str, ...] = ()
    if admx_data is not None:
        with contextlib.suppress(AdmxError):
            targets, usings = extract_namespaces(admx_data)
    for namespace in (*targets, *usings):
        if namespace.startswith("Microsoft."):
            ms_reasons.append(f"Microsoft namespace {namespace}")

    if adml_data is not None and _has_microsoft_copyright(adml_data):
        ms_reasons.append("Microsoft copyright in ADML")

    if ms_reasons:
        return "hash-reference", f"Microsoft-copyrighted: {'; '.join(ms_reasons)}"

    synth_reasons: list[str] = []
    for namespace in (*targets, *usings):
        if _is_synthetic_namespace(namespace):
            synth_reasons.append(f"synthetic namespace {namespace}")

    if synth_reasons:
        return "in-repo", f"Synthetic: {'; '.join(synth_reasons)}"

    if targets or usings:
        return "hash-reference", f"Vendor namespace: {', '.join((*targets, *usings))}"
    return "hash-reference", "Vendor or unknown content (no synthetic namespace declared)"


def _more_restrictive(
    a: ContentClassification, b: ContentClassification
) -> ContentClassification:
    return a if _RESTRICTIVENESS[a] <= _RESTRICTIVENESS[b] else b


def _source_classification(
    files: tuple[TemplateFile, ...],
) -> tuple[ContentClassification, str]:
    if not files:
        return "in-repo", "No files ingested"
    aggregate: ContentClassification = "in-repo"
    for file in files:
        aggregate = _more_restrictive(aggregate, file.content_classification)
    notes = "; ".join(
        f"{file.relative_path}: {file.content_classification}"
        for file in files
        if file.content_classification != "in-repo"
    )
    return aggregate, notes or "All files classified as in-repo"


def _retention_policy(classification: ContentClassification) -> str:
    if classification == "in-repo":
        return "full bytes may be retained"
    if classification == "hash-reference":
        return "hash only, no redistribution"
    if classification == "excluded":
        return "not retained"
    assert_never(classification)


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
#: Aggregate bytes read from one source, every ADMX and ADML in the folder and
#: its locale subfolders counted. Measured 2026-10-29: the Windows Server 2025
#: (10.0.26100) store at C:\Windows\PolicyDefinitions holds 446 files and
#: 7,382,895 bytes (223 ADMX, 223 en-US ADML). 512 MiB is about 70 times that,
#: room for a central store carrying many locales and vendor packs.
_MAX_TEMPLATE_SOURCE_BYTES = 512 * 1024 * 1024
#: Directory entries (files and folders) enumerated in the folder and its
#: immediate subfolders. The same store has 447; 20,000 is about 45 times that.
_MAX_TEMPLATE_SOURCE_ENTRIES = 20_000
_READ_CHUNK = 64 * 1024

_LINK_REFUSAL = (
    "Template folders are read without following symbolic links, junctions or "
    "other reparse points, and may hold only regular files and folders"
)


def _is_admx_name(name: str) -> bool:
    # Path.glob("*.admx") semantics: case-sensitive on POSIX, not on Windows.
    return fnmatch.fnmatch(name, "*.admx")


def _is_adml_name(name: str) -> bool:
    return fnmatch.fnmatch(name, "*.adml")


def _sort_key(name: str) -> tuple[str, str]:
    # Sibling paths sort by their normcase spelling (case-insensitively on
    # Windows), which is the order the path-based reader used.
    return os.path.normcase(name), name


@dataclass(frozen=True, slots=True)
class _Match:
    """An entry located the way ``admx.find_adml`` locates one.

    ``spelling`` is the name ``find_adml`` would have returned: the requested
    spelling when the filesystem's own lookup matches it (so case-insensitively
    on Windows), otherwise the on-disk name. Relative paths in the inventory,
    and so the lock, keep that spelling.
    """

    spelling: str
    name: str


def _find_name(names: Iterable[str], wanted: str) -> _Match | None:
    exact = os.path.normcase(wanted)
    candidates = sorted(names, key=_sort_key)
    for name in candidates:
        if os.path.normcase(name) == exact:
            return _Match(spelling=wanted, name=name)
    folded = wanted.lower()
    for name in candidates:
        if name.lower() == folded:
            return _Match(spelling=name, name=name)
    return None


@dataclass(frozen=True, slots=True)
class _AdmlRead:
    sha256: str
    size: int
    unpaired: tuple[ContentClassification, str] | None
    """Classification as an unpaired ADML, for names ``*.adml`` matches."""


@dataclass(frozen=True, slots=True)
class _Pair:
    relative_path: str
    directory: str | None
    name: str
    data: bytes


class _Budget:
    """Aggregate limits for one source; exceeding one refuses the source."""

    def __init__(self, directory: Path) -> None:
        self._directory = directory
        self._entries = 0
        self._bytes = 0

    def add_entry(self) -> None:
        self._entries += 1
        if self._entries > _MAX_TEMPLATE_SOURCE_ENTRIES:
            raise TemplateError(
                f"Template folder {self._directory} has more than "
                f"{_MAX_TEMPLATE_SOURCE_ENTRIES} files and folders; the source was "
                "not ingested"
            )

    def add_bytes(self, count: int) -> None:
        self._bytes += count
        if self._bytes > _MAX_TEMPLATE_SOURCE_BYTES:
            raise TemplateError(
                f"Template folder {self._directory} holds more than "
                f"{_MAX_TEMPLATE_SOURCE_BYTES} bytes of ADMX and ADML files; the "
                "source was not ingested"
            )


def _read_template_file(fd: int, relative: str, budget: _Budget) -> bytes:
    """Read one open template file, never more than one byte past the limit."""
    data = bytearray()
    while True:
        chunk = os.read(fd, min(_READ_CHUNK, _MAX_TEMPLATE_FILE_SIZE + 1 - len(data)))
        if not chunk:
            return bytes(data)
        data.extend(chunk)
        budget.add_bytes(len(chunk))
        if len(data) > _MAX_TEMPLATE_FILE_SIZE:
            raise TemplateError(
                f"Template file {relative} is larger than the "
                f"{_MAX_TEMPLATE_FILE_SIZE}-byte limit; the source was not ingested"
            )


@contextlib.contextmanager
def _refuse_unsafe_entries(directory: Path) -> Iterator[None]:
    """Turn a ``safe_io`` refusal into a ``TemplateError`` naming the folder.

    A refusal is a link, junction, reparse point or special file, or a no-follow
    open that met one: ``ELOOP`` for a file, ``ENOTDIR`` for a directory opened
    with ``O_DIRECTORY | O_NOFOLLOW``. Any other ``OSError`` (permission, I/O)
    is re-raised unchanged so the caller reports the folder as unreadable.
    """
    try:
        yield
    except SafeOpenError as error:
        cause = error.__cause__
        if (
            isinstance(cause, OSError)
            and not isinstance(cause, SafeOpenError)
            and cause.errno not in (errno.ELOOP, errno.ENOTDIR)
        ):
            raise
        raise TemplateError(
            f"Refused template folder {directory}: {error}. {_LINK_REFUSAL}"
        ) from error


@dataclass(slots=True)
class _Inventory:
    """Everything read from one source folder, each file read exactly once."""

    admx: dict[str, bytes] = field(default_factory=dict)
    root_adml: dict[str, bytes] = field(default_factory=dict)
    subdirectories: list[str] = field(default_factory=list)
    adml: dict[tuple[str | None, str], _AdmlRead] = field(default_factory=dict)
    pairs: dict[str, _Pair] = field(default_factory=dict)


def _record_adml(
    inventory: _Inventory, subdirectory: str | None, name: str, data: bytes
) -> None:
    relative = name if subdirectory is None else str(Path(subdirectory, name))
    unpaired = classify_template_file(relative, None, data) if _is_adml_name(name) else None
    inventory.adml[(subdirectory, name)] = _AdmlRead(
        sha256=_hash_bytes(data), size=len(data), unpaired=unpaired
    )


def _read_root(root_fd: int, budget: _Budget, inventory: _Inventory) -> None:
    for entry in iter_directory(root_fd):
        budget.add_entry()
        if entry.is_directory:
            inventory.subdirectories.append(entry.name)
        elif _is_admx_name(entry.name):
            inventory.admx[entry.name] = _read_template_file(entry.fd, entry.name, budget)
        elif entry.name.lower().endswith(".adml"):
            data = _read_template_file(entry.fd, entry.name, budget)
            inventory.root_adml[entry.name] = data
            _record_adml(inventory, None, entry.name, data)


def _read_locale(
    directory: Path,
    subdirectory: str,
    spelling: str,
    budget: _Budget,
    inventory: _Inventory,
) -> None:
    """Read every ADML in one subfolder; pair those still-unpaired ADMX need.

    Subfolders are read in ``find_adml``'s precedence (en-US, then the rest
    sorted), so only a candidate for a still-unpaired ADMX is kept in memory.
    """
    wanted = {
        Path(name).stem.lower() + ".adml"
        for name in inventory.admx
        if name not in inventory.pairs
    }
    candidates: dict[str, bytes] = {}
    dir_fd = open_directory(directory / subdirectory)
    try:
        for entry in iter_directory(dir_fd):
            budget.add_entry()
            if entry.is_directory or not entry.name.lower().endswith(".adml"):
                continue
            relative = str(Path(subdirectory, entry.name))
            data = _read_template_file(entry.fd, relative, budget)
            _record_adml(inventory, subdirectory, entry.name, data)
            if entry.name.lower() in wanted:
                candidates[entry.name] = data
    finally:
        os.close(dir_fd)
    for admx_name in sorted(inventory.admx, key=_sort_key):
        if admx_name in inventory.pairs:
            continue
        match = _find_name(candidates, Path(admx_name).stem + ".adml")
        if match is not None:
            inventory.pairs[admx_name] = _Pair(
                relative_path=str(Path(spelling, match.spelling)),
                directory=subdirectory,
                name=match.name,
                data=candidates[match.name],
            )


def _read_source(directory: Path) -> _Inventory:
    """Read a template folder once through the ``safe_io`` boundary.

    The folder and every path component above it are opened without following
    links; each entry of the folder and of its immediate subfolders is opened
    relative to its retained parent and refused if it is a link, junction,
    reparse point or special file. Every ADMX in the folder and every ADML in
    it or a subfolder is read exactly once, through the handle that
    enumeration opened, and those bytes feed both the hash and the parser.
    ADML pairing follows ``admx.find_adml``: a sibling, then ``en-US``, then
    the first other subfolder in sorted order.
    """
    budget = _Budget(directory)
    inventory = _Inventory()
    with _refuse_unsafe_entries(directory):
        root_fd = open_directory(directory)
        try:
            _read_root(root_fd, budget, inventory)
        finally:
            os.close(root_fd)
        for admx_name in sorted(inventory.admx, key=_sort_key):
            match = _find_name(inventory.root_adml, Path(admx_name).stem + ".adml")
            if match is not None:
                inventory.pairs[admx_name] = _Pair(
                    relative_path=match.spelling,
                    directory=None,
                    name=match.name,
                    data=inventory.root_adml[match.name],
                )
        inventory.root_adml.clear()
        en_us = _find_name(inventory.subdirectories, "en-US")
        order: list[tuple[str, str]] = []
        if en_us is not None:
            order.append((en_us.name, en_us.spelling))
        order.extend(
            (name, name)
            for name in sorted(inventory.subdirectories, key=_sort_key)
            if en_us is None or name != en_us.name
        )
        for subdirectory, spelling in order:
            _read_locale(directory, subdirectory, spelling, budget, inventory)
    return inventory


def _inventory_files(inventory: _Inventory) -> tuple[TemplateFile, ...]:
    files: list[TemplateFile] = []
    paired: set[tuple[str | None, str]] = set()
    for admx_name in sorted(inventory.admx, key=_sort_key):
        admx_data = inventory.admx[admx_name]
        pair = inventory.pairs.get(admx_name)
        classification, note = classify_template_file(
            admx_name, admx_data, pair.data if pair is not None else None
        )
        files.append(TemplateFile(
            relative_path=admx_name,
            sha256=_hash_bytes(admx_data),
            size=len(admx_data),
            content_classification=classification,
            license_note=note,
        ))
        if pair is None:
            continue
        paired.add((pair.directory, pair.name))
        adml_classification = classification
        adml_note = note
        if (
            Path(pair.relative_path).name.lower() in _MICROSOFT_FILE_NAMES
            or _has_microsoft_copyright(pair.data)
        ):
            adml_classification = "hash-reference"
            adml_note = f"Microsoft indicator in paired ADML {pair.relative_path}"
        files.append(TemplateFile(
            relative_path=pair.relative_path,
            sha256=_hash_bytes(pair.data),
            size=len(pair.data),
            content_classification=adml_classification,
            license_note=adml_note,
        ))

    locations: list[str | None] = [None, *sorted(inventory.subdirectories, key=_sort_key)]
    for location in locations:
        names = sorted(
            (name for (where, name) in inventory.adml if where == location),
            key=_sort_key,
        )
        for name in names:
            read = inventory.adml[(location, name)]
            if read.unpaired is None or (location, name) in paired:
                continue
            classification, note = read.unpaired
            files.append(TemplateFile(
                relative_path=name if location is None else str(Path(location, name)),
                sha256=read.sha256,
                size=read.size,
                content_classification=classification,
                license_note=note,
            ))
    return tuple(files)


def ingest_source(
    name: str,
    kind: SourceKind,
    directory: Path,
    claimed_classification: ContentClassification | None = None,
) -> IngestResult:
    """Ingest a template source directory with per-file resilience.

    Malformed ADMX/ADML files are skipped and reported in errors; they do
    not abort the entire ingest. Each file is mechanically classified for
    licensing; if ``claimed_classification`` is ``in-repo`` but any file is
    mechanically classified as non-``in-repo`` (e.g. Microsoft or vendor
    ADMX), ingestion is rejected.

    The folder is read once (see ``_read_source``): the lock built from the
    result attests exactly the bytes its catalogue was parsed from. A link,
    junction or reparse point anywhere in the folder or its path, a file over
    ``_MAX_TEMPLATE_FILE_SIZE``, or a folder over the aggregate byte or entry
    limit refuses the whole source with ``TemplateError``. ``directory`` is
    opened as given; a caller holding an operator-chosen path resolves it first.
    """
    if not directory.is_dir():
        raise TemplateError(f"Template directory does not exist: {directory}")

    inventory = _read_source(directory)
    files = _inventory_files(inventory)
    if claimed_classification == "in-repo":
        for file in files:
            if file.content_classification != "in-repo":
                raise TemplateError(
                    f"Source {name!r} claims in-repo classification but "
                    f"{file.relative_path} is classified as "
                    f"{file.content_classification}: {file.license_note}"
                )

    aggregate_classification, aggregate_note = _source_classification(files)
    if claimed_classification is not None:
        aggregate_classification = claimed_classification
        aggregate_note = f"Claimed {claimed_classification}; {aggregate_note}"
    source = TemplateSource(
        name=name,
        kind=kind,
        path=str(directory),
        content_classification=aggregate_classification,
        license_note=aggregate_note,
        files=files,
    )

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
    errors: list[IngestError] = []

    for admx_name in sorted(inventory.admx, key=_sort_key):
        pair = inventory.pairs.get(admx_name)
        if pair is None:
            errors.append(IngestError(
                relative_path=admx_name, message="No matching ADML found", kind="missing_adml"
            ))
            continue
        try:
            catalogue = build_catalogue(inventory.admx[admx_name], pair.data)
        except (AdmxError, ValueError) as exc:
            errors.append(IngestError(
                relative_path=admx_name, message=str(exc), kind="parse_error"
            ))
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
    policy_map: dict[str, list[tuple[str, str]]] = {}
    missing: list[MissingAdml] = []

    for result in sources:
        src_name = result.source.name

        for ns_decl in result.catalogue.target_namespaces:
            bucket = namespace_map.setdefault(ns_decl.namespace, [])
            if src_name not in bucket:
                bucket.append(src_name)

        for tf in result.source.files:
            file_map.setdefault(tf.relative_path, []).append((src_name, tf.sha256))

        for policy in result.catalogue.policies:
            qid = policy.qualified_id
            el_count = len(policy.enabled_list.items) if policy.enabled_list else 0
            dl_count = len(policy.disabled_list.items) if policy.disabled_list else 0
            semantic = (
                f"{policy.key}|{policy.value_name}|{policy.namespace}|"
                f"{policy.class_}|{policy.enabled_value}|{policy.disabled_value}|"
                f"{len(policy.elements)}|{el_count}|{dl_count}"
            )
            policy_map.setdefault(qid, []).append((src_name, _hash_bytes(
                semantic.encode()
            )))

        for err in result.errors:
            if err.kind == "missing_adml":
                missing.append(MissingAdml(admx_path=err.relative_path, source=src_name))

    ns_collisions: list[NamespaceCollision] = []
    for ns, srcs in sorted(namespace_map.items()):
        if len(srcs) > 1:
            ns_collisions.append(NamespaceCollision(namespace=ns, sources=tuple(sorted(srcs))))

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
            hashes = {h for _, h in policy_entries}
            if len(hashes) > 1:
                drift.append(PolicyDrift(
                    qualified_id=qid,
                    sources=tuple(sorted(set(s for s, _ in policy_entries))),
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
