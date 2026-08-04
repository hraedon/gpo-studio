"""Scope of Management (SOM) model and GPO precedence engine.

Models the AD container hierarchy (site, domain, OU) and computes the GPO
application precedence for a target, following the Windows LSDOU rules with
``enforced`` (No Override) and ``block_inheritance`` (Block Policy Inheritance)
semantics.

The precedence list is returned highest-precedence first: target OU links,
then parent OU links up to the domain root, then domain links, then site links.
Within a single scope, lower ``order`` means higher precedence.  Links blocked
by ``block_inheritance`` are kept at the end of the list with ``blocked=True``
so the author can see what was suppressed.  Non-enforced links from path nodes
above the blocking node are blocked, and non-enforced site links are likewise
blocked when any node on the inheritance path has ``block_inheritance=True``.
Enforced links always survive and are never marked blocked.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Literal, assert_never

from .model import ValidationError, ValidationIssue

SomScope = Literal["site", "domain", "ou"]


@dataclass(frozen=True, slots=True)
class SomLink:
    gpo_guid: str
    scope: SomScope
    scope_dn: str
    enabled: bool = True
    enforced: bool = False
    order: int = 1


@dataclass(frozen=True, slots=True)
class SomNode:
    dn: str
    name: str
    scope: SomScope
    parent_dn: str = ""
    block_inheritance: bool = False
    links: tuple[SomLink, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class PrecedenceEntry:
    gpo_guid: str
    scope: SomScope
    scope_dn: str
    order: int
    enforced: bool
    blocked: bool = False


@dataclass(frozen=True, slots=True)
class SomPrecedence:
    target_dn: str
    entries: tuple[PrecedenceEntry, ...]
    warnings: tuple[str, ...] = field(default_factory=tuple)


def _scope_tier(scope: SomScope) -> int:
    # Lower tier = higher precedence (applied later in LSDOU, wins conflicts).
    match scope:
        case "ou":
            return 0
        case "domain":
            return 1
        case "site":
            return 2
        case _:  # pragma: no cover
            assert_never(scope)


def _sorted_links(links: Sequence[SomLink]) -> list[SomLink]:
    return sorted(links, key=lambda link: link.order)


def _sorted_enabled_links(links: Sequence[SomLink]) -> list[SomLink]:
    return sorted((link for link in links if link.enabled), key=lambda link: link.order)


def _find_node(nodes: Sequence[SomNode], dn: str) -> SomNode | None:
    for node in nodes:
        if node.dn == dn:
            return node
    return None


def _precedence_key(
    link: SomLink, path_index: dict[str, int]
) -> tuple[int, int, int]:
    tier = _scope_tier(link.scope)
    # Only OU depth affects ordering within a tier: target (index 0) is closest
    # and wins. Domain and site tiers are sorted by order alone.
    idx = path_index.get(link.scope_dn, 0) if link.scope == "ou" else 0
    return (tier, idx, link.order)


def compute_precedence(
    nodes: Sequence[SomNode], target_dn: str
) -> SomPrecedence:
    """Compute the GPO precedence list for ``target_dn``.

    Builds the inheritance path from the target up to the domain root via
    ``parent_dn``, collects enabled links from each node on the path plus any
    site nodes, and orders them highest-precedence first.  Non-enforced links
    from nodes above the closest ``block_inheritance`` node are flagged
    ``blocked=True`` and moved to the end of the list.  Non-enforced site
    links are likewise blocked when any node on the inheritance path has
    ``block_inheritance=True``.

    A ``parent_dn`` that does not resolve to a provided node truncates the
    path and emits a warning.  A link whose ``scope_dn`` does not match the
    DN of the node it is attached to is skipped with a warning.
    """
    by_dn: dict[str, SomNode] = {n.dn: n for n in nodes}
    target = by_dn.get(target_dn)
    if target is None:
        # WI-026. An unresolvable target DN yields an empty precedence list,
        # which is indistinguishable from a correctly-computed "no GPOs apply
        # here" unless it says so. The common way to reach this is passing a
        # computer's own DN (CN=host,OU=...) where a container DN is required;
        # every caller in the test suite happens to pass a container, so the
        # silence went unnoticed until the WP-6B lane fed the model a DN from
        # a real directory. Whether an object DN *should* resolve to its
        # parent container is the open half of WI-026 and is not decided here.
        return SomPrecedence(
            target_dn=target_dn,
            entries=(),
            warnings=(
                f"Target DN {target_dn!r} does not match any provided SOM node, so no "
                "links were collected. If this is an object DN, pass the DN of the "
                "container that holds it.",
            ),
        )

    warnings: list[str] = []
    path: list[SomNode] = []
    seen: set[str] = set()
    current: SomNode | None = target
    while current is not None and current.dn not in seen:
        path.append(current)
        seen.add(current.dn)
        parent = current.parent_dn
        if parent and parent not in by_dn:
            warnings.append(
                f"Parent DN {parent!r} of node {current.dn!r} not found in "
                "provided nodes; inheritance path truncated."
            )
            break
        current = by_dn.get(parent) if parent else None

    path_index = {n.dn: i for i, n in enumerate(path)}

    # The closest-to-target block_inheritance node governs: every non-enforced
    # link from a node strictly above it (higher path index) is blocked.
    cutoff: int | None = None
    for idx, node in enumerate(path):
        if node.block_inheritance:
            cutoff = idx
            break

    if cutoff is not None:
        blocker = path[cutoff]
        if cutoff > 0:
            warnings.append(
                f"block_inheritance is enabled on {blocker.scope} {blocker.dn}"
            )
        else:
            warnings.append(
                f"block_inheritance on target {blocker.dn} blocks "
                "inherited links from parent scopes."
            )

    decorated: list[tuple[tuple[int, int, int], PrecedenceEntry]] = []

    for idx, node in enumerate(path):
        for link in _sorted_enabled_links(node.links):
            if link.scope_dn != node.dn:
                warnings.append(
                    f"Link for GPO {link.gpo_guid} has scope_dn "
                    f"{link.scope_dn!r} that does not match node DN "
                    f"{node.dn!r}; skipping."
                )
                continue
            blocked = (
                cutoff is not None and idx > cutoff and not link.enforced
            )
            entry = PrecedenceEntry(
                gpo_guid=link.gpo_guid,
                scope=link.scope,
                scope_dn=link.scope_dn,
                order=link.order,
                enforced=link.enforced,
                blocked=blocked,
            )
            if link.enforced:
                warnings.append(
                    f"GPO {link.gpo_guid} is enforced at {node.scope} {node.dn}"
                )
            if node.scope == "domain":
                warnings.append(
                    f"GPO {link.gpo_guid} is linked at the domain root {node.dn}"
                )
            decorated.append((_precedence_key(link, path_index), entry))

    # Site links apply in addition to the OU/domain path and always sit at the
    # lowest precedence tier. In Windows GPO, block_inheritance on a domain or
    # OU along the inheritance path blocks non-enforced site links as well;
    # enforced site links always survive.
    for node in nodes:
        if node.scope != "site" or node.dn in seen:
            continue
        for link in _sorted_enabled_links(node.links):
            if link.scope_dn != node.dn:
                warnings.append(
                    f"Link for GPO {link.gpo_guid} has scope_dn "
                    f"{link.scope_dn!r} that does not match node DN "
                    f"{node.dn!r}; skipping."
                )
                continue
            blocked = cutoff is not None and not link.enforced
            entry = PrecedenceEntry(
                gpo_guid=link.gpo_guid,
                scope=link.scope,
                scope_dn=link.scope_dn,
                order=link.order,
                enforced=link.enforced,
                blocked=blocked,
            )
            if link.enforced:
                warnings.append(
                    f"GPO {link.gpo_guid} is enforced at site {node.dn}"
                )
            decorated.append((_precedence_key(link, path_index), entry))

    # Active (non-blocked) entries first in precedence order, then blocked
    # entries in their logical precedence order.
    decorated.sort(key=lambda item: (item[1].blocked, item[0]))
    entries = tuple(entry for _, entry in decorated)

    return SomPrecedence(
        target_dn=target_dn,
        entries=entries,
        warnings=tuple(warnings),
    )


def plan_link_order(
    nodes: Sequence[SomNode],
    scope_dn: str,
    gpo_guid: str,
    new_order: int,
) -> tuple[tuple[SomLink, ...], tuple[SomLink, ...]]:
    """Plan a link reorder on ``scope_dn`` for ``gpo_guid``.

    Returns ``(before, after)`` where ``before`` is the current link ordering
    (sorted by ``order``) and ``after`` is the same set of links renumbered so
    the target link occupies ``new_order`` (1-indexed).  ``new_order`` must be
    within ``1..len(links)+1``.
    """
    node = _find_node(nodes, scope_dn)
    if node is None:
        raise ValidationError([
            ValidationIssue(
                severity="error",
                code="scope_not_found",
                message=f"No SOM node found for scope {scope_dn!r}.",
                path="scope_dn",
            )
        ])

    before_list = _sorted_links(node.links)
    count = len(before_list)
    if new_order < 1 or new_order > count + 1:
        raise ValidationError([
            ValidationIssue(
                severity="error",
                code="link_order_out_of_bounds",
                message=f"new_order {new_order} is out of bounds (1..{count + 1}).",
                path="new_order",
            )
        ])

    target_idx = next(
        (i for i, link in enumerate(before_list) if link.gpo_guid == gpo_guid),
        None,
    )
    if target_idx is None:
        raise ValidationError([
            ValidationIssue(
                severity="error",
                code="gpo_not_linked",
                message=f"GPO {gpo_guid!r} is not linked at scope {scope_dn!r}.",
                path="gpo_guid",
            )
        ])

    target_link = before_list[target_idx]
    remaining = [link for link in before_list if link.gpo_guid != gpo_guid]
    insert_idx = new_order - 1
    final_list = (
        list(remaining[:insert_idx])
        + [target_link]
        + list(remaining[insert_idx:])
    )
    before = tuple(before_list)
    after = tuple(replace(link, order=i + 1) for i, link in enumerate(final_list))
    return before, after


def set_block_inheritance(
    nodes: Sequence[SomNode], dn: str, block: bool
) -> tuple[SomNode, ...]:
    """Return a new tuple of nodes with ``block_inheritance`` toggled on ``dn``."""
    found = False
    result: list[SomNode] = []
    for node in nodes:
        if node.dn == dn:
            result.append(replace(node, block_inheritance=block))
            found = True
        else:
            result.append(node)
    if not found:
        raise ValidationError([
            ValidationIssue(
                severity="error",
                code="node_not_found",
                message=f"No SOM node found for DN {dn!r}.",
                path="dn",
            )
        ])
    return tuple(result)


__all__ = [
    "PrecedenceEntry",
    "SomLink",
    "SomNode",
    "SomPrecedence",
    "SomScope",
    "compute_precedence",
    "plan_link_order",
    "set_block_inheritance",
]
