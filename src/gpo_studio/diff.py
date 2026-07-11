"""Compute deterministic, setting-aware diffs between GPO snapshots."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from .model import GPO, GPOLink, RegistrySetting


@dataclass(frozen=True, slots=True)
class SettingChange:
    kind: Literal["added", "removed", "modified"]
    identity: tuple[str, str, str, str]
    old: RegistrySetting | None
    new: RegistrySetting | None


@dataclass(frozen=True, slots=True)
class LinkChange:
    kind: Literal["added", "removed", "modified"]
    target: str
    old: GPOLink | None
    new: GPOLink | None


@dataclass(frozen=True, slots=True)
class TwoWayDiff:
    settings: tuple[SettingChange, ...]
    links: tuple[LinkChange, ...]


@dataclass(frozen=True, slots=True)
class ThreeWayConflict:
    identity: tuple[str, str, str, str]
    baseline: RegistrySetting | None
    draft: RegistrySetting | None
    observed: RegistrySetting | None


@dataclass(frozen=True, slots=True)
class ThreeWayDiff:
    settings: tuple[SettingChange, ...]
    links: tuple[LinkChange, ...]
    conflicts: tuple[ThreeWayConflict, ...]


def _settings_equal(a: RegistrySetting, b: RegistrySetting) -> bool:
    return (
        a.value == b.value
        and a.registry_type == b.registry_type
        and a.action == b.action
        and a.comment == b.comment
    )


def _links_equal(a: GPOLink, b: GPOLink) -> bool:
    return a.enabled == b.enabled and a.enforced == b.enforced and a.order == b.order


def diff_settings(
    old: Iterable[RegistrySetting], new: Iterable[RegistrySetting]
) -> list[SettingChange]:
    old_map: dict[tuple[str, str, str, str], RegistrySetting] = {
        s.identity(): s for s in old
    }
    new_map: dict[tuple[str, str, str, str], RegistrySetting] = {
        s.identity(): s for s in new
    }
    changes: list[SettingChange] = []
    for identity, new_setting in new_map.items():
        if identity not in old_map:
            changes.append(
                SettingChange(kind="added", identity=identity, old=None, new=new_setting)
            )
        else:
            old_setting = old_map[identity]
            if not _settings_equal(old_setting, new_setting):
                changes.append(
                    SettingChange(
                        kind="modified", identity=identity, old=old_setting, new=new_setting
                    )
                )
    for identity, old_setting in old_map.items():
        if identity not in new_map:
            changes.append(
                SettingChange(kind="removed", identity=identity, old=old_setting, new=None)
            )
    changes.sort(key=lambda c: c.identity)
    return changes


def diff_links(old: Iterable[GPOLink], new: Iterable[GPOLink]) -> list[LinkChange]:
    old_map: dict[str, GPOLink] = {lnk.target.casefold(): lnk for lnk in old}
    new_map: dict[str, GPOLink] = {lnk.target.casefold(): lnk for lnk in new}
    changes: list[LinkChange] = []
    for target_key, new_link in new_map.items():
        if target_key not in old_map:
            changes.append(
                LinkChange(kind="added", target=new_link.target, old=None, new=new_link)
            )
        else:
            old_link = old_map[target_key]
            if not _links_equal(old_link, new_link):
                changes.append(
                    LinkChange(
                        kind="modified", target=new_link.target, old=old_link, new=new_link
                    )
                )
    for target_key, old_link in old_map.items():
        if target_key not in new_map:
            changes.append(
                LinkChange(kind="removed", target=old_link.target, old=old_link, new=None)
            )
    changes.sort(key=lambda c: c.target.casefold())
    return changes


def diff_gpos(old: GPO, new: GPO) -> TwoWayDiff:
    return TwoWayDiff(
        settings=tuple(diff_settings(old.settings, new.settings)),
        links=tuple(diff_links(old.links, new.links)),
    )


def three_way_diff(baseline: GPO, draft: GPO, observed: GPO) -> ThreeWayDiff:
    draft_changes = diff_settings(baseline.settings, draft.settings)
    observed_changes = diff_settings(baseline.settings, observed.settings)
    observed_by_identity = {c.identity: c for c in observed_changes}
    conflicts: list[ThreeWayConflict] = []
    for draft_change in draft_changes:
        identity = draft_change.identity
        if identity not in observed_by_identity:
            continue
        observed_change = observed_by_identity[identity]
        draft_setting = draft_change.new
        observed_setting = observed_change.new
        if draft_setting is None and observed_setting is None:
            continue
        if draft_setting is None or observed_setting is None:
            conflicts.append(
                ThreeWayConflict(
                    identity=identity,
                    baseline=draft_change.old,
                    draft=draft_setting,
                    observed=observed_setting,
                )
            )
            continue
        if not _settings_equal(draft_setting, observed_setting):
            conflicts.append(
                ThreeWayConflict(
                    identity=identity,
                    baseline=draft_change.old,
                    draft=draft_setting,
                    observed=observed_setting,
                )
            )
    conflicts.sort(key=lambda c: c.identity)
    return ThreeWayDiff(
        settings=tuple(draft_changes),
        links=tuple(diff_links(baseline.links, draft.links)),
        conflicts=tuple(conflicts),
    )
