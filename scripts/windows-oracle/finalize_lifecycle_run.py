#!/usr/bin/env python3
"""Finalize the Plan 034 same-domain lifecycle lane.

Grades one question per cell of ``gpo_studio.lifecycle.SCOPE_SURVIVAL``:
**after this GPMC operation, did this part of the GPO's scope survive the way
Studio predicted?** The observation is classified from what the guest read
back, using the same vocabulary as the table:

``kept``       the target carries the source's value (as authored and backed up);
``replaced``   the target carries the value it held just before the operation;
``lost``       the target carries no value (no links, no filter, no description);
``defaulted``  a Windows-assigned GUID, or the DACL of an untouched ``New-GPO``
               control created by the same run;
``unclassified`` none of those -- itself a finding.

The checks fall in two groups, and the verdict reports both:

* **lane checks** say whether the run is a valid measurement (environment,
  ownership, authoring landed as specified, every dimension distinguishable,
  the creation inventory complete, cleanup proven, bound source intact). A run
  that fails one of these says nothing about Studio.
* **claim checks** say whether Studio's predictions and plan claims agree with
  Windows. A valid run that fails these is the lane doing its job: the
  mismatches are recorded as data, ``lifecycle.py`` is corrected, and the lane
  is re-run.

**Missing or malformed data never passes.** Every value the grading reads is
type-checked first -- no ``str()`` coercion, so ``null`` cannot become the
string ``"None"`` and compare equal to another ``null``. A malformed result is
a comparison error, which fails the lane and every claim. In particular an
unparseable ``gPCWQLFilter`` is a harness error, never "no filter" (review
finding 3, 2026-10-08).

The expectation is the controller-built ``expected.json``, hash-bound below
(WI-025) and recomputed here from the bound builder and ``lifecycle.py``; the
guest never sees it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import runpy
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from gpo_studio.backup import BackupError, read_backup
from gpo_studio.lifecycle import manifest_from_backup
from gpo_studio.model import ValidationError
from gpo_studio.oracle_evidence import (
    OracleEvidenceError,
    assert_bound_source_bytes,
    lane_environment_violations,
    manifest_bound_source,
    tag_evidence_commit,
)

CANDIDATE_EXPECTATION = "expected.json"

#: Every file the candidate builder writes. Named rather than globbed, so a
#: consumed artifact the verdict never mentions is refused at the door.
REQUIRED_CANDIDATE_FILES = (CANDIDATE_EXPECTATION,)

DEPLOYED_FILES = {"run-lifecycle.ps1": "scripts/windows-oracle/run-lifecycle.ps1"}
LOCAL_FILES = {
    "run-lifecycle-oracle.sh": "scripts/windows-oracle/run-lifecycle-oracle.sh",
    "finalize_lifecycle_run.py": "scripts/windows-oracle/finalize_lifecycle_run.py",
    "build-lifecycle-candidate.py": "scripts/plan-033/build-lifecycle-candidate.py",
    "psdirect.ps1": "scripts/windows-oracle/psdirect.ps1",
    "lifecycle.py": "src/gpo_studio/lifecycle.py",
    "backup.py": "src/gpo_studio/backup.py",
    "oracle_evidence.py": "src/gpo_studio/oracle_evidence.py",
}
BUILDER = "scripts/plan-033/build-lifecycle-candidate.py"

OPERATIONS = ("copy", "copy_with_acl", "import_as_new", "import_into_existing", "restore_in_place")
DIMENSIONS = (
    "settings",
    "gpo_guid",
    "acl_security_filtering",
    "wmi_association",
    "links",
    "description",
)
OUTCOMES = frozenset({"kept", "lost", "defaulted", "replaced"})
IDENTITIES = frozenset({"source", "existing_target", "windows_assigned"})
COMMANDS = ("backup", *OPERATIONS)
PREEXISTING = frozenset({"import_into_existing", "restore_in_place"})
#: Which created-inventory role each new-GPO operation registers.
NEW_GPO_ROLES = ("copy", "copy_with_acl", "import_as_new")
INVENTORY_ROLES = frozenset({"control", "source", "target", *NEW_GPO_ROLES})
RESIDUAL_CATEGORIES = frozenset(
    {
        "surviving_gpos",
        "surviving_links",
        "surviving_wmi_filters",
        "surviving_groups",
        "surviving_ous",
    }
)
AUTHENTICATED_USERS = "S-1-5-11"

RESULT_KEYS = frozenset(
    {
        "schema_version",
        "run_id",
        "domain",
        "ownership_established",
        "fixture",
        "backup",
        "control_state",
        "source_baseline",
        "target_baseline",
        "restore_perturbed",
        "operations",
        "created",
        "cleanup",
        "cleanup_succeeded",
        "cleanup_state_restored",
        "environment",
        "error",
    }
)
STATE_STRINGS = (
    "gpo_id",
    "display_name",
    "description",
    "gpo_status",
    "settings_value",
    "gpc_wql_filter",
    "wmi_filter_id",
    "wmi_filter_name",
    "dacl_sddl",
)
STATE_LISTS = ("links", "permissions", "permission_names")
STATE_KEYS = frozenset({*STATE_STRINGS, *STATE_LISTS, "gpo_cmt_present"})
FIXTURE_KEYS = frozenset(
    {
        "stamp",
        "policy_key",
        "value_name",
        "source_value",
        "target_value",
        "perturbed_value",
        "source_description",
        "target_description",
        "perturbed_description",
        "ou_parent_dn",
        "ou_source_dn",
        "ou_target_dn",
        "source_group_sid",
        "target_group_sid",
        "source_wmi_filter_id",
        "target_wmi_filter_id",
        "source_wmi_filter_name",
        "target_wmi_filter_name",
        "import_as_new_name",
        "source_group_name",
        "target_group_name",
        "domain_dn",
        "ownership_marker",
    }
)
OPERATION_KEYS = frozenset(
    {"succeeded", "error", "target_preexisted", "target_before", "target_after"}
)

_HEX_GUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
_BARE_GUID = re.compile(rf"^{_HEX_GUID}$")
_BRACED_GUID = re.compile(rf"^\{{{_HEX_GUID}\}}$")
_SID = re.compile(r"^S-1-\d+(?:-\d+)+$")
_PERMISSION = re.compile(r"^S-1-\d+(?:-\d+)+\|Gpo[A-Za-z]+\|(?:True|False)$")
_WQL = re.compile(rf"^\[[^;\]]+;(\{{{_HEX_GUID}\}});\d+\]$")
_RUN_ID = re.compile(r"^lifecycle-\d{14}-\d{4}$")
_STAMP = re.compile(r"^\d{14}-\d{4}$")
_GROUP_NAME = re.compile(r"^zzlc-(\d{6})-(src|tgt)$")
_NIL_GUID = "00000000-0000-0000-0000-000000000000"
_WMI_CONTAINER = "CN=SOM,CN=WMIPolicy,CN=System,"
#: The GPO display-name suffix the guest generates for each inventory role.
GPO_NAME_SUFFIX = {
    "control": "control",
    "source": "source",
    "target": "target",
    "copy": "copy",
    "copy_with_acl": "copy_with_acl",
    "import_as_new": "imported",
}

Value = str | frozenset[str]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _bare(value: str) -> str:
    return value.strip().strip("{}").casefold()


def _text(data: Mapping[str, Any], key: str, label: str) -> str:
    value = data[key]
    if not isinstance(value, str):
        raise ValueError(f"{label}.{key} is {type(value).__name__}, not a string")
    return value


def _strings(value: object, label: str) -> list[str]:
    # PowerShell 5.1 serializes a one-element array that lost its @() as a bare
    # string; that is a harness defect, not a value, so it is refused.
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError(f"{label} is not a list of strings")
    return value


def _mapping(raw: object, keys: frozenset[str], label: str) -> Mapping[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError(f"{label} is not an object")
    if set(raw) != keys:
        raise ValueError(f"{label} keys differ from the schema: {sorted(set(raw) ^ keys)}")
    return raw


def validate_state(raw: object, label: str) -> Mapping[str, Any]:
    """A read-back GPO state with every field type-checked, or ValueError."""
    state = _mapping(raw, STATE_KEYS, label)
    for key in STATE_STRINGS:
        _text(state, key, label)
    for key in STATE_LISTS:
        _strings(state[key], f"{label}.{key}")
    if not isinstance(state["gpo_cmt_present"], bool):
        raise ValueError(f"{label}.gpo_cmt_present is not a boolean")
    if not _BARE_GUID.match(state["gpo_id"]) or _bare(state["gpo_id"]) == _NIL_GUID:
        raise ValueError(f"{label}.gpo_id {state['gpo_id']!r} is not a GPO GUID")
    for entry in state["permissions"]:
        if not _PERMISSION.match(entry):
            raise ValueError(f"{label}.permissions entry {entry!r} is not SID|level|denied")
    wql = state["gpc_wql_filter"]
    if wql == "":
        if state["wmi_filter_id"] != "":
            raise ValueError(f"{label}: wmi_filter_id without a gPCWQLFilter")
    else:
        match = _WQL.match(wql)
        if match is None:
            raise ValueError(f"{label}: gPCWQLFilter {wql!r} is present but unparseable")
        if _bare(match.group(1)) != _bare(state["wmi_filter_id"]):
            raise ValueError(f"{label}: wmi_filter_id disagrees with gPCWQLFilter")
    return state


def validate_fixture(raw: object) -> Mapping[str, Any]:
    fixture = _mapping(raw, FIXTURE_KEYS, "fixture")
    for key in FIXTURE_KEYS:
        if not _text(fixture, key, "fixture"):
            raise ValueError(f"fixture.{key} is empty")
    for key in ("source_group_sid", "target_group_sid"):
        if not _SID.match(fixture[key]):
            raise ValueError(f"fixture.{key} is not a SID")
    for key in ("source_wmi_filter_id", "target_wmi_filter_id"):
        if not _BRACED_GUID.match(fixture[key]):
            raise ValueError(f"fixture.{key} is not a braced GUID")
    if not _STAMP.match(fixture["stamp"]):
        raise ValueError("fixture.stamp is not the guest's yyyyMMddHHmmss-nnnn stamp")
    source_group = _GROUP_NAME.match(fixture["source_group_name"])
    target_group = _GROUP_NAME.match(fixture["target_group_name"])
    if (
        source_group is None
        or target_group is None
        or source_group.group(2) != "src"
        or target_group.group(2) != "tgt"
        or source_group.group(1) != target_group.group(1)
    ):
        raise ValueError("fixture group names are not the guest's zzlc-NNNNNN-src/tgt pair")
    return fixture


def dimension_value(state: Mapping[str, Any], dimension: str) -> Value:
    """The comparable value of one scope dimension of a read-back GPO state."""
    if dimension == "settings":
        return _text(state, "settings_value", "state")
    if dimension == "gpo_guid":
        return _bare(_text(state, "gpo_id", "state"))
    if dimension == "acl_security_filtering":
        # Trustee SID | permission level | denied. Names are evidence only:
        # they are display strings, and the SID is what the DACL holds.
        return frozenset(_strings(state["permissions"], "permissions"))
    if dimension == "wmi_association":
        return _bare(_text(state, "wmi_filter_id", "state"))
    if dimension == "links":
        return frozenset(dn.casefold() for dn in _strings(state["links"], "links"))
    if dimension == "description":
        return _text(state, "description", "state")
    raise ValueError(f"unknown scope dimension {dimension!r}")


def _empty(value: Value) -> bool:
    return len(value) == 0


def classify(
    dimension: str,
    source: Mapping[str, Any],
    before: Mapping[str, Any] | None,
    after: Mapping[str, Any],
    fresh: Mapping[str, Any],
) -> str:
    """Name what happened to one dimension, in SCOPE_SURVIVAL's vocabulary."""
    observed = dimension_value(after, dimension)
    if observed == dimension_value(source, dimension):
        return "kept"
    if before is not None and observed == dimension_value(before, dimension):
        return "replaced"
    if _empty(observed):
        return "lost"
    if dimension == "gpo_guid" and before is None:
        return "defaulted"
    if dimension == "acl_security_filtering" and observed == dimension_value(fresh, dimension):
        return "defaulted"
    return "unclassified"


def _authored(state: Mapping[str, Any], fixture: Mapping[str, Any], role: str) -> bool:
    """Did the guest author this GPO's scope exactly as the fixture says?"""
    value, description, ou, wmi, sid = {
        "source": (
            "source_value", "source_description", "ou_source_dn",
            "source_wmi_filter_id", "source_group_sid",
        ),
        "target": (
            "target_value", "target_description", "ou_target_dn",
            "target_wmi_filter_id", "target_group_sid",
        ),
        "perturbed": (
            "perturbed_value", "perturbed_description", "ou_target_dn",
            "target_wmi_filter_id", "target_group_sid",
        ),
    }[role]
    permissions = dimension_value(state, "acl_security_filtering")
    ok = (
        dimension_value(state, "settings") == fixture[value]
        and dimension_value(state, "description") == fixture[description]
        and dimension_value(state, "links") == frozenset({fixture[ou].casefold()})
        and dimension_value(state, "wmi_association") == _bare(fixture[wmi])
        and f"{fixture[sid]}|GpoApply|False" in permissions
        # MS16-072 filtering: Authenticated Users reduced to Read.
        and f"{AUTHENTICATED_USERS}|GpoRead|False" in permissions
    )
    if role == "perturbed":
        # The source's own filter group must be gone, or "restore put the ACL
        # back" and "restore left it alone" read the same.
        ok = ok and not any(p.startswith(f"{fixture['source_group_sid']}|") for p in permissions)
    return ok


def _untouched_new_gpo(state: Mapping[str, Any]) -> bool:
    """The control must be what New-GPO makes: empty, unlinked, AU may apply.

    Without this, any ACL recorded as the control's would define "defaulted".
    """
    return (
        all(_empty(dimension_value(state, d)) for d in ("settings", "wmi_association", "links"))
        and dimension_value(state, "description") == ""
        and f"{AUTHENTICATED_USERS}|GpoApply|False" in dimension_value(
            state, "acl_security_filtering"
        )
    )


def _observed_identity(
    claim: str,
    before: Mapping[str, Any] | None,
    after: Mapping[str, Any],
    known_ids: set[str],
    source_id: str,
) -> bool:
    observed = _bare(after["gpo_id"])
    if claim == "source":
        return observed == source_id
    if claim == "existing_target":
        return before is not None and observed == _bare(before["gpo_id"])
    if claim == "windows_assigned":
        return before is None and observed not in known_ids
    return False


def _validate_expectation(expected: Mapping[str, Any]) -> None:
    if list(expected["operation_order"]) != list(OPERATIONS):
        raise ValueError("expectation's operation order is not the lane's")
    if list(expected["dimensions"]) != list(DIMENSIONS):
        raise ValueError("expectation's dimensions are not the lane's")
    operations = expected["operations"]
    if not isinstance(operations, dict) or set(operations) != set(OPERATIONS):
        raise ValueError("expectation does not cover exactly the lane's operations")
    for op, claim in operations.items():
        survival = claim["survival"]
        if not isinstance(survival, dict) or set(survival) != set(DIMENSIONS):
            raise ValueError(f"expectation {op} survival does not cover every dimension")
        if not set(survival.values()) <= OUTCOMES:
            raise ValueError(f"expectation {op} has an outcome outside the vocabulary")
        if claim["target_identity"] not in IDENTITIES:
            raise ValueError(f"expectation {op} has an unknown target identity")
        if not isinstance(claim["requires_target_absent"], bool):
            raise ValueError(f"expectation {op} requires_target_absent is not a boolean")


def expected_inventory(fixture: Mapping[str, Any]) -> dict[str, list[str]]:
    """Every directory object the guest generates, by exact DN, from the fixture.

    Re-review P2(b): counts, suffixes and substrings let a duplicated group DN,
    unrelated groups under the run's OU, or filter DNs outside the WMI
    container all pass. The inventory must equal this list exactly.
    """
    prefix = f"zz-studio-lifecycle-{fixture['stamp']}"
    domain = fixture["domain_dn"]
    parent = f"OU={prefix},{domain}"
    return {
        "ous": [parent, f"OU=src-link,{parent}", f"OU=tgt-link,{parent}"],
        "groups": [
            f"CN={fixture['source_group_name']},{parent}",
            f"CN={fixture['target_group_name']},{parent}",
        ],
        "wmi_filters": [
            f"CN={fixture['source_wmi_filter_id']},{_WMI_CONTAINER}{domain}",
            f"CN={fixture['target_wmi_filter_id']},{_WMI_CONTAINER}{domain}",
        ],
    }


def _fixture_names_are_generated(fixture: Mapping[str, Any]) -> bool:
    """The fixture's own DNs and names are the ones the guest's stamp generates."""
    prefix = f"zz-studio-lifecycle-{fixture['stamp']}"
    ous = expected_inventory(fixture)["ous"]
    return (
        [fixture["ou_parent_dn"], fixture["ou_source_dn"], fixture["ou_target_dn"]] == ous
        and fixture["source_wmi_filter_name"] == f"{prefix}-src-wmi"
        and fixture["target_wmi_filter_name"] == f"{prefix}-tgt-wmi"
        and fixture["import_as_new_name"] == f"{prefix}-{GPO_NAME_SUFFIX['import_as_new']}"
        and _bare(fixture["source_wmi_filter_id"]) != _bare(fixture["target_wmi_filter_id"])
    )


def _inventory_complete(
    created: object,
    fixture: Mapping[str, Any],
    ids_by_role: Mapping[str, str],
) -> bool:
    """Did the guest record exactly the objects it created, under the ids it read back?

    Review finding 6 and re-review P2(b): every DN and GPO name must be the
    exact one the run generated, each GPO must be owned (its create returned
    it), and its id must be the one read back.
    """
    inventory = _mapping(created, frozenset({"ous", "groups", "wmi_filters", "gpos"}), "created")
    expected = expected_inventory(fixture)
    for key in ("ous", "groups", "wmi_filters"):
        recorded = _strings(inventory[key], f"created.{key}")
        if [dn.casefold() for dn in recorded] != [dn.casefold() for dn in expected[key]]:
            return False
    gpos = inventory["gpos"]
    if not isinstance(gpos, list):
        raise ValueError("created.gpos is not a list")
    entries = [
        _mapping(entry, frozenset({"role", "name", "id", "owned"}), "created.gpos[]")
        for entry in gpos
    ]
    roles = [entry["role"] for entry in entries]
    if sorted(roles) != sorted(INVENTORY_ROLES):
        return False
    prefix = f"zz-studio-lifecycle-{fixture['stamp']}"
    by_role = {entry["role"]: entry for entry in entries}
    ids = [entry["id"] for entry in entries]
    return (
        all(entry["owned"] is True for entry in entries)
        and all(
            by_role[role]["name"] == f"{prefix}-{suffix}"
            for role, suffix in GPO_NAME_SUFFIX.items()
        )
        and all(
            isinstance(i, str) and bool(_BARE_GUID.match(i)) and _bare(i) != _NIL_GUID
            for i in ids
        )
        and len({_bare(i) for i in ids}) == len(ids)
        and all(
            _bare(by_role[role]["id"]) == _bare(gpo_id) for role, gpo_id in ids_by_role.items()
        )
    )


def grade(result: Mapping[str, Any], expected: Mapping[str, Any]) -> tuple[
    dict[str, bool], dict[str, bool], dict[str, Any]
]:
    """Grade a result against an expectation; pure, so tests drive it directly.

    Returns ``(lane_checks, claim_checks, comparison)``. Raises ``KeyError`` /
    ``TypeError`` / ``ValueError`` on a malformed result, which ``main``
    records as a comparison error that fails the lane and every claim.
    """
    _validate_expectation(expected)
    fixture = validate_fixture(result["fixture"])
    source = validate_state(result["source_baseline"], "source_baseline")
    target = validate_state(result["target_baseline"], "target_baseline")
    control = validate_state(result["control_state"], "control_state")
    perturbed = validate_state(result["restore_perturbed"], "restore_perturbed")
    operations = result["operations"]
    if not isinstance(operations, dict) or set(operations) != set(OPERATIONS):
        raise ValueError("result does not report exactly the lane's operations")
    backup = _mapping(
        result["backup"], frozenset({"backup_id", "source_gpo_id", "relative_path"}), "backup"
    )
    if not _BRACED_GUID.match(_text(backup, "backup_id", "backup")):
        raise ValueError("backup.backup_id is not a braced GUID")

    lane: dict[str, bool] = {
        "source_authored_as_specified": _authored(source, fixture, "source"),
        "target_authored_as_specified": _authored(target, fixture, "target"),
        # Re-review P2(c): the perturbed object must BE the source being
        # restored, not some other GPO in the perturbed shape.
        "restore_perturbation_landed": _authored(perturbed, fixture, "perturbed")
        and _bare(perturbed["gpo_id"]) == _bare(source["gpo_id"])
        and _bare(perturbed["gpo_id"]) != _bare(target["gpo_id"]),
        "fixture_names_are_generated": _fixture_names_are_generated(fixture),
        "control_is_an_untouched_new_gpo": _untouched_new_gpo(control),
        "backup_names_the_source": _bare(_text(backup, "source_gpo_id", "backup"))
        == _bare(source["gpo_id"])
        and backup["relative_path"] == "backup",
        # Without these, two outcomes would read the same and a cell could pass
        # for the wrong reason.
        "every_dimension_distinguishable": all(
            not _empty(dimension_value(source, d))
            and dimension_value(target, d) != dimension_value(source, d)
            and dimension_value(perturbed, d) != dimension_value(source, d)
            for d in DIMENSIONS
            if d != "gpo_guid"
        )
        and dimension_value(source, "acl_security_filtering")
        != dimension_value(control, "acl_security_filtering")
        and dimension_value(target, "gpo_guid") != dimension_value(source, "gpo_guid"),
    }
    claims: dict[str, bool] = {}
    observed: dict[str, dict[str, str]] = {}
    mismatches: list[dict[str, str]] = []
    known_ids = {_bare(s["gpo_id"]) for s in (source, target, control)}
    ids_by_role = {"source": source["gpo_id"], "target": target["gpo_id"],
                   "control": control["gpo_id"]}
    for op in OPERATIONS:
        record = _mapping(operations[op], OPERATION_KEYS, f"operations.{op}")
        claim = expected["operations"][op]
        if not isinstance(record["succeeded"], bool):
            raise ValueError(f"operations.{op}.succeeded is not a boolean")
        if record["error"] is not None and not isinstance(record["error"], str):
            raise ValueError(f"operations.{op}.error is neither null nor a string")
        before = (
            validate_state(record["target_before"], f"{op}.target_before")
            if op in PREEXISTING and record["target_before"] is not None
            else None
        )
        bound = True
        if op == "restore_in_place":
            # Review finding 4: grade against the snapshot whose perturbation
            # was verified, not a second, unchecked copy of it.
            bound = record["target_before"] == result["restore_perturbed"]
            lane["restore_graded_against_the_verified_perturbation"] = bound
        if op == "import_into_existing":
            lane["pre_existing_target_untouched_until_its_import"] = before is not None and all(
                dimension_value(before, d) == dimension_value(target, d) for d in DIMENSIONS
            )
        succeeded = (
            record["succeeded"] is True
            and record["error"] is None
            and record["target_after"] is not None
            and (before is not None or op not in PREEXISTING)
            and bound
        )
        lane[f"operation_{op}_succeeded"] = succeeded
        # Measured by the guest immediately before the operation. A creating
        # operation's claims hold only if its target name was free
        # (Import-GPO -CreateIfNeeded imports into an existing GPO).
        expected_preexistence = op in PREEXISTING
        if claim["requires_target_absent"] and expected_preexistence:
            raise ValueError(f"expectation {op} requires absence of a pre-existing target")
        lane[f"operation_{op}_target_preexistence_measured"] = (
            record["target_preexisted"] is expected_preexistence
        )
        observed[op] = {}
        after = (
            validate_state(record["target_after"], f"{op}.target_after") if succeeded else None
        )
        if after is not None and op in NEW_GPO_ROLES:
            ids_by_role[op] = after["gpo_id"]
        claims[f"plan_target_identity.{op}"] = after is not None and _observed_identity(
            claim["target_identity"], before, after, known_ids, _bare(source["gpo_id"])
        )
        for dim in DIMENSIONS:
            predicted = claim["survival"][dim]
            outcome = (
                classify(dim, source, before, after, control) if after is not None else "not-run"
            )
            observed[op][dim] = outcome
            claims[f"survival.{op}.{dim}"] = outcome == predicted
            if outcome != predicted:
                mismatches.append(
                    {"operation": op, "dimension": dim, "predicted": predicted, "observed": outcome}
                )
    lane["creation_inventory_complete"] = _inventory_complete(
        result["created"], fixture, ids_by_role
    )
    comparison = {
        "observed_survival": observed,
        "predicted_survival": {
            op: dict(expected["operations"][op]["survival"]) for op in OPERATIONS
        },
        "mismatches": mismatches,
    }
    return lane, claims, comparison


def wmi_reference_identifies(reference: str, filter_id: str, filter_name: str) -> bool:
    """Does a backup's WMIFilter text identify exactly this filter?

    Re-review P2(a): containment let ``<name>-other-filter`` and a wrong GUID
    beside the right name both pass. Only three exact shapes are accepted --
    the gPCWQLFilter form ``[domain;{id};n]`` with exactly this id, the bare
    braced id, or exactly this name -- because the populated shape has never
    been captured; this lane's verdict records which one Windows writes.
    """
    text = reference.strip()
    match = _WQL.match(text)
    if match is not None:
        return _bare(match.group(1)) == _bare(filter_id)
    if _BRACED_GUID.match(text):
        return _bare(text) == _bare(filter_id)
    return text == filter_name


def bridge_check(run: Path, result: Mapping[str, Any]) -> tuple[bool, dict[str, Any]]:
    """Does ``manifest_from_backup(read_backup(...))`` read the real backup right?

    The WMI reference must identify the AUTHORED source filter -- by its id or
    its name -- and not the target's (review finding 7): "some text is present"
    cannot establish that the backup preserved this association.
    """
    try:
        backup = _mapping(
            result["backup"], frozenset({"backup_id", "source_gpo_id", "relative_path"}),
            "backup",
        )
        source = validate_state(result["source_baseline"], "source_baseline")
        fixture = validate_fixture(result["fixture"])
        relative = _text(backup, "relative_path", "backup")
        manifest = manifest_from_backup(read_backup(run / relative))
    except (BackupError, ValidationError, OSError, KeyError, ValueError) as exc:
        return False, {"error": f"{type(exc).__name__}: {exc}"}
    def identifies(prefix: str) -> bool:
        return wmi_reference_identifies(
            manifest.wmi_filter_reference,
            fixture[f"{prefix}_wmi_filter_id"],
            fixture[f"{prefix}_wmi_filter_name"],
        )

    data = {
        "backup_id": manifest.backup_id,
        "gpo_guid": manifest.gpo_guid,
        "gpo_display_name": manifest.gpo_display_name,
        "created_at": manifest.created_at,
        "has_wmi_filter": manifest.has_wmi_filter,
        # The populated Backup.xml WMIFilter shape: never captured before
        # this lane, recorded so the bridge can stop keeping it verbatim.
        "wmi_filter_reference": manifest.wmi_filter_reference,
        "wmi_reference_names_source_filter": identifies("source"),
        "wmi_reference_names_target_filter": identifies("target"),
        "files": [f.relative_path for f in manifest.files],
    }
    ok = (
        _bare(manifest.gpo_guid) == _bare(source["gpo_id"])
        and _bare(manifest.backup_id) == _bare(backup["backup_id"])
        and _bare(manifest.backup_id) != _bare(manifest.gpo_guid)
        and manifest.gpo_display_name == source["display_name"]
        and manifest.has_wmi_filter
        and data["wmi_reference_names_source_filter"] is True
        and data["wmi_reference_names_target_filter"] is False
    )
    return ok, data


def _member(environment: object) -> bool:
    return (
        isinstance(environment, dict)
        and type(environment.get("computer_system_domain_role")) is int
        and environment["computer_system_domain_role"] == 3
    )


def _cleanup_proven(result: Mapping[str, Any]) -> bool:
    """Exactly the five named residual categories, each re-queried and empty."""
    cleanup = result.get("cleanup")
    if not isinstance(cleanup, dict) or set(cleanup) != {"problems", "residual"}:
        return False
    residual = cleanup["residual"]
    return (
        cleanup["problems"] == []
        and isinstance(residual, dict)
        and set(residual) == RESIDUAL_CATEGORIES
        and all(value == [] for value in residual.values())
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--candidate-root", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--no-tag", action="store_true")
    args = parser.parse_args()
    run = args.run_dir.resolve()
    candidate_root = args.candidate_root.resolve()
    repo = args.repo_root.resolve()
    try:
        assert_bound_source_bytes(repo, {**DEPLOYED_FILES, **LOCAL_FILES}.values())
    except OracleEvidenceError as exc:
        print(f"finalize refused: {exc}", file=sys.stderr)
        return 1

    missing = [n for n in REQUIRED_CANDIDATE_FILES if not (candidate_root / n).is_file()]
    if missing:
        print(f"candidate root is missing {', '.join(missing)}", file=sys.stderr)
        return 1
    if not (run / "result.json").is_file():
        print(f"run directory has no result.json: {run}", file=sys.stderr)
        return 1

    result = json.loads((run / "result.json").read_text(encoding="utf-8-sig"))
    expected = json.loads((candidate_root / CANDIDATE_EXPECTATION).read_text(encoding="utf-8"))
    if not isinstance(result, dict) or not isinstance(expected, dict):
        print("result.json and expected.json must each be a JSON object", file=sys.stderr)
        return 1

    environment = result.get("environment")
    violations = (
        list(lane_environment_violations(environment))
        if isinstance(environment, dict)
        else ["environment was not recorded as an object"]
    )
    run_id = result.get("run_id")
    lane: dict[str, bool] = {
        "result_schema_exact": set(result) == RESULT_KEYS
        and type(result.get("schema_version")) is int
        and result["schema_version"] == 1,
        "run_id_well_formed": isinstance(run_id, str) and bool(_RUN_ID.match(run_id)),
        "domain_recorded": isinstance(result.get("domain"), str) and bool(result["domain"]),
        "ownership_established": result.get("ownership_established") is True,
        "harness_reported_no_error": result.get("error") is None,
        "member_server_host_role": _member(environment),
        "environment_matches_frozen_spec": not violations,
        "raw_command_artifacts_complete": all(
            (run / "commands" / f"{n}.{s}.txt").is_file()
            for n in COMMANDS
            for s in ("stdout", "stderr")
        )
        and (run / "builder.stdout.txt").is_file(),
        "cleanup_succeeded": result.get("cleanup_succeeded") is True,
        "cleanup_state_restored": result.get("cleanup_state_restored") is True,
        "cleanup_residual_empty": _cleanup_proven(result),
    }

    # WI-025: the expectation must be what the bound source says, not merely
    # a file that happens to sit in the candidate root.
    try:
        recomputed = runpy.run_path(str(repo / BUILDER))["expectation"]()
        lane["expectation_reproduces_from_bound_source"] = recomputed == expected
    except Exception as exc:  # noqa: BLE001 - any failure means "not reproduced"
        print(f"expectation could not be recomputed: {exc}", file=sys.stderr)
        lane["expectation_reproduces_from_bound_source"] = False

    bridge_ok, bridge = bridge_check(run, result)
    claims: dict[str, bool] = {"backup_bridge_reads_windows_backup": bridge_ok}

    error: str | None = None
    comparison: dict[str, Any] = {}
    try:
        graded_lane, graded_claims, comparison = grade(result, expected)
        lane["result_gradable"] = True
        lane.update(graded_lane)
        claims.update(graded_claims)
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        error = f"{type(exc).__name__}: {exc}"
        lane["result_gradable"] = False
        for op in OPERATIONS:
            claims[f"plan_target_identity.{op}"] = False
            for dim in DIMENSIONS:
                claims[f"survival.{op}.{dim}"] = False
    comparison["backup_bridge"] = bridge

    bound_local = manifest_bound_source(repo, LOCAL_FILES)
    source_hashes: dict[str, str] = {name: entry["sha256"] for name, entry in bound_local.items()}
    deployed_ok = True
    for name, relative in DEPLOYED_FILES.items():
        source_hashes[name] = _sha(repo / relative)
        deployed_ok &= (run / "deployed" / name).is_file() and _sha(
            run / "deployed" / name
        ) == source_hashes[name]
    lane["deployed_harness_matches_source"] = deployed_ok

    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    dirty = bool(
        subprocess.run(
            ["git", "status", "--porcelain"], cwd=repo, check=True, capture_output=True, text=True
        ).stdout
    )
    lane["source_tree_clean"] = not dirty

    harness_valid = all(lane.values())
    predictions_agree = all(claims.values())
    verdict = {
        "schema_version": 1,
        "run_id": run_id,
        "transport": "psdirect",
        "passed": harness_valid and predictions_agree,
        "harness_valid": harness_valid,
        "predictions_agree": predictions_agree,
        "checks": {**lane, **claims},
        "lane_checks": sorted(lane),
        "claim_checks": sorted(claims),
        "comparison": comparison,
        "comparison_error": error,
        "harness_error": result.get("error"),
        "candidate": {
            path.relative_to(candidate_root).as_posix(): _sha(path)
            for path in sorted(candidate_root.rglob("*"))
            if path.is_file()
        },
        "environment": environment,
        "environment_violations": violations,
        "source": {
            "commit": commit,
            "dirty": dirty,
            "files": source_hashes,
            "paths": {**DEPLOYED_FILES, **LOCAL_FILES},
            "banked_copies": sorted(DEPLOYED_FILES),
        },
        "artifacts": {
            p.relative_to(run).as_posix(): _sha(p)
            for p in sorted(run.rglob("*"))
            if p.is_file() and p.name != "verification.json"
        },
    }

    tag_outcome = None
    if verdict["passed"] and isinstance(run_id, str) and not args.no_tag:
        try:
            tag_outcome = tag_evidence_commit(repo, run_id, commit)
        except OracleEvidenceError as exc:
            print(f"evidence tag failed: {exc}", file=sys.stderr)
            return 1
    (run / "verification.json").write_text(
        json.dumps(verdict, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(verdict, indent=2, sort_keys=True))
    if tag_outcome is not None:
        print(f"EVIDENCE_TAG={tag_outcome}")
    return 0 if verdict["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
