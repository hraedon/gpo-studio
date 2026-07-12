"""Compute deterministic, setting-aware diffs between GPO snapshots."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from .model import GPO, GPOLink, RegistrySetting, SecurityFilter, WmiFilter


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
class SecurityFilterChange:
    kind: Literal["added", "removed", "modified"]
    principal: str
    old: SecurityFilter | None
    new: SecurityFilter | None


@dataclass(frozen=True, slots=True)
class WmiFilterChange:
    kind: Literal["added", "removed", "modified"]
    old: WmiFilter | None
    new: WmiFilter | None


@dataclass(frozen=True, slots=True)
class TwoWayDiff:
    settings: tuple[SettingChange, ...]
    links: tuple[LinkChange, ...]
    security_filters: tuple[SecurityFilterChange, ...] = ()
    wmi_filter: WmiFilterChange | None = None


@dataclass(frozen=True, slots=True)
class ThreeWayConflict:
    identity: tuple[str, str, str, str]
    baseline: RegistrySetting | None
    draft: RegistrySetting | None
    observed: RegistrySetting | None


@dataclass(frozen=True, slots=True)
class SecurityFilterConflict:
    principal: str
    baseline: SecurityFilter | None
    draft: SecurityFilter | None
    observed: SecurityFilter | None


@dataclass(frozen=True, slots=True)
class WmiFilterConflict:
    baseline: WmiFilter | None
    draft: WmiFilter | None
    observed: WmiFilter | None


@dataclass(frozen=True, slots=True)
class ThreeWayDiff:
    settings: tuple[SettingChange, ...]
    links: tuple[LinkChange, ...]
    conflicts: tuple[ThreeWayConflict, ...]
    security_filters: tuple[SecurityFilterChange, ...] = ()
    wmi_filter: WmiFilterChange | None = None
    security_filter_conflicts: tuple[SecurityFilterConflict, ...] = ()
    wmi_filter_conflict: WmiFilterConflict | None = None


def _settings_equal(a: RegistrySetting, b: RegistrySetting) -> bool:
    return (
        a.value == b.value
        and a.registry_type == b.registry_type
        and a.action == b.action
        and a.comment == b.comment
    )


def _links_equal(a: GPOLink, b: GPOLink) -> bool:
    return a.enabled == b.enabled and a.enforced == b.enforced and a.order == b.order


def _security_filters_equal(a: SecurityFilter, b: SecurityFilter) -> bool:
    return (
        a.permission == b.permission
        and a.inheritable == b.inheritable
        and a.target_type == b.target_type
    )


def _wmi_filters_equal(a: WmiFilter, b: WmiFilter) -> bool:
    return (
        a.name == b.name
        and a.query == b.query
        and a.language == b.language
        and a.description == b.description
    )


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


def diff_security_filters(
    old: Iterable[SecurityFilter], new: Iterable[SecurityFilter]
) -> list[SecurityFilterChange]:
    old_map: dict[str, SecurityFilter] = {sf.principal.casefold(): sf for sf in old}
    new_map: dict[str, SecurityFilter] = {sf.principal.casefold(): sf for sf in new}
    changes: list[SecurityFilterChange] = []
    for principal_key, new_sf in new_map.items():
        if principal_key not in old_map:
            changes.append(
                SecurityFilterChange(
                    kind="added", principal=new_sf.principal, old=None, new=new_sf
                )
            )
        else:
            old_sf = old_map[principal_key]
            if not _security_filters_equal(old_sf, new_sf):
                changes.append(
                    SecurityFilterChange(
                        kind="modified",
                        principal=new_sf.principal,
                        old=old_sf,
                        new=new_sf,
                    )
                )
    for principal_key, old_sf in old_map.items():
        if principal_key not in new_map:
            changes.append(
                SecurityFilterChange(
                    kind="removed", principal=old_sf.principal, old=old_sf, new=None
                )
            )
    changes.sort(key=lambda c: c.principal.casefold())
    return changes


def _diff_wmi_filter(old: WmiFilter | None, new: WmiFilter | None) -> WmiFilterChange | None:
    if old is None and new is None:
        return None
    if old is None and new is not None:
        return WmiFilterChange(kind="added", old=None, new=new)
    if old is not None and new is None:
        return WmiFilterChange(kind="removed", old=old, new=None)
    assert old is not None and new is not None
    if (
        old.name != new.name
        or old.query != new.query
        or old.language != new.language
        or old.description != new.description
    ):
        return WmiFilterChange(kind="modified", old=old, new=new)
    return None


def diff_gpos(old: GPO, new: GPO) -> TwoWayDiff:
    return TwoWayDiff(
        settings=tuple(diff_settings(old.settings, new.settings)),
        links=tuple(diff_links(old.links, new.links)),
        security_filters=tuple(diff_security_filters(old.security_filters, new.security_filters)),
        wmi_filter=_diff_wmi_filter(old.wmi_filter, new.wmi_filter),
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

    draft_sf_changes = diff_security_filters(baseline.security_filters, draft.security_filters)
    observed_sf_changes = diff_security_filters(
        baseline.security_filters, observed.security_filters
    )
    observed_sf_by_principal = {c.principal.casefold(): c for c in observed_sf_changes}
    sf_conflicts: list[SecurityFilterConflict] = []
    for draft_sf_change in draft_sf_changes:
        key = draft_sf_change.principal.casefold()
        if key not in observed_sf_by_principal:
            continue
        observed_sf_change = observed_sf_by_principal[key]
        draft_sf = draft_sf_change.new
        observed_sf = observed_sf_change.new
        if draft_sf is None and observed_sf is None:
            continue
        if draft_sf is None or observed_sf is None:
            sf_conflicts.append(
                SecurityFilterConflict(
                    principal=draft_sf_change.principal,
                    baseline=draft_sf_change.old,
                    draft=draft_sf,
                    observed=observed_sf,
                )
            )
            continue
        if not _security_filters_equal(draft_sf, observed_sf):
            sf_conflicts.append(
                SecurityFilterConflict(
                    principal=draft_sf_change.principal,
                    baseline=draft_sf_change.old,
                    draft=draft_sf,
                    observed=observed_sf,
                )
            )
    sf_conflicts.sort(key=lambda c: c.principal.casefold())

    draft_wmi_change = _diff_wmi_filter(baseline.wmi_filter, draft.wmi_filter)
    observed_wmi_change = _diff_wmi_filter(baseline.wmi_filter, observed.wmi_filter)
    wmi_conflict: WmiFilterConflict | None = None
    if draft_wmi_change is not None and observed_wmi_change is not None:
        draft_wmi = draft_wmi_change.new
        observed_wmi = observed_wmi_change.new
        if draft_wmi is None and observed_wmi is None:
            pass
        elif (
            draft_wmi is None
            or observed_wmi is None
            or not _wmi_filters_equal(draft_wmi, observed_wmi)
        ):
            wmi_conflict = WmiFilterConflict(
                baseline=baseline.wmi_filter, draft=draft_wmi, observed=observed_wmi
            )

    return ThreeWayDiff(
        settings=tuple(draft_changes),
        links=tuple(diff_links(baseline.links, draft.links)),
        conflicts=tuple(conflicts),
        security_filters=tuple(draft_sf_changes),
        wmi_filter=draft_wmi_change,
        security_filter_conflicts=tuple(sf_conflicts),
        wmi_filter_conflict=wmi_conflict,
    )
