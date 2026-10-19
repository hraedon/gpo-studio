"""Merge a client's typed GPP edit onto the stored item (final-r13).

Retained content never round-trips through a client. An edit carries the
typed fields Studio authors and the ids of what it keeps; the server merges it
onto the stored item, so every attribute, child, raw predicate and element
record the item was imported with stays exactly where the store holds it, and
only what the edit names changes:

* Targeting is a list of entries in the order the item will have. ``{id}``
  keeps that stored predicate unchanged, whatever it is (typed, a type Studio
  does not author, or raw). ``{id, type, ...}`` edits a stored predicate of an
  authorable type: its retained attributes stay if the type is unchanged and
  are dropped if the type changes (they described the old element). An entry
  without an id is a new typed predicate, with no retained content. A stored
  id the list leaves out is deleted, with exactly its own retained content.
  Raw and non-authorable predicates can therefore be kept, reordered or
  deleted -- never edited.
* Members likewise: an entry naming a stored member's id keeps that member's
  retained attributes; one without an id is a new member; one left out is
  removed.
* A registry item's value keeps its retained attributes.

Positions play no part: a deletion or a reorder cannot move one predicate's
retained attributes onto another (DeepSeek final-r12 B1).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, replace

from .gpp import GppGroup, GppGroupMember, GppRegistry, GppRegistryValue
from .gpp_provenance import predicate_ids
from .ilt import AUTHORABLE_PREDICATE_TYPES, IltFilter, IltPredicate
from .model import ValidationError, ValidationIssue


@dataclass(frozen=True, slots=True)
class PredicateEdit:
    """One targeting entry: keep (``predicate is None``), edit (both set) or add
    (``id is None``)."""

    id: str | None
    predicate: IltPredicate | None


@dataclass(frozen=True, slots=True)
class MemberEdit:
    id: str | None
    member: GppGroupMember


def _refuse(code: str, message: str, path: str) -> ValidationError:
    return ValidationError(
        [ValidationIssue(severity="error", code=code, message=message, path=path)]
    )


def merge_ilt(
    stored: IltFilter | None, entries: Sequence[PredicateEdit] | None, path: str = "ilt_filter"
) -> IltFilter | None:
    """The item's targeting after an edit (see the module doc)."""
    if entries is None:
        return None
    by_id = dict(zip(predicate_ids(stored), stored.items, strict=True)) if stored else {}
    seen: set[str] = set()
    items: list[IltPredicate | str] = []
    ids: list[str] = []
    for index, entry in enumerate(entries):
        where = f"{path}.items[{index}]"
        if entry.id is None:
            if entry.predicate is None:
                raise _refuse("invalid_predicate_edit", "A new predicate needs a type.", where)
            if entry.predicate.type not in AUTHORABLE_PREDICATE_TYPES:
                raise _refuse(
                    "predicate_type_not_authorable",
                    f"Studio does not author {entry.predicate.type!r} predicates.",
                    where,
                )
            items.append(replace(entry.predicate, unknown_attrs=()))
            ids.append(str(uuid.uuid4()))
            continue
        if entry.id in seen:
            raise _refuse(
                "duplicate_predicate_id", f"Predicate {entry.id!r} appears more than once.", where
            )
        seen.add(entry.id)
        if entry.id not in by_id:
            raise _refuse(
                "unknown_predicate_id",
                f"The item has no targeting predicate {entry.id!r}; reload it and retry.",
                where,
            )
        current = by_id[entry.id]
        if entry.predicate is None:
            items.append(current)
        else:
            if (
                not isinstance(current, IltPredicate)
                or current.type not in AUTHORABLE_PREDICATE_TYPES
            ):
                raise _refuse(
                    "predicate_read_only",
                    f"Predicate {entry.id!r} was imported in a form Studio does not edit; "
                    "it can be kept, moved or deleted, but not changed.",
                    where,
                )
            if entry.predicate.type not in AUTHORABLE_PREDICATE_TYPES:
                raise _refuse(
                    "predicate_type_not_authorable",
                    f"Studio does not author {entry.predicate.type!r} predicates.",
                    where,
                )
            # Same element: its retained attributes stay. Another type: they
            # described the old element and go with it.
            retained = current.unknown_attrs if entry.predicate.type == current.type else ()
            items.append(replace(entry.predicate, unknown_attrs=retained))
        ids.append(entry.id)
    if not items:
        return None
    return IltFilter(items=tuple(items), ids=tuple(ids))


def merge_members(
    stored: Sequence[GppGroupMember], edits: Sequence[MemberEdit], path: str = "members"
) -> tuple[GppGroupMember, ...]:
    by_id = {member.id: member for member in stored if member.id}
    seen: set[str] = set()
    merged: list[GppGroupMember] = []
    for index, edit in enumerate(edits):
        where = f"{path}[{index}]"
        if edit.id is None:
            merged.append(replace(edit.member, id="", unknown_attrs=()))
            continue
        if edit.id in seen:
            raise _refuse("duplicate_member_id", f"Member {edit.id!r} appears twice.", where)
        seen.add(edit.id)
        if edit.id not in by_id:
            raise _refuse(
                "unknown_member_id",
                f"The group has no member {edit.id!r}; reload it and retry.",
                where,
            )
        merged.append(merge_member(by_id[edit.id], edit.member))
    return tuple(merged)


def merge_member(stored: GppGroupMember, typed: GppGroupMember) -> GppGroupMember:
    return replace(stored, sid=typed.sid, name=typed.name, action=typed.action)


def merge_value(stored: GppRegistryValue, typed: GppRegistryValue) -> GppRegistryValue:
    return replace(
        stored,
        name=typed.name,
        value=typed.value,
        registry_type=typed.registry_type,
        action=typed.action,
        default=typed.default,
    )


def merge_group(
    stored: GppGroup,
    typed: GppGroup,
    members: Sequence[MemberEdit],
    ilt: Sequence[PredicateEdit] | None,
) -> GppGroup:
    return replace(
        stored,
        name=typed.name,
        sid=typed.sid,
        action=typed.action,
        description=typed.description,
        remove_all_users=typed.remove_all_users,
        remove_all_groups=typed.remove_all_groups,
        members=merge_members(stored.members, members, "group.members"),
        ilt_filter=merge_ilt(stored.ilt_filter, ilt, "group.ilt_filter"),
    )


def merge_registry(
    stored: GppRegistry, typed: GppRegistry, ilt: Sequence[PredicateEdit] | None
) -> GppRegistry:
    return replace(
        stored,
        key=typed.key,
        hive=typed.hive,
        action=typed.action,
        value=merge_value(stored.value, typed.value),
        ilt_filter=merge_ilt(stored.ilt_filter, ilt, "registry.ilt_filter"),
    )


def new_ilt(entries: Sequence[PredicateEdit] | None, path: str = "ilt_filter") -> IltFilter | None:
    """An added item's targeting: new typed predicates only (no ids to name)."""
    if entries is None:
        return None
    for index, entry in enumerate(entries):
        if entry.id is not None:
            raise _refuse(
                "unknown_predicate_id",
                "A new item has no stored predicates to keep; send new predicates only.",
                f"{path}.items[{index}]",
            )
    return merge_ilt(None, entries, path)


def new_members(edits: Sequence[MemberEdit], path: str = "members") -> tuple[GppGroupMember, ...]:
    for index, edit in enumerate(edits):
        if edit.id is not None:
            raise _refuse(
                "unknown_member_id",
                "A new group has no stored members to keep; send new members only.",
                f"{path}[{index}]",
            )
    return merge_members((), edits, path)
