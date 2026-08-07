from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, Literal

import pytest
from fastapi.testclient import TestClient

from gpo_studio.api import app
from gpo_studio.model import (
    GPO,
    RegistrySetting,
    SecurityFilter,
    WmiFilter,
)
from gpo_studio.rsop import (
    RsopGpoResult,
    RsopQuery,
    RsopResult,
    RsopSettingResult,
    RsopTarget,
    compare_rsop_results,
    compute_rsop,
)
from gpo_studio.som import SomLink, SomNode
from gpo_studio.store import WorkspaceStore

_DOMAIN_DN = "DC=ad,DC=hraedon,DC=com"
_OU_DN = "OU=Servers," + _DOMAIN_DN
_CHILD_OU_DN = "OU=Child," + _OU_DN

_GPO_A = "11111111-2222-3333-4444-555555555555"
_GPO_B = "22222222-3333-4444-5555-666666666666"
_GPO_C = "33333333-4444-5555-6666-777777777777"


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


def _setting(
    id_: str,
    side: str,
    key: str,
    value_name: str,
    value: str | int | list[str],
    *,
    hive: str = "HKLM",
    registry_type: str = "REG_SZ",
) -> RegistrySetting:
    return RegistrySetting(
        id=id_,
        side=side,  # type: ignore[arg-type]
        hive=hive,  # type: ignore[arg-type]
        key=key,
        value_name=value_name,
        registry_type=registry_type,  # type: ignore[arg-type]
        value=value,
    )


def _gpo(
    guid: str,
    name: str,
    *,
    settings: tuple[RegistrySetting, ...] = (),
    security_filters: tuple[SecurityFilter, ...] = (),
    wmi_filter: WmiFilter | None = None,
    computer_enabled: bool = True,
    user_enabled: bool = True,
) -> GPO:
    return GPO(
        guid=guid,
        name=name,
        settings=settings,
        security_filters=security_filters,
        wmi_filter=wmi_filter,
        computer_enabled=computer_enabled,
        user_enabled=user_enabled,
    )


def _target(
    *,
    computer_name: str = "",
    computer_dn: str = "",
    user_name: str = "",
    user_dn: str = "",
    domain: str = "ad.hraedon.com",
    computer_group_memberships: tuple[str, ...] = (),
    user_group_memberships: tuple[str, ...] = (),
    loopback_mode: str = "disabled",
) -> RsopTarget:
    # WI-047. There is deliberately no merged `group_memberships` shortcut here.
    # A helper that took one and split it would let a test say "the target is in
    # this group" without saying WHICH principal is, which is the ambiguity the
    # production type just had removed. Every call site below now states it.
    return RsopTarget(
        computer_name=computer_name,
        computer_dn=computer_dn,
        user_name=user_name,
        user_dn=user_dn,
        domain=domain,
        computer_group_memberships=computer_group_memberships,
        user_group_memberships=user_group_memberships,
        loopback_mode=loopback_mode,  # type: ignore[arg-type]
    )


def _query(
    query_id: str = "q1",
    *,
    target: RsopTarget | None = None,
    som_nodes: tuple[SomNode, ...] = (),
    gpos: tuple[GPO, ...] = (),
) -> RsopQuery:
    return RsopQuery(
        query_id=query_id,
        target=target
        or _target(
            user_name="alice",
            user_dn="OU=Users," + _DOMAIN_DN,
        ),
        som_nodes=som_nodes,
        gpos=gpos,
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_rsop_target_empty_error() -> None:
    target = RsopTarget(domain="ad.hraedon.com")
    issues = target.validate()
    assert any(i.code == "empty_target" and i.severity == "error" for i in issues)


def test_rsop_target_empty_domain_error() -> None:
    target = RsopTarget(user_name="alice")
    issues = target.validate()
    assert any(i.code == "empty_domain" and i.severity == "error" for i in issues)


def test_rsop_target_loopback_replace_without_computer_error() -> None:
    target = RsopTarget(
        user_name="alice",
        domain="ad.hraedon.com",
        loopback_mode="replace",
    )
    issues = target.validate()
    assert any(
        i.code == "loopback_replace_without_computer" and i.severity == "error"
        for i in issues
    )


def test_rsop_query_empty_id_error() -> None:
    query = RsopQuery(query_id="", target=_target(user_name="alice"))
    issues = query.validate()
    assert any(i.code == "empty_query_id" and i.severity == "error" for i in issues)


def test_rsop_query_empty_som_warning() -> None:
    query = RsopQuery(
        query_id="q1",
        target=_target(user_name="alice"),
        gpos=(_gpo(_GPO_A, "GPO A"),),
    )
    issues = query.validate()
    assert any(i.code == "empty_som_nodes" and i.severity == "warning" for i in issues)


def test_compute_rsop_raises_on_validation_error() -> None:
    """compute_rsop must raise ValidationError when the query has errors."""
    from gpo_studio.model import ValidationError

    # Empty query_id is an error.
    query = RsopQuery(query_id="", target=_target(user_name="alice"))
    with pytest.raises(ValidationError) as exc_info:
        compute_rsop(query)
    assert any(i.code == "empty_query_id" for i in exc_info.value.issues)


def test_compute_rsop_raises_on_empty_target() -> None:
    """compute_rsop must raise when target has no computer or user."""
    from gpo_studio.model import ValidationError

    query = RsopQuery(
        query_id="q1",
        target=_target(computer_name="", user_name="", domain="ad.hraedon.com"),
    )
    with pytest.raises(ValidationError) as exc_info:
        compute_rsop(query)
    assert any(i.code == "empty_target" for i in exc_info.value.issues)


# ---------------------------------------------------------------------------
# compute_rsop: simple and precedence
# ---------------------------------------------------------------------------


def test_compute_rsop_simple() -> None:
    gpo = _gpo(
        _GPO_A,
        "GPO A",
        settings=(
            _setting("s1", "computer", r"Software\X", "Val", "1"),
            _setting("s2", "user", r"Software\X", "Val", "2"),
        ),
    )
    nodes = (
        _node(
            "OU=Computers," + _DOMAIN_DN,
            "Computers",
            "ou",
            parent_dn=_DOMAIN_DN,
        ),
        _node(
            "OU=Users," + _DOMAIN_DN,
            "Users",
            "ou",
            parent_dn=_DOMAIN_DN,
        ),
        _node(_DOMAIN_DN, "ad", "domain", links=(_link(_GPO_A, _DOMAIN_DN, scope="domain"),)),
    )
    target = _target(
        computer_name="pc01",
        computer_dn="OU=Computers," + _DOMAIN_DN,
        user_name="alice",
        user_dn="OU=Users," + _DOMAIN_DN,
    )
    query = _query(target=target, som_nodes=nodes, gpos=(gpo,))
    result = compute_rsop(query)

    assert len(result.computer_settings) == 1
    assert len(result.user_settings) == 1
    assert result.gpos_applied()[0].gpo_guid == _GPO_A
    assert result.gpos_applied()[0].settings_applied == 2


def test_compute_rsop_precedence_wins() -> None:
    gpo_domain = _gpo(
        _GPO_A,
        "GPO A",
        settings=(_setting("s1", "computer", r"Software\X", "Val", "domain-value"),),
    )
    gpo_ou = _gpo(
        _GPO_B,
        "GPO B",
        settings=(_setting("s2", "computer", r"Software\X", "Val", "ou-value"),),
    )
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
            links=(_link(_GPO_A, _DOMAIN_DN, scope="domain"),),
        ),
    )
    target = _target(
        computer_name="pc01",
        computer_dn=_OU_DN,
    )
    query = _query(target=target, som_nodes=nodes, gpos=(gpo_domain, gpo_ou))
    result = compute_rsop(query)

    assert len(result.computer_settings) == 1
    effective = result.computer_settings[0]
    assert effective.effective_value == "ou-value"
    assert effective.winning_gpo_guid == _GPO_B
    assert effective.overridden_by == (_GPO_A,)


# ---------------------------------------------------------------------------
# Security filtering
# ---------------------------------------------------------------------------


def test_compute_rsop_security_filter_mismatch_filtered() -> None:
    gpo = _gpo(
        _GPO_A,
        "GPO A",
        settings=(_setting("s1", "computer", r"Software\X", "Val", "1"),),
        security_filters=(
            SecurityFilter(
                id="f1",
                principal="S-1-5-21-9999999999-9999999999-9999999999-1234",
                permission="apply",
                target_type="group",
            ),
        ),
    )
    nodes = (
        _node(
            "OU=Computers," + _DOMAIN_DN,
            "Computers",
            "ou",
            parent_dn=_DOMAIN_DN,
        ),
        _node(_DOMAIN_DN, "ad", "domain", links=(_link(_GPO_A, _DOMAIN_DN, scope="domain"),)),
    )
    target = _target(
        computer_name="pc01",
        computer_dn="OU=Computers," + _DOMAIN_DN,
        computer_group_memberships=("S-1-5-21-0000000000-0000000000-0000000000-5678",),
    )
    query = _query(target=target, som_nodes=nodes, gpos=(gpo,))
    result = compute_rsop(query)

    assert len(result.computer_settings) == 0
    assert len(result.gpos_filtered()) == 1
    assert "security_filter_mismatch" in result.gpos_filtered()[0].filtering_reasons


def test_compute_rsop_security_filter_match_applied() -> None:
    group_sid = "S-1-5-21-0000000000-0000000000-0000000000-5678"
    gpo = _gpo(
        _GPO_A,
        "GPO A",
        settings=(_setting("s1", "computer", r"Software\X", "Val", "1"),),
        security_filters=(
            SecurityFilter(
                id="f1",
                principal="Apply Group",
                permission="apply",
                target_type="group",
                sid=group_sid,
            ),
        ),
    )
    nodes = (
        _node(
            "OU=Computers," + _DOMAIN_DN,
            "Computers",
            "ou",
            parent_dn=_DOMAIN_DN,
        ),
        _node(_DOMAIN_DN, "ad", "domain", links=(_link(_GPO_A, _DOMAIN_DN, scope="domain"),)),
    )
    target = _target(
        computer_name="pc01",
        computer_dn="OU=Computers," + _DOMAIN_DN,
        computer_group_memberships=(group_sid,),
    )
    query = _query(target=target, som_nodes=nodes, gpos=(gpo,))
    result = compute_rsop(query)

    assert len(result.computer_settings) == 1
    assert len(result.gpos_applied()) == 1


def test_compute_rsop_security_filter_sid_case_insensitive() -> None:
    """Windows SIDs are case-insensitive; matching must be too."""
    group_sid = "S-1-5-21-0000000000-0000000000-0000000000-5678"
    gpo = _gpo(
        _GPO_A,
        "GPO A",
        settings=(_setting("s1", "computer", r"Software\X", "Val", "1"),),
        security_filters=(
            SecurityFilter(
                id="f1",
                principal="Apply Group",
                permission="apply",
                target_type="group",
                sid=group_sid.lower(),  # lowercase SID in filter
            ),
        ),
    )
    nodes = (
        _node(
            "OU=Computers," + _DOMAIN_DN,
            "Computers",
            "ou",
            parent_dn=_DOMAIN_DN,
        ),
        _node(_DOMAIN_DN, "ad", "domain", links=(_link(_GPO_A, _DOMAIN_DN, scope="domain"),)),
    )
    target = _target(
        computer_name="pc01",
        computer_dn="OU=Computers," + _DOMAIN_DN,
        computer_group_memberships=(group_sid.upper(),),  # uppercase SID in target
    )
    query = _query(target=target, som_nodes=nodes, gpos=(gpo,))
    result = compute_rsop(query)

    assert len(result.computer_settings) == 1
    assert len(result.gpos_applied()) == 1


def test_compute_rsop_principal_case_insensitive() -> None:
    """Principal names should also match case-insensitively."""
    gpo = _gpo(
        _GPO_A,
        "GPO A",
        settings=(_setting("s1", "computer", r"Software\X", "Val", "1"),),
        security_filters=(
            SecurityFilter(
                id="f1",
                principal="DOMAIN\\Users",
                permission="apply",
                target_type="group",
            ),
        ),
    )
    nodes = (
        _node(
            "OU=Computers," + _DOMAIN_DN,
            "Computers",
            "ou",
            parent_dn=_DOMAIN_DN,
        ),
        _node(_DOMAIN_DN, "ad", "domain", links=(_link(_GPO_A, _DOMAIN_DN, scope="domain"),)),
    )
    target = _target(
        computer_name="pc01",
        computer_dn="OU=Computers," + _DOMAIN_DN,
        computer_group_memberships=("domain\\users",),  # different case
    )
    query = _query(target=target, som_nodes=nodes, gpos=(gpo,))
    result = compute_rsop(query)

    assert len(result.computer_settings) == 1
    assert len(result.gpos_applied()) == 1


# ---------------------------------------------------------------------------
# Disabled GPO
# ---------------------------------------------------------------------------


def test_compute_rsop_computer_disabled() -> None:
    gpo = _gpo(
        _GPO_A,
        "GPO A",
        settings=(
            _setting("s1", "computer", r"Software\X", "Val", "1"),
            _setting("s2", "user", r"Software\X", "Val", "2"),
        ),
        computer_enabled=False,
    )
    nodes = (
        _node(
            "OU=Computers," + _DOMAIN_DN,
            "Computers",
            "ou",
            parent_dn=_DOMAIN_DN,
        ),
        _node(
            "OU=Users," + _DOMAIN_DN,
            "Users",
            "ou",
            parent_dn=_DOMAIN_DN,
        ),
        _node(_DOMAIN_DN, "ad", "domain", links=(_link(_GPO_A, _DOMAIN_DN, scope="domain"),)),
    )
    target = _target(
        computer_name="pc01",
        computer_dn="OU=Computers," + _DOMAIN_DN,
        user_name="alice",
        user_dn="OU=Users," + _DOMAIN_DN,
    )
    query = _query(target=target, som_nodes=nodes, gpos=(gpo,))
    result = compute_rsop(query)

    assert len(result.computer_settings) == 0
    assert len(result.user_settings) == 1
    gpo_result = result.gpo_results[0]
    assert gpo_result.status == "applied"
    assert "computer_side_disabled" in gpo_result.filtering_reasons


# ---------------------------------------------------------------------------
# Block inheritance and enforced
# ---------------------------------------------------------------------------


def test_compute_rsop_block_inheritance_blocks_above() -> None:
    gpo_parent = _gpo(
        _GPO_A,
        "GPO A",
        settings=(_setting("s1", "computer", r"Software\X", "Val", "1"),),
    )
    nodes = (
        _node(
            _CHILD_OU_DN,
            "Child",
            "ou",
            parent_dn=_OU_DN,
            block_inheritance=True,
        ),
        _node(
            _OU_DN,
            "Servers",
            "ou",
            parent_dn=_DOMAIN_DN,
            links=(_link(_GPO_A, _OU_DN),),
        ),
        _node(_DOMAIN_DN, "ad", "domain"),
    )
    target = _target(computer_name="pc01", computer_dn=_CHILD_OU_DN)
    query = _query(target=target, som_nodes=nodes, gpos=(gpo_parent,))
    result = compute_rsop(query)

    assert len(result.computer_settings) == 0
    filtered = result.gpos_filtered()
    assert len(filtered) == 1
    assert filtered[0].is_blocked
    assert "blocked_by_inheritance" in filtered[0].filtering_reasons


def test_compute_rsop_enforced_survives_block() -> None:
    gpo_parent = _gpo(
        _GPO_A,
        "GPO A",
        settings=(_setting("s1", "computer", r"Software\X", "Val", "1"),),
    )
    nodes = (
        _node(
            _CHILD_OU_DN,
            "Child",
            "ou",
            parent_dn=_OU_DN,
            block_inheritance=True,
        ),
        _node(
            _OU_DN,
            "Servers",
            "ou",
            parent_dn=_DOMAIN_DN,
            links=(_link(_GPO_A, _OU_DN, enforced=True),),
        ),
        _node(_DOMAIN_DN, "ad", "domain"),
    )
    target = _target(computer_name="pc01", computer_dn=_CHILD_OU_DN)
    query = _query(target=target, som_nodes=nodes, gpos=(gpo_parent,))
    result = compute_rsop(query)

    assert len(result.computer_settings) == 1
    assert result.computer_settings[0].winning_gpo_guid == _GPO_A
    assert len(result.gpos_applied()) == 1
    assert result.gpos_applied()[0].is_enforced


# ---------------------------------------------------------------------------
# WMI filter
# ---------------------------------------------------------------------------


def test_compute_rsop_wmi_filter_applied_with_warning() -> None:
    gpo = _gpo(
        _GPO_A,
        "GPO A",
        settings=(_setting("s1", "computer", r"Software\X", "Val", "1"),),
        wmi_filter=WmiFilter(id="w1", name="Filter", query='SELECT * FROM Win32_OperatingSystem'),
    )
    nodes = (
        _node(
            "OU=Computers," + _DOMAIN_DN,
            "Computers",
            "ou",
            parent_dn=_DOMAIN_DN,
        ),
        _node(_DOMAIN_DN, "ad", "domain", links=(_link(_GPO_A, _DOMAIN_DN, scope="domain"),)),
    )
    target = _target(computer_name="pc01", computer_dn="OU=Computers," + _DOMAIN_DN)
    query = _query(target=target, som_nodes=nodes, gpos=(gpo,))
    result = compute_rsop(query)

    assert len(result.computer_settings) == 1
    assert len(result.gpos_applied()) == 1
    assert any("wmi_filter_unknown" in r for r in result.gpos_applied()[0].filtering_reasons)
    assert any("wmi_filter_unknown" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# Loopback processing
# ---------------------------------------------------------------------------


def test_compute_rsop_loopback_replace_user_from_computer_gpos() -> None:
    gpo_computer = _gpo(
        _GPO_A,
        "Computer GPO",
        settings=(_setting("s1", "user", r"Software\X", "Val", "computer-value"),),
    )
    gpo_user = _gpo(
        _GPO_B,
        "User GPO",
        settings=(_setting("s2", "user", r"Software\X", "Val", "user-value"),),
    )
    nodes = (
        _node(
            "OU=Computers," + _DOMAIN_DN,
            "Computers",
            "ou",
            parent_dn=_DOMAIN_DN,
            links=(_link(_GPO_A, "OU=Computers," + _DOMAIN_DN),),
        ),
        _node(
            "OU=Users," + _DOMAIN_DN,
            "Users",
            "ou",
            parent_dn=_DOMAIN_DN,
            links=(_link(_GPO_B, "OU=Users," + _DOMAIN_DN),),
        ),
        _node(_DOMAIN_DN, "ad", "domain"),
    )
    target = _target(
        computer_name="pc01",
        computer_dn="OU=Computers," + _DOMAIN_DN,
        user_name="alice",
        user_dn="OU=Users," + _DOMAIN_DN,
        loopback_mode="replace",
    )
    query = _query(target=target, som_nodes=nodes, gpos=(gpo_computer, gpo_user))
    result = compute_rsop(query)

    assert len(result.user_settings) == 1
    assert result.user_settings[0].winning_gpo_guid == _GPO_A
    assert result.user_settings[0].effective_value == "computer-value"


def test_compute_rsop_loopback_merge_computer_wins() -> None:
    gpo_computer = _gpo(
        _GPO_A,
        "Computer GPO",
        settings=(_setting("s1", "user", r"Software\X", "Val", "computer-value"),),
    )
    gpo_user = _gpo(
        _GPO_B,
        "User GPO",
        settings=(_setting("s2", "user", r"Software\X", "Val", "user-value"),),
    )
    nodes = (
        _node(
            "OU=Computers," + _DOMAIN_DN,
            "Computers",
            "ou",
            parent_dn=_DOMAIN_DN,
            links=(_link(_GPO_A, "OU=Computers," + _DOMAIN_DN),),
        ),
        _node(
            "OU=Users," + _DOMAIN_DN,
            "Users",
            "ou",
            parent_dn=_DOMAIN_DN,
            links=(_link(_GPO_B, "OU=Users," + _DOMAIN_DN),),
        ),
        _node(_DOMAIN_DN, "ad", "domain"),
    )
    target = _target(
        computer_name="pc01",
        computer_dn="OU=Computers," + _DOMAIN_DN,
        user_name="alice",
        user_dn="OU=Users," + _DOMAIN_DN,
        loopback_mode="merge",
    )
    query = _query(target=target, som_nodes=nodes, gpos=(gpo_computer, gpo_user))
    result = compute_rsop(query)

    assert len(result.user_settings) == 1
    assert result.user_settings[0].effective_value == "computer-value"
    assert result.user_settings[0].winning_gpo_guid == _GPO_A
    assert _GPO_B in result.user_settings[0].overridden_by


# ---------------------------------------------------------------------------
# RsopResult helpers
# ---------------------------------------------------------------------------


def test_rsop_result_get_effective_value() -> None:
    result = RsopResult(
        query_id="q1",
        mode="planning",
        target=_target(user_name="alice"),
        computer_settings=(
            RsopSettingResult(
                setting_id="s1",
                side="computer",
                key=r"Software\X",
                value_name="Val",
                effective_value="1",
            ),
        ),
    )
    found = result.get_effective_value("computer", r"Software\X", "Val")
    assert found is not None
    assert found.effective_value == "1"
    assert result.get_effective_value("computer", r"Software\Y", "Val") is None


def test_rsop_result_gpos_applied_and_filtered() -> None:
    result = RsopResult(
        query_id="q1",
        mode="planning",
        target=_target(user_name="alice"),
        gpo_results=(
            RsopGpoResult(gpo_guid=_GPO_A, gpo_name="A", status="applied"),
            RsopGpoResult(gpo_guid=_GPO_B, gpo_name="B", status="blocked"),
        ),
    )
    assert len(result.gpos_applied()) == 1
    assert result.gpos_applied()[0].gpo_guid == _GPO_A
    assert len(result.gpos_filtered()) == 1
    assert result.gpos_filtered()[0].gpo_guid == _GPO_B


# ---------------------------------------------------------------------------
# compare_rsop_results
# ---------------------------------------------------------------------------


def _make_result(value: str, gpo_guid: str) -> RsopResult:
    return RsopResult(
        query_id="q1",
        mode="planning",
        target=_target(user_name="alice"),
        computer_settings=(
            RsopSettingResult(
                setting_id="s1",
                side="computer",
                key=r"Software\X",
                value_name="Val",
                effective_value=value,
                winning_gpo_guid=gpo_guid,
            ),
        ),
    )


def test_compare_rsop_added() -> None:
    baseline = RsopResult(query_id="q1", mode="planning", target=_target(user_name="alice"))
    current = _make_result("1", _GPO_A)
    diffs = compare_rsop_results(baseline, current)
    assert len(diffs) == 1
    assert diffs[0].change_type == "added"


def test_compare_rsop_removed() -> None:
    baseline = _make_result("1", _GPO_A)
    current = RsopResult(query_id="q1", mode="planning", target=_target(user_name="alice"))
    diffs = compare_rsop_results(baseline, current)
    assert len(diffs) == 1
    assert diffs[0].change_type == "removed"


def test_compare_rsop_modified() -> None:
    baseline = _make_result("1", _GPO_A)
    current = _make_result("2", _GPO_A)
    diffs = compare_rsop_results(baseline, current)
    assert len(diffs) == 1
    assert diffs[0].change_type == "modified"


def test_compare_rsop_gpo_changed() -> None:
    baseline = _make_result("1", _GPO_A)
    current = _make_result("1", _GPO_B)
    diffs = compare_rsop_results(baseline, current)
    assert len(diffs) == 1
    assert diffs[0].change_type == "gpo_changed"


# WI-026, RESOLVED 2026-08-04: a target DN that names no SOM node now resolves
# to its nearest ancestor that does.
#
# Found while constructing the WP-6B lane's prediction against the real estate,
# before the lane ran. compute_precedence looks up SOM *containers*, but
# RsopTarget.computer_dn is an object DN in every real caller: ad_discovery
# returns CN=host,OU=..., and the same field is matched against security-filter
# principals, where the object DN is the correct value. The two uses wanted
# different strings and only one of them worked.
#
# It survived because all thirteen other call sites in this file pass a
# container DN, so the model was only ever exercised with the one input shape it
# tolerated. Self-consistency is not evidence.
#
# The fix is backwards compatible -- a container DN is found on the first lookup
# and never enters the walk -- so the WP-6B certification computed under the old
# behaviour still stands, and the lane now passes the client's real object DN and
# produces the same prediction Windows confirmed.


def _wi026_nodes() -> tuple[SomNode, ...]:
    return (
        _node(
            _CHILD_OU_DN,
            "Child",
            "ou",
            parent_dn=_OU_DN,
            links=(_link(_GPO_A, _CHILD_OU_DN),),
        ),
        _node(_OU_DN, "Servers", "ou", parent_dn=_DOMAIN_DN),
        _node(_DOMAIN_DN, "ad", "domain"),
    )


def _wi026_gpo() -> GPO:
    return _gpo(
        _GPO_A,
        "GPO A",
        settings=(_setting("s1", "computer", r"Software\X", "Val", "applied"),),
    )


def test_wi026_container_dn_resolves_normally() -> None:
    """The pre-existing convention still works, unchanged."""
    query = _query(
        target=_target(computer_name="pc01", computer_dn=_CHILD_OU_DN),
        som_nodes=_wi026_nodes(),
        gpos=(_wi026_gpo(),),
    )
    result = compute_rsop(query)

    assert [g.gpo_name for g in result.gpos_applied()] == ["GPO A"]
    assert len(result.computer_settings) == 1
    assert result.warnings == ()


def test_wi026_computer_object_dn_resolves_to_its_container() -> None:
    """The shape a real directory returns now produces the right answer.

    Before the fix this returned no applied GPOs and no settings -- an empty
    result identical to a correctly-computed "no policy applies", for a machine
    Windows applies policy to.
    """
    query = _query(
        target=_target(computer_name="pc01", computer_dn="CN=pc01," + _CHILD_OU_DN),
        som_nodes=_wi026_nodes(),
        gpos=(_wi026_gpo(),),
    )
    result = compute_rsop(query)

    assert [g.gpo_name for g in result.gpos_applied()] == ["GPO A"]
    assert len(result.computer_settings) == 1
    assert result.computer_settings[0].effective_value == "applied"


def test_wi026_object_dn_resolution_is_case_insensitive() -> None:
    """AD returns whatever case it stored; the SOM tree is built by a caller.

    The live estate returns ``CN=LABCL01,OU=StudioRsopChild-...`` for a computer
    whose OU the harness created as ``StudioRsopChild-...``, so the two DNs being
    compared genuinely differ in case.
    """
    query = _query(
        target=_target(computer_name="pc01", computer_dn=("CN=PC01," + _CHILD_OU_DN).upper()),
        som_nodes=_wi026_nodes(),
        gpos=(_wi026_gpo(),),
    )
    result = compute_rsop(query)

    assert [g.gpo_name for g in result.gpos_applied()] == ["GPO A"]


def test_wi026_deeply_nested_object_dn_finds_the_nearest_container() -> None:
    """Resolution stops at the FIRST ancestor in the tree, not the domain.

    Walking too far would silently compute policy for the wrong container --
    which is a worse failure than the empty result it replaces, because it looks
    like an answer.
    """
    nodes = (
        _node(_CHILD_OU_DN, "Child", "ou", parent_dn=_OU_DN, links=(_link(_GPO_A, _CHILD_OU_DN),)),
        _node(_OU_DN, "Servers", "ou", parent_dn=_DOMAIN_DN, links=(_link(_GPO_B, _OU_DN),)),
        _node(_DOMAIN_DN, "ad", "domain"),
    )
    gpo_a = _wi026_gpo()
    gpo_b = _gpo(
        _GPO_B,
        "GPO B",
        settings=(_setting("s2", "computer", r"Software\X", "Val", "parent"),),
    )
    query = _query(
        target=_target(computer_name="pc01", computer_dn="CN=pc01," + _CHILD_OU_DN),
        som_nodes=nodes,
        gpos=(gpo_a, gpo_b),
    )
    result = compute_rsop(query)

    # The child OU applies last and wins; reaching only the parent would have
    # produced "parent" here.
    assert result.computer_settings[0].effective_value == "applied"
    assert result.computer_settings[0].winning_gpo_name == "GPO A"


def test_wi026_wholly_unrelated_dn_still_warns() -> None:
    """When nothing in the ancestry matches, the empty result must say so."""
    query = _query(
        target=_target(computer_name="pc01", computer_dn="CN=pc01,OU=Elsewhere,DC=other,DC=test"),
        som_nodes=_wi026_nodes(),
        gpos=(_wi026_gpo(),),
    )
    result = compute_rsop(query)

    assert result.gpo_results == ()
    assert any("nor does" in warning for warning in result.warnings), result.warnings


# WI-031, found by the WP-6B oracle 2026-08-04 and fixed the same day.
#
# Enforcement was absent from the precedence sort key entirely, so an enforced
# link was ordered by its scope like any other: a GPO enforced at the domain sat
# in the domain tier and LOST to a plain OU link. Three consecutive runs on a
# real Windows 11 26200 client resolved `domainEnforced` where Studio predicted
# `child`.
#
# Enforcement has two independent effects and only one was implemented. Surviving
# a block-inheritance cutoff worked -- which is why the applied/denied sets
# matched Windows exactly while the winning VALUE did not, and why a lane that
# compared only applied sets would have called this a pass.
#
# No existing test exercised enforced-versus-lower-scope precedence, which is why
# the whole suite stayed green over it.


def test_wi031_enforced_domain_link_beats_a_plain_ou_link() -> None:
    """The defect the oracle found, in the shape it found it."""
    domain_enforced = _gpo(
        _GPO_A,
        "DomainEnforced",
        settings=(_setting("s1", "computer", r"Software\X", "Block", "domainEnforced"),),
    )
    child = _gpo(
        _GPO_B,
        "ChildGPO",
        settings=(_setting("s2", "computer", r"Software\X", "Block", "child"),),
    )
    nodes = (
        _node(
            _CHILD_OU_DN,
            "Child",
            "ou",
            parent_dn=_DOMAIN_DN,
            links=(_link(_GPO_B, _CHILD_OU_DN),),
        ),
        _node(
            _DOMAIN_DN,
            "ad",
            "domain",
            links=(_link(_GPO_A, _DOMAIN_DN, scope="domain", enforced=True),),
        ),
    )
    result = compute_rsop(
        _query(
            target=_target(computer_name="pc01", computer_dn="CN=pc01," + _CHILD_OU_DN),
            som_nodes=nodes,
            gpos=(domain_enforced, child),
        )
    )

    winner = result.get_effective_value("computer", r"Software\X", "Block")
    assert winner is not None
    assert winner.effective_value == "domainEnforced"
    assert winner.winning_gpo_name == "DomainEnforced"


def test_wi031_among_enforced_links_the_higher_scope_wins() -> None:
    """The hierarchy inverts for enforced links: closest to the root wins.

    Two enforced links in conflict is the case that distinguishes "enforced
    outranks non-enforced" from the full rule. Without the inversion an enforced
    OU link would beat an enforced domain link, which is backwards.
    """
    domain_enforced = _gpo(
        _GPO_A,
        "DomainEnforced",
        settings=(_setting("s1", "computer", r"Software\X", "Block", "domain"),),
    )
    ou_enforced = _gpo(
        _GPO_B,
        "OuEnforced",
        settings=(_setting("s2", "computer", r"Software\X", "Block", "ou"),),
    )
    nodes = (
        _node(
            _CHILD_OU_DN,
            "Child",
            "ou",
            parent_dn=_DOMAIN_DN,
            links=(_link(_GPO_B, _CHILD_OU_DN, enforced=True),),
        ),
        _node(
            _DOMAIN_DN,
            "ad",
            "domain",
            links=(_link(_GPO_A, _DOMAIN_DN, scope="domain", enforced=True),),
        ),
    )
    result = compute_rsop(
        _query(
            target=_target(computer_name="pc01", computer_dn="CN=pc01," + _CHILD_OU_DN),
            som_nodes=nodes,
            gpos=(domain_enforced, ou_enforced),
        )
    )

    winner = result.get_effective_value("computer", r"Software\X", "Block")
    assert winner is not None
    assert winner.effective_value == "domain"


def test_wi031_non_enforced_precedence_is_unchanged() -> None:
    """The regression guard: ordinary LSDOU must not move.

    lsdou-precedence is WP-6B-certified against Windows, so any change to this
    ordering would invalidate a passing certification rather than improve it.
    """
    domain_gpo = _gpo(
        _GPO_A,
        "DomainPlain",
        settings=(_setting("s1", "computer", r"Software\X", "Block", "domain"),),
    )
    child = _gpo(
        _GPO_B,
        "ChildGPO",
        settings=(_setting("s2", "computer", r"Software\X", "Block", "child"),),
    )
    nodes = (
        _node(
            _CHILD_OU_DN,
            "Child",
            "ou",
            parent_dn=_DOMAIN_DN,
            links=(_link(_GPO_B, _CHILD_OU_DN),),
        ),
        _node(
            _DOMAIN_DN,
            "ad",
            "domain",
            links=(_link(_GPO_A, _DOMAIN_DN, scope="domain"),),
        ),
    )
    result = compute_rsop(
        _query(
            target=_target(computer_name="pc01", computer_dn="CN=pc01," + _CHILD_OU_DN),
            som_nodes=nodes,
            gpos=(domain_gpo, child),
        )
    )

    winner = result.get_effective_value("computer", r"Software\X", "Block")
    assert winner is not None
    assert winner.effective_value == "child"


class TestDenyFiltering:
    """WI-033: a deny ACE on Apply Group Policy keeps the GPO off the target.

    Before this, `SecurityFilter` had no polarity, so a DACL holding both an
    allow and a deny for the same principal was modelled as applying. The model
    told an operator a machine would receive settings Windows keeps off it --
    the dangerous direction, and demonstrated against a real 26200 client
    before being fixed.
    """

    def _query(self, filters: tuple[SecurityFilter, ...]) -> RsopQuery:
        dn = "OU=Child,DC=x"
        gpo = GPO(
            guid="g1",
            name="Filtered",
            security_filters=filters,
            settings=(
                RegistrySetting(
                    id="s",
                    side="computer",
                    hive="HKLM",
                    key="Software\\Policies\\StudioLab",
                    value_name="V",
                    registry_type="REG_SZ",
                    value="applied",
                ),
            ),
        )
        return RsopQuery(
            query_id="q",
            target=RsopTarget(
                computer_name="C",
                computer_dn=f"CN=C,{dn}",
                domain="x",
                computer_group_memberships=("LabGroup",),
            ),
            som_nodes=(
                SomNode(
                    dn=dn,
                    name="Child",
                    scope="ou",
                    parent_dn="",
                    links=(SomLink(gpo_guid="g1", scope="ou", scope_dn=dn, order=1),),
                ),
            ),
            gpos=(gpo,),
        )

    def _applied(self, filters: tuple[SecurityFilter, ...]) -> bool:
        result = compute_rsop(self._query(filters))
        return any(g.status == "applied" for g in result.gpo_results)

    def test_an_allow_alone_applies(self) -> None:
        """The control: without it, a deny test proves only that nothing applies."""
        assert self._applied((SecurityFilter(id="a", principal="C", permission="apply"),))

    def test_a_deny_beside_an_allow_wins(self) -> None:
        """The case that was wrong."""
        assert not self._applied(
            (
                SecurityFilter(id="a", principal="C", permission="apply"),
                SecurityFilter(id="d", principal="C", permission="apply", deny=True),
            )
        )

    def test_a_deny_on_a_group_the_target_belongs_to_wins(self) -> None:
        """Deny reaches through the token, exactly as allow does."""
        assert not self._applied(
            (
                SecurityFilter(id="a", principal="C", permission="apply"),
                SecurityFilter(id="d", principal="LabGroup", permission="apply", deny=True),
            )
        )

    def test_a_deny_for_someone_else_does_not_block(self) -> None:
        """Still a filter: a deny naming a principal the target is not must not bite."""
        assert self._applied(
            (
                SecurityFilter(id="a", principal="C", permission="apply"),
                SecurityFilter(id="d", principal="Stranger", permission="apply", deny=True),
            )
        )

    def test_a_deny_on_read_blocks_even_with_apply_allowed(self) -> None:
        """WI-040, and it got INVERTED by the oracle rather than confirmed.

        This assertion used to run the other way, under the name
        `test_a_deny_on_read_does_not_block_apply` and the docstring "the right
        being denied matters; this models Apply Group Policy only" -- which read
        as a certified design decision while sitting among four deny cases that
        really were measured. It was an assumption in the vocabulary of a
        certification, and it was wrong.

        Run `rsop-observe-20260805045139-3731` settled it on a real 26200
        client: a GPO carrying a deny on GenericRead, with its Read + Apply
        allow INTACT, did not apply. Applying takes both rights.
        """
        assert not self._applied(
            (
                SecurityFilter(id="a", principal="C", permission="apply"),
                SecurityFilter(id="d", principal="C", permission="read", deny=True),
            )
        )

    def test_a_read_deny_for_someone_else_does_not_block(self) -> None:
        """The mirror of the Apply case: a read deny must still be matched."""
        assert self._applied(
            (
                SecurityFilter(id="a", principal="C", permission="apply"),
                SecurityFilter(id="d", principal="Stranger", permission="read", deny=True),
            )
        )

    def test_the_reason_names_the_denied_right(self) -> None:
        """`security_filter_denied` would send an operator to Apply, which IS granted."""
        result = compute_rsop(
            self._query(
                (
                    SecurityFilter(id="a", principal="C", permission="apply"),
                    SecurityFilter(id="d", principal="C", permission="read", deny=True),
                )
            )
        )
        reasons = result.gpo_results[0].filtering_reasons
        assert "security_filter_read_denied" in reasons
        assert "security_filter_denied" not in reasons

    def test_the_reason_names_the_deny(self) -> None:
        """`security_filter_mismatch` would say the principal lacked Apply, which is false."""
        result = compute_rsop(
            self._query(
                (
                    SecurityFilter(id="a", principal="C", permission="apply"),
                    SecurityFilter(id="d", principal="C", permission="apply", deny=True),
                )
            )
        )
        reasons = {r for g in result.gpo_results for r in g.filtering_reasons}
        assert "security_filter_denied" in reasons
        assert "security_filter_mismatch" not in reasons


class TestWmiFilterEvaluation:
    """WI-035: a WMI filter the caller has evaluated must be honoured.

    Studio does not evaluate WQL and should not -- that is the CSE's job on the
    live machine. What it can do is honour an answer a caller already has.
    Before this, a WMI-filtered GPO was predicted to apply whatever its filter
    would evaluate to, which tells an operator settings will arrive when they
    will not. Demonstrated against a real 26200 client before being fixed.
    """

    def _query(self, results: tuple[tuple[str, bool], ...]) -> RsopQuery:
        dn = "OU=Child,DC=x"
        gpo = GPO(
            guid="g1",
            name="Filtered",
            wmi_filter=WmiFilter(
                id="w1", name="never", query="SELECT * FROM Win32_OperatingSystem"
            ),
            settings=(
                RegistrySetting(
                    id="s",
                    side="computer",
                    hive="HKLM",
                    key="Software\\Policies\\StudioLab",
                    value_name="V",
                    registry_type="REG_SZ",
                    value="applied",
                ),
            ),
        )
        return RsopQuery(
            query_id="q",
            target=RsopTarget(computer_name="C", computer_dn=f"CN=C,{dn}", domain="x"),
            som_nodes=(
                SomNode(
                    dn=dn,
                    name="Child",
                    scope="ou",
                    parent_dn="",
                    links=(SomLink(gpo_guid="g1", scope="ou", scope_dn=dn, order=1),),
                ),
            ),
            gpos=(gpo,),
            wmi_filter_results=results,
        )

    def _result(self, results: tuple[tuple[str, bool], ...]):
        return compute_rsop(self._query(results))

    def test_a_filter_evaluated_false_blocks_the_gpo(self) -> None:
        """The case that was wrong."""
        result = self._result((("w1", False),))
        assert not any(g.status == "applied" for g in result.gpo_results)
        assert "wmi_filter_false" in {r for g in result.gpo_results for r in g.filtering_reasons}

    def test_a_filter_evaluated_true_applies_without_a_warning(self) -> None:
        """A known-true filter is not a caveat; saying so keeps the warning meaningful."""
        result = self._result((("w1", True),))
        assert any(g.status == "applied" for g in result.gpo_results)
        assert "wmi_filter_unknown" not in result.warnings

    def test_an_unevaluated_filter_still_applies_and_still_warns(self) -> None:
        """Unknown must not become false: an invented absence is harder to notice."""
        result = self._result(())
        assert any(g.status == "applied" for g in result.gpo_results)
        assert "wmi_filter_unknown" in result.warnings

    def test_a_result_for_another_filter_does_not_apply_here(self) -> None:
        """Results are keyed by filter, not merged into one verdict."""
        result = self._result((("someone-else", False),))
        assert any(g.status == "applied" for g in result.gpo_results)
        assert "wmi_filter_unknown" in result.warnings

    def test_an_unevaluatable_filter_blocks(self) -> None:
        """WI-039, measured: Windows fails closed on a filter it cannot evaluate.

        A filter naming a class the target does not have cannot be true, and
        the machine treats that as not-applying rather than as not-filtering.
        """
        result = self._result((("w1", "unevaluatable"),))
        assert not any(g.status == "applied" for g in result.gpo_results)
        reasons = {r for g in result.gpo_results for r in g.filtering_reasons}
        assert "wmi_filter_unevaluatable" in reasons

    def test_unevaluatable_is_not_reported_as_false(self) -> None:
        """The reason has to distinguish them: they are different facts.

        A filter that evaluated false was evaluated. One that could not be
        evaluated was not, and an operator reading the reason should be able to
        tell which happened.
        """
        result = self._result((("w1", "unevaluatable"),))
        reasons = {r for g in result.gpo_results for r in g.filtering_reasons}
        assert "wmi_filter_false" not in reasons

    def test_unevaluatable_and_absent_stay_different(self) -> None:
        """The distinction WI-039 exists for.

        "Nobody supplied an answer" and "there is no answer to supply" deserve
        different predictions. Collapsing them is what the fix undoes.
        """
        unevaluatable = self._result((("w1", "unevaluatable"),))
        absent = self._result(())
        assert not any(g.status == "applied" for g in unevaluatable.gpo_results)
        assert any(g.status == "applied" for g in absent.gpo_results)


class TestReadDenyIsEvaluatedAgainstTheReadingPrincipal:
    """WI-043, MEASURED 2026-08-06. The abstention is gone because it was closed.

    Three runs across both scopes, and they collapse to one rule:

        side=computer, deny names the COMPUTER -> BLOCKS
                       (rsop-observe-20260805045139-3731, WI-040)
        side=user,     deny names the COMPUTER -> BLOCKS
                       (rsop-user-observe-20260806165543-8004, row B)
        side=user,     deny names the USER     -> APPLIES
                       (rsop-user-observe-20260806165543-8004, row A)

    A READ DENY GATES POLICY WHEN IT NAMES THE COMPUTER, ON EITHER SIDE, because
    MS16-072 has the computer perform the retrieval for both sides. The side
    being resolved does not decide it; the principal named by the deny does.

    This class previously asserted that the user side was `unevaluable`. That
    was the honest answer while nobody had looked, and it is the wrong answer
    now that someone has. The tests were rewritten rather than deleted so the
    replaced claim stays legible in history.

    Apply Group Policy runs the other way -- it is evaluated against the
    principal the policy applies TO -- which is why every test here that varies
    the read deny keeps the apply allow fixed.
    """

    def _query(
        self,
        side: Literal["computer", "user"],
        *,
        deny_names: str = "",
    ) -> RsopQuery:
        """A GPO whose apply allow names the resolving side's principal.

        The computer is ALWAYS present, even on a user-scope query. That is not
        scaffolding: after WI-047 the read deny is resolved against the
        computer's identities, so a user-scope query with no computer would have
        nothing for a computer-named deny to match and every row here would
        pass by accident.
        """
        dn = "OU=Child,DC=x"
        principal = "U" if side == "user" else "C"
        gpo = GPO(
            guid="g1",
            name="ReadDenied",
            security_filters=(
                SecurityFilter(id="a", principal=principal, permission="apply"),
                SecurityFilter(
                    id="d",
                    principal=deny_names or principal,
                    permission="read",
                    deny=True,
                ),
            ),
            settings=(
                RegistrySetting(
                    id="s",
                    side=side,
                    hive="HKCU" if side == "user" else "HKLM",
                    key="Software\\Policies\\StudioLab",
                    value_name="V",
                    registry_type="REG_SZ",
                    value="applied",
                ),
            ),
        )
        target = RsopTarget(
            computer_name="C",
            computer_dn=f"CN=C,{dn}",
            user_name="U" if side == "user" else "",
            user_dn=f"CN=U,{dn}" if side == "user" else "",
            domain="x",
        )
        return RsopQuery(
            query_id="q",
            target=target,
            som_nodes=(
                SomNode(
                    dn=dn,
                    name="Child",
                    scope="ou",
                    parent_dn="",
                    links=(SomLink(gpo_guid="g1", scope="ou", scope_dn=dn, order=1),),
                ),
            ),
            gpos=(gpo,),
        )

    def test_the_computer_side_blocks_on_a_computer_named_deny(self) -> None:
        """WI-040's certified case, unchanged by WI-047."""
        result = compute_rsop(self._query("computer"))
        assert result.gpo_results[0].status == "blocked"
        assert "security_filter_read_denied" in result.gpo_results[0].filtering_reasons

    def test_the_user_side_blocks_on_a_COMPUTER_named_deny(self) -> None:
        """Row B. Denying the reading principal withholds the user's policy."""
        result = compute_rsop(self._query("user", deny_names="C"))
        assert result.gpo_results[0].status == "blocked"
        assert "security_filter_read_denied" in result.gpo_results[0].filtering_reasons

    def test_the_user_side_APPLIES_on_a_user_named_deny(self) -> None:
        """Row A, and the assertion this class used to make the opposite of.

        The user's own read deny is not consulted, because the user is not the
        principal performing the retrieval. Measured on a real 26200 client:
        the GPO applied AND won the conflict at link order 1.
        """
        result = compute_rsop(self._query("user", deny_names="U"))
        assert result.gpo_results[0].status == "applied"
        # NOT `filtering_reasons == ()`. `status` is "applied on at least one
        # side" (WI-032) and the reasons are unioned across both, so the
        # computer side's `security_filter_mismatch` -- correct, since the apply
        # allow names the user -- rides along. The claim under test is narrower
        # and is the one that matters: the READ DENY is not what decided this.
        assert "security_filter_read_denied" not in result.gpo_results[0].filtering_reasons
        winner = result.get_effective_value("user", "Software\\Policies\\StudioLab", "V")
        assert winner is not None
        assert winner.effective_value == "applied"

    def test_a_deny_on_a_computer_GROUP_blocks_the_user_side(self) -> None:
        """The generalisation the results doc warned against shortcutting.

        Matching read denies on `computer_name` alone would pass every test
        above and be wrong here. Group expansion through the reading principal's
        token is the same mechanism, and it is only expressible at all because
        WI-047 gave the target per-side memberships.
        """
        query = self._query("user", deny_names="LAB\\ReadDenyGroup")
        query = replace(
            query,
            target=replace(
                query.target,
                computer_group_memberships=("LAB\\ReadDenyGroup",),
            ),
        )
        assert compute_rsop(query).gpo_results[0].status == "blocked"

    def test_a_deny_on_a_USER_group_does_not_block_the_user_side(self) -> None:
        """The control for the row above, and the one that proves the sides are
        really separated rather than merged under a new name.

        Same group name, same deny, put in the USER's token instead of the
        computer's. Before WI-047 the union made these two cases identical.
        """
        query = self._query("user", deny_names="LAB\\ReadDenyGroup")
        query = replace(
            query,
            target=replace(
                query.target,
                user_group_memberships=("LAB\\ReadDenyGroup",),
            ),
        )
        assert compute_rsop(query).gpo_results[0].status == "applied"

    def test_an_apply_deny_is_still_evaluated_against_the_resolving_side(self) -> None:
        """The two rights must not have been collapsed onto one principal.

        WI-033 certified that a deny on Apply naming the USER blocks the USER
        side. If WI-047 had routed every filter through the computer, this would
        now apply -- the failure direction that promises settings which never
        arrive.
        """
        query = self._query("user")
        gpo = replace(
            query.gpos[0],
            security_filters=(
                *query.gpos[0].security_filters[:1],
                SecurityFilter(id="da", principal="U", permission="apply", deny=True),
            ),
        )
        result = compute_rsop(replace(query, gpos=(gpo,)))
        assert result.gpo_results[0].status == "blocked"
        assert "security_filter_denied" in result.gpo_results[0].filtering_reasons

    def test_a_wmi_false_still_blocks_and_stays_conclusive(self) -> None:
        """Carried over from the class this replaced.

        It was the control proving a measured block outranks the open question.
        There is no open question here any more, but the property it really
        pins -- a WMI filter evaluated FALSE blocks on its own path and does not
        make the result inconclusive -- is still worth holding.
        """
        query = self._query("computer")
        gpo = replace(
            query.gpos[0],
            security_filters=(),
            wmi_filter=WmiFilter(id="w", name="w", query="SELECT * FROM Win32_Nothing"),
        )
        result = compute_rsop(replace(query, gpos=(gpo,), wmi_filter_results=(("w", False),)))
        assert result.gpo_results[0].status == "blocked"
        assert "wmi_filter_false" in result.gpo_results[0].filtering_reasons
        assert result.is_conclusive()

    def test_no_filtering_rule_produces_unevaluable_any_more(self) -> None:
        """A fact about the module that a reader should not have to discover.

        `unevaluable` was introduced by WI-039 and is still a member of
        `RsopGpoStatus`, still partitioned by `assert_never`, still propagated
        into winners and into the diff. But with WI-043 measured, NO FILTERING
        RULE PRODUCES IT: the WMI unevaluatable case blocks (Windows fails
        closed, measured), and the read-deny case is now answered.

        The machinery stays because the next unmeasured region will need it --
        that was WI-039's ruling and it has not changed. What is gone is an
        abstention that existed only because nobody had looked. This test says
        so out loud, and it will fail the moment a new rule starts abstaining,
        which is exactly when someone should be made to justify it.

        `TestUncertaintySurvivesTheDiff` keeps the machinery itself covered by
        constructing the state directly, which is now the only way to reach it.
        """
        for side in ("computer", "user"):
            for deny in ("", "C", "U"):
                result = compute_rsop(self._query(side, deny_names=deny))  # type: ignore[arg-type]
                assert result.gpos_unevaluable() == (), (
                    f"side={side} deny={deny!r} abstained; if that is deliberate, this "
                    "test is the place to say why"
                )
                assert result.is_conclusive()


class TestTheUnmeasuredCellsArePinned:
    """The two cells WI-047 changed by reasoning rather than by measurement.

    Raised by cross-lineage review of the tranche. Both are off-diagonal --
    the deny names the principal that is NOT the one being resolved -- and both
    were flipped from BLOCKS to APPLIES when `_gpo_filter_status` stopped
    matching against the union of both principals. Neither has an estate row.

    The direction matters. Applying a GPO that Windows would withhold is the
    failure that tells an operator about settings which never arrive, which is
    the same failure direction WI-033 was opened for. The mechanism argues the
    new answers are right: the computer performs the retrieval with its own
    token, so a user-named ACE cannot gate it, and Apply is evaluated against
    the principal the policy applies to, so a computer-named Apply deny has
    nothing to say about the user side.

    These tests do not make that argument true. They pin the answer the tranche
    chose, so that if WI-049 measures the estate and Windows disagrees, the
    change lands as a visible failure here rather than as a quiet edit.

    `_query` is deliberately not reused for the read cell: its computer-scope
    query carries no user identity at all, so a user-named deny would match
    nothing and the row would pass for the wrong reason.
    """

    def _query(
        self,
        side: Literal["computer", "user"],
        *,
        filters: tuple[SecurityFilter, ...],
    ) -> RsopQuery:
        """Both principals always present, so no row here passes vacuously."""
        dn = "OU=Child,DC=x"
        gpo = GPO(
            guid="g1",
            name="OffDiagonal",
            security_filters=filters,
            settings=(
                RegistrySetting(
                    id="s",
                    side=side,
                    hive="HKCU" if side == "user" else "HKLM",
                    key="Software\\Policies\\StudioLab",
                    value_name="V",
                    registry_type="REG_SZ",
                    value="applied",
                ),
            ),
        )
        return RsopQuery(
            query_id="q",
            target=RsopTarget(
                computer_name="C",
                computer_dn=f"CN=C,{dn}",
                user_name="U",
                user_dn=f"CN=U,{dn}",
                domain="x",
            ),
            som_nodes=(
                SomNode(
                    dn=dn,
                    name="Child",
                    scope="ou",
                    parent_dn="",
                    links=(SomLink(gpo_guid="g1", scope="ou", scope_dn=dn, order=1),),
                ),
            ),
            gpos=(gpo,),
        )

    def test_a_user_named_read_deny_does_not_block_the_computer_side(self) -> None:
        """The fourth read cell: REASONED, not measured (WI-049).

        The user exists and is named by the deny, so the row is not vacuous.
        Before WI-047 the union matched it and this blocked.
        """
        result = compute_rsop(
            self._query(
                "computer",
                filters=(
                    SecurityFilter(id="a", principal="C", permission="apply"),
                    SecurityFilter(id="d", principal="U", permission="read", deny=True),
                ),
            )
        )
        assert result.gpo_results[0].status == "applied"
        assert "security_filter_read_denied" not in result.gpo_results[0].filtering_reasons

    def test_a_computer_named_apply_deny_does_not_block_the_user_side(self) -> None:
        """The off-diagonal Apply cell: REASONED, not measured (WI-049).

        The computer is always present on a user-scope query, so this row was
        matched by the pre-WI-047 union and blocked.

        The assertion is on the WINNING VALUE, not on `filtering_reasons`.
        `security_filter_denied` is legitimately in the reasons: the deny names
        the computer, so it blocks the COMPUTER side, and the reasons are
        unioned across both (WI-032). The claim under test is narrower and is
        the one that changed -- the user side still receives the setting.
        """
        result = compute_rsop(
            self._query(
                "user",
                filters=(
                    SecurityFilter(id="a", principal="U", permission="apply"),
                    SecurityFilter(id="da", principal="C", permission="apply", deny=True),
                ),
            )
        )
        assert result.gpo_results[0].status == "applied"
        winner = result.get_effective_value("user", "Software\\Policies\\StudioLab", "V")
        assert winner is not None
        assert winner.effective_value == "applied"


class TestUncertaintySurvivesTheDiff:
    """WI-043: `compare_rsop_results` must not delete uncertainty on the way out.

    A diff is what a caller compares two estates with. Two results can name the
    same winner with the same value and differ in whether an unevaluable GPO
    could have overridden it -- and the first version of this function compared
    only value and winning GPO, so that pair returned an EMPTY diff. Every other
    part of the module was careful to represent the third state; this was the
    one exported function that quietly dropped it.
    """

    def _result(self, unevaluable: tuple[str, ...]) -> RsopResult:
        return RsopResult(
            query_id="q",
            mode="planning",
            target=RsopTarget(computer_name="C", domain="x"),
            computer_settings=(
                RsopSettingResult(
                    setting_id="s",
                    side="computer",
                    hive="HKLM",
                    key="Software\\Policies\\StudioLab",
                    value_name="V",
                    effective_value="same",
                    winning_gpo_guid="g0",
                    unevaluable_gpos=unevaluable,
                ),
            ),
        )

    def test_a_change_in_certainty_alone_is_a_difference(self) -> None:
        diffs = compare_rsop_results(self._result(()), self._result(("g1",)))
        assert [d.change_type for d in diffs] == ["uncertainty_changed"]
        assert diffs[0].value_name == "V"

    def test_it_is_symmetric(self) -> None:
        """Gaining certainty is as much a change as losing it."""
        diffs = compare_rsop_results(self._result(("g1",)), self._result(()))
        assert [d.change_type for d in diffs] == ["uncertainty_changed"]

    def test_identical_results_still_diff_to_nothing(self) -> None:
        """The control: without it, a function that flagged everything would pass."""
        assert compare_rsop_results(self._result(("g1",)), self._result(("g1",))) == ()
        assert compare_rsop_results(self._result(()), self._result(())) == ()

    def test_a_value_change_still_outranks_it(self) -> None:
        """A changed value is reported as `modified`, not demoted to uncertainty."""
        current = replace(
            self._result(("g1",)),
            computer_settings=(
                replace(self._result(("g1",)).computer_settings[0], effective_value="other"),
            ),
        )
        diffs = compare_rsop_results(self._result(()), current)
        assert [d.change_type for d in diffs] == ["modified"]


# ---------------------------------------------------------------------------
# The API surface (WI-030).
#
# `rsop.py` was reachable from nothing until 2026-08-06. The three qualifiers
# that kept it that way are closed: scope by WP-9, coverage by the WI-043
# tranche's twelve passing scenarios, and the decision to surface it by Ruling 1
# of `docs/direction-2026-08-06-reconciliation-and-lab-handover.md`.
#
# These tests exercise the wire shape. What they do NOT do is certify Windows
# behaviour -- that is what the twelve verdicts under `docs/plan-033/` are for,
# and a test passing here says only that the surface carries the semantics those
# runs certified, not that the semantics are right.


def _api_client(tmp_path: Path) -> TestClient:
    app.state.store = WorkspaceStore(tmp_path / "rsop.db")
    app.state.owns_store = False
    return TestClient(app)


def _api_link(
    gpo_guid: str, scope_dn: str, *, scope: str = "ou", order: int = 1
) -> dict[str, Any]:
    return {
        "gpo_guid": gpo_guid,
        "scope": scope,
        "scope_dn": scope_dn,
        "enabled": True,
        "enforced": False,
        "order": order,
    }


def _api_node(
    dn: str,
    name: str,
    scope: str,
    *,
    parent_dn: str = "",
    links: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "dn": dn,
        "name": name,
        "scope": scope,
        "parent_dn": parent_dn,
        "block_inheritance": False,
        "links": links or [],
    }


def _api_setting(
    id_: str,
    side: str,
    value: str,
    *,
    value_name: str = "Val",
    registry_type: str = "REG_SZ",
) -> dict[str, Any]:
    return {
        "id": id_,
        "side": side,
        "hive": "HKLM" if side == "computer" else "HKCU",
        "key": "Software\\Policies\\StudioLab",
        "value_name": value_name,
        "registry_type": registry_type,
        "value": value,
    }


def _api_two_tier_payload() -> dict[str, Any]:
    """A domain link and an OU link writing the same computer-side value."""
    return {
        "query_id": "q-two-tier",
        "target": {
            "computer_name": "LABCL01",
            "computer_dn": "CN=LABCL01," + _OU_DN,
            "domain": "ad.hraedon.com",
        },
        "nodes": [
            _api_node(
                _OU_DN,
                "Servers",
                "ou",
                parent_dn=_DOMAIN_DN,
                links=[_api_link(_GPO_B, _OU_DN)],
            ),
            _api_node(
                _DOMAIN_DN,
                "ad",
                "domain",
                links=[_api_link(_GPO_A, _DOMAIN_DN, scope="domain")],
            ),
        ],
        "gpos": [
            {
                "guid": _GPO_A,
                "name": "Domain Baseline",
                "settings": [_api_setting("s-a", "computer", "domain")],
            },
            {
                "guid": _GPO_B,
                "name": "Servers Override",
                "settings": [_api_setting("s-b", "computer", "ou")],
            },
        ],
    }


def test_api_rsop_compute_resolves_lsdou_precedence(tmp_path: Path) -> None:
    with _api_client(tmp_path) as client:
        resp = client.post("/api/rsop/compute", json=_api_two_tier_payload())
    assert resp.status_code == 200
    body = resp.json()
    assert body["query_id"] == "q-two-tier"
    assert [s["effective_value"] for s in body["computer_settings"]] == ["ou"]
    winner = body["computer_settings"][0]
    assert winner["winning_gpo_guid"] == _GPO_B
    assert winner["overridden_by"] == [_GPO_A]
    assert body["user_settings"] == []
    assert body["is_conclusive"] is True


def test_api_rsop_compute_reports_every_gpo_it_walked(tmp_path: Path) -> None:
    with _api_client(tmp_path) as client:
        resp = client.post("/api/rsop/compute", json=_api_two_tier_payload())
    results = {g["gpo_guid"]: g for g in resp.json()["gpo_results"]}
    assert results[_GPO_B]["status"] == "applied"
    assert results[_GPO_B]["precedence"] == 1
    assert results[_GPO_A]["status"] == "applied"
    assert results[_GPO_A]["settings_overridden"] == 1


def test_api_rsop_compute_always_states_the_per_side_limitation(tmp_path: Path) -> None:
    """WI-032, stated in the payload rather than only in the docs.

    `gpo_results[].status` is "applied on at least one side" and looks exactly
    like a per-side answer to a caller who has not been told otherwise. The
    limitation is unconditional because it holds for every answer this surface
    will ever give, not for the ones some heuristic decides are at risk.
    """
    with _api_client(tmp_path) as client:
        resp = client.post("/api/rsop/compute", json=_api_two_tier_payload())
    limitations = resp.json()["limitations"]
    assert "gpo_status_is_not_per_side" in [item["code"] for item in limitations]
    message = next(
        item["message"]
        for item in limitations
        if item["code"] == "gpo_status_is_not_per_side"
    )
    assert "WI-032" in message
    assert "computer_settings" in message


def test_api_rsop_compute_declares_slow_link_is_never_read(tmp_path: Path) -> None:
    """WI-036. The fields are accepted and no part of the result reflects them."""
    payload = _api_two_tier_payload()
    payload["target"]["slow_link"] = True
    with _api_client(tmp_path) as client:
        resp = client.post("/api/rsop/compute", json=payload)
    codes = [item["code"] for item in resp.json()["limitations"]]
    assert "slow_link_and_safe_mode_are_not_evaluated" in codes


def test_api_rsop_compute_omits_the_slow_link_limitation_when_unasked(
    tmp_path: Path,
) -> None:
    """The control. Without it a surface that listed every limitation always
    would pass the test above while saying nothing."""
    with _api_client(tmp_path) as client:
        resp = client.post("/api/rsop/compute", json=_api_two_tier_payload())
    codes = [item["code"] for item in resp.json()["limitations"]]
    assert "slow_link_and_safe_mode_are_not_evaluated" not in codes


def test_api_rsop_compute_warns_on_an_unevaluated_wmi_filter(tmp_path: Path) -> None:
    """WI-035. An unevaluated filter still applies, and the assumption is visible."""
    payload = _api_two_tier_payload()
    payload["gpos"][1]["wmi_filter"] = {
        "id": "wmi-1",
        "name": "Workstations only",
        "query": "SELECT * FROM Win32_OperatingSystem WHERE ProductType = 1",
    }
    with _api_client(tmp_path) as client:
        resp = client.post("/api/rsop/compute", json=payload)
    body = resp.json()
    assert "wmi_filter_unknown" in body["warnings"]
    assert [s["effective_value"] for s in body["computer_settings"]] == ["ou"]


def test_api_rsop_compute_honours_a_caller_supplied_wmi_result(tmp_path: Path) -> None:
    payload = _api_two_tier_payload()
    payload["gpos"][1]["wmi_filter"] = {
        "id": "wmi-1",
        "name": "Workstations only",
        "query": "SELECT * FROM Win32_OperatingSystem WHERE ProductType = 1",
    }
    payload["wmi_filter_results"] = {"wmi-1": False}
    with _api_client(tmp_path) as client:
        resp = client.post("/api/rsop/compute", json=payload)
    body = resp.json()
    assert [s["effective_value"] for s in body["computer_settings"]] == ["domain"]
    blocked = next(g for g in body["gpo_results"] if g["gpo_guid"] == _GPO_B)
    assert blocked["status"] == "blocked"
    assert "wmi_filter_false" in blocked["filtering_reasons"]


def test_api_rsop_compute_rejects_a_query_with_no_domain(tmp_path: Path) -> None:
    """The domain layer's own validation, reaching the caller as a 422."""
    payload = _api_two_tier_payload()
    payload["target"]["domain"] = ""
    with _api_client(tmp_path) as client:
        resp = client.post("/api/rsop/compute", json=payload)
    assert resp.status_code == 422
    assert resp.json()["error"]["issues"][0]["code"] == "empty_domain"


def test_api_rsop_compute_rejects_loopback_replace_without_a_computer(
    tmp_path: Path,
) -> None:
    payload = _api_two_tier_payload()
    payload["target"]["computer_name"] = ""
    payload["target"]["computer_dn"] = ""
    payload["target"]["user_name"] = "labuser"
    payload["target"]["loopback_mode"] = "replace"
    with _api_client(tmp_path) as client:
        resp = client.post("/api/rsop/compute", json=payload)
    assert resp.status_code == 422
    codes = [issue["code"] for issue in resp.json()["error"]["issues"]]
    assert "loopback_replace_without_computer" in codes


def test_api_rsop_compute_rejects_an_unknown_loopback_mode(tmp_path: Path) -> None:
    """Request-shape validation, which speaks before the domain layer does."""
    payload = _api_two_tier_payload()
    payload["target"]["loopback_mode"] = "sometimes"
    with _api_client(tmp_path) as client:
        resp = client.post("/api/rsop/compute", json=payload)
    assert resp.status_code == 422
    assert resp.json()["error"]["message"] == "Invalid request"


def test_api_rsop_compute_rejects_a_non_canonical_dword(tmp_path: Path) -> None:
    """The modelling surface inherits the authoring surface's numeric contract.

    A value that `/api/gpos/{guid}/settings` would refuse must not become
    predictable here, or the two surfaces disagree about what a DWORD is.
    """
    payload = _api_two_tier_payload()
    payload["gpos"][0]["settings"] = [
        _api_setting("s-a", "computer", "0x1", registry_type="REG_DWORD")
    ]
    with _api_client(tmp_path) as client:
        resp = client.post("/api/rsop/compute", json=payload)
    assert resp.status_code == 422


def _api_read_deny_payload(deny_names: str) -> dict[str, Any]:
    """One user-side GPO whose apply allow names the user and whose read deny
    names whoever the caller says. WI-043's discriminator, on the wire."""
    return {
        "query_id": "q-read-deny",
        "target": {
            "computer_name": "LABCL01",
            "computer_dn": "CN=LABCL01," + _OU_DN,
            "user_name": "labuser",
            "user_dn": "CN=labuser," + _OU_DN,
            "domain": "ad.hraedon.com",
        },
        "nodes": [_api_node(_OU_DN, "Servers", "ou", links=[_api_link(_GPO_A, _OU_DN)])],
        "gpos": [
            {
                "guid": _GPO_A,
                "name": "ReadDenied",
                "settings": [_api_setting("s-a", "user", "applied")],
                "security_filters": [
                    {"id": "f-allow", "principal": "labuser", "permission": "apply"},
                    {
                        "id": "f-deny",
                        "principal": deny_names,
                        "permission": "read",
                        "deny": True,
                    },
                ],
            }
        ],
    }


def test_api_rsop_compute_user_side_blocks_on_a_computer_named_read_deny(
    tmp_path: Path,
) -> None:
    """WI-043 row B, certified by `rsop-user-observe-20260806184006-2532`.

    Denying Read to the COMPUTER withholds the USER's policy, because MS16-072
    has the computer perform the retrieval for both sides. The surface has to
    carry this: a read deny is invisible to any reader that inspects Apply, and
    the failure direction is the model promising settings that never arrive.
    """
    with _api_client(tmp_path) as client:
        resp = client.post("/api/rsop/compute", json=_api_read_deny_payload("LABCL01"))
    body = resp.json()
    assert body["user_settings"] == []
    result = body["gpo_results"][0]
    assert result["status"] == "blocked"
    assert "security_filter_read_denied" in result["filtering_reasons"]


def test_api_rsop_compute_user_side_applies_on_a_user_named_read_deny(
    tmp_path: Path,
) -> None:
    """WI-043 row A, and the discriminator for the whole rule.

    A surface that routed read denies through the resolving side would block
    here and pass the test above. Measured on a real 26200 client: the GPO
    applied and won the conflict at link order 1.
    """
    with _api_client(tmp_path) as client:
        resp = client.post("/api/rsop/compute", json=_api_read_deny_payload("labuser"))
    body = resp.json()
    assert [s["effective_value"] for s in body["user_settings"]] == ["applied"]
    result = body["gpo_results"][0]
    assert result["status"] == "applied"
    assert "security_filter_read_denied" not in result["filtering_reasons"]


def test_api_rsop_compute_carries_per_side_group_memberships(tmp_path: Path) -> None:
    """WI-047. The two membership lists are not interchangeable on the wire.

    The same group name denied Read blocks when it is in the COMPUTER's token
    and does not when it is in the USER's. A request model with one merged list
    could not express the difference, which is the defect WI-047 fixed in the
    domain type.

    **This asserts the wire carries the rule, not that the rule is right.**
    WI-049: no estate run has ever exercised a deny that matches through a group
    rather than by name, in either direction. The candidate builder always
    passes `computer_group_memberships=()`. This test would pass identically if
    the rule turned out to be wrong.
    """
    on_computer = _api_read_deny_payload("LAB\\ReadDenyGroup")
    on_computer["target"]["computer_group_memberships"] = ["LAB\\ReadDenyGroup"]
    on_user = _api_read_deny_payload("LAB\\ReadDenyGroup")
    on_user["target"]["user_group_memberships"] = ["LAB\\ReadDenyGroup"]
    with _api_client(tmp_path) as client:
        blocked = client.post("/api/rsop/compute", json=on_computer).json()
        applied = client.post("/api/rsop/compute", json=on_user).json()
    assert blocked["gpo_results"][0]["status"] == "blocked"
    assert applied["gpo_results"][0]["status"] == "applied"


def test_api_rsop_compare_reports_a_changed_winning_value(tmp_path: Path) -> None:
    baseline = _api_two_tier_payload()
    current = _api_two_tier_payload()
    current["query_id"] = "q-current"
    current["gpos"][1]["settings"] = [_api_setting("s-b", "computer", "ou-revised")]
    with _api_client(tmp_path) as client:
        resp = client.post(
            "/api/rsop/compare", json={"baseline": baseline, "current": current}
        )
    assert resp.status_code == 200
    body = resp.json()
    assert [d["change_type"] for d in body["diffs"]] == ["modified"]
    assert body["diffs"][0]["baseline_value"] == "ou"
    assert body["diffs"][0]["current_value"] == "ou-revised"
    assert body["baseline_is_conclusive"] is True
    assert body["current_is_conclusive"] is True


def test_api_rsop_compare_reports_nothing_for_identical_topologies(
    tmp_path: Path,
) -> None:
    """The control for the test above."""
    with _api_client(tmp_path) as client:
        resp = client.post(
            "/api/rsop/compare",
            json={
                "baseline": _api_two_tier_payload(),
                "current": _api_two_tier_payload(),
            },
        )
    assert resp.json()["diffs"] == []


def test_api_rsop_compare_states_the_limitations_too(tmp_path: Path) -> None:
    """A caller that only ever calls `/compare` must still be told (WI-032)."""
    with _api_client(tmp_path) as client:
        resp = client.post(
            "/api/rsop/compare",
            json={
                "baseline": _api_two_tier_payload(),
                "current": _api_two_tier_payload(),
            },
        )
    codes = [item["code"] for item in resp.json()["limitations"]]
    assert "gpo_status_is_not_per_side" in codes


def test_api_rsop_compare_rejects_an_invalid_query_on_either_side(
    tmp_path: Path,
) -> None:
    """Either query failing validation fails the comparison: a diff against a
    query that never computed would be a comparison with nothing."""
    current = _api_two_tier_payload()
    current["target"]["domain"] = ""
    with _api_client(tmp_path) as client:
        resp = client.post(
            "/api/rsop/compare",
            json={"baseline": _api_two_tier_payload(), "current": current},
        )
    assert resp.status_code == 422
    assert resp.json()["error"]["issues"][0]["code"] == "empty_domain"
