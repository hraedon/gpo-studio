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
from typing import Any

import pytest

from gpo_studio.export import (
    _GPP_EXTENSION_PROFILES,
    extension_registration,
    gpmc_backup_bundle,
    native_backup_refusal,
)
from gpo_studio.gpp import (
    GppCollection,
    GppCommonOptions,
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

#: GPP Registry captures live in their own corpus root beside native-gpp-gpmc,
#: with their own sanitization record; report parity lists them explicitly.
REGISTRY_CORPUS = Path(__file__).parent / "fixtures" / "native-gpp-registry-gpmc"
#: The GPMC-editor captures of the other families.
EDITOR_CORPUS = Path(__file__).parent / "fixtures" / "native-gpp-gpmc"
CAPTURE = REGISTRY_CORPUS / "WI01A-Registry-GPMC"
CONTENT_ROOT = next(CAPTURE.glob("*/DomainSysvol/GPO"))
#: The revision-2 capture: Delete, REG_BINARY and key-only items besides the
#: revision-1 five.
SHAPES = CAPTURE.parent / "WI01A-RegistryShapes-GPMC"
SHAPES_ROOT = next(SHAPES.glob("*/DomainSysvol/GPO"))
KEY = r"Software\GPOStudio\GppRegistry"

#: What the capture script asked Windows to author (its ``$items`` table), by
#: side. The Delete item is absent: it failed to author in revision 1
#: (capture.json); revision 2 (SHAPES, below) measured it.
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

#: Common options Studio always writes explicitly, with the EXACT values that
#: say what the native items' absence says. The cmdlet wrote only ``disabled``.
#: Reading an absent ``removePolicy``/``userContext`` as 0 and an absent
#: ``bypassErrors`` as 0 (stop on error) is Studio's existing interpretation,
#: shared with the certified families -- not something these captures measure
#: (no capture records the extension's behaviour). What IS asserted is that
#: Studio writes exactly these values for a model with the native meaning; a
#: writer emitting "1" for any of them fails (batch-2 review P2).
STUDIO_EXPLICIT_COMMON = {"removePolicy": "0", "userContext": "0", "bypassErrors": "0"}
#: The common options a native cmdlet item means under that reading.
NATIVE_COMMON = GppCommonOptions(stop_on_error=True)


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
                common=NATIVE_COMMON,
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
        # Only the three explicit common options are set aside, and each must
        # carry exactly its expected value; native has none of them.
        explicit = {k: v for k, v in studio_attrs if k in STUDIO_EXPLICIT_COMMON}
        assert explicit == STUDIO_EXPLICIT_COMMON, where
        assert not set(STUDIO_EXPLICIT_COMMON) & set(native.attrib), where
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
    """Every action's image read straight off the native items of both captures."""
    measured = {
        native.find("Properties").attrib["action"]: native.attrib["image"]  # type: ignore[union-attr]
        for root in (CONTENT_ROOT, SHAPES_ROOT)
        for side in ("Machine", "User")
        for native in ET.fromstring(
            (root / side / "Preferences" / "Registry" / "Registry.xml").read_bytes()
        )
    }
    assert measured == {"C": "0", "U": "2", "R": "1", "D": "3"}
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
        for path in EDITOR_CORPUS.glob("*/*/DomainSysvol/GPO/*/Preferences/*/*.xml")
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
# Revision-2 capture: Delete, REG_BINARY and key-only (WI01A-RegistryShapes-GPMC)
# ---------------------------------------------------------------------------

#: The revision-2 script's Machine items that authored, in file order. Its
#: default-value item did not: "A parameter cannot be found that matches
#: parameter name 'Default'" (capture.json), so that shape stays unmeasured.
SHAPES_MACHINE: list[tuple[str, str, str, str | int | list[str], str]] = [
    (KEY, "CreateString", "REG_SZ", "alpha", "create"),
    (KEY, "UpdateDword", "REG_DWORD", 42, "update"),
    (KEY, "ReplaceExpand", "REG_EXPAND_SZ", "%SystemRoot%\\x", "replace"),
    (KEY, "DeleteMe", "REG_SZ", "gone", "delete"),
    # Authored as [byte[]](0xCA,0xFE,0x00,0x01).
    (KEY, "CreateBinary", "REG_BINARY", "CAFE0001", "create"),
    # Authored with -Key alone and -Action Update.
    (KEY + "\\KeyOnly", "", "REG_SZ", "", "update"),
]


def _shapes_bytes(side: str) -> bytes:
    return (SHAPES_ROOT / side / "Preferences" / "Registry" / "Registry.xml").read_bytes()


def _shapes_model() -> tuple[GppRegistry, ...]:
    natives = list(ET.fromstring(_shapes_bytes("Machine")))
    return tuple(
        GppRegistry(
            key=key,
            hive="HKEY_LOCAL_MACHINE",
            uid=native.attrib["uid"],
            value=GppRegistryValue(
                name=name, value=value, registry_type=reg_type, action=action  # type: ignore[arg-type]
            ),
            unknown_attrs=(("changed", native.attrib["changed"]),),
            common=NATIVE_COMMON,
        )
        for (key, name, reg_type, value, action), native in zip(
            SHAPES_MACHINE, natives, strict=True
        )
    )


def test_revision_2_authoring_record() -> None:
    capture = json.loads((SHAPES / "capture.json").read_text(encoding="utf-8-sig"))
    outcome = {row["value"]: row["ok"] for row in capture["authoring"]}
    assert outcome == {
        "CreateString": True, "UpdateDword": True, "ReplaceExpand": True,
        "UserMulti": True, "UserQword": True, "DeleteMe": True,
        "CreateBinary": True, "key-only": True, "default-value": False,
    }
    client, tool = _GPP_EXTENSION_PROFILES["Registry"]
    assert capture["ad"]["machine"] == capture["ad"]["user"] == f"[{client}{tool}]"


def test_revision_2_native_bytes_parse_to_what_was_authored() -> None:
    parsed = parse_gpp_registry(_shapes_bytes("Machine"))
    got = [
        (r.key, r.value.name, r.value.registry_type, r.value.value, r.value.action)
        for r in parsed
    ]
    assert got == SHAPES_MACHINE


def test_writer_matches_the_revision_2_items_from_an_authored_model() -> None:
    studio = serialize_gpp_registry(GppCollection(scope="computer", registry=_shapes_model()))
    _assert_same_element(ET.fromstring(studio), ET.fromstring(_shapes_bytes("Machine")))


@pytest.mark.parametrize("side", ["Machine", "User"])
def test_writer_matches_the_revision_2_items_after_an_import_and_edit(side: str) -> None:
    collection = parse_gpp_collection(
        SCOPE[side],  # type: ignore[arg-type]
        {"Registry/Registry.xml": _shapes_bytes(side)},
    )
    studio = serialize_gpp(mark_edited(collection))["Registry/Registry.xml"]
    _assert_same_element(ET.fromstring(studio), ET.fromstring(_shapes_bytes(side)))


def test_a_key_only_item_with_studios_empty_type_is_written_as_windows_types_it() -> None:
    """Studio's model also allows ``registry_type=""`` for a key-only item."""
    native = list(ET.fromstring(_shapes_bytes("Machine")))[-1]
    reg = replace(
        _shapes_model()[-1],
        value=GppRegistryValue(name="", value="", registry_type="", action="update"),
    )
    elem = ET.fromstring(
        serialize_gpp_registry(GppCollection(scope="computer", registry=(reg,)))
    )[0]
    _assert_same_element(elem, native)


@pytest.mark.parametrize("authored", ["CAFE0001", "ca fe 00 01", "CA FE 00 01", "cafe0001"])
def test_binary_is_written_as_windows_wrote_it(authored: str) -> None:
    """Measured: bytes CA FE 00 01 are ``CAFE0001`` -- upper case, no separators."""
    reg = GppRegistry(
        key=KEY,
        value=GppRegistryValue(name="B", value=authored, registry_type="REG_BINARY"),
    )
    props = ET.fromstring(
        serialize_gpp_registry(GppCollection(scope="computer", registry=(reg,)))
    ).find("Registry/Properties")
    assert props is not None
    native = list(ET.fromstring(_shapes_bytes("Machine")))[4].find("Properties")
    assert native is not None
    assert props.attrib["value"] == native.attrib["value"] == "CAFE0001"


@pytest.mark.parametrize("value", ["CAFE0", "CA FE", "CAFG"])
def test_binary_that_is_not_whole_hex_bytes_is_refused_on_read(value: str) -> None:
    with pytest.raises(GppError):
        parse_gpp_registry(_one(f'type="REG_BINARY" value="{value}"/>'))


def test_revision_2_report_and_backup_agree_through_studio() -> None:
    differences = compare_preferences(
        summary_from_backup(SHAPES_ROOT),
        summary_from_gpmc_report(SHAPES / "gpreport-verify.xml"),
    )
    assert differences == (), [d.describe() for d in differences]


# ---------------------------------------------------------------------------
# The action x type matrix (WI01A-RegistryMatrix-GPMC): every pair, whole items
# ---------------------------------------------------------------------------

MATRIX = REGISTRY_CORPUS / "WI01A-RegistryMatrix-GPMC"
MATRIX_ROOT = next(MATRIX.glob("*/DomainSysvol/GPO"))
MATRIX_ACTIONS = ("Create", "Replace", "Update", "Delete")
#: The capture script's `$values` table, by its -Type name.
MATRIX_VALUES: dict[str, tuple[str, str | int | list[str]]] = {
    "String": ("REG_SZ", "alpha"),
    "ExpandString": ("REG_EXPAND_SZ", "%SystemRoot%\\x"),
    "DWord": ("REG_DWORD", 42),
    "QWord": ("REG_QWORD", 4294967296),
    "MultiString": ("REG_MULTI_SZ", ["one", "two"]),
    # [byte[]](0xCA,0xFE,0x00,0x01)
    "Binary": ("REG_BINARY", "CAFE0001"),
}


def _matrix_bytes(side: str) -> bytes:
    return (MATRIX_ROOT / side / "Preferences" / "Registry" / "Registry.xml").read_bytes()


def _matrix_authored() -> dict[str, list[tuple[str, str, str, str | int | list[str], str]]]:
    """The capture script's loops, in its order: (key, name, type, value, action)."""
    machine = [
        (rf"Software\GPOStudio\GppMatrix\{action}", f"{action}{kind}", reg_type, value,
         action.lower())
        for action in MATRIX_ACTIONS
        for kind, (reg_type, value) in MATRIX_VALUES.items()
    ]
    user = [
        (rf"Software\GPOStudio\GppMatrixKeys\{action}", "", "REG_SZ", "", action.lower())
        for action in MATRIX_ACTIONS
    ]
    return {"Machine": machine, "User": user}


def _matrix_model(side: str) -> tuple[GppRegistry, ...]:
    hive = HIVE[side]
    natives = list(ET.fromstring(_matrix_bytes(side)))
    return tuple(
        GppRegistry(
            key=key,
            hive=hive,
            uid=native.attrib["uid"],
            value=GppRegistryValue(
                name=name, value=value, registry_type=reg_type, action=action  # type: ignore[arg-type]
            ),
            unknown_attrs=(("changed", native.attrib["changed"]),),
            common=NATIVE_COMMON,
        )
        for (key, name, reg_type, value, action), native in zip(
            _matrix_authored()[side], natives, strict=True
        )
    )


def test_every_matrix_item_authored() -> None:
    capture = json.loads((MATRIX / "capture.json").read_text(encoding="utf-8-sig"))
    assert len(capture["authoring"]) == 28
    assert all(row["ok"] for row in capture["authoring"])
    assert capture["error"] is None
    client, tool = _GPP_EXTENSION_PROFILES["Registry"]
    assert capture["ad"]["machine"] == capture["ad"]["user"] == f"[{client}{tool}]"


@pytest.mark.parametrize("side", ["Machine", "User"])
def test_matrix_bytes_parse_to_what_was_authored(side: str) -> None:
    parsed = parse_gpp_registry(_matrix_bytes(side))
    got = [
        (r.key, r.value.name, r.value.registry_type, r.value.value, r.value.action)
        for r in parsed
    ]
    assert got == _matrix_authored()[side]


@pytest.mark.parametrize("side", ["Machine", "User"])
def test_writer_matches_every_matrix_item_from_an_authored_model(side: str) -> None:
    """Each of the 28 items, attribute set and order, against Windows' bytes."""
    collection = GppCollection(scope=SCOPE[side], registry=_matrix_model(side))  # type: ignore[arg-type]
    studio = ET.fromstring(serialize_gpp_registry(collection))
    native = ET.fromstring(_matrix_bytes(side))
    assert len(studio) == len(native) == (24 if side == "Machine" else 4)
    _assert_same_element(studio, native)


@pytest.mark.parametrize("side", ["Machine", "User"])
def test_writer_matches_every_matrix_item_after_an_import_and_edit(side: str) -> None:
    collection = parse_gpp_collection(
        SCOPE[side],  # type: ignore[arg-type]
        {"Registry/Registry.xml": _matrix_bytes(side)},
    )
    studio = serialize_gpp(mark_edited(collection))["Registry/Registry.xml"]
    _assert_same_element(ET.fromstring(studio), ET.fromstring(_matrix_bytes(side)))


def _shape_of(props: ET.Element) -> tuple[str, str]:
    action = {"C": "create", "R": "replace", "U": "update", "D": "delete"}[props.attrib["action"]]
    shape = "key-only" if not props.attrib["name"] else props.attrib["type"]
    return action, shape


def test_the_measured_shape_set_is_exactly_what_the_matrix_captured() -> None:
    """`_MEASURED_GPP_REGISTRY_SHAPES` is read off Windows' bytes, not reasoned.

    Adding a pair to the set without a capture, or a capture losing a pair,
    fails here.
    """
    from gpo_studio.gpp import _MEASURED_GPP_REGISTRY_SHAPES

    captured = {
        _shape_of(item.find("Properties"))  # type: ignore[arg-type]
        for side in ("Machine", "User")
        for item in ET.fromstring(_matrix_bytes(side))
    }
    assert len(captured) == 28
    assert set(_MEASURED_GPP_REGISTRY_SHAPES) == captured


def test_matrix_image_codes_are_one_per_action() -> None:
    images = {
        (_shape_of(item.find("Properties"))[0], item.attrib["image"])  # type: ignore[arg-type]
        for side in ("Machine", "User")
        for item in ET.fromstring(_matrix_bytes(side))
    }
    assert images == {("create", "0"), ("replace", "1"), ("update", "2"), ("delete", "3")}


def test_matrix_report_and_backup_agree_through_studio() -> None:
    differences = compare_preferences(
        summary_from_backup(MATRIX_ROOT),
        summary_from_gpmc_report(MATRIX / "gpreport-verify.xml"),
    )
    assert differences == (), [d.describe() for d in differences]


def test_the_whole_matrix_exports_and_publishes(tmp_path: Path) -> None:
    gpo = _gpo(
        GppCollection(scope="computer", registry=_matrix_model("Machine")),
        GppCollection(scope="user", registry=_matrix_model("User")),
    )
    assert native_backup_refusal(gpo) is None
    assert extension_registration(gpo).unmeasured_shapes == ()
    plan = generate_publication_plan(gpo)
    assert not any(step.operation == "unsupported_gpp_registry_shape" for step in plan.steps)
    with zipfile.ZipFile(io.BytesIO(gpmc_backup_bundle(gpo))) as archive:
        archive.extractall(tmp_path)
    content_root = next(tmp_path.glob("*/DomainSysvol/GPO"))
    assert summary_from_backup(content_root) == summary_from_gpo(gpo)


@pytest.mark.parametrize(
    "capture", ["WI01A-Registry-GPMC", "WI01A-RegistryShapes-GPMC", "WI01A-RegistryMatrix-GPMC"]
)
def test_each_registry_capture_imports_through_the_api(
    capture: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The public backup-import path, as the inventory replay exercises the rest.

    These captures sit outside native-gpp-gpmc, so the inventory replay does not reach
    them; this does.
    """
    from contextlib import closing

    from fastapi.testclient import TestClient

    from gpo_studio.api import app
    from gpo_studio.store import WorkspaceStore, gpo_from_dict

    inbox = tmp_path / "inbox"
    import shutil

    shutil.copytree(REGISTRY_CORPUS / capture, inbox)
    monkeypatch.setenv("GPO_STUDIO_INBOX_DIR", str(inbox))
    with closing(WorkspaceStore(tmp_path / "import.db")) as store:
        monkeypatch.setattr(app.state, "store", store, raising=False)
        monkeypatch.setattr(app.state, "owns_store", False, raising=False)
        with TestClient(app) as client:
            response = client.post("/api/backups/import", json={
                "path": str(inbox), "actor": "registry-test", "reason": "native capture",
            })
            assert response.status_code == 201, response.text
            gpo = gpo_from_dict(response.json()["gpo"])
    natives = sum(
        len(ET.fromstring((side / "Preferences/Registry/Registry.xml").read_bytes()))
        for side in next((REGISTRY_CORPUS / capture).glob("*/DomainSysvol/GPO")).iterdir()
    )
    assert sum(len(c.registry) for c in gpo.gpp_collections) == natives


def test_the_registry_corpus_sanitization_record_covers_every_file() -> None:
    import hashlib

    record = json.loads((REGISTRY_CORPUS / "sanitization-record.json").read_text())
    tracked = {entry["relative_path"]: entry["sanitized_sha256"] for entry in record["files"]}
    on_disk = {
        path.relative_to(REGISTRY_CORPUS).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in REGISTRY_CORPUS.rglob("*")
        if path.is_file() and path.name != "sanitization-record.json"
    }
    assert tracked == on_disk


# ---------------------------------------------------------------------------
# Measured shapes export; the one unmeasured shape is refused everywhere
# ---------------------------------------------------------------------------


def test_every_revision_2_shape_exports_and_publishes(tmp_path: Path) -> None:
    gpo = _gpo(GppCollection(scope="computer", registry=_shapes_model()))
    assert native_backup_refusal(gpo) is None
    assert extension_registration(gpo).unmeasured_shapes == ()
    plan = generate_publication_plan(gpo)
    assert not any(step.operation == "unsupported_gpp_registry_shape" for step in plan.steps)
    with zipfile.ZipFile(io.BytesIO(gpmc_backup_bundle(gpo))) as archive:
        archive.extractall(tmp_path)
    content_root = next(tmp_path.glob("*/DomainSysvol/GPO"))
    assert summary_from_backup(content_root) == summary_from_gpo(gpo)


DEFAULT_VALUE = GppRegistryValue(name="", value="d", default=True)


def test_a_default_value_item_refuses_native_export_and_publication() -> None:
    """The GroupPolicy module cannot author one, so its wire form is unknown."""
    reg = GppRegistry(key=KEY, value=DEFAULT_VALUE)
    gpo = _gpo(GppCollection(scope="computer", registry=(reg,)))
    assert gpp_registry_unmeasured_shapes(gpo.gpp_collections[0]) == (
        f"computer HKEY_LOCAL_MACHINE\\{KEY}: a default-value item",
    )

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
            # What that import stored: the native item's absent bypassErrors
            # read as stop-on-error.
            "common": {"stop_on_error": True},
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
        GppRegistry(key=KEY + "\\One", value=DEFAULT_VALUE),
        GppRegistry(key=KEY + "\\Two", value=DEFAULT_VALUE),
    )))
    with pytest.raises(ValidationError) as caught:
        gpmc_backup_bundle(gpo)
    message = caught.value.issues[0].message
    assert "One: a default-value item" in message
    assert "Two: a default-value item" in message


def test_a_key_only_item_typed_other_than_reg_sz_is_not_a_measured_shape() -> None:
    """Every captured key-only item is typed REG_SZ; anything else was never seen."""
    reg = GppRegistry(
        key=KEY, value=GppRegistryValue(name="", value="", registry_type="REG_DWORD")
    )
    shapes = gpp_registry_unmeasured_shapes(GppCollection(scope="user", registry=(reg,)))
    assert shapes == (f"user HKEY_LOCAL_MACHINE\\{KEY}: a key-only item typed REG_DWORD",)



# ---------------------------------------------------------------------------
# Batch-2 review: the WP-1B native-shape check really inspects Registry XML
# ---------------------------------------------------------------------------


def _all_native_items() -> list[ET.Element]:
    items: list[ET.Element] = []
    for path in REGISTRY_CORPUS.glob("*/*/DomainSysvol/GPO/*/Preferences/Registry/Registry.xml"):
        items.extend(ET.fromstring(path.read_bytes()))
    return items


def test_the_shape_constants_are_windows_own() -> None:
    """writer_conformance states the native shape; every captured item has it."""
    from gpo_studio import writer_conformance as wc

    items = _all_native_items()
    assert len(items) == 5 + 8 + 28
    for path in REGISTRY_CORPUS.glob("*/*/DomainSysvol/GPO/*/Preferences/Registry/Registry.xml"):
        root = ET.fromstring(path.read_bytes())
        assert (root.tag, root.get("clsid")) == wc.NATIVE_REGISTRY_ROOT
    for item in items:
        props = item.find("Properties")
        assert props is not None
        assert (item.tag, item.get("clsid")) == wc.NATIVE_REGISTRY_ITEM
        assert tuple(item.attrib) == wc.NATIVE_REGISTRY_ITEM_ATTRS
        assert tuple(props.attrib) == wc.NATIVE_REGISTRY_PROPS_ATTRS
        assert item.get("image") == wc.NATIVE_REGISTRY_IMAGES[props.attrib["action"]]
    assert not set(wc.STUDIO_REGISTRY_COMMON_ATTRS) & {a for i in items for a in i.attrib}


def test_studio_output_for_every_capture_has_no_shape_findings() -> None:
    from gpo_studio.writer_conformance import registry_shape_findings

    for path in REGISTRY_CORPUS.glob("*/*/DomainSysvol/GPO/*/Preferences/Registry/Registry.xml"):
        collection = mark_edited(
            parse_gpp_collection("computer", {"Registry/Registry.xml": path.read_bytes()})
        )
        assert registry_shape_findings(collection) == [], path


def _gpo_with_registry() -> GPO:
    return _gpo(GppCollection(scope="computer", registry=_matrix_model("Machine")))


@pytest.mark.parametrize(
    ("label", "mutate"),
    [
        ("not native at all", lambda data: b"<not-native-at-all/>"),
        ("wrong root clsid", lambda data: data.replace(b"{A3CCFC41", b"{00000000", 1)),
        (
            "lower-case dword",
            lambda data: data.replace(b'value="0000002A"', b'value="0000002a"', 1),
        ),
        ("decimal dword", lambda data: data.replace(b'value="0000002A"', b'value="42"', 1)),
        ("wrong image", lambda data: data.replace(b'image="3"', b'image="2"', 1)),
        (
            "props order",
            lambda data: data.replace(
                b'displayDecimal="0" default="0"', b'default="0" displayDecimal="0"', 1
            ),
        ),
        ("semicolon multi", lambda data: data.replace(b'value="one two"', b'value="one;two"', 1)),
        ("status not name", lambda data: data.replace(b'status="CreateString"', b'status="X"', 1)),
        (
            "synthetic attribute",
            lambda data: data.replace(b"<Registry ", b'<Registry bogus="1" ', 1),
        ),
    ],
)
def test_a_non_native_registry_writer_fails_the_shape_check(
    label: str, mutate: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reviewer's mutation, and more: the finalizer's native_shape check bites."""
    from gpo_studio import writer_conformance

    real = writer_conformance.serialize_gpp

    def mutated(collection: GppCollection) -> dict[str, bytes]:
        files = dict(real(collection))
        if "Registry/Registry.xml" in files:
            files["Registry/Registry.xml"] = mutate(files["Registry/Registry.xml"])
        return files

    assert writer_conformance.native_shape_findings(_gpo_with_registry()) == ()
    monkeypatch.setattr(writer_conformance, "serialize_gpp", mutated)
    assert writer_conformance.native_shape_findings(_gpo_with_registry()), label


def test_the_wp1b_finalizer_grades_a_non_native_registry_candidate_as_failing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end through the builder's expected.json: native_shape_matches_corpus."""
    import runpy

    from gpo_studio import writer_conformance

    builder = runpy.run_path(
        str(Path(__file__).parents[1] / "scripts/plan-033/build-wp1b-candidates.py")
    )
    gpo = next(f() for cid, _, f in builder["CANDIDATES"] if cid == "gppregistry-both")
    assert builder["_expected"](gpo)["native_shape_findings"] == []
    real = writer_conformance.serialize_gpp
    monkeypatch.setattr(
        writer_conformance,
        "serialize_gpp",
        lambda c: {**real(c), "Registry/Registry.xml": b"<not-native-at-all/>"},
    )
    monkeypatch.setitem(builder["_expected"].__globals__, "native_shape_findings",
                        writer_conformance.native_shape_findings)
    assert builder["_expected"](gpo)["native_shape_findings"]


# ---------------------------------------------------------------------------
# Batch-2 review: unmodeled <Values> content is refused, never dropped
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "values",
    [
        '<Values futureMode="1"><Value>a</Value></Values>',
        "<Values><Value>a</Value><Future/></Values>",
        '<Values><Value lang="x">a</Value></Values>',
        "<Values><Value>a<b/></Value></Values>",
        "<Values><Value>a</Value></Values><Values><Value>a</Value></Values>",
        "<Values>stray<Value>a</Value></Values>",
    ],
)
def test_unmodeled_values_content_is_refused(values: str) -> None:
    with pytest.raises(GppError):
        parse_gpp_registry(_one(f'type="REG_MULTI_SZ" value="a">{values}</Properties>'))


def test_unmodeled_values_content_under_another_type_is_refused() -> None:
    with pytest.raises(GppError):
        parse_gpp_registry(_one('type="REG_SZ" value="a"><Values><Future/></Values></Properties>'))


def test_the_report_rendering_empty_values_is_still_read() -> None:
    reg = parse_gpp_registry(_one('type="REG_SZ" value="a"><Values /></Properties>'))[0]
    assert reg.value.value == "a"


# ---------------------------------------------------------------------------
# Batch-2 review: list values and key-only values
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("registry_type", ["REG_SZ", "REG_EXPAND_SZ", "REG_BINARY"])
def test_a_list_for_a_non_multi_string_type_is_refused_everywhere(registry_type: str) -> None:
    from gpo_studio.api import GppRegistryValueData
    from gpo_studio.validation import validate_gpp_registry_value

    value = GppRegistryValue(name="N", value=["a", "b"], registry_type=registry_type)
    with pytest.raises(GppError, match="cannot be a list|must be a hexadecimal string"):
        serialize_gpp_registry(
            GppCollection(scope="computer", registry=(GppRegistry(key=KEY, value=value),))
        )
    assert any(i.code == "type_mismatch" for i in validate_gpp_registry_value(value, "v"))
    with pytest.raises(ValueError, match="cannot be a list"):
        GppRegistryValueData(name="N", value=["a", "b"], registry_type=registry_type)


def test_a_key_only_item_carrying_a_value_is_not_a_measured_shape() -> None:
    reg = GppRegistry(
        key=KEY, value=GppRegistryValue(name="", value="junk", registry_type="REG_SZ")
    )
    gpo = _gpo(GppCollection(scope="computer", registry=(reg,)))
    refusal = native_backup_refusal(gpo)
    assert refusal is not None and refusal.code == "unmeasured_gpp_registry_shape"
    assert "a key-only item carrying a value" in refusal.message
