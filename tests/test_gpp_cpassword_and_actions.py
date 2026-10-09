"""A cpassword never travels in any form; a registry item's action edit is written.

WI-080 review (second reviewer):

* ``cpassword`` as an ELEMENT passed every check, which read attribute names
  only: a capture holding ``<cpassword>`` in a printer's ``Properties``
  imported, stored and was written by ``export.zip``, and so was one in a
  stored retained element. It predated WI-080 for data that never touches a
  retained element too (raw unknown children the API accepts). Every check now
  covers attributes and elements, any depth, any case, any namespace: import,
  the API's inputs, load, and export (``cpassword_detected``).
* ``GppRegistry.action`` (the action the workbench shows and edits) was never
  written; the writer writes ``value.action``. An API edit of it is now applied
  to the value, and an import reads it from the value.
"""

from __future__ import annotations

import base64
import io
import shutil
import xml.etree.ElementTree as ET
import zipfile
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from gpo_studio.api import app
from gpo_studio.backup_inventory import inventory_from_dict
from gpo_studio.gpp import (
    GppCollection,
    GppError,
    GppGroup,
    GppRegistry,
    GppRegistryValue,
    contains_cpassword,
    ensure_editor_ids,
    gpp_collection_from_dict,
    gpp_collection_to_dict,
    parse_gpp_collection,
)
from gpo_studio.model import StudioError
from gpo_studio.store import WorkspaceStore

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "tests/fixtures/native-gpp-gpmc"
PRINTERS_CAPTURE = NATIVE / "WI01A-Printers-GPMC"
(PRINTERS,) = PRINTERS_CAPTURE.glob("*/DomainSysvol/GPO/User/Preferences/Printers/Printers.xml")
REGISTRY_CAPTURE = ROOT / "tests/fixtures/native-gpp-registry-gpmc/WI01A-RegistryMatrix-GPMC"
_FORMS = {
    "element": b"<cpassword>SECRET</cpassword>",
    "upper-case element": b"<CPASSWORD>SECRET</CPASSWORD>",
    "namespaced element": b'<x:cpassword xmlns:x="urn:x">SECRET</x:cpassword>',
    "nested element": b"<Extra><cpassword>SECRET</cpassword></Extra>",
}


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("GPO_STUDIO_INBOX_DIR", str(tmp_path / "inbox"))
    with closing(WorkspaceStore(tmp_path / "api.db")) as store:
        monkeypatch.setattr(app.state, "store", store, raising=False)
        monkeypatch.setattr(app.state, "owns_store", False, raising=False)
        with TestClient(app, base_url="http://127.0.0.1") as test_client:
            yield test_client, store, tmp_path / "inbox"


@pytest.mark.parametrize("form", sorted(_FORMS))
def test_an_import_holding_a_cpassword_element_is_refused(client: Any, form: str) -> None:
    test_client, store, inbox = client
    shutil.copytree(PRINTERS_CAPTURE, inbox)
    (target,) = inbox.glob("*/DomainSysvol/GPO/User/Preferences/Printers/Printers.xml")
    data = target.read_bytes()
    planted = data.replace(b'port=""/>', b'port="">' + _FORMS[form] + b"</Properties>", 1)
    assert planted != data
    target.write_bytes(planted)
    response = test_client.post(
        "/api/backups/import",
        json={"path": str(inbox), "actor": "cpassword", "reason": "planted"},
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["message"] == "Invalid or unsafe backup content"
    assert store.list_gpos() == []


@pytest.mark.parametrize("form", sorted(_FORMS))
def test_a_stored_native_element_holding_a_cpassword_element_is_refused(form: str) -> None:
    parsed = ensure_editor_ids(
        parse_gpp_collection("user", {"Printers/Printers.xml": PRINTERS.read_bytes()})
    )
    data = gpp_collection_to_dict(parsed)
    native = data["printers"][0]["native_xml"]
    data["printers"][0]["native_xml"] = native.replace(
        "</Properties>", _FORMS[form].decode() + "</Properties>", 1
    ).replace('port="" />', 'port="">' + _FORMS[form].decode() + "</Properties>", 1)
    assert "SECRET" in data["printers"][0]["native_xml"]
    with pytest.raises(GppError, match="cpassword"):
        gpp_collection_from_dict(data)


def _group_payload(**changes: Any) -> dict[str, Any]:
    return {"name": "Planted", **changes}


@pytest.mark.parametrize(
    ("where", "payload"),
    [
        ("unknown child", _group_payload(unknown_children=["<cpassword>SECRET</cpassword>"])),
        ("properties child", _group_payload(
            unknown_props_children=["<Extra><CPassword>SECRET</CPassword></Extra>"]
        )),
        ("unknown attribute", _group_payload(unknown_props_attrs=[["cpassword", "SECRET"]])),
        ("raw filter", _group_payload(ilt_filter={"items": [
            '<FilterComputer bool="AND" not="0" name="x" cpassword="SECRET"/>'
        ]})),
        ("member attribute", _group_payload(members=[
            {"sid": "S-1-5-32-544", "name": "Administrators",
             "unknown_attrs": [["cpassword", "SECRET"]]},
        ])),
    ],
)
def test_the_api_refuses_a_cpassword_in_any_form(client: Any, where: str, payload: Any) -> None:
    test_client, store, _inbox = client
    gpo = test_client.post(
        "/api/gpos", json={"name": "Planted", "actor": "cpassword", "reason": where},
    ).json()["gpo"]
    response = test_client.post(
        f"/api/gpos/{gpo['guid']}/preferences/groups",
        json={"scope": "computer", "group": payload, "actor": "cpassword", "reason": where,
              "expected_revision": gpo["revision"]},
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["issues"][0]["code"] == "cpassword_detected"
    assert store.get_gpo(gpo["guid"]).gpp_collections == ()


def test_an_export_refuses_a_cpassword_element_that_reached_the_store(client: Any) -> None:
    """Data that never passed the API (an older workspace, a direct caller)."""
    test_client, store, _inbox = client
    group = GppGroup(name="Old", unknown_props_children=("<cpassword>SECRET</cpassword>",))
    gpo = store.create_gpo(
        name="Old data", identity="cpassword", reason="seed",
        gpp_collections=(GppCollection(scope="computer", groups=(group,)),),
    )
    for route in ("export.zip", "gpmc-backup"):
        response = test_client.get(f"/api/gpos/{gpo.guid}/{route}")
        assert response.status_code == 422, (route, response.text)
        codes = {issue["code"] for issue in response.json()["error"]["issues"]}
        assert "cpassword_detected" in codes, (route, codes)
        assert b"SECRET" not in response.content


def test_a_backup_inventory_holding_a_cpassword_element_is_refused() -> None:
    planted = base64.b64encode(
        b'<GroupPolicyBackupScheme><cpassword>SECRET</cpassword></GroupPolicyBackupScheme>'
    ).decode()
    with pytest.raises(StudioError, match="cpassword"):
        inventory_from_dict({"backup_xml_base64": planted, "report_xml_base64": "", "files": []})


# ---------------------------------------------------------------------------
# The registry item's action
# ---------------------------------------------------------------------------


def test_an_import_reads_a_registry_items_action_from_its_value() -> None:
    (path,) = REGISTRY_CAPTURE.glob("*/DomainSysvol/GPO/Machine/Preferences/Registry/Registry.xml")
    collection = parse_gpp_collection("computer", {"Registry/Registry.xml": path.read_bytes()})
    pairs = {(reg.action, reg.value.action) for reg in collection.registry}
    assert pairs == {
        ("add", "create"), ("replace", "replace"), ("update", "update"), ("remove", "delete"),
    }


def _registry_payload(registry: dict[str, Any]) -> dict[str, Any]:
    payload = {key: registry[key] for key in (
        "key", "hive", "action", "value", "id", "uid", "ilt_filter", "unknown_attrs",
        "unknown_props_children", "unknown_children",
    )}
    payload["value"] = {key: registry["value"][key] for key in (
        "name", "value", "registry_type", "action", "default", "id", "unknown_attrs",
    )}
    return payload


def _exported_actions(test_client: TestClient, guid: str, side: str) -> list[str]:
    bundle = test_client.get(f"/api/gpos/{guid}/export.zip")
    assert bundle.status_code == 200, bundle.text
    with zipfile.ZipFile(io.BytesIO(bundle.content)) as archive:
        root = ET.fromstring(archive.read(f"{side}/Preferences/Registry/Registry.xml"))
    return [item.find("Properties").get("action", "") for item in root]  # type: ignore[union-attr]


def test_an_api_edit_of_an_imported_registry_items_action_is_written(client: Any) -> None:
    test_client, store, inbox = client
    shutil.copytree(REGISTRY_CAPTURE, inbox)
    imported = test_client.post(
        "/api/backups/import",
        json={"path": str(inbox), "actor": "action", "reason": "import"},
    )
    assert imported.status_code == 201, imported.text
    fork = test_client.post(
        f"/api/gpos/{imported.json()['gpo']['guid']}/fork",
        json={"name": "Action edit", "actor": "action", "reason": "fork"},
    ).json()["gpo"]
    computer = next(c for c in fork["gpp_collections"] if c["scope"] == "computer")
    index, registry = next(
        (i, r) for i, r in enumerate(computer["registry"]) if r["value"]["action"] == "create"
    )
    assert registry["action"] == "add"  # read from the value
    payload = _registry_payload(registry)
    payload["action"] = "remove"  # the workbench's action select; the value row untouched
    edited = test_client.put(
        f"/api/gpos/{fork['guid']}/preferences/registry/{registry['id']}",
        json={"scope": "computer", "registry": payload, "actor": "action", "reason": "edit",
              "expected_revision": fork["revision"]},
    )
    assert edited.status_code == 200, edited.text
    stored = next(c for c in store.get_gpo(fork["guid"]).gpp_collections
                  if c.scope == "computer").registry[index]
    assert (stored.action, stored.value.action) == ("remove", "delete")
    assert _exported_actions(test_client, fork["guid"], "Machine")[index] == "D"


def test_an_api_edit_of_an_authored_registry_items_action_is_written(client: Any) -> None:
    """An item stored before the fix reads 'update' whatever its value does."""
    test_client, store, _inbox = client
    item = GppRegistry(
        key=r"Software\Policies\GPOStudio\Action",
        value=GppRegistryValue(name="V", value="x", registry_type="REG_SZ", action="create"),
    )
    assert item.action == "update"
    gpo = store.create_gpo(
        name="Authored action", identity="action", reason="seed",
        gpp_collections=(GppCollection(scope="computer", registry=(item,)),),
    )
    served = test_client.get(f"/api/gpos/{gpo.guid}").json()["gpo"]
    registry = served["gpp_collections"][0]["registry"][0]
    payload = _registry_payload(registry)
    payload["action"] = "replace"
    edited = test_client.put(
        f"/api/gpos/{gpo.guid}/preferences/registry/{registry['id']}",
        json={"scope": "computer", "registry": payload, "actor": "action", "reason": "edit",
              "expected_revision": served["revision"]},
    )
    assert edited.status_code == 200, edited.text
    stored = store.get_gpo(gpo.guid).gpp_collections[0].registry[0]
    assert (stored.action, stored.value.action) == ("replace", "replace")
    assert _exported_actions(test_client, gpo.guid, "Machine") == ["R"]
    # An edit of the value's own action wins, and the item's follows it.
    registry = test_client.get(f"/api/gpos/{gpo.guid}").json()["gpo"]["gpp_collections"][0][
        "registry"
    ][0]
    payload = _registry_payload(registry)
    payload["value"]["action"] = "update"
    edited = test_client.put(
        f"/api/gpos/{gpo.guid}/preferences/registry/{registry['id']}",
        json={"scope": "computer", "registry": payload, "actor": "action", "reason": "edit",
              "expected_revision": edited.json()["gpo"]["revision"]},
    )
    assert edited.status_code == 200, edited.text
    stored = store.get_gpo(gpo.guid).gpp_collections[0].registry[0]
    assert (stored.action, stored.value.action) == ("update", "update")



# ---------------------------------------------------------------------------
# Encodings: the check runs on the parsed tree (second review, P1)
# ---------------------------------------------------------------------------

_ENCODINGS = {
    "utf-16-le with BOM": ("utf-16-le", b"\xff\xfe"),
    "utf-16-be with BOM": ("utf-16-be", b"\xfe\xff"),
    "utf-16-le without BOM": ("utf-16-le", b""),
    "utf-16-be without BOM": ("utf-16-be", b""),
    "utf-8 with BOM": ("utf-8", b"\xef\xbb\xbf"),
}


def _encoded(text: str, encoding: str, bom: bytes) -> bytes:
    declared = "utf-8" if encoding == "utf-8" else "utf-16"
    return bom + text.replace('encoding="utf-8"', f'encoding="{declared}"', 1).encode(encoding)


@pytest.mark.parametrize("label", sorted(_ENCODINGS))
def test_an_import_in_any_encoding_is_held_to_the_cpassword_ban(
    client: Any, label: str
) -> None:
    test_client, store, inbox = client
    encoding, bom = _ENCODINGS[label]
    shutil.copytree(PRINTERS_CAPTURE, inbox)
    (target,) = inbox.glob("*/DomainSysvol/GPO/User/Preferences/Printers/Printers.xml")
    text = PRINTERS.read_bytes().decode("utf-8")
    # The control: the same file, clean, imports in that encoding.
    target.write_bytes(_encoded(text, encoding, bom))
    clean = test_client.post(
        "/api/backups/import",
        json={"path": str(inbox), "actor": "encoding", "reason": "clean"},
    )
    assert clean.status_code == 201, clean.text
    planted = text.replace('port=""/>', 'port=""><cpassword>SECRET</cpassword></Properties>', 1)
    target.write_bytes(_encoded(planted, encoding, bom))
    response = test_client.post(
        "/api/backups/import",
        json={"path": str(inbox), "actor": "encoding", "reason": "planted"},
    )
    assert response.status_code == 422, response.text
    assert "SECRET" not in response.text
    assert len(store.list_gpos()) == 1  # only the clean import


@pytest.mark.parametrize("label", sorted(_ENCODINGS))
def test_contains_cpassword_reads_every_encoding(label: str) -> None:
    encoding, bom = _ENCODINGS[label]
    text = '<?xml version="1.0" encoding="utf-8"?><Printers><cpassword>S</cpassword></Printers>'
    assert contains_cpassword(_encoded(text, encoding, bom)) is True
    clean = (
        '<?xml version="1.0" encoding="utf-8"?>'
        "<Printers><Comment>cpassword-free</Comment></Printers>"
    )
    assert contains_cpassword(_encoded(clean, encoding, bom)) is False


@pytest.mark.parametrize("label", sorted(_ENCODINGS))
def test_a_backup_inventory_in_any_encoding_is_held_to_the_ban(label: str) -> None:
    encoding, bom = _ENCODINGS[label]
    text = (
        '<?xml version="1.0" encoding="utf-8"?>'
        "<GroupPolicyBackupScheme><cpassword>SECRET</cpassword></GroupPolicyBackupScheme>"
    )
    planted = base64.b64encode(_encoded(text, encoding, bom)).decode()
    with pytest.raises(StudioError, match="cpassword"):
        inventory_from_dict({"backup_xml_base64": planted, "report_xml_base64": "", "files": []})


# ---------------------------------------------------------------------------
# No retained store may hold a namespaced name (second review, P2)
# ---------------------------------------------------------------------------


def test_an_import_with_a_namespaced_unknown_attribute_is_refused(client: Any) -> None:
    test_client, store, inbox = client
    shutil.copytree(PRINTERS_CAPTURE, inbox)
    (target,) = inbox.glob("*/DomainSysvol/GPO/User/Preferences/Printers/Printers.xml")
    data = target.read_bytes()
    target.write_bytes(data.replace(
        b'<SharedPrinter clsid=', b'<SharedPrinter xmlns:x="urn:review" x:extra="1" clsid=', 1
    ))
    response = test_client.post(
        "/api/backups/import",
        json={"path": str(inbox), "actor": "namespace", "reason": "planted"},
    )
    assert response.status_code == 422, response.text
    assert store.list_gpos() == []


def _stored_printers() -> dict[str, Any]:
    return gpp_collection_to_dict(ensure_editor_ids(
        parse_gpp_collection("user", {"Printers/Printers.xml": PRINTERS.read_bytes()})
    ))


@pytest.mark.parametrize(
    "plant",
    [
        lambda d: d["printers"][0]["unknown_attrs"].append(["{urn:review}extra", "1"]),
        lambda d: d["printers"][0]["unknown_children"].append(
            '<x:Extra xmlns:x="urn:review"/>'
        ),
        lambda d: d["printers"][0]["unknown_props_children"].append(
            '<Extra xmlns="urn:review"/>'
        ),
        lambda d: d["printers_unknown_attrs"].append(["{urn:review}root", "1"]),
        lambda d: d["printers_unknown_children"].append('<Extra xmlns="urn:review"/>'),
        lambda d: d["printers"][0]["ilt_filter"]["items"].append(
            '<FilterX xmlns="urn:review" bool="AND" not="0"/>'
        ),
    ],
    ids=["item attribute", "item child", "properties child", "root attribute",
         "root child", "raw filter"],
)
def test_a_stored_namespaced_name_in_any_store_is_refused_on_load(plant: Any) -> None:
    data = _stored_printers()
    data["printers"][0]["native_xml"] = ""
    plant(data)
    with pytest.raises(GppError, match="XML namespace"):
        gpp_collection_from_dict(data)


@pytest.mark.parametrize(
    "payload",
    [
        _group_payload(unknown_attrs=[["{urn:review}a", "1"]]),
        _group_payload(unknown_children=['<Extra xmlns="urn:review"/>']),
        _group_payload(members=[{"sid": "S-1-5-32-544", "name": "Administrators",
                                 "unknown_attrs": [["{urn:review}a", "1"]]}]),
    ],
    ids=["item attribute", "item child", "member attribute"],
)
def test_the_api_refuses_a_namespaced_name(client: Any, payload: Any) -> None:
    test_client, store, _inbox = client
    gpo = test_client.post(
        "/api/gpos", json={"name": "Namespaced", "actor": "namespace", "reason": "add"},
    ).json()["gpo"]
    response = test_client.post(
        f"/api/gpos/{gpo['guid']}/preferences/groups",
        json={"scope": "computer", "group": payload, "actor": "namespace", "reason": "add",
              "expected_revision": gpo["revision"]},
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["issues"][0]["code"] == "xml_namespace_refused"
