from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from gpo_studio.gpp import (
    GppScope,
    ensure_editor_ids,
    gpp_collection_from_dict,
    gpp_collection_to_dict,
    mark_edited,
    parse_gpp_collection,
    serialize_gpp,
)

FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "native-gpp-gpmc"

GPP_FIXTURES: list[tuple[str, str, GppScope]] = [
    ("WI01A-DriveMaps-GPMC", "User/Preferences/Drives/Drives.xml", "user"),
    ("WI01A-LocalGroups-GPMC", "Machine/Preferences/Groups/Groups.xml", "computer"),
    ("WI01A-LocalGroups-GPMC", "User/Preferences/Groups/Groups.xml", "user"),
    (
        "WI01A-MixedCSE-GPMC",
        "Machine/Preferences/ScheduledTasks/ScheduledTasks.xml",
        "computer",
    ),
    (
        "WI01A-MixedCSE-GPMC",
        "User/Preferences/Drives/Drives.xml",
        "user",
    ),
    (
        "WI01A-MixedCSE-GPMC",
        "Machine/Preferences/Groups/Groups.xml",
        "computer",
    ),
    (
        "WI01A-SchedTasks-GPMC",
        "Machine/Preferences/ScheduledTasks/ScheduledTasks.xml",
        "computer",
    ),
    (
        "WI01A-SchedTasks-GPMC",
        "User/Preferences/ScheduledTasks/ScheduledTasks.xml",
        "user",
    ),
]


def _read_fixture(fixture_id: str, relative: str) -> bytes:
    base = FIXTURE_ROOT / fixture_id
    matches = list(base.glob(f"*/DomainSysvol/GPO/{relative}"))
    assert len(matches) == 1, f"Expected exactly one match for {relative} in {base}"
    return matches[0].read_bytes()


@pytest.mark.parametrize(
    ("fixture_id", "relative", "scope"),
    GPP_FIXTURES,
    ids=[f"{f[0]}/{f[1]}" for f in GPP_FIXTURES],
)
def test_no_edit_roundtrip_preserves_bytes(
    fixture_id: str, relative: str, scope: GppScope
) -> None:
    original = _read_fixture(fixture_id, relative)
    filename = relative.split("Preferences/", 1)[1]
    files = {filename: original}
    collection = parse_gpp_collection(scope, files)
    assert collection.source_files
    result = serialize_gpp(collection)
    assert result == files


def test_edited_collection_serializes_from_model() -> None:
    original = _read_fixture("WI01A-DriveMaps-GPMC", "User/Preferences/Drives/Drives.xml")
    files = {"Drives/Drives.xml": original}
    collection = parse_gpp_collection("user", files)
    edited = mark_edited(collection)
    assert not edited.source_files
    result = serialize_gpp(edited)
    assert "Drives/Drives.xml" in result
    root = ET.fromstring(result["Drives/Drives.xml"])
    assert root.tag == "Drives"
    drive_elements = root.findall("Drive")
    assert len(drive_elements) == len(collection.drives)


def test_unknown_attrs_preserved_on_edited_roundtrip() -> None:
    original = _read_fixture("WI01A-DriveMaps-GPMC", "User/Preferences/Drives/Drives.xml")
    files = {"Drives/Drives.xml": original}
    collection = parse_gpp_collection("user", files)
    assert collection.drives
    assert collection.drives[0].unknown_attrs
    edited = mark_edited(collection)
    result = serialize_gpp(edited)
    reparsed = parse_gpp_collection("user", result)
    for orig_drive, new_drive in zip(collection.drives, reparsed.drives, strict=True):
        assert orig_drive.unknown_attrs == new_drive.unknown_attrs


def test_unknown_children_preserved_on_edited_roundtrip() -> None:
    xml_with_unknown_child = (
        b'<?xml version="1.0" encoding="utf-8"?>\n'
        b'<Groups clsid="{3125E937-EB16-4b4c-9934-544FC6D24D26}">'
        b'<Group clsid="{6D4A79E4-529C-4481-ABD0-F5BD7EA93BA7}" name="TestGroup">'
        b'<Properties action="U" groupName="TestGroup" groupSid="S-1-5-32-544"'
        b' deleteAllUsers="0" deleteAllGroups="0" removeAccounts="0"/>'
        b"</Group>"
        b"<CustomExtension><Setting value='1'/></CustomExtension>"
        b"</Groups>"
    )
    files = {"Groups/Groups.xml": xml_with_unknown_child}
    collection = parse_gpp_collection("computer", files)
    assert collection.groups_unknown_children
    edited = mark_edited(collection)
    result = serialize_gpp(edited)
    reparsed = parse_gpp_collection("computer", result)
    assert reparsed.groups_unknown_children == collection.groups_unknown_children


def test_source_files_not_cleared_by_replace() -> None:
    original = _read_fixture("WI01A-DriveMaps-GPMC", "User/Preferences/Drives/Drives.xml")
    files = {"Drives/Drives.xml": original}
    collection = parse_gpp_collection("user", files)
    modified = replace(collection, drives=())
    assert modified.source_files == collection.source_files


def test_ensure_editor_ids_clears_source_files() -> None:
    original = _read_fixture("WI01A-DriveMaps-GPMC", "User/Preferences/Drives/Drives.xml")
    files = {"Drives/Drives.xml": original}
    collection = parse_gpp_collection("user", files)
    assert collection.source_files
    edited = ensure_editor_ids(collection)
    assert edited.source_files == ()
    assert all(d.id for d in edited.drives)


def test_source_files_not_in_dict_roundtrip() -> None:
    original = _read_fixture("WI01A-DriveMaps-GPMC", "User/Preferences/Drives/Drives.xml")
    files = {"Drives/Drives.xml": original}
    collection = parse_gpp_collection("user", files)
    assert collection.source_files
    d = gpp_collection_to_dict(collection)
    assert "source_files" not in d
    restored = gpp_collection_from_dict(d)
    assert restored.source_files == ()


def test_serialize_after_dict_roundtrip_reconstructs() -> None:
    original = _read_fixture("WI01A-DriveMaps-GPMC", "User/Preferences/Drives/Drives.xml")
    files = {"Drives/Drives.xml": original}
    collection = parse_gpp_collection("user", files)
    d = gpp_collection_to_dict(collection)
    restored = gpp_collection_from_dict(d)
    result = serialize_gpp(restored)
    assert "Drives/Drives.xml" in result
    root = ET.fromstring(result["Drives/Drives.xml"])
    assert root.tag == "Drives"
    assert len(root.findall("Drive")) == len(collection.drives)


def test_source_files_ephemeral_after_from_dict() -> None:
    d: dict[str, object] = {"scope": "computer", "groups": [], "registry": []}
    collection = gpp_collection_from_dict(d)
    assert collection.source_files == ()
    result = serialize_gpp(collection)
    assert result == {}


def test_correct_edit_pattern() -> None:
    original = _read_fixture("WI01A-DriveMaps-GPMC", "User/Preferences/Drives/Drives.xml")
    files = {"Drives/Drives.xml": original}
    collection = parse_gpp_collection("user", files)
    assert collection.source_files
    edited = mark_edited(collection)
    assert not edited.source_files
    modified = replace(edited, drives=edited.drives[:1])
    result = serialize_gpp(modified)
    assert "Drives/Drives.xml" in result
    root = ET.fromstring(result["Drives/Drives.xml"])
    assert len(root.findall("Drive")) == 1


class TestTaskXmlEditedRoundtrip:
    def test_task_xml_survives_edited_roundtrip(self) -> None:
        matches = list(
            FIXTURE_ROOT.glob(
                "WI01A-SchedTasks-GPMC/*/DomainSysvol/GPO/"
                "Machine/Preferences/ScheduledTasks/ScheduledTasks.xml"
            )
        )
        assert len(matches) == 1
        data = matches[0].read_bytes()
        collection = parse_gpp_collection(
            "computer", {"ScheduledTasks/ScheduledTasks.xml": data}
        )
        edited = mark_edited(collection)
        result = serialize_gpp(edited)
        reparsed = parse_gpp_collection("computer", result)
        assert reparsed.scheduled_tasks
        assert all(t.task_xml for t in reparsed.scheduled_tasks)
        assert reparsed.scheduled_tasks[0].element_variant == "TaskV2"
        assert "cleanmgr" in reparsed.scheduled_tasks[0].program
