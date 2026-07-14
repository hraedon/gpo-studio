"""Compute deterministic, setting-aware diffs between GPO snapshots."""

from __future__ import annotations

from collections.abc import Hashable, Iterable
from dataclasses import dataclass
from typing import Literal

from .canonical import (
    gpp_group_identity,
    gpp_member_identity,
    gpp_registry_identity,
)
from .gpp import GppCollection, GppGroup, GppRegistry
from .ilt import IltFilter
from .model import (
    GPO,
    CseFileEntry,
    CseMetadataEntry,
    GPOLink,
    RegistrySetting,
    SecurityFilter,
    WmiFilter,
)


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
class GppGroupChange:
    kind: Literal["added", "removed", "modified", "reordered"]
    identity: tuple[str, str]
    scope: str
    old: GppGroup | None
    new: GppGroup | None


@dataclass(frozen=True, slots=True)
class GppRegistryChange:
    kind: Literal["added", "removed", "modified", "reordered"]
    identity: str
    scope: str
    old: GppRegistry | None
    new: GppRegistry | None


@dataclass(frozen=True, slots=True)
class GppCollectionChange:
    kind: Literal["added", "removed", "modified"]
    scope: str
    old: GppCollection | None
    new: GppCollection | None


@dataclass(frozen=True, slots=True)
class MetadataChange:
    field: str
    old: str | bool
    new: str | bool


@dataclass(frozen=True, slots=True)
class MetadataConflict:
    field: str
    baseline: str | bool
    draft: str | bool
    observed: str | bool


@dataclass(frozen=True, slots=True)
class CseMetadataChange:
    kind: Literal["added", "removed", "modified"]
    guid: str
    side: str
    old: CseMetadataEntry | None
    new: CseMetadataEntry | None


@dataclass(frozen=True, slots=True)
class TwoWayDiff:
    settings: tuple[SettingChange, ...]
    links: tuple[LinkChange, ...]
    security_filters: tuple[SecurityFilterChange, ...] = ()
    wmi_filter: WmiFilterChange | None = None
    gpp_groups: tuple[GppGroupChange, ...] = ()
    gpp_registry: tuple[GppRegistryChange, ...] = ()
    gpp_collection: tuple[GppCollectionChange, ...] = ()
    metadata: tuple[MetadataChange, ...] = ()
    cse_metadata: tuple[CseMetadataChange, ...] = ()


@dataclass(frozen=True, slots=True)
class ThreeWayConflict:
    identity: tuple[str, str, str, str]
    baseline: RegistrySetting | None
    draft: RegistrySetting | None
    observed: RegistrySetting | None


@dataclass(frozen=True, slots=True)
class LinkConflict:
    identity: str
    baseline: GPOLink | None
    draft: GPOLink | None
    observed: GPOLink | None


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
class CseMetadataConflict:
    guid: str
    side: str
    baseline: CseMetadataEntry | None
    draft: CseMetadataEntry | None
    observed: CseMetadataEntry | None


@dataclass(frozen=True, slots=True)
class GppGroupConflict:
    kind: Literal["group"]
    scope: str
    identity: tuple[str, str]
    baseline: GppGroup | None
    draft: GppGroup | None
    observed: GppGroup | None


@dataclass(frozen=True, slots=True)
class GppRegistryConflict:
    kind: Literal["registry"]
    scope: str
    identity: str
    baseline: GppRegistry | None
    draft: GppRegistry | None
    observed: GppRegistry | None


@dataclass(frozen=True, slots=True)
class GppReorderConflict:
    element_type: Literal["group", "registry"]
    scope: str
    baseline_order: tuple[str, ...]
    draft_order: tuple[str, ...]
    observed_order: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GppCollectionConflict:
    scope: str
    baseline: GppCollection | None
    draft: GppCollection | None
    observed: GppCollection | None


@dataclass(frozen=True, slots=True)
class ThreeWayDiff:
    settings: tuple[SettingChange, ...]
    links: tuple[LinkChange, ...]
    conflicts: tuple[ThreeWayConflict, ...]
    security_filters: tuple[SecurityFilterChange, ...] = ()
    wmi_filter: WmiFilterChange | None = None
    security_filter_conflicts: tuple[SecurityFilterConflict, ...] = ()
    wmi_filter_conflict: WmiFilterConflict | None = None
    link_conflicts: tuple[LinkConflict, ...] = ()
    gpp_groups: tuple[GppGroupChange, ...] = ()
    gpp_registry: tuple[GppRegistryChange, ...] = ()
    gpp_collection: tuple[GppCollectionChange, ...] = ()
    gpp_conflicts: tuple[GppGroupConflict | GppRegistryConflict, ...] = ()
    gpp_reorder_conflicts: tuple[GppReorderConflict, ...] = ()
    gpp_collection_conflicts: tuple[GppCollectionConflict, ...] = ()
    metadata: tuple[MetadataChange, ...] = ()
    metadata_conflicts: tuple[MetadataConflict, ...] = ()
    cse_metadata: tuple[CseMetadataChange, ...] = ()
    cse_metadata_conflicts: tuple[CseMetadataConflict, ...] = ()


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
        and a.principal.casefold() == b.principal.casefold()
        and a.sid.lower() == b.sid.lower()
    )


def _wmi_filters_equal(a: WmiFilter, b: WmiFilter) -> bool:
    return (
        a.name == b.name
        and a.query == b.query
        and a.language == b.language
        and a.description == b.description
    )


def _cse_files_equal(a: tuple[CseFileEntry, ...], b: tuple[CseFileEntry, ...]) -> bool:
    a_files = {f.relative_path: f for f in a}
    b_files = {f.relative_path: f for f in b}
    if a_files.keys() != b_files.keys():
        return False
    for path, a_file in a_files.items():
        b_file = b_files[path]
        if (
            a_file.content_hash != b_file.content_hash
            or a_file.size != b_file.size
        ):
            return False
    return True


def _cse_metadata_equal(a: CseMetadataEntry, b: CseMetadataEntry) -> bool:
    return a.guid == b.guid and a.side == b.side and _cse_files_equal(a.files, b.files)


def _ilt_equal(a: IltFilter | None, b: IltFilter | None) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return a.items == b.items


def _gpp_members_equal(a: GppGroup, b: GppGroup) -> bool:
    a_seq = [
        (
            gpp_member_identity(m),
            m.sid.lower(),
            m.name,
            m.action,
            m.unknown_attrs,
        )
        for m in a.members
    ]
    b_seq = [
        (
            gpp_member_identity(m),
            m.sid.lower(),
            m.name,
            m.action,
            m.unknown_attrs,
        )
        for m in b.members
    ]
    return a_seq == b_seq


def _gpp_groups_equal(a: GppGroup, b: GppGroup) -> bool:
    return (
        a.name.casefold() == b.name.casefold()
        and a.sid.lower() == b.sid.lower()
        and a.action == b.action
        and a.description == b.description
        and a.remove_all_users == b.remove_all_users
        and a.remove_all_groups == b.remove_all_groups
        and a.unknown_attrs == b.unknown_attrs
        and a.unknown_props_attrs == b.unknown_props_attrs
        and a.unknown_children == b.unknown_children
        and _gpp_members_equal(a, b)
        and _ilt_equal(a.ilt_filter, b.ilt_filter)
    )


def _gpp_registry_value_equal(a: GppRegistry, b: GppRegistry) -> bool:
    av, bv = a.value, b.value
    return (
        av.name.casefold() == bv.name.casefold()
        and av.registry_type == bv.registry_type
        and av.value == bv.value
        and av.action == bv.action
        and av.default == bv.default
        and av.unknown_attrs == bv.unknown_attrs
    )


def _gpp_registry_equal(a: GppRegistry, b: GppRegistry) -> bool:
    return (
        a.key.casefold() == b.key.casefold()
        and a.hive.casefold() == b.hive.casefold()
        and a.action == b.action
        and a.uid == b.uid
        and a.unknown_attrs == b.unknown_attrs
        and a.unknown_children == b.unknown_children
        and _ilt_equal(a.ilt_filter, b.ilt_filter)
        and _gpp_registry_value_equal(a, b)
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


def _reordered_common_identities[IdentityT](
    old_identities: list[IdentityT],
    new_identities: list[IdentityT],
) -> list[IdentityT]:
    old_id_set = set(old_identities)
    new_id_set = set(new_identities)
    common_old = [ident for ident in old_identities if ident in new_id_set]
    common_new = [ident for ident in new_identities if ident in old_id_set]
    if common_old == common_new:
        return []
    old_positions = {ident: idx for idx, ident in enumerate(common_old)}
    reordered: list[IdentityT] = []
    for new_idx, ident in enumerate(common_new):
        if old_positions.get(ident, -1) != new_idx:
            reordered.append(ident)
    return reordered


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


def _diff_gpp_groups(
    old: Iterable[GppGroup], new: Iterable[GppGroup], scope: str
) -> list[GppGroupChange]:
    old_groups = tuple(old)
    new_groups = tuple(new)
    old_map: dict[tuple[str, str], GppGroup] = {
        gpp_group_identity(g): g for g in old_groups
    }
    new_map: dict[tuple[str, str], GppGroup] = {
        gpp_group_identity(g): g for g in new_groups
    }
    changes: list[GppGroupChange] = []
    for identity, new_group in new_map.items():
        if identity not in old_map:
            changes.append(
                GppGroupChange(
                    kind="added", identity=identity, scope=scope, old=None, new=new_group
                )
            )
        else:
            old_group = old_map[identity]
            if not _gpp_groups_equal(old_group, new_group):
                changes.append(
                    GppGroupChange(
                        kind="modified",
                        identity=identity,
                        scope=scope,
                        old=old_group,
                        new=new_group,
                    )
                )
    for identity, old_group in old_map.items():
        if identity not in new_map:
            changes.append(
                GppGroupChange(
                    kind="removed", identity=identity, scope=scope, old=old_group, new=None
                )
            )
    for identity in _reordered_common_identities(
        [gpp_group_identity(g) for g in old_groups],
        [gpp_group_identity(g) for g in new_groups],
    ):
        changes.append(
            GppGroupChange(
                kind="reordered",
                identity=identity,
                scope=scope,
                old=old_map[identity],
                new=new_map[identity],
            )
        )
    changes.sort(key=lambda c: c.identity)
    return changes


def _diff_gpp_registry(
    old: Iterable[GppRegistry], new: Iterable[GppRegistry], scope: str
) -> list[GppRegistryChange]:
    old_registry = tuple(old)
    new_registry = tuple(new)
    old_map: dict[str, GppRegistry] = {gpp_registry_identity(r): r for r in old_registry}
    new_map: dict[str, GppRegistry] = {gpp_registry_identity(r): r for r in new_registry}
    changes: list[GppRegistryChange] = []
    for identity, new_reg in new_map.items():
        if identity not in old_map:
            changes.append(
                GppRegistryChange(
                    kind="added", identity=identity, scope=scope, old=None, new=new_reg
                )
            )
        else:
            old_reg = old_map[identity]
            if not _gpp_registry_equal(old_reg, new_reg):
                changes.append(
                    GppRegistryChange(
                        kind="modified",
                        identity=identity,
                        scope=scope,
                        old=old_reg,
                        new=new_reg,
                    )
                )
    for identity, old_reg in old_map.items():
        if identity not in new_map:
            changes.append(
                GppRegistryChange(
                    kind="removed", identity=identity, scope=scope, old=old_reg, new=None
                )
            )
    for identity in _reordered_common_identities(
        [gpp_registry_identity(r) for r in old_registry],
        [gpp_registry_identity(r) for r in new_registry],
    ):
        changes.append(
            GppRegistryChange(
                kind="reordered",
                identity=identity,
                scope=scope,
                old=old_map[identity],
                new=new_map[identity],
            )
        )
    changes.sort(key=lambda c: c.identity)
    return changes


def _gpp_collection_equal(a: GppCollection, b: GppCollection) -> bool:
    return (
        a.groups_unknown_attrs == b.groups_unknown_attrs
        and a.groups_unknown_children == b.groups_unknown_children
        and a.registry_unknown_attrs == b.registry_unknown_attrs
        and a.registry_unknown_children == b.registry_unknown_children
    )


def _diff_gpp_collections(
    old: tuple[GppCollection, ...], new: tuple[GppCollection, ...]
) -> list[GppCollectionChange]:
    old_map: dict[str, GppCollection] = {c.scope: c for c in old}
    new_map: dict[str, GppCollection] = {c.scope: c for c in new}
    scopes = set(old_map.keys()) | set(new_map.keys())
    changes: list[GppCollectionChange] = []
    for scope in sorted(scopes):
        old_c = old_map.get(scope)
        new_c = new_map.get(scope)
        if old_c is None and new_c is not None:
            changes.append(GppCollectionChange(
                kind="added", scope=scope, old=None, new=new_c
            ))
        elif new_c is None and old_c is not None:
            changes.append(GppCollectionChange(
                kind="removed", scope=scope, old=old_c, new=None
            ))
        elif old_c is not None and new_c is not None and not _gpp_collection_equal(old_c, new_c):
            changes.append(GppCollectionChange(
                kind="modified", scope=scope, old=old_c, new=new_c
            ))
    return changes


def diff_gpp(
    old: tuple[GppCollection, ...], new: tuple[GppCollection, ...]
) -> tuple[tuple[GppGroupChange, ...], tuple[GppRegistryChange, ...]]:
    old_map: dict[str, GppCollection] = {c.scope: c for c in old}
    new_map: dict[str, GppCollection] = {c.scope: c for c in new}
    scopes = set(old_map.keys()) | set(new_map.keys())
    group_changes: list[GppGroupChange] = []
    registry_changes: list[GppRegistryChange] = []
    for scope in sorted(scopes):
        old_collection = old_map.get(scope)
        new_collection = new_map.get(scope)
        old_groups = old_collection.groups if old_collection is not None else ()
        new_groups = new_collection.groups if new_collection is not None else ()
        old_registry = old_collection.registry if old_collection is not None else ()
        new_registry = new_collection.registry if new_collection is not None else ()
        group_changes.extend(_diff_gpp_groups(old_groups, new_groups, scope))
        registry_changes.extend(_diff_gpp_registry(old_registry, new_registry, scope))
    return (tuple(group_changes), tuple(registry_changes))


def diff_cse_metadata(
    old: Iterable[CseMetadataEntry], new: Iterable[CseMetadataEntry]
) -> list[CseMetadataChange]:
    old_map: dict[tuple[str, str], CseMetadataEntry] = {
        (e.guid, e.side): e for e in old
    }
    new_map: dict[tuple[str, str], CseMetadataEntry] = {
        (e.guid, e.side): e for e in new
    }
    changes: list[CseMetadataChange] = []
    for identity, new_entry in new_map.items():
        if identity not in old_map:
            changes.append(
                CseMetadataChange(
                    kind="added",
                    guid=new_entry.guid,
                    side=new_entry.side,
                    old=None,
                    new=new_entry,
                )
            )
        else:
            old_entry = old_map[identity]
            if not _cse_metadata_equal(old_entry, new_entry):
                changes.append(
                    CseMetadataChange(
                        kind="modified",
                        guid=new_entry.guid,
                        side=new_entry.side,
                        old=old_entry,
                        new=new_entry,
                    )
                )
    for identity, old_entry in old_map.items():
        if identity not in new_map:
            changes.append(
                CseMetadataChange(
                    kind="removed",
                    guid=old_entry.guid,
                    side=old_entry.side,
                    old=old_entry,
                    new=None,
                )
            )
    changes.sort(key=lambda c: (c.guid, c.side))
    return changes


def _diff_metadata(old: GPO, new: GPO) -> list[MetadataChange]:
    changes: list[MetadataChange] = []
    fields: list[tuple[str, str | bool, str | bool]] = [
        ("name", old.name, new.name),
        ("description", old.description, new.description),
        ("computer_enabled", old.computer_enabled, new.computer_enabled),
        ("user_enabled", old.user_enabled, new.user_enabled),
        ("domain", old.domain, new.domain),
        ("status", old.status, new.status),
        ("source_guid", old.source_guid, new.source_guid),
    ]
    for field_name, old_value, new_value in fields:
        if old_value != new_value:
            changes.append(
                MetadataChange(field=field_name, old=old_value, new=new_value)
            )
    changes.sort(key=lambda c: c.field)
    return changes


def diff_gpos(old: GPO, new: GPO) -> TwoWayDiff:
    gpp_groups, gpp_registry = diff_gpp(old.gpp_collections, new.gpp_collections)
    gpp_collection = _diff_gpp_collections(
        old.gpp_collections, new.gpp_collections
    )
    return TwoWayDiff(
        settings=tuple(diff_settings(old.settings, new.settings)),
        links=tuple(diff_links(old.links, new.links)),
        security_filters=tuple(
            diff_security_filters(old.security_filters, new.security_filters)
        ),
        wmi_filter=_diff_wmi_filter(old.wmi_filter, new.wmi_filter),
        gpp_groups=gpp_groups,
        gpp_registry=gpp_registry,
        gpp_collection=tuple(gpp_collection),
        metadata=tuple(_diff_metadata(old, new)),
        cse_metadata=tuple(diff_cse_metadata(old.cse_metadata, new.cse_metadata)),
    )


def _three_way_setting_conflicts(
    baseline: tuple[RegistrySetting, ...],
    draft: tuple[RegistrySetting, ...],
    observed: tuple[RegistrySetting, ...],
) -> tuple[ThreeWayConflict, ...]:
    draft_changes = diff_settings(baseline, draft)
    observed_changes = diff_settings(baseline, observed)
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
    return tuple(conflicts)


def _three_way_link_conflicts(
    baseline: tuple[GPOLink, ...],
    draft: tuple[GPOLink, ...],
    observed: tuple[GPOLink, ...],
) -> tuple[LinkConflict, ...]:
    draft_changes = diff_links(baseline, draft)
    observed_changes = diff_links(baseline, observed)
    observed_by_target: dict[str, LinkChange] = {
        c.target.casefold(): c for c in observed_changes
    }
    conflicts: list[LinkConflict] = []
    for draft_change in draft_changes:
        target_key = draft_change.target.casefold()
        if target_key not in observed_by_target:
            continue
        observed_change = observed_by_target[target_key]
        draft_link = draft_change.new
        observed_link = observed_change.new
        if draft_link is None and observed_link is None:
            continue
        if draft_link is None or observed_link is None:
            conflicts.append(
                LinkConflict(
                    identity=draft_change.target,
                    baseline=draft_change.old,
                    draft=draft_link,
                    observed=observed_link,
                )
            )
            continue
        if not _links_equal(draft_link, observed_link):
            conflicts.append(
                LinkConflict(
                    identity=draft_change.target,
                    baseline=draft_change.old,
                    draft=draft_link,
                    observed=observed_link,
                )
            )
    conflicts.sort(key=lambda c: c.identity.casefold())
    return tuple(conflicts)


def _three_way_security_filter_conflicts(
    baseline: tuple[SecurityFilter, ...],
    draft: tuple[SecurityFilter, ...],
    observed: tuple[SecurityFilter, ...],
) -> tuple[SecurityFilterConflict, ...]:
    draft_sf_changes = diff_security_filters(baseline, draft)
    observed_sf_changes = diff_security_filters(baseline, observed)
    observed_sf_by_principal: dict[str, SecurityFilterChange] = {
        c.principal.casefold(): c for c in observed_sf_changes
    }
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
    return tuple(sf_conflicts)


def _three_way_wmi_filter_conflict(
    baseline: WmiFilter | None,
    draft: WmiFilter | None,
    observed: WmiFilter | None,
) -> WmiFilterConflict | None:
    draft_wmi_change = _diff_wmi_filter(baseline, draft)
    observed_wmi_change = _diff_wmi_filter(baseline, observed)
    if draft_wmi_change is None or observed_wmi_change is None:
        return None
    draft_wmi = draft_wmi_change.new
    observed_wmi = observed_wmi_change.new
    if draft_wmi is None and observed_wmi is None:
        return None
    if draft_wmi is None or observed_wmi is None or not _wmi_filters_equal(
        draft_wmi, observed_wmi
    ):
        return WmiFilterConflict(baseline=baseline, draft=draft_wmi, observed=observed_wmi)
    return None


def _three_way_gpp_conflicts(
    baseline: tuple[GppCollection, ...],
    draft: tuple[GppCollection, ...],
    observed: tuple[GppCollection, ...],
) -> tuple[GppGroupConflict | GppRegistryConflict, ...]:
    draft_groups, draft_registry = diff_gpp(baseline, draft)
    observed_groups, observed_registry = diff_gpp(baseline, observed)
    observed_group_map: dict[tuple[str, tuple[str, str]], GppGroupChange] = {
        (c.scope, c.identity): c for c in observed_groups
    }
    observed_registry_map: dict[tuple[str, str], GppRegistryChange] = {
        (c.scope, c.identity): c for c in observed_registry
    }
    conflicts: list[GppGroupConflict | GppRegistryConflict] = []

    for draft_group_change in draft_groups:
        group_key = (draft_group_change.scope, draft_group_change.identity)
        if group_key not in observed_group_map:
            continue
        observed_group_change = observed_group_map[group_key]
        draft_group = draft_group_change.new
        observed_group = observed_group_change.new
        if draft_group is None and observed_group is None:
            continue
        if draft_group is None or observed_group is None:
            conflicts.append(
                GppGroupConflict(
                    kind="group",
                    scope=draft_group_change.scope,
                    identity=draft_group_change.identity,
                    baseline=draft_group_change.old,
                    draft=draft_group,
                    observed=observed_group,
                )
            )
            continue
        if not _gpp_groups_equal(draft_group, observed_group):
            conflicts.append(
                GppGroupConflict(
                    kind="group",
                    scope=draft_group_change.scope,
                    identity=draft_group_change.identity,
                    baseline=draft_group_change.old,
                    draft=draft_group,
                    observed=observed_group,
                )
            )

    for draft_reg_change in draft_registry:
        reg_key = (draft_reg_change.scope, draft_reg_change.identity)
        if reg_key not in observed_registry_map:
            continue
        observed_reg_change = observed_registry_map[reg_key]
        draft_reg = draft_reg_change.new
        observed_reg = observed_reg_change.new
        if draft_reg is None and observed_reg is None:
            continue
        if draft_reg is None or observed_reg is None:
            conflicts.append(
                GppRegistryConflict(
                    kind="registry",
                    scope=draft_reg_change.scope,
                    identity=draft_reg_change.identity,
                    baseline=draft_reg_change.old,
                    draft=draft_reg,
                    observed=observed_reg,
                )
            )
            continue
        if not _gpp_registry_equal(draft_reg, observed_reg):
            conflicts.append(
                GppRegistryConflict(
                    kind="registry",
                    scope=draft_reg_change.scope,
                    identity=draft_reg_change.identity,
                    baseline=draft_reg_change.old,
                    draft=draft_reg,
                    observed=observed_reg,
                )
            )

    conflicts.sort(key=lambda c: (c.scope, c.kind, str(c.identity)))
    return tuple(conflicts)


def _gpp_collection_reorder_conflict[IdentityT: Hashable](
    element_type: Literal["group", "registry"],
    scope: str,
    baseline_identities: list[IdentityT],
    draft_identities: list[IdentityT],
    observed_identities: list[IdentityT],
) -> GppReorderConflict | None:
    common = (
        set(baseline_identities)
        & set(draft_identities)
        & set(observed_identities)
    )
    b_order = tuple(str(i) for i in baseline_identities if i in common)
    d_order = tuple(str(i) for i in draft_identities if i in common)
    o_order = tuple(str(i) for i in observed_identities if i in common)
    if d_order != b_order and o_order != b_order and d_order != o_order:
        return GppReorderConflict(
            element_type=element_type,
            scope=scope,
            baseline_order=b_order,
            draft_order=d_order,
            observed_order=o_order,
        )
    return None


def _three_way_gpp_reorder_conflicts(
    baseline: tuple[GppCollection, ...],
    draft: tuple[GppCollection, ...],
    observed: tuple[GppCollection, ...],
) -> tuple[GppReorderConflict, ...]:
    baseline_map: dict[str, GppCollection] = {c.scope: c for c in baseline}
    draft_map: dict[str, GppCollection] = {c.scope: c for c in draft}
    observed_map: dict[str, GppCollection] = {c.scope: c for c in observed}
    scopes = set(baseline_map) | set(draft_map) | set(observed_map)
    conflicts: list[GppReorderConflict] = []
    for scope in sorted(scopes):
        baseline_collection = baseline_map.get(scope)
        draft_collection = draft_map.get(scope)
        observed_collection = observed_map.get(scope)
        baseline_groups = baseline_collection.groups if baseline_collection is not None else ()
        draft_groups = draft_collection.groups if draft_collection is not None else ()
        observed_groups = observed_collection.groups if observed_collection is not None else ()
        group_conflict = _gpp_collection_reorder_conflict(
            element_type="group",
            scope=scope,
            baseline_identities=[gpp_group_identity(g) for g in baseline_groups],
            draft_identities=[gpp_group_identity(g) for g in draft_groups],
            observed_identities=[gpp_group_identity(g) for g in observed_groups],
        )
        if group_conflict is not None:
            conflicts.append(group_conflict)
        baseline_registry = baseline_collection.registry if baseline_collection is not None else ()
        draft_registry = draft_collection.registry if draft_collection is not None else ()
        observed_registry = observed_collection.registry if observed_collection is not None else ()
        registry_conflict = _gpp_collection_reorder_conflict(
            element_type="registry",
            scope=scope,
            baseline_identities=[gpp_registry_identity(r) for r in baseline_registry],
            draft_identities=[gpp_registry_identity(r) for r in draft_registry],
            observed_identities=[gpp_registry_identity(r) for r in observed_registry],
        )
        if registry_conflict is not None:
            conflicts.append(registry_conflict)
    conflicts.sort(key=lambda c: (c.element_type, c.scope))
    return tuple(conflicts)


def _three_way_gpp_collection_conflicts(
    baseline: tuple[GppCollection, ...],
    draft: tuple[GppCollection, ...],
    observed: tuple[GppCollection, ...],
) -> tuple[GppCollectionConflict, ...]:
    draft_changes = _diff_gpp_collections(baseline, draft)
    observed_changes = _diff_gpp_collections(baseline, observed)
    observed_by_scope: dict[str, GppCollectionChange] = {
        c.scope: c for c in observed_changes
    }
    conflicts: list[GppCollectionConflict] = []
    for draft_change in draft_changes:
        if draft_change.scope not in observed_by_scope:
            continue
        observed_change = observed_by_scope[draft_change.scope]
        draft_c = draft_change.new
        observed_c = observed_change.new
        baseline_c = draft_change.old

        if draft_c is None and observed_c is None:
            continue
        if draft_c is None or observed_c is None:
            conflicts.append(
                GppCollectionConflict(
                    scope=draft_change.scope,
                    baseline=baseline_c,
                    draft=draft_c,
                    observed=observed_c,
                )
            )
            continue

        if baseline_c is None:
            baseline_c = GppCollection(scope=draft_change.scope)  # type: ignore[arg-type]

        if _gpp_collection_root_fields_conflict(
            baseline_c, draft_c, observed_c
        ):
            conflicts.append(
                GppCollectionConflict(
                    scope=draft_change.scope,
                    baseline=baseline_c,
                    draft=draft_c,
                    observed=observed_c,
                )
            )
    conflicts.sort(key=lambda c: c.scope)
    return tuple(conflicts)


def _gpp_collection_root_fields_conflict(
    baseline: GppCollection,
    draft: GppCollection,
    observed: GppCollection,
) -> bool:
    """Return True only when draft and observed changed the SAME root field
    to DIFFERENT values.

    Independent changes to different root metadata fields (e.g. draft
    changes groups_unknown_attrs while observed changes
    registry_unknown_attrs) touch different XML files and can be
    reconciled independently — they are not conflicts.

    Convergent changes (both changed the same field to the same value)
    are also not conflicts.
    """
    root_field_pairs = [
        ("groups_unknown_attrs", "groups_unknown_children"),
        ("registry_unknown_attrs", "registry_unknown_children"),
    ]
    for attrs_field, children_field in root_field_pairs:
        b_attrs = getattr(baseline, attrs_field)
        d_attrs = getattr(draft, attrs_field)
        o_attrs = getattr(observed, attrs_field)
        if d_attrs != b_attrs and o_attrs != b_attrs and d_attrs != o_attrs:
            return True
        b_children = getattr(baseline, children_field)
        d_children = getattr(draft, children_field)
        o_children = getattr(observed, children_field)
        if (
            d_children != b_children
            and o_children != b_children
            and d_children != o_children
        ):
            return True
    return False


def _three_way_cse_metadata_conflicts(
    baseline: tuple[CseMetadataEntry, ...],
    draft: tuple[CseMetadataEntry, ...],
    observed: tuple[CseMetadataEntry, ...],
) -> tuple[CseMetadataConflict, ...]:
    draft_changes = diff_cse_metadata(baseline, draft)
    observed_changes = diff_cse_metadata(baseline, observed)
    observed_by_identity: dict[tuple[str, str], CseMetadataChange] = {
        ((c.guid, c.side)): c for c in observed_changes
    }
    conflicts: list[CseMetadataConflict] = []
    for draft_change in draft_changes:
        identity = (draft_change.guid, draft_change.side)
        if identity not in observed_by_identity:
            continue
        observed_change = observed_by_identity[identity]
        draft_entry = draft_change.new
        observed_entry = observed_change.new
        if draft_entry is None and observed_entry is None:
            continue
        if draft_entry is None or observed_entry is None:
            conflicts.append(
                CseMetadataConflict(
                    guid=draft_change.guid,
                    side=draft_change.side,
                    baseline=draft_change.old,
                    draft=draft_entry,
                    observed=observed_entry,
                )
            )
            continue
        if not _cse_metadata_equal(draft_entry, observed_entry):
            conflicts.append(
                CseMetadataConflict(
                    guid=draft_change.guid,
                    side=draft_change.side,
                    baseline=draft_change.old,
                    draft=draft_entry,
                    observed=observed_entry,
                )
            )
    conflicts.sort(key=lambda c: (c.guid, c.side))
    return tuple(conflicts)


def _three_way_metadata_conflicts(
    baseline: GPO, draft: GPO, observed: GPO
) -> tuple[MetadataConflict, ...]:
    draft_meta = _diff_metadata(baseline, draft)
    observed_meta = _diff_metadata(baseline, observed)
    observed_meta_by_field: dict[str, MetadataChange] = {
        c.field: c for c in observed_meta
    }
    conflicts: list[MetadataConflict] = []
    for draft_change in draft_meta:
        if draft_change.field not in observed_meta_by_field:
            continue
        observed_change = observed_meta_by_field[draft_change.field]
        if draft_change.new != observed_change.new:
            conflicts.append(
                MetadataConflict(
                    field=draft_change.field,
                    baseline=draft_change.old,
                    draft=draft_change.new,
                    observed=observed_change.new,
                )
            )
    conflicts.sort(key=lambda c: c.field)
    return tuple(conflicts)


def three_way_diff(baseline: GPO, draft: GPO, observed: GPO) -> ThreeWayDiff:
    conflicts = _three_way_setting_conflicts(
        baseline.settings, draft.settings, observed.settings
    )
    sf_conflicts = _three_way_security_filter_conflicts(
        baseline.security_filters, draft.security_filters, observed.security_filters
    )
    wmi_conflict = _three_way_wmi_filter_conflict(
        baseline.wmi_filter, draft.wmi_filter, observed.wmi_filter
    )
    link_conflicts = _three_way_link_conflicts(
        baseline.links, draft.links, observed.links
    )
    gpp_groups, gpp_registry = diff_gpp(
        baseline.gpp_collections, draft.gpp_collections
    )
    gpp_conflicts = _three_way_gpp_conflicts(
        baseline.gpp_collections, draft.gpp_collections, observed.gpp_collections
    )
    gpp_reorder_conflicts = _three_way_gpp_reorder_conflicts(
        baseline.gpp_collections, draft.gpp_collections, observed.gpp_collections
    )
    gpp_collection_conflicts = _three_way_gpp_collection_conflicts(
        baseline.gpp_collections, draft.gpp_collections, observed.gpp_collections
    )
    cse_metadata_conflicts = _three_way_cse_metadata_conflicts(
        baseline.cse_metadata, draft.cse_metadata, observed.cse_metadata
    )
    metadata_conflicts = _three_way_metadata_conflicts(baseline, draft, observed)

    return ThreeWayDiff(
        settings=tuple(diff_settings(baseline.settings, draft.settings)),
        links=tuple(diff_links(baseline.links, draft.links)),
        conflicts=conflicts,
        security_filters=tuple(
            diff_security_filters(baseline.security_filters, draft.security_filters)
        ),
        wmi_filter=_diff_wmi_filter(baseline.wmi_filter, draft.wmi_filter),
        security_filter_conflicts=sf_conflicts,
        wmi_filter_conflict=wmi_conflict,
        link_conflicts=link_conflicts,
        gpp_groups=gpp_groups,
        gpp_registry=gpp_registry,
        gpp_collection=tuple(_diff_gpp_collections(
            baseline.gpp_collections, draft.gpp_collections
        )),
        gpp_conflicts=gpp_conflicts,
        gpp_reorder_conflicts=gpp_reorder_conflicts,
        gpp_collection_conflicts=gpp_collection_conflicts,
        metadata=tuple(_diff_metadata(baseline, draft)),
        metadata_conflicts=metadata_conflicts,
        cse_metadata=tuple(diff_cse_metadata(baseline.cse_metadata, draft.cse_metadata)),
        cse_metadata_conflicts=cse_metadata_conflicts,
    )
