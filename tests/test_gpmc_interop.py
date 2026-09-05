from __future__ import annotations

from gpo_studio.export import EMITTED_EXTENSION_GUIDS
from gpo_studio.gpmc_interop import (
    _KNOWN_CSE_GUIDS,
    InteropIssue,
    check_backup_importable,
    check_gpmc_interop,
)
from gpo_studio.model import (
    GPO,
    CseMetadataEntry,
    GPOLink,
    RegistrySetting,
    SecurityFilter,
)


def _clean_gpo(**kwargs: object) -> GPO:
    defaults: dict[str, object] = {
        "guid": "11111111-2222-3333-4444-555555555555",
        "name": "Synthetic Workstation Policy",
        "settings": (
            RegistrySetting(
                id="s1",
                side="computer",
                hive="HKLM",
                key=r"Software\Policies\Synthetic",
                value_name="Enabled",
                registry_type="REG_DWORD",
                value=1,
            ),
        ),
        "links": (GPOLink(id="l1", target="OU=Lab,DC=example,DC=test"),),
    }
    defaults.update(kwargs)
    return GPO(**defaults)  # type: ignore[arg-type]


def test_check_gpmc_interop_clean_gpo_passes() -> None:
    gpo = _clean_gpo()
    report = check_gpmc_interop(gpo)
    assert report.is_gpmc_importable is True
    assert report.is_gpmc_editable is True
    assert not any(i.level == "error" for i in report.issues)


def test_check_gpmc_interop_invalid_security_filter_sid_is_error() -> None:
    gpo = _clean_gpo(
        security_filters=(
            SecurityFilter(
                id="sf-1",
                principal="DOMAIN\\User1",
                permission="apply",
                sid="not-a-sid",
            ),
        ),
    )
    report = check_gpmc_interop(gpo)
    assert report.is_gpmc_importable is False
    assert any(
        i.level == "error" and i.check == "security_filter_sid"
        for i in report.issues
    )


def test_check_gpmc_interop_too_many_settings_is_warning() -> None:
    settings = tuple(
        RegistrySetting(
            id=f"s{i}",
            side="computer",
            hive="HKLM",
            key=r"Software\Policies\Synthetic",
            value_name=f"Value{i}",
            registry_type="REG_DWORD",
            value=1,
        )
        for i in range(10001)
    )
    gpo = _clean_gpo(settings=settings)
    report = check_gpmc_interop(gpo)
    assert report.is_gpmc_importable is True
    assert any(
        i.level == "warning" and i.check == "settings_count"
        for i in report.issues
    )


def test_check_backup_importable_valid_manifest() -> None:
    manifest = {
        "id": "{11111111-2222-3333-4444-555555555555}",
        "name": "Backup GPO",
        "domain": "example.test",
        "timestamp": "2024-01-01T00:00:00Z",
        "xml_files": {
            "manifest.xml": b"<?xml version=\"1.0\"?><root/>",
            "bkupInfo.xml": b"<?xml version=\"1.0\"?><root/>",
        },
        "encrypted": False,
    }
    report = check_backup_importable(manifest)
    assert report.is_gpmc_importable is True
    assert not any(i.level == "error" for i in report.issues)


def test_check_backup_importable_missing_fields_are_errors() -> None:
    manifest: dict[str, object] = {"id": "{11111111-2222-3333-4444-555555555555}"}
    report = check_backup_importable(manifest)
    assert report.is_gpmc_importable is False
    assert sum(1 for i in report.issues if i.check == "missing_required_field") == 3


def test_check_backup_importable_invalid_guid_is_error() -> None:
    manifest = {
        "id": "not-a-guid",
        "name": "Backup GPO",
        "domain": "example.test",
        "timestamp": "2024-01-01T00:00:00Z",
    }
    report = check_backup_importable(manifest)
    assert report.is_gpmc_importable is False
    assert any(i.check == "backup_guid_format" for i in report.issues)


def test_interop_issue_defaults() -> None:
    issue = InteropIssue(check="test", level="pass", message="ok")
    assert issue.component == ""


def test_known_cse_guids_are_exactly_the_export_emission_vocabulary() -> None:
    # Work-order R6 (2026-09-02): the two hand-written "GPP Groups" / "GPP
    # Registry" entries are GPP XML clsids that appear in no extension list
    # anywhere (0/26 production GPOs, 0/17 GPMC-authored lab backups), so the
    # known set is now Studio's own emission vocabulary, not a hand list.
    assert _KNOWN_CSE_GUIDS == EMITTED_EXTENSION_GUIDS
    assert "{3125E937-EB16-4b4c-9934-544FC6D24D26}" not in _KNOWN_CSE_GUIDS
    assert "{A3CC7818-8A30-4e0c-91C5-A4EA4B5A8DAB}" not in _KNOWN_CSE_GUIDS


def test_gpp_extension_metadata_does_not_trip_unknown_cse_guid() -> None:
    # A GPMC-authored GPP backup parses every token of the extension lists,
    # tool halves and the zero-GUID included, into cse_metadata. None of them
    # may report the GPO unimportable.
    metadata = tuple(
        CseMetadataEntry(guid=guid, side="machine")
        for guid in sorted(EMITTED_EXTENSION_GUIDS)
    )
    report = check_gpmc_interop(_clean_gpo(cse_metadata=metadata))
    assert not any(i.check == "unknown_cse_guid" for i in report.issues)
    assert report.is_gpmc_importable is True


def test_xml_clsid_in_cse_metadata_is_still_unknown() -> None:
    # The disproved GPP clsids belong to the XML vocabulary, not to extension
    # lists; if one ever appears in cse_metadata it is genuinely unknown and
    # keeps the conservative error.
    gpo = _clean_gpo(
        cse_metadata=(
            CseMetadataEntry(
                guid="{3125E937-EB16-4b4c-9934-544FC6D24D26}",
                side="machine",
            ),
        ),
    )
    report = check_gpmc_interop(gpo)
    assert any(
        i.level == "error" and i.check == "unknown_cse_guid" for i in report.issues
    )
    assert report.is_gpmc_importable is False
