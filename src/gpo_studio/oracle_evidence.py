"""Windows external-oracle evidence contract and semantic XML normalizer.

Plan 033 evidence is deliberately separate from the release evidence packs in
``evidence.py``.  This module records one lab execution in enough detail to
audit the oracle, owning state boundary, commands, artifacts, and cleanup
result.  Passing records may later be admitted into a release evidence pack.

The XML normalizer is intentionally conservative.  It recognizes only a small,
versioned set of generated values and Microsoft-added defaults.  Unknown
elements, attributes, typed values, actions, filters, extension GUIDs, and path
values remain part of the comparison.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import PureWindowsPath
from typing import Literal, cast

from .xml_safety import parse_xml_bounded

MANIFEST_SCHEMA_VERSION = 1
NORMALIZER_VERSION = "gpo-studio.windows-oracle-xml.v1"
MAX_ORACLE_XML_BYTES = 32 * 1024 * 1024

BoundaryOwner = Literal[
    "gpo-backup-content",
    "gpo-ad-object-security",
    "wmi-filter-object-association",
    "som-link-block-inheritance",
    "endpoint-resultant-state",
]
EvidenceState = Literal["pass", "fail", "unsupported", "inconclusive"]
ArtifactRole = Literal["input", "output", "raw-log", "snapshot"]

_BOUNDARIES: frozenset[str] = frozenset(
    {
        "gpo-backup-content",
        "gpo-ad-object-security",
        "wmi-filter-object-association",
        "som-link-block-inheritance",
        "endpoint-resultant-state",
    }
)
_EVIDENCE_STATES: frozenset[str] = frozenset(
    {"pass", "fail", "unsupported", "inconclusive"}
)
_ARTIFACT_ROLES: frozenset[str] = frozenset(
    {"input", "output", "raw-log", "snapshot"}
)
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_BACKUP_ID_RE = re.compile(
    r"^\{?[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\}?$"
)


class OracleEvidenceError(ValueError):
    """Raised when evidence or normalization input violates the contract."""


@dataclass(frozen=True, slots=True)
class SourceState:
    commit: str
    dirty: bool


@dataclass(frozen=True, slots=True)
class Fixture:
    fixture_id: str
    generation_recipe: str


@dataclass(frozen=True, slots=True)
class WindowsEnvironment:
    server_build: str
    client_build: str
    powershell_edition: str
    powershell_version: str
    group_policy_module_version: str
    gpmc_version: str
    locale: str
    lgpo_sha256: str


@dataclass(frozen=True, slots=True)
class ToolFingerprint:
    name: str
    version: str
    sha256: str | None = None


@dataclass(frozen=True, slots=True)
class ArtifactDigest:
    artifact_id: str
    role: ArtifactRole
    relative_path: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class CommandEvidence:
    command_id: str
    command_line: str
    exit_code: int
    stdout_sha256: str | None
    stderr_sha256: str | None
    relevant_event_ids: tuple[int, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class SemanticComparison:
    assertion_id: str
    oracle: str
    boundary_owner: BoundaryOwner
    normalizer_version: str
    expected_sha256: str
    observed_sha256: str
    equal: bool
    differences: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class CleanupEvidence:
    attempted: bool
    succeeded: bool
    snapshot_restored: bool
    removed_resources: tuple[str, ...] = field(default_factory=tuple)
    failures: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class CapabilityResult:
    matrix_row: str
    evidence_state: EvidenceState


@dataclass(frozen=True, slots=True)
class OracleEvidenceManifest:
    schema_version: int
    run_id: str
    started_at: str
    completed_at: str
    source: SourceState
    fixture: Fixture
    environment: WindowsEnvironment
    tools: tuple[ToolFingerprint, ...]
    artifacts: tuple[ArtifactDigest, ...]
    commands: tuple[CommandEvidence, ...]
    comparisons: tuple[SemanticComparison, ...]
    cleanup: CleanupEvidence
    capability: CapabilityResult


def _mapping(raw: object, label: str) -> Mapping[str, object]:
    if not isinstance(raw, dict):
        raise OracleEvidenceError(f"{label} must be a JSON object")
    return raw


def _exact_keys(
    data: Mapping[str, object],
    *,
    required: frozenset[str],
    optional: frozenset[str] = frozenset(),
    label: str,
) -> None:
    keys = frozenset(data)
    missing = sorted(required - keys)
    unknown = sorted(keys - required - optional)
    if missing:
        raise OracleEvidenceError(f"{label} is missing required keys: {missing}")
    if unknown:
        raise OracleEvidenceError(f"{label} has unknown keys: {unknown}")


def _string(data: Mapping[str, object], key: str, label: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise OracleEvidenceError(f"{label}.{key} must be a non-empty string")
    return value


def _bool(data: Mapping[str, object], key: str, label: str) -> bool:
    value = data.get(key)
    if not isinstance(value, bool):
        raise OracleEvidenceError(f"{label}.{key} must be a boolean")
    return value


def _integer(data: Mapping[str, object], key: str, label: str) -> int:
    value = data.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise OracleEvidenceError(f"{label}.{key} must be an integer")
    return value


def _optional_sha256(data: Mapping[str, object], key: str, label: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise OracleEvidenceError(f"{label}.{key} must be a 64-digit SHA-256 or null")
    return value.lower()


def _sha256(data: Mapping[str, object], key: str, label: str) -> str:
    value = _optional_sha256(data, key, label)
    if value is None:
        raise OracleEvidenceError(f"{label}.{key} must be a 64-digit SHA-256")
    return value


def _string_tuple(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise OracleEvidenceError(f"{label} must be an array")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item:
            raise OracleEvidenceError(f"{label} items must be non-empty strings")
        result.append(item)
    return tuple(result)


def _object_tuple(value: object, label: str) -> tuple[object, ...]:
    if not isinstance(value, list):
        raise OracleEvidenceError(f"{label} must be an array")
    return tuple(value)


def _timestamp(data: Mapping[str, object], key: str) -> dt.datetime:
    value = _string(data, key, "manifest")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise OracleEvidenceError(
            f"manifest.{key} must be an RFC 3339 timestamp"
        ) from exc
    if parsed.tzinfo is None:
        raise OracleEvidenceError(f"manifest.{key} must include a timezone")
    return parsed


def _source(raw: object) -> SourceState:
    data = _mapping(raw, "source")
    _exact_keys(data, required=frozenset({"commit", "dirty"}), label="source")
    return SourceState(
        commit=_string(data, "commit", "source"),
        dirty=_bool(data, "dirty", "source"),
    )


def _fixture(raw: object) -> Fixture:
    data = _mapping(raw, "fixture")
    _exact_keys(
        data,
        required=frozenset({"fixture_id", "generation_recipe"}),
        label="fixture",
    )
    return Fixture(
        fixture_id=_string(data, "fixture_id", "fixture"),
        generation_recipe=_string(data, "generation_recipe", "fixture"),
    )


def _environment(raw: object) -> WindowsEnvironment:
    data = _mapping(raw, "environment")
    keys = frozenset(
        {
            "server_build",
            "client_build",
            "powershell_edition",
            "powershell_version",
            "group_policy_module_version",
            "gpmc_version",
            "locale",
            "lgpo_sha256",
        }
    )
    _exact_keys(data, required=keys, label="environment")
    return WindowsEnvironment(
        server_build=_string(data, "server_build", "environment"),
        client_build=_string(data, "client_build", "environment"),
        powershell_edition=_string(data, "powershell_edition", "environment"),
        powershell_version=_string(data, "powershell_version", "environment"),
        group_policy_module_version=_string(
            data, "group_policy_module_version", "environment"
        ),
        gpmc_version=_string(data, "gpmc_version", "environment"),
        locale=_string(data, "locale", "environment"),
        lgpo_sha256=_sha256(data, "lgpo_sha256", "environment"),
    )


def _tool(raw: object, index: int) -> ToolFingerprint:
    label = f"tools[{index}]"
    data = _mapping(raw, label)
    _exact_keys(
        data,
        required=frozenset({"name", "version", "sha256"}),
        label=label,
    )
    return ToolFingerprint(
        name=_string(data, "name", label),
        version=_string(data, "version", label),
        sha256=_optional_sha256(data, "sha256", label),
    )


def _artifact(raw: object, index: int) -> ArtifactDigest:
    label = f"artifacts[{index}]"
    data = _mapping(raw, label)
    _exact_keys(
        data,
        required=frozenset(
            {"artifact_id", "role", "relative_path", "sha256", "size_bytes"}
        ),
        label=label,
    )
    role = data.get("role")
    if role not in _ARTIFACT_ROLES:
        raise OracleEvidenceError(f"{label}.role is invalid: {role!r}")
    relative_path = _string(data, "relative_path", label)
    parsed_path = PureWindowsPath(relative_path)
    if (
        parsed_path.is_absolute()
        or relative_path.startswith(("/", "\\"))
        or ".." in parsed_path.parts
    ):
        raise OracleEvidenceError(f"{label}.relative_path must be relative")
    size_bytes = _integer(data, "size_bytes", label)
    if size_bytes < 0:
        raise OracleEvidenceError(f"{label}.size_bytes cannot be negative")
    return ArtifactDigest(
        artifact_id=_string(data, "artifact_id", label),
        role=cast(ArtifactRole, role),
        relative_path=relative_path,
        sha256=_sha256(data, "sha256", label),
        size_bytes=size_bytes,
    )


def _command(raw: object, index: int) -> CommandEvidence:
    label = f"commands[{index}]"
    data = _mapping(raw, label)
    _exact_keys(
        data,
        required=frozenset(
            {
                "command_id",
                "command_line",
                "exit_code",
                "stdout_sha256",
                "stderr_sha256",
                "relevant_event_ids",
            }
        ),
        label=label,
    )
    raw_events = data.get("relevant_event_ids")
    if not isinstance(raw_events, list) or any(
        not isinstance(item, int) or isinstance(item, bool) or item < 0
        for item in raw_events
    ):
        raise OracleEvidenceError(
            f"{label}.relevant_event_ids must be an array of non-negative integers"
        )
    return CommandEvidence(
        command_id=_string(data, "command_id", label),
        command_line=_string(data, "command_line", label),
        exit_code=_integer(data, "exit_code", label),
        stdout_sha256=_optional_sha256(data, "stdout_sha256", label),
        stderr_sha256=_optional_sha256(data, "stderr_sha256", label),
        relevant_event_ids=tuple(raw_events),
    )


def _comparison(raw: object, index: int) -> SemanticComparison:
    label = f"comparisons[{index}]"
    data = _mapping(raw, label)
    _exact_keys(
        data,
        required=frozenset(
            {
                "assertion_id",
                "oracle",
                "boundary_owner",
                "normalizer_version",
                "expected_sha256",
                "observed_sha256",
                "equal",
                "differences",
            }
        ),
        label=label,
    )
    boundary = data.get("boundary_owner")
    if boundary not in _BOUNDARIES:
        raise OracleEvidenceError(f"{label}.boundary_owner is invalid: {boundary!r}")
    differences = _string_tuple(data.get("differences"), f"{label}.differences")
    equal = _bool(data, "equal", label)
    if equal and differences:
        raise OracleEvidenceError(f"{label} cannot be equal and contain differences")
    if not equal and not differences:
        raise OracleEvidenceError(f"{label} must explain a failed comparison")
    return SemanticComparison(
        assertion_id=_string(data, "assertion_id", label),
        oracle=_string(data, "oracle", label),
        boundary_owner=cast(BoundaryOwner, boundary),
        normalizer_version=_string(data, "normalizer_version", label),
        expected_sha256=_sha256(data, "expected_sha256", label),
        observed_sha256=_sha256(data, "observed_sha256", label),
        equal=equal,
        differences=differences,
    )


def _cleanup(raw: object) -> CleanupEvidence:
    data = _mapping(raw, "cleanup")
    _exact_keys(
        data,
        required=frozenset(
            {
                "attempted",
                "succeeded",
                "snapshot_restored",
                "removed_resources",
                "failures",
            }
        ),
        label="cleanup",
    )
    attempted = _bool(data, "attempted", "cleanup")
    succeeded = _bool(data, "succeeded", "cleanup")
    failures = _string_tuple(data.get("failures"), "cleanup.failures")
    if succeeded and (not attempted or failures):
        raise OracleEvidenceError(
            "cleanup cannot succeed unless attempted and free of failures"
        )
    return CleanupEvidence(
        attempted=attempted,
        succeeded=succeeded,
        snapshot_restored=_bool(data, "snapshot_restored", "cleanup"),
        removed_resources=_string_tuple(
            data.get("removed_resources"), "cleanup.removed_resources"
        ),
        failures=failures,
    )


def _capability(raw: object) -> CapabilityResult:
    data = _mapping(raw, "capability")
    _exact_keys(
        data,
        required=frozenset({"matrix_row", "evidence_state"}),
        label="capability",
    )
    state = data.get("evidence_state")
    if state not in _EVIDENCE_STATES:
        raise OracleEvidenceError(f"capability.evidence_state is invalid: {state!r}")
    return CapabilityResult(
        matrix_row=_string(data, "matrix_row", "capability"),
        evidence_state=cast(EvidenceState, state),
    )


def parse_oracle_manifest(raw: object) -> OracleEvidenceManifest:
    """Parse and strictly validate one Plan 033 execution manifest."""
    data = _mapping(raw, "manifest")
    _exact_keys(
        data,
        required=frozenset(
            {
                "schema_version",
                "run_id",
                "started_at",
                "completed_at",
                "source",
                "fixture",
                "environment",
                "tools",
                "artifacts",
                "commands",
                "comparisons",
                "cleanup",
                "capability",
            }
        ),
        label="manifest",
    )
    schema_version = _integer(data, "schema_version", "manifest")
    if schema_version != MANIFEST_SCHEMA_VERSION:
        raise OracleEvidenceError(
            f"manifest.schema_version must be {MANIFEST_SCHEMA_VERSION}"
        )
    tools = tuple(
        _tool(item, index)
        for index, item in enumerate(_object_tuple(data.get("tools"), "tools"))
    )
    if not tools:
        raise OracleEvidenceError("manifest must contain at least one tool")
    artifacts = tuple(
        _artifact(item, index)
        for index, item in enumerate(_object_tuple(data.get("artifacts"), "artifacts"))
    )
    commands = tuple(
        _command(item, index)
        for index, item in enumerate(_object_tuple(data.get("commands"), "commands"))
    )
    comparisons = tuple(
        _comparison(item, index)
        for index, item in enumerate(
            _object_tuple(data.get("comparisons"), "comparisons")
        )
    )
    if not artifacts:
        raise OracleEvidenceError("manifest must contain at least one artifact")
    artifact_roles = {item.role for item in artifacts}
    if not {"input", "output"}.issubset(artifact_roles):
        raise OracleEvidenceError(
            "manifest artifacts must contain at least one input and one output"
        )
    if not commands:
        raise OracleEvidenceError("manifest must contain at least one command")
    if not comparisons:
        raise OracleEvidenceError("manifest must contain at least one comparison")
    assertion_ids = [item.assertion_id for item in comparisons]
    if len(assertion_ids) != len(set(assertion_ids)):
        raise OracleEvidenceError("comparison assertion_id values must be unique")
    artifact_ids = [item.artifact_id for item in artifacts]
    if len(artifact_ids) != len(set(artifact_ids)):
        raise OracleEvidenceError("artifact_id values must be unique")
    command_ids = [item.command_id for item in commands]
    if len(command_ids) != len(set(command_ids)):
        raise OracleEvidenceError("command_id values must be unique")
    started_at = _timestamp(data, "started_at")
    completed_at = _timestamp(data, "completed_at")
    if completed_at < started_at:
        raise OracleEvidenceError("manifest.completed_at cannot precede started_at")
    cleanup = _cleanup(data.get("cleanup"))
    capability = _capability(data.get("capability"))
    if capability.evidence_state == "pass":
        if any(command.exit_code != 0 for command in commands):
            raise OracleEvidenceError(
                "passing capability cannot contain a failed command"
            )
        if any(not comparison.equal for comparison in comparisons):
            raise OracleEvidenceError(
                "passing capability cannot contain a failed comparison"
            )
        if not cleanup.succeeded or not cleanup.snapshot_restored:
            raise OracleEvidenceError(
                "passing capability requires successful cleanup and snapshot restore"
            )
    return OracleEvidenceManifest(
        schema_version=schema_version,
        run_id=_string(data, "run_id", "manifest"),
        started_at=_string(data, "started_at", "manifest"),
        completed_at=_string(data, "completed_at", "manifest"),
        source=_source(data.get("source")),
        fixture=_fixture(data.get("fixture")),
        environment=_environment(data.get("environment")),
        tools=tools,
        artifacts=artifacts,
        commands=commands,
        comparisons=comparisons,
        cleanup=cleanup,
        capability=capability,
    )


def canonical_manifest_bytes(manifest: OracleEvidenceManifest) -> bytes:
    """Return stable UTF-8 JSON bytes for signing or hashing."""
    return json.dumps(
        dataclasses.asdict(manifest),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def canonical_manifest_hash(manifest: OracleEvidenceManifest) -> str:
    """Return the bare lowercase SHA-256 of canonical manifest bytes."""
    return hashlib.sha256(canonical_manifest_bytes(manifest)).hexdigest()


_GPP_ITEM_TAGS: frozenset[str] = frozenset(
    {
        "Application",
        "DataSource",
        "Device",
        "Drive",
        "EnvironmentVariable",
        "File",
        "Folder",
        "FolderOptions",
        "Group",
        "ImmediateTask",
        "ImmediateTaskV2",
        "Ini",
        "LocalPrinter",
        "LocalUser",
        "MappedPrinter",
        "NetworkShare",
        "PortPrinter",
        "PowerItem",
        "PowerScheme",
        "Printer",
        "Registry",
        "RegionalOptions",
        "ScheduledTask",
        "ScheduledTaskV2",
        "Service",
        "Shortcut",
        "StartMenuTaskbar",
    }
)
_PATH_ATTRIBUTES: frozenset[str] = frozenset(
    {
        "frompath",
        "iconpath",
        "location",
        "path",
        "shortcutpath",
        "sourceexpandedpath",
        "startin",
        "targetpath",
    }
)
_MICROSOFT_DEFAULTS: Mapping[str, str] = {
    "bypassErrors": "0",
    "disabled": "0",
    "removePolicy": "0",
}
_NORMALIZED_UID = "{NORMALIZED-GENERATED-UID}"
_NORMALIZED_TIMESTAMP = "NORMALIZED-GENERATED-TIMESTAMP"
_NORMALIZED_BACKUP_ID = "{NORMALIZED-BACKUP-ID}"
_BACKUP_MANIFEST_NAMESPACE = (
    "http://www.microsoft.com/GroupPolicy/GPOOperations/Manifest"
)


@dataclass(frozen=True, slots=True)
class NormalizedXml:
    version: str
    document: Mapping[str, object]

    def canonical_bytes(self) -> bytes:
        return json.dumps(
            {"version": self.version, "document": self.document},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")

    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()


@dataclass(frozen=True, slots=True)
class XmlSemanticResult:
    equal: bool
    expected_sha256: str
    observed_sha256: str
    differences: tuple[str, ...]
    normalizer_version: str = NORMALIZER_VERSION


def _local_name(name: str) -> str:
    return name.rsplit("}", 1)[-1]


def _namespace(name: str) -> str | None:
    if not name.startswith("{"):
        return None
    return name[1:].split("}", 1)[0]


def _meaningful_text(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    return value


def _normalized_attribute(element_name: str, name: str, value: str) -> str:
    local_name = _local_name(name)
    if element_name in _GPP_ITEM_TAGS and local_name == "uid":
        return _NORMALIZED_UID
    if element_name in _GPP_ITEM_TAGS and local_name == "changed":
        return _NORMALIZED_TIMESTAMP
    if element_name == "FilterRunOnce" and local_name == "id":
        return _NORMALIZED_UID
    if local_name.casefold() in _PATH_ATTRIBUTES:
        return value.casefold()
    return value


def _normalized_element(
    element: ET.Element,
    *,
    parent_name: str | None = None,
    parent_namespace: str | None = None,
) -> Mapping[str, object]:
    element_name = _local_name(element.tag)
    element_namespace = _namespace(element.tag)
    attributes: dict[str, str] = {
        name: _normalized_attribute(element_name, name, value)
        for name, value in sorted(element.attrib.items())
    }
    if element_name in _GPP_ITEM_TAGS:
        for name, value in _MICROSOFT_DEFAULTS.items():
            attributes.setdefault(name, value)
    text = _meaningful_text(element.text)
    is_backup_manifest_field = (
        parent_name == "BackupInst"
        and parent_namespace == _BACKUP_MANIFEST_NAMESPACE
        and element_namespace == _BACKUP_MANIFEST_NAMESPACE
    )
    if is_backup_manifest_field and element_name in {"ID", "BackupTime"} and text is not None:
        text = (
            _NORMALIZED_BACKUP_ID
            if element_name == "ID"
            else _NORMALIZED_TIMESTAMP
        )
    return {
        "tag": element.tag,
        "attributes": attributes,
        "text": text,
        "children": [
            _normalized_element(
                child,
                parent_name=element_name,
                parent_namespace=element_namespace,
            )
            for child in element
        ],
    }


def normalize_xml_semantics(xml: str | bytes) -> NormalizedXml:
    """Normalize only explicitly supported non-semantic XML differences."""
    raw = xml.encode("utf-8") if isinstance(xml, str) else xml
    lowered = raw.lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        raise OracleEvidenceError("DTD and entity declarations are not supported")
    root = parse_xml_bounded(
        raw,
        max_size=MAX_ORACLE_XML_BYTES,
        error_class=OracleEvidenceError,
    )
    return NormalizedXml(
        version=NORMALIZER_VERSION,
        document=_normalized_element(root),
    )


def _semantic_differences(
    expected: object,
    observed: object,
    *,
    path: str = "$",
    limit: int = 100,
) -> list[str]:
    if expected == observed:
        return []
    if limit <= 0:
        return [f"{path}: difference limit reached"]
    if isinstance(expected, dict) and isinstance(observed, dict):
        result: list[str] = []
        for key in sorted(set(expected) | set(observed)):
            child_path = f"{path}.{key}"
            if key not in expected:
                result.append(f"{child_path}: unexpected")
            elif key not in observed:
                result.append(f"{child_path}: missing")
            else:
                result.extend(
                    _semantic_differences(
                        expected[key],
                        observed[key],
                        path=child_path,
                        limit=limit - len(result),
                    )
                )
            if len(result) >= limit:
                break
        return result
    if isinstance(expected, list) and isinstance(observed, list):
        result = []
        if len(expected) != len(observed):
            result.append(f"{path}: length {len(expected)} != {len(observed)}")
        for index, (expected_item, observed_item) in enumerate(
            zip(expected, observed, strict=False)
        ):
            result.extend(
                _semantic_differences(
                    expected_item,
                    observed_item,
                    path=f"{path}[{index}]",
                    limit=limit - len(result),
                )
            )
            if len(result) >= limit:
                break
        return result
    return [f"{path}: {expected!r} != {observed!r}"]


def compare_xml_semantics(expected: str | bytes, observed: str | bytes) -> XmlSemanticResult:
    """Compare XML and surface every unsupported difference as a failure."""
    normalized_expected = normalize_xml_semantics(expected)
    normalized_observed = normalize_xml_semantics(observed)
    differences = tuple(
        _semantic_differences(
            normalized_expected.document,
            normalized_observed.document,
        )
    )
    return XmlSemanticResult(
        equal=not differences,
        expected_sha256=normalized_expected.sha256(),
        observed_sha256=normalized_observed.sha256(),
        differences=differences,
    )


def normalize_backup_relative_path(relative_path: str, backup_id: str) -> str:
    """Normalize only the explicit native backup-ID path segment.

    The GPO ID and extension GUIDs elsewhere in a backup are intentionally not
    touched.  Callers must supply the exact backup ID captured for the run.
    """
    if not _BACKUP_ID_RE.fullmatch(backup_id):
        raise OracleEvidenceError("backup_id must be a GUID")
    normalized_id = backup_id.strip("{}").casefold()
    path = PureWindowsPath(relative_path)
    if path.is_absolute():
        raise OracleEvidenceError("backup path must be relative")
    parts = list(path.parts)
    matching_indexes = [
        index
        for index, part in enumerate(parts)
        if part.strip("{}").casefold() == normalized_id
    ]
    if len(matching_indexes) != 1:
        raise OracleEvidenceError(
            "backup path must contain the supplied backup ID exactly once"
        )
    parts[matching_indexes[0]] = _NORMALIZED_BACKUP_ID
    return str(PureWindowsPath(*parts))
