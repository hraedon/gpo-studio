from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from gpo_studio.api import app
from gpo_studio.delegation import (
    CapabilityGrant,
    CapabilityProfile,
    CapabilitySet,
    check_lockout_risk,
    compute_effective_rights,
    reconcile_principal,
)
from gpo_studio.model import PrincipalInfo
from gpo_studio.sddl import (
    Ace,
    Acl,
    SecurityDescriptor,
    format_sddl,
    parse_sddl,
)

_ADMIN = "S-1-5-32-544"
_USERS = "S-1-5-32-545"
_DOMAIN_ADMINS = "S-1-5-21-1-2-3-512"
_DOMAIN_USERS = "S-1-5-21-1-2-3-513"
_GUEST = "S-1-5-21-1-2-3-501"

_GPO_RIGHT_APPLY = "edacfd8f-ffb3-11d1-b41d-00a0c968f939"


def _sd(dacl_aces: tuple[Ace, ...]) -> SecurityDescriptor:
    return SecurityDescriptor(
        owner_sid=_ADMIN,
        group_sid=_ADMIN,
        dacl=Acl(aces=dacl_aces),
        sacl=None,
    )


def _ace(
    type_: str,
    flags: tuple[str, ...],
    rights: tuple[str, ...],
    trustee: str,
    object_guid: str = "",
) -> Ace:
    return Ace(
        type=type_,  # type: ignore[arg-type]
        flags=flags,
        rights=rights,
        object_guid=object_guid,
        inherit_object_guid="",
        trustee_sid=trustee,
    )


def test_effective_rights_simple_allow() -> None:
    sd = _sd((_ace("ALLOWED", (), ("GA",), _DOMAIN_USERS),))
    rights = compute_effective_rights(sd, _DOMAIN_USERS, frozenset())
    assert rights.can_apply
    assert rights.can_read
    assert rights.can_write_settings
    assert rights.can_write_security
    assert rights.can_delete
    assert rights.can_create_child


def test_effective_rights_deny_overrides_allow() -> None:
    sd = _sd(
        (
            _ace("ALLOWED", (), ("GA",), _DOMAIN_USERS),
            _ace("DENIED", (), ("GA",), _DOMAIN_USERS),
        )
    )
    rights = compute_effective_rights(sd, _DOMAIN_USERS, frozenset())
    assert not rights.can_apply
    assert not rights.can_read
    assert not rights.can_write_settings
    assert not rights.can_write_security
    assert not rights.can_delete
    assert not rights.can_create_child


def test_effective_rights_group_membership() -> None:
    sd = _sd((_ace("ALLOWED", (), ("GA",), _DOMAIN_ADMINS),))
    rights = compute_effective_rights(
        sd, _DOMAIN_USERS, frozenset({_DOMAIN_ADMINS})
    )
    assert rights.can_apply
    assert rights.can_read


def test_effective_rights_inherited_vs_explicit() -> None:
    # Explicit allow should override inherited deny.
    sd = _sd(
        (
            _ace("OBJECT_DENIED", ("ID",), ("GA",), _DOMAIN_USERS),
            _ace("ALLOWED", (), ("GA",), _DOMAIN_USERS),
        )
    )
    rights = compute_effective_rights(sd, _DOMAIN_USERS, frozenset())
    assert rights.can_apply

    # Inherited allow should not override explicit deny.
    sd2 = _sd(
        (
            _ace("DENIED", (), ("GA",), _DOMAIN_USERS),
            _ace("OBJECT_ALLOWED", ("ID",), ("GA",), _DOMAIN_USERS),
        )
    )
    rights2 = compute_effective_rights(sd2, _DOMAIN_USERS, frozenset())
    assert not rights2.can_apply


def test_effective_rights_object_ace_only_for_guid() -> None:
    sd = _sd(
        (
            _ace(
                "OBJECT_ALLOWED",
                (),
                ("CR",),
                _DOMAIN_USERS,
                object_guid=_GPO_RIGHT_APPLY,
            ),
        )
    )
    rights = compute_effective_rights(sd, _DOMAIN_USERS, frozenset())
    assert rights.can_apply
    assert not rights.can_read

    sd_mismatch = _sd(
        (
            _ace(
                "OBJECT_ALLOWED",
                (),
                ("CR",),
                _DOMAIN_USERS,
                object_guid="4125ff7c-1c30-4a23-9738-6c2e2c7f4a0a",
            ),
        )
    )
    rights2 = compute_effective_rights(sd_mismatch, _DOMAIN_USERS, frozenset())
    assert not rights2.can_apply


def test_object_specific_property_right_is_not_coarse_full_write() -> None:
    sd = _sd(
        (
            _ace(
                "OBJECT_ALLOWED",
                (),
                ("WP",),
                _DOMAIN_USERS,
                object_guid="11111111-2222-3333-4444-555555555555",
            ),
        )
    )
    rights = compute_effective_rights(sd, _DOMAIN_USERS, frozenset())
    assert not rights.can_write_settings
    assert not rights.can_write_security


def test_non_object_access_masks_drive_ordinary_rights() -> None:
    sd = _sd(
        (
            _ace("ALLOWED", (), ("RP", "WP", "WD", "SD", "CC"), _DOMAIN_USERS),
        )
    )
    rights = compute_effective_rights(sd, _DOMAIN_USERS, frozenset())
    assert rights.can_read
    assert rights.can_write_settings
    assert rights.can_write_security
    assert rights.can_delete
    assert rights.can_create_child
    assert not rights.can_apply


def test_capability_set_has() -> None:
    grants = (
        CapabilityGrant(profile="read", principal_sid=_DOMAIN_USERS),
        CapabilityGrant(
            profile="edit_settings",
            principal_sid=_DOMAIN_ADMINS,
            scope_dn="OU=Servers,DC=studio,DC=local",
        ),
    )
    cap_set = CapabilitySet(grants=grants)
    assert cap_set.has("read", _DOMAIN_USERS)
    assert not cap_set.has("edit_settings", _DOMAIN_USERS)
    assert cap_set.has(
        "edit_settings",
        _DOMAIN_ADMINS,
        scope_dn="OU=Servers,DC=studio,DC=local",
    )
    assert not cap_set.has(
        "edit_settings",
        _DOMAIN_ADMINS,
        scope_dn="OU=Workstations,DC=studio,DC=local",
    )


def test_capability_profile_literal() -> None:
    profiles: tuple[CapabilityProfile, ...] = (
        "read",
        "edit_settings",
        "edit_security",
        "delete",
        "link",
        "create_gpo",
        "generate_rsop",
        "generate_modeling",
    )
    assert len(profiles) == 8


def test_reconcile_principal_exact_match() -> None:
    candidate = PrincipalInfo(
        object_guid="00000000-0000-0000-0000-000000000001",
        object_sid=_DOMAIN_USERS,
        object_class="group",
        sam_account_name="Domain Users",
    )
    result = reconcile_principal(_DOMAIN_USERS, "Domain Users", candidate)
    assert result.status == "exact_match"
    assert result.is_safe_to_remap
    assert not result.warnings


def test_reconcile_principal_sid_history_match() -> None:
    candidate = PrincipalInfo(
        object_guid="00000000-0000-0000-0000-000000000001",
        object_sid="S-1-5-21-1-2-3-999",
        object_class="group",
        sid_history=(_DOMAIN_USERS,),
        sam_account_name="Domain Users",
    )
    result = reconcile_principal(_DOMAIN_USERS, "Domain Users", candidate)
    assert result.status == "sid_history_match"
    assert result.is_safe_to_remap
    assert result.warnings


def test_reconcile_principal_name_only_match_is_unsafe() -> None:
    candidate = PrincipalInfo(
        object_guid="00000000-0000-0000-0000-000000000001",
        object_sid="S-1-5-21-1-2-3-999",
        object_class="group",
        sam_account_name="Domain Users",
    )
    result = reconcile_principal(_DOMAIN_USERS, "Domain Users", candidate)
    assert result.status == "name_only_match"
    assert not result.is_safe_to_remap
    assert result.warnings


def test_reconcile_principal_not_found() -> None:
    result = reconcile_principal(_DOMAIN_USERS, "Domain Users", None)
    assert result.status == "not_found"
    assert not result.is_safe_to_remap
    assert result.warnings


def test_reconcile_principal_deleted() -> None:
    candidate = PrincipalInfo(
        object_guid="00000000-0000-0000-0000-000000000001",
        object_sid=_DOMAIN_USERS,
        object_class="group",
        resolution_state="deleted",
    )
    result = reconcile_principal(_DOMAIN_USERS, "Domain Users", candidate)
    assert result.status == "deleted"
    assert not result.is_safe_to_remap


def test_check_lockout_admin_loses_all_allowed_aces() -> None:
    current = _sd((_ace("ALLOWED", (), ("GA",), _ADMIN),))
    proposed = _sd((_ace("ALLOWED", (), ("GA",), _USERS),))
    result = check_lockout_risk(current, proposed, frozenset({_ADMIN}))
    assert result.level == "critical"
    assert _ADMIN in result.affected_principals


def test_check_lockout_admin_loses_write_security() -> None:
    current = _sd((_ace("ALLOWED", (), ("GA",), _ADMIN),))
    proposed = _sd((_ace("ALLOWED", (), ("GR",), _ADMIN),))
    result = check_lockout_risk(current, proposed, frozenset({_ADMIN}))
    assert result.level == "high"
    assert _ADMIN in result.affected_principals


def test_check_lockout_owner_changes_away_from_admin() -> None:
    current = SecurityDescriptor(
        owner_sid=_ADMIN,
        group_sid=_ADMIN,
        dacl=Acl(aces=(_ace("ALLOWED", (), ("GA",), _ADMIN),)),
        sacl=None,
    )
    proposed = SecurityDescriptor(
        owner_sid=_USERS,
        group_sid=_ADMIN,
        dacl=Acl(aces=(_ace("ALLOWED", (), ("GA",), _ADMIN),)),
        sacl=None,
    )
    result = check_lockout_risk(current, proposed, frozenset({_ADMIN}))
    assert result.level == "low"


def test_check_lockout_no_change() -> None:
    current = _sd((_ace("ALLOWED", (), ("GA",), _ADMIN),))
    result = check_lockout_risk(current, current, frozenset({_ADMIN}))
    assert result.level == "none"


def test_sddl_object_ace_round_trip() -> None:
    sddl = (
        f"O:{_ADMIN}G:{_ADMIN}"
        f"D:(OA;;CR;edacfd8f-ffb3-11d1-b41d-00a0c968f939;;{_DOMAIN_USERS})"
        f"(OD;;WP;edacfd8f-ffb3-11d1-b41d-00a0c968f939;;{_GUEST})"
        f"(AU;;GA;;;{_USERS})"
        f"(AL;;FA;;;{_ADMIN})"
    )
    sd = parse_sddl(sddl)
    assert format_sddl(sd) == sddl
    assert sd.dacl is not None
    types = [ace.type for ace in sd.dacl.aces]
    assert types == ["OBJECT_ALLOWED", "OBJECT_DENIED", "AUDIT_SUCCESS", "AUDIT_FAILURE"]
    assert sd.dacl.aces[0].is_object_ace
    assert not sd.dacl.aces[2].is_object_ace


def test_ace_is_inherited_property() -> None:
    ace = _ace("ALLOWED", ("ID", "CI"), ("GA",), _ADMIN)
    assert ace.is_inherited
    assert not _ace("ALLOWED", ("CI",), ("GA",), _ADMIN).is_inherited


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_api_effective_rights(client: TestClient) -> None:
    sddl = f"O:{_ADMIN}G:{_ADMIN}D:(A;;GA;;;{_DOMAIN_USERS})"
    response = client.post(
        "/api/delegation/effective-rights",
        json={
            "sddl": sddl,
            "principal_sid": _DOMAIN_USERS,
            "group_sids": [],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["principal_sid"] == _DOMAIN_USERS
    assert body["can_apply"] is True
    assert body["can_read"] is True


def test_api_effective_rights_invalid_sddl(client: TestClient) -> None:
    response = client.post(
        "/api/delegation/effective-rights",
        json={"sddl": "not valid", "principal_sid": _DOMAIN_USERS},
    )
    assert response.status_code == 422


def test_api_reconcile_principal_exact_match(client: TestClient) -> None:
    response = client.post(
        "/api/delegation/reconcile-principal",
        json={
            "reference_sid": _DOMAIN_USERS,
            "reference_name": "Domain Users",
            "candidate": {
                "object_guid": "00000000-0000-0000-0000-000000000001",
                "object_sid": _DOMAIN_USERS,
                "object_class": "group",
                "sam_account_name": "Domain Users",
            },
            "affected_adapters": ["security_filter", "delegation"],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "exact_match"
    assert body["is_safe_to_remap"] is True
    assert body["affected_adapters"] == ["security_filter", "delegation"]


def test_api_reconcile_principal_name_only_match(client: TestClient) -> None:
    response = client.post(
        "/api/delegation/reconcile-principal",
        json={
            "reference_sid": _DOMAIN_USERS,
            "reference_name": "Domain Users",
            "candidate": {
                "object_guid": "00000000-0000-0000-0000-000000000001",
                "object_sid": "S-1-5-21-1-2-3-999",
                "object_class": "group",
                "sam_account_name": "Domain Users",
            },
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "name_only_match"
    assert body["is_safe_to_remap"] is False


def test_api_check_lockout(client: TestClient) -> None:
    current = f"O:{_ADMIN}G:{_ADMIN}D:(A;;GA;;;{_ADMIN})"
    proposed = f"O:{_ADMIN}G:{_ADMIN}D:(A;;GR;;;{_ADMIN})"
    response = client.post(
        "/api/delegation/check-lockout",
        json={
            "current_sddl": current,
            "proposed_sddl": proposed,
            "admin_sids": [_ADMIN],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["level"] == "high"
    assert _ADMIN in body["affected_principals"]


def test_api_check_lockout_invalid_sddl(client: TestClient) -> None:
    response = client.post(
        "/api/delegation/check-lockout",
        json={
            "current_sddl": "bad",
            "proposed_sddl": f"O:{_ADMIN}G:{_ADMIN}D:(A;;GA;;;{_ADMIN})",
            "admin_sids": [_ADMIN],
        },
    )
    assert response.status_code == 422
