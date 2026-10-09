"""A user-side scheduled task gets the user-side default principal (WI-079).

GPMC writes ``runAs`` on every TaskV2, and its default depends on the side:
every user-side TaskV2 in the committed native captures runs as
``%LogonDomain%\\%LogonUser%`` (in ``runAs`` and in the task payload's
``UserId``), and every machine-side one as ``NT AUTHORITY\\System``.
``serialize_gpp_scheduled_tasks`` honoured the scope, but ``serialize_gpp``
reached the item serializer through ``_build_adapter_root``, which dropped it,
so a user-side task with no ``run_as`` was written to run as SYSTEM.
"""

from __future__ import annotations

import inspect
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from gpo_studio import gpp_adapters
from gpo_studio.gpp import GppCollection, GppScope, serialize_gpp
from gpo_studio.gpp_adapters import GppScheduledTask, serialize_gpp_scheduled_tasks

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "tests/fixtures/native-gpp-gpmc"
TASKS_FILE = "ScheduledTasks/ScheduledTasks.xml"
SIDES: dict[GppScope, str] = {"computer": "Machine", "user": "User"}


def _principals(task: ET.Element) -> tuple[str, list[str]]:
    props = next(child for child in task if child.tag == "Properties")
    user_ids = [e.text or "" for e in props.iter() if e.tag.rsplit("}", 1)[-1] == "UserId"]
    return props.get("runAs", ""), user_ids


def _native_run_as(side: str) -> set[str]:
    """``runAs`` of every TaskV2 the native GPMC captures hold on one side."""
    found: set[str] = set()
    pattern = f"*/*/DomainSysvol/GPO/{side}/Preferences/ScheduledTasks/ScheduledTasks.xml"
    for path in NATIVE.glob(pattern):
        for task in ET.fromstring(path.read_bytes()):
            if task.tag == "TaskV2":
                found.add(_principals(task)[0])
    return found


def test_the_native_captures_have_one_default_per_side() -> None:
    """The ground truth this file pins against, read off the captures."""
    assert _native_run_as("User") == {"%LogonDomain%\\%LogonUser%"}
    assert _native_run_as("Machine") == {"NT AUTHORITY\\System"}


@pytest.mark.parametrize("scope", ["computer", "user"])
def test_a_task_without_run_as_gets_its_sides_native_default(scope: GppScope) -> None:
    (expected,) = _native_run_as(SIDES[scope])
    task = GppScheduledTask(name="Default principal")
    written = ET.fromstring(
        serialize_gpp(GppCollection(scope=scope, scheduled_tasks=(task,)))[TASKS_FILE]
    )
    run_as, user_ids = _principals(written[0])
    assert run_as == expected
    assert user_ids == [expected]


@pytest.mark.parametrize("scope", ["computer", "user"])
def test_the_collection_writer_agrees_with_the_family_writer(scope: GppScope) -> None:
    task = GppScheduledTask(name="Same either way", program="cmd.exe")
    via_collection = serialize_gpp(GppCollection(scope=scope, scheduled_tasks=(task,)))
    assert via_collection[TASKS_FILE] == serialize_gpp_scheduled_tasks((task,), scope)


@pytest.mark.parametrize("case", ["WI01A-SchedTasks-GPMC", "WI01A-SchedTasksFull-GPMC"])
def test_imported_user_tasks_keep_their_principal_when_rewritten(case: str) -> None:
    from gpo_studio.report_parity import studio_gpo_from_backup

    gpo = studio_gpo_from_backup(NATIVE / case)
    user = next(c for c in gpo.gpp_collections if c.scope == "user")
    written = ET.fromstring(serialize_gpp(user)[TASKS_FILE])
    assert {_principals(task)[0] for task in written} == {"%LogonDomain%\\%LogonUser%"}


def test_every_scope_taking_item_serializer_is_given_the_scope() -> None:
    """A serializer that grows a scope parameter must be listed, or it gets the default."""
    takes_scope = {
        key for key, fn in gpp_adapters._ITEM_SERIALIZE_FUNCTIONS.items()
        if "scope" in inspect.signature(fn).parameters
    }
    assert takes_scope == gpp_adapters._SCOPED_ITEM_SERIALIZERS == {"scheduled_tasks"}
