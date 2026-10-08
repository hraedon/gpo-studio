"""Network and public-key security families.

**The firewall half is `firewall_policy.py`, re-exported** (WI-076, 2026-10-08).
`FirewallPolicy`, `FirewallRule`, `FirewallProfileSettings` and the firewall
literal types below are the capture-backed codec's own classes, which the
firewall lane certified (`firewall-20261008094055-2092337`) and
`/api/network-security/firewall/*` surfaces. The legacy firewall model that
used to live here assumed one global set of profile values, protocol names and
free-form port strings; it could not represent the native capture, so it was
replaced rather than aliased. Its rule-level advice (broad inbound allows,
disabled logging) went with it: the codec's `validate()` reports what Windows
was measured to accept, not what an administrator should prefer.

**IPsec connection security, Public Key and wired/wireless network policy are
out of scope for 1.x** (operator ruling 2026-10-07). Their typed models remain
below, reachable from no API endpoint, UI module or export path, and none has
Windows evidence. They are retained code, not capabilities.

This module stays independent from FastAPI.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from .firewall_policy import (
    FirewallAction,
    FirewallDirection,
    FirewallParseResult,
    FirewallPolicy,
    FirewallProfile,
    FirewallProfileSettings,
    FirewallRule,
    FirewallValidationError,
    UnknownFirewallToken,
    from_registry_records,
    to_registry_settings,
)
from .model import ValidationIssue

# ---------------------------------------------------------------------------
# Type aliases (out-of-scope families)
# ---------------------------------------------------------------------------

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

    * Firewall explicitly disabled on every profile **and** no IPsec rules
      → ``critical``
    * Any error → ``high``
    * Weak encryption anywhere (IPsec ``none``/``des`` or WEP wireless) → ``high``
    * Only warnings → ``medium``
    * No issues → ``low``
    """
    firewall_issues = firewall.validate()
    ipsec_issues = ipsec.validate()
    pki_issues = pki.validate()
    network_issues = networks.validate()

    # `None` is "not configured by this policy", so the local default
    # (enabled) applies; only an explicit False on every profile is "off".
    # The codec refuses an explicit False (unmeasured), so such a policy also
    # carries an error below.
    firewall_disabled = all(
        profile.enabled is False
        for profile in (firewall.domain, firewall.private, firewall.public)
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
    "FirewallParseResult",
    "FirewallPolicy",
    "FirewallProfile",
    "FirewallProfileSettings",
    "FirewallRule",
    "FirewallValidationError",
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
    "UnknownFirewallToken",
    "assess_network_security",
    "from_registry_records",
    "to_registry_settings",
]
