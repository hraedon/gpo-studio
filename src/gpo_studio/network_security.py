"""Network and public-key security families.

Windows Firewall with Advanced Security, IPsec connection security rules,
public key (certificate) policies, and wired/wireless network policies.

Unlike the account and object families in :mod:`policy_families` and
:mod:`object_security`, these families are not stored in the INF security
template; they use dedicated Group Policy Client-Side Extension (CSE) formats.
This module therefore exposes typed models with validation and aggregate risk
assessment, keeping the core independent from FastAPI.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from .model import ValidationIssue

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

FirewallDirection = Literal["inbound", "outbound"]
FirewallAction = Literal["allow", "block", "bypass"]
FirewallProtocol = Literal["tcp", "udp", "icmpv4", "icmpv6", "any"]
FirewallProfile = Literal["domain", "private", "public"]

IpsecMode = Literal["transport", "tunnel"]
IpsecAuthentication = Literal["kerberos", "certificate", "preshared_key", "ntlm"]
IpsecEncryption = Literal["none", "des", "3des", "aes128", "aes256", "gcm128", "gcm256"]

NetworkRiskLevel = Literal["low", "medium", "high", "critical"]

# IPsec encryption algorithms considered weak or disabled.
_WEAK_IPSEC_ENCRYPTION: frozenset[IpsecEncryption] = frozenset({"none", "des"})

# SHA-1 certificate thumbprint: 40 hexadecimal characters.
_THUMBPRINT_RE = re.compile(r"^[0-9a-fA-F]{40}$")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _validate_port_string(port: str) -> bool:
    """Return ``True`` if a port specification is well-formed.

    Accepts empty (no restriction), ``"*"`` (any), single ports (``"80"``),
    ranges (``"1024-2048"``), and comma-separated lists of the above.
    """
    stripped = port.strip()
    if not stripped or stripped == "*":
        return True
    for part in stripped.split(","):
        part = part.strip()
        if not part or part == "*":
            continue
        if "-" in part:
            bounds = part.split("-", 1)
            try:
                low = int(bounds[0].strip())
                high = int(bounds[1].strip())
            except (ValueError, IndexError):
                return False
            if not (0 <= low <= 65535) or not (0 <= high <= 65535) or low > high:
                return False
        else:
            try:
                value = int(part)
            except ValueError:
                return False
            if not (0 <= value <= 65535):
                return False
    return True


def _is_expired(not_after: str) -> bool:
    """Return ``True`` if *not_after* parses to a past date/time.

    Returns ``False`` when the string is empty or unparseable so that
    malformed dates do not produce spurious expiry warnings.
    """
    stripped = not_after.strip()
    if not stripped:
        return False
    try:
        parsed = datetime.fromisoformat(stripped)
    except ValueError:
        return False
    if parsed.tzinfo is not None:
        return parsed < datetime.now(parsed.tzinfo)
    return parsed < datetime.now()


# ---------------------------------------------------------------------------
# Windows Firewall with Advanced Security
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FirewallRule:
    rule_id: str = ""
    name: str = ""
    direction: FirewallDirection = "inbound"
    action: FirewallAction = "block"
    protocol: FirewallProtocol = "any"
    local_port: str = ""
    remote_port: str = ""
    local_address: str = ""
    remote_address: str = ""
    profiles: tuple[FirewallProfile, ...] = ("domain", "private", "public")
    enabled: bool = True
    description: str = ""
    program: str = ""
    service: str = ""

    def validate(self) -> tuple[ValidationIssue, ...]:
        issues: list[ValidationIssue] = []
        ident = self.name.strip() or self.rule_id.strip() or "?"
        path = f"FirewallPolicy/rules/{ident}"
        if not self.name.strip():
            issues.append(
                ValidationIssue(
                    "error",
                    "firewall_rule_empty_name",
                    "Firewall rule has an empty name.",
                    "FirewallPolicy/rules",
                )
            )
        if not _validate_port_string(self.local_port):
            issues.append(
                ValidationIssue(
                    "error",
                    "firewall_rule_invalid_local_port",
                    f"Local port specification '{self.local_port}' is invalid.",
                    f"{path}/local_port",
                )
            )
        if not _validate_port_string(self.remote_port):
            issues.append(
                ValidationIssue(
                    "error",
                    "firewall_rule_invalid_remote_port",
                    f"Remote port specification '{self.remote_port}' is invalid.",
                    f"{path}/remote_port",
                )
            )
        if (
            self.action == "allow"
            and self.direction == "inbound"
            and self.remote_address.strip() in ("", "*")
        ):
            issues.append(
                ValidationIssue(
                    "warning",
                    "firewall_broad_inbound_allow",
                    "Inbound allow rule with any remote address is overly permissive.",
                    f"{path}/remote_address",
                )
            )
        if self.protocol in ("icmpv4", "icmpv6") and (
            self.local_port.strip() or self.remote_port.strip()
        ):
            issues.append(
                ValidationIssue(
                    "warning",
                    "firewall_icmp_port_ignored",
                    "Port settings are ignored for ICMP rules.",
                    f"{path}/protocol",
                )
            )
        return tuple(issues)


@dataclass(frozen=True, slots=True)
class FirewallPolicy:
    rules: tuple[FirewallRule, ...] = field(default_factory=tuple)
    domain_profile_enabled: bool = True
    private_profile_enabled: bool = True
    public_profile_enabled: bool = True
    default_inbound_action: FirewallAction = "block"
    default_outbound_action: FirewallAction = "allow"
    logging_enabled: bool = False
    log_path: str = ""
    log_size_limit_kb: int = 4096

    def validate(self) -> tuple[ValidationIssue, ...]:
        issues: list[ValidationIssue] = []
        if not (
            self.domain_profile_enabled
            or self.private_profile_enabled
            or self.public_profile_enabled
        ):
            issues.append(
                ValidationIssue(
                    "warning",
                    "firewall_all_profiles_disabled",
                    "All firewall profiles are disabled; the firewall is "
                    "effectively off.",
                    "FirewallPolicy/profiles",
                )
            )
        if self.default_inbound_action == "allow":
            issues.append(
                ValidationIssue(
                    "warning",
                    "firewall_permissive_default_inbound",
                    "Default inbound action is set to allow, which is permissive.",
                    "FirewallPolicy/default_inbound_action",
                )
            )
        if not self.logging_enabled:
            issues.append(
                ValidationIssue(
                    "warning",
                    "firewall_logging_disabled",
                    "Firewall logging is disabled.",
                    "FirewallPolicy/logging_enabled",
                )
            )
        for rule in self.rules:
            issues.extend(rule.validate())
        return tuple(issues)

    def rules_for_profile(self, profile: FirewallProfile) -> tuple[FirewallRule, ...]:
        return tuple(rule for rule in self.rules if profile in rule.profiles)

    def rules_for_direction(
        self, direction: FirewallDirection
    ) -> tuple[FirewallRule, ...]:
        return tuple(rule for rule in self.rules if rule.direction == direction)


# ---------------------------------------------------------------------------
# IPsec / Connection Security
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class IpsecRule:
    rule_id: str = ""
    name: str = ""
    mode: IpsecMode = "transport"
    local_address: str = ""
    remote_address: str = ""
    protocol: str = ""
    local_port: str = ""
    remote_port: str = ""
    authentication: IpsecAuthentication = "kerberos"
    encryption: IpsecEncryption = "aes256"
    enabled: bool = True
    description: str = ""

    def validate(self) -> tuple[ValidationIssue, ...]:
        issues: list[ValidationIssue] = []
        ident = self.name.strip() or self.rule_id.strip() or "?"
        path = f"IpsecPolicy/rules/{ident}"
        if not self.name.strip():
            issues.append(
                ValidationIssue(
                    "error",
                    "ipsec_rule_empty_name",
                    "IPsec rule has an empty name.",
                    "IpsecPolicy/rules",
                )
            )
        if self.authentication == "preshared_key":
            issues.append(
                ValidationIssue(
                    "warning",
                    "ipsec_preshared_key_weak_auth",
                    "Pre-shared key authentication is weaker than Kerberos "
                    "or certificates.",
                    f"{path}/authentication",
                )
            )
        if self.encryption in _WEAK_IPSEC_ENCRYPTION:
            issues.append(
                ValidationIssue(
                    "warning",
                    "ipsec_weak_encryption",
                    f"IPsec encryption '{self.encryption}' is weak or disabled.",
                    f"{path}/encryption",
                )
            )
        if self.mode == "tunnel" and not self.remote_address.strip():
            issues.append(
                ValidationIssue(
                    "error",
                    "ipsec_tunnel_missing_remote_address",
                    "Tunnel mode requires a remote address.",
                    f"{path}/remote_address",
                )
            )
        return tuple(issues)


@dataclass(frozen=True, slots=True)
class IpsecPolicy:
    rules: tuple[IpsecRule, ...] = field(default_factory=tuple)

    def validate(self) -> tuple[ValidationIssue, ...]:
        issues: list[ValidationIssue] = []
        for rule in self.rules:
            issues.extend(rule.validate())
        return tuple(issues)


# ---------------------------------------------------------------------------
# Public Key Policies
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CertificateTrustEntry:
    thumbprint: str
    subject: str = ""
    issuer: str = ""
    purpose: Literal["trust", "disallow", "auto_enrollment"] = "trust"
    not_after: str = ""

    def validate(self) -> tuple[ValidationIssue, ...]:
        issues: list[ValidationIssue] = []
        thumb = self.thumbprint.strip()
        path = f"PublicKeyPolicy/{self.purpose}/{thumb or '?'}"
        if not thumb:
            issues.append(
                ValidationIssue(
                    "error",
                    "cert_trust_empty_thumbprint",
                    "Certificate trust entry has an empty thumbprint.",
                    "PublicKeyPolicy",
                )
            )
        elif not _THUMBPRINT_RE.match(thumb):
            issues.append(
                ValidationIssue(
                    "error",
                    "cert_trust_invalid_thumbprint",
                    f"Thumbprint '{self.thumbprint}' is not a valid "
                    "40-character SHA-1 hex string.",
                    f"{path}/thumbprint",
                )
            )
        if _is_expired(self.not_after):
            issues.append(
                ValidationIssue(
                    "warning",
                    "cert_trust_expired",
                    f"Certificate '{thumb}' expired on {self.not_after}.",
                    f"{path}/not_after",
                )
            )
        return tuple(issues)


@dataclass(frozen=True, slots=True)
class PublicKeyPolicy:
    trusted_roots: tuple[CertificateTrustEntry, ...] = field(default_factory=tuple)
    disallowed: tuple[CertificateTrustEntry, ...] = field(default_factory=tuple)
    auto_enrollment: tuple[CertificateTrustEntry, ...] = field(default_factory=tuple)
    efs_recovery_agents: tuple[str, ...] = field(default_factory=tuple)
    certificate_path_validation: Literal["chain", "chain_and_leaf"] = "chain"

    def validate(self) -> tuple[ValidationIssue, ...]:
        issues: list[ValidationIssue] = []
        if not self.trusted_roots:
            issues.append(
                ValidationIssue(
                    "warning",
                    "pki_no_trusted_roots",
                    "No trusted root certificates are configured; there are "
                    "no trust anchors.",
                    "PublicKeyPolicy/trusted_roots",
                )
            )
        if not self.efs_recovery_agents:
            issues.append(
                ValidationIssue(
                    "warning",
                    "pki_no_efs_recovery_agents",
                    "No EFS recovery agents are configured.",
                    "PublicKeyPolicy/efs_recovery_agents",
                )
            )
        trusted_thumbprints = {
            entry.thumbprint.strip().casefold() for entry in self.trusted_roots
        }
        for entry in self.disallowed:
            if entry.thumbprint.strip().casefold() in trusted_thumbprints:
                issues.append(
                    ValidationIssue(
                        "error",
                        "pki_cert_in_trust_and_disallow",
                        f"Certificate '{entry.thumbprint}' appears in both "
                        "trusted roots and disallowed lists.",
                        f"PublicKeyPolicy/disallowed/{entry.thumbprint}",
                    )
                )
        for entry in self.trusted_roots:
            issues.extend(entry.validate())
        for entry in self.disallowed:
            issues.extend(entry.validate())
        for entry in self.auto_enrollment:
            issues.extend(entry.validate())
        return tuple(issues)


# ---------------------------------------------------------------------------
# Network List Manager / Wired-Wireless
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NetworkPolicy:
    network_name: str = ""
    network_type: Literal["wired", "wireless", "any"] = "any"
    authentication: Literal["none", "802.1x", "wpa2", "wpa3"] = "none"
    encryption: Literal["none", "wep", "tkip", "aes"] = "none"
    auto_connect: bool = True
    description: str = ""

    def validate(self) -> tuple[ValidationIssue, ...]:
        issues: list[ValidationIssue] = []
        ident = self.network_name.strip() or "?"
        path = f"NetworkSecurityFamily/networks/{ident}"
        if not self.network_name.strip():
            issues.append(
                ValidationIssue(
                    "error",
                    "network_empty_name",
                    "Network policy has an empty name.",
                    "NetworkSecurityFamily/networks",
                )
            )
        if self.network_type == "wireless" and self.authentication == "none":
            issues.append(
                ValidationIssue(
                    "warning",
                    "network_unsecured_wireless",
                    "Wireless network has no authentication configured.",
                    f"{path}/authentication",
                )
            )
        if self.encryption == "wep":
            issues.append(
                ValidationIssue(
                    "warning",
                    "network_wep_weak_encryption",
                    "WEP encryption is weak and should not be used.",
                    f"{path}/encryption",
                )
            )
        return tuple(issues)


@dataclass(frozen=True, slots=True)
class NetworkSecurityFamily:
    networks: tuple[NetworkPolicy, ...] = field(default_factory=tuple)

    def validate(self) -> tuple[ValidationIssue, ...]:
        issues: list[ValidationIssue] = []
        for network in self.networks:
            issues.extend(network.validate())
        return tuple(issues)


# ---------------------------------------------------------------------------
# Aggregate assessment
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NetworkSecurityAssessment:
    firewall_issues: tuple[ValidationIssue, ...]
    ipsec_issues: tuple[ValidationIssue, ...]
    pki_issues: tuple[ValidationIssue, ...]
    network_issues: tuple[ValidationIssue, ...]
    overall_risk: NetworkRiskLevel


def assess_network_security(
    firewall: FirewallPolicy,
    ipsec: IpsecPolicy,
    pki: PublicKeyPolicy,
    networks: NetworkSecurityFamily,
) -> NetworkSecurityAssessment:
    """Aggregate all network security issues and compute overall risk.

    Risk rules (highest priority first):

    * Firewall disabled **and** no IPsec rules → ``critical``
    * Any error → ``high``
    * Weak encryption anywhere (IPsec ``none``/``des`` or WEP wireless) → ``high``
    * Only warnings → ``medium``
    * No issues → ``low``
    """
    firewall_issues = firewall.validate()
    ipsec_issues = ipsec.validate()
    pki_issues = pki.validate()
    network_issues = networks.validate()

    firewall_disabled = not (
        firewall.domain_profile_enabled
        or firewall.private_profile_enabled
        or firewall.public_profile_enabled
    )
    no_ipsec = len(ipsec.rules) == 0

    all_issues = (
        *firewall_issues,
        *ipsec_issues,
        *pki_issues,
        *network_issues,
    )
    has_error = any(issue.severity == "error" for issue in all_issues)
    has_warning = any(issue.severity == "warning" for issue in all_issues)
    weak_encryption = any(
        issue.code == "ipsec_weak_encryption" for issue in ipsec_issues
    ) or any(
        issue.code == "network_wep_weak_encryption" for issue in network_issues
    )

    risk: NetworkRiskLevel
    if firewall_disabled and no_ipsec:
        risk = "critical"
    elif has_error or weak_encryption:
        risk = "high"
    elif has_warning:
        risk = "medium"
    else:
        risk = "low"

    return NetworkSecurityAssessment(
        firewall_issues=firewall_issues,
        ipsec_issues=ipsec_issues,
        pki_issues=pki_issues,
        network_issues=network_issues,
        overall_risk=risk,
    )


__all__ = [
    "CertificateTrustEntry",
    "FirewallAction",
    "FirewallDirection",
    "FirewallPolicy",
    "FirewallProfile",
    "FirewallProtocol",
    "FirewallRule",
    "IpsecAuthentication",
    "IpsecEncryption",
    "IpsecMode",
    "IpsecPolicy",
    "IpsecRule",
    "NetworkPolicy",
    "NetworkRiskLevel",
    "NetworkSecurityAssessment",
    "NetworkSecurityFamily",
    "PublicKeyPolicy",
    "assess_network_security",
]
