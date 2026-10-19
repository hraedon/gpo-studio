"""An identity with a control character never reaches policy output (Sol final-r6 B2).

Each path below was executed by the reviewer on the real store and generators
before the fix:

* B2a: a GPP group SID ``"S-1-5-32-544\\n"`` was stored and written into
  Groups.xml as ``groupSid="S-1-5-32-544&#10;"`` (the format check was a
  ``match`` with ``$`` and only a warning);
* B2b: a link DN with a trailing LF passed validation (stripped before the
  check, stored raw) and the publication preview, and reached the PowerShell
  plan as ``Get-GPInheritance -Target '...\\n'``;
* B2c: a scheduled-task trigger time ``"03:00\\n"`` became the StartBoundary
  ``1970-01-01T03:00\\n:00``, which Windows rejects without a word.

Now the store refuses each (an error, not a warning), and nothing it accepts
carries the character into XML or PowerShell.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from gpo_studio.export import powershell_plan
from gpo_studio.gpp import GppCollection, GppError, GppGroup, GppGroupMember, serialize_gpp
from gpo_studio.gpp_adapters import GppScheduledTask, serialize_gpp_scheduled_tasks
from gpo_studio.model import GPOLink, ValidationError
from gpo_studio.publication import (
    _is_valid_dn,
    generate_publication_plan,
    validate_publication_plan,
)
from gpo_studio.store import WorkspaceStore

#: LF (the probe), CR, TAB, NUL-adjacent C0, DEL, NEL (C1) and a bidi override.
CONTROL = ["\n", "\r", "\t", "\x0b", "\x7f", "\x85", "‮"]


def _store(tmp_path):  # type: ignore[no-untyped-def]
    store = WorkspaceStore(tmp_path / "workspace.db")
    gpo = store.create_gpo("Identity probe", identity="alice", reason="probe")
    return store, gpo


def _group(sid: str, member_sid: str = "S-1-5-21-1-2-3-500") -> GppGroup:
    return GppGroup(
        name="Administrators",
        sid=sid,
        action="update",
        members=(GppGroupMember(sid=member_sid, name="DOMAIN\\Admins", action="add"),),
    )


@pytest.mark.parametrize("character", CONTROL)
def test_a_group_sid_with_a_control_character_is_refused(tmp_path, character: str) -> None:
    store, gpo = _store(tmp_path)
    with pytest.raises(ValidationError) as caught:
        store.put_gpp_group(
            gpo.guid, gpo.revision, "computer", _group("S-1-5-32-544" + character),
            identity="alice", reason="probe",
        )
    codes = {issue.code for issue in caught.value.issues}
    assert codes & {"control_character_in_gpp_group_sid", "invalid_gpp_group_sid"}, codes
    assert store.get_gpo(gpo.guid).revision == gpo.revision


@pytest.mark.parametrize("sid", [" S-1-5-32-544", "S-1-5-32-544 ", "S-1-5-32-544x", "S-1"])
def test_a_malformed_group_sid_is_an_error_not_a_warning(tmp_path, sid: str) -> None:
    store, gpo = _store(tmp_path)
    with pytest.raises(ValidationError) as caught:
        store.put_gpp_group(
            gpo.guid, gpo.revision, "computer", _group(sid), identity="alice", reason="probe"
        )
    assert "invalid_gpp_group_sid" in {issue.code for issue in caught.value.issues}


@pytest.mark.parametrize("character", CONTROL)
def test_a_member_sid_with_a_control_character_is_refused(tmp_path, character: str) -> None:
    store, gpo = _store(tmp_path)
    with pytest.raises(ValidationError):
        store.put_gpp_group(
            gpo.guid, gpo.revision, "computer",
            _group("S-1-5-32-544", member_sid="S-1-5-21-1-2-3-500" + character),
            identity="alice", reason="probe",
        )


def test_a_stored_group_writes_its_sid_exactly(tmp_path) -> None:
    store, gpo = _store(tmp_path)
    gpo = store.put_gpp_group(
        gpo.guid, gpo.revision, "computer", _group("S-1-5-32-544"),
        identity="alice", reason="probe",
    )
    xml = serialize_gpp(gpo.gpp_collections[0])["Groups/Groups.xml"]
    assert b'groupSid="S-1-5-32-544"' in xml
    assert b"&#10;" not in xml and b"&#13;" not in xml


@pytest.mark.parametrize("character", CONTROL)
def test_a_security_filter_sid_with_a_control_character_is_refused(
    tmp_path, character: str
) -> None:
    store, gpo = _store(tmp_path)
    with pytest.raises(ValidationError):
        store.put_security_filter(
            gpo.guid, gpo.revision,
            {"principal": "DOMAIN\\Admins", "sid": "S-1-5-21-1-2-3-512" + character},
            identity="alice", reason="probe",
        )


def test_a_malformed_security_filter_sid_is_an_error(tmp_path) -> None:
    store, gpo = _store(tmp_path)
    with pytest.raises(ValidationError) as caught:
        store.put_security_filter(
            gpo.guid, gpo.revision,
            {"principal": "DOMAIN\\Admins", "sid": "S-1-5-21-x"},
            identity="alice", reason="probe",
        )
    assert "invalid_sid_format" in {issue.code for issue in caught.value.issues}


@pytest.mark.parametrize("principal", [" DOMAIN\\Admins", "DOMAIN\\Admins\n", "DOMAIN\\Ad‮mins"])
def test_a_principal_with_whitespace_or_control_characters_is_refused(
    tmp_path, principal: str
) -> None:
    store, gpo = _store(tmp_path)
    with pytest.raises(ValidationError):
        store.put_security_filter(
            gpo.guid, gpo.revision, {"principal": principal},
            identity="alice", reason="probe",
        )


@pytest.mark.parametrize(
    "target",
    [
        "OU=Lab,DC=studio,DC=local\n",
        "OU=Lab,DC=studio,DC=local\r\n",
        " OU=Lab,DC=studio,DC=local",
        "OU=Lab,DC=studio,DC=local ",
        "OU=La\nb,DC=studio,DC=local",
        "OU=Lab‮,DC=studio,DC=local",
    ],
)
def test_a_link_target_that_is_not_exactly_a_dn_is_refused(tmp_path, target: str) -> None:
    store, gpo = _store(tmp_path)
    with pytest.raises(ValidationError) as caught:
        store.put_link(
            gpo.guid, gpo.revision, {"target": target, "order": 1},
            identity="alice", reason="probe",
        )
    assert "invalid_link_target" in {issue.code for issue in caught.value.issues}
    assert not _is_valid_dn(target)


def test_the_publication_preview_refuses_a_control_bearing_dn_it_is_handed() -> None:
    """The planner's own check, for a GPO that did not come through the store."""
    from gpo_studio.model import GPO

    gpo = GPO(guid="{00000000-0000-4000-8000-000000000001}", name="probe", domain="studio.local")
    gpo = replace(gpo, links=(GPOLink(id="l1", target="OU=Lab,DC=studio,DC=local\n", order=1),))
    plan = generate_publication_plan(gpo, target="ad")
    assert any(issue.check == "link_target" for issue in validate_publication_plan(plan))


def test_a_stored_link_reaches_powershell_exactly(tmp_path) -> None:
    store, gpo = _store(tmp_path)
    gpo = store.put_link(
        gpo.guid, gpo.revision, {"target": "OU=Lab,DC=studio,DC=local", "order": 1},
        identity="alice", reason="probe",
    )
    script = powershell_plan(gpo)
    assert "-Target 'OU=Lab,DC=studio,DC=local')" in script
    assert "DC=local\n" not in script


@pytest.mark.parametrize("time", ["03:00\n", "03:00\r", " 03:00", "03:00 ", "03:‮00"])
def test_a_trigger_time_with_whitespace_or_control_characters_is_never_written(
    time: str,
) -> None:
    task = GppScheduledTask(name="T", element_variant="TaskV2", trigger_type="once",
                            trigger_time=time)
    with pytest.raises(GppError, match="trigger time"):
        serialize_gpp_scheduled_tasks((task,), "computer")


def test_the_store_refuses_a_task_it_cannot_write(tmp_path) -> None:
    """The store reports the serializer's refusal (`unwritable_preferences`).

    Every preference mutation writes its collection before committing
    (`WorkspaceStore._validate_gpp`), so a task it cannot write never lands.
    """
    store, _ = _store(tmp_path)
    task = GppScheduledTask(name="T", element_variant="TaskV2", trigger_type="once",
                            trigger_time="03:00\n")
    with pytest.raises(ValidationError) as caught:
        store._validate_gpp(GppCollection(scope="computer", scheduled_tasks=(task,)))
    assert "unwritable_preferences" in {issue.code for issue in caught.value.issues}


def test_a_bare_trigger_time_is_still_completed() -> None:
    task = GppScheduledTask(name="T", element_variant="TaskV2", trigger_type="once",
                            trigger_time="03:00")
    xml = serialize_gpp_scheduled_tasks((task,), "computer")
    assert b"1970-01-01T03:00:00" in xml
