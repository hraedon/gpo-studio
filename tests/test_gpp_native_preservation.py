"""Every native GPP capture survives import, storage and export unchanged (WI-080).

Before WI-080 Studio kept an imported GPO's preference XML only in memory
(``GppCollection.source_files``). Every export of a STORED GPO -- the GPMC
backup, ``export.zip``, the publication planner -- rebuilt the XML from the
typed model, which dropped every ``Properties`` attribute the model does not
type (a printer's ``default``, an environment variable's ``partial``, a task's
``logonType`` ...) and minted a new ``FilterRunOnce`` id, so clients re-applied
apply-once items. The round-trip tests in the suite all compared Studio with
itself, and no lane compares a Studio export with the GPO it was imported from
at that level, so nothing saw it.

This module compares Studio's output with the WINDOWS bytes. For every
committed native capture it runs the paths an operator uses:

* import (the shipped parser), store in a real workspace, reload, then
  :func:`serialize_gpp` -- and the dict round trip on its own;
* the public API: ``POST /api/backups/import``, then ``GET .../export.zip`` and
  ``GET .../gpmc-backup``.

Each written file must be the same XML document as the capture: every element
and attribute present, with an equal value, in the same order. The only
differences allowed are the ones in `ALLOWED_NORMALISATIONS`, each with its
reason. Nothing about item identity, timestamps or run-once ids is allowed:
Studio keeps all of them.
"""

from __future__ import annotations

import io
import shutil
import uuid
import xml.etree.ElementTree as ET
import zipfile
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from gpo_studio.api import app
from gpo_studio.backup import read_backup
from gpo_studio.canonical import semantic_dict_gpp_collection
from gpo_studio.gpp import (
    GppCollection,
    GppCommonOptions,
    GppError,
    GppGroup,
    ensure_editor_ids,
    gpp_collection_from_dict,
    gpp_collection_to_dict,
    model_only,
    parse_gpp_collection,
    serialize_gpp,
)
from gpo_studio.import_export import collect_gpp_collections
from gpo_studio.store import WorkspaceStore

ROOT = Path(__file__).resolve().parents[1]
CAPTURE_ROOTS = (
    ROOT / "tests/fixtures/native-gpp-gpmc",
    ROOT / "tests/fixtures/native-gpp-registry-gpmc",
)
CAPTURES = sorted(
    manifest.parent for root in CAPTURE_ROOTS for manifest in root.glob("*/manifest.xml")
)
_SIDES = {"Machine": "computer", "User": "user"}
#: Captures whose GPMC backup route refuses, and the one refusal it gives. The
#: refusal is about extension registration, not the XML, and export.zip still
#: writes the file, so the XML is checked there.
GPMC_BACKUP_REFUSED: dict[str, str] = {
    # Families whose extension registration no capture has measured
    # (`export.py`, unverified families): the native backup is refused for them.
    # When one is measured its capture drops out of this table and the
    # gpmc-backup route is held to the XML too.
    capture: "unsupported_native_gpp_extension"
    for capture in (
        "WI01A-EnvVars-GPMC",
        "WI01A-Files-GPMC",
        "WI01A-Folders-GPMC",
        "WI01A-IniFiles-GPMC",
        "WI01A-Power-GPMC",
        "WI01A-Printers-GPMC",
        "WI01A-Shortcuts-GPMC",
    )
}

#: The differences between a capture and Studio's output that are allowed,
#: each with the reason it is not a difference in the document. Anything not
#: listed here fails. Keep this list short and justified: an entry is a claim
#: that Windows reads the two forms identically.
ALLOWED_NORMALISATIONS: dict[str, str] = {
    "whitespace-only text": (
        "GPMC indents items with a newline and a tab between root children; that "
        "text is not content (no GPP element has mixed content) and Studio writes "
        "none. Text that is not only whitespace is compared exactly."
    ),
    "xml declaration and encoding": (
        "the files are compared as parsed documents, so the declaration line, the "
        "byte order mark and the encoding name are outside the comparison; the "
        "writer always declares utf-8, as GPMC does."
    ),
}


def _text(value: str | None) -> str:
    # ALLOWED_NORMALISATIONS["whitespace-only text"].
    return "" if value is None or not value.strip() else value


def xml_differences(original: bytes, written: bytes) -> list[str]:
    """Every difference between two GPP documents, by element path."""
    differences: list[str] = []

    def walk(left: ET.Element, right: ET.Element, path: str) -> None:
        here = f"{path}/{left.tag}"
        if left.tag != right.tag:
            differences.append(f"{here}: element is <{right.tag}> in the output")
            return
        left_attrs = list(left.attrib.items())
        right_attrs = list(right.attrib.items())
        if left_attrs != right_attrs:
            left_map, right_map = dict(left_attrs), dict(right_attrs)
            for name, value in left_attrs:
                if name not in right_map:
                    differences.append(f"{here}: @{name}={value!r} dropped")
                elif right_map[name] != value:
                    differences.append(
                        f"{here}: @{name} changed {value!r} -> {right_map[name]!r}"
                    )
            for name, value in right_attrs:
                if name not in left_map:
                    differences.append(f"{here}: @{name}={value!r} added")
            if left_map == right_map:
                differences.append(
                    f"{here}: attribute order {[n for n, _ in left_attrs]} -> "
                    f"{[n for n, _ in right_attrs]}"
                )
        if _text(left.text) != _text(right.text):
            differences.append(f"{here}: text {left.text!r} -> {right.text!r}")
        if _text(left.tail) != _text(right.tail):
            differences.append(f"{here}: tail {left.tail!r} -> {right.tail!r}")
        left_children, right_children = list(left), list(right)
        for index, (a, b) in enumerate(zip(left_children, right_children, strict=False)):
            walk(a, b, f"{here}[{index}]")
        if len(left_children) != len(right_children):
            differences.append(
                f"{here}: {len(left_children)} children -> {len(right_children)}"
            )

    walk(ET.fromstring(original), ET.fromstring(written), "")
    return differences


def native_files(capture: Path) -> dict[tuple[str, str], bytes]:
    """``(scope, "Family/File.xml")`` -> the capture's bytes."""
    files: dict[tuple[str, str], bytes] = {}
    for path in capture.glob("*/DomainSysvol/GPO/*/Preferences/*/*.xml"):
        side = path.parents[2].name
        files[(_SIDES[side], f"{path.parent.name}/{path.name}")] = path.read_bytes()
    return files


def _assert_same_documents(
    expected: dict[tuple[str, str], bytes], written: dict[tuple[str, str], bytes], route: str
) -> None:
    assert set(written) == set(expected), f"{route}: wrote {sorted(written)}"
    report = {
        f"{scope} {name}": diffs
        for (scope, name), data in sorted(written.items())
        if (diffs := xml_differences(expected[(scope, name)], data))
    }
    assert report == {}, f"{route} changed the native XML: {report}"


def test_every_native_capture_is_covered() -> None:
    """The parametrization below reaches every committed GPP capture."""
    on_disk = {
        path.parents[6].name
        for root in CAPTURE_ROOTS
        for path in root.glob("*/*/DomainSysvol/GPO/*/Preferences/*/*.xml")
    }
    assert on_disk == {capture.name for capture in CAPTURES}
    assert len(CAPTURES) >= 19


@pytest.mark.parametrize("capture", CAPTURES, ids=lambda path: path.name)
def test_stored_gpo_serializes_to_the_native_xml(capture: Path, tmp_path: Path) -> None:
    expected = native_files(capture)
    backup_gpo = read_backup(capture).gpos[0]
    assert backup_gpo.content_root is not None
    collections = collect_gpp_collections(backup_gpo.content_root)

    # The dict round trip on its own (what an API payload or a test fixture uses).
    via_dict = {
        (collection.scope, name): data
        for collection in collections
        for name, data in serialize_gpp(
            gpp_collection_from_dict(gpp_collection_to_dict(collection))
        ).items()
    }
    _assert_same_documents(expected, via_dict, "gpp_collection_to_dict/from_dict")

    # The workspace: create, reload from SQLite, write.
    with closing(WorkspaceStore(tmp_path / "workspace.db")) as store:
        created = store.create_gpo(
            name=capture.name,
            identity="native-preservation",
            reason="WI-080 round trip",
            gpp_collections=collections,
        )
    with closing(WorkspaceStore(tmp_path / "workspace.db")) as store:
        stored = store.get_gpo(created.guid)
    assert all(not collection.source_files for collection in stored.gpp_collections)
    via_store = {
        (collection.scope, name): data
        for collection in stored.gpp_collections
        for name, data in serialize_gpp(collection).items()
    }
    _assert_same_documents(expected, via_store, "workspace store")


def _zip_preferences(data: bytes) -> dict[tuple[str, str], bytes]:
    files: dict[tuple[str, str], bytes] = {}
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for name in archive.namelist():
            parts = name.split("/")
            if "Preferences" not in parts or not name.endswith(".xml"):
                continue
            at = parts.index("Preferences")
            files[(_SIDES[parts[at - 1]], "/".join(parts[at + 1 :]))] = archive.read(name)
    return files


@pytest.mark.parametrize("capture", CAPTURES, ids=lambda path: path.name)
def test_public_exports_write_the_native_xml(
    capture: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = native_files(capture)
    inbox = tmp_path / "inbox"
    shutil.copytree(capture, inbox)
    monkeypatch.setenv("GPO_STUDIO_INBOX_DIR", str(inbox))
    with closing(WorkspaceStore(tmp_path / "api.db")) as store:
        monkeypatch.setattr(app.state, "store", store, raising=False)
        monkeypatch.setattr(app.state, "owns_store", False, raising=False)
        with TestClient(app, base_url="http://127.0.0.1") as client:
            imported = client.post(
                "/api/backups/import",
                json={"path": str(inbox), "actor": "native-preservation", "reason": "WI-080"},
            )
            assert imported.status_code == 201, imported.text
            guid = imported.json()["gpo"]["guid"]

            bundle = client.get(f"/api/gpos/{guid}/export.zip")
            assert bundle.status_code == 200, bundle.text
            _assert_same_documents(expected, _zip_preferences(bundle.content), "export.zip")

            backup = client.get(f"/api/gpos/{guid}/gpmc-backup")
            if capture.name in GPMC_BACKUP_REFUSED:
                # Refused for a reason that has nothing to do with the XML.
                assert backup.status_code == 422, backup.text
                codes = {issue["code"] for issue in backup.json()["error"]["issues"]}
                assert codes == {GPMC_BACKUP_REFUSED[capture.name]}, backup.text
                return
            assert backup.status_code == 200, backup.text
            _assert_same_documents(expected, _zip_preferences(backup.content), "gpmc-backup")


# ---------------------------------------------------------------------------
# Edits: what the model types wins; what it does not type stays
# ---------------------------------------------------------------------------

NATIVE = ROOT / "tests/fixtures/native-gpp-gpmc"


def _native_preference(capture: str, family_file: str, side: str = "User") -> bytes:
    (path,) = (NATIVE / capture).glob(f"*/DomainSysvol/GPO/{side}/Preferences/{family_file}")
    return path.read_bytes()


def _imported(capture: str, family_file: str, scope: str = "user") -> GppCollection:
    """A capture's file parsed and stored the way import stores it."""
    parsed = parse_gpp_collection(
        scope,  # type: ignore[arg-type]
        {family_file: _native_preference(capture, family_file)},
    )
    return gpp_collection_from_dict(gpp_collection_to_dict(ensure_editor_ids(parsed)))


def _written(collection: GppCollection, family_file: str) -> ET.Element:
    return ET.fromstring(serialize_gpp(collection)[family_file])


def test_an_edit_to_a_typed_field_wins_and_the_rest_stays_as_imported() -> None:
    collection = _imported("WI01A-Shortcuts-GPMC", "Shortcuts/Shortcuts.xml")
    first = collection.shortcuts[0]
    assert first.window_style == "normal"  # read from window=""
    edited = replace(collection, shortcuts=(
        replace(first, arguments="--edited", window_style="maximized"),
        *collection.shortcuts[1:],
    ))
    written = _written(edited, "Shortcuts/Shortcuts.xml")
    props = written[0].find("Properties")
    assert props is not None
    # The edited typed fields, each in its imported position.
    assert props.get("arguments") == "--edited"
    assert props.get("window") == "Maximized"
    native = ET.fromstring(_native_preference("WI01A-Shortcuts-GPMC", "Shortcuts/Shortcuts.xml"))
    native_props = native[0].find("Properties")
    assert native_props is not None
    assert list(props.attrib) == list(native_props.attrib)
    # What the model does not type is written as imported.
    for name in ("pidl", "targetType", "comment", "shortcutKey"):
        assert props.get(name) == native_props.get(name)
    # The item element and the filter did not change, so they are as imported.
    assert list(written[0].attrib.items()) == list(native[0].attrib.items())
    assert ET.tostring(written[0].find("Filters")) == ET.tostring(native[0].find("Filters"))
    # The other, unedited items are the import exactly.
    for out, source in zip(list(written)[1:], list(native)[1:], strict=True):
        assert xml_differences(ET.tostring(source), ET.tostring(out)) == []


def test_an_edit_back_to_the_imported_value_writes_the_imported_text() -> None:
    collection = _imported("WI01A-Shortcuts-GPMC", "Shortcuts/Shortcuts.xml")
    first = collection.shortcuts[0]
    # "normal" is what window="" means; setting it explicitly changes nothing.
    edited = replace(collection, shortcuts=(
        replace(first, window_style="normal", arguments="--edited"), *collection.shortcuts[1:],
    ))
    props = _written(edited, "Shortcuts/Shortcuts.xml")[0].find("Properties")
    assert props is not None and props.get("window") == ""


def test_an_unmodelled_attribute_survives_an_edit_of_the_item() -> None:
    collection = _imported("WI01A-Printers-GPMC", "Printers/Printers.xml")
    default_printer = collection.printers[0]
    edited = replace(collection, printers=(
        replace(default_printer, comment="Edited"), *collection.printers[1:],
    ))
    props = _written(edited, "Printers/Printers.xml")[0].find("Properties")
    assert props is not None
    assert props.get("comment") == "Edited"
    # GPMC's own "set as default printer" (the model does not type it) stays.
    assert props.get("default") == "1"
    # The writer's own attributes the source never had are not added.
    assert "setDefault" not in props.attrib and "useLocal" not in props.attrib


def test_a_writer_default_appears_only_once_an_edit_changes_it() -> None:
    collection = _imported("WI01A-IniFiles-GPMC", "IniFiles/IniFiles.xml")
    ini = collection.ini_files[0]
    native = ET.fromstring(_native_preference("WI01A-IniFiles-GPMC", "IniFiles/IniFiles.xml"))
    assert "disabled" not in native[0].attrib
    assert "disabled" not in _written(collection, "IniFiles/IniFiles.xml")[0].attrib
    disabled = replace(collection, ini_files=(
        replace(ini, common=replace(ini.common, disabled=True)), *collection.ini_files[1:],
    ))
    assert _written(disabled, "IniFiles/IniFiles.xml")[0].get("disabled") == "1"


def test_an_edit_that_removes_a_rendered_attribute_removes_it() -> None:
    collection = _imported("WI01A-LocalGroups-GPMC", "Groups/Groups.xml")
    group = collection.groups[0]
    edited = replace(collection, groups=(replace(group, description="Edited"),))
    props = _written(edited, "Groups/Groups.xml")[0].find("Properties")
    assert props is not None and props.get("description") == "Edited"
    cleared = replace(collection, groups=(replace(group, description="", remove_all_users=True),))
    props = _written(cleared, "Groups/Groups.xml")[0].find("Properties")
    # description="" is what the source said and what the model says.
    assert props is not None and props.get("description") == ""
    assert props.get("deleteAllUsers") == "1"


def test_a_legacy_placement_never_outlives_the_typed_value_it_carried() -> None:
    """An option an older Studio wrote on <Properties> is read from there.

    Edited, the item must still carry the option, once and in the right place:
    the merged element is parsed again and, because it would not mean what the
    model means, the model's own rendering is written instead.
    """
    legacy = (
        b'<?xml version="1.0" encoding="utf-8"?>'
        b'<Groups clsid="{3125E937-EB16-4b4c-9934-544FC6D24D26}">'
        b'<Group clsid="{6D4A79E4-529C-4481-ABD0-F5BD7EA93BA7}" name="Legacy">'
        b'<Properties action="U" groupName="Legacy" removePolicy="1" description="old"/>'
        b"</Group></Groups>"
    )
    parsed = parse_gpp_collection("computer", {"Groups/Groups.xml": legacy})
    collection = gpp_collection_from_dict(gpp_collection_to_dict(ensure_editor_ids(parsed)))
    assert collection.groups[0].common.remove_when_unapplied is True
    edited = replace(collection, groups=(replace(collection.groups[0], description="new"),))
    written = serialize_gpp(edited)["Groups/Groups.xml"]
    reread = parse_gpp_collection("computer", {"Groups/Groups.xml": written}).groups[0]
    assert reread.common.remove_when_unapplied is True
    assert reread.description == "new"
    item = ET.fromstring(written)[0]
    props = item.find("Properties")
    assert props is not None and "removePolicy" not in props.attrib
    assert item.get("removePolicy") == "1"


def test_a_cpassword_is_never_written_back_from_a_retained_element() -> None:
    collection = _imported("WI01A-Printers-GPMC", "Printers/Printers.xml")
    printer = collection.printers[0]
    smuggled = printer.native_xml.replace("<Properties ", '<Properties cpassword="x" ', 1)
    data = gpp_collection_to_dict(
        replace(collection, printers=(replace(printer, native_xml=smuggled),))
    )
    with pytest.raises(GppError, match="cpassword"):
        gpp_collection_from_dict(data)


@pytest.mark.parametrize(
    ("native_xml", "message"),
    [
        ('<Drive clsid="x" name="M:"><Properties/></Drive>', "not an item of printers"),
        ("<SharedPrinter", "Malformed"),
        ('<!DOCTYPE x [<!ENTITY e "v">]><SharedPrinter/>', "entity"),
        (7, "must be a string"),
    ],
    ids=["another family's item", "not XML", "an entity", "not a string"],
)
def test_a_stored_native_element_is_held_to_what_import_produces(
    native_xml: object, message: str
) -> None:
    collection = _imported("WI01A-Printers-GPMC", "Printers/Printers.xml")
    data = gpp_collection_to_dict(collection)
    data["printers"][0]["native_xml"] = native_xml
    with pytest.raises(GppError, match=message):
        gpp_collection_from_dict(data)


# ---------------------------------------------------------------------------
# FilterRunOnce identity
# ---------------------------------------------------------------------------

_NATIVE_RUN_ONCE = "{96AFCD0C-EA2A-4ECD-BB76-284E9A8FB23C}"


def _run_once_ids(root: ET.Element) -> list[str]:
    return [elem.get("id", "") for elem in root.iter("FilterRunOnce")]


def test_the_imported_run_once_id_is_kept_through_storage_and_edits() -> None:
    collection = _imported("WI01A-LocalGroups-GPMC", "Groups/Groups.xml")
    group = collection.groups[0]
    assert group.common.apply_once is True
    assert group.common.run_once_id == _NATIVE_RUN_ONCE
    # An edit that rewrites the filter from the model still carries the id.
    edited_filter = replace(collection, groups=(replace(group, ilt_filter=None),))
    assert _run_once_ids(_written(edited_filter, "Groups/Groups.xml")) == [_NATIVE_RUN_ONCE]
    # So does the model alone, with no retained element at all.
    alone = _written(model_only(collection), "Groups/Groups.xml")
    assert _run_once_ids(alone) == [_NATIVE_RUN_ONCE]


def test_apply_once_off_then_on_restores_the_same_identity() -> None:
    collection = _imported("WI01A-LocalGroups-GPMC", "Groups/Groups.xml")
    group = collection.groups[0]
    off = replace(group, common=replace(group.common, apply_once=False))
    off_collection = gpp_collection_from_dict(
        gpp_collection_to_dict(replace(collection, groups=(off,)))
    )
    assert _run_once_ids(_written(off_collection, "Groups/Groups.xml")) == []
    stored_off = off_collection.groups[0]
    on = replace(stored_off, common=replace(stored_off.common, apply_once=True))
    on_collection = replace(off_collection, groups=(on,))
    assert _run_once_ids(_written(on_collection, "Groups/Groups.xml")) == [_NATIVE_RUN_ONCE]


def test_an_item_that_never_had_a_run_once_id_gets_the_deterministic_one() -> None:
    group = GppGroup(
        name="Authored",
        id="11111111-2222-3333-4444-555555555555",
        common=GppCommonOptions(apply_once=True),
    )
    written = _written(GppCollection(scope="computer", groups=(group,)), "Groups/Groups.xml")
    expected = uuid.uuid5(uuid.NAMESPACE_URL, f"gpo-studio/gpp/run-once/{group.id}")
    assert _run_once_ids(written) == ["{" + str(expected).upper() + "}"]


# ---------------------------------------------------------------------------
# Storage compatibility: data stored before WI-080 is written as before
# ---------------------------------------------------------------------------


def _as_stored_before_wi080(collection: GppCollection) -> dict[str, Any]:
    """The dict a pre-WI-080 Studio stored: no native_xml, no run_once_id."""
    data = gpp_collection_to_dict(collection)
    for items in data.values():
        if not isinstance(items, list):
            continue
        for entry in items:
            if isinstance(entry, dict):
                entry.pop("native_xml", None)
                if isinstance(entry.get("common"), dict):
                    entry["common"].pop("run_once_id", None)
    return data


def _pre_wi080_model(collection: GppCollection) -> GppCollection:
    """The same collection as a pre-WI-080 import held it."""
    from gpo_studio.gpp_adapters import ADAPTER_KEYS

    def strip(items: tuple[Any, ...]) -> tuple[Any, ...]:
        return tuple(replace(i, common=replace(i.common, run_once_id="")) for i in items)

    alone = model_only(collection)
    return replace(
        alone,
        groups=strip(alone.groups),
        registry=strip(alone.registry),
        **{key: strip(getattr(alone, key)) for key in ADAPTER_KEYS},
    )


@pytest.mark.parametrize("capture", CAPTURES, ids=lambda path: path.name)
def test_data_stored_before_the_fix_is_written_and_hashed_as_before(capture: Path) -> None:
    backup_gpo = read_backup(capture).gpos[0]
    assert backup_gpo.content_root is not None
    for collection in collect_gpp_collections(backup_gpo.content_root):
        old = gpp_collection_from_dict(_as_stored_before_wi080(collection))
        assert serialize_gpp(old) == serialize_gpp(_pre_wi080_model(collection))
        assert semantic_dict_gpp_collection(old) == semantic_dict_gpp_collection(
            _pre_wi080_model(collection)
        )
        assert "retained_native" not in str(semantic_dict_gpp_collection(old))


def test_a_retained_element_enters_the_hash_only_when_it_changes_what_is_written() -> None:
    collection = _imported("WI01A-Printers-GPMC", "Printers/Printers.xml")
    with_native = semantic_dict_gpp_collection(collection)
    native = ET.fromstring(collection.printers[0].native_xml)
    assert ET.fromstring(with_native["printers"][0]["retained_native"]).attrib == native.attrib
    alone = semantic_dict_gpp_collection(model_only(collection))
    assert "retained_native" not in alone["printers"][0]
    # Two imports that differ only in an attribute the model does not type.
    printer = collection.printers[0]
    flipped = replace(collection, printers=(
        replace(printer, native_xml=printer.native_xml.replace('default="1"', 'default="0"')),
        *collection.printers[1:],
    ))
    assert semantic_dict_gpp_collection(flipped) != with_native


# ---------------------------------------------------------------------------
# The workbench edit path: the API never takes native_xml, the store keeps it
# ---------------------------------------------------------------------------


def test_an_api_edit_keeps_the_import_record_and_its_edit_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    capture = NATIVE / "WI01A-LocalGroups-GPMC"
    inbox = tmp_path / "inbox"
    shutil.copytree(capture, inbox)
    monkeypatch.setenv("GPO_STUDIO_INBOX_DIR", str(inbox))
    with closing(WorkspaceStore(tmp_path / "api.db")) as store:
        monkeypatch.setattr(app.state, "store", store, raising=False)
        monkeypatch.setattr(app.state, "owns_store", False, raising=False)
        with TestClient(app, base_url="http://127.0.0.1") as client:
            imported = client.post(
                "/api/backups/import",
                json={"path": str(inbox), "actor": "native-preservation", "reason": "WI-080"},
            )
            assert imported.status_code == 201, imported.text
            fork = client.post(
                f"/api/gpos/{imported.json()['gpo']['guid']}/fork",
                json={"name": "Edited fork", "actor": "native-preservation", "reason": "edit"},
            )
            assert fork.status_code == 201, fork.text
            gpo = fork.json()["gpo"]
            user = next(c for c in gpo["gpp_collections"] if c["scope"] == "user")
            group = user["groups"][0]
            # Import provenance the writer reads; the API does not serve it.
            assert "native_xml" not in group
            # What the workbench sends back (static/js/gpp.mjs): no native_xml.
            payload = {
                key: group[key]
                for key in (
                    "name", "sid", "action", "description", "remove_all_users",
                    "remove_all_groups", "members", "id", "ilt_filter", "unknown_attrs",
                    "unknown_props_attrs", "unknown_props_children", "unknown_children",
                )
            }
            payload["description"] = "Edited in the workbench"
            payload["native_xml"] = "<Group/>"  # ignored: the API never takes one
            edited = client.put(
                f"/api/gpos/{gpo['guid']}/preferences/groups/{group['id']}",
                json={
                    "scope": "user", "group": payload, "actor": "native-preservation",
                    "reason": "edit", "expected_revision": gpo["revision"],
                },
            )
            assert edited.status_code == 200, edited.text
            stored = store.get_gpo(gpo["guid"])
            stored_group = next(c for c in stored.gpp_collections if c.scope == "user").groups[0]
            assert stored_group.native_xml.startswith("<Group ")
            bundle = client.get(f"/api/gpos/{gpo['guid']}/export.zip")
            assert bundle.status_code == 200, bundle.text
    written = ET.fromstring(_zip_preferences(bundle.content)[("user", "Groups/Groups.xml")])
    native = ET.fromstring(_native_preference("WI01A-LocalGroups-GPMC", "Groups/Groups.xml"))
    props, native_props = written[0].find("Properties"), native[0].find("Properties")
    assert props is not None and native_props is not None
    assert props.get("description") == "Edited in the workbench"
    # Everything else on <Properties> is as imported, in the imported order.
    assert list(props.attrib) == list(native_props.attrib)
    assert {k: v for k, v in props.attrib.items() if k != "description"} == {
        k: v for k, v in native_props.attrib.items() if k != "description"
    }
    # The members did not change: written as imported.
    members, native_members = props.find("Members"), native_props.find("Members")
    assert members is not None and native_members is not None
    assert ET.tostring(members) == ET.tostring(native_members)


def test_reimporting_studios_own_export_leaves_the_hash_unchanged() -> None:
    """Studio's export, imported again, retains elements the model writes anyway."""
    authored = GppCollection(scope="computer", groups=(
        GppGroup(name="Authored", id="11111111-2222-3333-4444-555555555555",
                 description="authored", common=GppCommonOptions(apply_once=True)),
    ))
    files = serialize_gpp(authored)
    reimported = gpp_collection_from_dict(gpp_collection_to_dict(
        ensure_editor_ids(parse_gpp_collection("computer", files))
    ))
    assert reimported.groups[0].native_xml
    assert semantic_dict_gpp_collection(reimported) == semantic_dict_gpp_collection(authored)
    assert serialize_gpp(reimported) == files
