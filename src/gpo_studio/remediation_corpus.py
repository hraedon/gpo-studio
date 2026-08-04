"""Plan 033 remediation scenario corpus: loader and validator.

Plans 025-032 landed domain layers that diverged from Windows reality. The
remediation program proves each repaired behavior against native Windows
tooling, and the corpus under ``tests/fixtures/scenarios/`` is the durable
record those proofs run against: one JSON scenario per expected behavior,
annotated with provenance (how the expectation is known), the test platform
that proves it, and the boundary each assertion belongs to.

This module is the executable contract for that data. The JSON schemas
(``docs/plan-033/remediation-scenario-v1.schema.json`` and
``test-platform-registry-v1.schema.json``) are the cheap structural gate;
validation here is the real one. ``load_scenario`` and ``load_corpus``
enforce referential integrity between scenarios and the platform registry,
readiness honesty (a scenario may not claim ``ready`` when its lane needs an
unqualified platform), and per-family payload shape.

Anchor integrity is deliberately **not** part of loading. It lives in
``anchor_violations()``, which needs a repo root and reads the working tree;
keeping it separate leaves loading cheap and IO-free so a caller can validate
the corpus without touching the filesystem beyond the scenario files. A
caller that wants the guarantee must run that pass — the test suite does, in
``test_every_anchor_hash_verifies``.

The module is stdlib-only, matching the project convention that corpus and
conformance code (``conformance.py``, ``oracle_evidence.py``) stays
independent of the delivery layer.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, assert_never, cast

SCENARIO_SCHEMA_VERSION = "1"
PLATFORM_SCHEMA_VERSION = "1"

Family = Literal["gpp-services", "security-template", "rsop-topology", "ilt-os"]
FAMILIES: tuple[Family, ...] = (
    "gpp-services",
    "security-template",
    "rsop-topology",
    "ilt-os",
)

ProvenanceTier = Literal["native-observation", "spec-informed", "hypothesis"]
PROVENANCE_TIERS: tuple[ProvenanceTier, ...] = (
    "native-observation",
    "spec-informed",
    "hypothesis",
)

Readiness = Literal["ready", "blocked"]

#: The five owning boundaries of docs/plan-033/boundary-matrix.md. Every
#: scenario assertion names a subset of its lane's boundaries; the matrix is
#: the authority and this set mirrors it exactly.
BOUNDARIES: frozenset[str] = frozenset(
    {
        "gpo-backup-content",
        "gpo-ad-object-security",
        "wmi-filter-object-association",
        "som-link-block-inheritance",
        "endpoint-resultant-state",
    }
)

PlatformStatus = Literal["frozen", "pending-qualification"]

_ANCHOR_KINDS: frozenset[str] = frozenset({"native-capture", "spec-doc", "lab-runbook"})


class RemediationCorpusError(ValueError):
    """A scenario file or the platform registry failed validation."""


@dataclass(frozen=True, slots=True)
class Host:
    host_id: str
    role: str
    os: str
    build: str
    status: PlatformStatus
    qualifying_run: str | None
    snapshot_required: bool
    notes: str


@dataclass(frozen=True, slots=True)
class Tool:
    tool_id: str
    kind: str
    frozen_version: str | None
    status: PlatformStatus
    notes: str


@dataclass(frozen=True, slots=True)
class Lane:
    lane_id: str
    plan_wp: str
    boundaries: frozenset[str]
    required_hosts: tuple[str, ...]
    required_tools: tuple[str, ...]
    oracle: str
    status: str
    notes: str


@dataclass(frozen=True, slots=True)
class PlatformRegistry:
    hosts: tuple[Host, ...]
    tools: tuple[Tool, ...]
    lanes: tuple[Lane, ...]

    def lane(self, lane_id: str) -> Lane | None:
        for lane in self.lanes:
            if lane.lane_id == lane_id:
                return lane
        return None

    def pending_platforms(self, lane: Lane) -> tuple[str, ...]:
        """Ids of the lane's required hosts/tools that are not yet frozen."""
        host_status = {host.host_id: host.status for host in self.hosts}
        tool_status = {tool.tool_id: tool.status for tool in self.tools}
        pending: list[str] = []
        for host_id in lane.required_hosts:
            if host_status.get(host_id) == "pending-qualification":
                pending.append(host_id)
        for tool_id in lane.required_tools:
            if tool_status.get(tool_id) == "pending-qualification":
                pending.append(tool_id)
        return tuple(pending)


@dataclass(frozen=True, slots=True)
class Anchor:
    kind: str
    path: str
    sha256: str | None
    note: str


@dataclass(frozen=True, slots=True)
class Provenance:
    tier: ProvenanceTier
    note: str
    anchors: tuple[Anchor, ...]


@dataclass(frozen=True, slots=True)
class ScenarioPlatform:
    lane: str
    boundaries: frozenset[str]


@dataclass(frozen=True, slots=True)
class Scenario:
    scenario_id: str
    family: Family
    title: str
    readiness: Readiness
    blocked_reason: str | None
    work_items: tuple[str, ...]
    provenance: Provenance
    platform: ScenarioPlatform
    authored_intent: dict[str, object]
    expected_native: dict[str, object]
    open_questions: tuple[str, ...]
    path: Path


def _err(context: str, detail: str) -> RemediationCorpusError:
    return RemediationCorpusError(f"{context}: {detail}")


def _require_str(data: dict[str, object], key: str, context: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise _err(context, f"{key!r} must be a non-empty string")
    return value


def _require_str_list(data: dict[str, object], key: str, context: str) -> tuple[str, ...]:
    value = data.get(key)
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(item, str) and item for item in value)
    ):
        raise _err(context, f"{key!r} must be a non-empty list of non-empty strings")
    return tuple(value)


def _load_json(path: Path) -> dict[str, object]:
    try:
        raw: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise _err(str(path), f"unreadable or invalid JSON: {error}") from error
    if not isinstance(raw, dict):
        raise _err(str(path), "top level must be a JSON object")
    return raw


def load_platform_registry(path: Path) -> PlatformRegistry:
    """Load and validate the test platform registry (``platforms.json``)."""
    raw = _load_json(path)
    context = str(path)
    if raw.get("schema_version") != PLATFORM_SCHEMA_VERSION:
        raise _err(context, f"schema_version must be {PLATFORM_SCHEMA_VERSION!r}")

    hosts_raw = raw.get("hosts")
    tools_raw = raw.get("tools")
    lanes_raw = raw.get("lanes")
    if not isinstance(hosts_raw, list) or not hosts_raw:
        raise _err(context, "hosts must be a non-empty list")
    if not isinstance(tools_raw, list) or not tools_raw:
        raise _err(context, "tools must be a non-empty list")
    if not isinstance(lanes_raw, list) or not lanes_raw:
        raise _err(context, "lanes must be a non-empty list")

    hosts: list[Host] = []
    for entry in hosts_raw:
        if not isinstance(entry, dict):
            raise _err(context, "hosts entries must be objects")
        host_id = _require_str(entry, "host_id", context)
        status = _status(entry, context)
        qualifying_run_raw = entry.get("qualifying_run")
        if qualifying_run_raw is not None and not isinstance(qualifying_run_raw, str):
            raise _err(context, f"{host_id}: qualifying_run must be a string")
        # A frozen host must name the run that earned it, and a pending host
        # must not name one. Without this the registry can claim a
        # qualification no evidence supports, or lag one that exists -- the
        # latter is what actually happened, four times.
        if status == "frozen" and not qualifying_run_raw:
            raise _err(
                context,
                f"{host_id}: a frozen host must cite the qualifying_run that "
                "qualified it, as recorded in environment-spec.md",
            )
        if status == "pending-qualification" and qualifying_run_raw:
            raise _err(
                context,
                f"{host_id}: a pending-qualification host must not cite a "
                f"qualifying_run (got {qualifying_run_raw!r}); if the run is real, "
                "the status is stale",
            )
        hosts.append(
            Host(
                host_id=host_id,
                role=_require_str(entry, "role", context),
                os=_require_str(entry, "os", context),
                build=_require_str(entry, "build", context),
                status=status,
                qualifying_run=qualifying_run_raw,
                snapshot_required=bool(entry.get("snapshot_required", False)),
                notes=str(entry.get("notes", "")),
            )
        )

    tools: list[Tool] = []
    for entry in tools_raw:
        if not isinstance(entry, dict):
            raise _err(context, "tools entries must be objects")
        frozen_version = entry.get("frozen_version")
        tools.append(
            Tool(
                tool_id=_require_str(entry, "tool_id", context),
                kind=_require_str(entry, "kind", context),
                frozen_version=frozen_version if isinstance(frozen_version, str) else None,
                status=_status(entry, context),
                notes=str(entry.get("notes", "")),
            )
        )

    lanes: list[Lane] = []
    for entry in lanes_raw:
        if not isinstance(entry, dict):
            raise _err(context, "lanes entries must be objects")
        boundaries = frozenset(_require_str_list(entry, "boundaries", context))
        unknown = boundaries - BOUNDARIES
        if unknown:
            raise _err(
                context,
                f"lane {entry.get('lane_id')!r} names unknown boundaries {sorted(unknown)}",
            )
        lanes.append(
            Lane(
                lane_id=_require_str(entry, "lane_id", context),
                plan_wp=_require_str(entry, "plan_wp", context),
                boundaries=boundaries,
                required_hosts=_require_str_list(entry, "required_hosts", context),
                required_tools=_require_str_list(entry, "required_tools", context),
                oracle=_require_str(entry, "oracle", context),
                status=_require_str(entry, "status", context),
                notes=str(entry.get("notes", "")),
            )
        )

    if not hosts or not tools or not lanes:
        raise _err(context, "hosts, tools, and lanes must all be non-empty")

    for label, ids in (
        ("host_id", [host.host_id for host in hosts]),
        ("tool_id", [tool.tool_id for tool in tools]),
        ("lane_id", [lane.lane_id for lane in lanes]),
    ):
        duplicates = sorted(item for item, n in Counter(ids).items() if n > 1)
        if duplicates:
            raise _err(context, f"duplicate {label} values: {duplicates}")

    host_ids = {host.host_id for host in hosts}
    tool_ids = {tool.tool_id for tool in tools}
    for lane in lanes:
        for host_id in lane.required_hosts:
            if host_id not in host_ids:
                raise _err(context, f"lane {lane.lane_id!r} requires unknown host {host_id!r}")
        for tool_id in lane.required_tools:
            if tool_id not in tool_ids:
                raise _err(context, f"lane {lane.lane_id!r} requires unknown tool {tool_id!r}")

    return PlatformRegistry(hosts=tuple(hosts), tools=tuple(tools), lanes=tuple(lanes))


def _status(entry: dict[str, object], context: str) -> PlatformStatus:
    status = entry.get("status")
    if status == "frozen":
        return "frozen"
    if status == "pending-qualification":
        return "pending-qualification"
    raise _err(context, f"status must be 'frozen' or 'pending-qualification', got {status!r}")


def _validate_family_payload(
    family: Family,
    authored_intent: dict[str, object],
    expected_native: dict[str, object],
    context: str,
) -> None:
    """Enforce the per-family payload contract.

    The JSON schema deliberately leaves the two payloads opaque; the real
    per-family shape is enforced here, where it is type-checked. The contract
    is documented in docs/plan-033/remediation-corpus.md; adding a family
    means extending this dispatch, and the assert_never makes an unhandled
    family a type error, not a silent pass.
    """
    match family:
        case "gpp-services":
            _require_key(authored_intent, "items", list, context)
            _require_key(expected_native, "items", list, context)
        case "security-template":
            _require_key(authored_intent, "sections", list, context)
            _require_key(expected_native, "entries", list, context)
            _require_key(expected_native, "round_trip", str, context)
        case "rsop-topology":
            _require_key(authored_intent, "topology", dict, context)
            # Either shape is valid, but whichever is present must be
            # non-empty, matching the other families. A conflict-resolution
            # scenario that names no winner asserts nothing.
            winners = expected_native.get("winners")
            per_mode = expected_native.get("per_mode")
            has_winners = isinstance(winners, list) and bool(winners)
            has_per_mode = isinstance(per_mode, list) and bool(per_mode)
            if not (has_winners or has_per_mode):
                raise _err(
                    context,
                    "expected_native must carry a non-empty 'winners' or 'per_mode' "
                    "for rsop-topology",
                )
        case "ilt-os":
            _require_key(authored_intent, "predicate", dict, context)
            _require_key(expected_native, "match_semantics", dict, context)
        case _:
            assert_never(family)


def _require_key(
    data: dict[str, object], key: str, kind: type, context: str
) -> None:
    value = data.get(key)
    if not isinstance(value, kind) or (isinstance(value, (list, dict)) and not value):
        raise _err(context, f"payload key {key!r} must be a non-empty {kind.__name__}")


def load_scenario(path: Path, registry: PlatformRegistry) -> Scenario:
    """Load one scenario file and validate it against the registry."""
    raw = _load_json(path)
    if raw.get("schema_version") != SCENARIO_SCHEMA_VERSION:
        raise _err(str(path), f"schema_version must be {SCENARIO_SCHEMA_VERSION!r}")
    readiness_raw = _require_str(raw, "readiness", str(path))
    if readiness_raw not in ("ready", "blocked"):
        raise _err(str(path), f"readiness must be 'ready' or 'blocked', got {readiness_raw!r}")
    readiness = cast(Readiness, readiness_raw)
    blocked_reason_raw = raw.get("blocked_reason")
    blocked_reason = blocked_reason_raw if isinstance(blocked_reason_raw, str) else None
    if readiness == "blocked" and not blocked_reason:
        raise _err(str(path), "blocked scenarios must carry a blocked_reason naming the gap")
    if readiness == "ready" and blocked_reason_raw is not None:
        raise _err(str(path), "a ready scenario must not carry blocked_reason")

    family_raw = _require_str(raw, "family", str(path))
    if family_raw not in FAMILIES:
        raise _err(str(path), f"unknown family {family_raw!r}; known: {sorted(FAMILIES)}")
    family: Family = family_raw

    platform_raw = raw.get("platform")
    if not isinstance(platform_raw, dict):
        raise _err(str(path), "'platform' must be an object")
    lane_id = _require_str(platform_raw, "lane", str(path))
    lane = registry.lane(lane_id)
    if lane is None:
        raise _err(str(path), f"unknown lane {lane_id!r}")
    boundaries = frozenset(_require_str_list(platform_raw, "boundaries", str(path)))
    unknown_boundaries = boundaries - BOUNDARIES
    if unknown_boundaries:
        raise _err(str(path), f"unknown boundaries {sorted(unknown_boundaries)}")
    if not boundaries <= lane.boundaries:
        raise _err(
            str(path),
            f"boundaries {sorted(boundaries)} are not a subset of lane {lane_id!r} "
            f"boundaries {sorted(lane.boundaries)}",
        )

    pending = registry.pending_platforms(lane)
    if readiness == "ready" and pending:
        raise _err(
            str(path),
            f"scenario claims ready but lane {lane_id!r} requires unqualified "
            f"platforms {sorted(pending)}; mark it blocked with the platform gap "
            "as blocked_reason",
        )

    authored_intent = raw.get("authored_intent")
    expected_native = raw.get("expected_native")
    if not isinstance(authored_intent, dict) or not isinstance(expected_native, dict):
        raise _err(str(path), "'authored_intent' and 'expected_native' must be objects")
    _validate_family_payload(family, authored_intent, expected_native, str(path))

    tier_raw = raw.get("provenance", {})
    if not isinstance(tier_raw, dict):
        raise _err(str(path), "'provenance' must be an object")
    tier = tier_raw.get("tier")
    if tier not in PROVENANCE_TIERS:
        raise _err(str(path), f"provenance.tier must be one of {list(PROVENANCE_TIERS)}")
    anchors_raw = tier_raw.get("anchors", [])
    if not isinstance(anchors_raw, list):
        raise _err(str(path), "provenance.anchors must be a list")
    anchors: list[Anchor] = []
    for anchor_raw in anchors_raw:
        if not isinstance(anchor_raw, dict):
            raise _err(str(path), "provenance.anchors entries must be objects")
        kind = anchor_raw.get("kind")
        if kind not in _ANCHOR_KINDS:
            raise _err(str(path), f"anchor kind must be one of {sorted(_ANCHOR_KINDS)}")
        sha256 = anchor_raw.get("sha256")
        if kind == "native-capture" and not isinstance(sha256, str):
            raise _err(
                str(path),
                "native-capture anchors must carry a sha256 so a changed capture "
                "breaks the corpus loudly",
            )
        anchors.append(
            Anchor(
                kind=str(kind),
                path=_require_str(anchor_raw, "path", str(path)),
                sha256=sha256 if isinstance(sha256, str) else None,
                note=str(anchor_raw.get("note", "")),
            )
        )

    open_questions_raw = raw.get("open_questions", [])
    if not isinstance(open_questions_raw, list) or not all(
        isinstance(question, str) for question in open_questions_raw
    ):
        raise _err(str(path), "open_questions must be a list of strings")

    work_items_raw = raw.get("work_items", [])
    if not isinstance(work_items_raw, list) or not all(
        isinstance(item, str) for item in work_items_raw
    ):
        raise _err(str(path), "work_items must be a list of strings")

    scenario_id = _require_str(raw, "scenario_id", str(path))
    if path.stem != scenario_id:
        raise _err(str(path), f"file stem {path.stem!r} must equal scenario_id {scenario_id!r}")
    if path.parent.name != family:
        raise _err(
            str(path),
            f"scenario must live in the {family!r} directory, not {path.parent.name!r}",
        )

    return Scenario(
        scenario_id=scenario_id,
        family=family,
        title=_require_str(raw, "title", str(path)),
        readiness=readiness,
        blocked_reason=blocked_reason,
        work_items=tuple(work_items_raw),
        provenance=Provenance(
            tier=tier,
            note=_require_str(tier_raw, "note", str(path)),
            anchors=tuple(anchors),
        ),
        platform=ScenarioPlatform(lane=lane_id, boundaries=boundaries),
        authored_intent=authored_intent,
        expected_native=expected_native,
        open_questions=tuple(open_questions_raw),
        path=path,
    )


def load_corpus(
    directory: Path, registry: PlatformRegistry
) -> tuple[Scenario, ...]:
    """Load every scenario under ``directory`` (one file per family subdir).

    ``platforms.json`` in the same directory is the registry source; scenario
    files are the remaining ``<family>/<scenario-id>.json`` files.
    """
    scenarios: list[Scenario] = []
    for path in sorted(directory.glob("*/*.json")):
        scenarios.append(load_scenario(path, registry))
    if not scenarios:
        raise _err(str(directory), "no scenario files found")
    ids = [scenario.scenario_id for scenario in scenarios]
    duplicates = sorted(item for item, n in Counter(ids).items() if n > 1)
    if duplicates:
        raise _err(str(directory), f"duplicate scenario_id values: {duplicates}")
    return tuple(scenarios)


def anchor_violations(scenario: Scenario, repo_root: Path) -> tuple[str, ...]:
    """Return every anchor integrity violation for a scenario.

    An anchor whose file vanished or whose recorded sha256 no longer matches
    is reported, so a changed capture breaks the corpus loudly instead of
    silently shifting the ground truth under the scenarios that cite it.
    """
    violations: list[str] = []
    for anchor in scenario.provenance.anchors:
        target = repo_root / anchor.path
        if not target.is_file():
            violations.append(
                f"{scenario.scenario_id}: anchor path does not exist: {anchor.path}"
            )
            continue
        if anchor.sha256 is not None:
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
            if digest != anchor.sha256:
                violations.append(
                    f"{scenario.scenario_id}: anchor sha256 mismatch for {anchor.path}: "
                    f"recorded {anchor.sha256}, actual {digest}"
                )
    return tuple(violations)
