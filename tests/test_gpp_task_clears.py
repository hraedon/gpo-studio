"""Clearing a task's command field is written, or refused; never ignored (WI-080).

Second re-check (P1): a task with no import record treated an empty command
field as unset, so a deliberate clear on a payload-authored task, or on a task
bd84b3a stored, succeeded while the export kept the old value (a cleared
``arguments`` still exported ``/sagerun:1``). Now:

* an imported task (its native element retained) has a record of what was
  imported, so a clear is an edit and is written into the payload;
* a task with no record cannot tell a clear from an unset field. An empty
  field the payload fills is refused as ambiguous whenever another command
  field is set; only a task whose command fields are ALL empty -- built from a
  payload alone, as the endpoint lane's are -- leaves its payload as it is.

Tasks have no edit route, so each case is set up in the store and exported
through the public ``export.zip`` and ``gpmc-backup`` routes.
"""

from __future__ import annotations

import io
import json
import xml.etree.ElementTree as ET
import zipfile
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from gpo_studio.api import app
from gpo_studio.gpp import (
    GppCollection,
    ensure_editor_ids,
    gpp_collection_from_dict,
    gpp_collection_to_dict,
    parse_gpp_collection,
)
from gpo_studio.gpp_adapters import GppImmediateTask, GppScheduledTask
from gpo_studio.store import WorkspaceStore, gpo_from_dict

ROOT = Path(__file__).resolve().parents[1]
(SCHED,) = (ROOT / "tests/fixtures/native-gpp-gpmc/WI01A-SchedTasks-GPMC").glob(
    "*/DomainSysvol/GPO/Machine/Preferences/ScheduledTasks/ScheduledTasks.xml"
)
BASELINE = ROOT / "tests/fixtures/gpp-store-baseline-bd84b3a/WI01A-SchedTasks-GPMC.json"
FIELDS = ("program", "arguments", "start_in")
_TAG = {"program": "Command", "arguments": "Arguments", "start_in": "WorkingDirectory"}
_PAYLOAD = (
    '<Task version="1.2"><Settings><Enabled>true</Enabled></Settings>'
    '<Actions Context="Author"><Exec><Command>C:\\Old\\run.exe</Command>'
    "<Arguments>/old</Arguments><WorkingDirectory>C:\\Old</WorkingDirectory>"
    "</Exec></Actions></Task>"
)
_FULL = {"program": "C:\\Old\\run.exe", "arguments": "/old", "start_in": "C:\\Old"}


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    with closing(WorkspaceStore(tmp_path / "clears.db")) as store:
        monkeypatch.setattr(app.state, "store", store, raising=False)
        monkeypatch.setattr(app.state, "owns_store", False, raising=False)
        with TestClient(app, base_url="http://127.0.0.1") as test_client:
            yield test_client, store


def _exports(test_client: TestClient, store: WorkspaceStore, collection: GppCollection) -> Any:
    gpo = store.create_gpo(
        name=f"Clear {len(store.list_gpos())}", identity="clears", reason="seed",
        gpp_collections=(collection,),
    )
    # The GPO stays readable whatever its export does.
    assert test_client.get(f"/api/gpos/{gpo.guid}").status_code == 200
    return [
        test_client.get(f"/api/gpos/{gpo.guid}/{route}")
        for route in ("export.zip", "gpmc-backup")
    ]


def _payload_exec(response: Any, side: str = "Machine") -> list[ET.Element]:
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        name = next(
            n for n in archive.namelist()
            if n.endswith(f"{side}/Preferences/ScheduledTasks/ScheduledTasks.xml")
        )
        root = ET.fromstring(archive.read(name))
    return [elem for elem in root.iter("Exec")]


def _assert_refused(responses: list[Any], field_name: str) -> None:
    for response in responses:
        assert response.status_code == 422, response.text
        assert field_name in response.text
        assert "cannot be told from a deliberate clear" in response.text


@pytest.mark.parametrize("family", ["scheduled", "immediate"])
@pytest.mark.parametrize("field_name", FIELDS)
def test_a_payload_authored_clear_is_refused(client: Any, family: str, field_name: str) -> None:
    test_client, store = client
    fields = {**_FULL, field_name: ""}
    task: Any = (
        GppScheduledTask(name="T", element_variant="TaskV2", task_xml=_PAYLOAD, **fields)
        if family == "scheduled"
        else GppImmediateTask(name="T", task_xml=_PAYLOAD, **fields)
    )
    collection = (
        GppCollection(scope="computer", scheduled_tasks=(task,))
        if family == "scheduled"
        else GppCollection(scope="computer", immediate_tasks=(task,))
    )
    _assert_refused(_exports(test_client, store, collection), field_name)


@pytest.mark.parametrize("family", ["scheduled", "immediate"])
def test_a_payload_only_task_is_exported_from_its_payload(client: Any, family: str) -> None:
    """The endpoint lane's shape: every command field empty, never edited."""
    test_client, store = client
    task: Any = (
        GppScheduledTask(name="T", element_variant="TaskV2", task_xml=_PAYLOAD)
        if family == "scheduled"
        else GppImmediateTask(name="T", task_xml=_PAYLOAD)
    )
    collection = (
        GppCollection(scope="computer", scheduled_tasks=(task,))
        if family == "scheduled"
        else GppCollection(scope="computer", immediate_tasks=(task,))
    )
    bundle, _backup = _exports(test_client, store, collection)
    assert bundle.status_code == 200, bundle.text
    (exec_elem,) = _payload_exec(bundle)
    assert [(c.tag, c.text) for c in exec_elem] == [
        ("Command", "C:\\Old\\run.exe"), ("Arguments", "/old"), ("WorkingDirectory", "C:\\Old"),
    ]


@pytest.mark.parametrize("field_name", ["program", "arguments"])
def test_a_clear_on_a_task_bd84b3a_stored_is_refused(client: Any, field_name: str) -> None:
    """The review's case: clearing /sagerun:1 must not export /sagerun:1."""
    test_client, store = client
    record = json.loads(BASELINE.read_text("utf-8"))
    collection = next(
        c for c in gpo_from_dict(record["gpo"]).gpp_collections if c.scope == "computer"
    )
    task = collection.scheduled_tasks[0]
    assert task.arguments == "/sagerun:1" and not task.native_xml
    cleared = replace(collection, scheduled_tasks=(
        replace(task, **{field_name: ""}), *collection.scheduled_tasks[1:],
    ))
    responses = _exports(test_client, store, cleared)
    _assert_refused(responses, field_name)
    assert all(b"/sagerun:1" not in r.content for r in responses)


@pytest.mark.parametrize("field_name", ["program", "arguments"])
def test_a_clear_on_an_imported_task_is_written(client: Any, field_name: str) -> None:
    """With an import record a clear is an edit, written into the payload."""
    test_client, store = client
    parsed = parse_gpp_collection(
        "computer", {"ScheduledTasks/ScheduledTasks.xml": SCHED.read_bytes()}
    )
    collection = gpp_collection_from_dict(gpp_collection_to_dict(ensure_editor_ids(parsed)))
    task = collection.scheduled_tasks[0]
    assert task.native_xml and getattr(task, field_name)
    cleared = replace(collection, scheduled_tasks=(
        replace(task, **{field_name: ""}), *collection.scheduled_tasks[1:],
    ))
    for response in _exports(test_client, store, cleared):
        assert response.status_code == 200, response.text
        exec_elem = _payload_exec(response)[0]
        child = exec_elem.find(_TAG[field_name])
        assert child is None or (child.text or "") == ""
