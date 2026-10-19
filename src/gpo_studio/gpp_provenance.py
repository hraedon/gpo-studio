"""Where retained GPP content came from, recorded when it is acquired (final-r13).

An imported preference item carries content Studio's model does not type:
retained attributes and children, raw targeting predicates, the typed
predicates' unknown attributes, member and value attributes, the item's
element as Windows wrote it (``native_xml``) and a task's payload. Egress lets
an element through that the closed vocabulary does not name only when it is
retained content an IMPORT stored -- so that provenance is recorded, not
inferred from the content being present.

Every retained piece is a *fragment* with a key that binds it to its place:
the family, the item id, the field, and for targeting, member and value
content the id of the predicate, member or value it belongs to. A
collection's ``retained_provenance`` holds the digest of every fragment an
import acquired. Import mints the records (`mint`); fork copies them with the
collection; clone re-keys the source item's records onto the copy's fresh ids
(`carried_records`); an item restore copies the records the named revision
holds for that item (`records_for`), and never creates one. A mutation keeps
the records of the fragments it keeps and loses the rest (`keep_records`):
nothing but an acquisition adds a record. A fragment without a record is
*unverified*: kept and shown, but judged by the closed vocabulary at egress.

The keys are bound to ids, not positions, so deleting or reordering a
predicate, member or child cannot move a record onto another piece, and the
same piece copied into another item is a new fragment (Sol final-r12 N1).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import uuid
from collections import Counter
from collections.abc import Iterable, Iterator
from typing import Any

from .ilt import IltFilter, IltPredicate
from .xml_safety import parse_xml_bounded

#: (key, element text or None). The key is a tuple of strings.
Fragment = tuple[tuple[str, ...], str | None]

ROOT = "#root"


def family_keys() -> tuple[str, ...]:
    """Every item family of a `GppCollection`, in a fixed order."""
    from .gpp_adapters import ADAPTER_KEYS

    return ("groups", "registry", *ADAPTER_KEYS)


def digest(key: tuple[str, ...]) -> str:
    data = json.dumps(list(key), ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8", "surrogatepass")).hexdigest()


def predicate_ids(ilt: IltFilter | None) -> tuple[str, ...]:
    """The predicate ids, or positional stand-ins for a filter not yet given ids
    (a collection a test or a parser built; the store always assigns them)."""
    if ilt is None:
        return ()
    return ilt.ids if ilt.ids else tuple(f"#{index}" for index in range(len(ilt.items)))


def item_fragments(family: str, item: Any) -> Iterator[Fragment]:
    """Every retained fragment of one item, keyed by its place."""
    owner = (family, str(getattr(item, "id", "")))
    for name in ("unknown_attrs", "unknown_props_attrs"):
        for attr, value in getattr(item, name, ()) or ():
            yield (*owner, name, attr, value), None
    for name in ("unknown_children", "unknown_props_children"):
        seen: Counter[str] = Counter()
        for text in getattr(item, name, ()) or ():
            seen[text] += 1
            yield (*owner, name, text, str(seen[text])), text
    for name in ("native_xml", "task_xml"):
        text = getattr(item, name, "") or ""
        if text:
            yield (*owner, name, text), text
    ilt = getattr(item, "ilt_filter", None)
    if ilt is not None:
        for predicate_id, predicate in zip(predicate_ids(ilt), ilt.items, strict=True):
            if isinstance(predicate, IltPredicate):
                for attr, value in predicate.unknown_attrs:
                    yield (*owner, "ilt", predicate_id, "attr", predicate.type, attr, value), None
            else:
                yield (*owner, "ilt", predicate_id, "raw", predicate), predicate
    for index, member in enumerate(getattr(item, "members", ()) or ()):
        member_id = str(getattr(member, "id", "") or f"#{index}")
        for attr, value in getattr(member, "unknown_attrs", ()) or ():
            yield (*owner, "member", member_id, attr, value), None
    value = getattr(item, "value", None)
    if dataclasses.is_dataclass(value):
        for attr, text in getattr(value, "unknown_attrs", ()) or ():
            yield (*owner, "value", str(getattr(value, "id", "")), attr, text), None
    common = getattr(item, "common", None)
    run_once_id = getattr(common, "run_once_id", "") if common is not None else ""
    if run_once_id:
        # Identity, not content: no egress allowance, but a mutation may not
        # change it (Sol final-r12 N1); only an acquisition sets it.
        yield (*owner, "run_once_id", run_once_id), None


def root_fragments(collection: Any, family: str) -> Iterator[Fragment]:
    for attr, value in getattr(collection, f"{family}_unknown_attrs", ()) or ():
        yield (family, ROOT, "unknown_attrs", attr, value), None
    seen: Counter[str] = Counter()
    for text in getattr(collection, f"{family}_unknown_children", ()) or ():
        seen[text] += 1
        yield (family, ROOT, "unknown_children", text, str(seen[text])), text


def fragments(
    collection: Any, families: Iterable[str] | None = None
) -> dict[str, Fragment]:
    """digest -> fragment, for every retained fragment of *families* (all by default)."""
    found: dict[str, Fragment] = {}
    if collection is None:
        return found
    for family in families if families is not None else family_keys():
        for item in getattr(collection, family, ()) or ():
            for fragment in item_fragments(family, item):
                found[digest(fragment[0])] = fragment
        for fragment in root_fragments(collection, family):
            found[digest(fragment[0])] = fragment
    return found


def records(collection: Any) -> frozenset[str]:
    """The provenance records that still name a fragment of *collection*."""
    if collection is None:
        return frozenset()
    stored = set(getattr(collection, "retained_provenance", ()) or ())
    return frozenset(stored & set(fragments(collection)))


def with_records(collection: Any, digests: Iterable[str]) -> Any:
    """*collection* holding exactly the given records that name its fragments."""
    present = set(fragments(collection))
    return dataclasses.replace(
        collection, retained_provenance=tuple(sorted(set(digests) & present))
    )


def mint(collection: Any) -> Any:
    """Record every retained fragment as imported: the acquisition boundary only."""
    return with_records(collection, fragments(collection))


def keep_records(collection: Any, before: Any, *granted: Iterable[str]) -> Any:
    """*collection* after a mutation: the records *before* held, plus any
    *granted* (a clone's or an item restore's), for the fragments it still has.
    Whatever records the mutated collection itself carries are ignored, so no
    mutation can add one."""
    allowed = set(records(before))
    for extra in granted:
        allowed |= set(extra)
    return with_records(collection, allowed)


def item_records(collection: Any, family: str, item: Any) -> frozenset[str]:
    """The records *collection* holds for one of its items' fragments."""
    held = records(collection)
    return frozenset(
        key for key in (digest(f[0]) for f in item_fragments(family, item)) if key in held
    )


def carried_records(
    family: str, source: Any, copy: Any, source_records: Iterable[str]
) -> frozenset[str]:
    """A clone's records: the source item's recorded fragments, re-keyed onto the
    copy's ids (item, members by position, value). Fragments the copy does not
    keep (its imported ``uid``, its element record) carry nothing."""
    held = set(source_records)
    ids: dict[str, str] = {str(source.id): str(copy.id)}
    for old, new in zip(
        getattr(source, "members", ()) or (), getattr(copy, "members", ()) or (), strict=False
    ):
        ids[str(old.id)] = str(new.id)
    old_value, new_value = getattr(source, "value", None), getattr(copy, "value", None)
    if dataclasses.is_dataclass(old_value) and dataclasses.is_dataclass(new_value):
        ids[str(getattr(old_value, "id", ""))] = str(getattr(new_value, "id", ""))
    copy_keys = {digest(f[0]) for f in item_fragments(family, copy)}
    carried: set[str] = set()
    for key, _text in item_fragments(family, source):
        if digest(key) not in held:
            continue
        mapped = list(key)
        mapped[1] = ids.get(key[1], key[1])
        if len(key) > 3 and key[2] in ("member", "value"):
            mapped[3] = ids.get(key[3], key[3])
        new_key = digest(tuple(mapped))
        if new_key in copy_keys:
            carried.add(new_key)
    return frozenset(carried)


def new_fragments(collection: Any, before: Any, *granted: Iterable[str]) -> list[Fragment]:
    """Fragments *collection* has that neither *before* nor a grant holds: what a
    mutation would ADD. Keyed by place, so a piece copied into another item, a
    changed run-once id or a predicate's attributes moved to another predicate
    are all new."""
    allowed = set(fragments(before))
    for extra in granted:
        allowed |= set(extra)
    return [fragment for key, fragment in fragments(collection).items() if key not in allowed]


# --- element names for egress ------------------------------------------------------


def _element_names(text: str) -> set[str]:
    data = text.encode("utf-8", "surrogatepass")
    size = max(len(data), 1)
    try:
        root = parse_xml_bounded(data, max_size=size, max_attr_length=size, max_text_length=size)
    except ValueError:
        return set()  # not well formed: the writer and xml_problems refuse it
    return {element.tag.rsplit("}", 1)[-1] for element in root.iter()}


def element_names(
    collection: Any, families: Iterable[str], *, verified: bool
) -> frozenset[str]:
    """The element names in the retained fragments of *families* that do
    (``verified=True``) or do not hold an import record."""
    held = records(collection)
    names: set[str] = set()
    for key, (_place, text) in fragments(collection, families).items():
        if text is not None and (key in held) == verified:
            names |= _element_names(text)
    return frozenset(names)


def unverified_items(
    collection: Any, families: Iterable[str], element: str | None = None
) -> list[str]:
    """A label per item (or file root) holding retained content without an
    import record -- with *element*, only those whose unrecorded content holds
    that element (DeepSeek final-r14 N9) -- for an actionable refusal."""
    held = records(collection)
    labels: list[str] = []
    for key, ((family, owner, *_rest), text) in fragments(collection, families).items():
        if key in held:
            continue
        if element is not None and (text is None or element not in _element_names(text)):
            continue
        label = f"{family} {'file root' if owner == ROOT else owner}"
        if label not in labels:
            labels.append(label)
    return labels


# --- ids and the legacy run-once shape ---------------------------------------------


def with_predicate_ids(item: Any, seed: str | None = None) -> Any:
    """*item* with an id for every targeting predicate that has none.

    New ids are random unless *seed* is given: a legacy snapshot loaded without
    them gets ids derived from the item, so every load of it agrees.
    """
    ilt = getattr(item, "ilt_filter", None)
    if ilt is None or (ilt.ids and len(ilt.ids) == len(ilt.items)):
        return item
    ids = tuple(
        str(uuid.uuid5(uuid.NAMESPACE_URL, f"{seed}/ilt/{index}"))
        if seed is not None
        else str(uuid.uuid4())
        for index in range(len(ilt.items))
    )
    return dataclasses.replace(item, ilt_filter=IltFilter(items=ilt.items, ids=ids))


def _is_run_once(raw: str) -> str | None:
    """The ``id`` of a raw ``FilterRunOnce`` predicate, or ``None`` if it is not one."""
    data = raw.encode("utf-8", "surrogatepass")
    size = max(len(data), 1)
    try:
        element = parse_xml_bounded(
            data, max_size=size, max_attr_length=size, max_text_length=size
        )
    except ValueError:
        return None
    if element.tag.rsplit("}", 1)[-1] != "FilterRunOnce":
        return None
    return element.get("id", "")


def normalize_run_once(item: Any) -> Any:
    """A legacy raw ``FilterRunOnce`` predicate as the model holds apply-once.

    Import promotes ``FilterRunOnce`` to ``common.apply_once`` and keeps its id
    as ``common.run_once_id``; before the final-r11 guard the API could also
    store one as a raw predicate. Written back as raw, the item carried two
    run-once filters, its clone shared the original's run-once identity and
    egress refused the export because the file no longer read back as the model
    (Sol final-r12 B2). The first raw one's id is kept when the item has none.
    """
    ilt = getattr(item, "ilt_filter", None)
    common = getattr(item, "common", None)
    if ilt is None or common is None:
        return item
    keep_items: list[IltPredicate | str] = []
    keep_ids: list[str] = []
    run_once_id: str | None = None
    for predicate_id, predicate in zip(predicate_ids(ilt), ilt.items, strict=True):
        found = _is_run_once(predicate) if isinstance(predicate, str) else None
        if found is not None:
            if run_once_id is None:
                run_once_id = found
            continue
        keep_items.append(predicate)
        keep_ids.append(predicate_id)
    if run_once_id is None:
        return item
    new_common = dataclasses.replace(
        common,
        apply_once=True,
        run_once_id=common.run_once_id or run_once_id,
    )
    new_filter = (
        IltFilter(items=tuple(keep_items), ids=tuple(keep_ids) if ilt.ids else ())
        if keep_items
        else None
    )
    return dataclasses.replace(item, common=new_common, ilt_filter=new_filter)


def map_items(collection: Any, transform: Any) -> Any:
    """*collection* with ``transform(family, item)`` applied to every item."""
    changes: dict[str, Any] = {}
    for family in family_keys():
        items = getattr(collection, family, ())
        if items:
            changes[family] = tuple(transform(family, item) for item in items)
    return dataclasses.replace(collection, **changes) if changes else collection


# --- copies: fork and clone --------------------------------------------------------


def _fresh_run_once(common: Any) -> Any:
    """A copy's own run-once identity: a client that applied the original once
    must apply the copy too, so the copy never shares the original's id."""
    if common is None or not (common.apply_once or common.run_once_id):
        return common
    return dataclasses.replace(common, run_once_id="{" + str(uuid.uuid4()).upper() + "}")


def fresh_item(item: Any, *, clone: bool) -> Any:
    """*item* as a new item: fresh item, member and value ids and a fresh
    run-once id; predicate ids stay (they are scoped to the item). A clone
    (``clone=True``, the only caller today) also leaves behind what identifies
    the original to Windows: the imported ``uid`` attribute, a registry item's
    ``uid``, the element record and its slot in the source document."""
    changes: dict[str, Any] = {"id": str(uuid.uuid4())}
    members = getattr(item, "members", None)
    if members and hasattr(members[0], "id"):
        changes["members"] = tuple(
            dataclasses.replace(member, id=str(uuid.uuid4())) for member in members
        )
    value = getattr(item, "value", None)
    if dataclasses.is_dataclass(value) and hasattr(value, "id"):
        changes["value"] = dataclasses.replace(value, id=str(uuid.uuid4()))  # type: ignore[type-var]
    common = getattr(item, "common", None)
    if common is not None:
        changes["common"] = _fresh_run_once(common)
    if clone:
        changes["native_xml"] = ""
        if hasattr(item, "document_position"):
            changes["document_position"] = None
        changes["unknown_attrs"] = tuple(
            pair for pair in getattr(item, "unknown_attrs", ()) if pair[0] != "uid"
        )
        if hasattr(item, "uid"):
            changes["uid"] = ""
    return dataclasses.replace(item, **changes)
