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
) -> tuple[int, int, int, int]:
    """Sort key for a link, ascending = higher precedence (applied last, wins).

    WI-031, found by the Plan 033 WP-6B oracle on 2026-08-04 and fixed here.

    Enforcement was previously absent from this key entirely, so an enforced
    link was ordered by its scope like any other. A GPO enforced at the domain
    therefore sat in the domain tier and *lost* to a plain OU link -- Studio
    predicted the OU value where Windows resolved the enforced one. Three
    consecutive runs on a real 26200 client observed ``domainEnforced`` where
    Studio predicted ``child``.

    Windows has two rules here and this key encodes both:

    1. **An enforced link outranks every non-enforced link**, whatever their
       scopes. That is the leading element.
    2. **Among enforced links the hierarchy inverts**: the enforced link
       closest to the root wins, so site beats domain beats OU, and a shallower
       OU beats a deeper one. Negating tier and depth expresses exactly that.

    Link order within a container is unaffected: order 1 still applies last and
    wins, enforced or not.

    Note that enforcement's *other* effect -- surviving a block-inheritance
    cutoff -- was already correct and is handled separately, which is why the
    applied/denied sets matched Windows while the winning value did not. The
    two halves of "enforced" are independent, and only one of them was
    implemented.
    """
    tier = _scope_tier(link.scope)
    # Only OU depth affects ordering within a tier: target (index 0) is closest
    # and wins. Domain and site tiers are sorted by order alone.
    idx = path_index.get(link.scope_dn, 0) if link.scope == "ou" else 0
    if link.enforced:
        return (0, -tier, -idx, link.order)
    return (1, tier, idx, link.order)


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
    # DNs are matched case-insensitively because Active Directory treats them
    # that way, and the two DNs being compared here routinely come from
    # different places: a SOM tree assembled by a caller, and an object DN read
    # back from the directory, which returns whatever case is stored
    # (``CN=LABCL01,OU=Studio...``).
    by_dn_fold: dict[str, SomNode] = {n.dn.casefold(): n for n in nodes}
    warnings: list[str] = []

    # WI-026, resolved 2026-08-04. A target DN that names no SOM node resolves
    # to its nearest ancestor that does.
    #
    # This function looks up SOM *containers*, but ``RsopTarget.computer_dn``
    # is an object DN in every real caller -- ``ad_discovery`` returns
    # ``CN=host,OU=...``, and the same field is matched against security-filter
    # principals, where the object DN is the correct value. Before this, an
    # object DN produced an empty precedence list, so ``compute_rsop`` reported
    # that no GPOs applied to a machine Windows applies six GPOs to, with no
    # warning to say why.
    #
    # Walking up is what Windows does: a computer's GPOs come from its parent
    # container chain. It is also backwards compatible -- a container DN is
    # found on the first lookup and never enters the walk -- which is why the
    # WP-6B certification computed under the old behaviour still stands.
    #
    # Verified against the oracle: the lane now passes the client's real object
    # DN and produces the same prediction Windows confirmed.
    target = by_dn_fold.get(target_dn.casefold())
    if target is None:
        remainder = target_dn
        while "," in remainder:
            remainder = remainder.split(",", 1)[1].lstrip()
            ancestor = by_dn_fold.get(remainder.casefold())
            if ancestor is not None:
                target = ancestor
                break

    if target is None:
        # Nothing in the DN's ancestry is in the tree. The empty result stands,
        # but it says so: "no node matched" and "no GPOs apply here" are
        # different answers and a caller cannot tell them apart from an empty
        # entry list alone.
        return SomPrecedence(
            target_dn=target_dn,
            entries=(),
            warnings=(
                f"Target DN {target_dn!r} does not match any provided SOM node, nor does "
                "any of its ancestors, so no links were collected.",
            ),
        )

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

    decorated: list[tuple[tuple[int, int, int, int], PrecedenceEntry]] = []

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
