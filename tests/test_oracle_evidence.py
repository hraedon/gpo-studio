"""Tests for the Plan 033 external-oracle evidence contract."""

from __future__ import annotations

import copy
import json

import pytest

from gpo_studio.oracle_evidence import (
    NORMALIZER_VERSION,
    OracleEvidenceError,
    canonical_manifest_bytes,
    canonical_manifest_hash,
    compare_xml_semantics,
    normalize_backup_relative_path,
    normalize_xml_semantics,
    parse_oracle_manifest,
)

HASH_A = "a" * 64
HASH_B = "b" * 64


def _manifest() -> dict[str, object]:
    return {
        "schema_version": 1,
        "run_id": "synthetic-run-001",
        "started_at": "2026-07-25T00:00:00Z",
        "completed_at": "2026-07-25T00:05:00Z",
        "source": {"commit": "0123456789abcdef", "dirty": True},
        "fixture": {
            "fixture_id": "synthetic-gpp-drive-001",
            "generation_recipe": "fixtures/recipes/gpp-drive.json",
        },
        "environment": {
            "server_build": "synthetic-server-build",
            "client_build": "synthetic-client-build",
            "powershell_edition": "Desktop",
            "powershell_version": "5.1.synthetic",
            "group_policy_module_version": "synthetic-module-version",
            "gpmc_version": "synthetic-gpmc-version",
            "locale": "en-US",
            "lgpo_sha256": HASH_A,
        },
        "tools": [
            {"name": "GPMC", "version": "synthetic-gpmc-version", "sha256": None},
            {"name": "LGPO.exe", "version": "synthetic-lgpo-version", "sha256": HASH_A},
        ],
        "artifacts": [
            {
                "artifact_id": "input-xml",
                "role": "input",
                "relative_path": "artifacts/input.xml",
                "sha256": HASH_A,
                "size_bytes": 42,
            },
            {
                "artifact_id": "output-xml",
                "role": "output",
                "relative_path": "artifacts/output.xml",
                "sha256": HASH_B,
                "size_bytes": 43,
            },
        ],
        "commands": [
            {
                "command_id": "gpmc-import",
                "command_line": "Import-GPO -BackupId {SYNTHETIC}",
                "exit_code": 0,
                "stdout_sha256": HASH_A,
                "stderr_sha256": None,
                "relevant_event_ids": [4016],
            }
        ],
        "comparisons": [
            {
                "assertion_id": "drive-item-semantics",
                "oracle": "GPMC Backup-GPO re-export",
                "boundary_owner": "gpo-backup-content",
                "normalizer_version": NORMALIZER_VERSION,
                "expected_artifact_id": "input-xml",
                "observed_artifact_id": "input-xml",
                "expected_sha256": HASH_A,
                "observed_sha256": HASH_A,
                "equal": True,
                "differences": [],
            }
        ],
        "cleanup": {
            "attempted": True,
            "succeeded": True,
            "state_restored": True,
            "removed_resources": ["synthetic-gpo"],
            "failures": [],
        },
        "capability": {
            "matrix_row": "gpp.drive.writer",
            "evidence_state": "pass",
        },
    }


def test_parse_manifest_and_canonical_hash_are_stable() -> None:
    manifest = parse_oracle_manifest(_manifest())
    assert manifest.comparisons[0].boundary_owner == "gpo-backup-content"
    assert canonical_manifest_hash(manifest) == canonical_manifest_hash(manifest)
    assert json.loads(canonical_manifest_bytes(manifest))["run_id"] == "synthetic-run-001"


def test_manifest_rejects_unknown_fields() -> None:
    raw = _manifest()
    raw["unreviewed"] = True
    with pytest.raises(OracleEvidenceError, match="unknown keys"):
        parse_oracle_manifest(raw)


def test_manifest_requires_one_oracle_and_boundary_per_assertion() -> None:
    raw = _manifest()
    comparisons = raw["comparisons"]
    assert isinstance(comparisons, list)
    comparison = comparisons[0]
    assert isinstance(comparison, dict)
    comparison["oracle"] = ""
    with pytest.raises(OracleEvidenceError, match="oracle"):
        parse_oracle_manifest(raw)


def test_manifest_rejects_equal_comparison_with_differences() -> None:
    raw = _manifest()
    comparisons = raw["comparisons"]
    assert isinstance(comparisons, list)
    comparison = comparisons[0]
    assert isinstance(comparison, dict)
    comparison["differences"] = ["typed value changed"]
    with pytest.raises(OracleEvidenceError, match="equal"):
        parse_oracle_manifest(raw)


def test_manifest_rejects_partial_cleanup_claimed_as_success() -> None:
    raw = _manifest()
    cleanup = raw["cleanup"]
    assert isinstance(cleanup, dict)
    cleanup["failures"] = ["OU remained"]
    with pytest.raises(OracleEvidenceError, match="cleanup cannot succeed"):
        parse_oracle_manifest(raw)


def test_manifest_cannot_pass_with_failed_command_or_comparison() -> None:
    failed_command = _manifest()
    commands = failed_command["commands"]
    assert isinstance(commands, list)
    command = commands[0]
    assert isinstance(command, dict)
    command["exit_code"] = 1
    with pytest.raises(OracleEvidenceError, match="failed command"):
        parse_oracle_manifest(failed_command)

    failed_comparison = _manifest()
    comparisons = failed_comparison["comparisons"]
    assert isinstance(comparisons, list)
    comparison = comparisons[0]
    assert isinstance(comparison, dict)
    comparison["equal"] = False
    comparison["differences"] = ["action differs"]
    with pytest.raises(OracleEvidenceError, match="failed comparison"):
        parse_oracle_manifest(failed_comparison)


def test_manifest_cannot_pass_without_state_restore() -> None:
    raw = _manifest()
    cleanup = raw["cleanup"]
    assert isinstance(cleanup, dict)
    cleanup["state_restored"] = False
    with pytest.raises(OracleEvidenceError, match="state restore"):
        parse_oracle_manifest(raw)


def test_manifest_rejects_incomplete_artifact_roles() -> None:
    raw = _manifest()
    artifacts = raw["artifacts"]
    assert isinstance(artifacts, list)
    artifacts.pop()
    with pytest.raises(OracleEvidenceError, match="input and one output"):
        parse_oracle_manifest(raw)


def test_manifest_rejects_reversed_or_timezone_free_timestamps() -> None:
    raw = _manifest()
    raw["completed_at"] = "2026-07-24T23:00:00Z"
    with pytest.raises(OracleEvidenceError, match="cannot precede"):
        parse_oracle_manifest(raw)

    raw = _manifest()
    raw["started_at"] = "2026-07-25T00:00:00"
    with pytest.raises(OracleEvidenceError, match="timezone"):
        parse_oracle_manifest(raw)


def test_manifest_rejects_non_sha256_hash() -> None:
    raw = _manifest()
    environment = raw["environment"]
    assert isinstance(environment, dict)
    environment["lgpo_sha256"] = "sha256:not-a-hash"
    with pytest.raises(OracleEvidenceError, match="SHA-256"):
        parse_oracle_manifest(raw)


def test_normalizer_accepts_attribute_order_whitespace_and_defaults() -> None:
    expected = """
      <Drives>
        <Drive uid="{11111111-1111-1111-1111-111111111111}" changed="first">
          <Properties action="U" path="C:\\Data" />
        </Drive>
      </Drives>
    """
    observed = """
      <Drives><Drive bypassErrors="0" disabled="0" removePolicy="0"
        changed="second" uid="{22222222-2222-2222-2222-222222222222}">
        <Properties path="c:\\data" action="U"></Properties>
      </Drive></Drives>
    """
    result = compare_xml_semantics(expected, observed)
    assert result.equal is True
    assert result.differences == ()


@pytest.mark.parametrize(
    ("changed_fragment", "label"),
    [
        ('action="D"', "action"),
        ('value="2"', "typed value"),
        ('<FilterOrgUnit name="different" />', "filter"),
        ('bkp:ID="{22222222-2222-2222-2222-222222222222}"', "extension GUID"),
        ("<Unknown>different</Unknown>", "unknown element"),
        ('targetPath="C:\\Other"', "file path"),
    ],
)
def test_normalizer_never_hides_semantic_differences(
    changed_fragment: str, label: str
) -> None:
    namespace = 'xmlns:bkp="urn:synthetic-backup"'
    base_fragment = {
        "action": 'action="U"',
        "typed value": 'value="1"',
        "filter": '<FilterOrgUnit name="original" />',
        "extension GUID": 'bkp:ID="{11111111-1111-1111-1111-111111111111}"',
        "unknown element": "<Unknown>original</Unknown>",
        "file path": 'targetPath="C:\\Data"',
    }[label]
    if base_fragment.startswith("<"):
        expected = (
            f"<Root {namespace}><File><Properties />{base_fragment}</File></Root>"
        )
        observed = (
            f"<Root {namespace}><File><Properties />{changed_fragment}</File></Root>"
        )
    else:
        expected = (
            f"<Root {namespace}><File {base_fragment}><Properties /></File></Root>"
        )
        observed = (
            f"<Root {namespace}><File {changed_fragment}><Properties /></File></Root>"
        )
    result = compare_xml_semantics(expected, observed)
    assert result.equal is False
    assert result.differences
    assert result.expected_sha256 != result.observed_sha256


def test_filter_run_once_generated_id_is_normalized_but_filter_is_preserved() -> None:
    expected = (
        '<Drive><FilterRunOnce id="{11111111-1111-1111-1111-111111111111}" '
        'bool="AND" hidden="1" not="0" /></Drive>'
    )
    observed = (
        '<Drive><FilterRunOnce id="{22222222-2222-2222-2222-222222222222}" '
        'bool="AND" hidden="1" not="0" /></Drive>'
    )
    changed_filter = observed.replace('bool="AND"', 'bool="OR"')
    assert compare_xml_semantics(expected, observed).equal is True
    assert compare_xml_semantics(expected, changed_filter).equal is False


def test_backup_metadata_normalizes_only_explicit_generated_fields() -> None:
    namespace = "http://www.microsoft.com/GroupPolicy/GPOOperations/Manifest"
    expected = (
        f'<BackupInst xmlns="{namespace}">'
        "<GPOGuid>{AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA}</GPOGuid>"
        "<BackupTime>first</BackupTime>"
        "<ID>{11111111-1111-1111-1111-111111111111}</ID>"
        "</BackupInst>"
    )
    observed = (
        f'<BackupInst xmlns="{namespace}">'
        "<GPOGuid>{AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA}</GPOGuid>"
        "<BackupTime>second</BackupTime>"
        "<ID>{22222222-2222-2222-2222-222222222222}</ID>"
        "</BackupInst>"
    )
    changed_gpo_id = observed.replace("AAAAAAAA", "BBBBBBBB")
    assert compare_xml_semantics(expected, observed).equal is True
    assert compare_xml_semantics(expected, changed_gpo_id).equal is False


def test_backup_path_normalizer_preserves_gpo_id_and_content_path() -> None:
    backup_id = "{11111111-1111-1111-1111-111111111111}"
    gpo_id = "{22222222-2222-2222-2222-222222222222}"
    path = f"{backup_id}\\DomainSysvol\\GPO\\{gpo_id}\\Machine\\Registry.pol"
    normalized = normalize_backup_relative_path(path, backup_id)
    assert normalized.startswith("{NORMALIZED-BACKUP-ID}")
    assert gpo_id in normalized
    assert normalized.endswith("Machine\\Registry.pol")


def test_backup_path_rejects_ambiguous_backup_id() -> None:
    backup_id = "{11111111-1111-1111-1111-111111111111}"
    with pytest.raises(OracleEvidenceError, match="exactly once"):
        normalize_backup_relative_path(f"{backup_id}\\{backup_id}\\Backup.xml", backup_id)


def test_normalizer_rejects_doctype() -> None:
    with pytest.raises(OracleEvidenceError, match="DTD"):
        normalize_xml_semantics("<!DOCTYPE Root><Root />")


def test_normalizer_hash_changes_when_unknown_attribute_changes() -> None:
    first = normalize_xml_semantics('<Root vendorExtension="one" />')
    second = normalize_xml_semantics('<Root vendorExtension="two" />')
    assert first.sha256() != second.sha256()


def test_manifest_does_not_mutate_input() -> None:
    raw = _manifest()
    before = copy.deepcopy(raw)
    parse_oracle_manifest(raw)
    assert raw == before
