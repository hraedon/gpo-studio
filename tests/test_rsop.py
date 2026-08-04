from __future__ import annotations

import pytest

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
    group_memberships: tuple[str, ...] = (),
    loopback_mode: str = "disabled",
) -> RsopTarget:
    return RsopTarget(
        computer_name=computer_name,
        computer_dn=computer_dn,
        user_name=user_name,
        user_dn=user_dn,
        domain=domain,
        group_memberships=group_memberships,
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
        group_memberships=("S-1-5-21-0000000000-0000000000-0000000000-5678",),
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
        group_memberships=(group_sid,),
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
        group_memberships=(group_sid.upper(),),  # uppercase SID in target
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
        group_memberships=("domain\\users",),  # different case
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
    assert gpo_result.is_applied
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
            RsopGpoResult(gpo_guid=_GPO_A, gpo_name="A", is_applied=True),
            RsopGpoResult(gpo_guid=_GPO_B, gpo_name="B", is_applied=False),
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
                group_memberships=("LabGroup",),
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
        return any(g.is_applied for g in result.gpo_results)

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

    def test_a_deny_on_read_does_not_block_apply(self) -> None:
        """The right being denied matters; this models Apply Group Policy only."""
        assert self._applied(
            (
                SecurityFilter(id="a", principal="C", permission="apply"),
                SecurityFilter(id="d", principal="C", permission="read", deny=True),
            )
        )

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
        assert not any(g.is_applied for g in result.gpo_results)
        assert "wmi_filter_false" in {r for g in result.gpo_results for r in g.filtering_reasons}

    def test_a_filter_evaluated_true_applies_without_a_warning(self) -> None:
        """A known-true filter is not a caveat; saying so keeps the warning meaningful."""
        result = self._result((("w1", True),))
        assert any(g.is_applied for g in result.gpo_results)
        assert "wmi_filter_unknown" not in result.warnings

    def test_an_unevaluated_filter_still_applies_and_still_warns(self) -> None:
        """Unknown must not become false: an invented absence is harder to notice."""
        result = self._result(())
        assert any(g.is_applied for g in result.gpo_results)
        assert "wmi_filter_unknown" in result.warnings

    def test_a_result_for_another_filter_does_not_apply_here(self) -> None:
        """Results are keyed by filter, not merged into one verdict."""
        result = self._result((("someone-else", False),))
        assert any(g.is_applied for g in result.gpo_results)
        assert "wmi_filter_unknown" in result.warnings
