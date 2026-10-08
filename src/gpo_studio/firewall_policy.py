"""Capture-backed Windows Firewall Registry.pol codec (WS2025, v2.33).

Only the measured tranche is writable. Unknown records are returned separately;
unknown rule tokens retain their original positions but prevent emission. None
means an absent profile value, never a Windows default. This module has no web,
AD or SYSVOL access. Native capture is not a Studio write-lane verdict.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from typing import Literal, Never, assert_never, cast

from .model import RegistrySetting, RegistryType, ValidationIssue
from .registry_pol import PolRecord

FIREWALL_KEY = r"SOFTWARE\Policies\Microsoft\WindowsFirewall"
FIREWALL_TOOL_GUID = "{B05566AC-FE9C-4368-BE01-7A4CBB6CBA11}"
FirewallAction = Literal["allow", "block", "bypass"]
FirewallDirection = Literal["inbound", "outbound"]
FirewallProfile = Literal["domain", "private", "public"]


class FirewallValidationError(ValueError):
    """A measured-wire contract violation with machine-readable issues."""

    def __init__(self, issues: tuple[ValidationIssue, ...]) -> None:
        self.issues = issues
        super().__init__("; ".join(f"{i.code}: {i.message}" for i in issues))


def _issue(code: str, message: str, path: str = "FirewallPolicy") -> ValidationIssue:
    return ValidationIssue("error", "firewall_" + code, message, path)


def _fail(code: str, message: str) -> Never:
    raise FirewallValidationError((_issue(code, message),))


def _action_wire(action: FirewallAction) -> str:
    match action:
        case "allow":
            return "Allow"
        case "block":
            return "Block"
        case "bypass":
            return "ByPass"
        case _:
            assert_never(action)


def _direction_wire(direction: FirewallDirection) -> str:
    match direction:
        case "inbound":
            return "In"
        case "outbound":
            return "Out"
        case _:
            assert_never(direction)


def _profile_wire(profile: FirewallProfile) -> str:
    match profile:
        case "domain":
            return "Domain"
        case "private":
            return "Private"
        case "public":
            return "Public"
        case _:
            assert_never(profile)


@dataclass(frozen=True, slots=True)
class UnknownFirewallToken:
    """Zero-based position after v2.33, with the complete uninterpreted token."""

    position: int
    text: str


@dataclass(frozen=True, slots=True)
class FirewallRule:
    rule_id: str
    name: str
    direction: FirewallDirection = "inbound"
    action: FirewallAction = "allow"
    enabled: bool = True
    protocol: int | None = None  # None = Any, hence no Protocol token
    profiles: tuple[FirewallProfile, ...] = ()  # empty = Any, not three tokens
    local_port: str | None = None
    remote_port: int | None = None
    remote_port_range: tuple[int, int] | None = None
    icmp4: str | None = None
    local_address: str | None = None  # measured LA4 host form only
    remote_addresses: tuple[str, ...] = ()  # RA4 then RA6; CIDR accepted for v4
    program: str | None = None
    service: str | None = None
    interface_type: str | None = None  # measured wire vocabulary: Lan
    description: str | None = None
    group: str | None = None
    edge_traversal: bool | None = None  # absent differs from unmeasured Edge=FALSE
    remote_machine: str | None = None
    security: str | None = None
    unknown_tokens: tuple[UnknownFirewallToken, ...] = ()

    def validate(self) -> tuple[ValidationIssue, ...]:
        try:
            _rule_tokens(self)
        except FirewallValidationError as error:
            return error.issues
        return ()


@dataclass(frozen=True, slots=True)
class FirewallProfileSettings:
    enabled: bool | None = None
    default_inbound_action: FirewallAction | None = None
    default_outbound_action: FirewallAction | None = None
    disable_notifications: bool | None = None
    log_dropped_packets: bool | None = None
    log_successful_connections: bool | None = None
    log_file_size_kb: int | None = None
    log_file_path: str | None = None


@dataclass(frozen=True, slots=True)
class FirewallPolicy:
    policy_version: int | None = None
    domain: FirewallProfileSettings = field(default_factory=FirewallProfileSettings)
    private: FirewallProfileSettings = field(default_factory=FirewallProfileSettings)
    public: FirewallProfileSettings = field(default_factory=FirewallProfileSettings)
    rules: tuple[FirewallRule, ...] = ()

    def validate(self) -> tuple[ValidationIssue, ...]:
        try:
            to_registry_settings(self)
        except FirewallValidationError as error:
            return error.issues
        return ()


@dataclass(frozen=True, slots=True)
class FirewallParseResult:
    policy: FirewallPolicy
    unrecognised_records: tuple[PolRecord | RegistrySetting, ...] = ()


def _safe_text(value: str, field_name: str) -> str:
    if not value or any(c in value for c in "|\0\r\n"):
        _fail("invalid_text", f"{field_name} must be nonempty and contain no wire delimiters")
    return value


def _address(value: str, family: int, subnet: bool) -> str:
    if value == "LocalSubnet" and subnet:
        return value
    try:
        if "/" in value and subnet:
            network = ipaddress.ip_network(value, strict=True)
            if network.version != family:
                raise ValueError("address family mismatch")
            if isinstance(network, ipaddress.IPv4Network):
                return f"{network.network_address}/{network.netmask}"
            return str(network)
        address = ipaddress.ip_address(value)
        if address.version != family:
            raise ValueError("address family mismatch")
        # Only LA4 host, RA4 subnet and RA6 subnet were measured.
        if subnet:
            raise ValueError("remote host form is unmeasured")
        return str(address)
    except ValueError as error:
        _fail("unmeasured_address", str(error))


# Relative token order is known only for these native shapes. Refuse new
# combinations rather than inventing Windows' ordering between unseen pairs.
_MEASURED_RULE_SHAPES: frozenset[tuple[str, ...]] = frozenset(
    tuple(shape.split())
    for shape in (
        "Action Active Dir Protocol Profile LPort Name",
        "Action Active Dir Protocol Profile Profile RPort2_10 RPort Name",
        "Action Active Dir Protocol ICMP4 Name",
        "Action Active Dir Protocol Name",
        "Action Active Dir LA4 RA4 RA6 Name",
        "Action Active Dir Protocol RPort App Name",
        "Action Active Dir Protocol LPort Svc Name",
        "Action Active Dir Protocol LPort RA4 RA6 Name",
        "Action Active Dir Protocol LPort Name",
        "Action Active Dir Protocol LPort Name Desc EmbedCtxt",
        "Action Active Dir Protocol LPort IFType Name Edge",
        "Action Active Dir Protocol LPort Name RMauth Security",
    )
)


def _rule_tokens(rule: FirewallRule) -> list[str]:
    if rule.unknown_tokens:
        _fail(
            "unknown_tokens",
            "Unknown tokens are preserved on read and require measurement to write",
        )
    _safe_text(rule.rule_id, "rule_id")
    if any(c in rule.rule_id for c in "\\;"):
        _fail("invalid_rule_id", "Rule identity contains registry delimiters")
    # Runtime validation precedes exhaustive typed dispatch (dataclass callers
    # can bypass annotations). New Literal variants still fail assert_never.
    if rule.action not in ("allow", "block", "bypass"):
        _fail("unmeasured_action", "Action is outside the measured vocabulary")
    if rule.direction not in ("inbound", "outbound"):
        _fail("unmeasured_direction", "Direction is outside the measured vocabulary")
    if type(rule.enabled) is not bool:
        _fail("invalid_active", "Active must be boolean")
    tokens = [
        f"Action={_action_wire(rule.action)}",
        f"Active={'TRUE' if rule.enabled else 'FALSE'}",
        f"Dir={_direction_wire(rule.direction)}",
    ]
    if rule.protocol is not None:
        if type(rule.protocol) is not int or rule.protocol not in (6, 17, 1, 58, 47):
            _fail("unmeasured_protocol", "Only 6, 17, 1, 58, 47 and absent Any are measured")
        tokens.append(f"Protocol={rule.protocol}")
    if tuple(rule.profiles) not in ((), ("domain",), ("domain", "private")):
        _fail("unmeasured_profiles", "Only Any, Domain and Domain+Private were measured")
    tokens.extend(f"Profile={_profile_wire(p)}" for p in rule.profiles)
    if rule.local_port is not None:
        if rule.protocol not in (6, 17):
            _fail("port_protocol", "Port tokens require measured TCP/UDP protocol")
        if rule.local_port in ("RPC", "RPC-EPMap"):
            if rule.protocol != 6:
                _fail("keyword_protocol", "Measured RPC keywords require TCP")
        elif not re.fullmatch(r"[0-9]+", rule.local_port) or not 1 <= int(rule.local_port) <= 65535:
            _fail("unmeasured_local_port", "Only a single port, RPC or RPC-EPMap is measured")
        tokens.append(f"LPort={rule.local_port}")
    if (
        rule.remote_port_range is not None or rule.remote_port is not None
    ) and rule.protocol not in (6, 17):
        _fail("port_protocol", "Port tokens require measured TCP/UDP protocol")
    if rule.remote_port_range is not None:
        low, high = rule.remote_port_range
        if type(low) is not int or type(high) is not int or not 1 <= low < high <= 65535:
            _fail("invalid_remote_range", "Range requires two ascending ports from 1 to 65535")
        tokens.append(f"RPort2_10={low}-{high}")
    if rule.remote_port is not None:
        if type(rule.remote_port) is not int or not 1 <= rule.remote_port <= 65535:
            _fail("invalid_remote_port", "Single remote port must be from 1 to 65535")
        tokens.append(f"RPort={rule.remote_port}")
    if rule.icmp4 is not None:
        if rule.protocol != 1 or rule.icmp4 != "8:0":
            _fail("unmeasured_icmp", "Only ICMPv4 echo type/code 8:0 is measured")
        tokens.append("ICMP4=8:0")
    if rule.local_address is not None:
        tokens.append("LA4=" + _address(rule.local_address, 4, False))
    families: set[int] = set()
    addresses: list[tuple[int, str]] = []
    for address in rule.remote_addresses:
        if address == "LocalSubnet":
            if len(rule.remote_addresses) != 1:
                _fail("unmeasured_address_list", "LocalSubnet must be the only address")
            addresses.extend(((4, address), (6, address)))
            continue
        family = 6 if ":" in address else 4
        if family in families:
            _fail("unmeasured_address_list", "Multiple addresses in one family are unmeasured")
        families.add(family)
        addresses.append((family, _address(address, family, True)))
    tokens.extend(f"RA{family}={value}" for family, value in sorted(addresses))
    for key, value in (("App", rule.program), ("Svc", rule.service)):
        if value is not None:
            tokens.append(f"{key}={_safe_text(value, key)}")
    if rule.interface_type is not None:
        if rule.interface_type != "Lan":
            _fail("unmeasured_interface", "Only IFType=Lan was measured")
        tokens.append("IFType=Lan")
    tokens.append("Name=" + _safe_text(rule.name, "name"))
    for key, value in (("Desc", rule.description), ("EmbedCtxt", rule.group)):
        if value is not None:
            tokens.append(f"{key}={_safe_text(value, key)}")
    if rule.edge_traversal is not None:
        if rule.edge_traversal is not True:
            _fail("unmeasured_edge", "Only absent Edge or Edge=TRUE was measured")
        tokens.append("Edge=TRUE")
    if rule.action == "bypass":
        if rule.remote_machine != "D:(A;;CC;;;WD)" or rule.security != "Authenticate":
            _fail(
                "unmeasured_security", "Bypass requires the measured authentication and RMauth SDDL"
            )
        tokens.extend(("RMauth=" + rule.remote_machine, "Security=Authenticate"))
    elif rule.remote_machine is not None or rule.security is not None:
        _fail("unmeasured_security", "Security tokens were measured only with ByPass")
    if tuple(token.partition("=")[0] for token in tokens) not in _MEASURED_RULE_SHAPES:
        _fail(
            "unmeasured_token_combination", "This token combination/order has no native measurement"
        )
    return tokens


# Registry name, dataclass field, Logging subkey, registry type.
_PROFILE_FIELDS: tuple[tuple[str, str, bool, RegistryType], ...] = (
    ("EnableFirewall", "enabled", False, "REG_DWORD"),
    ("DisableNotifications", "disable_notifications", False, "REG_DWORD"),
    ("DefaultInboundAction", "default_inbound_action", False, "REG_DWORD"),
    ("DefaultOutboundAction", "default_outbound_action", False, "REG_DWORD"),
    ("LogDroppedPackets", "log_dropped_packets", True, "REG_DWORD"),
    ("LogSuccessfulConnections", "log_successful_connections", True, "REG_DWORD"),
    ("LogFileSize", "log_file_size_kb", True, "REG_DWORD"),
    ("LogFilePath", "log_file_path", True, "REG_SZ"),
)


def _setting(key: str, name: str, kind: RegistryType, value: str | int) -> RegistrySetting:
    return RegistrySetting(
        "firewall:" + key + ":" + name, "computer", "HKLM", key, name, kind, value
    )


def _profile_records(
    profile: FirewallProfile, settings: FirewallProfileSettings
) -> list[RegistrySetting]:
    if profile == "public" and settings != FirewallProfileSettings():
        _fail("unmeasured_public_settings", "Public profile was only measured unconfigured")
    result = []
    key = FIREWALL_KEY + "\\" + _profile_wire(profile) + "Profile"
    for name, attr, logging, kind in _PROFILE_FIELDS:
        value = getattr(settings, attr)
        if value is None:
            continue
        wire: str | int
        if attr in ("default_inbound_action", "default_outbound_action"):
            expected = "block" if attr == "default_inbound_action" else "allow"
            if value != expected:
                _fail("unmeasured_profile_action", f"{attr} was only measured as {expected}")
            wire = 1 if value == "block" else 0
        elif attr == "log_file_path":
            if not isinstance(value, str):
                _fail("invalid_log_path", "Log path must be a string")
            wire = _safe_text(value, attr)
        elif attr == "log_file_size_kb":
            if type(value) is not int or value != 8192:
                _fail("unmeasured_log_size", "Only LogFileSize=8192 was measured")
            wire = value
        else:
            if type(value) is not bool:
                _fail("invalid_profile_boolean", f"{attr} must be bool or None")
            if attr in ("enabled", "disable_notifications") and value is not True:
                _fail("unmeasured_profile_boolean", f"{attr}=false was not measured")
            wire = int(value)
        result.append(_setting(key + ("\\Logging" if logging else ""), name, kind, wire))
    return result


def to_registry_settings(policy: FirewallPolicy) -> list[RegistrySetting]:
    """Emit measured machine policy, refusing unsupported wire features."""
    result: list[RegistrySetting] = []
    if policy.policy_version is not None:
        if type(policy.policy_version) is not int or policy.policy_version != 545:
            _fail("unmeasured_policy_version", "Only PolicyVersion=545 was measured")
        result.append(_setting(FIREWALL_KEY, "PolicyVersion", "REG_DWORD", 545))
    elif policy.rules or any(
        p != FirewallProfileSettings() for p in (policy.domain, policy.private, policy.public)
    ):
        _fail("missing_policy_version", "Configured firewall policy requires PolicyVersion=545")
    result.extend(_profile_records("domain", policy.domain))
    identities: set[str] = set()
    for rule in policy.rules:
        ident = rule.rule_id.casefold()
        if ident in identities:
            _fail("duplicate_rule", "Rule names must be unique ignoring case")
        identities.add(ident)
        data = "v2.33|" + "|".join(_rule_tokens(rule)) + "|"
        result.append(_setting(FIREWALL_KEY + "\\FirewallRules", rule.rule_id, "REG_SZ", data))
    result.extend(_profile_records("private", policy.private))
    result.extend(_profile_records("public", policy.public))
    return result


def _parse_rule(rule_id: str, text: str) -> FirewallRule:
    parts = text.split("|")
    if parts[0] != "v2.33" or parts[-1] != "":
        _fail("unmeasured_rule_version", "Rules require v2.33 and a trailing pipe")
    fields: dict[str, str] = {}
    profiles: list[FirewallProfile] = []
    unknown: list[UnknownFirewallToken] = []
    known = {
        "Action",
        "Active",
        "Dir",
        "Protocol",
        "LPort",
        "RPort",
        "RPort2_10",
        "ICMP4",
        "LA4",
        "RA4",
        "RA6",
        "App",
        "Svc",
        "IFType",
        "Name",
        "Desc",
        "EmbedCtxt",
        "Edge",
        "RMauth",
        "Security",
    }
    for position, token in enumerate(parts[1:-1]):
        key, sep, value = token.partition("=")
        if not sep or not value:
            _fail("malformed_token", "Token must have a key and nonempty value")
        if key == "Profile":
            match value:
                case "Domain":
                    profiles.append("domain")
                case "Private":
                    profiles.append("private")
                case _:
                    _fail("unmeasured_profiles", "Profile token is unmeasured")
        elif key not in known:
            unknown.append(UnknownFirewallToken(position, token))
        elif key in fields:
            _fail("duplicate_token", f"Duplicate {key} token")
        else:
            fields[key] = value
    if not {"Action", "Active", "Dir", "Name"} <= fields.keys():
        _fail("missing_rule_token", "Action, Active, Dir and Name are required")
    action: FirewallAction
    match fields["Action"]:
        case "Allow":
            action = "allow"
        case "Block":
            action = "block"
        case "ByPass":
            action = "bypass"
        case _:
            _fail("unmeasured_action", "Action token is unmeasured")
    direction: FirewallDirection
    match fields["Dir"]:
        case "In":
            direction = "inbound"
        case "Out":
            direction = "outbound"
        case _:
            _fail("unmeasured_direction", "Dir token is unmeasured")
    if fields["Active"] not in ("TRUE", "FALSE"):
        _fail("invalid_active", "Active token must be TRUE or FALSE")
    if "Edge" in fields and fields["Edge"] != "TRUE":
        _fail("unmeasured_edge", "Only Edge=TRUE was measured")
    try:
        protocol = int(fields["Protocol"]) if "Protocol" in fields else None
        port = int(fields["RPort"]) if "RPort" in fields else None
        port_range = None
        if "RPort2_10" in fields:
            low, high = fields["RPort2_10"].split("-")
            port_range = (int(low), int(high))
    except ValueError as error:
        raise FirewallValidationError(
            (_issue("invalid_number", "Invalid numeric token"),)
        ) from error
    addresses = tuple(fields[k] for k in ("RA4", "RA6") if k in fields)
    if addresses == ("LocalSubnet", "LocalSubnet"):
        addresses = ("LocalSubnet",)
    elif "LocalSubnet" in addresses:
        _fail("unmeasured_address", "LocalSubnet requires both RA4 and RA6")
    for key, family in (("RA4", 4), ("RA6", 6)):
        if key in fields:
            _address(fields[key], family, True)
    rule = FirewallRule(
        rule_id=rule_id,
        name=fields["Name"],
        direction=direction,
        action=action,
        enabled=fields["Active"] == "TRUE",
        protocol=protocol,
        profiles=tuple(profiles),
        local_port=fields.get("LPort"),
        remote_port=port,
        remote_port_range=port_range,
        icmp4=fields.get("ICMP4"),
        local_address=fields.get("LA4"),
        remote_addresses=addresses,
        program=fields.get("App"),
        service=fields.get("Svc"),
        interface_type=fields.get("IFType"),
        description=fields.get("Desc"),
        group=fields.get("EmbedCtxt"),
        edge_traversal=True if "Edge" in fields else None,
        remote_machine=fields.get("RMauth"),
        security=fields.get("Security"),
        unknown_tokens=tuple(unknown),
    )
    canonical = _rule_tokens(replace(rule, unknown_tokens=()))
    observed = [t for t in parts[1:-1] if t.partition("=")[0] in known | {"Profile"}]
    if observed != canonical:
        _fail(
            "unmeasured_token_form",
            "Known tokens differ from measured order or canonical wire form",
        )
    return rule


def from_registry_records(records: Iterable[PolRecord | RegistrySetting]) -> FirewallParseResult:
    """Read machine records; never consume unrelated or unknown registry records."""
    profile_values: dict[str, dict[str, object]] = {p: {} for p in ("domain", "private", "public")}
    rules: list[FirewallRule] = []
    unrecognised: list[PolRecord | RegistrySetting] = []
    version: int | None = None
    seen: set[tuple[str, str]] = set()
    for record in records:
        if isinstance(record, RegistrySetting) and (
            record.side != "computer" or record.hive != "HKLM"
        ):
            unrecognised.append(record)
            continue
        identity = (record.key.casefold(), record.value_name.casefold())
        if not (
            identity[0] == FIREWALL_KEY.casefold()
            or identity[0].startswith(FIREWALL_KEY.casefold() + "\\")
        ):
            unrecognised.append(record)
            continue
        if identity in seen:
            _fail("duplicate_record", "Duplicate registry identity")
        seen.add(identity)
        if record.action != "set":
            _fail("unmeasured_record_action", "Firewall deletion markers were not measured")
        key = record.key.casefold()
        if key == FIREWALL_KEY.casefold() and record.value_name.casefold() == "policyversion":
            if (
                record.registry_type != "REG_DWORD"
                or type(record.value) is not int
                or record.value != 545
            ):
                _fail("unmeasured_policy_version", "PolicyVersion must be REG_DWORD 545")
            version = record.value
        elif key == (FIREWALL_KEY + "\\FirewallRules").casefold():
            if record.registry_type != "REG_SZ" or not isinstance(record.value, str):
                _fail("invalid_rule_type", "Firewall rules must be REG_SZ")
            rules.append(_parse_rule(record.value_name, record.value))
        else:
            matched = False
            for profile in ("domain", "private", "public"):
                base = FIREWALL_KEY + "\\" + profile + "Profile"
                for name, attr, logging, kind in _PROFILE_FIELDS:
                    expected_key = (base + ("\\Logging" if logging else "")).casefold()
                    if key != expected_key or record.value_name.casefold() != name.casefold():
                        continue
                    matched = True
                    if record.registry_type != kind:
                        _fail("invalid_profile_type", f"{name} requires {kind}")
                    value = record.value
                    if attr == "log_file_path":
                        if not isinstance(value, str):
                            _fail("invalid_log_path", "LogFilePath requires a string")
                    elif type(value) is not int:
                        _fail("invalid_profile_type", f"{name} requires integer data")
                    elif attr == "log_file_size_kb":
                        pass
                    elif attr in ("default_inbound_action", "default_outbound_action"):
                        if value not in (0, 1):
                            _fail("unmeasured_profile_action", "Unknown profile action code")
                        value = "block" if value == 1 else "allow"
                    else:
                        if value not in (0, 1):
                            _fail("invalid_profile_boolean", "Boolean DWORD must be 0 or 1")
                        value = bool(value)
                    profile_values[profile][attr] = value
            if not matched:
                unrecognised.append(record)

    # Each field has been checked against its registry type and wire vocabulary.
    def settings(profile: str) -> FirewallProfileSettings:
        values = profile_values[profile]
        return FirewallProfileSettings(
            enabled=cast(bool | None, values.get("enabled")),
            default_inbound_action=cast(
                FirewallAction | None, values.get("default_inbound_action")
            ),
            default_outbound_action=cast(
                FirewallAction | None, values.get("default_outbound_action")
            ),
            disable_notifications=cast(bool | None, values.get("disable_notifications")),
            log_dropped_packets=cast(bool | None, values.get("log_dropped_packets")),
            log_successful_connections=cast(bool | None, values.get("log_successful_connections")),
            log_file_size_kb=cast(int | None, values.get("log_file_size_kb")),
            log_file_path=cast(str | None, values.get("log_file_path")),
        )

    policy = FirewallPolicy(
        version, settings("domain"), settings("private"), settings("public"), tuple(rules)
    )
    # Unknown rule tokens are retained, so validate only the measured projection.
    to_registry_settings(replace(policy, rules=tuple(replace(r, unknown_tokens=()) for r in rules)))
    return FirewallParseResult(policy, tuple(unrecognised))
