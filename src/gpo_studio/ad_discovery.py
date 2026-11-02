"""Generate AD discovery PowerShell scripts and parse their JSON output."""

from __future__ import annotations

import ipaddress
import re
from typing import Any, cast

from .emit_integrity import DISCOVERY_PARAMETERS, ps_literal
from .identity_values import guid_problem, sid_problem
from .model import (
    DomainInfo,
    ForestInfo,
    GPOLink,
    OrganizationalUnit,
    PrincipalInfo,
    ResolutionState,
    SiteInfo,
    SubnetInfo,
    TrustDirection,
    TrustInfo,
    TrustType,
    ValidationError,
    ValidationIssue,
)

_MAX_DISCOVERY_ITEM_COUNT = 10000
_MAX_JSON_NESTING_DEPTH = 64
_MAX_PRINCIPAL_RESULTS = 1000
_MAX_OU_RESULTS = 10000
_MAX_GPO_RESULTS = 10000
_MAX_SITE_RESULTS = 1000
_MAX_SUBNET_RESULTS = 1000

_DISCOVERY_KINDS = frozenset({
    "gpo-studio-discovery-forest",
    "gpo-studio-discovery-domain",
    "gpo-studio-discovery-sites",
    "gpo-studio-discovery-ous",
    "gpo-studio-discovery-gpos",
    "gpo-studio-discovery-principals",
})


# PowerShell shared by the scripts that read directory search results.
#
# A missing attribute is an EMPTY ResultPropertyValueCollection, not $null, and
# PowerShell treats that object as true: ``if ($_.Properties['x']) { ...[0] }``
# took the true branch and printed null (#148 live review: netbios_name,
# display_name). Windows PowerShell 5.1 also cannot index that collection with
# -1, so object_class was always empty. These read by Count and by an explicit
# last index. Neither is used for objectGUID: a function returning a byte[]
# would unroll it.
#
# ConvertTo-LdapFilterValue escapes an assertion value per RFC 4515 section 3:
# '*', '(', ')', '\' and NUL become \2a, \28, \29, \5c and \00.
_PS_VALUE_HELPERS = [
    "function Get-FirstValue($values) {",
    "    if ($null -ne $values -and $values.Count -gt 0) { [string]$values[0] } else { '' }",
    "}",
    "function Get-LastValue($values) {",
    "    if ($null -ne $values -and $values.Count -gt 0) "
    "{ [string]$values[$values.Count - 1] } else { '' }",
    "}",
    "function ConvertTo-LdapFilterValue([string]$value) {",
    "    $sb = New-Object System.Text.StringBuilder",
    "    foreach ($c in $value.ToCharArray()) {",
    "        if ($c -eq [char]0x2a -or $c -eq [char]0x28 -or $c -eq [char]0x29 "
    "-or $c -eq [char]0x5c -or $c -eq [char]0) {",
    "            [void]$sb.AppendFormat('\\{0:x2}', [int]$c)",
    "        } else { [void]$sb.Append($c) }",
    "    }",
    "    $sb.ToString()",
    "}",
]


def _ps_param(value: str, parameter: str) -> str:
    """A ``param()`` default, through the one PowerShell quoting primitive.

    This module used to double only the ASCII ``'``; a typographic quote in the
    domain closed the literal and the rest ran as code when the script was run
    (DeepSeek final-r9 B1, executed under pwsh).
    """
    return ps_literal(
        value, parameter, table=DISCOVERY_PARAMETERS, where="discovery script"
    )


def _join_lines(lines: list[str]) -> str:
    return "\n".join(lines) + "\n"


def _check_nesting_depth(obj: Any, depth: int = 0, count: list[int] | None = None) -> None:
    if count is None:
        count = [0]
    count[0] += 1
    if count[0] > _MAX_DISCOVERY_ITEM_COUNT:
        raise ValidationError([
            ValidationIssue(
                severity="error",
                code="too_many_items",
                message=f"Discovery JSON exceeds {_MAX_DISCOVERY_ITEM_COUNT} total nodes.",
                path="",
            )
        ])
    if depth > _MAX_JSON_NESTING_DEPTH:
        raise ValidationError([
            ValidationIssue(
                severity="error",
                code="json_nesting_too_deep",
                message=f"JSON nesting depth exceeds {_MAX_JSON_NESTING_DEPTH}.",
                path="",
            )
        ])
    if isinstance(obj, dict):
        for v in obj.values():
            _check_nesting_depth(v, depth + 1, count)
    elif isinstance(obj, list):
        for item in obj:
            _check_nesting_depth(item, depth + 1, count)


def _require_kind(data: dict[str, Any], expected: str) -> None:
    kind = data.get("kind")
    if kind != expected:
        raise ValidationError([
            ValidationIssue(
                severity="error",
                code="invalid_discovery_kind",
                message=f"Discovery JSON must have kind {expected!r}, got {kind!r}.",
                path="kind",
            )
        ])


def _text(value: object, name: str) -> str:
    # str() turned null into the text "None" and a list into "[...]" (#148
    # live review): only a JSON string is text.
    if not isinstance(value, str):
        raise ValidationError([
            ValidationIssue(
                severity="error",
                code="invalid_string_field",
                message=f"Field {name!r} must be a string.",
                path=name,
            )
        ])
    return value


def _str_field(data: dict[str, Any], name: str) -> str:
    value = data.get(name)
    if value is None:
        raise ValidationError([
            ValidationIssue(
                severity="error",
                code="missing_field",
                message=f"Missing required field {name!r}.",
                path=name,
            )
        ])
    return _text(value, name)


def _optional_str(data: dict[str, Any], name: str, default: str = "") -> str:
    """An optional text field: absent or null is ``default``, never "None"."""
    value = data.get(name)
    return default if value is None else _text(value, name)


def _str_list(data: dict[str, Any], name: str) -> tuple[str, ...]:
    value = data.get(name)
    if not isinstance(value, list):
        raise ValidationError([
            ValidationIssue(
                severity="error",
                code="invalid_list_field",
                message=f"Field {name!r} must be a list.",
                path=name,
            )
        ])
    return tuple(_text(item, name) for item in value)


def _validate_guid(value: str, path: str) -> None:
    if guid_problem(value) is not None:
        raise ValidationError([
            ValidationIssue(
                severity="error",
                code="invalid_guid_format",
                message=f"Invalid GUID format: {value!r}",
                path=path,
            )
        ])


def _validate_sid(value: str, path: str) -> None:
    if sid_problem(value) is not None or not value.startswith("S-1-5-"):
        raise ValidationError([
            ValidationIssue(
                severity="error",
                code="invalid_sid_format",
                message=f"Invalid SID format: {value!r}",
                path=path,
            )
        ])


_IPV4_ADDRESS = re.compile(r"(0|[1-9][0-9]{0,2})(\.(0|[1-9][0-9]{0,2})){3}")
_PREFIX = re.compile(r"[1-9][0-9]{0,2}")


def _canonical_ipv6(address: ipaddress.IPv6Address) -> str:
    """RFC 5952 section 4 text, in hexadecimal only.

    Leading zeros dropped, "::" replacing the longest run of two or more zero
    fields (the first of equal runs), never a single zero field, lower case.
    Written out here rather than taken from ``str()``, which prints an
    IPv4-mapped address in dotted form.
    """
    fields = [(int(address) >> (16 * (7 - i))) & 0xFFFF for i in range(8)]
    best_start, best_length, start = -1, 1, None
    for index, field in enumerate([*fields, 1]):
        if field == 0 and start is None:
            start = index
        elif field != 0 and start is not None:
            if index - start > best_length:
                best_start, best_length = start, index - start
            start = None
    text = [format(field, "x") for field in fields]
    if best_start < 0:
        return ":".join(text)
    head = ":".join(text[:best_start])
    tail = ":".join(text[best_start + best_length:])
    return f"{head}::{tail}"


def _subnet_name(value: str, path: str) -> str:
    """An AD subnet object name, per MS-ADTS "Subnet Object"
    (learn.microsoft.com/openspecs/windows_protocols/ms-adts/255d4630-f033-4289-8db6-c36060b47e17).

    One "/"; a prefix with no leading zeros, 1-32 (IPv4) or 1-128 (IPv6). An
    IPv4 address in dotted decimal with no leading zeros, no host bits set,
    and not equal to its own mask. An IPv6 address in RFC 5952 canonical text,
    compared case-insensitively, with no host-bit rule (the spec lists
    ``a:b::c:d:0:0/64`` as valid) and so no zone ID or dotted form.

    Returns the name with an IPv6 address in lower case: AD compares names
    case-insensitively, so ``2001:DB8::/32`` and ``2001:db8::/32`` are one
    subnet and must be one cache key. IPv4 names are already unique.
    """
    def refuse() -> ValidationError:
        return ValidationError([
            ValidationIssue(
                severity="error",
                code="invalid_cidr_format",
                message=f"Invalid AD subnet name: {value!r}",
                path=path,
            )
        ])

    address_text, slash, prefix_text = value.partition("/")
    if not slash or "/" in prefix_text or not _PREFIX.fullmatch(prefix_text):
        raise refuse()
    prefix = int(prefix_text)
    if _IPV4_ADDRESS.fullmatch(address_text):
        if prefix > 32 or any(int(part) > 255 for part in address_text.split(".")):
            raise refuse()
        address = int(ipaddress.IPv4Address(address_text))
        mask = (0xFFFFFFFF << (32 - prefix)) & 0xFFFFFFFF
        if address & ~mask & 0xFFFFFFFF or address == mask:
            raise refuse()
        return value
    if prefix > 128 or "%" in address_text or "." in address_text:
        raise refuse()
    try:
        ipv6 = ipaddress.IPv6Address(address_text)
    except ValueError:
        raise refuse() from None
    canonical = _canonical_ipv6(ipv6)
    if address_text.lower() != canonical:
        raise refuse()
    return f"{canonical}/{prefix}"


def _parse_trust_direction(value: str) -> TrustDirection:
    if value in ("inbound", "outbound", "bidirectional", "unknown"):
        return cast(TrustDirection, value)
    raise ValidationError([
        ValidationIssue(
            severity="error",
            code="invalid_trust_direction",
            message=f"Invalid trust direction: {value!r}",
            path="trusts.direction",
        )
    ])


def _parse_trust_type(value: str) -> TrustType:
    if value in ("parent-child", "cross-link", "external", "forest", "unknown"):
        return cast(TrustType, value)
    raise ValidationError([
        ValidationIssue(
            severity="error",
            code="invalid_trust_type",
            message=f"Invalid trust type: {value!r}",
            path="trusts.trust_type",
        )
    ])


def _parse_resolution_state(value: str) -> ResolutionState:
    if value in ("resolved", "ambiguous", "deleted", "inaccessible", "stale"):
        return cast(ResolutionState, value)
    raise ValidationError([
        ValidationIssue(
            severity="error",
            code="invalid_resolution_state",
            message=f"Invalid resolution state: {value!r}",
            path="resolution_state",
        )
    ])


_MAX_LINK_ORDER = 2**31 - 1


def _bool_field(data: dict[str, Any], name: str, default: bool, path: str) -> bool:
    # bool("false") is True: only a JSON boolean is a boolean.
    value = data.get(name, default)
    if type(value) is not bool:
        raise ValidationError([
            ValidationIssue(
                severity="error",
                code="invalid_boolean_field",
                message=f"Field {name!r} must be true or false.",
                path=path,
            )
        ])
    return value


def _parse_gpo_link(raw: object) -> GPOLink:
    if not isinstance(raw, dict):
        raise ValidationError([
            ValidationIssue(
                severity="error",
                code="invalid_gpo_link_entry",
                message="Each GPO link must be a JSON object.",
                path="gpo_links",
            )
        ])
    order = raw.get("order", 1)
    # int() accepted "7", 7.9 and True, and raised on "NaN", null, [] or {}
    # (a 500, #148 review): the order is a JSON integer from 1.
    if type(order) is not int or not 1 <= order <= _MAX_LINK_ORDER:
        raise ValidationError([
            ValidationIssue(
                severity="error",
                code="invalid_gpo_link_order",
                message=f"GPO link order must be an integer from 1 to {_MAX_LINK_ORDER}.",
                path="gpo_links.order",
            )
        ])
    link_id = _optional_str(raw, "id")
    if not link_id:
        raise ValidationError([
            ValidationIssue(
                severity="error",
                code="missing_gpo_link_id",
                message="GPO link id is required.",
                path="gpo_links.id",
            )
        ])
    return GPOLink(
        id=link_id,
        target=_optional_str(raw, "target"),
        enabled=_bool_field(raw, "enabled", True, "gpo_links.enabled"),
        enforced=_bool_field(raw, "enforced", False, "gpo_links.enforced"),
        order=order,
    )


def discovery_script_forest() -> str:
    """Generate a PowerShell script that collects forest topology."""
    lines = [
        "# Generated by GPO Studio. Run on a domain-joined machine; output is JSON to stdout.",
        "$ErrorActionPreference = 'Stop'",
        "$forest = [System.DirectoryServices.ActiveDirectory.Forest]::GetCurrentForest()",
        "$domains = @($forest.Domains | ForEach-Object { $_.Name })",
        "$globalCatalogs = @($forest.GlobalCatalogs | ForEach-Object { $_.Name })",
        "$trusts = @($forest.GetAllTrustRelationships() | ForEach-Object {",
        # Inside a switch $_ is the switch value, not the trust.
        "    $trust = $_",
        "    $direction = switch ($trust.TrustDirection.ToString()) {",
        "        'Inbound' { 'inbound' }",
        "        'Outbound' { 'outbound' }",
        "        'Bidirectional' { 'bidirectional' }",
        "        default { 'unknown' }",
        "    }",
        "    $trustType = switch ($trust.TrustType.ToString()) {",
        "        'ParentChild' { 'parent-child' }",
        "        'CrossLink' { 'cross-link' }",
        "        'External' { 'external' }",
        "        'Forest' { 'forest' }",
        "        default { 'unknown' }",
        "    }",
        # Neither TrustRelationshipInformation type has a Transitive property,
        # so $_.Transitive was always null (#148 review). The trust type
        # decides it: parent-child, tree-root, shortcut (cross-link) and
        # forest trusts are transitive; an external trust never is. A
        # Kerberos realm trust carries it in trustAttributes, which this
        # script does not read, so it stops instead of guessing.
        "    $transitive = switch ($trust.TrustType.ToString()) {",
        "        'ParentChild' { $true }",
        "        'TreeRoot' { $true }",
        "        'CrossLink' { $true }",
        "        'Forest' { $true }",
        "        'External' { $false }",
        "        default { throw \"Cannot tell whether the $($trust.TrustType) trust to "
        "$($trust.TargetName) is transitive\" }",
        "    }",
        "    @{",
        "        source = $trust.SourceName",
        "        target = $trust.TargetName",
        "        direction = $direction",
        "        trust_type = $trustType",
        "        transitive = $transitive",
        "    }",
        "})",
        "$result = @{",
        "    kind = 'gpo-studio-discovery-forest'",
        "    name = $forest.Name",
        "    schema_master = $forest.SchemaRoleOwner.Name",
        "    domain_naming_master = $forest.NamingRoleOwner.Name",
        "    domains = $domains",
        "    global_catalogs = $globalCatalogs",
        "    trusts = $trusts",
        "}",
        "ConvertTo-Json -InputObject $result -Depth 10",
    ]
    return _join_lines(lines)


def parse_forest(data: dict[str, Any]) -> ForestInfo:
    """Parse PowerShell discovery JSON into ForestInfo."""
    _require_kind(data, "gpo-studio-discovery-forest")
    _check_nesting_depth(data)
    trusts_raw = data.get("trusts", [])
    if not isinstance(trusts_raw, list):
        raise ValidationError([
            ValidationIssue(
                severity="error",
                code="invalid_trusts_field",
                message="The 'trusts' field must be a list.",
                path="trusts",
            )
        ])
    trusts = tuple(parse_trust(item) for item in trusts_raw)
    return ForestInfo(
        name=_str_field(data, "name"),
        schema_master=_str_field(data, "schema_master"),
        domain_naming_master=_str_field(data, "domain_naming_master"),
        domains=_str_list(data, "domains"),
        global_catalogs=_str_list(data, "global_catalogs"),
        trusts=trusts,
    )


def parse_trust(data: dict[str, Any]) -> TrustInfo:
    """Parse a trust entry into TrustInfo."""
    if not isinstance(data, dict):
        raise ValidationError([
            ValidationIssue(
                severity="error",
                code="invalid_trust_entry",
                message="Each trust entry must be a JSON object.",
                path="trusts",
            )
        ])
    return TrustInfo(
        source=_str_field(data, "source"),
        target=_str_field(data, "target"),
        direction=_parse_trust_direction(_str_field(data, "direction")),
        trust_type=_parse_trust_type(_str_field(data, "trust_type")),
        transitive=_bool_field(data, "transitive", True, "trusts.transitive"),
    )


def discovery_script_domain(domain: str = "") -> str:
    """Generate a PowerShell script that collects domain info."""
    domain_param = _ps_param(domain, "Domain")
    lines = [
        "# Generated by GPO Studio. Run on a domain-joined machine; output is JSON to stdout.",
        f"param([string]$Domain = {domain_param})",
        "$ErrorActionPreference = 'Stop'",
        *_PS_VALUE_HELPERS,
        # $Domain is a [string] parameter: assigning the Domain object to it
        # converted it back to its name, so every property below was null
        # (#148 review). The object gets its own variable.
        "if ($Domain -eq '') { "
        "$adDomain = [System.DirectoryServices.ActiveDirectory.Domain]::GetCurrentDomain() }",
        "else {",
        "    $ctx = New-Object "
        "System.DirectoryServices.ActiveDirectory.DirectoryContext('Domain', $Domain)",
        "    $adDomain = [System.DirectoryServices.ActiveDirectory.Domain]::GetDomain($ctx)",
        "}",
        "$domainControllers = @($adDomain.DomainControllers | ForEach-Object { $_.Name })",
        "$root = $adDomain.GetDirectoryEntry()",
        "$domainDn = [string]$root.distinguishedName",
        # The NetBIOS name is not on the domain object: it is nETBIOSName on the
        # domain's crossRef in CN=Partitions of the configuration partition,
        # the entry whose nCName is the domain's DN (#148 live review).
        "$rootDse = [ADSI]\"LDAP://$($adDomain.Name)/RootDSE\"",
        "$configNc = [string]$rootDse.configurationNamingContext",
        "$searcher = New-Object System.DirectoryServices.DirectorySearcher("
        "[ADSI]\"LDAP://$($adDomain.Name)/CN=Partitions,$configNc\")",
        "$searcher.SearchScope = 'OneLevel'",
        "$searcher.Filter = \"(&(objectClass=crossRef)"
        "(nCName=$(ConvertTo-LdapFilterValue $domainDn)))\"",
        "$searcher.PropertiesToLoad.Add('nETBIOSName') | Out-Null",
        "$crossRef = $searcher.FindOne()",
        "$netbios = if ($crossRef) { Get-FirstValue $crossRef.Properties['nETBIOSName'] } "
        "else { '' }",
        # .NET's DomainMode has no Windows Server 2025 value and reports
        # Unknown for it. msDS-Behavior-Version 10 is Windows Server 2025
        # (MS-ADTS 6.1.4.2); any other level .NET cannot name is reported by
        # number rather than guessed.
        "$functionalLevel = $adDomain.DomainMode.ToString()",
        "if ($functionalLevel -eq 'Unknown') {",
        "    $levels = @($root.'msDS-Behavior-Version')",
        "    if ($levels.Count -gt 0) {",
        "        $level = [int]$levels[0]",
        "        $functionalLevel = if ($level -eq 10) { 'Windows2025Domain' } "
        "else { \"Level $level\" }",
        "    }",
        "}",
        "$result = @{",
        "    kind = 'gpo-studio-discovery-domain'",
        "    dns_name = $adDomain.Name",
        "    netbios_name = $netbios",
        "    domain_controllers = $domainControllers",
        "    pdc_emulator = $adDomain.PdcRoleOwner.Name",
        "    rid_master = $adDomain.RidRoleOwner.Name",
        "    infrastructure_master = $adDomain.InfrastructureRoleOwner.Name",
        "    functional_level = $functionalLevel",
        "}",
        "ConvertTo-Json -InputObject $result -Depth 10",
    ]
    return _join_lines(lines)


def parse_domain(data: dict[str, Any]) -> DomainInfo:
    """Parse PowerShell discovery JSON into DomainInfo."""
    _require_kind(data, "gpo-studio-discovery-domain")
    _check_nesting_depth(data)
    return DomainInfo(
        dns_name=_str_field(data, "dns_name"),
        netbios_name=_optional_str(data, "netbios_name"),
        domain_controllers=_str_list(data, "domain_controllers"),
        pdc_emulator=_str_field(data, "pdc_emulator"),
        rid_master=_str_field(data, "rid_master"),
        infrastructure_master=_str_field(data, "infrastructure_master"),
        functional_level=_str_field(data, "functional_level"),
    )


def discovery_script_sites() -> str:
    """Generate a PowerShell script that collects AD sites and subnets."""
    lines = [
        "# Generated by GPO Studio. Run on a domain-joined machine; output is JSON to stdout.",
        "$ErrorActionPreference = 'Stop'",
        "$forest = [System.DirectoryServices.ActiveDirectory.Forest]::GetCurrentForest()",
        "$sites = @($forest.Sites | ForEach-Object {",
        "    $site = $_",
        "    $subnets = @($site.Subnets | ForEach-Object { $_.Name })",
        "    @{",
        "        kind = 'gpo-studio-discovery-site'",
        "        name = $site.Name",
        "        description = ''",
        "        subnets = $subnets",
        "        site_links = @()",
        "    }",
        "})",
        "$subnets = @($forest.Sites | ForEach-Object { $_.Subnets } | ForEach-Object {",
        "    @{",
        "        kind = 'gpo-studio-discovery-subnet'",
        "        cidr = $_.Name",
        "        site_name = $_.Site.Name",
        "        description = ''",
        "    }",
        "})",
        "$result = @{",
        "    kind = 'gpo-studio-discovery-sites'",
        "    sites = $sites",
        "    subnets = $subnets",
        "}",
        "ConvertTo-Json -InputObject $result -Depth 10",
    ]
    return _join_lines(lines)


def parse_sites(data: dict[str, Any]) -> tuple[tuple[SiteInfo, ...], tuple[SubnetInfo, ...]]:
    """Parse PowerShell discovery JSON into SiteInfo and SubnetInfo tuples."""
    _require_kind(data, "gpo-studio-discovery-sites")
    _check_nesting_depth(data)
    sites_raw = data.get("sites", [])
    if not isinstance(sites_raw, list):
        raise ValidationError([
            ValidationIssue(
                severity="error",
                code="invalid_sites_field",
                message="The 'sites' field must be a list.",
                path="sites",
            )
        ])
    if len(sites_raw) > _MAX_SITE_RESULTS:
        raise ValidationError([
            ValidationIssue(
                severity="error",
                code="too_many_sites",
                message=f"Site discovery exceeds {_MAX_SITE_RESULTS} sites.",
                path="sites",
            )
        ])
    subnets_raw = data.get("subnets", [])
    if not isinstance(subnets_raw, list):
        raise ValidationError([
            ValidationIssue(
                severity="error",
                code="invalid_subnets_field",
                message="The 'subnets' field must be a list.",
                path="subnets",
            )
        ])
    if len(subnets_raw) > _MAX_SUBNET_RESULTS:
        raise ValidationError([
            ValidationIssue(
                severity="error",
                code="too_many_subnets",
                message=f"Subnet discovery exceeds {_MAX_SUBNET_RESULTS} subnets.",
                path="subnets",
            )
        ])
    sites = tuple(_parse_site(item) for item in sites_raw)
    subnets = tuple(_parse_subnet(item) for item in subnets_raw)
    return sites, subnets


def _parse_site(data: dict[str, Any]) -> SiteInfo:
    if not isinstance(data, dict):
        raise ValidationError([
            ValidationIssue(
                severity="error",
                code="invalid_site_entry",
                message="Each site entry must be a JSON object.",
                path="sites",
            )
        ])
    name = _str_field(data, "name")
    subnets_raw = data.get("subnets", [])
    if not isinstance(subnets_raw, list):
        raise ValidationError([
            ValidationIssue(
                severity="error",
                code="invalid_site_subnets",
                message="Site subnets must be a list.",
                path="sites.subnets",
            )
        ])
    site_subnets = tuple(
        _subnet_name(_text(cidr, "sites.subnets"), "sites.subnets") for cidr in subnets_raw
    )
    site_links_raw = data.get("site_links", [])
    if not isinstance(site_links_raw, list):
        raise ValidationError([
            ValidationIssue(
                severity="error",
                code="invalid_site_links",
                message="Site links must be a list.",
                path="sites.site_links",
            )
        ])
    return SiteInfo(
        name=name,
        description=_optional_str(data, "description"),
        subnets=site_subnets,
        site_links=_str_list(data, "site_links") if "site_links" in data else (),
    )


def _parse_subnet(data: dict[str, Any]) -> SubnetInfo:
    if not isinstance(data, dict):
        raise ValidationError([
            ValidationIssue(
                severity="error",
                code="invalid_subnet_entry",
                message="Each subnet entry must be a JSON object.",
                path="subnets",
            )
        ])
    cidr = _subnet_name(_str_field(data, "cidr"), "subnets.cidr")
    return SubnetInfo(
        cidr=cidr,
        site_name=_str_field(data, "site_name"),
        description=_optional_str(data, "description"),
    )


def discovery_script_ous(domain: str, search_base: str = "") -> str:
    """Generate a PowerShell script that collects OUs and their GPO links.

    The domain head is a scope of management too: its own links (the Default
    Domain Policy) were never collected because the search matched only OUs
    (#148 live review). It is emitted as one more entry.
    """
    domain_param = _ps_param(domain, "Domain")
    search_base_param = _ps_param(search_base, "SearchBase")
    lines = [
        "# Generated by GPO Studio. Run on a domain-joined machine; output is JSON to stdout.",
        "param(",
        f"    [string]$Domain = {domain_param},",
        f"    [string]$SearchBase = {search_base_param}",
        ")",
        "$ErrorActionPreference = 'Stop'",
        *_PS_VALUE_HELPERS,
        "$ctx = New-Object "
        "System.DirectoryServices.ActiveDirectory.DirectoryContext('Domain', $Domain)",
        "$adDomain = [System.DirectoryServices.ActiveDirectory.Domain]::GetDomain($ctx)",
        "$rootDn = [string]$adDomain.GetDirectoryEntry().distinguishedName",
        "if (-not $SearchBase) { $SearchBase = $rootDn }",
        "$searcher = New-Object System.DirectoryServices.DirectorySearcher([ADSI]\"LDAP://$SearchBase\")",
        "$searcher.Filter = "
        "'(|(objectCategory=organizationalUnit)(objectCategory=domainDNS))'",
        "$searcher.PageSize = 1000",
        "$searcher.PropertiesToLoad.Add('name') | Out-Null",
        "$searcher.PropertiesToLoad.Add('distinguishedName') | Out-Null",
        "$searcher.PropertiesToLoad.Add('description') | Out-Null",
        "$searcher.PropertiesToLoad.Add('gPLink') | Out-Null",
        "$results = $searcher.FindAll()",
        "$ous = @($results | ForEach-Object {",
        "    $dn = Get-FirstValue $_.Properties['distinguishedName']",
        "    $name = Get-FirstValue $_.Properties['name']",
        "    $description = Get-FirstValue $_.Properties['description']",
        "    $gplink = Get-FirstValue $_.Properties['gPLink']",
        # The whole value must be gPLink entries (GPMC leaves a single space
        # when the last link is removed). Matching fragments dropped a
        # malformed entry silently and renumbered the rest; an incomplete link
        # list is worse than no discovery, so the run stops (#164 review).
        r"    if ($gplink -notmatch '^\s*(\[LDAP://[^;\]]+;[0-9]{1,10}\]\s*)*$') {",
        "        throw \"Malformed gPLink on ${dn}: $gplink\"",
        "    }",
        r"    $linkMatches = @([regex]::Matches($gplink, '\[LDAP://([^;\]]+);([0-9]+)\]', "
        "'IgnoreCase'))",
        "    $links = @()",
        # gPLink lists a scope's links from lowest to highest precedence: GPMC
        # puts a new link first, at the lowest precedence, and the LAST entry
        # is link order 1. Order is counted from the end (#148 live review).
        # Option bit 0x1 means the link is DISABLED and 0x2 enforced
        # (MS-GPOL 2.2.2); "enabled" read bit 0x1 as enabled (#160).
        "    for ($i = $linkMatches.Count - 1; $i -ge 0; $i--) {",
        "        $m = $linkMatches[$i]",
        # A DWORD: [int] threw on values above Int32 and stopped the run
        # without saying why.
        "        $options = [long]$m.Groups[2].Value",
        "        if ($options -gt 4294967295) {",
        "            throw \"gPLink option $options on $dn is not a DWORD\"",
        "        }",
        "        $links += @{",
        "            id = [guid]::NewGuid().ToString()",
        "            target = $m.Groups[1].Value",
        "            enabled = ($options -band 1) -eq 0",
        "            enforced = ($options -band 2) -ne 0",
        "            order = $linkMatches.Count - $i",
        "        }",
        "    }",
        "    @{",
        "        kind = 'gpo-studio-discovery-ou'",
        "        distinguished_name = $dn",
        "        name = $name",
        "        parent_dn = ''",
        "        description = $description",
        "        gpo_links = $links",
        "    }",
        "})",
        "$result = @{",
        "    kind = 'gpo-studio-discovery-ous'",
        "    ous = $ous",
        "}",
        "ConvertTo-Json -InputObject $result -Depth 10",
    ]
    return _join_lines(lines)


def parse_ous(data: dict[str, Any]) -> tuple[OrganizationalUnit, ...]:
    """Parse PowerShell discovery JSON into OrganizationalUnit objects."""
    _require_kind(data, "gpo-studio-discovery-ous")
    _check_nesting_depth(data)
    ous_raw = data.get("ous", [])
    if not isinstance(ous_raw, list):
        raise ValidationError([
            ValidationIssue(
                severity="error",
                code="invalid_ous_field",
                message="The 'ous' field must be a list.",
                path="ous",
            )
        ])
    if len(ous_raw) > _MAX_OU_RESULTS:
        raise ValidationError([
            ValidationIssue(
                severity="error",
                code="too_many_ous",
                message=f"OU discovery exceeds {_MAX_OU_RESULTS} OUs.",
                path="ous",
            )
        ])
    return tuple(_parse_ou(item) for item in ous_raw)


def _parse_ou(data: dict[str, Any]) -> OrganizationalUnit:
    if not isinstance(data, dict):
        raise ValidationError([
            ValidationIssue(
                severity="error",
                code="invalid_ou_entry",
                message="Each OU entry must be a JSON object.",
                path="ous",
            )
        ])
    links_raw = data.get("gpo_links", [])
    if not isinstance(links_raw, list):
        raise ValidationError([
            ValidationIssue(
                severity="error",
                code="invalid_ou_links",
                message="OU gpo_links must be a list.",
                path="ous.gpo_links",
            )
        ])
    links = tuple(_parse_gpo_link(item) for item in links_raw)
    return OrganizationalUnit(
        distinguished_name=_str_field(data, "distinguished_name"),
        name=_str_field(data, "name"),
        parent_dn=_optional_str(data, "parent_dn"),
        description=_optional_str(data, "description"),
        gpo_links=links,
    )


def discovery_script_gpos(domain: str = "") -> str:
    """Generate a PowerShell script that collects GPO inventory."""
    domain_param = _ps_param(domain, "Domain")
    lines = [
        "# Generated by GPO Studio. Run on a domain-joined machine; output is JSON to stdout.",
        f"param([string]$Domain = {domain_param})",
        "$ErrorActionPreference = 'Stop'",
        *_PS_VALUE_HELPERS,
        "$forest = [System.DirectoryServices.ActiveDirectory.Forest]::GetCurrentForest()",
        "$adDomain = if ($Domain) {",
        "    $forest.Domains | Where-Object { $_.Name -eq $Domain } | Select-Object -First 1",
        "} else {",
        "    [System.DirectoryServices.ActiveDirectory.Domain]::GetCurrentDomain()",
        "}",
        "if (-not $adDomain) { throw \"Domain $Domain not found in forest\" }",
        "$root = $adDomain.GetDirectoryEntry()",
        "$searcher = New-Object System.DirectoryServices.DirectorySearcher($root)",
        "$searcher.Filter = '(objectCategory=groupPolicyContainer)'",
        "$searcher.PageSize = 1000",
        "$searcher.PropertiesToLoad.Add('name') | Out-Null",
        "$searcher.PropertiesToLoad.Add('displayName') | Out-Null",
        "$searcher.PropertiesToLoad.Add('gPCFileSysPath') | Out-Null",
        "$results = $searcher.FindAll()",
        "$gpos = @($results | ForEach-Object {",
        "    @{",
        "        guid = Get-FirstValue $_.Properties['name']",
        "        name = Get-FirstValue $_.Properties['displayName']",
        "        gpc_file_sys_path = Get-FirstValue $_.Properties['gPCFileSysPath']",
        "    }",
        "})",
        "$result = @{",
        "    kind = 'gpo-studio-discovery-gpos'",
        "    domain = $adDomain.Name",
        "    gpos = $gpos",
        "}",
        "ConvertTo-Json -InputObject $result -Depth 10",
    ]
    return _join_lines(lines)


def parse_gpos_discovery(data: dict[str, Any]) -> dict[str, Any]:
    """Validate GPO discovery JSON and return the validated document for caching.

    Only the validated fields are returned: the caller keyed the cache with
    ``str(domain)``, so a null domain was stored under "None", and the other
    fields went into the cache unchecked (#164 review).
    """
    _require_kind(data, "gpo-studio-discovery-gpos")
    _check_nesting_depth(data)
    gpos_raw = data.get("gpos", [])
    if not isinstance(gpos_raw, list):
        raise ValidationError([
            ValidationIssue(
                severity="error",
                code="invalid_gpos_field",
                message="The 'gpos' field must be a list.",
                path="gpos",
            )
        ])
    if len(gpos_raw) > _MAX_GPO_RESULTS:
        raise ValidationError([
            ValidationIssue(
                severity="error",
                code="too_many_gpos",
                message=f"GPO discovery exceeds {_MAX_GPO_RESULTS} GPOs.",
                path="gpos",
            )
        ])
    domain = _str_field(data, "domain")
    gpos: list[dict[str, str]] = []
    for idx, raw in enumerate(gpos_raw):
        if not isinstance(raw, dict):
            raise ValidationError([
                ValidationIssue(
                    severity="error",
                    code="invalid_gpo_entry",
                    message="Each GPO entry must be a JSON object.",
                    path=f"gpos[{idx}]",
                )
            ])
        guid = _optional_str(raw, "guid")
        _validate_guid(guid, f"gpos[{idx}].guid")
        gpos.append({
            "guid": guid,
            "name": _optional_str(raw, "name"),
            "gpc_file_sys_path": _optional_str(raw, "gpc_file_sys_path"),
        })
    return {"kind": "gpo-studio-discovery-gpos", "domain": domain, "gpos": gpos}


def discovery_script_principal_search(
    search_term: str,
    domain: str = "",
    object_class: str = "",
    search_base: str = "",
) -> str:
    """Generate a PowerShell script that searches for AD principals."""
    search_term_param = _ps_param(search_term, "SearchTerm")
    domain_param = _ps_param(domain, "Domain")
    object_class_param = _ps_param(object_class, "ObjectClass")
    search_base_param = _ps_param(search_base, "SearchBase")
    lines = [
        "# Generated by GPO Studio. Run on a domain-joined machine; output is JSON to stdout.",
        "param(",
        f"    [string]$SearchTerm = {search_term_param},",
        f"    [string]$Domain = {domain_param},",
        f"    [string]$ObjectClass = {object_class_param},",
        f"    [string]$SearchBase = {search_base_param}",
        ")",
        "$ErrorActionPreference = 'Stop'",
        *_PS_VALUE_HELPERS,
        # An empty assertion value, as in (sAMAccountName=), is refused by AD.
        "if ($SearchTerm -eq '') { throw 'A search term is required' }",
        "$forest = [System.DirectoryServices.ActiveDirectory.Forest]::GetCurrentForest()",
        "$adDomain = if ($Domain) {",
        "    $forest.Domains | Where-Object { $_.Name -eq $Domain } | Select-Object -First 1",
        "} else {",
        "    [System.DirectoryServices.ActiveDirectory.Domain]::GetCurrentDomain()",
        "}",
        "if (-not $adDomain) { throw \"Domain $Domain not found in forest\" }",
        "$root = if ($SearchBase) { [ADSI]\"LDAP://$SearchBase\" } "
        "else { $adDomain.GetDirectoryEntry() }",
        "$searcher = New-Object System.DirectoryServices.DirectorySearcher($root)",
        # Each filter component carries its own parentheses: the class filter
        # was wrapped in a second pair, (&((objectClass=user))...), which RFC
        # 4515 does not allow; and a name search always added (objectSid=),
        # an empty value AD refuses (#148 live review).
        "$classFilter = if ($ObjectClass) "
        "{ \"(objectClass=$(ConvertTo-LdapFilterValue $ObjectClass))\" } "
        "else { '(objectCategory=*)' }",
        "$escaped = ConvertTo-LdapFilterValue $SearchTerm",
        "$terms = @(\"(sAMAccountName=$escaped)\", \"(cn=$escaped)\")",
        # Only the literal S-1-... form is a SID: the SecurityIdentifier
        # constructor also accepts SDDL aliases, so a search for "DA" or "BA"
        # matched Domain Admins or BUILTIN\Administrators by SID (#164 review).
        # An identifier authority of 2^32 or more is written in hexadecimal
        # (MS-DTYP 2.4.2.1, "S-1-0x010000000000-1"); the constructor checks it.
        # Case-insensitive: MS-DTYP's ABNF strings are, and Windows accepts
        # "s-1-..." (#164 review). "S-1-5", with no subauthority, is not a SID.
        "if ($SearchTerm -match '^S-1-(0x[0-9A-F]{1,12}|[0-9]+)(-[0-9]+)+$') {",
        "    try {",
        "        $sid = New-Object System.Security.Principal.SecurityIdentifier($SearchTerm)",
        "        $sidBytes = New-Object byte[] $sid.BinaryLength",
        "        $sid.GetBinaryForm($sidBytes, 0)",
        "        $sidHex = ($sidBytes | ForEach-Object { '\\{0:x2}' -f $_ }) -join ''",
        "        $terms += \"(objectSid=$sidHex)\"",
        "    } catch {",
        "        # Not a SID Windows accepts: search by name only.",
        "    }",
        "}",
        "$searcher.Filter = \"(&$classFilter(|$($terms -join '')))\"",
        "$searcher.PageSize = 100",
        "$searcher.PropertiesToLoad.Add('objectGUID') | Out-Null",
        "$searcher.PropertiesToLoad.Add('objectSid') | Out-Null",
        "$searcher.PropertiesToLoad.Add('sIDHistory') | Out-Null",
        "$searcher.PropertiesToLoad.Add('objectClass') | Out-Null",
        "$searcher.PropertiesToLoad.Add('sAMAccountName') | Out-Null",
        "$searcher.PropertiesToLoad.Add('displayName') | Out-Null",
        "$searcher.PropertiesToLoad.Add('canonicalName') | Out-Null",
        "$searcher.PropertiesToLoad.Add('distinguishedName') | Out-Null",
        "$results = $searcher.FindAll()",
        "$hostname = $env:COMPUTERNAME",
        "$principals = @($results | ForEach-Object {",
        "    $guid = [guid]$_.Properties['objectGUID'][0]",
        "    $sid = New-Object System.Security.Principal.SecurityIdentifier("
        "$_.Properties['objectSid'][0], 0)",
        # @(): a single value would otherwise be emitted as a string, and
        # the importer requires a list (#148 review).
        "    $sidHistory = @($_.Properties['sIDHistory'] | ForEach-Object { "
        "(New-Object System.Security.Principal.SecurityIdentifier($_, 0)).Value })",
        "    @{",
        "        kind = 'gpo-studio-discovery-principal'",
        "        object_guid = $guid.ToString()",
        "        object_sid = $sid.Value",
        "        sid_history = $sidHistory",
        "        object_class = Get-LastValue $_.Properties['objectClass']",
        "        sam_account_name = Get-FirstValue $_.Properties['sAMAccountName']",
        "        display_name = Get-FirstValue $_.Properties['displayName']",
        "        canonical_name = Get-FirstValue $_.Properties['canonicalName']",
        "        distinguished_name = Get-FirstValue $_.Properties['distinguishedName']",
        "        domain = $adDomain.Name",
        "        source_dc = $hostname",
        "        collected_at = (Get-Date -Format 'o')",
        "        resolution_state = 'resolved'",
        "    }",
        "})",
        "$result = @{",
        "    kind = 'gpo-studio-discovery-principals'",
        "    principals = $principals",
        "}",
        "ConvertTo-Json -InputObject $result -Depth 10",
    ]
    return _join_lines(lines)


def parse_principals(data: dict[str, Any]) -> tuple[PrincipalInfo, ...]:
    """Parse PowerShell discovery JSON into PrincipalInfo objects."""
    _require_kind(data, "gpo-studio-discovery-principals")
    _check_nesting_depth(data)
    principals_raw = data.get("principals", [])
    if not isinstance(principals_raw, list):
        raise ValidationError([
            ValidationIssue(
                severity="error",
                code="invalid_principals_field",
                message="The 'principals' field must be a list.",
                path="principals",
            )
        ])
    if len(principals_raw) > _MAX_PRINCIPAL_RESULTS:
        raise ValidationError([
            ValidationIssue(
                severity="error",
                code="too_many_principals",
                message=f"Principal discovery exceeds {_MAX_PRINCIPAL_RESULTS} principals.",
                path="principals",
            )
        ])
    return tuple(_parse_principal(item) for item in principals_raw)


def _parse_principal(data: dict[str, Any]) -> PrincipalInfo:
    if not isinstance(data, dict):
        raise ValidationError([
            ValidationIssue(
                severity="error",
                code="invalid_principal_entry",
                message="Each principal entry must be a JSON object.",
                path="principals",
            )
        ])
    object_guid = _str_field(data, "object_guid")
    _validate_guid(object_guid, "object_guid")
    object_sid = _str_field(data, "object_sid")
    _validate_sid(object_sid, "object_sid")
    sid_history_raw = data.get("sid_history", [])
    if not isinstance(sid_history_raw, list):
        raise ValidationError([
            ValidationIssue(
                severity="error",
                code="invalid_sid_history",
                message="sid_history must be a list.",
                path="sid_history",
            )
        ])
    for idx, sid in enumerate(sid_history_raw):
        _validate_sid(_text(sid, f"sid_history[{idx}]"), f"sid_history[{idx}]")
    resolution_state = _parse_resolution_state(
        _optional_str(data, "resolution_state", "resolved")
    )
    return PrincipalInfo(
        object_guid=object_guid,
        object_sid=object_sid,
        sid_history=tuple(sid_history_raw),
        object_class=_str_field(data, "object_class"),
        sam_account_name=_optional_str(data, "sam_account_name"),
        display_name=_optional_str(data, "display_name"),
        canonical_name=_optional_str(data, "canonical_name"),
        distinguished_name=_optional_str(data, "distinguished_name"),
        domain=_optional_str(data, "domain"),
        source_dc=_optional_str(data, "source_dc"),
        collected_at=_optional_str(data, "collected_at"),
        resolution_state=resolution_state,
    )


def discovery_kind_from_data(data: dict[str, Any]) -> str | None:
    """Return the discovery kind from raw JSON, or None if absent."""
    kind = data.get("kind")
    return str(kind) if isinstance(kind, str) and kind in _DISCOVERY_KINDS else None


__all__ = [
    "discovery_script_forest",
    "discovery_script_domain",
    "discovery_script_sites",
    "discovery_script_ous",
    "discovery_script_gpos",
    "discovery_script_principal_search",
    "parse_forest",
    "parse_domain",
    "parse_sites",
    "parse_ous",
    "parse_gpos_discovery",
    "parse_principals",
    "parse_trust",
    "discovery_kind_from_data",
]
