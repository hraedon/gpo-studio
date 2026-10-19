"""Every identity field, every hostile shape, every route in: refused, or never shipped.

The final-r6 and r7 reviews found an LF, a non-breaking space, a lowercase SID
prefix or a dangling DN escape reaching Groups.xml, a task payload or the
PowerShell plan through one field after its neighbour had been fixed (member
SID beside group SID, Task v1 beside TaskV2, an imported payload beside the
typed projection). This test is the inventory made executable: for each field
that carries a SID, a DN, a GUID, a time or an account/group name, it feeds
each hostile shape through each route that can set it

* (a) the API mutation,
* (b) GPMC backup import (the native capture with the value patched in),
* (c) gpo-lens estate import,

and asserts the route refuses it, or else that no output Studio writes for
the GPO (export.zip, the GPMC backup, the PowerShell plan) carries it. Each
route has a positive control (the valid value is accepted and shipped), so a
dead route cannot pass as a refusal.

Section (d) probes the writer's own table (`emit_integrity.GPP_XML`) through
import: fields no typed model writes (a retained native element's attributes,
an item's uid, FilterRunOnce@id, a v1 Task's triggerTime, a TaskV2 payload's
UserId/GroupId/boundaries/command), each with a written-when-valid control and
an empty-payload case. That the table covers everything the writers emit is
`tests/test_emit_integrity.py`, which derives the names from the writers.
A last test holds DeepSeek final-r7 N4 and final-r8 N5: a value stored before
a rule tightened blocks export but not its own repair, and is not a licence
to swap it for a different bad value.
"""

from __future__ import annotations

import io
import shutil
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Callable, Iterator
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from gpo_studio import identity_values
from gpo_studio.api import app
from gpo_studio.store import WorkspaceStore

ROOT = Path(__file__).resolve().parents[1]
CAPTURES = ROOT / "tests" / "fixtures" / "native-gpp-gpmc"

#: name -> how it turns a valid value into a hostile one.
SHAPES: dict[str, Callable[[str], str]] = {
    "lf": lambda v: v + "\n",
    "crlf": lambda v: v + "\r\n",
    "nul": lambda v: v + "\x00",
    "nbsp": lambda v: v + " ",
    "line-separator": lambda v: v + " ",
    "rlo": lambda v: v[:2] + "‮" + v[2:],
    "zwsp": lambda v: v + "​",
    "leading-space": lambda v: " " + v,
    "trailing-space": lambda v: v + " ",
}
#: Shapes that only make sense for some kinds.
KIND_SHAPES: dict[str, dict[str, Callable[[str], str]]] = {
    "sid": {"lowercase": str.lower, "junk-suffix": lambda v: v + "x"},
    "dn": {
        "dangling-escape": lambda v: v + "\\",
        "escaped-separator": lambda v: v.replace(",", "\\,", 1),
        "unescaped-leading-rdn-space": lambda v: v.replace("=", "= ", 1),
    },
    "guid": {"junk-suffix": lambda v: v + "x", "one-brace": lambda v: "{" + v},
    "datetime": {"junk-suffix": lambda v: v + "x", "not-a-time": lambda v: "tomorrow"},
    "name": {},
    "line": {},
}
#: Free text may hold NBSP and surrounding spaces; it may not hold a control,
#: format or separator character.
_LINE_SHAPES = ("crlf", "lf", "line-separator", "nul", "rlo", "zwsp")


def _shape_names(kind: str) -> list[str]:
    if kind == "line":
        return sorted(_LINE_SHAPES)
    return sorted({**SHAPES, **KIND_SHAPES[kind]})


def _cases(fields: dict[str, tuple[str, str]]) -> list[Any]:
    cases = []
    for field, (kind, _valid) in sorted(fields.items()):
        for shape in _shape_names(kind):
            cases.append(pytest.param(field, shape, id=f"{field}-{shape}"))
    return cases


def _hostile(kind: str, valid: str, shape: str) -> str:
    value = {**SHAPES, **KIND_SHAPES[kind]}[shape](valid)
    assert value != valid
    return value


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    monkeypatch.setenv("GPO_STUDIO_INBOX_DIR", str(tmp_path / "inbox"))
    with closing(WorkspaceStore(tmp_path / "api.db")) as store:
        monkeypatch.setattr(app.state, "store", store, raising=False)
        monkeypatch.setattr(app.state, "owns_store", False, raising=False)
        with TestClient(app, base_url="http://127.0.0.1") as test_client:
            yield test_client, tmp_path / "inbox"


def _outputs(test_client: TestClient, guid: str) -> list[tuple[str, bytes]]:
    """Every file Studio would ship for the GPO (a refused route yields none)."""
    found: list[tuple[str, bytes]] = []
    for route in ("export.zip", "gpmc-backup"):
        response = test_client.get(f"/api/gpos/{guid}/{route}")
        if response.status_code == 200:
            with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                found += [(n, archive.read(n)) for n in archive.namelist()]
    plan = test_client.get(f"/api/gpos/{guid}/plan.ps1")
    if plan.status_code == 200:
        found.append(("plan.ps1", plan.content))
    return found


def _carried(outputs: list[tuple[str, bytes]], value: str) -> list[str]:
    """Files whose XML attributes or text hold the value exactly, or that contain it."""
    carriers = []
    encoded = value.encode("utf-8")
    for name, data in outputs:
        if name.lower().endswith(".xml"):
            try:
                root = ET.fromstring(data)
            except ET.ParseError:
                carriers.append(name)
                continue
            for element in root.iter():
                if value in element.attrib.values() or element.text == value:
                    carriers.append(name)
                    break
        elif encoded in data:
            carriers.append(name)
    return carriers


def _refused_or_never_shipped(
    test_client: TestClient, status: int, guid: str | None, value: str
) -> None:
    if status >= 400:
        return
    assert status in (200, 201), status
    assert guid is not None
    carried = _carried(_outputs(test_client, guid), value)
    assert not carried, f"accepted and shipped {value!r} in {carried}"


# --- (a) the API -----------------------------------------------------------------

API_FIELDS: dict[str, tuple[str, str]] = {
    "group-sid": ("sid", "S-1-5-32-544"),
    "group-name": ("name", "Administrators"),
    "member-sid": ("sid", "S-1-5-21-1-2-3-500"),
    "member-name": ("name", "DOMAIN\\Admins"),
    "link-target": ("dn", "OU=Lab,DC=studio,DC=local"),
    "filter-sid": ("sid", "S-1-5-21-1-2-3-512"),
    "filter-principal": ("name", "DOMAIN\\Admins"),
}


def _api_mutation(test_client: TestClient, field: str, value: str) -> tuple[int, str]:
    gpo = test_client.post(
        "/api/gpos", json={"name": "Identity probe", "actor": "probe", "reason": field}
    ).json()["gpo"]
    common = {"actor": "probe", "reason": field, "expected_revision": gpo["revision"]}
    group = {
        "name": "Administrators",
        "sid": "S-1-5-32-544",
        "action": "update",
        "members": [{"sid": "S-1-5-21-1-2-3-500", "name": "DOMAIN\\Admins", "action": "add"}],
    }
    if field == "group-sid":
        group["sid"] = value
    elif field == "group-name":
        group["name"] = value
    elif field == "member-sid":
        group["members"][0]["sid"] = value
    elif field == "member-name":
        group["members"][0]["name"] = value
    if field.startswith(("group", "member")):
        response = test_client.post(
            f"/api/gpos/{gpo['guid']}/preferences/groups",
            json={"scope": "computer", "group": group, **common},
        )
    elif field == "link-target":
        response = test_client.post(
            f"/api/gpos/{gpo['guid']}/links",
            json={"link": {"target": value, "order": 1}, **common},
        )
    else:
        filter_ = {"principal": "DOMAIN\\Admins", "sid": "S-1-5-21-1-2-3-512"}
        filter_["sid" if field == "filter-sid" else "principal"] = value
        response = test_client.post(
            f"/api/gpos/{gpo['guid']}/security-filters", json={"filter": filter_, **common}
        )
    return response.status_code, gpo["guid"]


@pytest.mark.parametrize(("field", "shape"), _cases(API_FIELDS))
def test_the_api_refuses_or_never_ships_a_hostile_identity(
    client: Any, field: str, shape: str
) -> None:
    test_client, _inbox = client
    kind, valid = API_FIELDS[field]
    value = _hostile(kind, valid, shape)
    status, guid = _api_mutation(test_client, field, value)
    _refused_or_never_shipped(test_client, status, guid, value)


# --- (b) GPMC backup import ------------------------------------------------------

#: field -> (kind, capture, file glob, valid text as it appears, how it is written).
BACKUP_FIELDS: dict[str, tuple[str, str, str, str, str]] = {
    "group-sid": (
        "sid",
        "WI01A-LocalGroups-GPMC",
        "Machine/Preferences/Groups/Groups.xml",
        'groupSid="S-1-5-32-544"',
        "attr",
    ),
    "group-name": (
        "name",
        "WI01A-LocalGroups-GPMC",
        "Machine/Preferences/Groups/Groups.xml",
        'groupName="Administrators (built-in)"',
        "attr",
    ),
    "member-sid": (
        "sid",
        "WI01A-LocalGroups-GPMC",
        "Machine/Preferences/Groups/Groups.xml",
        'sid="S-1-5-21-0000000000-0000000000-0000000000-5124"',
        "attr",
    ),
    "member-name": (
        "name",
        "WI01A-LocalGroups-GPMC",
        "Machine/Preferences/Groups/Groups.xml",
        'name="HRAENET\\svc-gpolens"',
        "attr",
    ),
    "task-start-boundary": (
        "datetime",
        "WI01A-SchedTasksFull-GPMC",
        "Machine/Preferences/ScheduledTasks/ScheduledTasks.xml",
        "<StartBoundary>2026-07-26T02:00:00</StartBoundary>",
        "text",
    ),
    "task-user-id": (
        "name",
        "WI01A-SchedTasksFull-GPMC",
        "Machine/Preferences/ScheduledTasks/ScheduledTasks.xml",
        "<UserId>%LogonDomain%\\%LogonUser%</UserId>",
        "text",
    ),
    "task-run-as": (
        "name",
        "WI01A-SchedTasksFull-GPMC",
        "Machine/Preferences/ScheduledTasks/ScheduledTasks.xml",
        'runAs="NT AUTHORITY\\System"',
        "attr",
    ),
    "ilt-ou": (
        "dn",
        "WI01A-NestedILT-GPMC",
        "User/Preferences/Drives/Drives.xml",
        'name="OU=Lab-WS2,OU=Lab-WS,OU=Workstations,DC=ad,DC=hraedon,DC=com"',
        "attr",
    ),
}


def _xml_escaped(value: str) -> str:
    """The value as XML that reads back exactly (character references for anything odd)."""
    return "".join(
        c if (0x20 < ord(c) < 0x7F and c not in "&<>\"'") or c == " " else f"&#x{ord(c):X};"
        for c in value
    )


def _inner(original: str, how: str) -> str:
    if how == "attr":
        return original.split('="', 1)[1][:-1]
    return original.split(">", 1)[1].rsplit("<", 1)[0]


@pytest.mark.parametrize(
    ("field", "shape"), _cases({k: (v[0], _inner(v[3], v[4])) for k, v in BACKUP_FIELDS.items()})
)
def test_a_backup_import_refuses_or_never_ships_a_hostile_identity(
    client: Any, field: str, shape: str
) -> None:
    test_client, inbox = client
    kind, capture, relative, original, how = BACKUP_FIELDS[field]
    valid = _inner(original, how)
    value = _hostile(kind, valid, shape)
    shutil.copytree(CAPTURES / capture, inbox)
    (target,) = inbox.glob(f"*/DomainSysvol/GPO/{relative}")
    data = target.read_text(encoding="utf-8")
    assert original in data, original
    target.write_text(
        data.replace(original, original.replace(valid, _xml_escaped(value)), 1),
        encoding="utf-8",
        newline="",
    )
    response = test_client.post(
        "/api/backups/import", json={"path": str(inbox), "actor": "probe", "reason": field}
    )
    guid = response.json()["gpo"]["guid"] if response.status_code == 201 else None
    _refused_or_never_shipped(test_client, response.status_code, guid, value)


# --- (c) estate import -------------------------------------------------------------

ESTATE_FIELDS: dict[str, tuple[str, str]] = {
    "gpo-guid": ("guid", "11111111-2222-3333-4444-555555555555"),
    "link-target": ("dn", "OU=Workstations,DC=corp,DC=example,DC=test"),
    "filter-principal": ("name", "CORP\\DomainAdmins"),
    "filter-sid": ("sid", "S-1-5-21-1-2-3-512"),
}


def _estate(field: str, value: str) -> dict[str, Any]:
    gpo: dict[str, Any] = {
        "guid": ESTATE_FIELDS["gpo-guid"][1],
        "display_name": "Estate probe",
        "domain": "corp.example.test",
        "settings": [],
        "links": [
            {
                "id": "l1",
                "target": ESTATE_FIELDS["link-target"][1],
                "enabled": True,
                "enforced": False,
                "order": 1,
            }
        ],
        "security_filters": [
            {
                "id": "sf1",
                "principal": ESTATE_FIELDS["filter-principal"][1],
                "sid": ESTATE_FIELDS["filter-sid"][1],
                "permission": "apply",
                "inheritable": True,
                "target_type": "group",
            }
        ],
        "wmi_filter": None,
    }
    if field == "gpo-guid":
        gpo["guid"] = value
    elif field == "link-target":
        gpo["links"][0]["target"] = value
    else:
        gpo["security_filters"][0]["sid" if field == "filter-sid" else "principal"] = value
    return {
        "kind": "gpo-lens-estate",
        "domain": "corp.example.test",
        "exported_at": "2026-07-12T10:00:00Z",
        "gpos": [gpo],
    }


def _estate_import(test_client: TestClient, field: str, value: str) -> tuple[int, list[str]]:
    """Import, then fork every imported GPO: the fork is what reaches an export."""
    response = test_client.post("/api/estate/import", json=_estate(field, value))
    guids: list[str] = []
    if response.status_code < 400:
        for row in test_client.get("/api/gpos").json()["items"]:
            guids.append(row["guid"])
            fork = test_client.post(
                f"/api/gpos/{row['guid']}/fork",
                json={"name": "Estate fork", "actor": "probe", "reason": field},
            )
            if fork.status_code == 201:
                guids.append(fork.json()["gpo"]["guid"])
    return response.status_code, guids


@pytest.mark.parametrize(("field", "shape"), _cases(ESTATE_FIELDS))
def test_an_estate_import_refuses_or_never_ships_a_hostile_identity(
    client: Any, field: str, shape: str
) -> None:
    test_client, _inbox = client
    kind, valid = ESTATE_FIELDS[field]
    value = _hostile(kind, valid, shape)
    status, guids = _estate_import(test_client, field, value)
    if status >= 400:
        return
    for guid in guids:
        _refused_or_never_shipped(test_client, status, guid, value)


# --- the shared validators themselves ------------------------------------------------


@pytest.mark.parametrize(
    ("check", "valid", "kind"),
    [
        (identity_values.sid_problem, "S-1-5-21-1-2-3-500", "sid"),
        (identity_values.dn_problem, "OU=Lab,DC=studio,DC=local", "dn"),
        (identity_values.guid_problem, "{11111111-2222-3333-4444-555555555555}", "guid"),
        (identity_values.datetime_problem, "2026-07-26T02:00:00", "datetime"),
        (identity_values.name_problem, "DOMAIN\\Admins", "name"),
    ],
    ids=["sid", "dn", "guid", "datetime", "name"],
)
def test_each_shared_validator_refuses_every_hostile_shape(
    check: Callable[[str], str | None], valid: str, kind: str
) -> None:
    assert check(valid) is None
    for shape in sorted({**SHAPES, **KIND_SHAPES[kind]}):
        value = _hostile(kind, valid, shape)
        assert check(value) is not None, (kind, shape, value)


@pytest.mark.parametrize(
    "dn",
    [
        "OU=Lab\\, Annex,DC=studio,DC=local",
        "OU=\\ Lab,DC=studio,DC=local",
        "OU=Lab\\2C Annex,DC=studio,DC=local",
        "CN=Users,DC=studio,DC=local",
    ],
)
def test_a_correctly_escaped_dn_is_valid(dn: str) -> None:
    assert identity_values.dn_problem(dn) is None


@pytest.mark.parametrize(
    "dn",
    [
        "OU=Lab\\0A,DC=studio,DC=local",
        "OU=Lab\\C2\\A0,DC=studio,DC=local",
        "OU=Lab\\ZZ,DC=studio,DC=local",
        "OU=La+b,DC=studio,DC=local",
        "OU=Lab",
    ],
)
def test_an_escape_that_decodes_to_a_refused_character_is_refused(dn: str) -> None:
    assert identity_values.dn_problem(dn) is not None


# --- positive controls: the routes above are live and the outputs are searched ------
#
# Without these, a mistyped route (404) or a fixture that no longer contains the
# patched text would make every hostile case above "refused" vacuously.


@pytest.mark.parametrize("field", sorted(API_FIELDS))
def test_the_api_accepts_and_ships_the_valid_value(client: Any, field: str) -> None:
    test_client, _inbox = client
    _kind, valid = API_FIELDS[field]
    status, guid = _api_mutation(test_client, field, valid)
    assert status in (200, 201), status
    assert _carried(_outputs(test_client, guid), valid), field


@pytest.mark.parametrize("field", sorted(BACKUP_FIELDS))
def test_a_backup_import_accepts_and_ships_the_valid_value(client: Any, field: str) -> None:
    test_client, inbox = client
    _kind, capture, _relative, original, how = BACKUP_FIELDS[field]
    shutil.copytree(CAPTURES / capture, inbox)
    response = test_client.post(
        "/api/backups/import", json={"path": str(inbox), "actor": "probe", "reason": field}
    )
    assert response.status_code == 201, response.text
    guid = response.json()["gpo"]["guid"]
    assert _carried(_outputs(test_client, guid), _inner(original, how).replace("&quot;", '"')), (
        field
    )


@pytest.mark.parametrize("field", sorted(ESTATE_FIELDS))
def test_an_estate_import_accepts_and_ships_the_valid_value(client: Any, field: str) -> None:
    test_client, _inbox = client
    valid = ESTATE_FIELDS[field][1]
    status, guids = _estate_import(test_client, field, valid)
    assert status in (200, 201), status
    assert len(guids) == 2, guids  # the imported GPO and its fork
    outputs = [o for guid in guids for o in _outputs(test_client, guid)]
    assert outputs, "neither the imported GPO nor its fork has an output to search"
    if field == "gpo-guid":
        assert valid in guids
    else:
        assert _carried(outputs, valid), field


# --- a store written before a rule tightened can still be repaired (DS N4) -----------


def _plant(test_client: TestClient, monkeypatch: pytest.MonkeyPatch, guid: str) -> None:
    """Write what a 1.1.0rc1 workspace could hold: two bad group SIDs and a bad link."""
    import gpo_studio.store as store_module

    with monkeypatch.context() as unchecked:
        unchecked.setattr(WorkspaceStore, "_validate_gpp", lambda *_a, **_k: None)
        unchecked.setattr(store_module, "_refuse_identity_issues", lambda *_a: None)
        for name, sid in (("Legacy one", "S-1-5-32-544 "), ("Legacy two", "S-1-5-32-545x")):
            revision = test_client.get(f"/api/gpos/{guid}").json()["gpo"]["revision"]
            planted = test_client.post(
                f"/api/gpos/{guid}/preferences/groups",
                json={
                    "scope": "computer",
                    "group": {"name": name, "sid": sid},
                    "actor": "rc1",
                    "reason": "legacy",
                    "expected_revision": revision,
                },
            )
            assert planted.status_code == 201, planted.text
        revision = test_client.get(f"/api/gpos/{guid}").json()["gpo"]["revision"]
        planted = test_client.post(
            f"/api/gpos/{guid}/links",
            json={
                "link": {"target": "OU=Lab,DC=studio,DC=local\\", "order": 1},
                "actor": "rc1",
                "reason": "legacy",
                "expected_revision": revision,
            },
        )
        assert planted.status_code == 201, planted.text


def _exports(test_client: TestClient, guid: str) -> set[int]:
    return {
        test_client.get(f"/api/gpos/{guid}/{route}").status_code
        for route in ("export.zip", "gpmc-backup", "plan.ps1")
    }


def test_a_stored_value_a_tightened_rule_refuses_is_correctable_through_the_api(
    client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    test_client, _inbox = client
    guid = test_client.post(
        "/api/gpos", json={"name": "Legacy", "actor": "probe", "reason": "N4"}
    ).json()["gpo"]["guid"]
    _plant(test_client, monkeypatch, guid)

    def gpo() -> dict[str, Any]:
        return test_client.get(f"/api/gpos/{guid}").json()["gpo"]

    def audit() -> dict[str, Any]:
        return {"actor": "probe", "reason": "repair", "expected_revision": gpo()["revision"]}

    def groups() -> dict[str, str]:
        (collection,) = gpo()["gpp_collections"]
        return {g["name"]: g["id"] for g in collection["groups"]}

    # The store still loads, and nothing it cannot ship leaves it.
    assert _exports(test_client, guid) == {422}

    route = f"/api/gpos/{guid}/preferences/groups"
    one, two = groups()["Legacy one"], groups()["Legacy two"]
    # Keeping a bad value is not an excuse to add another, or to swap it for a new one.
    added = test_client.post(
        route,
        json={"scope": "computer", "group": {"name": "New", "sid": "S-1-5-32-546\n"}, **audit()},
    )
    assert added.status_code == 422, added.text
    swapped = test_client.put(
        f"{route}/{one}",
        json={
            "scope": "computer",
            "group": {"name": "Legacy one", "sid": "S-1-5-32-544\n"},
            **audit(),
        },
    )
    assert swapped.status_code == 422, swapped.text
    # The same code at the same place, a different bad value: still a new error,
    # because the message names the value (DeepSeek final-r8 N5).
    same_kind = test_client.put(
        f"{route}/{one}",
        json={
            "scope": "computer",
            "group": {"name": "Legacy one", "sid": "S-1-5-32-599 "},
            **audit(),
        },
    )
    assert same_kind.status_code == 422, same_kind.text
    # Legacy values do not block an unrelated valid edit (it adds no new error).
    unrelated = test_client.post(
        route,
        json={"scope": "computer", "group": {"name": "Valid", "sid": "S-1-5-32-547"}, **audit()},
    )
    assert unrelated.status_code == 201, unrelated.text

    # The repairs: correct one, delete the other, correct the link.
    fixed = test_client.put(
        f"{route}/{one}",
        json={
            "scope": "computer",
            "group": {"name": "Legacy one", "sid": "S-1-5-32-544"},
            **audit(),
        },
    )
    assert fixed.status_code == 200, fixed.text
    assert _exports(test_client, guid) == {422}, "one bad SID and the link remain"
    deleted = test_client.request("DELETE", f"{route}/{two}", json={"scope": "computer", **audit()})
    assert deleted.status_code == 200, deleted.text
    assert _exports(test_client, guid) == {422}, "the link remains"
    (link,) = gpo()["links"]
    relinked = test_client.put(
        f"/api/gpos/{guid}/links/{link['id']}",
        json={"link": {"target": "OU=Lab,DC=studio,DC=local", "order": 1}, **audit()},
    )
    assert relinked.status_code == 200, relinked.text
    assert _exports(test_client, guid) == {200}


# --- (d) every value the writer emits, wherever it comes from ----------------------
#
# The typed fields are a projection: a retained native element, a v1 Task's
# triggerTime, an imported TaskV2's payload and an item's own uid are written as
# they are. So the writer checks the XML it writes, against
# `emit_integrity.GPP_XML` (final-r8; the completeness of that table is
# `tests/test_emit_integrity.py`, which derives it from the writers). These are
# probes into it: one source document per field, taken from a native capture
# where Windows wrote the field and edited into one where it did not, parsed as
# an import parses it. Final-r8's probes are among them: an item's uid,
# FilterRunOnce@id, a service name, a task's name and a power scheme's nameGuid.

_V = "@@V@@"
_TASK_V1 = (
    '<Task clsid="{2DEECB1C-261F-4e13-9B21-16FB83BC03BD}" name="V1" image="0"'
    ' changed="2026-07-26 16:21:33" uid="{11111111-2222-3333-4444-555555555555}">'
    '<Properties action="U" name="V1" runAs="" program="cmd.exe" arguments=""'
    f' startIn="" enabled="1" triggerType="ONCE" triggerTime="{_V}" triggerDays=""/>'
    "</Task>"
)
_GROUPS = ("WI01A-LocalGroups-GPMC", "Groups/Groups.xml")
_DRIVES = ("WI01A-DriveMaps-GPMC", "Drives/Drives.xml")
_NESTED = ("WI01A-NestedILT-GPMC", "Drives/Drives.xml")
_SERVICES = ("WI01A-ServicesRecovery-GPMC", "Services/Services.xml")
_TASKS = ("WI01A-SchedTasks-GPMC", "ScheduledTasks/ScheduledTasks.xml")
_ENV = ("WI01A-EnvVars-GPMC", "EnvironmentVariables/EnvironmentVariables.xml")
_POWER = ("WI01A-Power-GPMC", "PowerOptions/PowerOptions.xml")
_SID_5124 = "S-1-5-21-0000000000-0000000000-0000000000-5124"
_SID_4674 = "S-1-5-21-0000000000-0000000000-0000000000-4674"
_OU = "OU=Lab-WS2,OU=Lab-WS,OU=Workstations,DC=ad,DC=hraedon,DC=com"

#: (root, element, attribute or #text) -> (capture, file under Preferences/,
#: text to replace, replacement, valid value).
PROBES: dict[tuple[str, str, str], tuple[str, str, str, str, str]] = {
    ("Groups", "Member", "sid"): (*_GROUPS, f'sid="{_SID_5124}"', f'sid="{_V}"', _SID_5124),
    ("Groups", "Member", "name"): (
        *_GROUPS,
        'name="HRAENET\\svc-gpolens"',
        f'name="{_V}"',
        "HRAENET\\svc-gpolens",
    ),
    ("Groups", "Properties", "groupSid"): (
        *_GROUPS,
        'groupSid="S-1-5-32-544"',
        f'groupSid="{_V}"',
        "S-1-5-32-544",
    ),
    ("Groups", "Properties", "groupName"): (
        *_GROUPS,
        'groupName="Administrators (built-in)"',
        f'groupName="{_V}"',
        "Administrators (built-in)",
    ),
    ("Groups", "Properties", "newName"): (
        *_GROUPS,
        'newName=""',
        f'newName="{_V}"',
        "Local Admins",
    ),
    ("Groups", "FilterRunOnce", "id"): (
        *_GROUPS,
        'id="{96AFCD0C-EA2A-4ECD-BB76-284E9A8FB23C}"',
        f'id="{_V}"',
        "{96AFCD0C-EA2A-4ECD-BB76-284E9A8FB23C}",
    ),
    ("Drives", "Properties", "userName"): (
        *_DRIVES,
        'userName=""',
        f'userName="{_V}"',
        "HRAENET\\svc-maps",
    ),
    ("Drives", "Properties", "username"): (
        *_DRIVES,
        'userName=""',
        f'userName="" username="{_V}"',
        "HRAENET\\svc-maps",
    ),
    ("Drives", "FilterRunOnce", "id"): (
        *_DRIVES,
        'id="{F9EA1205-34A7-41AF-8AAD-A76FFB3EDFE4}"',
        f'id="{_V}"',
        "{F9EA1205-34A7-41AF-8AAD-A76FFB3EDFE4}",
    ),
    ("NTServices", "Properties", "accountName"): (
        *_SERVICES,
        'accountName="LocalSystem"',
        f'accountName="{_V}"',
        "LocalSystem",
    ),
    ("NTServices", "Properties", "serviceName"): (
        *_SERVICES,
        'serviceName="Spooler"',
        f'serviceName="{_V}"',
        "Spooler",
    ),
    ("NTServices", "NTService", "uid"): (
        *_SERVICES,
        'uid="{3DA17861-8910-4232-9F50-7EE67ED8266D}"',
        f'uid="{_V}"',
        "{3DA17861-8910-4232-9F50-7EE67ED8266D}",
    ),
    ("ScheduledTasks", "Properties", "runAs"): (
        *_TASKS,
        'runAs="NT AUTHORITY\\System"',
        f'runAs="{_V}"',
        "NT AUTHORITY\\System",
    ),
    ("ScheduledTasks", "Properties", "name"): (
        *_TASKS,
        'action="U" name="GpoStudio-Cleanup"',
        f'action="U" name="{_V}"',
        "GpoStudio-Cleanup",
    ),
    ("ScheduledTasks", "TaskV2", "name"): (
        *_TASKS,
        'name="GpoStudio-Cleanup" image',
        f'name="{_V}" image',
        "GpoStudio-Cleanup",
    ),
    ("ScheduledTasks", "Properties", "triggerTime"): (
        *_TASKS,
        "</ScheduledTasks>",
        _TASK_V1 + "</ScheduledTasks>",
        "02:00:00",
    ),
    ("ScheduledTasks", "StartBoundary", "#text"): (
        *_TASKS,
        "<StartBoundary>2026-07-26T02:00:00</StartBoundary>",
        f"<StartBoundary>{_V}</StartBoundary>",
        "2026-07-26T02:00:00",
    ),
    ("ScheduledTasks", "EndBoundary", "#text"): (
        *_TASKS,
        "<EndBoundary>%LocalTimeXmlEx%</EndBoundary>",
        f"<EndBoundary>{_V}</EndBoundary>",
        "%LocalTimeXmlEx%",
    ),
    ("ScheduledTasks", "UserId", "#text"): (
        *_TASKS,
        "<UserId>%LogonDomain%\\%LogonUser%</UserId>",
        f"<UserId>{_V}</UserId>",
        "NT AUTHORITY\\System",
    ),
    ("ScheduledTasks", "GroupId", "#text"): (
        *_TASKS,
        "<UserId>%LogonDomain%\\%LogonUser%</UserId>",
        f"<GroupId>{_V}</GroupId>",
        "BUILTIN\\Users",
    ),
    ("ScheduledTasks", "Command", "#text"): (
        *_TASKS,
        "<Command>C:\\Windows\\System32\\cleanmgr.exe</Command>",
        f"<Command>{_V}</Command>",
        "C:\\Windows\\System32\\cleanmgr.exe",
    ),
    ("EnvironmentVariables", "FilterGroup", "sid"): (
        *_ENV,
        f'sid="{_SID_4674}"',
        f'sid="{_V}"',
        _SID_4674,
    ),
    ("EnvironmentVariables", "FilterGroup", "name"): (
        *_ENV,
        'name="HRAENET\\lab-admins"',
        f'name="{_V}"',
        "HRAENET\\lab-admins",
    ),
    ("PowerOptions", "Properties", "nameGuid"): (
        *_POWER,
        'nameGuid="{8C5E7FDA-E8BF-4A96-9A85-A6E23A8C635C}"',
        f'nameGuid="{_V}"',
        "{8C5E7FDA-E8BF-4A96-9A85-A6E23A8C635C}",
    ),
    ("Drives", "FilterUser", "sid"): (
        *_NESTED,
        "<Filters>",
        f'<Filters><FilterUser bool="AND" not="0" name="HRAENET\\u1" sid="{_V}"/>',
        "S-1-5-21-0000000000-0000000000-0000000000-1104",
    ),
    ("Drives", "FilterUser", "name"): (
        *_NESTED,
        "<Filters>",
        f'<Filters><FilterUser bool="AND" not="0" name="{_V}" sid=""/>',
        "HRAENET\\u1",
    ),
    ("Drives", "FilterOrgUnit", "name"): (*_NESTED, f'name="{_OU}"', f'name="{_V}"', _OU),
    ("Drives", "FilterComputerName", "name"): (
        *_NESTED,
        "<Filters>",
        f'<Filters><FilterComputerName bool="AND" not="0" type="NETBIOS" name="{_V}"/>',
        "LAB-WS01",
    ),
    ("Drives", "FilterDomain", "name"): (
        *_NESTED,
        'name="ad.hraedon.com"',
        f'name="{_V}"',
        "ad.hraedon.com",
    ),
    ("Drives", "FilterDate", "startDate"): (
        *_NESTED,
        "<Filters>",
        '<Filters><FilterDate bool="AND" not="0" period="RANGE"'
        f' startDate="{_V}" endDate="2026-12-31"/>',
        "2026-01-01",
    ),
    ("Drives", "FilterDate", "endDate"): (
        *_NESTED,
        "<Filters>",
        '<Filters><FilterDate bool="AND" not="0" period="RANGE"'
        f' startDate="2026-01-01" endDate="{_V}"/>',
        "2026-12-31",
    ),
}
#: The writer table's kinds, as the shape families above name them.
_SHAPE_KIND = {
    "sid": "sid",
    "principal": "name",
    "security_principal": "name",
    "name": "name",
    "ou": "dn",
    "dn": "dn",
    "guid": "guid",
    "braced_guid": "guid",
    "time": "datetime",
    "datetime": "datetime",
    "date": "datetime",
    "line": "line",
    "text": "line",
}


def _kind_of(key: tuple[str, str, str]) -> str:
    from gpo_studio.emit_integrity import GPP_XML, classify_xml

    field = classify_xml(GPP_XML, *key)
    assert field is not None, f"{key} is not in the writer's table"
    return _SHAPE_KIND[field.kind]


def _source(key: tuple[str, str, str], value: str) -> tuple[str, dict[str, bytes]]:
    capture, relative, old, new, _valid = PROBES[key]
    (path,) = [
        candidate
        for candidate in sorted(
            (CAPTURES / capture).glob(f"*/DomainSysvol/GPO/*/Preferences/{relative}")
        )
        if old in candidate.read_text(encoding="utf-8")
    ][:1]
    data = path.read_text(encoding="utf-8")
    data = data.replace(old, new.replace(_V, _xml_escaped(value)), 1)
    scope = "computer" if "/Machine/" in path.as_posix() else "user"
    return scope, {relative: data.encode("utf-8")}


def _through_the_boundary(
    key: tuple[str, str, str], value: str
) -> tuple[list[str], list[tuple[str, bytes]]]:
    """(errors, written files) for a document holding `value` at `key`."""
    from gpo_studio.gpp import GppError, parse_gpp_collection, serialize_gpp
    from gpo_studio.validation import validate_gpp_collection

    scope, files = _source(key, value)
    try:
        collection = parse_gpp_collection(scope, files)  # type: ignore[arg-type]
    except GppError as error:
        return [f"parse: {error}"], []
    errors = [i.code for i in validate_gpp_collection(collection) if i.severity == "error"]
    if errors:
        return errors, []
    try:
        written = sorted(serialize_gpp(collection).items())
    except GppError as error:  # the writer's own check, after validation missed it
        return [f"write: {error}"], []
    return errors, written


@pytest.mark.parametrize("key", sorted(PROBES), ids=str)
def test_a_serialized_value_is_written_when_valid(key: tuple[str, str, str]) -> None:
    valid = PROBES[key][4]
    errors, written = _through_the_boundary(key, valid)
    assert not errors, errors
    assert _carried(written, valid), f"{key} is not written, so the boundary never sees it"


@pytest.mark.parametrize(
    "key",
    [key for key in sorted(PROBES) if key[2] == "#text" and key[1] != "Command"],
    ids=str,
)
def test_an_empty_task_payload_identity_is_refused(key: tuple[str, str, str]) -> None:
    """DeepSeek final-r8 B3: ``<StartBoundary/>`` is not "unset", it is a task
    Windows never creates; the same holds for an empty principal."""
    errors, _written = _through_the_boundary(key, "")
    assert errors, f"an empty {key[1]} was accepted"


@pytest.mark.parametrize(
    ("key", "shape"),
    [
        pytest.param(key, shape, id=f"{key}-{shape}")
        for key in sorted(PROBES)
        for shape in _shape_names(_kind_of(key))
    ],
)
def test_a_serialized_value_is_refused_or_never_written(
    key: tuple[str, str, str], shape: str
) -> None:
    value = _hostile(_kind_of(key), PROBES[key][4], shape)
    errors, written = _through_the_boundary(key, value)
    if errors:
        return
    carried = _carried(written, value)
    assert not carried, f"wrote {value!r} at {key} in {carried}"
