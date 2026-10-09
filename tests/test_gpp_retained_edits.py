"""An edit to an imported preference item reaches the exported file, or is refused.

Review of WI-080 (P1, P2): with each item's native element retained, the
writer reconciles it with the model and checks that the result renders as the
model does. That check compares the writer with itself, so a typed value the
writer never puts on the wire is invisible to it: an immediate task's command
lives in its <Task> payload, and an edit to it exported the old command; a
shortcut's name lives only on its item element, and an edit to it exported the
old name. These tests edit every typed value of every item of every native
capture and hold the export to the rule: the edit reads back from the file,
or the export is refused. It never silently reads back as the imported value.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import fields, replace
from pathlib import Path
from types import UnionType
from typing import Any, Literal, Union, get_args, get_origin, get_type_hints

import pytest

from gpo_studio.gpp import (
    GppCollection,
    GppError,
    ensure_editor_ids,
    gpp_collection_from_dict,
    gpp_collection_to_dict,
    parse_gpp_collection,
    serialize_gpp,
)
from gpo_studio.gpp_adapters import ADAPTER_KEYS

ROOT = Path(__file__).resolve().parents[1]
NATIVE_FILES = sorted(
    ROOT.glob("tests/fixtures/native-gpp*/*/*/DomainSysvol/GPO/*/Preferences/*/*.xml")
)
FAMILIES = ("groups", "registry", *ADAPTER_KEYS)
_BOOKKEEPING = {"id", "native_xml", "document_position"}


def _scope(path: Path) -> Any:
    return "computer" if "/Machine/" in path.as_posix() else "user"


def _imported(path: Path) -> GppCollection:
    parsed = parse_gpp_collection(
        _scope(path), {f"{path.parent.name}/{path.name}": path.read_bytes()}
    )
    return gpp_collection_from_dict(gpp_collection_to_dict(ensure_editor_ids(parsed)))


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


def _cases() -> list[tuple[Path, str, int, str]]:
    cases: list[tuple[Path, str, int, str]] = []
    for path in NATIVE_FILES:
        collection = _imported(path)
        for family in FAMILIES:
            for index, item in enumerate(getattr(collection, family)):
                for f in fields(item):
                    if f.name in _BOOKKEEPING or f.name.endswith("password"):
                        continue
                    if _edited(item, f.name) is None:
                        continue
                    cases.append((path, family, index, f.name))
    return cases


CASES = _cases()


def _reread(collection: GppCollection, family: str, index: int) -> Any:
    files = serialize_gpp(collection)
    reread = parse_gpp_collection(collection.scope, files)
    return getattr(reread, family)[index]


@pytest.mark.parametrize(
    ("path", "family", "index", "field_name"),
    CASES,
    ids=[f"{p.parents[5].name}-{fam}{i}-{name}" for p, fam, i, name in CASES],
)
def test_an_edited_typed_value_reaches_the_file_or_is_refused(
    path: Path, family: str, index: int, field_name: str
) -> None:
    collection = _imported(path)
    items = getattr(collection, family)
    item = items[index]
    before = getattr(item, field_name)
    edited_item = replace(item, **{field_name: _edited(item, field_name)})
    edited = replace(
        collection,
        **{family: items[:index] + (edited_item,) + items[index + 1 :]},
    )
    try:
        after = getattr(_reread(edited, family, index), field_name)
    except (GppError, ValueError):
        return  # refused: the operator is told, nothing wrong is exported
    assert after != before, (
        f"{family}[{index}].{field_name}: the edit exported as the imported value {before!r}"
    )


def test_the_edit_matrix_covers_every_family_with_a_capture() -> None:
    covered = {family for _, family, _, _ in CASES}
    assert covered == {
        "groups", "registry", "drives", "environment", "files", "folders", "ini_files",
        "printers", "scheduled_tasks", "immediate_tasks", "services", "shortcuts",
    }
    assert len(CASES) > 300


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
    with pytest.raises(GppError, match="edit to suppress cannot be written"):
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
