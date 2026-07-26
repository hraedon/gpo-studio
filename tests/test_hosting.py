from __future__ import annotations

import typing

from gpo_studio.hosting import (
    ROLE_PERMISSIONS,
    AuditEvent,
    AuditQuery,
    AuthenticatedIdentity,
    AuthorizationDecision,
    DeploymentConfig,
    HostedConfig,
    Operation,
    RoleGrant,
    SessionConfig,
    can_self_approve,
    check_authorization,
    filter_audit_events,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _valid_hosted(**overrides: object) -> HostedConfig:
    base: dict[str, object] = {
        "bind_address": "127.0.0.1",
        "bind_port": 8443,
        "trusted_proxy_addresses": ("10.0.0.5",),
        "public_hostname": "gpo.studio.lab",
        "tls_certificate_path": "/etc/gpo-studio/tls.crt",
        "tls_key_path": "/etc/gpo-studio/tls.key",
        "auth_provider": "windows",
        "admin_group": "GPO-Admins",
        "database_url": "postgresql://gpo:gpo@db.local/gpo",
        "session_secret_path": "/etc/gpo-studio/session.key",
        "session_max_age_minutes": 60,
        "rate_limit_per_minute": 60,
    }
    base.update(overrides)
    return HostedConfig(**base)  # type: ignore[arg-type]


def _windows_identity(**overrides: object) -> AuthenticatedIdentity:
    base: dict[str, object] = {
        "subject": "S-1-5-21-1-2-3-1001",
        "display_name": "alice",
        "provider": "windows",
        "session_id": "sess-1",
        "authenticated_at": "2026-07-25T10:00:00+00:00",
    }
    base.update(overrides)
    return AuthenticatedIdentity(**base)  # type: ignore[arg-type]


def _errors(issues: tuple[object, ...]) -> list[str]:
    """Extract the codes of error-severity ValidationIssues."""
    return [
        i.code  # type: ignore[attr-defined]
        for i in issues
        if getattr(i, "severity", "") == "error"
    ]


def _warnings(issues: tuple[object, ...]) -> list[str]:
    return [
        i.code  # type: ignore[attr-defined]
        for i in issues
        if getattr(i, "severity", "") == "warning"
    ]


# ---------------------------------------------------------------------------
# HostedConfig
# ---------------------------------------------------------------------------


def test_hosted_config_valid() -> None:
    cfg = _valid_hosted()
    issues = cfg.validate()
    assert not _errors(issues)


def test_hosted_config_non_loopback_bind_error() -> None:
    cfg = _valid_hosted(bind_address="0.0.0.0")
    assert "non_loopback_bind" in _errors(cfg.validate())


def test_hosted_config_loopback_ipv6_ok() -> None:
    cfg = _valid_hosted(bind_address="::1")
    assert "non_loopback_bind" not in _errors(cfg.validate())


def test_hosted_config_empty_public_hostname_error() -> None:
    cfg = _valid_hosted(public_hostname="")
    assert "empty_public_hostname" in _errors(cfg.validate())


def test_hosted_config_empty_tls_certificate_error() -> None:
    cfg = _valid_hosted(tls_certificate_path="")
    assert "empty_tls_certificate_path" in _errors(cfg.validate())


def test_hosted_config_empty_tls_key_error() -> None:
    cfg = _valid_hosted(tls_key_path="")
    assert "empty_tls_key_path" in _errors(cfg.validate())


def test_hosted_config_oidc_without_issuer_error() -> None:
    cfg = _valid_hosted(auth_provider="oidc", oidc_issuer="")
    assert "empty_oidc_issuer" in _errors(cfg.validate())


def test_hosted_config_oidc_with_issuer_ok() -> None:
    cfg = _valid_hosted(
        auth_provider="oidc",
        oidc_issuer="https://idp.lab/",
        oidc_client_id="gpo",
        oidc_audience="gpo",
    )
    assert "empty_oidc_issuer" not in _errors(cfg.validate())


def test_hosted_config_windows_provider_no_oidc_check() -> None:
    cfg = _valid_hosted(auth_provider="windows", oidc_issuer="")
    assert "empty_oidc_issuer" not in _errors(cfg.validate())


def test_hosted_config_empty_admin_group_error() -> None:
    cfg = _valid_hosted(admin_group="")
    assert "empty_admin_group" in _errors(cfg.validate())


def test_hosted_config_empty_database_url_error() -> None:
    cfg = _valid_hosted(database_url="")
    assert "empty_database_url" in _errors(cfg.validate())


def test_hosted_config_sqlite_database_error() -> None:
    cfg = _valid_hosted(database_url="sqlite:///gpo.db")
    errors = _errors(cfg.validate())
    assert "sqlite_not_allowed" in errors
    assert "empty_database_url" not in errors


def test_hosted_config_sqlite_prefix_case_insensitive() -> None:
    cfg = _valid_hosted(database_url="SQLITE:///gpo.db")
    assert "sqlite_not_allowed" in _errors(cfg.validate())


def test_hosted_config_postgres_url_ok() -> None:
    cfg = _valid_hosted(database_url="postgresql://host/db")
    assert "sqlite_not_allowed" not in _errors(cfg.validate())


def test_hosted_config_empty_session_secret_path_error() -> None:
    cfg = _valid_hosted(session_secret_path="")
    assert "empty_session_secret_path" in _errors(cfg.validate())


def test_hosted_config_empty_trusted_proxy_addresses_error() -> None:
    cfg = _valid_hosted(trusted_proxy_addresses=())
    assert "empty_trusted_proxy_addresses" in _errors(cfg.validate())


def test_hosted_config_session_max_age_too_short_error() -> None:
    cfg = _valid_hosted(session_max_age_minutes=4)
    assert "session_max_age_too_short" in _errors(cfg.validate())


def test_hosted_config_session_max_age_boundary_ok() -> None:
    cfg = _valid_hosted(session_max_age_minutes=5)
    assert "session_max_age_too_short" not in _errors(cfg.validate())


def test_hosted_config_rate_limit_too_low_error() -> None:
    cfg = _valid_hosted(rate_limit_per_minute=0)
    assert "rate_limit_too_low" in _errors(cfg.validate())


def test_hosted_config_rate_limit_boundary_ok() -> None:
    cfg = _valid_hosted(rate_limit_per_minute=1)
    assert "rate_limit_too_low" not in _errors(cfg.validate())


def test_hosted_config_all_errors_collected() -> None:
    """Multiple missing fields surface as multiple distinct errors."""
    cfg = HostedConfig(
        bind_address="0.0.0.0",
        public_hostname="",
        tls_certificate_path="",
        tls_key_path="",
        admin_group="",
        database_url="",
        session_secret_path="",
        trusted_proxy_addresses=(),
        session_max_age_minutes=1,
        rate_limit_per_minute=0,
    )
    errors = _errors(cfg.validate())
    # Every fail-closed rule should fire at least once.
    assert "non_loopback_bind" in errors
    assert "empty_public_hostname" in errors
    assert "empty_tls_certificate_path" in errors
    assert "empty_tls_key_path" in errors
    assert "empty_admin_group" in errors
    assert "empty_database_url" in errors
    assert "empty_session_secret_path" in errors
    assert "empty_trusted_proxy_addresses" in errors
    assert "session_max_age_too_short" in errors
    assert "rate_limit_too_low" in errors


# ---------------------------------------------------------------------------
# DeploymentConfig
# ---------------------------------------------------------------------------


def test_deployment_local_profile_ok() -> None:
    cfg = DeploymentConfig(profile="local", hosted=None)
    issues = cfg.validate()
    assert not _errors(issues)
    assert not _warnings(issues)


def test_deployment_local_with_hosted_warns() -> None:
    cfg = DeploymentConfig(profile="local", hosted=_valid_hosted())
    issues = cfg.validate()
    assert not _errors(issues)
    assert "hosted_ignored" in _warnings(issues)


def test_deployment_hosted_without_config_error() -> None:
    cfg = DeploymentConfig(profile="hosted", hosted=None)
    issues = cfg.validate()
    assert "hosted_config_required" in _errors(issues)


def test_deployment_hosted_with_valid_config_ok() -> None:
    cfg = DeploymentConfig(profile="hosted", hosted=_valid_hosted())
    issues = cfg.validate()
    assert not _errors(issues)


def test_deployment_hosted_propagates_hosted_errors() -> None:
    bad_hosted = _valid_hosted(database_url="sqlite:///x.db")
    cfg = DeploymentConfig(profile="hosted", hosted=bad_hosted)
    issues = cfg.validate()
    assert "sqlite_not_allowed" in _errors(issues)


def test_deployment_is_hosted() -> None:
    assert DeploymentConfig(profile="hosted", hosted=_valid_hosted()).is_hosted()
    assert not DeploymentConfig(profile="local").is_hosted()


# ---------------------------------------------------------------------------
# AuthenticatedIdentity
# ---------------------------------------------------------------------------


def test_identity_valid_local() -> None:
    identity = _windows_identity()
    issues = identity.validate()
    assert not _errors(issues)


def test_identity_valid_local_dev_in_local_mode() -> None:
    identity = AuthenticatedIdentity(
        subject="local",
        provider="local_dev",
        session_id="s1",
    )
    issues = identity.validate()
    assert not _errors(issues)


def test_identity_empty_subject_error() -> None:
    identity = _windows_identity(subject="")
    assert "empty_subject" in _errors(identity.validate())


def test_identity_empty_session_id_error() -> None:
    identity = _windows_identity(session_id="")
    assert "empty_session_id" in _errors(identity.validate())


def test_identity_local_dev_in_hosted_error() -> None:
    identity = AuthenticatedIdentity(
        subject="local",
        provider="local_dev",
        session_id="s1",
    )
    issues = identity.validate(hosted=True)
    assert "local_dev_in_hosted" in _errors(issues)


def test_identity_windows_in_hosted_ok() -> None:
    identity = _windows_identity()
    issues = identity.validate(hosted=True)
    assert not _errors(issues)


def test_identity_oidc_in_hosted_ok() -> None:
    identity = AuthenticatedIdentity(
        subject="oidc|alice",
        provider="oidc",
        session_id="s1",
    )
    issues = identity.validate(hosted=True)
    assert not _errors(issues)


# ---------------------------------------------------------------------------
# SessionConfig
# ---------------------------------------------------------------------------


def test_session_config_valid() -> None:
    cfg = SessionConfig()
    issues = cfg.validate()
    assert not _errors(issues)


def test_session_config_max_age_too_short_error() -> None:
    cfg = SessionConfig(max_age_minutes=4)
    assert "max_age_too_short" in _errors(cfg.validate())


def test_session_config_idle_exceeds_max_age_error() -> None:
    cfg = SessionConfig(max_age_minutes=30, idle_timeout_minutes=60)
    assert "idle_exceeds_max_age" in _errors(cfg.validate())


def test_session_config_idle_equal_max_age_ok() -> None:
    cfg = SessionConfig(max_age_minutes=30, idle_timeout_minutes=30)
    assert "idle_exceeds_max_age" not in _errors(cfg.validate())


def test_session_config_same_site_none_without_secure_error() -> None:
    cfg = SessionConfig(same_site="none", secure_cookie=False)
    errors = _errors(cfg.validate())
    assert "same_site_none_without_secure" in errors
    # http_only=False is also triggered by secure_cookie=False? No, separate.
    # But http_only is True by default so only the same_site rule fires.


def test_session_config_same_site_none_with_secure_ok() -> None:
    cfg = SessionConfig(same_site="none", secure_cookie=True)
    assert "same_site_none_without_secure" not in _errors(cfg.validate())


def test_session_config_http_only_false_error() -> None:
    cfg = SessionConfig(http_only=False)
    assert "http_only_disabled" in _errors(cfg.validate())


def test_session_config_same_site_strict_ok() -> None:
    cfg = SessionConfig(same_site="strict", secure_cookie=True)
    assert not _errors(cfg.validate())


def test_session_config_same_site_lax_ok() -> None:
    cfg = SessionConfig(same_site="lax", secure_cookie=False)
    # lax doesn't require secure_cookie.
    assert "same_site_none_without_secure" not in _errors(cfg.validate())


# ---------------------------------------------------------------------------
# ROLE_PERMISSIONS
# ---------------------------------------------------------------------------


def test_role_permissions_reader_can_read_not_edit() -> None:
    perms = ROLE_PERMISSIONS["reader"]
    assert "read_gpo" in perms
    assert "read_audit" in perms
    assert "edit_gpo" not in perms
    assert "delete_gpo" not in perms
    assert "approve_gpo" not in perms


def test_role_permissions_author_can_edit_not_approve() -> None:
    perms = ROLE_PERMISSIONS["author"]
    assert "edit_gpo" in perms
    assert "create_gpo" in perms
    assert "approve_gpo" not in perms
    assert "delete_gpo" not in perms
    assert "review_gpo" not in perms


def test_role_permissions_reviewer_can_review_not_approve() -> None:
    perms = ROLE_PERMISSIONS["reviewer"]
    assert "review_gpo" in perms
    assert "approve_gpo" not in perms
    assert "edit_gpo" not in perms


def test_role_permissions_approver_can_approve_not_edit() -> None:
    perms = ROLE_PERMISSIONS["approver"]
    assert "approve_gpo" in perms
    assert "edit_gpo" not in perms


def test_role_permissions_exporter_downloads() -> None:
    perms = ROLE_PERMISSIONS["exporter"]
    assert "export_gpo" in perms
    assert "download_bundle" in perms
    assert "download_ps_plan" in perms
    assert "approve_gpo" not in perms
    assert "delete_gpo" not in perms


def test_role_permissions_auditor_exports_audit_not_gpo() -> None:
    perms = ROLE_PERMISSIONS["auditor"]
    assert "export_audit" in perms
    assert "read_audit" in perms
    assert "export_gpo" not in perms
    assert "edit_gpo" not in perms


def test_role_permissions_platform_admin_has_everything() -> None:
    all_ops = set(typing.get_args(Operation))
    assert ROLE_PERMISSIONS["platform_admin"] >= frozenset(all_ops)


def test_role_permissions_every_role_can_read_gpo() -> None:
    for role, perms in ROLE_PERMISSIONS.items():
        assert "read_gpo" in perms, f"{role} should be able to read_gpo"


# ---------------------------------------------------------------------------
# RoleGrant.validate
# ---------------------------------------------------------------------------


def test_role_grant_valid() -> None:
    grant = RoleGrant(
        role="author",
        principal_subject="S-1",
        granted_by="admin",
    )
    assert not _errors(grant.validate())


def test_role_grant_empty_principal_subject_error() -> None:
    grant = RoleGrant(role="author", principal_subject="", granted_by="admin")
    assert "empty_principal_subject" in _errors(grant.validate())


def test_role_grant_empty_granted_by_warning() -> None:
    grant = RoleGrant(role="author", principal_subject="S-1", granted_by="")
    issues = grant.validate()
    assert not _errors(issues)
    assert "empty_granted_by" in _warnings(issues)


# ---------------------------------------------------------------------------
# check_authorization
# ---------------------------------------------------------------------------


def test_check_authorization_allowed_with_grant() -> None:
    identity = _windows_identity()
    grants = (
        RoleGrant(
            role="author",
            principal_subject=identity.subject,
            granted_by="admin",
        ),
    )
    decision = check_authorization(identity, "edit_gpo", grants)
    assert decision.allowed
    assert decision.role == "author"
    assert decision.principal_subject == identity.subject
    assert decision.operation == "edit_gpo"


def test_check_authorization_denied_without_grant() -> None:
    identity = _windows_identity()
    grants: tuple[RoleGrant, ...] = ()
    decision = check_authorization(identity, "edit_gpo", grants)
    assert not decision.allowed
    assert decision.role is None
    assert decision.principal_subject == identity.subject


def test_check_authorization_denied_wrong_subject() -> None:
    identity = _windows_identity()
    grants = (
        RoleGrant(
            role="author",
            principal_subject="someone-else",
            granted_by="admin",
        ),
    )
    decision = check_authorization(identity, "edit_gpo", grants)
    assert not decision.allowed


def test_check_authorization_denied_operation_outside_role() -> None:
    identity = _windows_identity()
    grants = (
        RoleGrant(
            role="reader",
            principal_subject=identity.subject,
            granted_by="admin",
        ),
    )
    # reader cannot edit_gpo.
    decision = check_authorization(identity, "edit_gpo", grants)
    assert not decision.allowed


def test_check_authorization_platform_admin_bypass() -> None:
    identity = _windows_identity()
    grants = (
        RoleGrant(
            role="platform_admin",
            principal_subject=identity.subject,
            granted_by="bootstrap",
        ),
    )
    # platform_admin transitively grants every operation.
    for op in ("delete_gpo", "manage_platform", "approve_gpo", "import_gpo"):
        decision = check_authorization(identity, op, grants)
        assert decision.allowed, f"platform_admin should authorize {op}"
        assert decision.role == "platform_admin"


def test_check_authorization_scope_filtering_workspace_grant() -> None:
    """A workspace-wide grant (scope='') applies at any scope."""
    identity = _windows_identity()
    grants = (
        RoleGrant(
            role="author",
            principal_subject=identity.subject,
            scope="",
            granted_by="admin",
        ),
    )
    decision = check_authorization(identity, "edit_gpo", grants, scope="OU=Finance")
    assert decision.allowed


def test_check_authorization_scope_filtering_scoped_grant_matches() -> None:
    identity = _windows_identity()
    grants = (
        RoleGrant(
            role="author",
            principal_subject=identity.subject,
            scope="OU=Finance",
            granted_by="admin",
        ),
    )
    decision = check_authorization(identity, "edit_gpo", grants, scope="OU=Finance")
    assert decision.allowed


def test_check_authorization_scope_filtering_scoped_grant_mismatch() -> None:
    identity = _windows_identity()
    grants = (
        RoleGrant(
            role="author",
            principal_subject=identity.subject,
            scope="OU=Finance",
            granted_by="admin",
        ),
    )
    decision = check_authorization(identity, "edit_gpo", grants, scope="OU=Marketing")
    assert not decision.allowed


def test_check_authorization_scope_filtering_workspace_query_excludes_scoped_grant() -> None:
    """At workspace scope (scope=''), scoped grants do NOT apply."""
    identity = _windows_identity()
    grants = (
        RoleGrant(
            role="author",
            principal_subject=identity.subject,
            scope="OU=Finance",
            granted_by="admin",
        ),
    )
    decision = check_authorization(identity, "edit_gpo", grants, scope="")
    assert not decision.allowed


def test_check_authorization_multiple_grants_first_match_wins() -> None:
    identity = _windows_identity()
    grants = (
        RoleGrant(
            role="reader",
            principal_subject=identity.subject,
            granted_by="admin",
        ),
        RoleGrant(
            role="author",
            principal_subject=identity.subject,
            granted_by="admin",
        ),
    )
    decision = check_authorization(identity, "edit_gpo", grants)
    assert decision.allowed
    # The first matching grant that authorizes the operation wins.
    assert decision.role == "author"


def test_check_authorization_decision_defaults() -> None:
    decision = AuthorizationDecision(
        allowed=False,
        operation="read_gpo",
        principal_subject="S-1",
    )
    assert decision.role is None
    assert decision.reason == ""


# ---------------------------------------------------------------------------
# can_self_approve
# ---------------------------------------------------------------------------


def test_can_self_approve_same_subject_false() -> None:
    identity = _windows_identity(subject="S-1")
    assert not can_self_approve(identity, "S-1")


def test_can_self_approve_different_subject_true() -> None:
    identity = _windows_identity(subject="S-1")
    assert can_self_approve(identity, "S-2")


def test_can_self_approve_empty_author_subject_true() -> None:
    # An empty author subject is not the identity's subject.
    identity = _windows_identity(subject="S-1")
    assert can_self_approve(identity, "")


# ---------------------------------------------------------------------------
# AuditEvent
# ---------------------------------------------------------------------------


def test_audit_event_valid() -> None:
    event = AuditEvent(
        event_id="evt-1",
        action="create",
        actor_subject="S-1",
        timestamp="2026-07-25T10:00:00+00:00",
    )
    assert not _errors(event.validate())


def test_audit_event_empty_event_id_error() -> None:
    event = AuditEvent(
        event_id="",
        action="create",
        actor_subject="S-1",
        timestamp="2026-07-25T10:00:00+00:00",
    )
    assert "empty_event_id" in _errors(event.validate())


def test_audit_event_empty_actor_subject_error() -> None:
    event = AuditEvent(
        event_id="evt-1",
        action="create",
        actor_subject="",
        timestamp="2026-07-25T10:00:00+00:00",
    )
    assert "empty_actor_subject" in _errors(event.validate())


def test_audit_event_empty_timestamp_error() -> None:
    event = AuditEvent(
        event_id="evt-1",
        action="create",
        actor_subject="S-1",
        timestamp="",
    )
    assert "empty_timestamp" in _errors(event.validate())


def test_audit_event_break_glass_without_detail_error() -> None:
    event = AuditEvent(
        event_id="evt-1",
        action="break_glass_access",
        actor_subject="S-1",
        timestamp="2026-07-25T10:00:00+00:00",
        detail="",
    )
    assert "break_glass_without_detail" in _errors(event.validate())


def test_audit_event_break_glass_with_detail_ok() -> None:
    event = AuditEvent(
        event_id="evt-1",
        action="break_glass_access",
        actor_subject="S-1",
        timestamp="2026-07-25T10:00:00+00:00",
        detail="emergency rotation of expired admin cert",
    )
    assert "break_glass_without_detail" not in _errors(event.validate())


def test_audit_event_denied_without_reason_warning() -> None:
    event = AuditEvent(
        event_id="evt-1",
        action="create",
        actor_subject="S-1",
        timestamp="2026-07-25T10:00:00+00:00",
        outcome="denied",
        reason="",
    )
    issues = event.validate()
    assert not _errors(issues)
    assert "denied_without_reason" in _warnings(issues)


def test_audit_event_denied_with_reason_no_warning() -> None:
    event = AuditEvent(
        event_id="evt-1",
        action="create",
        actor_subject="S-1",
        timestamp="2026-07-25T10:00:00+00:00",
        outcome="denied",
        reason="insufficient role",
    )
    assert "denied_without_reason" not in _warnings(event.validate())


def test_audit_event_failure_outcome_no_warning() -> None:
    event = AuditEvent(
        event_id="evt-1",
        action="create",
        actor_subject="S-1",
        timestamp="2026-07-25T10:00:00+00:00",
        outcome="failure",
        reason="",
    )
    # Only 'denied' triggers the missing-reason warning.
    assert "denied_without_reason" not in _warnings(event.validate())


# ---------------------------------------------------------------------------
# filter_audit_events
# ---------------------------------------------------------------------------


def _evt(
    event_id: str,
    *,
    actor: str = "S-1",
    action: str = "create",
    target_type: str = "gpo",
    target_id: str = "g-1",
    outcome: str = "success",
    timestamp: str = "2026-07-25T10:00:00+00:00",
) -> AuditEvent:
    return AuditEvent(
        event_id=event_id,
        action=action,  # type: ignore[arg-type]
        actor_subject=actor,
        target_type=target_type,
        target_id=target_id,
        outcome=outcome,  # type: ignore[arg-type]
        timestamp=timestamp,
    )


_EVENTS: tuple[AuditEvent, ...] = (
    _evt("e1", actor="S-1", timestamp="2026-07-25T10:00:00+00:00"),
    _evt("e2", actor="S-2", timestamp="2026-07-25T11:00:00+00:00"),
    _evt("e3", actor="S-1", action="update", timestamp="2026-07-25T09:00:00+00:00"),
    _evt("e4", actor="S-1", action="delete", timestamp="2026-07-26T08:00:00+00:00"),
    _evt(
        "e5",
        actor="S-3",
        action="delete",
        outcome="denied",
        timestamp="2026-07-24T08:00:00+00:00",
    ),
)


def test_filter_audit_events_by_actor() -> None:
    result = filter_audit_events(_EVENTS, AuditQuery(actor_subject="S-1"))
    ids = tuple(e.event_id for e in result)
    assert ids == ("e4", "e1", "e3")  # most recent first


def test_filter_audit_events_by_action() -> None:
    result = filter_audit_events(
        _EVENTS, AuditQuery(action="create")
    )
    ids = tuple(e.event_id for e in result)
    # Only create events: e1, e2 (e2 has actor S-2 but still 'create').
    assert set(ids) == {"e1", "e2"}
    # Most recent first.
    assert ids[0] == "e2"


def test_filter_audit_events_by_outcome() -> None:
    result = filter_audit_events(_EVENTS, AuditQuery(outcome="denied"))
    ids = tuple(e.event_id for e in result)
    assert ids == ("e5",)


def test_filter_audit_events_by_target_type() -> None:
    custom = (
        _evt("a", target_type="gpo"),
        _evt("b", target_type="workspace", timestamp="2026-07-26T00:00:00+00:00"),
    )
    result = filter_audit_events(custom, AuditQuery(target_type="workspace"))
    assert tuple(e.event_id for e in result) == ("b",)


def test_filter_audit_events_by_target_id() -> None:
    custom = (
        _evt("a", target_id="g-1"),
        _evt("b", target_id="g-2", timestamp="2026-07-26T00:00:00+00:00"),
    )
    result = filter_audit_events(custom, AuditQuery(target_id="g-2"))
    assert tuple(e.event_id for e in result) == ("b",)


def test_filter_audit_events_by_time_range() -> None:
    result = filter_audit_events(
        _EVENTS,
        AuditQuery(
            since="2026-07-25T00:00:00+00:00",
            until="2026-07-25T23:59:59+00:00",
        ),
    )
    ids = tuple(e.event_id for e in result)
    # Events on 2026-07-25: e1 (10:00), e2 (11:00), e3 (09:00).
    assert set(ids) == {"e1", "e2", "e3"}
    assert ids == ("e2", "e1", "e3")  # most recent first


def test_filter_audit_events_since_only() -> None:
    result = filter_audit_events(
        _EVENTS,
        AuditQuery(since="2026-07-26T00:00:00+00:00"),
    )
    ids = tuple(e.event_id for e in result)
    assert ids == ("e4",)


def test_filter_audit_events_until_only() -> None:
    result = filter_audit_events(
        _EVENTS,
        AuditQuery(until="2026-07-24T23:59:59+00:00"),
    )
    ids = tuple(e.event_id for e in result)
    assert ids == ("e5",)


def test_filter_audit_events_limit() -> None:
    result = filter_audit_events(_EVENTS, AuditQuery(limit=2))
    assert len(result) == 2
    # Most recent first: e4 (07-26 08:00), e2 (07-25 11:00).
    assert result[0].event_id == "e4"
    assert result[1].event_id == "e2"


def test_filter_audit_events_empty_query_returns_all() -> None:
    result = filter_audit_events(_EVENTS, AuditQuery())
    assert len(result) == len(_EVENTS)
    # Most recent first.
    assert result[0].event_id == "e4"
    assert result[-1].event_id == "e5"


def test_filter_audit_events_most_recent_first() -> None:
    result = filter_audit_events(_EVENTS, AuditQuery())
    timestamps = [e.timestamp for e in result]
    assert timestamps == sorted(timestamps, reverse=True)


def test_filter_audit_events_combined_criteria() -> None:
    result = filter_audit_events(
        _EVENTS,
        AuditQuery(actor_subject="S-1", action="create"),
    )
    ids = tuple(e.event_id for e in result)
    assert ids == ("e1",)


def test_filter_audit_events_no_matches() -> None:
    result = filter_audit_events(_EVENTS, AuditQuery(actor_subject="nobody"))
    assert result == ()
