from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gpo_studio.gpp import GppGroup, parse_gpp_groups
from gpo_studio.gpp_adapters import (  # type: ignore[import-untyped]
    GppDrive,
    GppImmediateTask,
    GppScheduledTask,
    parse_gpp_drives,
    parse_gpp_immediate_tasks,
    parse_gpp_scheduled_tasks,
)

ACTION_MAP = {"U": "update", "R": "replace", "D": "remove", "C": "add"}


@dataclass
class Mismatch:
    fixture_id: str
    uid: str
    field_path: str
    expected: object
    actual: object


@dataclass
class ComparisonResult:
    fixture_id: str
    mismatches: list[Mismatch] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return len(self.mismatches) == 0

    def summary(self) -> str:
        if self.ok:
            return f"{self.fixture_id}: all fields match"
        lines = [f"{self.fixture_id}: {len(self.mismatches)} mismatch(es)"]
        for m in self.mismatches:
            lines.append(
                f"  [{m.uid}] {m.field_path}: expected={m.expected!r} actual={m.actual!r}"
            )
        return "\n".join(lines)


def load_manifest(fixture_dir: Path) -> dict[str, Any]:
    result: dict[str, Any] = json.loads(
        (fixture_dir / "semantic-manifest.json").read_text(encoding="utf-8")
    )
    return result


def _find_gpp_xml(fixture_dir: Path, relative: str) -> bytes:
    matches = list(fixture_dir.glob(f"*/DomainSysvol/GPO/{relative}"))
    if len(matches) != 1:
        raise FileNotFoundError(
            f"Expected exactly one match for {relative} in {fixture_dir}, got {len(matches)}"
        )
    return matches[0].read_bytes()


def _gpp_files_from_layout(manifest: dict[str, Any]) -> list[str]:
    return [f.strip() for f in manifest["native_layout"]["gpp_file"].split(",")]


def _compare_drive(
    fixture_id: str, item: dict[str, Any], drive: GppDrive
) -> list[Mismatch]:
    uid = item["uid"]
    mismatches: list[Mismatch] = []

    def check(path: str, expected: object, actual: object) -> None:
        if expected != actual:
            mismatches.append(Mismatch(fixture_id, uid, path, expected, actual))

    check("action", ACTION_MAP[item["action"]], drive.action)
    check("letter", item["drive_letter"], drive.letter)
    if "unc_path" in item:
        check("path", item["unc_path"], drive.path)
    if "label" in item:
        check("label", item["label"], drive.label)
    if "persistent" in item:
        check("persistent", item["persistent"], drive.persistent)
    if "use_specific_letter" in item:
        check("use_letter", item["use_specific_letter"], drive.use_letter)
    return mismatches


def _compare_group(
    fixture_id: str, item: dict[str, Any], group: GppGroup
) -> list[Mismatch]:
    uid = item["uid"]
    mismatches: list[Mismatch] = []

    def check(path: str, expected: object, actual: object) -> None:
        if expected != actual:
            mismatches.append(Mismatch(fixture_id, uid, path, expected, actual))

    check("action", ACTION_MAP[item["action"]], group.action)
    check("group_name", item["group_name"], group.name)
    if "description" in item:
        check("description", item["description"], group.description)
    if "delete_all_users" in item:
        check("delete_all_users", item["delete_all_users"], group.remove_all_users)
    if "delete_all_groups" in item:
        check("delete_all_groups", item["delete_all_groups"], group.remove_all_groups)
    if "members" in item:
        expected_members = item["members"]
        check("members.count", len(expected_members), len(group.members))
        for i, (em, am) in enumerate(zip(expected_members, group.members, strict=False)):
            check(f"members[{i}].name", em["name"], am.name)
            if "action" in em:
                check(f"members[{i}].action", em["action"].lower(), am.action)
            if "sid" in em:
                check(f"members[{i}].sid", em["sid"], am.sid)
    return mismatches


def _compare_immediate_task(
    fixture_id: str, item: dict[str, Any], task: GppImmediateTask
) -> list[Mismatch]:
    uid = item["uid"]
    mismatches: list[Mismatch] = []

    def check(path: str, expected: object, actual: object) -> None:
        if expected != actual:
            mismatches.append(Mismatch(fixture_id, uid, path, expected, actual))

    check("action", ACTION_MAP[item["action"]], task.action)
    check("name", item["task_name"], task.name)
    if "run_as" in item:
        check("run_as", item["run_as"], task.run_as)
    return mismatches


def _compare_scheduled_task(
    fixture_id: str, item: dict[str, Any], task: GppScheduledTask
) -> list[Mismatch]:
    uid = item["uid"]
    mismatches: list[Mismatch] = []

    def check(path: str, expected: object, actual: object) -> None:
        if expected != actual:
            mismatches.append(Mismatch(fixture_id, uid, path, expected, actual))

    check("action", ACTION_MAP[item["action"]], task.action)
    check("name", item["task_name"], task.name)
    if "run_as" in item:
        check("run_as", item["run_as"], task.run_as)
    check("element_variant", "TaskV2", task.element_variant)
    if not task.task_xml:
        mismatches.append(
            Mismatch(fixture_id, uid, "task_xml", "non-empty", "empty")
        )
    if "command" in item:
        check("program", item["command"], task.program)
    if "arguments" in item:
        check("arguments", item["arguments"], task.arguments)
    return mismatches


def compare_fixture(fixture_dir: Path) -> ComparisonResult:
    manifest = load_manifest(fixture_dir)
    fixture_id = manifest["fixture_id"]
    result = ComparisonResult(fixture_id=fixture_id)

    items_by_element: dict[str, list[dict]] = {}
    for item in manifest["items"]:
        items_by_element.setdefault(item["element"], []).append(item)

    scope_files: dict[str, list[str]] = {}
    for gpp_file in _gpp_files_from_layout(manifest):
        scope = "user" if gpp_file.startswith("User/") else "computer"
        scope_files.setdefault(scope, []).append(gpp_file)

    drive_items = items_by_element.get("Drive", [])
    if drive_items:
        parsed_drives: list[GppDrive] = []
        for _scope, files in scope_files.items():
            for f in files:
                if "Drives/" in f:
                    parsed_drives.extend(parse_gpp_drives(_find_gpp_xml(fixture_dir, f)))
        parsed_by_uid = {
            dict(d.unknown_attrs).get("uid", ""): d for d in parsed_drives
        }
        for item in drive_items:
            drive = parsed_by_uid.get(item["uid"])
            if drive is None:
                result.mismatches.append(
                    Mismatch(fixture_id, item["uid"], "<existence>", "present", "missing")
                )
                continue
            result.mismatches.extend(_compare_drive(fixture_id, item, drive))

    group_items = items_by_element.get("Group", [])
    if group_items:
        parsed_groups: list[GppGroup] = []
        for _scope, files in scope_files.items():
            for f in files:
                if "Groups/" in f:
                    parsed_groups.extend(
                        parse_gpp_groups(_find_gpp_xml(fixture_dir, f))
                    )
        parsed_by_uid = {
            dict(g.unknown_attrs).get("uid", ""): g for g in parsed_groups
        }
        for item in group_items:
            group = parsed_by_uid.get(item["uid"])
            if group is None:
                result.mismatches.append(
                    Mismatch(fixture_id, item["uid"], "<existence>", "present", "missing")
                )
                continue
            result.mismatches.extend(_compare_group(fixture_id, item, group))

    immediate_items = items_by_element.get("ImmediateTaskV2", [])
    if immediate_items:
        parsed_tasks: list[GppImmediateTask] = []
        for _scope, files in scope_files.items():
            for f in files:
                if "ScheduledTasks/" in f:
                    parsed_tasks.extend(
                        parse_gpp_immediate_tasks(_find_gpp_xml(fixture_dir, f))
                    )
        parsed_by_uid = {
            dict(t.unknown_attrs).get("uid", ""): t for t in parsed_tasks
        }
        for item in immediate_items:
            task = parsed_by_uid.get(item["uid"])
            if task is None:
                result.mismatches.append(
                    Mismatch(fixture_id, item["uid"], "<existence>", "present", "missing")
                )
                continue
            result.mismatches.extend(_compare_immediate_task(fixture_id, item, task))

    taskv2_items = items_by_element.get("TaskV2", [])
    if taskv2_items:
        parsed_scheduled: list[GppScheduledTask] = []
        for _scope, files in scope_files.items():
            for f in files:
                if "ScheduledTasks/" in f:
                    parsed_scheduled.extend(
                        parse_gpp_scheduled_tasks(_find_gpp_xml(fixture_dir, f))
                    )
        parsed_by_uid = {
            dict(t.unknown_attrs).get("uid", ""): t for t in parsed_scheduled
        }
        for item in taskv2_items:
            task = parsed_by_uid.get(item["uid"])
            if task is None:
                result.mismatches.append(
                    Mismatch(fixture_id, item["uid"], "<existence>", "present", "missing")
                )
                continue
            result.mismatches.extend(_compare_scheduled_task(fixture_id, item, task))

    return result
