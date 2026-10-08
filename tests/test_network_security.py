"""Tests for network and public-key security families."""

from __future__ import annotations

from gpo_studio import firewall_policy, network_security
from gpo_studio.model import ValidationIssue
from gpo_studio.network_security import (
    CertificateTrustEntry,
    FirewallPolicy,
    FirewallProfileSettings,
    FirewallRule,
    IpsecPolicy,
    IpsecRule,
    NetworkPolicy,
    NetworkSecurityFamily,
    PublicKeyPolicy,
    assess_network_security,
)

_VALID_THUMBPRINT = "0123456789abcdef0123456789abcdef01234567"
_ALT_THUMBPRINT = "fedcba9876543210fedcba9876543210fedcba98"


# ---------------------------------------------------------------------------
# The firewall half: explicit re-exports of firewall_policy (WI-076)
# ---------------------------------------------------------------------------

_REEXPORTED = (
    "FirewallAction",
    "FirewallDirection",
    "FirewallParseResult",
    "FirewallPolicy",
    "FirewallProfile",
    "FirewallProfileSettings",
    "FirewallRule",
    "FirewallValidationError",
    "UnknownFirewallToken",
    "from_registry_records",
    "to_registry_settings",
)


def test_the_firewall_half_is_the_certified_codec_not_a_lookalike() -> None:
    """Every firewall name is the codec's own object, by identity."""
    for name in _REEXPORTED:
        assert getattr(network_security, name) is getattr(firewall_policy, name), name
        assert name in network_security.__all__, name


def test_the_legacy_firewall_model_is_gone() -> None:
    """No second firewall model survives beside the certified one.

    The legacy classes assumed global profile values, protocol names and
    free-form port strings; keeping them under the same names would let a
    caller build a policy no lane measured and believe it was the surfaced one.
    """
    assert not hasattr(network_security, "FirewallProtocol")
    assert not hasattr(network_security, "_validate_port_string")
    assert "FirewallProtocol" not in network_security.__all__
    assert not hasattr(FirewallPolicy, "domain_profile_enabled")
    assert not hasattr(FirewallPolicy, "rules_for_profile")


def test_validate_through_the_facade_is_the_codecs_refusal() -> None:
    rule = FirewallRule("R1", "unmeasured protocol", protocol=99)
    assert [i.code for i in rule.validate()] == ["firewall_unmeasured_protocol"]
    assert FirewallPolicy().validate() == ()


# ---------------------------------------------------------------------------
# IpsecRule
# ---------------------------------------------------------------------------


def test_ipsec_rule_valid() -> None:
    rule = IpsecRule(
        name="Domain isolation",
        mode="transport",
        authentication="kerberos",
        encryption="aes256",
    )
    assert rule.validate() == ()


def test_ipsec_rule_empty_name_error() -> None:
    rule = IpsecRule(name="")
    issues = rule.validate()
    assert any(i.code == "ipsec_rule_empty_name" for i in issues)
    assert any(i.severity == "error" for i in issues)


def test_ipsec_rule_preshared_key_warning() -> None:
    rule = IpsecRule(
        name="PSK rule",
        authentication="preshared_key",
    )
    issues = rule.validate()
    assert any(i.code == "ipsec_preshared_key_weak_auth" for i in issues)
    assert all(i.severity == "warning" for i in issues if i.code == "ipsec_preshared_key_weak_auth")


def test_ipsec_rule_weak_encryption_warning() -> None:
    rule_des = IpsecRule(name="DES rule", encryption="des")
    rule_none = IpsecRule(name="None rule", encryption="none")
    for rule in (rule_des, rule_none):
        issues = rule.validate()
        assert any(i.code == "ipsec_weak_encryption" for i in issues)


def test_ipsec_rule_strong_encryption_clean() -> None:
    for enc in ("3des", "aes128", "aes256", "gcm128", "gcm256"):
        rule = IpsecRule(name=f"{enc} rule", encryption=enc)
        assert all(i.code != "ipsec_weak_encryption" for i in rule.validate())


def test_ipsec_rule_tunnel_missing_remote_address_error() -> None:
    rule = IpsecRule(
        name="Tunnel no remote",
        mode="tunnel",
        remote_address="",
    )
    issues = rule.validate()
    assert any(i.code == "ipsec_tunnel_missing_remote_address" for i in issues)
    assert any(i.severity == "error" for i in issues)


def test_ipsec_rule_tunnel_with_remote_clean() -> None:
    rule = IpsecRule(
        name="Tunnel with remote",
        mode="tunnel",
        remote_address="10.0.0.1",
    )
    assert all(i.code != "ipsec_tunnel_missing_remote_address" for i in rule.validate())


def test_ipsec_policy_aggregates_rule_issues() -> None:
    bad_rule = IpsecRule(name="")
    policy = IpsecPolicy(rules=(bad_rule,))
    issues = policy.validate()
    assert any(i.code == "ipsec_rule_empty_name" for i in issues)


# ---------------------------------------------------------------------------
# CertificateTrustEntry
# ---------------------------------------------------------------------------


def test_cert_trust_entry_valid() -> None:
    entry = CertificateTrustEntry(thumbprint=_VALID_THUMBPRINT, subject="CN=Test")
    assert entry.validate() == ()


def test_cert_trust_entry_empty_thumbprint_error() -> None:
    entry = CertificateTrustEntry(thumbprint="")
    issues = entry.validate()
    assert any(i.code == "cert_trust_empty_thumbprint" for i in issues)
    assert any(i.severity == "error" for i in issues)


def test_cert_trust_entry_invalid_thumbprint_length_error() -> None:
    entry = CertificateTrustEntry(thumbprint="abc123")
    issues = entry.validate()
    assert any(i.code == "cert_trust_invalid_thumbprint" for i in issues)


def test_cert_trust_entry_invalid_thumbprint_non_hex_error() -> None:
    entry = CertificateTrustEntry(thumbprint="z" * 40)
    issues = entry.validate()
    assert any(i.code == "cert_trust_invalid_thumbprint" for i in issues)


def test_cert_trust_entry_expired_warning() -> None:
    entry = CertificateTrustEntry(
        thumbprint=_VALID_THUMBPRINT,
        not_after="2020-01-01T00:00:00",
    )
    issues = entry.validate()
    assert any(i.code == "cert_trust_expired" for i in issues)
    assert all(i.severity == "warning" for i in issues if i.code == "cert_trust_expired")


def test_cert_trust_entry_future_expiry_clean() -> None:
    entry = CertificateTrustEntry(
        thumbprint=_VALID_THUMBPRINT,
        not_after="2099-12-31",
    )
    assert all(i.code != "cert_trust_expired" for i in entry.validate())


def test_cert_trust_entry_uppercase_thumbprint_valid() -> None:
    entry = CertificateTrustEntry(thumbprint=_VALID_THUMBPRINT.upper())
    assert all(i.code != "cert_trust_invalid_thumbprint" for i in entry.validate())


# ---------------------------------------------------------------------------
# PublicKeyPolicy
# ---------------------------------------------------------------------------


def _valid_trusted_root() -> CertificateTrustEntry:
    return CertificateTrustEntry(thumbprint=_VALID_THUMBPRINT, subject="CN=Root CA")


def test_public_key_policy_no_trusted_roots_warning() -> None:
    policy = PublicKeyPolicy(
        trusted_roots=(),
        efs_recovery_agents=(_ALT_THUMBPRINT,),
    )
    issues = policy.validate()
    assert any(i.code == "pki_no_trusted_roots" for i in issues)
    assert all(i.severity == "warning" for i in issues if i.code == "pki_no_trusted_roots")


def test_public_key_policy_no_efs_recovery_agents_warning() -> None:
    policy = PublicKeyPolicy(
        trusted_roots=(_valid_trusted_root(),),
        efs_recovery_agents=(),
    )
    issues = policy.validate()
    assert any(i.code == "pki_no_efs_recovery_agents" for i in issues)


def test_public_key_policy_disallowed_in_trusted_error() -> None:
    entry = CertificateTrustEntry(thumbprint=_VALID_THUMBPRINT)
    policy = PublicKeyPolicy(
        trusted_roots=(entry,),
        disallowed=(CertificateTrustEntry(thumbprint=_VALID_THUMBPRINT),),
        efs_recovery_agents=(_ALT_THUMBPRINT,),
    )
    issues = policy.validate()
    assert any(i.code == "pki_cert_in_trust_and_disallow" for i in issues)
    assert any(i.severity == "error" for i in issues)


def test_public_key_policy_disallowed_not_in_trusted_clean() -> None:
    policy = PublicKeyPolicy(
        trusted_roots=(_valid_trusted_root(),),
        disallowed=(CertificateTrustEntry(thumbprint=_ALT_THUMBPRINT),),
        efs_recovery_agents=(_ALT_THUMBPRINT,),
    )
    assert all(i.code != "pki_cert_in_trust_and_disallow" for i in policy.validate())


def test_public_key_policy_valid_clean() -> None:
    policy = PublicKeyPolicy(
        trusted_roots=(_valid_trusted_root(),),
        efs_recovery_agents=(_ALT_THUMBPRINT,),
    )
    assert policy.validate() == ()


def test_public_key_policy_case_insensitive_thumbprint_conflict() -> None:
    entry = CertificateTrustEntry(thumbprint=_VALID_THUMBPRINT)
    policy = PublicKeyPolicy(
        trusted_roots=(entry,),
        disallowed=(
            CertificateTrustEntry(thumbprint=_VALID_THUMBPRINT.upper()),
        ),
        efs_recovery_agents=(_ALT_THUMBPRINT,),
    )
    issues = policy.validate()
    assert any(i.code == "pki_cert_in_trust_and_disallow" for i in issues)


# ---------------------------------------------------------------------------
# NetworkPolicy
# ---------------------------------------------------------------------------


def test_network_policy_valid() -> None:
    policy = NetworkPolicy(
        network_name="Corp WiFi",
        network_type="wireless",
        authentication="wpa2",
        encryption="aes",
    )
    assert policy.validate() == ()


def test_network_policy_empty_name_error() -> None:
    policy = NetworkPolicy(network_name="")
    issues = policy.validate()
    assert any(i.code == "network_empty_name" for i in issues)
    assert any(i.severity == "error" for i in issues)


def test_network_policy_unsecured_wireless_warning() -> None:
    policy = NetworkPolicy(
        network_name="Open WiFi",
        network_type="wireless",
        authentication="none",
    )
    issues = policy.validate()
    assert any(i.code == "network_unsecured_wireless" for i in issues)
    assert all(i.severity == "warning" for i in issues if i.code == "network_unsecured_wireless")


def test_network_policy_wired_no_auth_clean() -> None:
    policy = NetworkPolicy(
        network_name="Wired LAN",
        network_type="wired",
        authentication="none",
    )
    assert all(i.code != "network_unsecured_wireless" for i in policy.validate())


def test_network_policy_wep_warning() -> None:
    policy = NetworkPolicy(
        network_name="Legacy WiFi",
        network_type="wireless",
        authentication="wpa2",
        encryption="wep",
    )
    issues = policy.validate()
    assert any(i.code == "network_wep_weak_encryption" for i in issues)
    assert all(i.severity == "warning" for i in issues if i.code == "network_wep_weak_encryption")


def test_network_security_family_aggregates_issues() -> None:
    family = NetworkSecurityFamily(
        networks=(
            NetworkPolicy(network_name=""),
            NetworkPolicy(network_name="OK"),
        )
    )
    issues = family.validate()
    assert any(i.code == "network_empty_name" for i in issues)


# ---------------------------------------------------------------------------
# NetworkSecurityAssessment
# ---------------------------------------------------------------------------


def _clean_firewall() -> FirewallPolicy:
    """The measured Domain/Private profile tranche, which the codec accepts."""
    return FirewallPolicy(
        policy_version=545,
        domain=FirewallProfileSettings(enabled=True, default_inbound_action="block"),
        private=FirewallProfileSettings(enabled=True, default_inbound_action="block"),
    )


def _disabled_firewall() -> FirewallPolicy:
    """Explicitly off on every profile: unmeasured, so the codec refuses it."""
    off = FirewallProfileSettings(enabled=False)
    return FirewallPolicy(policy_version=545, domain=off, private=off, public=off)


def _clean_ipsec() -> IpsecPolicy:
    return IpsecPolicy(
        rules=(
            IpsecRule(
                name="Domain isolation",
                authentication="kerberos",
                encryption="aes256",
            ),
        )
    )


def _clean_pki() -> PublicKeyPolicy:
    return PublicKeyPolicy(
        trusted_roots=(_valid_trusted_root(),),
        efs_recovery_agents=(_ALT_THUMBPRINT,),
    )


def test_assessment_critical_firewall_off_no_ipsec() -> None:
    assessment = assess_network_security(
        _disabled_firewall(), IpsecPolicy(), _clean_pki(), NetworkSecurityFamily()
    )
    assert assessment.overall_risk == "critical"
    assert assessment.firewall_issues, "an explicit False is unmeasured and refused"


def test_assessment_unconfigured_profiles_are_not_disabled() -> None:
    """`None` means not configured (local default on), never "off"."""
    assessment = assess_network_security(
        FirewallPolicy(), IpsecPolicy(), _clean_pki(), NetworkSecurityFamily()
    )
    assert assessment.overall_risk == "low"


def test_assessment_critical_firewall_off_with_ipsec_not_critical() -> None:
    """Firewall disabled but IPsec present → not critical (IPsec mitigates)."""
    assessment = assess_network_security(
        _disabled_firewall(), _clean_ipsec(), _clean_pki(), NetworkSecurityFamily()
    )
    assert assessment.overall_risk != "critical"


def test_assessment_high_weak_ipsec_encryption() -> None:
    ipsec = IpsecPolicy(
        rules=(
            IpsecRule(name="Weak enc", encryption="des"),
        )
    )
    assessment = assess_network_security(
        _clean_firewall(), ipsec, _clean_pki(), NetworkSecurityFamily()
    )
    assert assessment.overall_risk == "high"
    assert any(i.code == "ipsec_weak_encryption" for i in assessment.ipsec_issues)


def test_assessment_high_weak_network_encryption() -> None:
    networks = NetworkSecurityFamily(
        networks=(
            NetworkPolicy(
                network_name="Legacy",
                network_type="wireless",
                authentication="wpa2",
                encryption="wep",
            ),
        )
    )
    assessment = assess_network_security(
        _clean_firewall(), _clean_ipsec(), _clean_pki(), networks
    )
    assert assessment.overall_risk == "high"


def test_assessment_high_error_present() -> None:
    firewall = FirewallPolicy(
        policy_version=545, rules=(FirewallRule("R1", "unmeasured", protocol=99),)
    )
    assessment = assess_network_security(
        firewall, _clean_ipsec(), _clean_pki(), NetworkSecurityFamily()
    )
    assert assessment.overall_risk == "high"


def test_assessment_medium_only_warnings() -> None:
    pki = PublicKeyPolicy(trusted_roots=(_valid_trusted_root(),))
    assessment = assess_network_security(
        _clean_firewall(), _clean_ipsec(), pki, NetworkSecurityFamily()
    )
    assert assessment.overall_risk == "medium"
    assert assessment.firewall_issues == ()


def test_assessment_low_all_clean() -> None:
    assessment = assess_network_security(
        _clean_firewall(), _clean_ipsec(), _clean_pki(), NetworkSecurityFamily()
    )
    assert assessment.overall_risk == "low"
    assert assessment.firewall_issues == ()
    assert assessment.ipsec_issues == ()
    assert assessment.pki_issues == ()
    assert assessment.network_issues == ()


# ---------------------------------------------------------------------------
# Round-trip: create → validate → assess
# ---------------------------------------------------------------------------


def test_round_trip_full_policy_assessment() -> None:
    firewall = FirewallPolicy(
        policy_version=545,
        domain=FirewallProfileSettings(enabled=True, default_inbound_action="block"),
        rules=(
            FirewallRule(
                "Allow-HTTPS",
                "Allow HTTPS",
                protocol=6,
                profiles=("domain",),
                local_port="443",
            ),
            FirewallRule(
                "Block-GRE", "Block GRE", direction="outbound", action="block", protocol=47
            ),
        ),
    )
    ipsec = IpsecPolicy(
        rules=(
            IpsecRule(
                name="Server isolation",
                mode="transport",
                authentication="certificate",
                encryption="aes256",
                remote_address="10.0.0.0/8",
            ),
        )
    )
    pki = PublicKeyPolicy(
        trusted_roots=(
            CertificateTrustEntry(
                thumbprint=_VALID_THUMBPRINT,
                subject="CN=Lab Root CA",
                issuer="CN=Lab Root CA",
                purpose="trust",
            ),
        ),
        auto_enrollment=(
            CertificateTrustEntry(
                thumbprint=_ALT_THUMBPRINT,
                subject="CN=Auto Enroll",
                purpose="auto_enrollment",
            ),
        ),
        efs_recovery_agents=(_VALID_THUMBPRINT,),
    )
    networks = NetworkSecurityFamily(
        networks=(
            NetworkPolicy(
                network_name="Corp LAN",
                network_type="wired",
                authentication="802.1x",
            ),
        )
    )

    fw_issues = firewall.validate()
    ipsec_issues = ipsec.validate()
    pki_issues = pki.validate()
    net_issues = networks.validate()

    assert fw_issues == ()
    assert ipsec_issues == ()
    assert pki_issues == ()
    assert net_issues == ()

    assessment = assess_network_security(firewall, ipsec, pki, networks)
    assert assessment.overall_risk == "low"
    assert assessment.firewall_issues == fw_issues
    assert assessment.ipsec_issues == ipsec_issues
    assert assessment.pki_issues == pki_issues
    assert assessment.network_issues == net_issues


def test_assessment_issues_are_typed() -> None:
    assessment = assess_network_security(
        _disabled_firewall(),
        IpsecPolicy(),
        PublicKeyPolicy(),
        NetworkSecurityFamily(),
    )
    for issue in (
        *assessment.firewall_issues,
        *assessment.ipsec_issues,
        *assessment.pki_issues,
        *assessment.network_issues,
    ):
        assert isinstance(issue, ValidationIssue)
