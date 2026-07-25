"""Generate AD discovery PowerShell scripts and parse their JSON output."""

from __future__ import annotations

import re
from typing import Any, cast

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

_GUID_RE = re.compile(
    r"^\{?[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\}?$"
)
_SID_RE = re.compile(r"^S-1-5(-\d+)+$")
_CIDR_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}/\d{1,2}$")

_DISCOVERY_KINDS = frozenset({
    "gpo-studio-discovery-forest",
    "gpo-studio-discovery-domain",
    "gpo-studio-discovery-sites",
    "gpo-studio-discovery-ous",
    "gpo-studio-discovery-gpos",
    "gpo-studio-discovery-principals",
})


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


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
    return str(value)


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
    return tuple(str(item) for item in value)


def _validate_guid(value: str, path: str) -> None:
    if not value or not _GUID_RE.match(value):
        raise ValidationError([
            ValidationIssue(
                severity="error",
                code="invalid_guid_format",
                message=f"Invalid GUID format: {value!r}",
                path=path,
            )
        ])


def _validate_sid(value: str, path: str) -> None:
    if not value or not _SID_RE.match(value):
        raise ValidationError([
            ValidationIssue(
                severity="error",
                code="invalid_sid_format",
                message=f"Invalid SID format: {value!r}",
                path=path,
            )
        ])


def _validate_cidr(value: str, path: str) -> None:
    if not value or not _CIDR_RE.match(value):
        raise ValidationError([
            ValidationIssue(
                severity="error",
                code="invalid_cidr_format",
                message=f"Invalid CIDR format: {value!r}",
                path=path,
            )
        ])


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


def _parse_gpo_link(raw: dict[str, Any]) -> GPOLink:
    link_id = str(raw.get("id", ""))
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
        target=str(raw.get("target", "")),
        enabled=bool(raw.get("enabled", True)),
        enforced=bool(raw.get("enforced", False)),
        order=int(raw.get("order", 1)),
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
        "    $direction = switch ($_.TrustDirection.ToString()) {",
        "        'Inbound' { 'inbound' }",
        "        'Outbound' { 'outbound' }",
        "        'Bidirectional' { 'bidirectional' }",
        "        default { 'unknown' }",
        "    }",
        "    $trustType = switch ($_.TrustType.ToString()) {",
        "        'ParentChild' { 'parent-child' }",
        "        'CrossLink' { 'cross-link' }",
        "        'External' { 'external' }",
        "        'Forest' { 'forest' }",
        "        default { 'unknown' }",
        "    }",
        "    @{",
        "        source = $_.SourceName",
        "        target = $_.TargetName",
        "        direction = $direction",
        "        trust_type = $trustType",
        "        transitive = $_.Transitive",
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
        transitive=bool(data.get("transitive", True)),
    )


def discovery_script_domain(domain: str = "") -> str:
    """Generate a PowerShell script that collects domain info."""
    domain_param = _ps_quote(domain) if domain else "''"
    lines = [
        "# Generated by GPO Studio. Run on a domain-joined machine; output is JSON to stdout.",
        f"param([string]$Domain = {domain_param})",
        "$ErrorActionPreference = 'Stop'",
        "if ($Domain -eq '') { "
        "$Domain = [System.DirectoryServices.ActiveDirectory.Domain]::GetCurrentDomain() }",
        "else {",
        "    $ctx = New-Object "
        "System.DirectoryServices.ActiveDirectory.DirectoryContext('Domain', $Domain)",
        "    $Domain = [System.DirectoryServices.ActiveDirectory.Domain]::GetDomain($ctx)",
        "}",
        "$domainControllers = @($Domain.DomainControllers | ForEach-Object { $_.Name })",
        "$root = $Domain.GetDirectoryEntry()",
        "$searcher = New-Object System.DirectoryServices.DirectorySearcher($root)",
        "$searcher.Filter = '(objectClass=domain)'",
        "$searcher.PropertiesToLoad.Add('nETBIOSName') | Out-Null",
        "$domainEntry = $searcher.FindOne()",
        "$netbios = if ($domainEntry -and $domainEntry.Properties['nETBIOSName']) "
        "{ $domainEntry.Properties['nETBIOSName'][0] } else { '' }",
        "$result = @{",
        "    kind = 'gpo-studio-discovery-domain'",
        "    dns_name = $Domain.Name",
        "    netbios_name = $netbios",
        "    domain_controllers = $domainControllers",
        "    pdc_emulator = $Domain.PdcRoleOwner.Name",
        "    rid_master = $Domain.RidRoleOwner.Name",
        "    infrastructure_master = $Domain.InfrastructureRoleOwner.Name",
        "    functional_level = $Domain.DomainMode.ToString()",
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
        netbios_name=str(data.get("netbios_name", "")),
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
    for cidr in subnets_raw:
        _validate_cidr(str(cidr), "sites.subnets")
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
        description=str(data.get("description", "")),
        subnets=tuple(str(s) for s in subnets_raw),
        site_links=tuple(str(s) for s in site_links_raw),
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
    cidr = _str_field(data, "cidr")
    _validate_cidr(cidr, "subnets.cidr")
    return SubnetInfo(
        cidr=cidr,
        site_name=_str_field(data, "site_name"),
        description=str(data.get("description", "")),
    )


def discovery_script_ous(domain: str, search_base: str = "") -> str:
    """Generate a PowerShell script that collects OUs and their GPO links."""
    domain_param = _ps_quote(domain)
    search_base_param = _ps_quote(search_base)
    lines = [
        "# Generated by GPO Studio. Run on a domain-joined machine; output is JSON to stdout.",
        "param(",
        f"    [string]$Domain = {domain_param},",
        f"    [string]$SearchBase = {search_base_param}",
        ")",
        "$ErrorActionPreference = 'Stop'",
        "$ctx = New-Object "
        "System.DirectoryServices.ActiveDirectory.DirectoryContext('Domain', $Domain)",
        "$adDomain = [System.DirectoryServices.ActiveDirectory.Domain]::GetDomain($ctx)",
        "$rootDn = $adDomain.GetDirectoryEntry().distinguishedName",
        "if (-not $SearchBase) { $SearchBase = $rootDn }",
        "$searcher = New-Object System.DirectoryServices.DirectorySearcher([ADSI]\"LDAP://$SearchBase\")",
        "$searcher.Filter = '(objectCategory=organizationalUnit)'",
        "$searcher.PageSize = 1000",
        "$searcher.PropertiesToLoad.Add('name') | Out-Null",
        "$searcher.PropertiesToLoad.Add('distinguishedName') | Out-Null",
        "$searcher.PropertiesToLoad.Add('description') | Out-Null",
        "$searcher.PropertiesToLoad.Add('gPLink') | Out-Null",
        "$results = $searcher.FindAll()",
        "$ous = @($results | ForEach-Object {",
        "    $dn = $_.Properties['distinguishedName'][0]",
        "    $name = $_.Properties['name'][0]",
        "    $description = if ($_.Properties['description']) "
        "{ $_.Properties['description'][0] } else { '' }",
        "    $gplink = if ($_.Properties['gPLink']) "
        "{ $_.Properties['gPLink'][0] } else { '' }",
        "    $links = @()",
        "    if ($gplink) {",
        "        $order = 0",
        r"        foreach ($m in [regex]::Matches($gplink, '\[LDAP://([^;]+);(\d+)\]')) {",
        "            $order++",
        "            $links += @{",
        "                id = [guid]::NewGuid().ToString()",
        "                target = $m.Groups[1].Value",
        "                enabled = ([int]$m.Groups[2].Value -band 1) -ne 0",
        "                enforced = ([int]$m.Groups[2].Value -band 2) -ne 0",
        "                order = $order",
        "            }",
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
        parent_dn=str(data.get("parent_dn", "")),
        description=str(data.get("description", "")),
        gpo_links=links,
    )


def discovery_script_gpos(domain: str = "") -> str:
    """Generate a PowerShell script that collects GPO inventory."""
    domain_param = _ps_quote(domain)
    lines = [
        "# Generated by GPO Studio. Run on a domain-joined machine; output is JSON to stdout.",
        f"param([string]$Domain = {domain_param})",
        "$ErrorActionPreference = 'Stop'",
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
        "        guid = $_.Properties['name'][0]",
        "        name = if ($_.Properties['displayName']) "
        "{ $_.Properties['displayName'][0] } else { '' }",
        "        gpc_file_sys_path = if ($_.Properties['gPCFileSysPath']) "
        "{ $_.Properties['gPCFileSysPath'][0] } else { '' }",
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
    """Validate GPO discovery JSON and return it for caching."""
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
        guid = str(raw.get("guid", ""))
        _validate_guid(guid, f"gpos[{idx}].guid")
    return data


def discovery_script_principal_search(
    search_term: str,
    domain: str = "",
    object_class: str = "",
    search_base: str = "",
) -> str:
    """Generate a PowerShell script that searches for AD principals."""
    search_term_param = _ps_quote(search_term)
    domain_param = _ps_quote(domain)
    object_class_param = _ps_quote(object_class)
    search_base_param = _ps_quote(search_base)
    lines = [
        "# Generated by GPO Studio. Run on a domain-joined machine; output is JSON to stdout.",
        "param(",
        f"    [string]$SearchTerm = {search_term_param},",
        f"    [string]$Domain = {domain_param},",
        f"    [string]$ObjectClass = {object_class_param},",
        f"    [string]$SearchBase = {search_base_param}",
        ")",
        "$ErrorActionPreference = 'Stop'",
        "function Escape-LdapFilter($s) {",
        "    $sb = New-Object System.Text.StringBuilder",
        "    foreach ($c in $s.ToCharArray()) {",
        "        if ($c -eq '*' -or $c -eq '(' -or $c -eq ')' -or $c -eq '\\' "
        "-or $c -eq [char]0) {",
        "            [void]$sb.AppendFormat('\\{0:x2}', [int]$c)",
        "        } else { [void]$sb.Append($c) }",
        "    }",
        "    $sb.ToString()",
        "}",
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
        "$classFilter = if ($ObjectClass) { \"(objectClass=$ObjectClass)\" } "
        "else { '(objectCategory=*)' }",
        "$sidBytes = $null",
        "try {",
        "    $sid = New-Object System.Security.Principal.SecurityIdentifier($SearchTerm)",
        "    $sidBytes = New-Object byte[] $sid.BinaryLength",
        "    $sid.GetBinaryForm($sidBytes, 0)",
        "} catch {",
        "    $sidBytes = $null",
        "}",
        "$sidHex = if ($sidBytes) { ($sidBytes | ForEach-Object { '\\{0:x2}' -f $_ }) -join '' } "
        "else { '' }",
        "$escaped = Escape-LdapFilter $SearchTerm",
        "$filter = \"(&($classFilter)(|"
        "(sAMAccountName=$escaped)(cn=$escaped)(objectSid=$sidHex)))\"",
        "$searcher.Filter = $filter",
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
        "    $sidHistory = @()",
        "    if ($_.Properties['sIDHistory']) {",
        "        $sidHistory = $_.Properties['sIDHistory'] | ForEach-Object { "
        "(New-Object System.Security.Principal.SecurityIdentifier($_, 0)).Value }",
        "    }",
        "    $objectClass = if ($_.Properties['objectClass']) "
        "{ $_.Properties['objectClass'][-1] } else { '' }",
        "    @{",
        "        kind = 'gpo-studio-discovery-principal'",
        "        object_guid = $guid.ToString()",
        "        object_sid = $sid.Value",
        "        sid_history = $sidHistory",
        "        object_class = $objectClass",
        "        sam_account_name = if ($_.Properties['sAMAccountName']) "
        "{ $_.Properties['sAMAccountName'][0] } else { '' }",
        "        display_name = if ($_.Properties['displayName']) "
        "{ $_.Properties['displayName'][0] } else { '' }",
        "        canonical_name = if ($_.Properties['canonicalName']) "
        "{ $_.Properties['canonicalName'][0] } else { '' }",
        "        distinguished_name = if ($_.Properties['distinguishedName']) "
        "{ $_.Properties['distinguishedName'][0] } else { '' }",
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
        _validate_sid(str(sid), f"sid_history[{idx}]")
    resolution_state = _parse_resolution_state(
        str(data.get("resolution_state", "resolved"))
    )
    return PrincipalInfo(
        object_guid=object_guid,
        object_sid=object_sid,
        sid_history=tuple(str(s) for s in sid_history_raw),
        object_class=_str_field(data, "object_class"),
        sam_account_name=str(data.get("sam_account_name", "")),
        display_name=str(data.get("display_name", "")),
        canonical_name=str(data.get("canonical_name", "")),
        distinguished_name=str(data.get("distinguished_name", "")),
        domain=str(data.get("domain", "")),
        source_dc=str(data.get("source_dc", "")),
        collected_at=str(data.get("collected_at", "")),
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
