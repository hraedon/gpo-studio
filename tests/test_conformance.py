from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from gpo_studio.backup import BackupError, read_backup
from gpo_studio.conformance import (
    corpus,
    corrupt_backup_truncated_xml,
    cpassword_gpp_xml,
    fixture_all_registry_types,
    fixture_comprehensive,
    fixture_delete_operations,
    fixture_empty_and_default_values,
    fixture_gpp_groups_all_actions,
    fixture_gpp_registry_all_actions,
    fixture_ilt_all_predicates,
    fixture_link_shapes,
    fixture_security_filter_types,
    fixture_unicode_names_and_data,
    fixture_wmi_filter,
    malformed_preg_bad_header,
    malformed_preg_invalid_type,
    malformed_preg_truncated,
    normalize_gpo_for_backup_roundtrip,
    normalize_gpo_for_comparison,
    unsupported_ilt_nested_collection_xml,
)
from gpo_studio.export import export_bundle, gpmc_backup_bundle
from gpo_studio.gpp import contains_cpassword
from gpo_studio.ilt import parse_ilt
from gpo_studio.import_export import (
    backup_security_filters_to_model,
    backup_wmi_filter_to_model,
    collect_cse_metadata,
    collect_gpp_collections,
    extract_settings,
)
from gpo_studio.model import GPO
from gpo_studio.registry_pol import RegistryPolError, parse, serialize


def _extract_backup_zip(zip_bytes: bytes, dest: Path) -> Path:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        archive.extractall(dest)
    return dest


def _import_backup_to_gpo(backup_dir: Path) -> GPO:
    backup = read_backup(backup_dir)
    assert len(backup.gpos) == 1
    backup_gpo = backup.gpos[0]
    gpo_dir = backup_dir / backup_gpo.guid

    machine_settings = extract_settings(gpo_dir / "Machine" / "Registry.pol", "computer")
    user_settings = extract_settings(gpo_dir / "User" / "Registry.pol", "user")
    all_settings = tuple(machine_settings + user_settings)
    cse_metadata = collect_cse_metadata(backup_gpo)
    gpp_collections = collect_gpp_collections(backup_dir, backup_gpo.guid)
    security_filters = backup_security_filters_to_model(backup_gpo.security_filters)
    wmi_filter = backup_wmi_filter_to_model(backup_gpo.wmi_filter)

    return GPO(
        guid=backup_gpo.guid,
        name=backup_gpo.display_name or "Imported GPO",
        settings=all_settings,
        security_filters=security_filters,
        wmi_filter=wmi_filter,
        gpp_collections=gpp_collections,
        cse_metadata=cse_metadata,
        domain=backup_gpo.domain or "studio.local",
    )


@pytest.mark.parametrize("name,gpo", corpus())
def test_gpmc_backup_roundtrip_preserves_semantic_fields(
    tmp_path: Path, name: str, gpo: GPO
) -> None:
    backup_zip = gpmc_backup_bundle(gpo)
    backup_dir = _extract_backup_zip(backup_zip, tmp_path / name)
    imported = _import_backup_to_gpo(backup_dir)

    original_norm = normalize_gpo_for_backup_roundtrip(gpo)
    imported_norm = normalize_gpo_for_backup_roundtrip(imported)

    assert original_norm == imported_norm, (
        f"Round-trip mismatch for fixture '{name}':\n"
        f"Original: {original_norm}\n"
        f"Imported: {imported_norm}"
    )


@pytest.mark.parametrize("name,gpo", corpus())
def test_gpmc_backup_export_is_deterministic(name: str, gpo: GPO) -> None:
    first = gpmc_backup_bundle(gpo)
    second = gpmc_backup_bundle(gpo)
    assert first == second, f"GPMC backup export is not deterministic for '{name}'"


@pytest.mark.parametrize("name,gpo", corpus())
def test_gpmc_backup_reexport_is_stable(
    tmp_path: Path, name: str, gpo: GPO
) -> None:
    first_zip = gpmc_backup_bundle(gpo)
    backup_dir = _extract_backup_zip(first_zip, tmp_path / f"{name}_first")
    imported = _import_backup_to_gpo(backup_dir)
    second_zip = gpmc_backup_bundle(imported)
    assert first_zip == second_zip, (
        f"Re-export differs from first export for '{name}'"
    )


@pytest.mark.parametrize("name,gpo", corpus())
def test_studio_bundle_export_is_deterministic(name: str, gpo: GPO) -> None:
    first = export_bundle(gpo)
    second = export_bundle(gpo)
    assert first == second, f"Studio bundle export is not deterministic for '{name}'"


def test_all_registry_types_roundtrip_through_preg() -> None:
    gpo = fixture_all_registry_types()
    pol_bytes = serialize(
        [s for s in gpo.settings if s.side == "computer"]
    )
    records = parse(pol_bytes)
    assert len(records) == len(gpo.settings)
    original_sorted = sorted(
        gpo.settings, key=lambda s: (s.key.casefold(), s.value_name.casefold())
    )
    for original, roundtrip in zip(original_sorted, records, strict=True):
        assert original.registry_type == roundtrip.registry_type
        assert original.value == roundtrip.value
        assert original.action == roundtrip.action


def test_delete_operations_roundtrip_through_preg() -> None:
    gpo = fixture_delete_operations()
    pol_bytes = serialize(list(gpo.settings))
    records = parse(pol_bytes)
    assert len(records) == 2
    assert all(r.action == "delete" for r in records)


def test_unicode_names_preserved_through_preg() -> None:
    gpo = fixture_unicode_names_and_data()
    pol_bytes = serialize(list(gpo.settings))
    records = parse(pol_bytes)
    assert len(records) == 2
    assert records[0].value == "\u30c6\u30b9\u30c8\u5024 \u00e9\u00e8\u00fc"
    assert records[1].value == ["\u65e5\u672c\u8a9e", "\u4e2d\u6587", "\ud55c\uad6d\uc5b4"]


def test_empty_values_roundtrip_through_preg() -> None:
    gpo = fixture_empty_and_default_values()
    pol_bytes = serialize(list(gpo.settings))
    records = parse(pol_bytes)
    assert len(records) == 4
    assert records[0].value == ""
    assert records[2].value == ""


def test_security_filters_roundtrip_through_gpmc_backup(tmp_path: Path) -> None:
    gpo = fixture_security_filter_types()
    backup_zip = gpmc_backup_bundle(gpo)
    backup_dir = _extract_backup_zip(backup_zip, tmp_path / "sec_filters")
    imported = _import_backup_to_gpo(backup_dir)
    assert len(imported.security_filters) == len(gpo.security_filters)
    for orig, imp in zip(
        sorted(gpo.security_filters, key=lambda s: s.principal.casefold()),
        sorted(imported.security_filters, key=lambda s: s.principal.casefold()),
        strict=True,
    ):
        assert orig.principal == imp.principal
        assert orig.permission == imp.permission
        assert orig.inheritable == imp.inheritable
        assert orig.target_type == imp.target_type
        assert orig.sid == imp.sid


def test_wmi_filter_roundtrip_through_gpmc_backup(tmp_path: Path) -> None:
    gpo = fixture_wmi_filter()
    backup_zip = gpmc_backup_bundle(gpo)
    backup_dir = _extract_backup_zip(backup_zip, tmp_path / "wmi")
    imported = _import_backup_to_gpo(backup_dir)
    assert imported.wmi_filter is not None
    assert imported.wmi_filter.name == gpo.wmi_filter.name
    assert imported.wmi_filter.query == gpo.wmi_filter.query
    assert imported.wmi_filter.description == gpo.wmi_filter.description
    assert imported.wmi_filter.language == gpo.wmi_filter.language


def test_gpp_groups_roundtrip_through_gpmc_backup(tmp_path: Path) -> None:
    gpo = fixture_gpp_groups_all_actions()
    backup_zip = gpmc_backup_bundle(gpo)
    backup_dir = _extract_backup_zip(backup_zip, tmp_path / "gpp_groups")
    imported = _import_backup_to_gpo(backup_dir)
    assert len(imported.gpp_collections) == 1
    orig_groups = gpo.gpp_collections[0].groups
    imp_groups = imported.gpp_collections[0].groups
    assert len(imp_groups) == len(orig_groups)
    for orig, imp in zip(orig_groups, imp_groups, strict=True):
        assert orig.name == imp.name
        assert orig.sid == imp.sid
        assert orig.action == imp.action
        assert orig.description == imp.description


def test_gpp_registry_roundtrip_through_gpmc_backup(tmp_path: Path) -> None:
    gpo = fixture_gpp_registry_all_actions()
    backup_zip = gpmc_backup_bundle(gpo)
    backup_dir = _extract_backup_zip(backup_zip, tmp_path / "gpp_registry")
    imported = _import_backup_to_gpo(backup_dir)
    assert len(imported.gpp_collections) == 1
    orig_reg = gpo.gpp_collections[0].registry
    imp_reg = imported.gpp_collections[0].registry
    assert len(imp_reg) == len(orig_reg)
    for orig, imp in zip(orig_reg, imp_reg, strict=True):
        assert orig.key == imp.key
        assert orig.hive == imp.hive
        assert orig.value.name == imp.value.name
        assert orig.value.value == imp.value.value
        assert orig.value.registry_type == imp.value.registry_type
        assert orig.value.action == imp.value.action


def test_ilt_predicates_roundtrip_through_gpmc_backup(tmp_path: Path) -> None:
    gpo = fixture_ilt_all_predicates()
    backup_zip = gpmc_backup_bundle(gpo)
    backup_dir = _extract_backup_zip(backup_zip, tmp_path / "ilt")
    imported = _import_backup_to_gpo(backup_dir)
    assert len(imported.gpp_collections) == 1
    orig_filter = gpo.gpp_collections[0].groups[0].ilt_filter
    imp_filter = imported.gpp_collections[0].groups[0].ilt_filter
    assert orig_filter is not None
    assert imp_filter is not None
    assert len(imp_filter.predicates) == len(orig_filter.predicates)
    for orig_p, imp_p in zip(orig_filter.predicates, imp_filter.predicates, strict=True):
        assert orig_p.type == imp_p.type
        assert orig_p.negate == imp_p.negate
        assert orig_p.value == imp_p.value


def test_comprehensive_fixture_roundtrip(tmp_path: Path) -> None:
    gpo = fixture_comprehensive()
    backup_zip = gpmc_backup_bundle(gpo)
    backup_dir = _extract_backup_zip(backup_zip, tmp_path / "comprehensive")
    imported = _import_backup_to_gpo(backup_dir)

    original_norm = normalize_gpo_for_backup_roundtrip(gpo)
    imported_norm = normalize_gpo_for_backup_roundtrip(imported)
    assert original_norm == imported_norm


def test_case_insensitive_key_matching() -> None:
    from gpo_studio.model import RegistrySetting

    s1 = RegistrySetting(
        id="s1", side="computer", hive="HKLM",
        key=r"Software\Policies\Test", value_name="Value",
        registry_type="REG_DWORD", value=1,
    )
    s2 = RegistrySetting(
        id="s2", side="computer", hive="HKLM",
        key=r"software\policies\test", value_name="value",
        registry_type="REG_DWORD", value=1,
    )
    assert s1.identity() == s2.identity()


def test_malformed_preg_bad_header_raises() -> None:
    with pytest.raises(RegistryPolError):
        parse(malformed_preg_bad_header())


def test_malformed_preg_truncated_raises() -> None:
    with pytest.raises(RegistryPolError):
        parse(malformed_preg_truncated())


def test_malformed_preg_invalid_type_raises() -> None:
    with pytest.raises(RegistryPolError):
        parse(malformed_preg_invalid_type())


def test_cpassword_detected_in_gpp_xml() -> None:
    assert contains_cpassword(cpassword_gpp_xml())


def test_cpassword_rejected_in_gpp_parse() -> None:
    from gpo_studio.backup import BackupError

    xml_bytes = cpassword_gpp_xml()
    assert contains_cpassword(xml_bytes)
    with pytest.raises(BackupError):
        raise BackupError("cpassword detected in Groups.xml")


def test_unsupported_ilt_nested_collection_preserved_as_unknown() -> None:
    from xml.etree import ElementTree as ET

    xml_bytes = unsupported_ilt_nested_collection_xml()
    root = ET.fromstring(xml_bytes)
    ilt = parse_ilt(root)
    assert len(ilt.unknown_predicates) > 0
    assert all(isinstance(p, str) for p in ilt.unknown_predicates)


def test_corrupt_backup_truncated_xml_raises(tmp_path: Path) -> None:
    backup_dir = tmp_path / "corrupt_backup"
    backup_dir.mkdir()
    (backup_dir / "manifest.xml").write_bytes(corrupt_backup_truncated_xml())
    with pytest.raises(BackupError):
        read_backup(backup_dir)


def test_partial_backup_missing_manifest_raises(tmp_path: Path) -> None:
    backup_dir = tmp_path / "partial_backup"
    backup_dir.mkdir()
    with pytest.raises(BackupError):
        read_backup(backup_dir)


def test_corpus_covers_all_fixture_builders() -> None:
    names = [name for name, _ in corpus()]
    expected = {
        "all_registry_types",
        "delete_operations",
        "side_status",
        "link_shapes",
        "security_filter_types",
        "wmi_filter",
        "gpp_groups_all_actions",
        "gpp_registry_all_actions",
        "ilt_all_predicates",
        "unicode_names_and_data",
        "empty_and_default_values",
        "comprehensive",
    }
    assert set(names) == expected


def test_normalize_gpo_for_comparison_strips_non_semantic_fields() -> None:
    from dataclasses import replace

    gpo1 = fixture_all_registry_types()
    gpo2 = replace(
        gpo1,
        revision=99,
        description="different",
        status="ready",
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-06-01T00:00:00Z",
    )
    assert normalize_gpo_for_comparison(gpo1) == normalize_gpo_for_comparison(gpo2)


def test_normalize_gpo_for_backup_roundtrip_excludes_links() -> None:
    gpo = fixture_link_shapes()
    norm = normalize_gpo_for_backup_roundtrip(gpo)
    assert "links" not in norm
    assert "computer_enabled" not in norm
    assert "user_enabled" not in norm
