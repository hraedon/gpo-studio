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
import difflib
import hashlib
import json
import re
import subprocess
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
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
_COMMIT_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
# command_id is interpolated into stream filenames (commands/<id>.stdout.txt),
# so it must be a safe filename component: no path separators, traversal, drive
# letters, or shell metacharacters.
_COMMAND_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
SUPPORTED_NORMALIZER_VERSIONS: frozenset[str] = frozenset({NORMALIZER_VERSION})
FROZEN_LOCALES: frozenset[str] = frozenset({"en-US"})
_BACKUP_ID_RE = re.compile(
    r"^\{?[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\}?$"
)


CLIENT_NOT_TESTED = "not-tested"

# Matches the trailing build number of an OS build string, tolerating a UBR
# suffix. `run-evidence.ps1` currently composes this as "$Caption $BuildNumber",
# which never carries a UBR, but that is a property of one capture site far from
# here: a lane that switched to a UBR-bearing source (CurrentBuildNumber +
# UBR, or `[Environment]::OSVersion`) would otherwise silently yield no family
# and downgrade every run to inconclusive.
_BUILD_FAMILY = re.compile(r"(\d{5,})(?:\.\d+)?\s*$")


@dataclass(frozen=True, slots=True)
class FrozenEnvironment:
    """The frozen qualification profile for Plan 033.

    Qualification matches the OS and PowerShell **build family**, not an exact
    servicing revision. Pinning the revision meant every Patch Tuesday silently
    downgraded otherwise-valid runs to ``inconclusive``, which trains reviewers
    to ignore the signal; the family is what actually determines Group Policy
    behavior. The exact revision is still recorded in every manifest, so a run
    remains reproducible and a suspected servicing regression is still
    diagnosable after the fact.

    The profile deliberately does **not** gate on tools the harness never
    invokes. ``lgpo_sha256`` is recorded but not qualified: no lane executes
    ``LGPO.exe``: it is only hashed. Gating on it made runs fail for a binary
    that never influenced a single byte of evidence.

    Mirrors ``docs/plan-033/environment-spec.md``; keep the two in sync.
    """

    server_build_family: str
    client_build_family: str
    powershell_edition: str
    powershell_version_family: str
    group_policy_module_version: str
    gpmc_version: str
    locale: str


FROZEN_ENVIRONMENT = FrozenEnvironment(
    server_build_family="26100",
    client_build_family="26200",
    powershell_edition="Desktop",
    powershell_version_family="5.1.26100",
    group_policy_module_version="1.0.0.0",
    gpmc_version="built-in",
    locale="en-US",
)


class OracleEvidenceError(ValueError):
    """Raised when evidence or normalization input violates the contract."""


class IntegrityViolation(OracleEvidenceError):
    """Raised when an evidence pack's files do not match their recorded hashes.

    Distinct from a contract violation so callers can tell "the manifest is
    malformed" apart from "the manifest is well-formed but its evidence files
    have been altered or lost since the run".
    """


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
    expected_artifact_id: str
    observed_artifact_id: str
    expected_sha256: str
    observed_sha256: str
    equal: bool
    differences: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class CleanupEvidence:
    attempted: bool
    succeeded: bool
    state_restored: bool
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
        or parsed_path.drive
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
                "expected_artifact_id",
                "observed_artifact_id",
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
        expected_artifact_id=_string(data, "expected_artifact_id", label),
        observed_artifact_id=_string(data, "observed_artifact_id", label),
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
                "state_restored",
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
        state_restored=_bool(data, "state_restored", "cleanup"),
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


EVIDENCE_TAG_PREFIX = "evidence/"

_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def evidence_tag_name(run_id: str) -> str:
    """Return the git tag that preserves the source commit for ``run_id``.

    A certified manifest binds itself to the commit it was produced from, but
    squash-merging a PR orphans that commit: it becomes unreachable from
    ``main`` and vanishes at the next ``gc``, so the binding cannot be verified
    from a fresh clone. Tagging keeps the commit alive without changing the
    merge policy. See issue #22.

    Rejects a run id that would produce a malformed or ambiguous ref, so a
    corrupt manifest cannot create a tag that shadows something else.
    """
    if not _RUN_ID.match(run_id):
        raise OracleEvidenceError(
            f"run_id {run_id!r} is not safe to use as a git ref; expected "
            f"alphanumerics, dot, dash, or underscore"
        )
    # git-check-ref-format rejects these independently of the character set.
    if ".." in run_id or run_id.endswith((".lock", ".")):
        raise OracleEvidenceError(f"run_id {run_id!r} would create an invalid git ref")
    return f"{EVIDENCE_TAG_PREFIX}{run_id}"


def assert_evidence_tag_writable(repo_root: Path) -> None:
    """Raise if ``git tag -a`` could not create a tag here.

    Called before a finalizer writes its verdict, so that a run which cannot be
    bound to a commit fails *before* leaving a durable "passed" file behind. The
    concrete case is a container or CI shell with no configured git identity:
    ``git tag -a`` exits 128 with "Committer identity unknown", and a finalizer
    that had already written its verdict would exit non-zero while leaving on
    disk a certification nothing preserves the source tree for.
    """
    identity = subprocess.run(
        ["git", "-C", str(repo_root), "var", "GIT_COMMITTER_IDENT"],
        capture_output=True,
        text=True,
        check=False,
    )
    if identity.returncode != 0:
        raise OracleEvidenceError(
            "git has no committer identity, so this run could not be bound to a "
            f"commit by an evidence tag: {identity.stderr.strip()}"
        )


def tag_evidence_commit(repo_root: Path, run_id: str, commit: str) -> str:
    """Tag the source commit a certified run binds itself to.

    Squash-merging orphans that commit: it becomes unreachable from ``main`` and
    is collected eventually, after which the manifest's provenance claim cannot
    be checked from a fresh clone. The tag costs nothing, keeps the merge policy
    unchanged, and makes the binding permanent. See issue #22.

    This lives here, rather than in one lane's finalizer, because every lane
    needs it. It was originally written for the WP-0 finalizer alone, which left
    WP-1B, WP-2, and WP-3 certifying runs whose commits nothing preserved --
    exactly how WP-0's and WP-2's own bindings were lost
    (``docs/evidence-binding-audit-2026-08-03.md``).

    Returns a human-readable outcome; never raises for an already-correct tag,
    because finalizing twice must stay idempotent.
    """
    tag = evidence_tag_name(run_id)

    # `rev-parse --verify refs/tags/<tag>` rather than resolving the bare name:
    # a bare name would also match a branch called evidence/<run-id>, and the
    # answer to "does this evidence tag exist" must not depend on some other
    # ref happening to share its name.
    existing = subprocess.run(
        [
            "git", "-C", str(repo_root), "rev-parse",
            "--verify", "--quiet", f"refs/tags/{tag}^{{commit}}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if existing.returncode == 0:
        found = existing.stdout.strip()
        if found == commit:
            return f"tag {tag} already points at {commit[:12]}"
        raise OracleEvidenceError(
            f"tag {tag} already exists at {found[:12]} but this run binds to "
            f"{commit[:12]}; refusing to move an evidence tag"
        )

    created = subprocess.run(
        [
            "git", "-C", str(repo_root), "tag", "-a", tag, commit,
            "-m", f"Source tree for certified evidence run {run_id} (issue #22).",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if created.returncode != 0:
        raise OracleEvidenceError(
            f"could not create evidence tag {tag}: {created.stderr.strip()}"
        )
    return f"created {tag} at {commit[:12]} — push it: git push origin {tag}"


def _build_family(value: str) -> str | None:
    """Return the trailing build number of an OS build string, if it has one.

    ``"Microsoft Windows Server 2025 Standard 26100"`` -> ``"26100"``
    ``"Microsoft Windows Server 2025 Standard 26100.4652"`` -> ``"26100"``

    Returns ``None`` when no build number is readable, which callers report as a
    violation: unreadable fails closed rather than passing unqualified.
    """
    match = _BUILD_FAMILY.search(value.strip())
    return match.group(1) if match else None


def frozen_environment_violations(
    environment: WindowsEnvironment,
    frozen: FrozenEnvironment = FROZEN_ENVIRONMENT,
) -> tuple[str, ...]:
    """Return human-readable deviations from the frozen qualification profile.

    An empty tuple means the environment is on-target. Build numbers are
    qualified by family, so a servicing revision within the frozen family is on
    target and its exact value is still recorded in the manifest.

    ``client_build`` may be the ``CLIENT_NOT_TESTED`` sentinel: a lane that
    never touches a client is on-target without endpoint evidence. A lane that
    does touch one is responsible for asserting a real client build in its own
    finalizer; this parser cannot tell the two apart.
    """
    violations: list[str] = []

    server_family = _build_family(environment.server_build)
    if server_family != frozen.server_build_family:
        violations.append(
            f"environment.server_build is {environment.server_build!r} "
            f"(build family {server_family or 'unreadable'}), expected family "
            f"{frozen.server_build_family!r}"
        )

    if environment.client_build != CLIENT_NOT_TESTED:
        client_family = _build_family(environment.client_build)
        if client_family != frozen.client_build_family:
            violations.append(
                f"environment.client_build is {environment.client_build!r} "
                f"(build family {client_family or 'unreadable'}), expected family "
                f"{frozen.client_build_family!r} or {CLIENT_NOT_TESTED!r}"
            )

    ps_version = environment.powershell_version
    ps_family = frozen.powershell_version_family
    if ps_version != ps_family and not ps_version.startswith(f"{ps_family}."):
        violations.append(
            f"environment.powershell_version is {ps_version!r}, expected "
            f"version family {ps_family!r}"
        )

    checks: tuple[tuple[str, str, str], ...] = (
        ("powershell_edition", environment.powershell_edition, frozen.powershell_edition),
        (
            "group_policy_module_version",
            environment.group_policy_module_version,
            frozen.group_policy_module_version,
        ),
        ("gpmc_version", environment.gpmc_version, frozen.gpmc_version),
        ("locale", environment.locale, frozen.locale),
    )
    for field_name, observed, expected in checks:
        if observed != expected:
            violations.append(
                f"environment.{field_name} is {observed!r}, expected {expected!r}"
            )
    return tuple(violations)


#: Fields a lane harness must record for its environment to be checkable at all.
#: ``lgpo_sha256`` is deliberately absent: it is recorded, not qualified, and no
#: lane executes the binary (see environment-spec.md).
_LANE_ENVIRONMENT_FIELDS: tuple[str, ...] = (
    "server_build",
    "powershell_edition",
    "powershell_version",
    "group_policy_module_version",
    "gpmc_version",
    "locale",
)


def lane_environment_violations(
    recorded: Mapping[str, object],
    frozen: FrozenEnvironment = FROZEN_ENVIRONMENT,
) -> tuple[str, ...]:
    """Check a lane result's ``environment`` object against the frozen profile.

    The WP-1B/WP-2/WP-3/endpoint lanes write a bespoke ``result.json`` rather
    than a full manifest, so :func:`parse_oracle_manifest` never sees them and
    its ``pass`` gate never runs.  Environment-spec rule 7 names
    :data:`FROZEN_ENVIRONMENT` as the single source of truth for the profile;
    this is how a lane finalizer honours that rule without keeping its own copy.

    A private copy is not a harmless duplication.  WP-3's finalizer carried one
    that pinned an exact PowerShell servicing revision and gated a pass on
    ``lgpo_sha256`` -- both of which the 2026-07-29 build-family re-freeze had
    already removed, and neither of which any lane's evidence depends on.  It
    would have refused a correct run on a correctly qualified host.

    Extra recorded keys (``server_caption``, ``lgpo_sha256``) are ignored:
    provenance a lane chooses to record is not something to gate on.  Lanes that
    never touch a client are on-target with no client evidence; a lane that does
    touch one asserts a real ``client_build`` in its own finalizer, per rule 6.
    """
    missing = [field for field in _LANE_ENVIRONMENT_FIELDS if field not in recorded]
    if missing:
        return tuple(
            f"environment.{field} was not recorded by the harness" for field in missing
        )
    environment = WindowsEnvironment(
        server_build=str(recorded["server_build"]),
        client_build=str(recorded.get("client_build") or CLIENT_NOT_TESTED),
        powershell_edition=str(recorded["powershell_edition"]),
        powershell_version=str(recorded["powershell_version"]),
        group_policy_module_version=str(recorded["group_policy_module_version"]),
        gpmc_version=str(recorded["gpmc_version"]),
        locale=str(recorded["locale"]),
        # Recorded, not qualified: a placeholder keeps the dataclass honest
        # without letting a missing binary influence a verdict.
        lgpo_sha256=str(recorded.get("lgpo_sha256") or "missing"),
    )
    return frozen_environment_violations(environment, frozen)


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
    artifact_map = {item.artifact_id: item for item in artifacts}
    for comparison in comparisons:
        if comparison.expected_artifact_id not in artifact_map:
            raise OracleEvidenceError(
                f"comparison {comparison.assertion_id!r} expected_artifact_id "
                f"{comparison.expected_artifact_id!r} does not reference a known artifact"
            )
        if comparison.observed_artifact_id not in artifact_map:
            raise OracleEvidenceError(
                f"comparison {comparison.assertion_id!r} observed_artifact_id "
                f"{comparison.observed_artifact_id!r} does not reference a known artifact"
            )
        expected_artifact = artifact_map[comparison.expected_artifact_id]
        if comparison.expected_sha256 != expected_artifact.sha256:
            raise OracleEvidenceError(
                f"comparison {comparison.assertion_id!r} expected_sha256 does not match "
                f"artifact {comparison.expected_artifact_id!r}"
            )
        observed_artifact = artifact_map[comparison.observed_artifact_id]
        if comparison.observed_sha256 != observed_artifact.sha256:
            raise OracleEvidenceError(
                f"comparison {comparison.assertion_id!r} observed_sha256 does not match "
                f"artifact {comparison.observed_artifact_id!r}"
            )
    started_at = _timestamp(data, "started_at")
    completed_at = _timestamp(data, "completed_at")
    if completed_at < started_at:
        raise OracleEvidenceError("manifest.completed_at cannot precede started_at")
    cleanup = _cleanup(data.get("cleanup"))
    capability = _capability(data.get("capability"))
    source = _source(data.get("source"))
    environment = _environment(data.get("environment"))
    if capability.evidence_state == "pass":
        if any(command.exit_code != 0 for command in commands):
            raise OracleEvidenceError(
                "passing capability cannot contain a failed command"
            )
        if any(not comparison.equal for comparison in comparisons):
            raise OracleEvidenceError(
                "passing capability cannot contain a failed comparison"
            )
        if not cleanup.succeeded or not cleanup.state_restored:
            raise OracleEvidenceError(
                "passing capability requires successful cleanup and state restore"
            )
        if not _COMMIT_SHA_RE.fullmatch(source.commit):
            raise OracleEvidenceError(
                "passing capability requires a valid hexadecimal commit SHA"
            )
        if source.dirty:
            raise OracleEvidenceError(
                "passing capability requires a clean (non-dirty) source tree"
            )
        for comparison in comparisons:
            if comparison.normalizer_version not in SUPPORTED_NORMALIZER_VERSIONS:
                raise OracleEvidenceError(
                    f"comparison {comparison.assertion_id!r} has unsupported "
                    f"normalizer_version {comparison.normalizer_version!r}"
                )
        for command in commands:
            if command.stdout_sha256 is None and command.stderr_sha256 is None:
                raise OracleEvidenceError(
                    f"command {command.command_id!r} must have at least one of "
                    "stdout_sha256 or stderr_sha256"
                )
        if environment.locale not in FROZEN_LOCALES:
            raise OracleEvidenceError(
                f"passing capability requires locale in {sorted(FROZEN_LOCALES)}, "
                f"got {environment.locale!r}"
            )
        env_violations = frozen_environment_violations(environment)
        if env_violations:
            raise OracleEvidenceError(
                "passing capability requires the frozen qualification "
                f"environment; deviations: {'; '.join(env_violations)}"
            )
        for comparison in comparisons:
            if "synthetic" in comparison.oracle.casefold():
                raise OracleEvidenceError(
                    f"comparison {comparison.assertion_id!r} oracle must not "
                    "contain 'synthetic'"
                )
        if capability.matrix_row.startswith("dry-run."):
            raise OracleEvidenceError(
                "passing capability matrix_row must not start with 'dry-run.'"
            )
    return OracleEvidenceManifest(
        schema_version=schema_version,
        run_id=_string(data, "run_id", "manifest"),
        started_at=_string(data, "started_at", "manifest"),
        completed_at=_string(data, "completed_at", "manifest"),
        source=source,
        fixture=_fixture(data.get("fixture")),
        environment=environment,
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
# GPO report (Get-GPOReport / Backup-GPO gpreport.xml) metadata that records when
# the report or GPO was created/modified/read.  These are generated timestamps
# with no policy meaning, so two reports of the same GPO always differ on
# ReadTime and must not be treated as a semantic difference.
_GPO_REPORT_TIMESTAMPS: frozenset[str] = frozenset(
    {"CreatedTime", "ModifiedTime", "ReadTime"}
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
    if (
        element_name in _GPO_REPORT_TIMESTAMPS
        and parent_name == "GPO"
        and text is not None
    ):
        text = _NORMALIZED_TIMESTAMP
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


_WP0_COMPARISON_ASSERTION_ID = "wp0-gpo-report-self-consistency"
_WP0_COMPARISON_ORACLE = "Backup-GPO native gpreport vs Get-GPOReport native output"


def git_source_state(repo_root: Path) -> SourceState:
    """Compute the authoritative source provenance from the git working tree.

    Provenance is computed here (where the repository lives) rather than on the
    Windows evidence host, which has no git.  A missing git binary or a
    non-repository path is reported as a dirty state with a sentinel commit so
    it can never be mistaken for a clean, passing source (and so the resulting
    manifest still parses).
    """
    unknown = SourceState(commit="unknown", dirty=True)
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return unknown
    if not commit:
        return unknown
    return SourceState(commit=commit, dirty=bool(status.strip()))


def _sha256_of_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _artifact_field(item: object, key: str) -> str:
    value = _mapping(item, "artifact").get(key)
    return value if isinstance(value, str) else ""


def _resolve_artifact_path(run_dir: Path, relative_path: str) -> Path:
    """Resolve a manifest relative_path (Windows-style) against the local run dir.

    Manifests are produced on Windows and store backslash-separated paths; the
    finalize step usually runs on a POSIX host, so the separators must be
    translated before the file is read.  Absolute, drive-relative, and
    parent-traversing paths are refused so a raw manifest can never reach
    outside ``run_dir``.
    """
    parsed = PureWindowsPath(relative_path)
    if parsed.is_absolute() or parsed.drive or ".." in parsed.parts:
        raise OracleEvidenceError(
            f"artifact relative_path must be a relative path: {relative_path!r}"
        )
    return run_dir.joinpath(*parsed.parts)


def _build_normalized_artifact(
    run_dir: Path,
    *,
    artifact_id: str,
    source_relative_path: str,
) -> ArtifactDigest:
    """Normalize a raw XML artifact, persist it, and describe it as an artifact.

    The persisted ``normalized/<id>.json`` file carries the normalized semantic
    hash, which comparisons bind to.  The raw artifact hash is preserved
    separately on the original artifact.
    """
    source_path = _resolve_artifact_path(run_dir, source_relative_path)
    normalized = normalize_xml_semantics(source_path.read_bytes())
    canonical = normalized.canonical_bytes()
    normalized_dir = run_dir / "normalized"
    normalized_dir.mkdir(parents=True, exist_ok=True)
    relative_path = f"normalized/{artifact_id}.json"
    (run_dir / relative_path).write_bytes(canonical)
    return ArtifactDigest(
        artifact_id=artifact_id,
        role="output",
        relative_path=relative_path,
        sha256=_sha256_of_bytes(canonical),
        size_bytes=len(canonical),
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_evidence_pack(
    run_dir: Path, manifest: Mapping[str, object]
) -> tuple[str, ...]:
    """Rehash every artifact file and command stream and compare to the manifest.

    Returns the discrepancies (empty tuple means the pack is intact).  This is
    the integrity check Sol requires: it runs before finalization and again
    during later review, so any artifact or command stream that has been altered
    or lost since the run is detected.

    Two layers are checked:

    * each artifact's file is rehashed against its recorded ``sha256``;
    * each command's ``commands/<id>.stdout.txt`` / ``.stderr.txt`` is rehashed
      against the command's recorded stream hash, and that recorded hash must
      itself match the corresponding artifact (binding the command evidence to
      the artifact set rather than trusting the command record alone).
    """
    problems: list[str] = []

    artifacts_raw = _object_tuple(manifest.get("artifacts"), "artifacts")
    artifact_hashes: dict[str, str] = {}
    for index, item in enumerate(artifacts_raw):
        data = _mapping(item, f"artifacts[{index}]")
        artifact_id = _string(data, "artifact_id", f"artifacts[{index}]")
        recorded_sha = _sha256(data, "sha256", f"artifacts[{index}]")
        relative_path = _string(data, "relative_path", f"artifacts[{index}]")
        artifact_hashes[artifact_id] = recorded_sha
        try:
            path = _resolve_artifact_path(run_dir, relative_path)
        except OracleEvidenceError as exc:
            problems.append(f"artifact {artifact_id!r}: {exc}")
            continue
        if not path.is_file():
            problems.append(
                f"artifact {artifact_id!r}: file missing at {relative_path!r}"
            )
            continue
        actual = _sha256_file(path)
        if actual != recorded_sha:
            problems.append(
                f"artifact {artifact_id!r}: recorded sha256 {recorded_sha} "
                f"!= actual {actual}"
            )

    commands_raw = _object_tuple(manifest.get("commands"), "commands")
    commands_dir = run_dir / "commands"
    for index, item in enumerate(commands_raw):
        label = f"commands[{index}]"
        data = _mapping(item, label)
        command_id = _string(data, "command_id", label)
        if not _COMMAND_ID_RE.fullmatch(command_id):
            problems.append(
                f"command {command_id!r}: command_id is not a safe filename "
                "component"
            )
            continue
        recorded_streams: set[str] = set()
        for stream in ("stdout", "stderr"):
            recorded = _optional_sha256(data, f"{stream}_sha256", label)
            if recorded is None:
                continue
            recorded_streams.add(stream)
            stream_path = commands_dir / f"{command_id}.{stream}.txt"
            if not stream_path.is_file():
                problems.append(
                    f"command {command_id!r}: {stream} stream missing at "
                    f"{stream_path.name!r}"
                )
                continue
            actual = _sha256_file(stream_path)
            if actual != recorded:
                problems.append(
                    f"command {command_id!r}: recorded {stream}_sha256 "
                    f"{recorded} != actual {actual}"
                )
            bound_artifact = f"{command_id}-{stream}"
            if bound_artifact in artifact_hashes:
                if artifact_hashes[bound_artifact] != recorded:
                    problems.append(
                        f"command {command_id!r}: {stream}_sha256 {recorded} does "
                        f"not match artifact {bound_artifact!r} "
                        f"{artifact_hashes[bound_artifact]}"
                    )
            else:
                problems.append(
                    f"command {command_id!r}: {stream}_sha256 {recorded} has no "
                    f"matching artifact {bound_artifact!r}"
                )
        # Reject stream files that exist on disk but are not recorded on the
        # command, so evidence cannot be smuggled in unverified.
        if commands_dir.is_dir():
            for stream in ("stdout", "stderr"):
                if stream in recorded_streams:
                    continue
                stray = commands_dir / f"{command_id}.{stream}.txt"
                if stray.is_file():
                    problems.append(
                        f"command {command_id!r}: unrecorded {stream} stream file "
                        f"{stray.name!r} present on disk"
                    )

    return tuple(problems)


def assert_evidence_pack(run_dir: Path, manifest: Mapping[str, object]) -> None:
    """Raise :class:`IntegrityViolation` if the evidence pack is not intact."""
    problems = verify_evidence_pack(run_dir, manifest)
    if problems:
        raise IntegrityViolation(
            "evidence pack integrity check failed:\n" + "\n".join(problems)
        )


# Deployed harness files recorded as hashed input artifacts so a run is bound to
# the exact scripts and recipe that produced it (and, via git, to the commit).
# Each entry is (artifact_id, deployed relative path, repository path).
_HARNESS_INPUT_FILES: tuple[tuple[str, str, str], ...] = (
    (
        "harness-run-evidence",
        "scripts/run-evidence.ps1",
        "scripts/windows-oracle/run-evidence.ps1",
    ),
    ("harness-common", "scripts/common.psm1", "scripts/windows-oracle/common.psm1"),
    (
        "harness-recipe",
        "scripts/recipe.json",
        "tests/fixtures/recipes/synthetic-registry-basic.json",
    ),
    # The privileged launcher the orchestrator deploys and executes on the host.
    (
        "harness-remote-run",
        "scripts/remote-run.ps1",
        "scripts/windows-oracle/remote-run.ps1",
    ),
    # The control-plane orchestrator that drives the run.
    (
        "harness-orchestrator",
        "orchestrator/run-windows-oracle.sh",
        "scripts/windows-oracle/run-windows-oracle.sh",
    ),
)
_HARNESS_INPUTS_MANIFEST = "harness-inputs.json"


def _git_show_file(repo_root: Path, commit: str, relative_path: str) -> bytes:
    result = subprocess.run(
        ["git", "show", f"{commit}:{relative_path}"],
        cwd=repo_root,
        capture_output=True,
        check=True,
    )
    return result.stdout


def build_harness_inputs(
    run_dir: Path,
    repo_root: Path,
    *,
    commit: str | None = None,
    check_git: bool = True,
) -> list[dict[str, object]]:
    """Verify the deployed harness inputs and describe them as input artifacts.

    Reads ``harness-inputs.json`` (written by the orchestrator at deploy time,
    before the credential boundary), confirms each deployed file's hash matches,
    and - when ``check_git`` is true - confirms the deployed bytes are identical
    to the file at the recorded commit.  The result is a set of ``input``
    artifacts that bind the run to the exact harness code that produced it.
    """
    inputs_path = run_dir / _HARNESS_INPUTS_MANIFEST
    if not inputs_path.is_file():
        raise IntegrityViolation(
            f"{_HARNESS_INPUTS_MANIFEST} is missing; the harness inputs cannot "
            "be verified"
        )
    try:
        inputs = json.loads(inputs_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise IntegrityViolation(f"{_HARNESS_INPUTS_MANIFEST} is not valid JSON: {exc}") from exc
    if not isinstance(inputs, dict):
        raise IntegrityViolation(f"{_HARNESS_INPUTS_MANIFEST} must be a JSON object")

    recorded_commit = inputs.get("commit")
    if not isinstance(recorded_commit, str) or not _COMMIT_SHA_RE.fullmatch(
        recorded_commit
    ):
        raise IntegrityViolation(
            f"{_HARNESS_INPUTS_MANIFEST}.commit must be a valid hexadecimal "
            "commit SHA"
        )
    if commit is None:
        commit = recorded_commit
    elif commit != recorded_commit:
        # The recorded deploy-time commit is authoritative.  Finalization must
        # not silently bind the deployed inputs to a different commit than the
        # one recorded at deploy time.
        raise IntegrityViolation(
            f"harness inputs were recorded at commit {recorded_commit!r} but "
            f"finalization supplied {commit!r}"
        )

    deployed = inputs.get("files")
    if not isinstance(deployed, dict):
        raise IntegrityViolation(f"{_HARNESS_INPUTS_MANIFEST}.files must be an object")

    artifacts: list[dict[str, object]] = []
    problems: list[str] = []
    seen: set[str] = set()
    for artifact_id, relative_path, repo_path in _HARNESS_INPUT_FILES:
        entry = deployed.get(relative_path)
        if not isinstance(entry, dict):
            problems.append(f"deployed harness input {relative_path!r} is not recorded")
            continue
        seen.add(relative_path)
        recorded_sha = entry.get("sha256")
        size = entry.get("size_bytes")
        if not isinstance(recorded_sha, str) or not _SHA256_RE.fullmatch(recorded_sha):
            problems.append(f"harness input {relative_path!r} has no valid sha256")
            continue
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            problems.append(f"harness input {relative_path!r} has no valid size_bytes")
            continue
        deployed_path = run_dir / relative_path
        if not deployed_path.is_file():
            problems.append(f"deployed harness input {relative_path!r} is missing")
            continue
        actual_sha = _sha256_file(deployed_path)
        if actual_sha != recorded_sha:
            problems.append(
                f"deployed harness input {relative_path!r}: recorded sha256 "
                f"{recorded_sha} != actual {actual_sha}"
            )
            continue
        if check_git:
            if not commit or not _COMMIT_SHA_RE.fullmatch(commit):
                problems.append(
                    "harness inputs cannot be bound to a commit: no valid "
                    "commit recorded"
                )
                continue
            try:
                committed_bytes = _git_show_file(repo_root, commit, repo_path)
            except (OSError, subprocess.CalledProcessError):
                problems.append(
                    f"harness input {relative_path!r} not found at commit {commit!r}"
                )
                continue
            if _sha256_of_bytes(committed_bytes) != recorded_sha:
                problems.append(
                    f"deployed harness input {relative_path!r} differs from the "
                    f"file at commit {commit!r}"
                )
                diff = "\n".join(
                    difflib.unified_diff(
                        committed_bytes.decode("utf-8", "replace").splitlines(),
                        deployed_path.read_bytes()
                        .decode("utf-8", "replace")
                        .splitlines(),
                        fromfile=f"{relative_path}@{commit}",
                        tofile=f"deployed/{relative_path}",
                        lineterm="",
                    )
                )
                if diff:
                    problems.append(diff)
                continue
        artifacts.append(
            {
                "artifact_id": artifact_id,
                "role": "input",
                "relative_path": relative_path,
                "sha256": recorded_sha,
                "size_bytes": size,
            }
        )

    extra = sorted(set(deployed) - seen)
    if extra:
        problems.append(f"unexpected deployed harness inputs: {extra}")

    if problems:
        raise IntegrityViolation(
            "harness input verification failed:\n" + "\n".join(problems)
        )
    return artifacts


def finalize_oracle_run(
    run_dir: Path,
    repo_root: Path,
) -> OracleEvidenceManifest:
    """Finalize a raw Windows evidence run into a validated manifest.

    The Windows harness writes ``manifest.raw.json`` plus the captured artifacts
    into ``run_dir``.  This step is the single authority for:

    * source provenance (computed from the git repository, never the host);
    * semantic comparisons (run through the versioned XML normalizer, bound to
      persisted normalized artifacts);
    * the final ``evidence_state``.

    A run that failed during setup or collection still yields a parser-valid
    ``fail`` manifest: the failed command and its logs are preserved, a sentinel
    output artifact stands in for the missing report, and an explanatory
    comparison records why no semantic comparison was possible.
    """
    raw_path = run_dir / "manifest.raw.json"
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise OracleEvidenceError("manifest.raw.json must be a JSON object")

    source = git_source_state(repo_root)

    commands_raw = raw.get("commands")
    commands = tuple(
        _command(item, index)
        for index, item in enumerate(_object_tuple(commands_raw, "commands"))
    )
    cleanup = _cleanup(raw.get("cleanup"))
    artifacts_raw = list(_object_tuple(raw.get("artifacts"), "artifacts"))
    capability = _mapping(raw.get("capability"), "capability")
    matrix_row = capability.get("matrix_row")

    # Bind every command stream to an artifact so the command evidence is part
    # of the artifact set (not just an unverified hash on the command record).
    # The harness may have registered some explicitly (e.g. the cleanup
    # re-query); register the rest here.  verify_evidence_pack then rehashes the
    # underlying file to confirm the recorded hash is genuine.
    existing_artifact_ids = {
        _artifact_field(item, "artifact_id") for item in artifacts_raw
    }
    for item in _object_tuple(commands_raw, "commands"):
        command_data = _mapping(item, "command")
        command_id = _string(command_data, "command_id", "command")
        for stream in ("stdout", "stderr"):
            stream_sha = _optional_sha256(command_data, f"{stream}_sha256", "command")
            if stream_sha is None:
                continue
            stream_artifact_id = f"{command_id}-{stream}"
            if stream_artifact_id in existing_artifact_ids:
                continue
            relative_path = f"commands/{command_id}.{stream}.txt"
            stream_file = run_dir / relative_path
            size_bytes = stream_file.stat().st_size if stream_file.is_file() else 0
            artifacts_raw.append(
                {
                    "artifact_id": stream_artifact_id,
                    "role": "raw-log",
                    "relative_path": relative_path,
                    "sha256": stream_sha,
                    "size_bytes": size_bytes,
                }
            )
            existing_artifact_ids.add(stream_artifact_id)

    # Verify and bind the deployed harness scripts and recipe as input artifacts
    # (tied to the recorded commit), then rehash the entire raw pack - every
    # artifact file and every command stream - before building any comparisons.
    # Both checks fail closed so altered or lost evidence is never finalized.
    harness_inputs = build_harness_inputs(run_dir, repo_root, commit=source.commit)
    artifacts_raw.extend(harness_inputs)
    assert_evidence_pack(run_dir, {"artifacts": artifacts_raw, "commands": commands_raw})

    by_role: dict[str, Mapping[str, object]] = {}
    for item in artifacts_raw:
        mapping = _mapping(item, "artifact")
        role = mapping.get("role")
        if isinstance(role, str):
            by_role.setdefault(role, mapping)

    input_artifact = by_role.get("input")
    if input_artifact is None:
        raise OracleEvidenceError("raw manifest is missing an input artifact")

    comparisons: list[dict[str, object]] = []
    failed_command = next((c for c in commands if c.exit_code != 0), None)

    if failed_command is not None or not cleanup.succeeded:
        output_artifact = by_role.get("output")
        if output_artifact is None:
            failure_note = (
                "run failed before producing an output artifact: "
                f"command {failed_command.command_id!r} exited "
                f"{failed_command.exit_code}"
                if failed_command is not None
                else "run failed during cleanup; no output artifact produced"
            )
            failure_bytes = failure_note.encode("utf-8")
            output_dir = run_dir / "artifacts"
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "failure.txt").write_bytes(failure_bytes)
            output_artifact = {
                "artifact_id": "failure-output",
                "role": "output",
                "relative_path": "artifacts/failure.txt",
                "sha256": _sha256_of_bytes(failure_bytes),
                "size_bytes": len(failure_bytes),
            }
            artifacts_raw.append(output_artifact)
        reason = (
            f"command {failed_command.command_id!r} exited "
            f"{failed_command.exit_code}"
            if failed_command is not None
            else "cleanup did not succeed; state was not restored"
        )
        output_id = _string(output_artifact, "artifact_id", "output artifact")
        output_sha = _sha256(output_artifact, "sha256", "output artifact")
        comparisons.append(
            {
                "assertion_id": _WP0_COMPARISON_ASSERTION_ID,
                "oracle": _WP0_COMPARISON_ORACLE,
                "boundary_owner": "gpo-backup-content",
                "normalizer_version": NORMALIZER_VERSION,
                "expected_artifact_id": output_id,
                "observed_artifact_id": output_id,
                "expected_sha256": output_sha,
                "observed_sha256": output_sha,
                "equal": False,
                "differences": [f"comparison not performed: {reason}"],
            }
        )
        evidence_state = "fail"
    else:
        backup_report = next(
            (
                item
                for item in artifacts_raw
                if _artifact_field(item, "relative_path")
                .casefold()
                .endswith("gpreport.xml")
                and "backup" in _artifact_field(item, "relative_path").casefold()
            ),
            None,
        )
        standalone_report = next(
            (
                item
                for item in artifacts_raw
                if _artifact_field(item, "artifact_id") == "gpreport"
            ),
            None,
        )
        if backup_report is None or standalone_report is None:
            raise OracleEvidenceError(
                "successful run must capture both a backup gpreport.xml and a "
                "standalone gpreport.xml"
            )
        backup_rel = _string(
            _mapping(backup_report, "backup report"), "relative_path", "backup report"
        )
        standalone_rel = _string(
            _mapping(standalone_report, "standalone report"),
            "relative_path",
            "standalone report",
        )
        expected_artifact = _build_normalized_artifact(
            run_dir,
            artifact_id="normalized-backup-gpreport",
            source_relative_path=backup_rel,
        )
        observed_artifact = _build_normalized_artifact(
            run_dir,
            artifact_id="normalized-standalone-gpreport",
            source_relative_path=standalone_rel,
        )
        artifacts_raw.append(dataclasses.asdict(expected_artifact))
        artifacts_raw.append(dataclasses.asdict(observed_artifact))
        semantic = compare_xml_semantics(
            _resolve_artifact_path(run_dir, backup_rel).read_bytes(),
            _resolve_artifact_path(run_dir, standalone_rel).read_bytes(),
        )
        comparisons.append(
            {
                "assertion_id": _WP0_COMPARISON_ASSERTION_ID,
                "oracle": _WP0_COMPARISON_ORACLE,
                "boundary_owner": "gpo-backup-content",
                "normalizer_version": NORMALIZER_VERSION,
                "expected_artifact_id": expected_artifact.artifact_id,
                "observed_artifact_id": observed_artifact.artifact_id,
                "expected_sha256": expected_artifact.sha256,
                "observed_sha256": observed_artifact.sha256,
                "equal": semantic.equal,
                "differences": list(semantic.differences),
            }
        )
        environment_obj = _environment(raw.get("environment"))
        # Mirror the parser's full `pass` gate so finalize never labels a run
        # `pass` that parse_oracle_manifest would then reject.
        pass_eligible = (
            semantic.equal
            and not source.dirty
            and _COMMIT_SHA_RE.fullmatch(source.commit) is not None
            and cleanup.state_restored
            and not frozen_environment_violations(environment_obj)
            and not (
                isinstance(matrix_row, str) and matrix_row.startswith("dry-run.")
            )
        )
        evidence_state = "pass" if pass_eligible else "inconclusive"

    finalized: dict[str, object] = dict(raw)
    finalized["source"] = {"commit": source.commit, "dirty": source.dirty}
    finalized["artifacts"] = artifacts_raw
    finalized["comparisons"] = comparisons
    finalized["capability"] = {
        "matrix_row": matrix_row,
        "evidence_state": evidence_state,
    }

    manifest = parse_oracle_manifest(finalized)
    out_path = run_dir / "manifest.json"
    out_path.write_text(
        json.dumps(finalized, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest
