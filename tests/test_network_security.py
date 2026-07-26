"""Tests for network and public-key security families."""

from __future__ import annotations

from gpo_studio.model import ValidationIssue
from gpo_studio.network_security import (
    CertificateTrustEntry,
    FirewallPolicy,
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
# FirewallRule
# ---------------------------------------------------------------------------


def test_firewall_rule_valid() -> None:
    rule = FirewallRule(
        name="Allow HTTPS inbound",
        direction="inbound",
        action="allow",
        protocol="tcp",
        local_port="443",
        remote_address="10.0.0.0/8",
    )
    assert rule.validate() == ()


def test_firewall_rule_empty_name_error() -> None:
    rule = FirewallRule(name="")
    issues = rule.validate()
    assert any(i.code == "firewall_rule_empty_name" for i in issues)
    assert any(i.severity == "error" for i in issues)


def test_firewall_rule_invalid_port_range() -> None:
    rule = FirewallRule(
        name="Bad range",
        local_port="80-20",
    )
    issues = rule.validate()
    assert any(i.code == "firewall_rule_invalid_local_port" for i in issues)
    assert all(i.severity == "error" for i in issues if i.code.startswith("firewall_rule_invalid"))


def test_firewall_rule_invalid_port_non_numeric() -> None:
    rule = FirewallRule(
        name="Bad port",
        remote_port="abc",
    )
    issues = rule.validate()
    assert any(i.code == "firewall_rule_invalid_remote_port" for i in issues)


def test_firewall_rule_valid_port_range_and_list() -> None:
    rule = FirewallRule(
        name="Multi port",
        local_port="80,443,1024-2048",
        remote_port="*",
    )
    assert all(i.code != "firewall_rule_invalid_local_port" for i in rule.validate())
    assert all(i.code != "firewall_rule_invalid_remote_port" for i in rule.validate())


def test_firewall_rule_broad_inbound_allow_warning() -> None:
    rule = FirewallRule(
        name="Allow all inbound",
        direction="inbound",
        action="allow",
        remote_address="*",
    )
    issues = rule.validate()
    assert any(i.code == "firewall_broad_inbound_allow" for i in issues)
    assert all(i.severity == "warning" for i in issues if i.code == "firewall_broad_inbound_allow")


def test_firewall_rule_broad_inbound_allow_not_triggered_for_block() -> None:
    rule = FirewallRule(
        name="Block all inbound",
        direction="inbound",
        action="block",
        remote_address="*",
    )
    assert all(i.code != "firewall_broad_inbound_allow" for i in rule.validate())


def test_firewall_rule_broad_inbound_allow_not_triggered_for_outbound() -> None:
    rule = FirewallRule(
        name="Allow all outbound",
        direction="outbound",
        action="allow",
        remote_address="*",
    )
    assert all(i.code != "firewall_broad_inbound_allow" for i in rule.validate())


def test_firewall_rule_icmp_port_warning() -> None:
    rule = FirewallRule(
        name="ICMP rule with port",
        protocol="icmpv4",
        local_port="80",
    )
    issues = rule.validate()
    assert any(i.code == "firewall_icmp_port_ignored" for i in issues)
    assert all(i.severity == "warning" for i in issues if i.code == "firewall_icmp_port_ignored")


def test_firewall_rule_icmp_without_port_clean() -> None:
    rule = FirewallRule(
        name="ICMP rule clean",
        protocol="icmpv6",
    )
    assert all(i.code != "firewall_icmp_port_ignored" for i in rule.validate())


# ---------------------------------------------------------------------------
# FirewallPolicy
# ---------------------------------------------------------------------------


def test_firewall_policy_all_profiles_disabled_warning() -> None:
    policy = FirewallPolicy(
        domain_profile_enabled=False,
        private_profile_enabled=False,
        public_profile_enabled=False,
        logging_enabled=True,
    )
    issues = policy.validate()
    assert any(i.code == "firewall_all_profiles_disabled" for i in issues)
    assert all(
        i.severity == "warning"
        for i in issues
        if i.code == "firewall_all_profiles_disabled"
    )


def test_firewall_policy_permissive_default_warning() -> None:
    policy = FirewallPolicy(
        default_inbound_action="allow",
        logging_enabled=True,
    )
    issues = policy.validate()
    assert any(i.code == "firewall_permissive_default_inbound" for i in issues)


def test_firewall_policy_logging_disabled_warning() -> None:
    policy = FirewallPolicy(logging_enabled=False)
    issues = policy.validate()
    assert any(i.code == "firewall_logging_disabled" for i in issues)


def test_firewall_policy_valid_clean() -> None:
    policy = FirewallPolicy(
        domain_profile_enabled=True,
        private_profile_enabled=True,
        public_profile_enabled=True,
        default_inbound_action="block",
        logging_enabled=True,
    )
    assert policy.validate() == ()


def test_firewall_policy_aggregates_rule_issues() -> None:
    bad_rule = FirewallRule(name="")
    policy = FirewallPolicy(rules=(bad_rule,), logging_enabled=True)
    issues = policy.validate()
    assert any(i.code == "firewall_rule_empty_name" for i in issues)


def test_firewall_policy_rules_for_profile() -> None:
    domain_rule = FirewallRule(name="domain only", profiles=("domain",))
    public_rule = FirewallRule(name="public only", profiles=("public",))
    all_rule = FirewallRule(
        name="all profiles", profiles=("domain", "private", "public")
    )
    policy = FirewallPolicy(
        rules=(domain_rule, public_rule, all_rule), logging_enabled=True
    )

    domain_rules = policy.rules_for_profile("domain")
    assert {r.name for r in domain_rules} == {"domain only", "all profiles"}

    private_rules = policy.rules_for_profile("private")
    assert {r.name for r in private_rules} == {"all profiles"}

    public_rules = policy.rules_for_profile("public")
    assert {r.name for r in public_rules} == {"public only", "all profiles"}


def test_firewall_policy_rules_for_direction() -> None:
    inbound = FirewallRule(name="in", direction="inbound")
    outbound = FirewallRule(name="out", direction="outbound")
    policy = FirewallPolicy(rules=(inbound, outbound), logging_enabled=True)

    inbound_rules = policy.rules_for_direction("inbound")
    assert {r.name for r in inbound_rules} == {"in"}

    outbound_rules = policy.rules_for_direction("outbound")
    assert {r.name for r in outbound_rules} == {"out"}


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
    return FirewallPolicy(
        domain_profile_enabled=True,
        private_profile_enabled=True,
        public_profile_enabled=True,
        default_inbound_action="block",
        logging_enabled=True,
    )


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
    firewall = FirewallPolicy(
        domain_profile_enabled=False,
        private_profile_enabled=False,
        public_profile_enabled=False,
        logging_enabled=True,
    )
    ipsec = IpsecPolicy()
    assessment = assess_network_security(firewall, ipsec, _clean_pki(), NetworkSecurityFamily())
    assert assessment.overall_risk == "critical"
    assert any(i.code == "firewall_all_profiles_disabled" for i in assessment.firewall_issues)


def test_assessment_critical_firewall_off_with_ipsec_not_critical() -> None:
    """Firewall disabled but IPsec present → not critical (IPsec mitigates)."""
    firewall = FirewallPolicy(
        domain_profile_enabled=False,
        private_profile_enabled=False,
        public_profile_enabled=False,
        logging_enabled=True,
    )
    assessment = assess_network_security(
        firewall, _clean_ipsec(), _clean_pki(), NetworkSecurityFamily()
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
        rules=(FirewallRule(name=""),),
        domain_profile_enabled=True,
        private_profile_enabled=True,
        public_profile_enabled=True,
        default_inbound_action="block",
        logging_enabled=True,
    )
    assessment = assess_network_security(
        firewall, _clean_ipsec(), _clean_pki(), NetworkSecurityFamily()
    )
    assert assessment.overall_risk == "high"


def test_assessment_medium_only_warnings() -> None:
    firewall = FirewallPolicy(
        domain_profile_enabled=True,
        private_profile_enabled=True,
        public_profile_enabled=True,
        default_inbound_action="block",
        logging_enabled=False,
    )
    assessment = assess_network_security(
        firewall, _clean_ipsec(), _clean_pki(), NetworkSecurityFamily()
    )
    assert assessment.overall_risk == "medium"


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
        rules=(
            FirewallRule(
                name="Allow HTTPS",
                direction="inbound",
                action="allow",
                protocol="tcp",
                local_port="443",
                remote_address="10.0.0.0/8",
                profiles=("domain", "private"),
            ),
            FirewallRule(
                name="Block telemetry",
                direction="outbound",
                action="block",
                protocol="tcp",
                remote_port="443",
                remote_address="0.0.0.0/0",
                profiles=("domain", "private"),
            ),
        ),
        domain_profile_enabled=True,
        private_profile_enabled=True,
        public_profile_enabled=False,
        default_inbound_action="block",
        logging_enabled=True,
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

    # Firewall has public profile disabled (a warning) but is otherwise sound.
    assert all(i.severity != "error" for i in fw_issues)
    assert ipsec_issues == ()
    assert pki_issues == ()
    assert net_issues == ()

    assessment = assess_network_security(firewall, ipsec, pki, networks)
    assert assessment.overall_risk in ("medium", "low")
    assert assessment.firewall_issues == fw_issues
    assert assessment.ipsec_issues == ipsec_issues
    assert assessment.pki_issues == pki_issues
    assert assessment.network_issues == net_issues

    # rules_for_profile / rules_for_direction work on the constructed policy.
    domain_rules = firewall.rules_for_profile("domain")
    assert {r.name for r in domain_rules} == {"Allow HTTPS", "Block telemetry"}

    public_rules = firewall.rules_for_profile("public")
    assert public_rules == ()

    outbound_rules = firewall.rules_for_direction("outbound")
    assert {r.name for r in outbound_rules} == {"Block telemetry"}


def test_assessment_issues_are_typed() -> None:
    assessment = assess_network_security(
        FirewallPolicy(
            domain_profile_enabled=False,
            private_profile_enabled=False,
            public_profile_enabled=False,
        ),
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
