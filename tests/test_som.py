from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gpo_studio.api import app
from gpo_studio.model import ValidationError
from gpo_studio.som import (
    PrecedenceEntry,
    SomLink,
    SomNode,
    compute_precedence,
    plan_link_order,
    set_block_inheritance,
)
from gpo_studio.store import WorkspaceStore

_DOMAIN_DN = "DC=ad,DC=hraedon,DC=com"
_OU_DN = "OU=Servers," + _DOMAIN_DN
_CHILD_OU_DN = "OU=Child," + _OU_DN
_GRANDPARENT_OU_DN = "OU=Grandparent," + _DOMAIN_DN
_SITE_DN = "CN=Default-First-Site-Name,CN=Sites,CN=Configuration," + _DOMAIN_DN

_GPO_A = "11111111-2222-3333-4444-555555555555"
_GPO_B = "22222222-3333-4444-5555-666666666666"
_GPO_C = "33333333-4444-5555-6666-777777777777"
_GPO_D = "44444444-5555-6666-7777-888888888888"


def _link(
    gpo_guid: str,
    scope_dn: str,
    *,
    scope: str = "ou",
    order: int = 1,
    enforced: bool = False,
    enabled: bool = True,
) -> SomLink:
    return SomLink(
        gpo_guid=gpo_guid,
        scope=scope,  # type: ignore[arg-type]
        scope_dn=scope_dn,
        enabled=enabled,
        enforced=enforced,
        order=order,
    )


def _node(
    dn: str,
    name: str,
    scope: str,
    parent_dn: str = "",
    block_inheritance: bool = False,
    links: tuple[SomLink, ...] = (),
) -> SomNode:
    return SomNode(
        dn=dn,
        name=name,
        scope=scope,  # type: ignore[arg-type]
        parent_dn=parent_dn,
        block_inheritance=block_inheritance,
        links=links,
    )


def _guids(entries: tuple[PrecedenceEntry, ...]) -> list[str]:
    return [e.gpo_guid for e in entries if not e.blocked]


# ---------------------------------------------------------------------------
# Precedence: ordering
# ---------------------------------------------------------------------------


def test_precedence_simple_ou_chain() -> None:
    """Domain -> OU -> child OU. Child OU wins, site last."""
    nodes = (
        _node(
            _CHILD_OU_DN,
            "Child",
            "ou",
            parent_dn=_OU_DN,
            links=(_link(_GPO_C, _CHILD_OU_DN),),
        ),
        _node(
            _OU_DN,
            "Servers",
            "ou",
            parent_dn=_DOMAIN_DN,
            links=(_link(_GPO_B, _OU_DN),),
        ),
        _node(
            _DOMAIN_DN,
            "ad",
            "domain",
            links=(_link(_GPO_A, _DOMAIN_DN, scope="domain"),),
        ),
    )
    result = compute_precedence(nodes, _CHILD_OU_DN)
    assert result.target_dn == _CHILD_OU_DN
    assert _guids(result.entries) == [_GPO_C, _GPO_B, _GPO_A]
    assert all(not e.blocked for e in result.entries)


def test_precedence_site_links_lowest() -> None:
    """Site links rank below domain and OU links."""
    nodes = (
        _node(
            _CHILD_OU_DN,
            "Child",
            "ou",
            parent_dn=_OU_DN,
            links=(_link(_GPO_C, _CHILD_OU_DN),),
        ),
        _node(
            _OU_DN,
            "Servers",
            "ou",
            parent_dn=_DOMAIN_DN,
        ),
        _node(
            _DOMAIN_DN,
            "ad",
            "domain",
            links=(_link(_GPO_A, _DOMAIN_DN, scope="domain"),),
        ),
        _node(
            _SITE_DN,
            "Default-First-Site",
            "site",
            links=(_link(_GPO_D, _SITE_DN, scope="site"),),
        ),
    )
    result = compute_precedence(nodes, _CHILD_OU_DN)
    assert _guids(result.entries) == [_GPO_C, _GPO_A, _GPO_D]
    assert result.entries[-1].scope == "site"


def test_precedence_multiple_links_same_scope_order() -> None:
    """Within a scope, lower order number = higher precedence."""
    nodes = (
        _node(
            _OU_DN,
            "Servers",
            "ou",
            parent_dn=_DOMAIN_DN,
            links=(
                _link(_GPO_B, _OU_DN, order=2),
                _link(_GPO_A, _OU_DN, order=1),
            ),
        ),
        _node(_DOMAIN_DN, "ad", "domain"),
    )
    result = compute_precedence(nodes, _OU_DN)
    assert _guids(result.entries) == [_GPO_A, _GPO_B]


def test_precedence_parent_ou_before_domain() -> None:
    """Parent OU links outrank domain links, target OU outranks parent."""
    nodes = (
        _node(
            _CHILD_OU_DN,
            "Child",
            "ou",
            parent_dn=_OU_DN,
            links=(_link(_GPO_C, _CHILD_OU_DN),),
        ),
        _node(
            _OU_DN,
            "Servers",
            "ou",
            parent_dn=_DOMAIN_DN,
            links=(_link(_GPO_B, _OU_DN),),
        ),
        _node(
            _DOMAIN_DN,
            "ad",
            "domain",
            links=(_link(_GPO_A, _DOMAIN_DN, scope="domain"),),
        ),
    )
    result = compute_precedence(nodes, _CHILD_OU_DN)
    assert _guids(result.entries) == [_GPO_C, _GPO_B, _GPO_A]


# ---------------------------------------------------------------------------
# Precedence: block_inheritance and enforced
# ---------------------------------------------------------------------------


def test_block_inheritance_blocks_non_enforced_links_from_above() -> None:
    """OU1 blocks: non-enforced domain link is blocked (flagged, at end)."""
    nodes = (
        _node(
            _CHILD_OU_DN,
            "Child",
            "ou",
            parent_dn=_OU_DN,
            links=(_link(_GPO_C, _CHILD_OU_DN),),
        ),
        _node(
            _OU_DN,
            "Servers",
            "ou",
            parent_dn=_DOMAIN_DN,
            block_inheritance=True,
            links=(_link(_GPO_B, _OU_DN),),
        ),
        _node(
            _DOMAIN_DN,
            "ad",
            "domain",
            links=(_link(_GPO_A, _DOMAIN_DN, scope="domain"),),
        ),
    )
    result = compute_precedence(nodes, _CHILD_OU_DN)
    active = [e for e in result.entries if not e.blocked]
    blocked = [e for e in result.entries if e.blocked]
    assert _guids(tuple(active)) == [_GPO_C, _GPO_B]
    assert len(blocked) == 1
    assert blocked[0].gpo_guid == _GPO_A
    assert any("block_inheritance" in w for w in result.warnings)


def test_enforced_links_bypass_block_inheritance() -> None:
    """Enforced domain link survives block_inheritance on OU1."""
    nodes = (
        _node(
            _CHILD_OU_DN,
            "Child",
            "ou",
            parent_dn=_OU_DN,
            links=(_link(_GPO_C, _CHILD_OU_DN),),
        ),
        _node(
            _OU_DN,
            "Servers",
            "ou",
            parent_dn=_DOMAIN_DN,
            block_inheritance=True,
            links=(_link(_GPO_B, _OU_DN),),
        ),
        _node(
            _DOMAIN_DN,
            "ad",
            "domain",
            links=(
                _link(
                    _GPO_A,
                    _DOMAIN_DN,
                    scope="domain",
                    enforced=True,
                ),
            ),
        ),
    )
    result = compute_precedence(nodes, _CHILD_OU_DN)
    assert all(not e.blocked for e in result.entries)
    enforced_entry = next(e for e in result.entries if e.gpo_guid == _GPO_A)
    assert enforced_entry.enforced is True
    assert any("enforced" in w for w in result.warnings)


def test_block_inheritance_on_target_blocks_ancestors() -> None:
    """block_inheritance on the target itself blocks all ancestor links."""
    nodes = (
        _node(
            _OU_DN,
            "Servers",
            "ou",
            parent_dn=_DOMAIN_DN,
            block_inheritance=True,
            links=(_link(_GPO_B, _OU_DN),),
        ),
        _node(
            _DOMAIN_DN,
            "ad",
            "domain",
            links=(_link(_GPO_A, _DOMAIN_DN, scope="domain"),),
        ),
    )
    result = compute_precedence(nodes, _OU_DN)
    active = [e for e in result.entries if not e.blocked]
    blocked = [e for e in result.entries if e.blocked]
    assert _guids(tuple(active)) == [_GPO_B]
    assert [e.gpo_guid for e in blocked] == [_GPO_A]


def test_enforced_link_on_ou_not_blocked_by_own_block() -> None:
    """An enforced link on a node below the blocker is never blocked."""
    nodes = (
        _node(
            _CHILD_OU_DN,
            "Child",
            "ou",
            parent_dn=_OU_DN,
            links=(_link(_GPO_C, _CHILD_OU_DN, enforced=True),),
        ),
        _node(
            _OU_DN,
            "Servers",
            "ou",
            parent_dn=_DOMAIN_DN,
            block_inheritance=True,
        ),
        _node(_DOMAIN_DN, "ad", "domain"),
    )
    result = compute_precedence(nodes, _CHILD_OU_DN)
    assert all(not e.blocked for e in result.entries)


# ---------------------------------------------------------------------------
# Precedence: edge cases
# ---------------------------------------------------------------------------


def test_precedence_empty_nodes() -> None:
    result = compute_precedence((), _CHILD_OU_DN)
    assert result.entries == ()
    assert result.target_dn == _CHILD_OU_DN


def test_precedence_target_not_found() -> None:
    nodes = (_node(_DOMAIN_DN, "ad", "domain"),)
    result = compute_precedence(nodes, _OU_DN)
    assert result.entries == ()


def test_precedence_single_node() -> None:
    nodes = (
        _node(
            _OU_DN,
            "Servers",
            "ou",
            links=(_link(_GPO_A, _OU_DN, order=1), _link(_GPO_B, _OU_DN, order=2)),
        ),
    )
    result = compute_precedence(nodes, _OU_DN)
    assert _guids(result.entries) == [_GPO_A, _GPO_B]


def test_precedence_disabled_links_excluded() -> None:
    nodes = (
        _node(
            _OU_DN,
            "Servers",
            "ou",
            links=(
                _link(_GPO_A, _OU_DN, order=1, enabled=False),
                _link(_GPO_B, _OU_DN, order=2, enabled=True),
            ),
        ),
    )
    result = compute_precedence(nodes, _OU_DN)
    assert _guids(result.entries) == [_GPO_B]


def test_precedence_cycle_guarded() -> None:
    """A parent_dn cycle must terminate, not hang."""
    nodes = (
        _node(_OU_DN, "Servers", "ou", parent_dn=_CHILD_OU_DN),
        _node(_CHILD_OU_DN, "Child", "ou", parent_dn=_OU_DN),
    )
    result = compute_precedence(nodes, _CHILD_OU_DN)
    assert result.entries == ()


def test_precedence_warnings_domain_root_and_enforced() -> None:
    nodes = (
        _node(
            _OU_DN,
            "Servers",
            "ou",
            parent_dn=_DOMAIN_DN,
            links=(_link(_GPO_B, _OU_DN),),
        ),
        _node(
            _DOMAIN_DN,
            "ad",
            "domain",
            links=(
                _link(_GPO_A, _DOMAIN_DN, scope="domain", enforced=True),
            ),
        ),
    )
    result = compute_precedence(nodes, _OU_DN)
    assert any("domain root" in w for w in result.warnings)
    assert any("enforced" in w for w in result.warnings)


def test_site_links_blocked_by_domain_block_inheritance() -> None:
    """block_inheritance on the domain blocks non-enforced site links."""
    nodes = (
        _node(_CHILD_OU_DN, "Child", "ou", parent_dn=_OU_DN),
        _node(_OU_DN, "Servers", "ou", parent_dn=_DOMAIN_DN),
        _node(_DOMAIN_DN, "ad", "domain", block_inheritance=True),
        _node(
            _SITE_DN,
            "Default-First-Site",
            "site",
            links=(_link(_GPO_D, _SITE_DN, scope="site"),),
        ),
    )
    result = compute_precedence(nodes, _CHILD_OU_DN)
    site_entry = next(e for e in result.entries if e.scope == "site")
    assert site_entry.gpo_guid == _GPO_D
    assert site_entry.blocked is True


def test_enforced_site_link_not_blocked() -> None:
    """Enforced site link survives block_inheritance on the domain."""
    nodes = (
        _node(_CHILD_OU_DN, "Child", "ou", parent_dn=_OU_DN),
        _node(_OU_DN, "Servers", "ou", parent_dn=_DOMAIN_DN),
        _node(_DOMAIN_DN, "ad", "domain", block_inheritance=True),
        _node(
            _SITE_DN,
            "Default-First-Site",
            "site",
            links=(_link(_GPO_D, _SITE_DN, scope="site", enforced=True),),
        ),
    )
    result = compute_precedence(nodes, _CHILD_OU_DN)
    site_entry = next(e for e in result.entries if e.scope == "site")
    assert site_entry.gpo_guid == _GPO_D
    assert site_entry.blocked is False
    assert any("enforced" in w for w in result.warnings)


def test_missing_parent_emits_warning() -> None:
    """A parent_dn that resolves to no provided node truncates the path."""
    missing_dn = "OU=Missing," + _DOMAIN_DN
    nodes = (
        _node(
            _OU_DN,
            "Servers",
            "ou",
            parent_dn=missing_dn,
            links=(_link(_GPO_B, _OU_DN),),
        ),
        _node(
            _DOMAIN_DN,
            "ad",
            "domain",
            links=(_link(_GPO_A, _DOMAIN_DN, scope="domain"),),
        ),
    )
    result = compute_precedence(nodes, _OU_DN)
    assert any("not found in provided nodes" in w for w in result.warnings)
    assert any("inheritance path truncated" in w for w in result.warnings)
    # The domain was not reached, so its link is absent.
    assert _GPO_A not in _guids(result.entries)
    assert _GPO_B in _guids(result.entries)


def test_block_inheritance_on_target_no_misleading_warning() -> None:
    """block_inheritance on the target emits a clarified warning, not the
    'is enabled on' ancestor form."""
    nodes = (
        _node(
            _OU_DN,
            "Servers",
            "ou",
            parent_dn=_DOMAIN_DN,
            block_inheritance=True,
            links=(_link(_GPO_B, _OU_DN),),
        ),
        _node(
            _DOMAIN_DN,
            "ad",
            "domain",
            links=(_link(_GPO_A, _DOMAIN_DN, scope="domain"),),
        ),
    )
    result = compute_precedence(nodes, _OU_DN)
    # The misleading ancestor-form warning must not fire for the target itself.
    assert not any(
        "block_inheritance is enabled on" in w for w in result.warnings
    )
    # The clarified target-form warning is emitted instead.
    assert any("block_inheritance on target" in w for w in result.warnings)
    # Target's own link survives; ancestor link is blocked.
    assert _GPO_B in _guids(result.entries)
    blocked = [e for e in result.entries if e.blocked]
    assert [e.gpo_guid for e in blocked] == [_GPO_A]


def test_link_scope_dn_mismatch_skipped_with_warning() -> None:
    """A link whose scope_dn differs from its node DN is skipped."""
    mismatch_dn = "OU=Other," + _DOMAIN_DN
    nodes = (
        _node(
            _OU_DN,
            "Servers",
            "ou",
            links=(_link(_GPO_A, mismatch_dn),),
        ),
    )
    result = compute_precedence(nodes, _OU_DN)
    assert result.entries == ()
    assert any("does not match node DN" in w for w in result.warnings)
    assert any("skipping" in w for w in result.warnings)


def test_cycle_with_links_processes_both_nodes() -> None:
    """A parent_dn cycle terminates and both nodes' links are collected."""
    nodes = (
        _node(
            _OU_DN,
            "Servers",
            "ou",
            parent_dn=_CHILD_OU_DN,
            links=(_link(_GPO_A, _OU_DN),),
        ),
        _node(
            _CHILD_OU_DN,
            "Child",
            "ou",
            parent_dn=_OU_DN,
            links=(_link(_GPO_B, _CHILD_OU_DN),),
        ),
    )
    result = compute_precedence(nodes, _CHILD_OU_DN)
    assert _GPO_A in _guids(result.entries)
    assert _GPO_B in _guids(result.entries)
    assert all(not e.blocked for e in result.entries)


def test_multiple_block_inheritance_closest_wins() -> None:
    """When multiple ancestors block, the closest one to the target governs."""
    nodes = (
        _node(_CHILD_OU_DN, "Child", "ou", parent_dn=_OU_DN),
        _node(
            _OU_DN,
            "Servers",
            "ou",
            parent_dn=_GRANDPARENT_OU_DN,
            block_inheritance=True,
            links=(_link(_GPO_B, _OU_DN),),
        ),
        _node(
            _GRANDPARENT_OU_DN,
            "Grandparent",
            "ou",
            parent_dn=_DOMAIN_DN,
            block_inheritance=True,
            links=(_link(_GPO_C, _GRANDPARENT_OU_DN),),
        ),
        _node(
            _DOMAIN_DN,
            "ad",
            "domain",
            links=(_link(_GPO_A, _DOMAIN_DN, scope="domain"),),
        ),
    )
    result = compute_precedence(nodes, _CHILD_OU_DN)
    active = [e for e in result.entries if not e.blocked]
    blocked = [e for e in result.entries if e.blocked]
    # Parent (closest blocker) keeps its own link; grandparent and domain blocked.
    assert _guids(tuple(active)) == [_GPO_B]
    assert [e.gpo_guid for e in blocked] == [_GPO_C, _GPO_A]
    # Warning names the closest blocker (parent), not the grandparent.
    assert any(
        "block_inheritance is enabled on" in w and _OU_DN in w
        for w in result.warnings
    )
    assert not any(_GRANDPARENT_OU_DN in w for w in result.warnings)


# ---------------------------------------------------------------------------
# plan_link_order
# ---------------------------------------------------------------------------


def test_plan_link_order_move_to_first() -> None:
    nodes = (
        _node(
            _OU_DN,
            "Servers",
            "ou",
            links=(
                _link(_GPO_A, _OU_DN, order=1),
                _link(_GPO_B, _OU_DN, order=2),
                _link(_GPO_C, _OU_DN, order=3),
            ),
        ),
    )
    before, after = plan_link_order(nodes, _OU_DN, _GPO_B, 1)
    assert [link.gpo_guid for link in before] == [_GPO_A, _GPO_B, _GPO_C]
    assert [link.gpo_guid for link in after] == [_GPO_B, _GPO_A, _GPO_C]
    assert [link.order for link in after] == [1, 2, 3]


def test_plan_link_order_move_to_last() -> None:
    nodes = (
        _node(
            _OU_DN,
            "Servers",
            "ou",
            links=(
                _link(_GPO_A, _OU_DN, order=1),
                _link(_GPO_B, _OU_DN, order=2),
                _link(_GPO_C, _OU_DN, order=3),
            ),
        ),
    )
    before, after = plan_link_order(nodes, _OU_DN, _GPO_A, 3)
    assert [link.gpo_guid for link in after] == [_GPO_B, _GPO_C, _GPO_A]
    assert [link.order for link in after] == [1, 2, 3]


def test_plan_link_order_append_beyond_end() -> None:
    """new_order = len+1 appends the link at the end."""
    nodes = (
        _node(
            _OU_DN,
            "Servers",
            "ou",
            links=(
                _link(_GPO_A, _OU_DN, order=1),
                _link(_GPO_B, _OU_DN, order=2),
            ),
        ),
    )
    before, after = plan_link_order(nodes, _OU_DN, _GPO_A, 3)
    assert [link.gpo_guid for link in after] == [_GPO_B, _GPO_A]
    assert [link.order for link in after] == [1, 2]


def test_plan_link_order_out_of_bounds() -> None:
    nodes = (
        _node(
            _OU_DN,
            "Servers",
            "ou",
            links=(_link(_GPO_A, _OU_DN, order=1),),
        ),
    )
    with pytest.raises(ValidationError) as exc_info:
        plan_link_order(nodes, _OU_DN, _GPO_A, 0)
    assert exc_info.value.issues[0].code == "link_order_out_of_bounds"

    with pytest.raises(ValidationError) as exc_info2:
        plan_link_order(nodes, _OU_DN, _GPO_A, 3)
    assert exc_info2.value.issues[0].code == "link_order_out_of_bounds"


def test_plan_link_order_gpo_not_linked() -> None:
    nodes = (
        _node(
            _OU_DN,
            "Servers",
            "ou",
            links=(_link(_GPO_A, _OU_DN, order=1),),
        ),
    )
    with pytest.raises(ValidationError) as exc_info:
        plan_link_order(nodes, _OU_DN, _GPO_B, 1)
    assert exc_info.value.issues[0].code == "gpo_not_linked"


def test_plan_link_order_scope_not_found() -> None:
    nodes = (_node(_OU_DN, "Servers", "ou"),)
    with pytest.raises(ValidationError) as exc_info:
        plan_link_order(nodes, _DOMAIN_DN, _GPO_A, 1)
    assert exc_info.value.issues[0].code == "scope_not_found"


def test_plan_link_order_preserves_link_fields() -> None:
    """Enforced/enabled flags survive the renumbering."""
    nodes = (
        _node(
            _OU_DN,
            "Servers",
            "ou",
            links=(
                _link(_GPO_A, _OU_DN, order=1, enforced=True, enabled=False),
                _link(_GPO_B, _OU_DN, order=2),
            ),
        ),
    )
    before, after = plan_link_order(nodes, _OU_DN, _GPO_B, 1)
    a_link = next(link for link in after if link.gpo_guid == _GPO_A)
    assert a_link.enforced is True
    assert a_link.enabled is False
    assert a_link.order == 2
    assert [link.gpo_guid for link in after] == [_GPO_B, _GPO_A]


# ---------------------------------------------------------------------------
# set_block_inheritance
# ---------------------------------------------------------------------------


def test_set_block_inheritance_enables() -> None:
    nodes = (
        _node(_OU_DN, "Servers", "ou"),
        _node(_CHILD_OU_DN, "Child", "ou", parent_dn=_OU_DN),
    )
    result = set_block_inheritance(nodes, _OU_DN, True)
    updated = next(n for n in result if n.dn == _OU_DN)
    assert updated.block_inheritance is True
    other = next(n for n in result if n.dn == _CHILD_OU_DN)
    assert other.block_inheritance is False
    assert len(result) == len(nodes)


def test_set_block_inheritance_disables() -> None:
    nodes = (_node(_OU_DN, "Servers", "ou", block_inheritance=True),)
    result = set_block_inheritance(nodes, _OU_DN, False)
    assert result[0].block_inheritance is False


def test_set_block_inheritance_returns_new_tuple() -> None:
    """Original nodes are not mutated (frozen dataclasses)."""
    nodes = (_node(_OU_DN, "Servers", "ou"),)
    result = set_block_inheritance(nodes, _OU_DN, True)
    assert nodes[0].block_inheritance is False
    assert result[0].block_inheritance is True
    assert result is not nodes


def test_set_block_inheritance_node_not_found() -> None:
    nodes = (_node(_OU_DN, "Servers", "ou"),)
    with pytest.raises(ValidationError) as exc_info:
        set_block_inheritance(nodes, _DOMAIN_DN, True)
    assert exc_info.value.issues[0].code == "node_not_found"


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------


def _client(tmp_path: Path) -> TestClient:
    app.state.store = WorkspaceStore(tmp_path / "som.db")
    app.state.owns_store = False
    return TestClient(app)


def _node_payload(
    dn: str,
    name: str,
    scope: str,
    parent_dn: str = "",
    block_inheritance: bool = False,
    links: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "dn": dn,
        "name": name,
        "scope": scope,
        "parent_dn": parent_dn,
        "block_inheritance": block_inheritance,
        "links": links or [],
    }


def _link_payload(
    gpo_guid: str, scope_dn: str, scope: str = "ou", order: int = 1
) -> dict[str, object]:
    return {
        "gpo_guid": gpo_guid,
        "scope": scope,
        "scope_dn": scope_dn,
        "enabled": True,
        "enforced": False,
        "order": order,
    }


def test_api_som_precedence(tmp_path: Path) -> None:
    payload = {
        "target_dn": _CHILD_OU_DN,
        "nodes": [
            _node_payload(
                _CHILD_OU_DN,
                "Child",
                "ou",
                parent_dn=_OU_DN,
                links=[_link_payload(_GPO_C, _CHILD_OU_DN)],
            ),
            _node_payload(
                _OU_DN,
                "Servers",
                "ou",
                parent_dn=_DOMAIN_DN,
                links=[_link_payload(_GPO_B, _OU_DN, order=1)],
            ),
            _node_payload(
                _DOMAIN_DN,
                "ad",
                "domain",
                links=[_link_payload(_GPO_A, _DOMAIN_DN, scope="domain")],
            ),
        ],
    }
    with _client(tmp_path) as client:
        resp = client.post("/api/som/precedence", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["target_dn"] == _CHILD_OU_DN
    guids = [e["gpo_guid"] for e in body["entries"] if not e["blocked"]]
    assert guids == [_GPO_C, _GPO_B, _GPO_A]


def test_api_som_precedence_target_not_found(tmp_path: Path) -> None:
    payload = {
        "target_dn": _CHILD_OU_DN,
        "nodes": [_node_payload(_DOMAIN_DN, "ad", "domain")],
    }
    with _client(tmp_path) as client:
        resp = client.post("/api/som/precedence", json=payload)
    assert resp.status_code == 200
    assert resp.json()["entries"] == []


def test_api_som_plan_link_order(tmp_path: Path) -> None:
    payload = {
        "scope_dn": _OU_DN,
        "gpo_guid": _GPO_B,
        "new_order": 1,
        "nodes": [
            _node_payload(
                _OU_DN,
                "Servers",
                "ou",
                links=[
                    _link_payload(_GPO_A, _OU_DN, order=1),
                    _link_payload(_GPO_B, _OU_DN, order=2),
                    _link_payload(_GPO_C, _OU_DN, order=3),
                ],
            )
        ],
    }
    with _client(tmp_path) as client:
        resp = client.post("/api/som/plan-link-order", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert [link["gpo_guid"] for link in body["before"]] == [_GPO_A, _GPO_B, _GPO_C]
    assert [link["gpo_guid"] for link in body["after"]] == [_GPO_B, _GPO_A, _GPO_C]
    assert [link["order"] for link in body["after"]] == [1, 2, 3]


def test_api_som_plan_link_order_validation(tmp_path: Path) -> None:
    payload = {
        "scope_dn": _OU_DN,
        "gpo_guid": _GPO_A,
        "new_order": 5,
        "nodes": [
            _node_payload(
                _OU_DN,
                "Servers",
                "ou",
                links=[_link_payload(_GPO_A, _OU_DN, order=1)],
            )
        ],
    }
    with _client(tmp_path) as client:
        resp = client.post("/api/som/plan-link-order", json=payload)
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"]["issues"][0]["code"] == "link_order_out_of_bounds"


def test_api_som_set_block_inheritance(tmp_path: Path) -> None:
    payload = {
        "dn": _OU_DN,
        "block": True,
        "nodes": [
            _node_payload(_OU_DN, "Servers", "ou"),
            _node_payload(_CHILD_OU_DN, "Child", "ou", parent_dn=_OU_DN),
        ],
    }
    with _client(tmp_path) as client:
        resp = client.post("/api/som/set-block-inheritance", json=payload)
    assert resp.status_code == 200
    nodes = resp.json()["nodes"]
    updated = next(n for n in nodes if n["dn"] == _OU_DN)
    assert updated["block_inheritance"] is True
    other = next(n for n in nodes if n["dn"] == _CHILD_OU_DN)
    assert other["block_inheritance"] is False


def test_api_som_set_block_inheritance_not_found(tmp_path: Path) -> None:
    payload = {
        "dn": _DOMAIN_DN,
        "block": True,
        "nodes": [_node_payload(_OU_DN, "Servers", "ou")],
    }
    with _client(tmp_path) as client:
        resp = client.post("/api/som/set-block-inheritance", json=payload)
    assert resp.status_code == 422
    assert resp.json()["error"]["issues"][0]["code"] == "node_not_found"


def test_api_som_precedence_with_block_inheritance(tmp_path: Path) -> None:
    """End-to-end: block_inheritance surfaces as blocked entry + warning."""
    payload = {
        "target_dn": _CHILD_OU_DN,
        "nodes": [
            _node_payload(
                _CHILD_OU_DN,
                "Child",
                "ou",
                parent_dn=_OU_DN,
                links=[_link_payload(_GPO_C, _CHILD_OU_DN)],
            ),
            _node_payload(
                _OU_DN,
                "Servers",
                "ou",
                parent_dn=_DOMAIN_DN,
                block_inheritance=True,
                links=[_link_payload(_GPO_B, _OU_DN)],
            ),
            _node_payload(
                _DOMAIN_DN,
                "ad",
                "domain",
                links=[_link_payload(_GPO_A, _DOMAIN_DN, scope="domain")],
            ),
        ],
    }
    with _client(tmp_path) as client:
        resp = client.post("/api/som/precedence", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    blocked = [e for e in body["entries"] if e["blocked"]]
    assert [e["gpo_guid"] for e in blocked] == [_GPO_A]
    assert any("block_inheritance" in w for w in body["warnings"])
