#!/usr/bin/env python3
"""Validate a WP-3 secedit round trip and write its reviewable verdict."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from gpo_studio.oracle_evidence import (
    OracleEvidenceError,
    lane_environment_violations,
    tag_evidence_commit,
)
from gpo_studio.security_template import (
    SecurityTemplate,
    SecurityTemplateError,
    decode_security_template,
    parse_security_template,
)

#: Harness files deployed to the Windows guest, per transport.  The finalizer
#: binds each retrieved copy to the committed source, so this set has to match
#: what the lane actually deploys or the provenance check is meaningless.
#:
#: ``psdirect`` is the only transport, and it deploys the harness alone: no
#: scheduled-task launcher, because PowerShell Direct carries the credential to
#: the guest through the hypervisor and the resulting logon authenticates
#: outward to AD and SYSVOL on its own.  The table stays keyed by transport
#: because the verdict records which one produced it, and a verdict from the
#: retired SSH path names a set this table no longer contains -- which is how a
#: reviewer tells them apart.
TRANSPORT_DEPLOYED_FILES: dict[str, dict[str, str]] = {
    "psdirect": {
        "run-wp3-security-template.ps1": (
            "scripts/windows-oracle/run-wp3-security-template.ps1"
        ),
    },
}

#: Scripts that execute on the controller, where the source-tree copy *is* the
#: executed copy.  ``psdirect.ps1`` belongs here rather than in the deployed set:
#: it drives the transport from the controller and is never copied to the guest.
TRANSPORT_LOCAL_FILES: dict[str, dict[str, str]] = {
    "psdirect": {
        "run-wp3-oracle.sh": "scripts/windows-oracle/run-wp3-oracle.sh",
        "build-wp3-candidate.py": "scripts/plan-033/build-wp3-candidate.py",
        "finalize_wp3_run.py": "scripts/windows-oracle/finalize_wp3_run.py",
        "psdirect.ps1": "scripts/windows-oracle/psdirect.ps1",
    },
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


#: Well-known principals, by the SID `secedit` resolves them to on export.
#:
#: The estate's export replaces every authored NAME with a SID, on both sides of
#: a `Group Membership` entry -- the group in the key and the members in the
#: value. Two of those SIDs are constants (`BUILTIN\` aliases), and one is not:
#: the built-in Administrator is `S-1-5-21-<machine>-500`, so the expected side
#: cannot spell it out. It is matched on its **RID** instead, which is what
#: makes it well-known in the first place.
_WELL_KNOWN_SIDS: dict[str, str] = {
    "administrators": "S-1-5-32-544",
    "power users": "S-1-5-32-547",
    "backup operators": "S-1-5-32-551",
    "users": "S-1-5-32-545",
}
_WELL_KNOWN_RIDS: dict[str, str] = {
    "administrator": "500",
    "guest": "501",
}


def _canonical_principal(raw: str) -> str:
    """Reduce a principal to a form both sides of the comparison can reach.

    `secedit` writes SIDs; the candidate writes whichever is clearer to read.
    A domain or machine-relative SID is reduced to its RID, because the machine
    half is not knowable when the candidate is built.
    """
    value = raw.strip().lstrip("*").casefold()
    if not value:
        return ""
    if value in _WELL_KNOWN_SIDS:
        return _WELL_KNOWN_SIDS[value].casefold()
    if value in _WELL_KNOWN_RIDS:
        return f"rid:{_WELL_KNOWN_RIDS[value]}"
    if value.startswith("s-1-5-21-"):
        return f"rid:{value.rsplit('-', 1)[-1]}"
    return value


def _principal_set(raw: str) -> set[str]:
    return {
        canonical
        for part in raw.split(",")
        if (canonical := _canonical_principal(part))
    }


def _group_membership_key_matches(expected_key: str, actual_key: str) -> bool:
    """Match `<group>__Members` where the group may be a name or a SID."""
    for suffix in ("__memberof", "__members"):
        expected_folded = expected_key.casefold()
        actual_folded = actual_key.casefold()
        if expected_folded.endswith(suffix) and actual_folded.endswith(suffix):
            return _canonical_principal(
                expected_folded[: -len(suffix)]
            ) == _canonical_principal(actual_folded[: -len(suffix)])
    return False


def _setting_matches(
    template: SecurityTemplate,
    *,
    section: str,
    key: str,
    expected: str,
) -> bool:
    if section.casefold() == "group membership":
        # The key itself is a principal here, and the export rewrites it, so the
        # entry cannot be found by lookup at all -- it has to be searched for.
        actual_section = template.get_section(section)
        if actual_section is None:
            return False
        for actual_key, actual_value in actual_section.entries:
            if _group_membership_key_matches(key, actual_key):
                return _principal_set(actual_value) == _principal_set(expected)
        return False

    actual = template.get_value(section, key)
    if actual is None:
        return False
    if section.casefold() == "privilege rights":
        expected_principals = {
            principal.strip().casefold()
            for principal in expected.split(",")
            if principal.strip()
        }
        actual_principals = {
            principal.strip().casefold()
            for principal in actual.split(",")
            if principal.strip()
        }
        return actual_principals == expected_principals
    return actual.strip() == expected.strip()


def _matches_expected(
    template: SecurityTemplate,
    settings: list[dict[str, Any]],
) -> tuple[bool, list[dict[str, str | None]]]:
    differences: list[dict[str, str | None]] = []
    for setting in settings:
        section = str(setting["section"])
        key = str(setting["key"])
        expected = str(setting["value"])
        if not _setting_matches(
            template,
            section=section,
            key=key,
            expected=expected,
        ):
            differences.append(
                {
                    "section": section,
                    "key": key,
                    "expected": expected,
                    "actual": template.get_value(section, key),
                }
            )
    return not differences, differences


def _candidate_key_set_matches(
    template: SecurityTemplate,
    settings: list[dict[str, Any]],
) -> bool:
    """Require exact keys for each authored policy area in Studio output."""
    expected_by_section: dict[str, set[str]] = {}
    for setting in settings:
        section = str(setting["section"]).casefold()
        expected_by_section.setdefault(section, set()).add(
            str(setting["key"]).casefold()
        )

    authored_sections = [
        section
        for section in template.sections
        if section.name.casefold() not in {"unicode", "version"}
    ]
    actual_section_names = [section.name.casefold() for section in authored_sections]
    if (
        len(actual_section_names) != len(set(actual_section_names))
        or set(actual_section_names) != set(expected_by_section)
    ):
        return False

    for section_name, expected_keys in expected_by_section.items():
        section = template.get_section(section_name)
        if section is None:
            return False
        actual_keys = [key.casefold() for key, _ in section.entries]
        if len(actual_keys) != len(set(actual_keys)):
            return False
        if section_name == "group membership":
            # Keys here are principals and the export rewrites them, so an exact
            # set comparison would fail on a correctly authored template. Match
            # them pairwise instead, still requiring a bijection so an extra or
            # missing entry is caught.
            unmatched = list(actual_keys)
            for expected_key in expected_keys:
                found = next(
                    (a for a in unmatched if _group_membership_key_matches(expected_key, a)),
                    None,
                )
                if found is None:
                    return False
                unmatched.remove(found)
            if unmatched:
                return False
            continue
        if set(actual_keys) != expected_keys:
            return False
    return True


def _observed_operations_match(result: dict[str, Any]) -> bool:
    operations = result.get("invoked_operations")
    if not isinstance(operations, list):
        return False
    expected_names = ["validate", "import", "export"]
    observed_names: list[str] = []
    for operation in operations:
        if not isinstance(operation, dict):
            return False
        name = operation.get("name")
        arguments = operation.get("arguments")
        if not isinstance(name, str) or not isinstance(arguments, list):
            return False
        if not arguments or not all(isinstance(item, str) for item in arguments):
            return False
        observed_names.append(name)
        if str(arguments[0]).casefold() != f"/{name}".casefold():
            return False
        if any(str(argument).casefold() == "/configure" for argument in arguments):
            return False
    if observed_names != expected_names:
        return False

    # Import and export must request the SAME areas.
    #
    # secedit only handles the areas it is asked for, and it says nothing about
    # the ones it was not: a section outside them never reaches the database and
    # never appears in the export, which the comparison reports as "expected X,
    # actual None" -- indistinguishable from a template Studio wrote wrongly.
    # That cost a run on 2026-08-04, when Group Membership was authored while
    # the lane still asked only for securitypolicy and user_rights.
    #
    # If the two lists ever drift apart the lane compares against an export that
    # was never asked for the section under test, so the disagreement is checked
    # rather than assumed.
    def _areas(operation_name: str) -> list[str] | None:
        for operation in operations:
            if operation.get("name") != operation_name:
                continue
            arguments = [str(item) for item in operation.get("arguments", [])]
            if "/areas" not in [a.casefold() for a in arguments]:
                return None
            index = [a.casefold() for a in arguments].index("/areas")
            collected: list[str] = []
            for argument in arguments[index + 1 :]:
                if argument.startswith("/"):
                    break
                collected.append(argument.casefold())
            return sorted(collected)
        return None

    import_areas = _areas("import")
    export_areas = _areas("export")
    return bool(import_areas) and import_areas == export_areas


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    # The candidate this controller built. Required, not optional: it is the
    # verdict's yardstick. Reading expected.json and candidate.inf out of the
    # pulled run directory meant grading the guest against artifacts the guest
    # itself returned, so a stale or swapped copy on the guest produced a
    # self-consistent triple that no check could distinguish from a correct one.
    parser.add_argument("--candidate-root", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    # Which transport carried the run. Recorded in the verdict so a reviewer can
    # tell which qualified environment produced it, and it selects the harness
    # file set the provenance check expects. Only one transport remains; the
    # argument stays because the recorded value is what distinguishes these
    # verdicts from the ones the retired SSH path produced.
    parser.add_argument(
        "--transport", choices=sorted(TRANSPORT_DEPLOYED_FILES), default="psdirect"
    )
    parser.add_argument(
        "--no-tag",
        action="store_true",
        help=(
            "do not create the evidence/<run-id> tag for a passing run. The tag "
            "preserves the source commit that squash-merging would otherwise "
            "orphan (issue #22); skip it only when tagging is handled elsewhere."
        ),
    )
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    repo_root = args.repo_root.resolve()
    candidate_root = args.candidate_root.resolve()

    result = json.loads((run_dir / "result.json").read_text(encoding="utf-8-sig"))
    expected = json.loads(
        (candidate_root / "expected.json").read_text(encoding="utf-8")
    )
    checks: dict[str, bool] = {
        "validate_succeeded": result["validate_exit_code"] == 0,
        "import_succeeded": result["import_exit_code"] == 0,
        "export_succeeded": result["export_exit_code"] == 0,
        "export_created": result["export_created"] is True,
        "cleanup_succeeded": result["cleanup_succeeded"] is True,
        "database_absent_after_cleanup": (
            result["database_absent_after_cleanup"] is True
        ),
        "database_residual_files_empty": result["database_residual_files"] == [],
        "observed_secedit_operations_match": _observed_operations_match(result),
        "expected_schema_supported": expected.get("schema_version") == 1,
    }

    # The profile comes from FROZEN_ENVIRONMENT, per environment-spec rule 7.
    # The copy that used to live here had drifted from it in two ways that both
    # refuse correct runs: an exact PowerShell servicing revision (so every
    # Patch Tuesday invalidates valid evidence) and an lgpo_sha256 gate (for a
    # binary this harness only hashes and never executes -- the 2026-07-29
    # re-freeze removed that, here it survived).
    environment_violations = list(lane_environment_violations(result["environment"]))
    checks["environment_matches_frozen_spec"] = not environment_violations

    candidate_differences: list[dict[str, str | None]] = []
    export_differences: list[dict[str, str | None]] = []
    decode_error: str | None = None
    checks["candidate_decodes"] = False
    checks["candidate_has_required_preamble"] = False
    checks["candidate_semantics_match"] = False
    checks["candidate_authored_key_set_exact"] = False
    checks["windows_export_decodes"] = False
    checks["windows_export_semantics_match"] = False
    try:
        # The controller's copy: "Studio authored this template with these
        # semantics" is a claim about the artifact this repository produced.
        # Only exported.inf has to come from the guest -- it is Windows' output.
        candidate = parse_security_template(
            decode_security_template((candidate_root / "candidate.inf").read_bytes())
        )
        checks["candidate_decodes"] = True
        checks["candidate_has_required_preamble"] = (
            candidate.get_value("Unicode", "Unicode") == "yes"
            and candidate.get_value("Version", "signature") == '"$CHICAGO$"'
            and candidate.get_value("Version", "Revision") == "1"
        )
        (
            checks["candidate_semantics_match"],
            candidate_differences,
        ) = _matches_expected(candidate, expected["settings"])
        checks["candidate_authored_key_set_exact"] = _candidate_key_set_matches(
            candidate,
            expected["settings"],
        )

        exported = parse_security_template(
            decode_security_template((run_dir / "exported.inf").read_bytes())
        )
        checks["windows_export_decodes"] = True
        (
            checks["windows_export_semantics_match"],
            export_differences,
        ) = _matches_expected(exported, expected["settings"])
    except (KeyError, OSError, SecurityTemplateError, TypeError, ValueError) as error:
        decode_error = str(error)

    deployed_map = {
        name: repo_root / source
        for name, source in TRANSPORT_DEPLOYED_FILES[args.transport].items()
    }
    local_map = {
        name: repo_root / source
        for name, source in TRANSPORT_LOCAL_FILES[args.transport].items()
    }
    source_hashes: dict[str, str] = {}
    deployed_harness_ok = True
    local_evidence_copies_ok = True
    for name, source_path in deployed_map.items():
        source_hash = _sha256(source_path)
        source_hashes[name] = source_hash
        evidence_path = run_dir / "deployed" / name
        if not evidence_path.is_file() or _sha256(evidence_path) != source_hash:
            deployed_harness_ok = False
    for name, source_path in local_map.items():
        source_hash = _sha256(source_path)
        source_hashes[name] = source_hash
        evidence_path = run_dir / name
        if not evidence_path.is_file() or _sha256(evidence_path) != source_hash:
            local_evidence_copies_ok = False
    checks["deployed_harness_matches_source"] = deployed_harness_ok

    # The guest ran against the candidate this controller built, byte for byte.
    # Without this, binding the yardstick to the controller copy would prove the
    # verdict was graded correctly while saying nothing about what was graded.
    # Recorded here rather than in `source.files`: every entry in that block is
    # a repository file resolvable at `source.commit`, and the candidate is a
    # generated artifact that exists at no commit. Filing it there invited a
    # reviewer to resolve it against the tree and conclude the verdict was
    # malformed -- which one did.
    candidate_delivery: dict[str, dict[str, object]] = {}
    for name in ("candidate.inf", "expected.json"):
        controller_copy = candidate_root / name
        guest_copy = run_dir / name
        intact = (
            guest_copy.is_file()
            and controller_copy.is_file()
            and _sha256(guest_copy) == _sha256(controller_copy)
        )
        candidate_delivery[name] = {
            "controller_sha256": (
                _sha256(controller_copy) if controller_copy.is_file() else None
            ),
            "guest_copy_matches": intact,
        }
    checks["candidate_delivered_intact"] = all(
        bool(entry["guest_copy_matches"]) for entry in candidate_delivery.values()
    )
    # These files did not round-trip through Windows. This check detects
    # evidence-pack corruption; it is not independent execution provenance.
    checks["local_evidence_copies_match_source"] = local_evidence_copies_ok

    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    dirty = bool(
        subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    )
    checks["source_tree_clean"] = not dirty

    verdict = {
        "schema_version": 1,
        "run_id": result["run_id"],
        "passed": all(checks.values()),
        "checks": checks,
        "candidate_differences": candidate_differences,
        "export_differences": export_differences,
        "decode_error": decode_error,
        "harness_error": result["error"],
        "transport": args.transport,
        "candidate_delivery": candidate_delivery,
        "environment": result["environment"],
        "environment_violations": environment_violations,
        "source": {"commit": commit, "dirty": dirty, "files": source_hashes},
        "artifacts": {
            str(path.relative_to(run_dir)): _sha256(path)
            for path in sorted(run_dir.rglob("*"))
            if path.is_file() and path.name != "verification.json"
        },
    }
    # Bind before recording: a run that cannot be tagged must not leave a
    # durable "passed" file claiming a binding it does not have. See the same
    # comment in finalize_wp1b_run.py for the two ways that happens.
    tag_outcome: str | None = None
    if verdict["passed"] and not args.no_tag:
        try:
            tag_outcome = tag_evidence_commit(repo_root, result["run_id"], commit)
        except OracleEvidenceError as exc:
            print(f"evidence tag failed: {exc}", file=sys.stderr)
            return 1

    (run_dir / "verification.json").write_text(
        json.dumps(verdict, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(verdict, indent=2, sort_keys=True))

    # Preserve the commit this certification binds to (issue #22). Only WP-0's
    # finalizer tagged, which is how WP-0's and WP-2's own bindings were lost --
    # see docs/evidence-binding-audit-2026-08-03.md.
    if tag_outcome is not None:
        print(f"EVIDENCE_TAG={tag_outcome}")
    return 0 if verdict["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
