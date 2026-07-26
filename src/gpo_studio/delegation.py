"""Delegation, effective rights, and principal reconciliation for GPO Studio."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .model import PrincipalInfo
from .sddl import Ace, SecurityDescriptor, parse_sddl

CapabilityProfile = Literal[
    "read",
    "edit_settings",
    "edit_security",
    "delete",
    "link",
    "create_gpo",
    "generate_rsop",
    "generate_modeling",
]


# Microsoft extended-right GUID for Apply Group Policy. Ordinary AD access
# rights such as read property, write DACL, delete, and create child are access
# mask bits, not invented control-access GUIDs.
GPO_RIGHT_APPLY = "edacfd8f-ffb3-11d1-b41d-00a0c968f939"  # Apply Group Policy


@dataclass(frozen=True, slots=True)
class CapabilityGrant:
    profile: CapabilityProfile
    principal_sid: str
    principal_name: str = ""
    scope_dn: str = ""  # empty = domain-wide


@dataclass(frozen=True, slots=True)
class CapabilitySet:
    grants: tuple[CapabilityGrant, ...]

    def has(
        self,
        profile: CapabilityProfile,
        principal_sid: str,
        scope_dn: str = "",
    ) -> bool:
        """Check if a principal has a specific capability, optionally scoped."""
        for grant in self.grants:
            if grant.profile != profile:
                continue
            if grant.principal_sid != principal_sid:
                continue
            if scope_dn and grant.scope_dn and grant.scope_dn != scope_dn:
                continue
            return True
        return False


@dataclass(frozen=True, slots=True)
class EffectiveRights:
    principal_sid: str
    principal_name: str
    can_apply: bool
    can_read: bool
    can_write_settings: bool
    can_write_security: bool
    can_delete: bool
    can_create_child: bool
    denied_reasons: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class LockoutRisk:
    level: Literal["none", "low", "high", "critical"]
    description: str
    affected_principals: tuple[str, ...] = field(default_factory=tuple)


ReconciliationStatus = Literal[
    "exact_match",
    "sid_history_match",
    "name_only_match",
    "ambiguous",
    "not_found",
    "inaccessible",
    "deleted",
]


@dataclass(frozen=True, slots=True)
class PrincipalReconciliation:
    reference_sid: str
    reference_name: str
    observed_principal: PrincipalInfo | None
    status: ReconciliationStatus
    is_safe_to_remap: bool
    warnings: tuple[str, ...] = field(default_factory=tuple)
    affected_adapters: tuple[str, ...] = field(default_factory=tuple)


def _normalize_guid(value: str) -> str:
    """Return a lowercased GUID string without braces."""
    return value.strip("{}").casefold()


def _ace_grants_capability(
    ace: Ace,
    capability_guid: str | None,
    generic_rights: frozenset[str],
    object_rights: frozenset[str],
) -> bool:
    """Return True when an ACE grants the named capability."""
    if capability_guid is not None and ace.is_object_ace:
        if _normalize_guid(ace.object_guid) != _normalize_guid(capability_guid):
            return False
        return bool(object_rights.intersection(ace.rights))
    if capability_guid is not None:
        return bool(generic_rights.intersection(ace.rights))

    # Object-specific RP/WP/CC ACEs grant only the named property or child
    # class, not the whole coarse capability represented by EffectiveRights.
    # Standard/generic rights remain meaningful on either ACE form.
    if ace.is_object_ace and ace.object_guid:
        object_scoped = frozenset({"RP", "WP", "CC", "DC", "CR"})
        return bool(generic_rights.intersection(ace.rights) - object_scoped)
    return bool(generic_rights.intersection(ace.rights))


def _capability_allowed(
    aces: tuple[Ace, ...],
    principal_sid: str,
    group_sids: frozenset[str],
    inherited: bool,
    capability_guid: str | None,
    generic_rights: frozenset[str],
    object_rights: frozenset[str],
) -> bool:
    """Check whether a capability is allowed by any matching ACE."""
    for ace in aces:
        if ace.type not in ("ALLOWED", "OBJECT_ALLOWED"):
            continue
        if ace.is_inherited != inherited:
            continue
        if ace.trustee_sid != principal_sid and ace.trustee_sid not in group_sids:
            continue
        if _ace_grants_capability(ace, capability_guid, generic_rights, object_rights):
            return True
    return False


def _capability_denied(
    aces: tuple[Ace, ...],
    principal_sid: str,
    group_sids: frozenset[str],
    inherited: bool,
    capability_guid: str | None,
    generic_rights: frozenset[str],
    object_rights: frozenset[str],
) -> bool:
    """Check whether a capability is denied by any matching ACE."""
    for ace in aces:
        if ace.type not in ("DENIED", "OBJECT_DENIED"):
            continue
        if ace.is_inherited != inherited:
            continue
        if ace.trustee_sid != principal_sid and ace.trustee_sid not in group_sids:
            continue
        if _ace_grants_capability(ace, capability_guid, generic_rights, object_rights):
            return True
    return False


def _compute_capability(
    aces: tuple[Ace, ...],
    principal_sid: str,
    group_sids: frozenset[str],
    capability_guid: str | None,
    generic_rights: frozenset[str],
    object_rights: frozenset[str],
) -> bool:
    """Resolve one capability honoring explicit > inherited and deny > allow."""
    explicit_denied = _capability_denied(
        aces, principal_sid, group_sids, False, capability_guid, generic_rights, object_rights
    )
    explicit_allowed = _capability_allowed(
        aces, principal_sid, group_sids, False, capability_guid, generic_rights, object_rights
    )
    inherited_denied = _capability_denied(
        aces, principal_sid, group_sids, True, capability_guid, generic_rights, object_rights
    )
    inherited_allowed = _capability_allowed(
        aces, principal_sid, group_sids, True, capability_guid, generic_rights, object_rights
    )

    if explicit_denied:
        return False
    if explicit_allowed:
        return True
    if inherited_denied:
        return False
    return inherited_allowed


def compute_effective_rights(
    sd: SecurityDescriptor,
    principal_sid: str,
    group_sids: frozenset[str],
) -> EffectiveRights:
    """Compute effective rights for a principal given a security descriptor.

    Algorithm:
    1. Collect all ALLOWED ACEs where trustee is principal or in group_sids
    2. Collect all DENIED ACEs where trustee is principal or in group_sids
    3. Denied rights override allowed rights (deny wins)
    4. Inherited ACEs (is_inherited=True) are lower priority than explicit ACEs
    5. Object ACEs (is_object_ace=True) apply only to the specific object GUID
    """
    aces = sd.dacl.aces if sd.dacl is not None else ()

    apply_generic = frozenset({"GA"})
    apply_object = frozenset({"CR"})
    read_generic = frozenset({"GA", "GR", "RP"})
    write_settings_generic = frozenset({"GA", "GW", "WP"})
    write_security_generic = frozenset({"GA", "WD", "WO"})
    delete_generic = frozenset({"GA", "SD"})
    create_child_generic = frozenset({"GA", "CC"})

    denied_reasons: list[str] = []

    def check_extended(
        name: str,
        guid: str,
        generic: frozenset[str],
        object_rights: frozenset[str],
    ) -> bool:
        if _compute_capability(
            aces, principal_sid, group_sids, guid, generic, object_rights
        ):
            return True
        if _capability_denied(
            aces, principal_sid, group_sids, False, guid, generic, object_rights
        ) or _capability_denied(
            aces, principal_sid, group_sids, True, guid, generic, object_rights
        ):
            denied_reasons.append(f"{name} is denied")
        return False

    def check_access_mask(name: str, rights: frozenset[str]) -> bool:
        if _compute_capability(
            aces,
            principal_sid,
            group_sids,
            None,
            rights,
            frozenset(),
        ):
            return True
        if _capability_denied(
            aces,
            principal_sid,
            group_sids,
            False,
            None,
            rights,
            frozenset(),
        ) or _capability_denied(
            aces,
            principal_sid,
            group_sids,
            True,
            None,
            rights,
            frozenset(),
        ):
            denied_reasons.append(f"{name} is denied")
        return False

    can_apply = check_extended(
        "apply", GPO_RIGHT_APPLY, apply_generic, apply_object
    )
    can_read = check_access_mask("read", read_generic)
    can_write_settings = check_access_mask(
        "write_settings",
        write_settings_generic,
    )
    can_write_security = check_access_mask(
        "write_security",
        write_security_generic,
    )
    can_delete = check_access_mask("delete", delete_generic)
    can_create_child = check_access_mask("create_child", create_child_generic)

    return EffectiveRights(
        principal_sid=principal_sid,
        principal_name="",
        can_apply=can_apply,
        can_read=can_read,
        can_write_settings=can_write_settings,
        can_write_security=can_write_security,
        can_delete=can_delete,
        can_create_child=can_create_child,
        denied_reasons=tuple(denied_reasons),
    )


def _names_match(reference_name: str, candidate: PrincipalInfo) -> bool:
    ref = reference_name.casefold()
    if not ref:
        return False
    return (
        ref == candidate.sam_account_name.casefold()
        or ref == candidate.display_name.casefold()
        or ref == candidate.canonical_name.casefold()
        or ref == candidate.distinguished_name.casefold()
    )


def reconcile_principal(
    reference_sid: str,
    reference_name: str,
    candidate: PrincipalInfo | None,
    affected_adapters: tuple[str, ...] = (),
) -> PrincipalReconciliation:
    """Produce a reconciliation review for a principal reference.

    Rules (from Plan 023 WP-3):
    - exact_match: candidate.object_sid == reference_sid -> safe
    - sid_history_match: reference_sid in candidate.sid_history -> safe with warning
    - name_only_match: candidate matches by name but SID differs -> NOT safe, warn
    - not_found: candidate is None -> NOT safe
    - Never silently substitute a same-named object
    """
    if candidate is None:
        return PrincipalReconciliation(
            reference_sid=reference_sid,
            reference_name=reference_name,
            observed_principal=None,
            status="not_found",
            is_safe_to_remap=False,
            warnings=("No matching principal found in AD.",),
            affected_adapters=affected_adapters,
        )

    if candidate.resolution_state == "ambiguous":
        return PrincipalReconciliation(
            reference_sid=reference_sid,
            reference_name=reference_name,
            observed_principal=candidate,
            status="ambiguous",
            is_safe_to_remap=False,
            warnings=("Multiple candidates were returned for this principal.",),
            affected_adapters=affected_adapters,
        )

    if candidate.resolution_state == "deleted":
        return PrincipalReconciliation(
            reference_sid=reference_sid,
            reference_name=reference_name,
            observed_principal=candidate,
            status="deleted",
            is_safe_to_remap=False,
            warnings=("The matching principal is a deleted object (tombstone).",),
            affected_adapters=affected_adapters,
        )

    if candidate.resolution_state == "inaccessible":
        return PrincipalReconciliation(
            reference_sid=reference_sid,
            reference_name=reference_name,
            observed_principal=candidate,
            status="inaccessible",
            is_safe_to_remap=False,
            warnings=("The principal object could not be queried in AD.",),
            affected_adapters=affected_adapters,
        )

    if candidate.object_sid == reference_sid:
        return PrincipalReconciliation(
            reference_sid=reference_sid,
            reference_name=reference_name,
            observed_principal=candidate,
            status="exact_match",
            is_safe_to_remap=True,
            affected_adapters=affected_adapters,
        )

    if reference_sid in candidate.sid_history:
        return PrincipalReconciliation(
            reference_sid=reference_sid,
            reference_name=reference_name,
            observed_principal=candidate,
            status="sid_history_match",
            is_safe_to_remap=True,
            warnings=(
                "Reference SID was found in the principal's sIDHistory; "
                "verify this is the intended identity.",
            ),
            affected_adapters=affected_adapters,
        )

    if _names_match(reference_name, candidate):
        return PrincipalReconciliation(
            reference_sid=reference_sid,
            reference_name=reference_name,
            observed_principal=candidate,
            status="name_only_match",
            is_safe_to_remap=False,
            warnings=(
                "Name matches but SID differs. "
                "Silent substitution of a same-named object is not allowed.",
            ),
            affected_adapters=affected_adapters,
        )

    return PrincipalReconciliation(
        reference_sid=reference_sid,
        reference_name=reference_name,
        observed_principal=candidate,
        status="not_found",
        is_safe_to_remap=False,
        warnings=("The returned principal does not match the reference SID or name.",),
        affected_adapters=affected_adapters,
    )


def _admin_allowed_aces(
    sd: SecurityDescriptor,
    admin_sid: str,
) -> tuple[Ace, ...]:
    """Return ALLOWED/OBJECT_ALLOWED ACEs for an admin principal."""
    if sd.dacl is None:
        return ()
    return tuple(
        ace
        for ace in sd.dacl.aces
        if ace.type in ("ALLOWED", "OBJECT_ALLOWED") and ace.trustee_sid == admin_sid
    )


def _has_write_security(
    sd: SecurityDescriptor,
    admin_sid: str,
    group_sids: frozenset[str],
) -> bool:
    """Check whether a principal retains write_security on a descriptor."""
    rights = compute_effective_rights(sd, admin_sid, group_sids)
    return rights.can_write_security


def check_lockout_risk(
    sd: SecurityDescriptor,
    proposed_sd: SecurityDescriptor,
    admin_sids: frozenset[str],
) -> LockoutRisk:
    """Check if a proposed SD change could lock out administrators.

    Rules:
    - If any admin SID loses all ALLOWED ACEs -> critical
    - If any admin SID loses write_security -> high
    - If owner changes away from admin group -> low
    - Otherwise -> none
    """
    # Group SIDs used for effective-rights computation of the admin themselves.
    # Admins are evaluated only by their own SIDs to detect direct loss.
    empty_groups: frozenset[str] = frozenset()

    lost_all: list[str] = []
    lost_write_security: list[str] = []

    for admin_sid in admin_sids:
        before_allowed = _admin_allowed_aces(sd, admin_sid)
        after_allowed = _admin_allowed_aces(proposed_sd, admin_sid)
        if before_allowed and not after_allowed:
            lost_all.append(admin_sid)
            continue
        if _has_write_security(sd, admin_sid, empty_groups) and not _has_write_security(
            proposed_sd, admin_sid, empty_groups
        ):
            lost_write_security.append(admin_sid)

    if lost_all:
        return LockoutRisk(
            level="critical",
            description="One or more administrators would lose all allowed ACEs.",
            affected_principals=tuple(lost_all),
        )

    if lost_write_security:
        return LockoutRisk(
            level="high",
            description="One or more administrators would lose write_security rights.",
            affected_principals=tuple(lost_write_security),
        )

    if sd.owner_sid in admin_sids and proposed_sd.owner_sid not in admin_sids:
        return LockoutRisk(
            level="low",
            description="Owner would change away from an administrator group.",
            affected_principals=(sd.owner_sid, proposed_sd.owner_sid),
        )

    return LockoutRisk(
        level="none",
        description="No administrator lockout risk detected.",
    )


def effective_rights_from_sddl(
    sddl: str,
    principal_sid: str,
    group_sids: list[str],
) -> EffectiveRights:
    """Parse an SDDL string and compute effective rights."""
    sd = parse_sddl(sddl)
    return compute_effective_rights(sd, principal_sid, frozenset(group_sids))


__all__ = [
    "CapabilityProfile",
    "CapabilityGrant",
    "CapabilitySet",
    "EffectiveRights",
    "LockoutRisk",
    "PrincipalReconciliation",
    "ReconciliationStatus",
    "GPO_RIGHT_APPLY",
    "compute_effective_rights",
    "check_lockout_risk",
    "reconcile_principal",
    "effective_rights_from_sddl",
]
