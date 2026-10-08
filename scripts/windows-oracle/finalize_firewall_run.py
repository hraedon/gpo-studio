#!/usr/bin/env python3
"""Grade the firewall two-leg lane; missing evidence always fails closed."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import struct
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import asdict
from pathlib import Path
from typing import Any
from uuid import UUID

from gpo_studio.firewall_policy import from_registry_records
from gpo_studio.oracle_evidence import (
    OracleEvidenceError,
    assert_bound_source_bytes,
    lane_environment_violations,
    manifest_bound_source,
    tag_evidence_commit,
)
from gpo_studio.registry_pol import parse

CANDIDATE_ARCHIVE = "studio-firewall-backup.zip"
CANDIDATE_EXPECTATION = "expected.json"

#: Every file the candidate builder writes. Named rather than globbed, so a
#: consumed artifact the verdict never mentions is refused at the door instead
#: of recorded as a shorter block (WI-025's worked argument).
REQUIRED_CANDIDATE_FILES = (CANDIDATE_ARCHIVE, "authoring.json", CANDIDATE_EXPECTATION)

DEPLOYED_FILES = {"run-firewall-policy.ps1": "scripts/windows-oracle/run-firewall-policy.ps1"}
LOCAL_FILES = {
    "run-firewall-oracle.sh": "scripts/windows-oracle/run-firewall-oracle.sh",
    "finalize_firewall_run.py": "scripts/windows-oracle/finalize_firewall_run.py",
    "build-firewall-candidate.py": "scripts/plan-033/build-firewall-candidate.py",
    "psdirect.ps1": "scripts/windows-oracle/psdirect.ps1",
    "firewall_policy.py": "src/gpo_studio/firewall_policy.py",
    "publication.py": "src/gpo_studio/publication.py",
    "export.py": "src/gpo_studio/export.py",
    "canonical.py": "src/gpo_studio/canonical.py",
    "gpp.py": "src/gpo_studio/gpp.py",
    "model.py": "src/gpo_studio/model.py",
    "registry_pol.py": "src/gpo_studio/registry_pol.py",
    "validation.py": "src/gpo_studio/validation.py",
    "oracle_evidence.py": "src/gpo_studio/oracle_evidence.py",
    "xml_safety.py": "src/gpo_studio/xml_safety.py",
}


RESULT_KEYS = {
    "schema_version",
    "run_id",
    "domain",
    "read_leg",
    "write_leg",
    "operations",
    "persistent_before",
    "persistent_after",
    "cleanup_verified",
    "cleanup_remaining",
    "environment",
    "error",
}
LEG_KEYS = {
    "target_name",
    "owned_gpo_id",
    "policy_store",
    "authoring_succeeded",
    "import_succeeded",
    "rules_readback",
    "profiles_readback",
    "registry_pol_base64",
    "ad_attributes",
    "report_xml",
    "report_links_to_count",
}
NORMALIZATIONS = (
    "icmpv4-port-filter-rpc",
    "rpc-endpoint-map-spelling",
    "wired-interface-spelling",
    "authenticated-bypass-action",
    "ipv4-subnet-mask-form",
    "local-subnet-family-expansion",
)
FILTERS = ("Port", "Address", "Application", "Service", "InterfaceType", "Interface", "Security")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rows(rows: object) -> dict[str, dict[str, Any]]:
    if not isinstance(rows, list) or not rows:
        raise ValueError("readback must be a nonempty list")
    out = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("name"), str):
            raise ValueError("row lacks string name")
        if row["name"] in out:
            raise ValueError("duplicate readback identity")
        if not all(type(v) is str for v in row.values()):
            raise ValueError("readback contains a non-string/CIM value")
        out[row["name"]] = row
    return out


def _policy_dict(policy: object) -> dict[str, Any]:
    data = json.loads(json.dumps(asdict(policy)))
    data["rules"].sort(key=lambda r: r["rule_id"])
    return data


def _record_bytes(raw: bytes) -> dict[tuple[str, str], bytes]:
    """Extract native chunks, preserving data bytes and token/case spellings.

    parse validates the PReg grammar; the size field then delimits raw chunks.
    Never compare parse/serialize output in place of Windows' original bytes.
    """
    records = parse(raw)
    offset = 8
    result = {}
    for record in records:
        start = offset
        offset += 2  # '['
        for _ in range(2):  # UTF-16 key and name, NUL then ';'
            while raw[offset : offset + 2] != b"\0\0":
                offset += 2
                if offset >= len(raw):
                    raise ValueError("unterminated record")
            offset += 4
        offset += 6  # type DWORD and ';'
        size = struct.unpack_from("<I", raw, offset)[0]
        offset += 6 + size + 2  # size DWORD, ';', data, ']'
        identity = (record.key, record.value_name)
        if identity in result:
            raise ValueError("duplicate registry record")
        result[identity] = raw[start:offset]
    if offset != len(raw):
        raise ValueError("record byte extraction left trailing bytes")
    return result


def _expected_records(expected: dict[str, Any]) -> dict[tuple[str, str], bytes]:
    records = expected["registry_records"]
    if not isinstance(records, list) or not records:
        raise ValueError("missing expected registry records")
    out = {}
    for record in records:
        key = (record["key"], record["value_name"])
        if key in out:
            raise ValueError("duplicate expected registry identity")
        out[key] = base64.b64decode(record["bytes_base64"], validate=True)
    return out


def _required_operations(result: dict[str, Any], expected: dict[str, Any]) -> list[tuple]:
    sequence = [("Get-NetFirewallRule", "before", "PersistentStore", "")]
    for leg in ("read", "write"):
        data = result[leg + "_leg"]
        name, ident, store = data["target_name"], data["owned_gpo_id"], data["policy_store"]
        sequence.extend([("Get-GPO", leg, None, name), ("New-GPO", leg, None, name)])
        if leg == "read":
            sequence.extend(
                ("New-NetFirewallRule", leg, store, r["name"]) for r in expected["rules_readback"]
            )
            sequence.extend(
                ("Set-NetFirewallProfile", leg, store, p) for p in ("Domain", "Private")
            )
        else:
            sequence.append(("Import-GPO", leg, None, ident))
        sequence.append(("Get-NetFirewallRule", leg, store, ""))
        # Rule enumeration order belongs to Windows; filters checked separately.
        sequence.append(("Get-NetFirewallProfile", leg, store, ""))
        sequence.extend(
            (cmd, leg, None, ident)
            for cmd in ("Get-GPO", "Get-ADObject", "Read-RegistryPol", "Get-GPOReport")
        )
    sequence.extend(
        ("Remove-GPO", leg, None, result[leg + "_leg"]["owned_gpo_id"]) for leg in ("read", "write")
    )
    sequence.extend(
        [("Get-GPO", "cleanup", None, ""), ("Get-NetFirewallRule", "after", "PersistentStore", "")]
    )
    return sequence


def _operations_match(result: dict[str, Any], expected: dict[str, Any], run: Path) -> bool:
    try:
        operations = result["operations"]
        if not isinstance(operations, list) or not operations:
            return False
        read, write = result["read_leg"], result["write_leg"]
        if not all(
            isinstance(data, dict) and isinstance(data.get("owned_gpo_id"), str)
            for data in (read, write)
        ):
            return False
        if UUID(read["owned_gpo_id"]) == UUID(write["owned_gpo_id"]):
            return False
        for leg, data in [("read", read), ("write", write)]:
            if data["target_name"] != f"StudioFwLane-{leg}-{result['run_id']}":
                return False
            if data["policy_store"] != result["domain"] + "\\" + data["target_name"]:
                return False
        sequence = []
        filters = []
        for index, op in enumerate(operations):
            if set(op) != {"name", "leg", "policy_store", "subject", "ok", "stdout", "stderr"}:
                return False
            if op["ok"] is not True or not isinstance(op["name"], str):
                return False
            for stream in ("stdout", "stderr"):
                name = f"{index:04d}-{op['name']}.{stream}.txt"
                if op[stream] != name or not (run / "commands" / name).is_file():
                    return False
            row = (op["name"], op["leg"], op["policy_store"], op["subject"])
            (filters if op["name"].endswith("Filter") else sequence).append(row)
        wanted_filters = [
            (
                "Get-NetFirewall" + kind + "Filter",
                leg,
                result[leg + "_leg"]["policy_store"],
                r["name"],
            )
            for leg in ("read", "write")
            for r in expected["rules_readback"]
            for kind in FILTERS
        ]
        # Retain duplicates: repeating one rule's filters cannot cover a missing rule.
        return sequence == _required_operations(result, expected) and sorted(filters) == sorted(
            wanted_filters
        )
    except (KeyError, TypeError, ValueError):
        return False


def grade(
    result: dict[str, Any], expected: dict[str, Any], run: Path, repo: Path, candidate_root: Path
) -> tuple[dict[str, bool], dict[str, Any]]:
    read = result.get("read_leg")
    write = result.get("write_leg")
    read = read if isinstance(read, dict) else {}
    write = write if isinstance(write, dict) else {}
    checks = {
        "result_schema_exact": set(result) == RESULT_KEYS
        and type(result.get("schema_version")) is int
        and result["schema_version"] == 1
        and set(read) == LEG_KEYS
        and set(write) == LEG_KEYS
        and isinstance(result.get("run_id"), str)
        and bool(result["run_id"])
        and isinstance(result.get("domain"), str)
        and bool(result["domain"])
        and all(
            all(
                isinstance(leg.get(field), str) and bool(leg[field])
                for field in (
                    "target_name",
                    "owned_gpo_id",
                    "policy_store",
                    "registry_pol_base64",
                    "report_xml",
                )
            )
            and isinstance(leg.get("ad_attributes"), dict)
            and set(leg["ad_attributes"])
            == {"gPCMachineExtensionNames", "gPCUserExtensionNames", "gPCFileSysPath"}
            and all(type(v) is str for v in leg["ad_attributes"].values())
            and all(
                type(leg.get(field)) is bool
                for field in ("authoring_succeeded", "import_succeeded")
            )
            for leg in (read, write)
        ),
        "authoring_succeeded": read.get("authoring_succeeded") is True,
        "import_succeeded": write.get("import_succeeded") is True,
        "gpos_never_linked": all(
            type(leg.get("report_links_to_count")) is int and leg["report_links_to_count"] == 0
            for leg in (read, write)
        ),
        "persistent_store_untouched": result.get("persistent_before") == []
        and result.get("persistent_after") == [],
        "cleanup_verified": result.get("cleanup_verified") is True
        and result.get("cleanup_remaining") == [],
        "harness_reported_no_error": "error" in result and result["error"] is None,
        "operations_match_scoped_contract": _operations_match(result, expected, run),
        "builder_stdout_present": (run / "builder.stdout.txt").is_file(),
    }
    env = result.get("environment")
    violations = (
        list(lane_environment_violations(env))
        if isinstance(env, dict)
        else ["environment missing or not an object"]
    )
    checks["environment_matches_frozen_spec"] = not violations
    checks["member_server_host_role"] = (
        isinstance(env, dict)
        and type(env.get("computer_system_domain_role")) is int
        and env["computer_system_domain_role"] == 3
        and str(env.get("computer_system_name", "")).casefold() == "labms01"
    )
    comparison: dict[str, Any] = {
        "environment_violations": violations,
        "errors": {},
        "extension_observations": {},
    }
    for name in (
        "windows_registry_pol_parses_with_zero_unrecognised",
        "read_parsed_policy_equals_authored_policy",
        "read_registry_records_equal_codec_emission",
    ):
        checks[name] = False
    try:
        raw = base64.b64decode(read["registry_pol_base64"], validate=True)
        parsed = from_registry_records(parse(raw))
        checks["windows_registry_pol_parses_with_zero_unrecognised"] = (
            not parsed.unrecognised_records
            and all(not r.unknown_tokens for r in parsed.policy.rules)
        )
        checks["read_parsed_policy_equals_authored_policy"] = (
            _policy_dict(parsed.policy) == expected["policy"]
        )
        checks["read_registry_records_equal_codec_emission"] = _record_bytes(
            raw
        ) == _expected_records(expected)
    except (KeyError, TypeError, ValueError, struct.error) as exc:
        comparison["errors"]["registry"] = str(exc)
    for prefix, leg in (("read", read), ("write", write)):
        checks[prefix + "_cmdlet_readback_matches_expected"] = False
        if prefix == "write":
            checks["write_every_rule_id_present"] = False
            checks["write_no_extra_rules"] = False
        for name in NORMALIZATIONS:
            checks[prefix + "_normalization_" + name] = False
        try:
            rules = _rows(leg["rules_readback"])
            profiles = _rows(leg["profiles_readback"])
            wanted = _rows(expected["rules_readback"])
            checks[prefix + "_cmdlet_readback_matches_expected"] = (
                rules == wanted and profiles == _rows(expected["profiles_readback"])
            )
            if prefix == "write":
                checks["write_every_rule_id_present"] = set(wanted) <= set(rules)
                checks["write_no_extra_rules"] = set(rules) <= set(wanted)
            norms = expected["normalizations"]
            if [n["name"] for n in norms] != list(NORMALIZATIONS):
                raise ValueError("named normalization contract missing or changed")
            for norm in norms:
                fields = norm["readback"]
                checks[prefix + "_normalization_" + norm["name"]] = bool(fields) and all(
                    rules.get(norm["rule_id"], {}).get(k) == v for k, v in fields.items()
                )
        except (KeyError, TypeError, ValueError) as exc:
            comparison["errors"][prefix] = str(exc)
        try:
            report = ET.fromstring(leg["report_xml"])
            links = [
                node
                for node in report.iter()
                if node.tag.rsplit("}", 1)[-1] == "LinksTo" and len(node)
            ]
            checks["gpos_never_linked"] &= not links
            extensions = [
                node
                for node in report.iter()
                if node.tag.rsplit("}", 1)[-1] == "Extension"
                and (
                    "Firewall" in node.tag or any("Firewall" in child.tag for child in node.iter())
                )
            ]
            attributes = leg["ad_attributes"]
            if not isinstance(attributes, dict):
                raise ValueError("missing AD attributes")
            if not isinstance(attributes.get("gPCMachineExtensionNames"), str):
                raise ValueError("missing extension metadata")
            comparison["extension_observations"][prefix] = {
                "gPCMachineExtensionNames": attributes["gPCMachineExtensionNames"],
                "gpmc_firewall_extension_rendered": bool(extensions),
            }
        except (KeyError, TypeError, ValueError, ET.ParseError) as exc:
            checks["gpos_never_linked"] = False
            comparison["errors"][prefix + "_report"] = str(exc)
    checks["deployed_harness_matches_source"] = all(
        (run / "deployed" / name).is_file()
        and _sha(run / "deployed" / name) == _sha(repo / relative)
        for name, relative in DEPLOYED_FILES.items()
    )
    checks["candidate_delivered_intact"] = all(
        (run / guest).is_file()
        and (candidate_root / local).is_file()
        and _sha(run / guest) == _sha(candidate_root / local)
        for local, guest in (
            (CANDIDATE_ARCHIVE, "candidate.zip"),
            ("authoring.json", "authoring.json"),
        )
    )
    return checks, comparison


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--candidate-root", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--no-tag", action="store_true")
    args = parser.parse_args()
    run, candidate_root, repo = (
        p.resolve() for p in (args.run_dir, args.candidate_root, args.repo_root)
    )
    try:
        assert_bound_source_bytes(repo, {**DEPLOYED_FILES, **LOCAL_FILES}.values())
        missing = [
            name for name in REQUIRED_CANDIDATE_FILES if not (candidate_root / name).is_file()
        ]
        if missing:
            raise ValueError("candidate root is missing " + ", ".join(missing))
        result = json.loads((run / "result.json").read_text(encoding="utf-8-sig"))
        expected = json.loads((candidate_root / CANDIDATE_EXPECTATION).read_text())
        if not isinstance(result, dict) or not isinstance(expected, dict):
            raise ValueError("result and expectation must be objects")
        checks, comparison = grade(result, expected, run, repo, candidate_root)
    except (OracleEvidenceError, OSError, TypeError, ValueError) as exc:
        print(f"finalize refused: {exc}", file=sys.stderr)
        return 1
    bound_local = manifest_bound_source(repo, LOCAL_FILES)
    hashes = {name: entry["sha256"] for name, entry in bound_local.items()}
    hashes.update({name: _sha(repo / path) for name, path in DEPLOYED_FILES.items()})
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    dirty = bool(
        subprocess.run(
            ["git", "status", "--porcelain"], cwd=repo, check=True, capture_output=True, text=True
        ).stdout
    )
    checks["source_tree_clean"] = not dirty
    verdict = {
        "schema_version": 2,
        "run_id": result.get("run_id"),
        "transport": "psdirect",
        "passed": all(checks.values()),
        "checks": checks,
        "comparison": comparison,
        "harness_error": result.get("error"),
        "environment": result.get("environment"),
        "environment_violations": comparison["environment_violations"],
        "candidate": {
            p.relative_to(candidate_root).as_posix(): _sha(p)
            for p in sorted(candidate_root.rglob("*"))
            if p.is_file()
        },
        "source": {
            "commit": commit,
            "dirty": dirty,
            "files": hashes,
            "paths": {**DEPLOYED_FILES, **LOCAL_FILES},
            "banked_copies": sorted(DEPLOYED_FILES),
        },
        "artifacts": {
            p.relative_to(run).as_posix(): _sha(p)
            for p in sorted(run.rglob("*"))
            if p.is_file() and p.name != "verification.json"
        },
    }
    tag = None
    if verdict["passed"] and not args.no_tag:
        try:
            tag = tag_evidence_commit(repo, result["run_id"], commit)
        except OracleEvidenceError as exc:
            print(f"evidence tag failed: {exc}", file=sys.stderr)
            return 1
    (run / "verification.json").write_text(json.dumps(verdict, indent=2, sort_keys=True) + "\n")
    print(json.dumps(verdict, indent=2, sort_keys=True))
    if tag is not None:
        print(f"EVIDENCE_TAG={tag}")
    return 0 if verdict["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
