"""GPP Registry against the native capture (batch 2, WI-075).

Every expectation here comes from Windows, not from Studio: the Registry.xml
bytes ``Set-GPPrefRegistryValue`` wrote on WS2025, the extension lists it
registered in AD, the GPMC report and the ``Backup-GPO`` tree
(``tests/fixtures/native-gpp-gpmc/WI01A-Registry-GPMC``), and the authoring
inputs of the capture script that produced them
(``scripts/plan-033/capture-gpp-registry-native.ps1``). A comparison of Studio's
writer with Studio's own parser would prove only self-consistency.
"""

from __future__ import annotations

import io
import json
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

from gpo_studio.export import (
    _GPP_EXTENSION_PROFILES,
    extension_registration,
    gpmc_backup_bundle,
    native_backup_refusal,
)
from gpo_studio.gpp import (
    GppCollection,
    GppError,
    GppRegistry,
    GppRegistryValue,
    ensure_editor_ids,
    gpp_collection_from_dict,
    gpp_registry_unmeasured_shapes,
    mark_edited,
    parse_gpp_collection,
    parse_gpp_registry,
    serialize_gpp,
    serialize_gpp_registry,
)
from gpo_studio.model import GPO, ValidationError
from gpo_studio.publication import generate_publication_plan, validate_publication_plan
from gpo_studio.writer_conformance import (
    compare_preferences,
    summary_from_backup,
    summary_from_gpmc_report,
    summary_from_gpo,
)

CAPTURE = Path(__file__).parent / "fixtures" / "native-gpp-gpmc" / "WI01A-Registry-GPMC"
CONTENT_ROOT = next(CAPTURE.glob("*/DomainSysvol/GPO"))
KEY = r"Software\GPOStudio\GppRegistry"

#: What the capture script asked Windows to author (its ``$items`` table), by
#: side. The Delete item is absent: it failed to author (capture.json), which is
#: why the Delete wire form is still unmeasured.
AUTHORED: dict[str, list[tuple[str, str, str | int | list[str], str, str]]] = {
    "Machine": [
        ("CreateString", "REG_SZ", "alpha", "create", "C"),
        ("UpdateDword", "REG_DWORD", 42, "update", "U"),
        ("ReplaceExpand", "REG_EXPAND_SZ", "%SystemRoot%\\x", "replace", "R"),
    ],
    "User": [
        ("UserMulti", "REG_MULTI_SZ", ["one", "two"], "update", "U"),
        ("UserQword", "REG_QWORD", 4294967296, "create", "C"),
    ],
}
HIVE = {"Machine": "HKEY_LOCAL_MACHINE", "User": "HKEY_CURRENT_USER"}
SCOPE = {"Machine": "computer", "User": "user"}

#: Common options Studio always writes explicitly. The cmdlet wrote only
#: ``disabled``; Studio's parser reads the other three from their absence
#: (``removePolicy``/``userContext`` absent = 0, ``bypassErrors`` absent = 0,
#: i.e. stop on error), so the explicit forms say the same thing.
STUDIO_EXPLICIT_COMMON = ("removePolicy", "userContext", "bypassErrors")


def _native_bytes(side: str) -> bytes:
    return (CONTENT_ROOT / side / "Preferences" / "Registry" / "Registry.xml").read_bytes()


def _native_items(side: str) -> list[ET.Element]:
    return list(ET.fromstring(_native_bytes(side)))


def _authored_model(side: str) -> tuple[GppRegistry, ...]:
    """The capture's inputs as a Studio model, built WITHOUT the parser.

    The uid and ``changed`` stamp are Windows-generated identity, not policy;
    they are copied from the native item so the comparison can be exact.
    """
    items = []
    for (name, reg_type, value, action, _code), native in zip(
        AUTHORED[side], _native_items(side), strict=True
    ):
        items.append(
            GppRegistry(
                key=KEY,
                hive=HIVE[side],
                uid=native.attrib["uid"],
                value=GppRegistryValue(
                    name=name, value=value, registry_type=reg_type, action=action  # type: ignore[arg-type]
                ),
                unknown_attrs=(("changed", native.attrib["changed"]),),
            )
        )
    return tuple(items)


def _assert_same_element(studio: ET.Element, native: ET.Element, path: str = "") -> None:
    """Tag, attribute SET AND ORDER, text and children must match.

    Whitespace-only text is the native file's pretty-printing (CRLF + two-space
    indent) and carries no policy. On <Registry>, Studio's explicit common
    options are removed before comparing; nothing else is excused.
    """
    where = f"{path}/{native.tag}"
    assert studio.tag == native.tag, where
    studio_attrs = list(studio.attrib.items())
    if native.tag == "Registry":
        studio_attrs = [(k, v) for k, v in studio_attrs if k not in STUDIO_EXPLICIT_COMMON]
    assert studio_attrs == list(native.attrib.items()), where
    assert (studio.text or "").strip() == (native.text or "").strip(), where
    assert len(studio) == len(native), where
    for studio_child, native_child in zip(studio, native, strict=True):
        _assert_same_element(studio_child, native_child, where)


# ---------------------------------------------------------------------------
# Reading the native bytes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("side", ["Machine", "User"])
def test_native_registry_xml_parses_to_what_was_authored(side: str) -> None:
    parsed = parse_gpp_registry(_native_bytes(side))
    got = [
        (r.value.name, r.value.registry_type, r.value.value, r.value.action, r.hive, r.key)
        for r in parsed
    ]
    want = [(n, t, v, a, HIVE[side], KEY) for n, t, v, a, _ in AUTHORED[side]]
    assert got == want


def test_native_bytes_are_the_exact_capture() -> None:
    """The fixture Registry.xml files are Windows' bytes, unsanitized.

    capture.json holds the SYSVOL copies the script read with ReadAllBytes;
    Backup-GPO's copies must be byte-identical to them (BOM, CRLF and all).
    """
    capture = json.loads((CAPTURE / "capture.json").read_text(encoding="utf-8-sig"))
    import base64

    for side in ("Machine", "User"):
        assert base64.b64decode(capture[f"{side}_registry_xml_base64"]) == _native_bytes(side)


# ---------------------------------------------------------------------------
# Writing: Studio's output against the native bytes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("side", ["Machine", "User"])
def test_writer_matches_native_items_from_an_authored_model(side: str) -> None:
    collection = GppCollection(scope=SCOPE[side], registry=_authored_model(side))  # type: ignore[arg-type]
    studio_root = ET.fromstring(serialize_gpp_registry(collection))
    native_root = ET.fromstring(_native_bytes(side))
    _assert_same_element(studio_root, native_root)


@pytest.mark.parametrize("side", ["Machine", "User"])
def test_writer_matches_native_items_after_an_import_and_edit(side: str) -> None:
    """The import-then-edit path: the D8 verbatim bytes are bypassed."""
    collection = parse_gpp_collection(
        SCOPE[side],  # type: ignore[arg-type]
        {"Registry/Registry.xml": _native_bytes(side)},
    )
    studio = serialize_gpp(mark_edited(collection))["Registry/Registry.xml"]
    _assert_same_element(ET.fromstring(studio), ET.fromstring(_native_bytes(side)))


def test_explicit_common_options_mean_what_the_native_absence_means() -> None:
    """The three attributes the native items omit decode to the same options."""
    native = parse_gpp_registry(_native_bytes("Machine"))
    rewritten = parse_gpp_registry(
        serialize_gpp_registry(GppCollection(scope="computer", registry=native))
    )
    assert [r.common for r in rewritten] == [r.common for r in native]


@pytest.mark.parametrize(
    ("registry_type", "value", "wire"),
    [
        # Measured: UpdateDword 42 and UserQword 4294967296.
        ("REG_DWORD", 42, "0000002A"),
        ("REG_QWORD", 4294967296, "0000000100000000"),
        # Same rule, values chosen to catch a decimal or lower-case writer.
        ("REG_DWORD", 3000000000, "B2D05E00"),
        ("REG_DWORD", 0, "00000000"),
        ("REG_QWORD", 0x123456789ABCDEF0, "123456789ABCDEF0"),
    ],
)
def test_numeric_values_are_fixed_width_upper_hex(
    registry_type: str, value: int, wire: str
) -> None:
    reg = GppRegistry(
        key=KEY, value=GppRegistryValue(name="N", value=value, registry_type=registry_type)
    )
    root = ET.fromstring(serialize_gpp_registry(GppCollection(scope="computer", registry=(reg,))))
    props = root.find("Registry/Properties")
    assert props is not None
    assert props.attrib["value"] == wire
    assert parse_gpp_registry(serialize_gpp_registry(
        GppCollection(scope="computer", registry=(reg,))
    ))[0].value.value == value


@pytest.mark.parametrize(
    ("registry_type", "value"),
    [("REG_DWORD", 2**32), ("REG_DWORD", -1), ("REG_QWORD", 2**64)],
)
def test_out_of_range_numbers_are_refused_by_the_writer(registry_type: str, value: int) -> None:
    reg = GppRegistry(
        key=KEY, value=GppRegistryValue(name="N", value=value, registry_type=registry_type)
    )
    with pytest.raises(GppError):
        serialize_gpp_registry(GppCollection(scope="computer", registry=(reg,)))


def test_image_codes_follow_the_measured_actions() -> None:
    """C/R/U images read straight off the native items; Delete has none (WI-075)."""
    measured = {
        native.find("Properties").attrib["action"]: native.attrib["image"]  # type: ignore[union-attr]
        for side in ("Machine", "User")
        for native in _native_items(side)
    }
    assert measured == {"C": "0", "U": "2", "R": "1"}
    for action, code in (("create", "C"), ("replace", "R"), ("update", "U"), ("delete", "D")):
        reg = GppRegistry(key=KEY, value=GppRegistryValue(name="N", value="v", action=action))  # type: ignore[arg-type]
        elem = ET.fromstring(
            serialize_gpp_registry(GppCollection(scope="computer", registry=(reg,)))
        )[0]
        assert elem.get("image") == measured.get(code), action


def test_bom_follows_the_gpmc_editor_corpus_not_the_cmdlet() -> None:
    """Both forms are Windows-written; the GPMC editor's is the one Studio emits.

    The cmdlet capture starts with a UTF-8 BOM; every GPMC-editor GPP file in
    the corpus (the families already certified among them) has none. XML
    readers treat the two identically, so this pins a choice, with its basis.
    """
    assert _native_bytes("Machine").startswith(b"\xef\xbb\xbf")
    editor_files = [
        path
        for path in CAPTURE.parent.glob("*/*/DomainSysvol/GPO/*/Preferences/*/*.xml")
        if CAPTURE not in path.parents
    ]
    assert editor_files
    assert not any(path.read_bytes().startswith(b"\xef\xbb\xbf") for path in editor_files)
    studio = serialize_gpp_registry(
        GppCollection(scope="computer", registry=_authored_model("Machine"))
    )
    assert studio.startswith(b'<?xml version="1.0" encoding="utf-8"?>')


def test_editor_ids_are_braced_upper_case_like_native_uids() -> None:
    native_uid = _native_items("Machine")[0].attrib["uid"]
    assert re.fullmatch(r"\{[0-9A-F]{8}(-[0-9A-F]{4}){3}-[0-9A-F]{12}\}", native_uid)
    collection = ensure_editor_ids(
        GppCollection(scope="computer", registry=(GppRegistry(key=KEY),))
    )
    generated = collection.registry[0].uid
    assert re.fullmatch(r"\{[0-9A-F]{8}(-[0-9A-F]{4}){3}-[0-9A-F]{12}\}", generated)


def test_a_lower_case_stored_uid_is_written_in_native_form() -> None:
    reg = GppRegistry(
        key=KEY,
        uid="80ba2f39-55ec-40e1-a554-a6096468d60e",
        value=GppRegistryValue(name="N", value="v"),
    )
    written = serialize_gpp_registry(GppCollection(scope="computer", registry=(reg,)))
    elem = ET.fromstring(written)[0]
    assert elem.attrib["uid"] == "{80BA2F39-55EC-40E1-A554-A6096468D60E}"


# ---------------------------------------------------------------------------
# What the parser refuses rather than guesses
# ---------------------------------------------------------------------------


def _one(props: str) -> bytes:
    return (
        '<RegistrySettings clsid="{A3CCFC41-DFDB-43a5-8D26-0FE8B954DA51}">'
        '<Registry clsid="{9CD4B2F4-923D-47f5-A062-E897DD1DAD50}" name="N">'
        f'<Properties action="C" hive="HKEY_LOCAL_MACHINE" key="K" name="N" {props}'
        "</Registry></RegistrySettings>"
    ).encode()


@pytest.mark.parametrize(
    "props",
    [
        # Pre-batch-2 Studio wrote decimal; 42 decimal is not 0x42.
        'type="REG_DWORD" value="42"/>',
        # Eight decimal digits are indistinguishable from hex: width is not enough
        # on its own, but a non-hex digit is.
        'type="REG_DWORD" value="0000002G"/>',
        'type="REG_QWORD" value="4294967296"/>',
        # Pre-batch-2 Studio's ';'-joined multi-string, with no <Values> list.
        'type="REG_MULTI_SZ" value="a;b"/>',
        # value and <Values> disagree: nothing says which the extension applies.
        'type="REG_MULTI_SZ" value="a b"><Values><Value>a</Value></Values></Properties>',
        # A populated <Values> under a non-multi type would be dropped on re-export.
        'type="REG_SZ" value="a"><Values><Value>a</Value></Values></Properties>',
    ],
)
def test_unmeasured_wire_forms_are_refused_on_read(props: str) -> None:
    with pytest.raises(GppError):
        parse_gpp_registry(_one(props))


def test_the_gpmc_report_rendering_parses() -> None:
    """GPMC's report adds an EMPTY <Values/> and <Filters/> to every item."""
    report = summary_from_gpmc_report(CAPTURE / "gpreport-verify.xml")
    preferences = report["preferences"]
    assert isinstance(preferences, dict)
    names = [item["name"] for item in preferences["computer"]["gpp_registry"]]
    assert sorted(names) == sorted(n for n, *_ in AUTHORED["Machine"])


def test_report_and_backup_agree_through_studio() -> None:
    differences = compare_preferences(
        summary_from_backup(CONTENT_ROOT),
        summary_from_gpmc_report(CAPTURE / "gpreport-verify.xml"),
    )
    assert differences == (), [d.describe() for d in differences]


# ---------------------------------------------------------------------------
# Extension registration: the measured pair, from the capture
# ---------------------------------------------------------------------------


def _measured_registry_list() -> str:
    capture = json.loads((CAPTURE / "capture.json").read_text(encoding="utf-8-sig"))
    machine, user = capture["ad"]["machine"], capture["ad"]["user"]
    assert machine == user
    return str(machine)


def test_the_registered_pair_is_the_one_windows_registered() -> None:
    client, tool = _GPP_EXTENSION_PROFILES["Registry"]
    assert _measured_registry_list() == f"[{client}{tool}]"
    backup_xml = next(CAPTURE.glob("*/Backup.xml")).read_text(encoding="utf-8-sig")
    for side in ("Machine", "User"):
        recorded = re.search(rf"<{side}ExtensionGuids><!\[CDATA\[(.*?)\]\]>", backup_xml)
        assert recorded is not None
        assert recorded.group(1) == f"[{client}{tool}]"


def _gpo(*collections: GppCollection) -> GPO:
    return GPO(
        guid="9b1de5c0-0000-4000-8000-0000000000f1",
        name="GPP Registry native",
        domain="synthetic.test",
        gpp_collections=collections,
    )


def _authored_gpo() -> GPO:
    return _gpo(
        GppCollection(scope="computer", registry=_authored_model("Machine")),
        GppCollection(scope="user", registry=_authored_model("User")),
    )


def test_native_backup_registers_the_measured_pair_on_both_sides(tmp_path: Path) -> None:
    gpo = _authored_gpo()
    assert native_backup_refusal(gpo) is None
    with zipfile.ZipFile(io.BytesIO(gpmc_backup_bundle(gpo))) as archive:
        archive.extractall(tmp_path)
        backup_xml = next(
            archive.read(n) for n in archive.namelist() if n.endswith("/Backup.xml")
        ).decode()
    pair = _measured_registry_list()
    for side in ("Machine", "User"):
        recorded = re.search(rf"<(?:\w+:)?{side}ExtensionGuids>(.*?)</", backup_xml)
        assert recorded is not None
        assert recorded.group(1).endswith(pair), side
    # And the written files read back as the authored policy.
    content_root = next(tmp_path.glob("*/DomainSysvol/GPO"))
    assert summary_from_backup(content_root) == summary_from_gpo(gpo)


def test_extension_registration_states_the_pair_for_the_planner() -> None:
    registration = extension_registration(_authored_gpo())
    assert registration.unverified_families == ()
    assert registration.unmeasured_shapes == ()
    pair = _measured_registry_list()
    assert registration.machine.endswith(pair)
    assert registration.user.endswith(pair)


# ---------------------------------------------------------------------------
# Unmeasured item shapes: refused by the export and the planner alike
# ---------------------------------------------------------------------------

UNMEASURED: dict[str, GppRegistryValue] = {
    "delete": GppRegistryValue(name="Gone", value="x", action="delete"),
    "binary": GppRegistryValue(name="Bin", value="CAFE", registry_type="REG_BINARY"),
    "key-only": GppRegistryValue(name="", value="", registry_type=""),
    "default": GppRegistryValue(name="", value="d", default=True),
}


@pytest.mark.parametrize("shape", sorted(UNMEASURED))
def test_unmeasured_shapes_refuse_native_export_and_publication(shape: str) -> None:
    reg = GppRegistry(key=KEY, value=UNMEASURED[shape])
    gpo = _gpo(GppCollection(scope="computer", registry=(reg,)))
    assert len(gpp_registry_unmeasured_shapes(gpo.gpp_collections[0])) == 1

    refusal = native_backup_refusal(gpo)
    assert refusal is not None
    assert refusal.code == "unmeasured_gpp_registry_shape"

    registration = extension_registration(gpo)
    assert registration.unverified_families == ()
    assert len(registration.unmeasured_shapes) == 1

    plan = generate_publication_plan(gpo)
    assert any(step.operation == "unsupported_gpp_registry_shape" for step in plan.steps)
    checks = {issue.check for issue in validate_publication_plan(plan) if issue.level == "error"}
    assert "unsupported_gpp_registry_shape" in checks


def test_measured_shapes_do_not_refuse_publication() -> None:
    plan = generate_publication_plan(_authored_gpo())
    assert not any(step.operation.startswith("unsupported") for step in plan.steps)


# ---------------------------------------------------------------------------
# Workspaces stored before batch 2
# ---------------------------------------------------------------------------


def test_a_pre_batch_2_import_is_re_typed_on_load() -> None:
    """Before batch 2 a native import kept status/image and <Values> as unknown.

    The REG_MULTI_SZ strings survived only inside that <Values>; the typed
    value was the space-joined attribute read as one string.
    """
    stored = {
        "scope": "user",
        "registry": [{
            "key": KEY,
            "hive": "HKEY_CURRENT_USER",
            "uid": "{A93FEDD0-81D5-457B-B2AE-2BEA9B045B45}",
            "unknown_attrs": [
                ["status", "UserMulti"], ["image", "2"], ["changed", "2026-10-08 9:28:03"],
            ],
            "unknown_props_children": [
                "<Values><Value>one</Value><Value>two</Value></Values>"
            ],
            "value": {
                "name": "UserMulti",
                "value": ["one two"],
                "registry_type": "REG_MULTI_SZ",
                "action": "update",
                "unknown_attrs": [["displayDecimal", "0"]],
            },
        }],
    }
    restored = gpp_collection_from_dict(stored).registry[0]
    assert restored.value.value == ["one", "two"]
    assert restored.unknown_attrs == (("changed", "2026-10-08 9:28:03"),)
    assert restored.unknown_props_children == ()
    studio = serialize_gpp_registry(GppCollection(scope="user", registry=(restored,)))
    _assert_same_element(
        ET.fromstring(studio)[0], _native_items("User")[0]
    )


def test_an_imported_display_radix_is_preserved_in_place() -> None:
    reg = parse_gpp_registry(_one('displayDecimal="1" type="REG_DWORD" value="0000002A"/>'))[0]
    assert reg.value.unknown_attrs == (("displayDecimal", "1"),)
    props = ET.fromstring(
        serialize_gpp_registry(GppCollection(scope="computer", registry=(replace(reg),)))
    ).find("Registry/Properties")
    assert props is not None
    assert list(props.attrib)[:3] == ["action", "displayDecimal", "default"]
    assert props.attrib["displayDecimal"] == "1"


def test_validation_error_lists_every_refusal_reason() -> None:
    """Two unmeasured items in one GPO are both named, not just the first."""
    gpo = _gpo(GppCollection(scope="computer", registry=(
        GppRegistry(key=KEY, value=UNMEASURED["delete"]),
        GppRegistry(key=KEY, value=UNMEASURED["binary"]),
    )))
    with pytest.raises(ValidationError) as caught:
        gpmc_backup_bundle(gpo)
    message = caught.value.issues[0].message
    assert "the Delete action" in message
    assert "REG_BINARY" in message
