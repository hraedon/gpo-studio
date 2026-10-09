"""An edit to a preference item reaches the exported file, or is refused.

Review of WI-080 (P1, P2): with each item's native element retained, the
writer reconciles it with the model and checks that the result renders as the
model does. That check compared the writer with itself, so a typed value the
writer never puts on the wire was invisible to it: an immediate task's command
lives in its <Task> payload, and an edit to it exported the old command; a
shortcut's name lives only on its item element, and an edit to it exported the
old name.

The matrix below edits every scalar typed value of every item of every native
capture, in every shape a stored item takes (second review, N1):

* ``imported``: imported after WI-080, its native element retained;
* ``model-only``: no retained element (authored in Studio, or any item once
  its record is dropped);
* ``bd84b3a-stored``: the record bd84b3a itself stored
  (tests/fixtures/gpp-store-baseline-bd84b3a);
* ``namespace-discarded``: imported from a file whose item elements carry an
  XML namespace, so no element was retained.

Each edit must read back from the written file EQUAL to the edited value (or
as a documented `gpp.TYPED_NORMALISATIONS`), or the export must be refused.
Only the fields in `_NO_RECORD_EXCUSED` are excused, and only where an item has
no import record to tell an edit from an unset value.
"""

from __future__ import annotations

import functools
import json
import xml.etree.ElementTree as ET
from dataclasses import fields, replace
from pathlib import Path
from types import UnionType
from typing import Any, Literal, Union, get_args, get_origin, get_type_hints

import pytest

from gpo_studio.gpp import (
    GppCollection,
    GppError,
    _reads_back,
    ensure_editor_ids,
    gpp_collection_from_dict,
    gpp_collection_to_dict,
    model_only,
    parse_gpp_collection,
    serialize_gpp,
)
from gpo_studio.gpp_adapters import (
    ADAPTER_FILE_PATHS,
    ADAPTER_KEYS,
    GppImmediateTask,
    GppScheduledTask,
)
from gpo_studio.store import gpo_from_dict

ROOT = Path(__file__).resolve().parents[1]
NATIVE_FILES = sorted(
    ROOT.glob("tests/fixtures/native-gpp*/*/*/DomainSysvol/GPO/*/Preferences/*/*.xml")
)
BASELINE = ROOT / "tests/fixtures/gpp-store-baseline-bd84b3a"
FAMILIES = ("groups", "registry", *ADAPTER_KEYS)
_BOOKKEEPING = {"id", "native_xml", "document_position"}
SHAPES = ("imported", "model-only", "bd84b3a-stored", "namespace-discarded")


def _scope(path: Path) -> Any:
    return "computer" if "/Machine/" in path.as_posix() else "user"


def _stored(scope: Any, files: dict[str, bytes]) -> GppCollection:
    parsed = parse_gpp_collection(scope, files)
    return gpp_collection_from_dict(gpp_collection_to_dict(ensure_editor_ids(parsed)))


def _imported(path: Path) -> GppCollection:
    return _stored(_scope(path), {f"{path.parent.name}/{path.name}": path.read_bytes()})


def _namespaced_items(data: bytes) -> bytes:
    """The file with each item element (each root child) in a foreign namespace.

    Only the item elements' own tags: their attributes, ``Properties``, filters
    and children stay unqualified, so the import is accepted and nothing is
    retained (the element uses a namespace).
    """
    root = ET.fromstring(data)
    for child in root:
        if child.tag in _TYPED_ITEM_TAGS:  # a retained unknown root child stays as it is
            child.tag = "{urn:review}" + child.tag
    return ET.tostring(root, encoding="utf-8")


_TYPED_ITEM_TAGS = frozenset({
    "Group", "User", "Registry", "EnvironmentVariable", "Ini", "RegionalOptions",
    "PowerScheme", "Device", "GlobalFolderOptionsVista", "DataSource", "Drive", "File",
    "Folder", "NetShare", "SharedPrinter", "Shortcut", "Application", "NTService", "Task",
    "TaskV2", "ImmediateTaskV2",
})


def _baseline_collection(path: Path) -> GppCollection | None:
    capture = path.parents[6].name
    record = json.loads((BASELINE / f"{capture}.json").read_text("utf-8"))
    gpo = gpo_from_dict(record["gpo"])
    wanted_family = path.parent.name
    for collection in gpo.gpp_collections:
        if collection.scope == _scope(path) and any(
            name.startswith(f"{wanted_family}/")
            for name in serialize_gpp(collection)
        ):
            return collection
    return None


@functools.cache
def _shape(shape: str, path: Path) -> GppCollection | None:
    if shape == "imported":
        return _imported(path)
    if shape == "model-only":
        return model_only(_imported(path))
    if shape == "bd84b3a-stored":
        return _baseline_collection(path)
    relative = f"{path.parent.name}/{path.name}"
    return _stored(_scope(path), {relative: _namespaced_items(path.read_bytes())})


def _choices(item: Any, field_name: str) -> tuple[Any, ...]:
    """The values a ``Literal`` field allows (from ``X | None`` too), else ``()``."""
    hint = get_type_hints(type(item))[field_name]
    options = get_args(hint) if get_origin(hint) in (Union, UnionType) else (hint,)
    return tuple(
        value for option in options if get_origin(option) is Literal for value in get_args(option)
    )


def _edited(item: Any, field_name: str) -> Any:
    value = getattr(item, field_name)
    choices = _choices(item, field_name)
    if choices:
        return next(choice for choice in choices if choice != value)
    return _edited_value(value)


def _edited_value(value: Any) -> Any:
    if isinstance(value, bool):
        return not value
    if isinstance(value, int):
        return value + 1
    if isinstance(value, str):
        return f"{value}Edited"
    return None


_FAMILY_FILES = {
    "groups": "Groups/Groups.xml", "registry": "Registry/Registry.xml", **ADAPTER_FILE_PATHS,
}


def _cases() -> list[tuple[str, Path, str, int, str]]:
    cases: list[tuple[str, Path, str, int, str]] = []
    for shape in SHAPES:
        for path in NATIVE_FILES:
            collection = _shape(shape, path)
            if collection is None:
                continue
            relative = f"{path.parent.name}/{path.name}"
            for family in FAMILIES:
                if _FAMILY_FILES[family] != relative:
                    continue  # a stored collection holds every file of its side
                for index, item in enumerate(getattr(collection, family)):
                    for f in fields(item):
                        if f.name in _BOOKKEEPING or f.name.endswith("password"):
                            continue
                        if _edited(item, f.name) is None:
                            continue
                        cases.append((shape, path, family, index, f.name))
    return cases


CASES = _cases()

#: Fields whose edit cannot be told from an unset value without an import
#: record, each with why. Excused only for the shapes that have no record.
_NO_RECORD_EXCUSED: dict[tuple[str, str], str] = {
    # The writer writes the value's action; the item's is a second name for it.
    # The API reconciles an edit of it (`gpp.registry_action_edit`); a direct
    # model edit of a record-less item has nothing to compare with.
    ("registry", "action"): "a second name for value.action, reconciled by the API",
}


def _reread(collection: GppCollection, family: str, index: int) -> Any:
    files = serialize_gpp(collection)
    reread = parse_gpp_collection(collection.scope, files)
    return getattr(reread, family)[index]


@pytest.mark.parametrize(
    ("shape", "path", "family", "index", "field_name"),
    CASES,
    ids=[f"{s}-{p.parents[6].name}-{p.parents[2].name}-{fam}{i}-{name}"
         for s, p, fam, i, name in CASES],
)
def test_an_edit_reaches_the_file_or_is_refused(
    shape: str, path: Path, family: str, index: int, field_name: str
) -> None:
    if shape != "imported" and (family, field_name) in _NO_RECORD_EXCUSED:
        pytest.skip(_NO_RECORD_EXCUSED[(family, field_name)])
    collection = _shape(shape, path)
    assert collection is not None
    items = getattr(collection, family)
    item = items[index]
    if shape == "imported":
        assert item.native_xml
    elif shape in ("model-only", "namespace-discarded", "bd84b3a-stored"):
        assert not item.native_xml
    want = _edited(item, field_name)
    edited = replace(
        collection,
        **{family: items[:index] + (replace(item, **{field_name: want}),) + items[index + 1 :]},
    )
    try:
        got = getattr(_reread(edited, family, index), field_name)
    except (GppError, ValueError):
        return  # refused: the operator is told, nothing wrong is exported
    assert _reads_back(field_name, want, got), (
        f"{shape} {family}[{index}].{field_name}: edited to {want!r}, reads back {got!r}"
    )


def test_the_edit_matrix_covers_every_shape_and_family_with_a_capture() -> None:
    for shape in SHAPES:
        covered = {family for s, _, family, _, _ in CASES if s == shape}
        assert covered == {
            "groups", "registry", "drives", "environment", "files", "folders", "ini_files",
            "printers", "scheduled_tasks", "immediate_tasks", "services", "shortcuts",
        }, shape
        assert sum(1 for s, *_ in CASES if s == shape) >= 590, shape


# ---------------------------------------------------------------------------
# P1: a task's command lives in its <Task> payload
# ---------------------------------------------------------------------------

SCHED = next(f for f in NATIVE_FILES if "WI01A-SchedTasks-GPMC" in f.as_posix()
             and "/Machine/" in f.as_posix())


def _payload_exec(written: bytes, tag: str, index: int = 0) -> ET.Element:
    root = ET.fromstring(written)
    item = [child for child in root if child.tag == tag][index]
    exec_elem = item.find("Properties/Task/Actions/Exec")
    assert exec_elem is not None
    return exec_elem


def test_an_immediate_task_command_edit_is_written_into_its_payload() -> None:
    """Only an imported item records what was imported, so only its edits can be
    told from unset scalars: a task built from a payload alone keeps the payload's
    command, as it always did (test_writer_conformance)."""
    collection = _imported(SCHED)
    task = collection.immediate_tasks[0]
    assert task.task_xml and task.program
    edited = replace(collection, immediate_tasks=(
        replace(task, program=r"C:\New\run.exe", arguments="/new", start_in=r"C:\New"),
    ))
    written = serialize_gpp(edited)["ScheduledTasks/ScheduledTasks.xml"]
    exec_elem = _payload_exec(written, "ImmediateTaskV2")
    assert [(c.tag, c.text) for c in exec_elem] == [
        ("Command", r"C:\New\run.exe"), ("Arguments", "/new"), ("WorkingDirectory", r"C:\New"),
    ]
    reread = parse_gpp_collection("computer", {"ScheduledTasks/ScheduledTasks.xml": written})
    again = reread.immediate_tasks[0]
    assert (again.program, again.arguments, again.start_in) == (
        r"C:\New\run.exe", "/new", r"C:\New",
    )
    # The Properties attributes stay as GPMC writes them (WI-081).
    props = ET.fromstring(written).find("ImmediateTaskV2/Properties")
    assert props is not None and "program" not in props.attrib


def test_deleting_an_immediate_task_command_is_written() -> None:
    collection = _imported(SCHED)
    task = collection.immediate_tasks[0]
    edited = replace(collection, immediate_tasks=(replace(task, program=""),))
    written = serialize_gpp(edited)["ScheduledTasks/ScheduledTasks.xml"]
    command = _payload_exec(written, "ImmediateTaskV2").find("Command")
    assert command is not None and (command.text or "") == ""
    reread = parse_gpp_collection("computer", {"ScheduledTasks/ScheduledTasks.xml": written})
    assert reread.immediate_tasks[0].program == ""


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("program", r"C:\New\task.exe"),
        # Lost on bd84b3a as well as on 95fe269 (second review, N2).
        ("arguments", "/new --flag"),
        ("start_in", r"C:\New"),
        ("enabled", None),
    ],
)
def test_a_scheduled_task_edit_is_written_into_its_payload(field_name: str, value: Any) -> None:
    """A TaskV2's command and enabled state live in its payload.

    Scheduled tasks have no API edit route, so this goes through the model and
    ``serialize_gpp``, the path every export takes.
    """
    collection = _imported(SCHED)
    task = collection.scheduled_tasks[0]
    new = (not task.enabled) if field_name == "enabled" else value
    assert getattr(task, field_name) != new
    edited = replace(collection, scheduled_tasks=(
        replace(task, **{field_name: new}), *collection.scheduled_tasks[1:],
    ))
    written = serialize_gpp(edited)["ScheduledTasks/ScheduledTasks.xml"]
    reread = parse_gpp_collection("computer", {"ScheduledTasks/ScheduledTasks.xml": written})
    assert getattr(reread.scheduled_tasks[0], field_name) == new
    # Every other item in the file is still the import exactly.
    for other in (*reread.scheduled_tasks[1:], *reread.immediate_tasks):
        assert other.native_xml


def test_a_scheduled_task_schedule_edit_is_refused_not_lost() -> None:
    collection = _imported(SCHED)
    task = collection.scheduled_tasks[0]
    edited = replace(collection, scheduled_tasks=(
        replace(task, trigger_time="2031-01-01T05:00:00"), *collection.scheduled_tasks[1:],
    ))
    with pytest.raises(GppError, match="Triggers"):
        serialize_gpp(edited)


def test_a_command_edit_on_a_task_without_an_exec_action_is_refused() -> None:
    def exec_free(task_xml: str) -> bool:
        actions = ET.fromstring(task_xml).find("Actions")
        return actions is not None and actions.find("Exec") is None

    collection, index = next(
        (collection, i)
        for collection in (_imported(f) for f in NATIVE_FILES if f.name == "ScheduledTasks.xml")
        for i, task in enumerate(collection.scheduled_tasks)
        if task.task_xml and exec_free(task.task_xml)
    )
    tasks = collection.scheduled_tasks
    edited = replace(collection, scheduled_tasks=(
        *tasks[:index], replace(tasks[index], program="mail.exe"), *tasks[index + 1:],
    ))
    with pytest.raises(GppError, match="no Exec action"):
        serialize_gpp(edited)


def test_an_edit_the_writer_does_not_write_is_refused() -> None:
    """A typed value with no place on the wire: refused, not exported unedited."""
    folders = next(f for f in NATIVE_FILES if f.name == "Folders.xml")
    collection = _imported(folders)
    edited = replace(collection, folders=(
        replace(collection.folders[0], suppress=True), *collection.folders[1:],
    ))
    with pytest.raises(GppError, match="suppress cannot be written"):
        serialize_gpp(edited)


def test_child_and_payload_values_of_the_editable_families_reach_the_file() -> None:
    """Members, filters and multi-string values are children, not attributes."""
    groups = next(f for f in NATIVE_FILES if "WI01A-LocalGroups-GPMC" in f.as_posix()
                  and "/User/" in f.as_posix())
    collection = _imported(groups)
    group = collection.groups[0]
    member = replace(group.members[0], name="HRAENET\\edited-member")
    assert group.ilt_filter is not None
    predicate = replace(group.ilt_filter.items[0], negate=True)  # type: ignore[arg-type]
    edited = replace(collection, groups=(replace(
        group,
        members=(member,),
        ilt_filter=replace(group.ilt_filter, items=(predicate,)),
    ),))
    written = serialize_gpp(edited)["Groups/Groups.xml"]
    again = parse_gpp_collection("user", {"Groups/Groups.xml": written}).groups[0]
    assert again.members[0].name == "HRAENET\\edited-member"
    assert again.ilt_filter is not None and again.ilt_filter.items[0].negate is True  # type: ignore[union-attr]
    # The run-once id survives an edit to the filter.
    assert ET.fromstring(written).find("Group/Filters/FilterRunOnce") is not None

    registry = next(f for f in NATIVE_FILES if "WI01A-RegistryMatrix-GPMC" in f.as_posix()
                    and "/Machine/" in f.as_posix())
    collection = _imported(registry)
    index = next(i for i, r in enumerate(collection.registry)
                 if r.value.registry_type == "REG_MULTI_SZ")
    reg = collection.registry[index]
    items = collection.registry
    edited = replace(collection, registry=(
        *items[:index],
        replace(reg, value=replace(reg.value, value=["one", "two words"])),
        *items[index + 1:],
    ))
    written = serialize_gpp(edited)["Registry/Registry.xml"]
    again = parse_gpp_collection("computer", {"Registry/Registry.xml": written})
    assert again.registry[index].value.value == ["one", "two words"]


# ---------------------------------------------------------------------------
# P2: a shortcut's name lives on its item element
# ---------------------------------------------------------------------------

SHORTCUTS = next(f for f in NATIVE_FILES if f.name == "Shortcuts.xml")


def test_a_shortcut_name_edit_is_written() -> None:
    collection = _imported(SHORTCUTS)
    for index, shortcut in enumerate(collection.shortcuts):
        assert shortcut.name and shortcut.shortcut_path  # GPMC: the path's leaf
        items = collection.shortcuts
        edited = replace(collection, shortcuts=(
            *items[:index], replace(shortcut, name="Renamed"), *items[index + 1:],
        ))
        written = serialize_gpp(edited)["Shortcuts/Shortcuts.xml"]
        item = ET.fromstring(written)[index]
        assert item.get("name") == "Renamed"
        props = item.find("Properties")
        assert props is not None and props.get("shortcutPath") == shortcut.shortcut_path
        again = parse_gpp_collection("user", {"Shortcuts/Shortcuts.xml": written})
        assert again.shortcuts[index].name == "Renamed"


def test_deleting_an_imported_shortcut_name_is_refused() -> None:
    """GPME names every shortcut (the leaf of its path); no name cannot be written."""
    collection = _imported(SHORTCUTS)
    edited = replace(collection, shortcuts=(
        replace(collection.shortcuts[0], name=""), *collection.shortcuts[1:],
    ))
    with pytest.raises(GppError, match="edit to name cannot be written"):
        serialize_gpp(edited)


# ---------------------------------------------------------------------------
# P2: a retained element may use no XML namespace
# ---------------------------------------------------------------------------

PRINTERS = next(f for f in NATIVE_FILES if f.name == "Printers.xml")


def test_a_namespaced_native_element_is_refused_on_load() -> None:
    collection = _imported(PRINTERS)
    data = gpp_collection_to_dict(collection)
    native = data["printers"][0]["native_xml"]
    data["printers"][0]["native_xml"] = native.replace(
        "<SharedPrinter ", '<SharedPrinter xmlns="urn:review" ', 1
    )
    with pytest.raises(GppError, match="XML namespace"):
        gpp_collection_from_dict(data)
    # A namespaced attribute too, and the xml: one is no exception.
    data["printers"][0]["native_xml"] = native.replace(
        "<Properties ", '<Properties xml:space="preserve" ', 1
    )
    with pytest.raises(GppError, match="XML namespace"):
        gpp_collection_from_dict(data)


def test_import_does_not_retain_a_namespaced_item() -> None:
    namespaced = PRINTERS.read_bytes().replace(
        b"<SharedPrinter ", b'<SharedPrinter xmlns="urn:review" ', 1
    )
    collection = parse_gpp_collection("user", {"Printers/Printers.xml": namespaced})
    assert collection.printers[0].native_xml == ""
    assert collection.printers[1].native_xml
    stored = gpp_collection_from_dict(gpp_collection_to_dict(ensure_editor_ids(collection)))
    written = serialize_gpp(stored)["Printers/Printers.xml"]
    assert b"urn:review" not in written


# ---------------------------------------------------------------------------
# Tasks with no import record: the payload is the record (second review, P1)
# ---------------------------------------------------------------------------

_PAYLOAD = (
    '<Task version="1.2"><Principals><Principal id="Author">'
    "<UserId>NT AUTHORITY\\System</UserId></Principal></Principals>"
    "<Settings><Enabled>true</Enabled></Settings>"
    "<Triggers><CalendarTrigger><StartBoundary>2026-01-01T03:00:00</StartBoundary>"
    "<ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay></CalendarTrigger>"
    "</Triggers>"
    '<Actions Context="Author"><Exec><Command>C:\\Old\\run.exe</Command>'
    "<Arguments>/old</Arguments></Exec></Actions></Task>"
)


def _payload_authored(task: Any) -> GppCollection:
    if isinstance(task, GppScheduledTask):
        return GppCollection(scope="computer", scheduled_tasks=(task,))
    return GppCollection(scope="computer", immediate_tasks=(task,))


def _reread_task(collection: GppCollection) -> Any:
    written = serialize_gpp(collection)["ScheduledTasks/ScheduledTasks.xml"]
    reread = parse_gpp_collection("computer", {"ScheduledTasks/ScheduledTasks.xml": written})
    return (reread.scheduled_tasks or reread.immediate_tasks)[0]


@pytest.mark.parametrize("family", ["scheduled", "immediate"])
def test_a_payload_authored_task_keeps_its_payload_when_its_scalars_are_unset(
    family: str,
) -> None:
    """The endpoint lane's shape: a task built from a payload alone, scalars empty."""
    task: Any = (
        GppScheduledTask(name="T", element_variant="TaskV2", task_xml=_PAYLOAD)
        if family == "scheduled"
        else GppImmediateTask(name="T", task_xml=_PAYLOAD)
    )
    again = _reread_task(_payload_authored(task))
    assert (again.program, again.arguments, again.start_in) == ("C:\\Old\\run.exe", "/old", "")


@pytest.mark.parametrize("family", ["scheduled", "immediate"])
@pytest.mark.parametrize("field_name", ["program", "arguments", "start_in"])
def test_a_payload_authored_task_edit_is_written(family: str, field_name: str) -> None:
    base: Any = (
        GppScheduledTask(name="T", element_variant="TaskV2", task_xml=_PAYLOAD)
        if family == "scheduled"
        else GppImmediateTask(name="T", task_xml=_PAYLOAD)
    )
    edited = replace(base, **{field_name: "C:\\\\New"})
    again = _reread_task(_payload_authored(edited))
    assert getattr(again, field_name) == "C:\\\\New"


def test_a_payload_authored_task_disable_is_written_and_an_ambiguous_enable_refused() -> None:
    task = GppScheduledTask(name="T", element_variant="TaskV2", task_xml=_PAYLOAD)
    assert _reread_task(_payload_authored(replace(task, enabled=False))).enabled is False
    disabled = _PAYLOAD.replace("<Enabled>true</Enabled>", "<Enabled>false</Enabled>", 1)
    with pytest.raises(GppError, match="payload says the task is disabled"):
        serialize_gpp(_payload_authored(replace(task, task_xml=disabled)))


def test_a_record_less_schedule_edit_is_refused_even_to_once() -> None:
    task = GppScheduledTask(
        name="T", element_variant="TaskV2", task_xml=_PAYLOAD,
        trigger_type="daily", trigger_time="2026-01-01T03:00:00",
    )
    assert _reread_task(_payload_authored(task)).trigger_type == "daily"
    with pytest.raises(GppError, match="Triggers"):
        serialize_gpp(_payload_authored(replace(task, trigger_type="once")))


def test_a_task_imported_in_a_namespace_has_its_edits_written() -> None:
    """Its element is not retained, so it has no import record."""
    namespaced = SCHED.read_bytes().replace(
        b'<Task version="1.2">',
        b'<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">',
    )
    assert namespaced != SCHED.read_bytes()
    collection = gpp_collection_from_dict(gpp_collection_to_dict(ensure_editor_ids(
        parse_gpp_collection("computer", {"ScheduledTasks/ScheduledTasks.xml": namespaced})
    )))
    task = collection.scheduled_tasks[0]
    assert task.native_xml == "" and task.arguments
    edited = replace(collection, scheduled_tasks=(
        replace(task, arguments="/edited"), *collection.scheduled_tasks[1:],
    ))
    written = serialize_gpp(edited)["ScheduledTasks/ScheduledTasks.xml"]
    again = parse_gpp_collection("computer", {"ScheduledTasks/ScheduledTasks.xml": written})
    assert again.scheduled_tasks[0].arguments == "/edited"


@pytest.mark.parametrize("field_name", ["program", "arguments", "start_in"])
def test_a_task_stored_by_bd84b3a_has_its_edits_written(field_name: str) -> None:
    """The review's case: bd84b3a's own record exported ``/sagerun:1`` after an edit."""
    record = json.loads((BASELINE / "WI01A-SchedTasks-GPMC.json").read_text("utf-8"))
    gpo = gpo_from_dict(record["gpo"])
    collection = next(c for c in gpo.gpp_collections if c.scope == "computer")
    task = collection.scheduled_tasks[0]
    assert task.native_xml == "" and task.arguments == "/sagerun:1"
    edited = replace(collection, scheduled_tasks=(
        replace(task, **{field_name: "C:\\\\Edited"}), *collection.scheduled_tasks[1:],
    ))
    written = serialize_gpp(edited)["ScheduledTasks/ScheduledTasks.xml"]
    assert b"/sagerun:1" not in written or field_name != "arguments"
    again = parse_gpp_collection("computer", {"ScheduledTasks/ScheduledTasks.xml": written})
    assert getattr(again.scheduled_tasks[0], field_name) == "C:\\\\Edited"


def test_a_record_less_task_whose_edit_cannot_reach_the_payload_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The read-back check covers items with no import record too."""
    from gpo_studio import gpp_adapters

    monkeypatch.setattr(gpp_adapters, "_typed_task_payload", lambda task_xml, *a, **k: task_xml)
    task = GppImmediateTask(name="T", task_xml=_PAYLOAD, program=r"C:\\New\\run.exe")
    with pytest.raises(GppError, match="program cannot be written into its <Task> payload"):
        serialize_gpp(_payload_authored(task))


def test_a_scalar_edit_contradicting_an_edited_payload_is_refused() -> None:
    """Both edited and disagreeing: neither silently wins (second review, N4)."""
    collection = _imported(SCHED)
    task = collection.scheduled_tasks[0]
    payload = task.task_xml.replace(task.program, r"C:\\Payload\\edit.exe", 1)
    assert payload != task.task_xml
    contradicting = replace(collection, scheduled_tasks=(
        replace(task, task_xml=payload, program=r"C:\\Scalar\\edit.exe"),
        *collection.scheduled_tasks[1:],
    ))
    with pytest.raises(GppError, match="both task_xml and program were edited"):
        serialize_gpp(contradicting)
    agreeing = replace(collection, scheduled_tasks=(
        replace(task, task_xml=payload, program=r"C:\\Payload\\edit.exe"),
        *collection.scheduled_tasks[1:],
    ))
    again = _reread_task(replace(agreeing, scheduled_tasks=agreeing.scheduled_tasks[:1]))
    assert again.program == r"C:\\Payload\\edit.exe"


def test_an_edit_the_writer_turns_into_a_third_value_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Per scalar, the edited value itself must read back (second review, N2)."""
    from gpo_studio import gpp_adapters

    printers = next(f for f in NATIVE_FILES if f.name == "Printers.xml")
    collection = _imported(printers)
    real = gpp_adapters._ITEM_SERIALIZE_FUNCTIONS["printers"]

    def normalising(printer: Any) -> ET.Element:
        elem = real(printer)
        props = elem.find("Properties")
        if props is not None and props.get("comment") == "Edited":
            props.set("comment", "Something else")
        return elem

    monkeypatch.setitem(gpp_adapters._ITEM_SERIALIZE_FUNCTIONS, "printers", normalising)
    edited = replace(collection, printers=(
        replace(collection.printers[0], comment="Edited"), *collection.printers[1:],
    ))
    with pytest.raises(GppError, match="the edit to comment cannot be written"):
        serialize_gpp(edited)
