"""Hosted deployment profile domain model for GPO Studio (Plan 032).

This module models the *hosted control plane* layer that turns the
loopback-only single-operator workbench into an authenticated, multi-user
deployment. The web process still never writes directly to AD or SYSVOL; the
hosted profile adds identity, authorization, sessions, and audit on top of
the existing local workspace.

The module is intentionally side-effect free: validation, authorization
decisions, and audit filtering are pure functions over immutable dataclasses.
Operational concerns (IIS, PostgreSQL, TLS termination) are adapters outside
this module.

Design rules (from Plan 032):

- Hosted mode never starts anonymously or with only the
  ``GPO_STUDIO_UNSAFE_BIND`` acknowledgement. Missing TLS, trusted-proxy,
  authentication, authorization, or production-database configuration is a
  startup failure.
- Request-supplied ``actor`` values are never authoritative in hosted mode.
  Identity is derived server-side from the authenticated session.
- ``local_dev`` is ONLY for the local profile — never for hosted.
- Authorization is deny-by-default; ``platform_admin`` is the only role that
  transitively grants every operation.
- Authors cannot satisfy their own required review (no self-approval).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, assert_never

from .model import ValidationIssue

# ---------------------------------------------------------------------------
# Profile and provider vocabulary
# ---------------------------------------------------------------------------

DeploymentProfile = Literal["local", "hosted"]

AuthProvider = Literal["windows", "oidc", "local_dev"]
# ``local_dev`` is ONLY for the local profile — never for hosted.


# ---------------------------------------------------------------------------
# Deployment configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HostedConfig:
    """Configuration required for hosted mode. All fields are mandatory.

    Validation is fail-closed: every rule below is an error. A hosted profile
    that fails ``validate()`` must refuse to start.
    """

    bind_address: str = "127.0.0.1"  # must be loopback
    bind_port: int = 8080
    trusted_proxy_addresses: tuple[str, ...] = ()  # allowed proxy IPs
    public_hostname: str = ""  # allow-listed public hostname
    tls_certificate_path: str = ""
    tls_key_path: str = ""
    auth_provider: Literal["windows", "oidc"] = "windows"
    oidc_issuer: str = ""  # required if auth_provider=oidc
    oidc_client_id: str = ""
    oidc_audience: str = ""
    admin_group: str = ""  # Windows group or OIDC group for platform-admin
    database_url: str = ""  # PostgreSQL connection string
    session_secret_path: str = ""  # path to session signing key
    session_max_age_minutes: int = 60
    session_idle_timeout_minutes: int = 30
    csrf_enabled: bool = True
    hsts_enabled: bool = True
    rate_limit_per_minute: int = 60

    def validate(self) -> tuple[ValidationIssue, ...]:
        """Fail-closed validation. Every check below is an error."""
        issues: list[ValidationIssue] = []
        if self.bind_address not in ("127.0.0.1", "::1"):
            issues.append(
                ValidationIssue(
                    "error",
                    "non_loopback_bind",
                    "bind_address must be loopback (127.0.0.1 or ::1).",
                    "bind_address",
                )
            )
        if not self.public_hostname.strip():
            issues.append(
                ValidationIssue(
                    "error",
                    "empty_public_hostname",
                    "public_hostname must not be empty.",
                    "public_hostname",
                )
            )
        if not self.tls_certificate_path.strip():
            issues.append(
                ValidationIssue(
                    "error",
                    "empty_tls_certificate_path",
                    "tls_certificate_path must not be empty.",
                    "tls_certificate_path",
                )
            )
        if not self.tls_key_path.strip():
            issues.append(
                ValidationIssue(
                    "error",
                    "empty_tls_key_path",
                    "tls_key_path must not be empty.",
                    "tls_key_path",
                )
            )
        if self.auth_provider == "oidc" and not self.oidc_issuer.strip():
            issues.append(
                ValidationIssue(
                    "error",
                    "empty_oidc_issuer",
                    "oidc_issuer is required when auth_provider=oidc.",
                    "oidc_issuer",
                )
            )
        if not self.admin_group.strip():
            issues.append(
                ValidationIssue(
                    "error",
                    "empty_admin_group",
                    "admin_group must not be empty.",
                    "admin_group",
                )
            )
        if not self.database_url.strip():
            issues.append(
                ValidationIssue(
                    "error",
                    "empty_database_url",
                    "database_url must not be empty.",
                    "database_url",
                )
            )
        elif self.database_url.strip().lower().startswith("sqlite"):
            issues.append(
                ValidationIssue(
                    "error",
                    "sqlite_not_allowed",
                    "Hosted mode must use PostgreSQL, not SQLite.",
                    "database_url",
                )
            )
        if not self.session_secret_path.strip():
            issues.append(
                ValidationIssue(
                    "error",
                    "empty_session_secret_path",
                    "session_secret_path must not be empty.",
                    "session_secret_path",
                )
            )
        if not self.trusted_proxy_addresses:
            issues.append(
                ValidationIssue(
                    "error",
                    "empty_trusted_proxy_addresses",
                    "trusted_proxy_addresses must not be empty.",
                    "trusted_proxy_addresses",
                )
            )
        if self.session_max_age_minutes < 5:
            issues.append(
                ValidationIssue(
                    "error",
                    "session_max_age_too_short",
                    "session_max_age_minutes must be at least 5.",
                    "session_max_age_minutes",
                )
            )
        if self.rate_limit_per_minute < 1:
            issues.append(
                ValidationIssue(
                    "error",
                    "rate_limit_too_low",
                    "rate_limit_per_minute must be at least 1.",
                    "rate_limit_per_minute",
                )
            )
        return tuple(issues)


@dataclass(frozen=True, slots=True)
class DeploymentConfig:
    """Top-level deployment profile selection.

    In the ``local`` profile the application is loopback-only and SQLite-backed
    and no hosted configuration is required. In the ``hosted`` profile a
    fully-populated :class:`HostedConfig` must validate.
    """

    profile: DeploymentProfile = "local"
    hosted: HostedConfig | None = None

    def validate(self) -> tuple[ValidationIssue, ...]:
        """Validate profile-specific rules.

        - ``profile=hosted`` with ``hosted=None`` is an error (config required).
        - ``profile=hosted`` delegates to :meth:`HostedConfig.validate`.
        - ``profile=local`` with a non-None ``hosted`` is a warning (ignored).
        """
        issues: list[ValidationIssue] = []
        match self.profile:
            case "local":
                if self.hosted is not None:
                    issues.append(
                        ValidationIssue(
                            "warning",
                            "hosted_ignored",
                            "Hosted configuration is ignored in local profile.",
                            "hosted",
                        )
                    )
            case "hosted":
                if self.hosted is None:
                    issues.append(
                        ValidationIssue(
                            "error",
                            "hosted_config_required",
                            "Hosted profile requires hosted configuration.",
                            "hosted",
                        )
                    )
                else:
                    issues.extend(self.hosted.validate())
            case _:
                assert_never(self.profile)
        return tuple(issues)

    def is_hosted(self) -> bool:
        return self.profile == "hosted"


# ---------------------------------------------------------------------------
# Authentication model
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AuthenticatedIdentity:
    """Server-derived identity.

    Never constructed from client-supplied headers in hosted mode. The
    ``subject`` is a stable provider/subject key; ``display_name`` is carried
    separately for UI surfaces and may change across rename events.
    """

    subject: str  # stable provider/subject key
    display_name: str = ""
    provider: AuthProvider = "local_dev"
    email: str = ""
    groups: tuple[str, ...] = ()  # group SIDs or OIDC group claims
    authenticated_at: str = ""
    session_id: str = ""
    is_admin: bool = False  # derived from admin_group membership

    def validate(self, *, hosted: bool = False) -> tuple[ValidationIssue, ...]:
        """Validate identity structural rules.

        ``hosted=True`` rejects the ``local_dev`` provider unconditionally;
        ``local_dev`` is only valid in the local profile where authentication
        is not enforced.
        """
        issues: list[ValidationIssue] = []
        if not self.subject.strip():
            issues.append(
                ValidationIssue(
                    "error",
                    "empty_subject",
                    "subject must not be empty.",
                    "subject",
                )
            )
        if hosted and self.provider == "local_dev":
            issues.append(
                ValidationIssue(
                    "error",
                    "local_dev_in_hosted",
                    "local_dev provider is never allowed in hosted mode.",
                    "provider",
                )
            )
        if not self.session_id.strip():
            issues.append(
                ValidationIssue(
                    "error",
                    "empty_session_id",
                    "session_id must not be empty.",
                    "session_id",
                )
            )
        return tuple(issues)


@dataclass(frozen=True, slots=True)
class SessionConfig:
    """Session cookie and rotation policy."""

    max_age_minutes: int = 60
    idle_timeout_minutes: int = 30
    secure_cookie: bool = True  # Secure attribute
    http_only: bool = True  # HttpOnly attribute
    same_site: Literal["strict", "lax", "none"] = "strict"
    rotation_enabled: bool = True  # rotate session ID on privilege change

    def validate(self) -> tuple[ValidationIssue, ...]:
        """Validate session policy rules.

        - ``max_age_minutes < 5`` → error.
        - ``idle_timeout_minutes > max_age_minutes`` → error.
        - ``same_site=none`` without ``secure_cookie`` → error.
        - ``http_only=False`` → error (never allowed).
        """
        issues: list[ValidationIssue] = []
        if self.max_age_minutes < 5:
            issues.append(
                ValidationIssue(
                    "error",
                    "max_age_too_short",
                    "max_age_minutes must be at least 5.",
                    "max_age_minutes",
                )
            )
        if self.idle_timeout_minutes > self.max_age_minutes:
            issues.append(
                ValidationIssue(
                    "error",
                    "idle_exceeds_max_age",
                    "idle_timeout_minutes must not exceed max_age_minutes.",
                    "idle_timeout_minutes",
                )
            )
        if self.same_site == "none" and not self.secure_cookie:
            issues.append(
                ValidationIssue(
                    "error",
                    "same_site_none_without_secure",
                    "same_site=none requires secure_cookie=True.",
                    "same_site",
                )
            )
        if not self.http_only:
            issues.append(
                ValidationIssue(
                    "error",
                    "http_only_disabled",
                    "http_only must always be True.",
                    "http_only",
                )
            )
        return tuple(issues)


# ---------------------------------------------------------------------------
# Authorization matrix
# ---------------------------------------------------------------------------

Role = Literal[
    "reader",
    "author",
    "reviewer",
    "approver",
    "exporter",
    "auditor",
    "platform_admin",
]

Operation = Literal[
    "read_gpo",
    "create_gpo",
    "edit_gpo",
    "delete_gpo",
    "review_gpo",
    "approve_gpo",
    "export_gpo",
    "import_gpo",
    "read_audit",
    "export_audit",
    "manage_roles",
    "manage_workspace",
    "manage_platform",
    "download_bundle",
    "download_ps_plan",
]

# Deny-by-default role→operation matrix. ``platform_admin`` transitively
# grants every operation; every other role is a least-privilege subset.
ROLE_PERMISSIONS: dict[Role, frozenset[Operation]] = {
    "reader": frozenset({"read_gpo", "read_audit"}),
    "author": frozenset({"read_gpo", "create_gpo", "edit_gpo", "read_audit"}),
    "reviewer": frozenset({"read_gpo", "review_gpo", "read_audit"}),
    "approver": frozenset({"read_gpo", "approve_gpo", "read_audit"}),
    "exporter": frozenset(
        {"read_gpo", "export_gpo", "download_bundle", "download_ps_plan", "read_audit"}
    ),
    "auditor": frozenset({"read_gpo", "read_audit", "export_audit"}),
    "platform_admin": frozenset(
        {
            "read_gpo",
            "create_gpo",
            "edit_gpo",
            "delete_gpo",
            "review_gpo",
            "approve_gpo",
            "export_gpo",
            "import_gpo",
            "read_audit",
            "export_audit",
            "manage_roles",
            "manage_workspace",
            "manage_platform",
            "download_bundle",
            "download_ps_plan",
        }
    ),
}


@dataclass(frozen=True, slots=True)
class RoleGrant:
    """A grant of a :data:`Role` to a principal, optionally scoped."""

    role: Role
    principal_subject: str  # identity subject key
    scope: str = ""  # workspace or OU scope (empty = workspace-wide)
    granted_by: str = ""
    granted_at: str = ""

    def validate(self) -> tuple[ValidationIssue, ...]:
        """Validate role-grant structural rules."""
        issues: list[ValidationIssue] = []
        if not self.principal_subject.strip():
            issues.append(
                ValidationIssue(
                    "error",
                    "empty_principal_subject",
                    "principal_subject must not be empty.",
                    "principal_subject",
                )
            )
        if not self.granted_by.strip():
            issues.append(
                ValidationIssue(
                    "warning",
                    "empty_granted_by",
                    "granted_by should record who issued the grant.",
                    "granted_by",
                )
            )
        return tuple(issues)


@dataclass(frozen=True, slots=True)
class AuthorizationDecision:
    """Result of an authorization check."""

    allowed: bool
    operation: Operation
    principal_subject: str
    role: Role | None = None  # role that granted access (None if denied)
    reason: str = ""


def _grant_matches_scope(grant: RoleGrant, scope: str) -> bool:
    """Return True when a grant applies at ``scope``.

    A workspace-wide grant (``grant.scope == ""``) applies at any scope. A
    scoped grant applies only at its own scope. A workspace-level query
    (``scope == ""``) matches only workspace-wide grants.
    """
    if grant.scope == "":
        return True
    return grant.scope == scope


def check_authorization(
    identity: AuthenticatedIdentity,
    operation: Operation,
    grants: tuple[RoleGrant, ...],
    scope: str = "",
) -> AuthorizationDecision:
    """Check if an identity is authorized for an operation.

    Algorithm:

    1. Collect grants matching ``identity.subject`` and ``scope``.
    2. For each matching grant, check the role's permissions for ``operation``.
    3. ``platform_admin`` transitively grants every operation via the matrix.
    4. Deny by default if no matching grant authorizes the operation.
    5. Self-approval is checked separately by :func:`can_self_approve`.
    """
    for grant in grants:
        if grant.principal_subject != identity.subject:
            continue
        if not _grant_matches_scope(grant, scope):
            continue
        if operation in ROLE_PERMISSIONS[grant.role]:
            return AuthorizationDecision(
                allowed=True,
                operation=operation,
                principal_subject=identity.subject,
                role=grant.role,
                reason=f"Authorized by role {grant.role!r}.",
            )
    return AuthorizationDecision(
        allowed=False,
        operation=operation,
        principal_subject=identity.subject,
        role=None,
        reason="No matching grant authorizes this operation.",
    )


def can_self_approve(identity: AuthenticatedIdentity, author_subject: str) -> bool:
    """Return True if ``identity`` may approve a change authored by ``author_subject``.

    Authors cannot satisfy their own required review. Returns ``False`` when
    ``identity.subject == author_subject``.
    """
    return identity.subject != author_subject


# ---------------------------------------------------------------------------
# Audit event model
# ---------------------------------------------------------------------------

AuditAction = Literal[
    "create",
    "read",
    "update",
    "delete",
    "approve",
    "reject",
    "review",
    "export",
    "import",
    "publish",
    "login",
    "logout",
    "session_expired",
    "role_granted",
    "role_revoked",
    "config_changed",
    "backup_created",
    "restore_performed",
    "break_glass_access",
]


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """A single immutable audit event."""

    event_id: str
    action: AuditAction
    actor_subject: str
    actor_display: str = ""
    actor_provider: AuthProvider = "local_dev"
    target_type: str = ""  # e.g. "gpo", "workspace", "role"
    target_id: str = ""
    request_id: str = ""  # correlation ID
    prior_revision: int | None = None
    new_revision: int | None = None
    reason: str = ""
    outcome: Literal["success", "failure", "denied"] = "success"
    detail: str = ""
    timestamp: str = ""
    ip_address: str = ""

    def validate(self) -> tuple[ValidationIssue, ...]:
        """Validate audit-event structural rules."""
        issues: list[ValidationIssue] = []
        if not self.event_id.strip():
            issues.append(
                ValidationIssue(
                    "error",
                    "empty_event_id",
                    "event_id must not be empty.",
                    "event_id",
                )
            )
        if not self.actor_subject.strip():
            issues.append(
                ValidationIssue(
                    "error",
                    "empty_actor_subject",
                    "actor_subject must not be empty.",
                    "actor_subject",
                )
            )
        if not self.timestamp.strip():
            issues.append(
                ValidationIssue(
                    "error",
                    "empty_timestamp",
                    "timestamp must not be empty.",
                    "timestamp",
                )
            )
        if self.action == "break_glass_access" and not self.detail.strip():
            issues.append(
                ValidationIssue(
                    "error",
                    "break_glass_without_detail",
                    "break_glass_access requires a non-empty detail.",
                    "detail",
                )
            )
        if self.outcome == "denied" and not self.reason.strip():
            issues.append(
                ValidationIssue(
                    "warning",
                    "denied_without_reason",
                    "denied outcome should record a reason.",
                    "reason",
                )
            )
        return tuple(issues)


@dataclass(frozen=True, slots=True)
class AuditQuery:
    """Filter criteria for audit-event queries."""

    actor_subject: str = ""
    action: AuditAction | None = None
    target_type: str = ""
    target_id: str = ""
    outcome: Literal["success", "failure", "denied"] | None = None
    since: str = ""  # ISO timestamp lower bound
    until: str = ""  # ISO timestamp upper bound
    limit: int = 100


def filter_audit_events(
    events: tuple[AuditEvent, ...],
    query: AuditQuery,
) -> tuple[AuditEvent, ...]:
    """Filter audit events by query criteria.

    Returns matching events, most recent first (descending ISO timestamp
    order). ``since``/``until`` are compared lexicographically against the
    event ``timestamp``, which is well-defined for ISO 8601 strings. The
    ``limit`` is applied after sorting.
    """
    filtered = [
        e
        for e in events
        if (not query.actor_subject or e.actor_subject == query.actor_subject)
        and (query.action is None or e.action == query.action)
        and (not query.target_type or e.target_type == query.target_type)
        and (not query.target_id or e.target_id == query.target_id)
        and (query.outcome is None or e.outcome == query.outcome)
        and (not query.since or e.timestamp >= query.since)
        and (not query.until or e.timestamp <= query.until)
    ]
    filtered.sort(key=lambda e: e.timestamp, reverse=True)
    return tuple(filtered[: query.limit])


__all__ = [
    "AuditAction",
    "AuditEvent",
    "AuditQuery",
    "AuthenticatedIdentity",
    "AuthProvider",
    "AuthorizationDecision",
    "DeploymentConfig",
    "DeploymentProfile",
    "HostedConfig",
    "Operation",
    "ROLE_PERMISSIONS",
    "Role",
    "RoleGrant",
    "SessionConfig",
    "can_self_approve",
    "check_authorization",
    "filter_audit_events",
]
