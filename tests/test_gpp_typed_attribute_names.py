"""Every attribute name the GPP writer types is one Windows writes (WI-081).

Printers' typed ``set_default``/``use_local`` were read and written as
``setDefault``/``useLocal``, which appear in no capture: GPMC writes
``default``/``skipLocal``. So a GPMC default printer read as not default, and
an authored or edited one wrote an attribute Windows has not been seen to
read. The same audit found three more names no capture contains, written on
``Properties`` by the Folders (``suppress``), Shortcuts (``name``) and
Immediate Tasks (``program``, ``arguments``, ``startIn``) writers.

The audit itself is a test here: for every committed native capture, each
attribute the writer puts on ``Properties`` (and below it, outside the filter
and the task payload) when it writes the imported model must appear on that
element in some capture. Families with no capture (Regional Options, Devices,
Folder Options, Data Sources, Network Shares, Applications, Power Schemes,
local users) cannot be audited and are not claimed.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import replace
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from gpo_studio.canonical import semantic_dict_gpp_collection
from gpo_studio.gpp import (
    GppCollection,
    ensure_editor_ids,
    gpp_collection_from_dict,
    gpp_collection_to_dict,
    model_only,
    parse_gpp_collection,
    serialize_gpp,
)
from gpo_studio.gpp_adapters import (
    GppImmediateTask,
    GppPrinter,
    parse_gpp_printers,
    serialize_gpp_immediate_tasks,
    serialize_gpp_printers,
)

ROOT = Path(__file__).resolve().parents[1]
NATIVE_FILES = sorted(
    ROOT.glob("tests/fixtures/native-gpp*/*/*/DomainSysvol/GPO/*/Preferences/*/*.xml")
)
PRINTERS = next(f for f in NATIVE_FILES if f.name == "Printers.xml")


def _names(elem: ET.Element, path: str, into: dict[str, set[str]]) -> None:
    here = f"{path}/{elem.tag}"
    into[here] |= set(elem.attrib)
    for child in elem:
        # ILT is audited by the ILT codec's own tests; the TaskV2/ImmediateTaskV2
        # payload is written verbatim.
        if child.tag not in {"Filters", "Task"}:
            _names(child, here, into)


def _scope(path: Path) -> Any:
    return "computer" if "/Machine/" in path.as_posix() else "user"


def test_the_writer_types_no_attribute_name_a_capture_lacks() -> None:
    native: dict[str, set[str]] = defaultdict(set)
    written: dict[str, set[str]] = defaultdict(set)
    for path in NATIVE_FILES:
        relative = f"{path.parent.name}/{path.name}"
        data = path.read_bytes()
        _names(ET.fromstring(data), "", native)
        alone = model_only(parse_gpp_collection(_scope(path), {relative: data}))
        for out in serialize_gpp(alone).values():
            _names(ET.fromstring(out), "", written)
    invented = {
        element: sorted(names - native[element])
        for element, names in written.items()
        if "/Properties" in element and names - native[element]
    }
    assert invented == {}
    assert len([e for e in written if e.endswith("/Properties")]) >= 12


def test_gpmc_names_the_default_printer_and_studio_reads_it() -> None:
    printers = parse_gpp_printers(PRINTERS.read_bytes())
    assert [(p.path.rsplit("\\", 1)[-1], p.set_default) for p in printers] == [
        ("Lab-Color", True),
        ("Lab-Mono", False),
        ("Old-Printer", False),
        ("Create-Printer", False),
        ("Replace-Printer", False),
    ]
    assert not any(p.skip_local for p in printers)


def test_an_authored_printer_writes_gpmcs_names_in_gpmcs_order() -> None:
    printer = GppPrinter(path=r"\\printsv\Authored", set_default=True, skip_local=True)
    props = ET.fromstring(serialize_gpp_printers((printer,), "user")).find(
        "SharedPrinter/Properties"
    )
    assert props is not None
    assert list(props.attrib.items()) == [
        ("action", "C"), ("comment", ""), ("path", r"\\printsv\Authored"),
        ("default", "1"), ("skipLocal", "1"),
    ]
    assert parse_gpp_printers(serialize_gpp_printers((printer,), "user"))[0] == printer


def test_an_edited_import_writes_gpmcs_names() -> None:
    parsed = parse_gpp_collection("user", {"Printers/Printers.xml": PRINTERS.read_bytes()})
    collection = gpp_collection_from_dict(gpp_collection_to_dict(ensure_editor_ids(parsed)))
    first = collection.printers[0]
    edited = replace(collection, printers=(
        replace(first, set_default=False), *collection.printers[1:],
    ))
    for written in (serialize_gpp(edited), serialize_gpp(model_only(edited))):
        props = ET.fromstring(written["Printers/Printers.xml"])[0].find("Properties")
        assert props is not None
        assert props.get("default") == "0"
        assert "setDefault" not in props.attrib and "useLocal" not in props.attrib
    # The retained export keeps the edit in GPMC's own position.
    native = ET.fromstring(PRINTERS.read_bytes())[0].find("Properties")
    written_props = ET.fromstring(serialize_gpp(edited)["Printers/Printers.xml"])[0].find(
        "Properties"
    )
    assert native is not None and written_props is not None
    assert list(written_props.attrib) == list(native.attrib)


def test_studios_old_names_are_still_read() -> None:
    legacy = (
        b'<Printers clsid="{1F577D12-3D1B-471e-A1B7-060317597B9C}">'
        b'<SharedPrinter clsid="{9A5E9697-9095-436d-A0EE-4D128FDFBCE5}" name="Old">'
        b'<Properties action="U" path="\\\\printsv\\Old" setDefault="1" useLocal="1"'
        b' comment=""/></SharedPrinter></Printers>'
    )
    (printer,) = parse_gpp_printers(legacy)
    assert printer.set_default is True and printer.skip_local is True


def test_a_printer_stored_before_the_rename_loads_and_hashes_as_before() -> None:
    stored = gpp_collection_to_dict(
        GppCollection(scope="user", printers=(GppPrinter(path=r"\\printsv\P", skip_local=True),))
    )
    entry = stored["printers"][0]
    entry["use_local"] = entry.pop("skip_local")
    loaded = gpp_collection_from_dict(stored)
    assert loaded.printers[0].skip_local is True
    canonical = semantic_dict_gpp_collection(loaded)["printers"][0]
    assert canonical["use_local"] is True and "skip_local" not in canonical


def test_the_api_reports_gpmcs_default_printer(
    tmp_path: Path, monkeypatch: Any
) -> None:
    import shutil
    from contextlib import closing

    from gpo_studio.api import app
    from gpo_studio.store import WorkspaceStore

    capture = PRINTERS.parents[6]
    inbox = tmp_path / "inbox"
    shutil.copytree(capture, inbox)
    monkeypatch.setenv("GPO_STUDIO_INBOX_DIR", str(inbox))
    with closing(WorkspaceStore(tmp_path / "api.db")) as store:
        monkeypatch.setattr(app.state, "store", store, raising=False)
        monkeypatch.setattr(app.state, "owns_store", False, raising=False)
        with TestClient(app, base_url="http://127.0.0.1") as client:
            imported = client.post(
                "/api/backups/import",
                json={"path": str(inbox), "actor": "wi081", "reason": "printers"},
            )
            assert imported.status_code == 201, imported.text
    printers = imported.json()["gpo"]["gpp_collections"][0]["printers"]
    assert [p["set_default"] for p in printers] == [True, False, False, False, False]


def test_an_immediate_task_writes_its_command_only_where_there_is_no_payload() -> None:
    payload = (
        '<Task version="1.2"><Actions><Exec><Command>a.exe</Command></Exec></Actions></Task>'
    )
    with_payload = GppImmediateTask(name="T", run_as="NT AUTHORITY\\System", program="a.exe",
                                    task_xml=payload)
    props = ET.fromstring(serialize_gpp_immediate_tasks((with_payload,), "computer")).find(
        "ImmediateTaskV2/Properties"
    )
    assert props is not None
    assert list(props.attrib) == ["action", "name", "runAs"]
    bare = GppImmediateTask(name="T", program="a.exe", arguments="/x", start_in="C:\\")
    props = ET.fromstring(serialize_gpp_immediate_tasks((bare,), "computer")).find(
        "ImmediateTaskV2/Properties"
    )
    assert props is not None
    assert (props.get("program"), props.get("arguments"), props.get("startIn")) == (
        "a.exe", "/x", "C:\\",
    )


def test_an_imported_shortcut_is_named_as_gpmc_names_it() -> None:
    path = next(f for f in NATIVE_FILES if f.name == "Shortcuts.xml")
    collection = parse_gpp_collection("user", {"Shortcuts/Shortcuts.xml": path.read_bytes()})
    native = ET.fromstring(path.read_bytes())
    assert [s.name for s in collection.shortcuts] == [item.get("name") for item in native]
