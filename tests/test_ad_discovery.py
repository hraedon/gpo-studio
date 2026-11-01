from __future__ import annotations

import json
import sqlite3
from typing import Any

import pytest
from fastapi.testclient import TestClient

from gpo_studio.ad_discovery import (
    discovery_script_domain,
    discovery_script_forest,
    discovery_script_gpos,
    discovery_script_ous,
    discovery_script_principal_search,
    discovery_script_sites,
    parse_domain,
    parse_forest,
    parse_gpos_discovery,
    parse_ous,
    parse_principals,
    parse_sites,
    parse_trust,
)
from gpo_studio.api import app
from gpo_studio.model import (
    DomainInfo,
    ForestInfo,
    OrganizationalUnit,
    PrincipalInfo,
    SiteInfo,
    SubnetInfo,
    TrustInfo,
    ValidationError,
)
from gpo_studio.schema import SCHEMA_VERSION
from gpo_studio.store import WorkspaceStore

_VALID_FOREST = {
    "kind": "gpo-studio-discovery-forest",
    "name": "ad.hraedon.com",
    "schema_master": "dc01.ad.hraedon.com",
    "domain_naming_master": "dc01.ad.hraedon.com",
    "domains": ["ad.hraedon.com"],
    "global_catalogs": ["dc01.ad.hraedon.com", "dc02.ad.hraedon.com"],
    "trusts": [
        {
            "source": "ad.hraedon.com",
            "target": "child.ad.hraedon.com",
            "direction": "bidirectional",
            "trust_type": "parent-child",
            "transitive": True,
        }
    ],
}

_VALID_DOMAIN = {
    "kind": "gpo-studio-discovery-domain",
    "dns_name": "ad.hraedon.com",
    "netbios_name": "HRAEDON",
    "domain_controllers": ["dc01.ad.hraedon.com", "dc02.ad.hraedon.com"],
    "pdc_emulator": "dc01.ad.hraedon.com",
    "rid_master": "dc01.ad.hraedon.com",
    "infrastructure_master": "dc02.ad.hraedon.com",
    "functional_level": "Windows2016Domain",
}

_VALID_SITES = {
    "kind": "gpo-studio-discovery-sites",
    "sites": [
        {
            "kind": "gpo-studio-discovery-site",
            "name": "Default-First-Site-Name",
            "description": "Default site",
            "subnets": ["192.168.1.0/24"],
            "site_links": ["DEFAULTIPSITELINK"],
        }
    ],
    "subnets": [
        {
            "kind": "gpo-studio-discovery-subnet",
            "cidr": "192.168.1.0/24",
            "site_name": "Default-First-Site-Name",
            "description": "",
        }
    ],
}

_VALID_OUS = {
    "kind": "gpo-studio-discovery-ous",
    "ous": [
        {
            "kind": "gpo-studio-discovery-ou",
            "distinguished_name": "OU=Servers,DC=ad,DC=hraedon,DC=com",
            "name": "Servers",
            "parent_dn": "DC=ad,DC=hraedon,DC=com",
            "description": "Server accounts",
            "gpo_links": [
                {
                    "id": "11111111-2222-3333-4444-555555555555",
                    "target": "OU=Servers,DC=ad,DC=hraedon,DC=com",
                    "enabled": True,
                    "enforced": False,
                    "order": 1,
                }
            ],
        }
    ],
}

_VALID_GPOS = {
    "kind": "gpo-studio-discovery-gpos",
    "domain": "ad.hraedon.com",
    "gpos": [
        {
            "guid": "11111111-2222-3333-4444-555555555555",
            "name": "Server Baseline",
            "gpc_file_sys_path": (
                "\\\\dc01.ad.hraedon.com\\SysVol\\ad.hraedon.com\\Policies\\"
                "{11111111-2222-3333-4444-555555555555}"
            ),
        }
    ],
}

_VALID_PRINCIPALS = {
    "kind": "gpo-studio-discovery-principals",
    "principals": [
        {
            "kind": "gpo-studio-discovery-principal",
            "object_guid": "11111111-2222-3333-4444-555555555555",
            "object_sid": "S-1-5-21-1-2-3-1001",
            "sid_history": ["S-1-5-21-1-2-3-2001"],
            "object_class": "user",
            "sam_account_name": "alice",
            "display_name": "Alice Example",
            "canonical_name": "ad.hraedon.com/Users/alice",
            "distinguished_name": "CN=alice,CN=Users,DC=ad,DC=hraedon,DC=com",
            "domain": "ad.hraedon.com",
            "source_dc": "mvmcitest01",
            "collected_at": "2026-07-25T10:00:00Z",
            "resolution_state": "resolved",
        }
    ],
}


def test_schema_version_bumped() -> None:
    assert SCHEMA_VERSION == 4


def test_discovery_script_forest_content() -> None:
    script = discovery_script_forest()
    assert "GetCurrentForest" in script
    assert "ConvertTo-Json" in script
    assert "gpo-studio-discovery-forest" in script
    assert "$ErrorActionPreference = 'Stop'" in script
    assert "SchemaRoleOwner" in script


def test_discovery_script_domain_content() -> None:
    script = discovery_script_domain("ad.hraedon.com")
    assert "GetDomain" in script
    assert "ConvertTo-Json" in script
    assert "gpo-studio-discovery-domain" in script
    assert "ad.hraedon.com" in script
    assert "nETBIOSName" in script


def test_discovery_script_sites_content() -> None:
    script = discovery_script_sites()
    assert "GetCurrentForest" in script
    assert "$forest.Sites" in script
    assert "gpo-studio-discovery-sites" in script
    assert "ConvertTo-Json" in script


def test_discovery_script_ous_content() -> None:
    script = discovery_script_ous("ad.hraedon.com", "DC=ad,DC=hraedon,DC=com")
    assert "DirectorySearcher" in script
    assert "objectCategory=organizationalUnit" in script
    assert "gpo-studio-discovery-ous" in script
    assert "ad.hraedon.com" in script
    assert "gPLink" in script


def test_discovery_script_gpos_content() -> None:
    script = discovery_script_gpos("ad.hraedon.com")
    assert "DirectorySearcher" in script
    assert "objectCategory=groupPolicyContainer" in script
    assert "gpo-studio-discovery-gpos" in script
    assert "ad.hraedon.com" in script


def test_discovery_script_principal_search_content() -> None:
    script = discovery_script_principal_search("alice", "ad.hraedon.com", "user")
    assert "DirectorySearcher" in script
    assert "sAMAccountName" in script
    assert "objectSid" in script
    assert "gpo-studio-discovery-principals" in script
    assert "alice" in script
    # The class is escaped like the search term (#148 live review).
    assert "(objectClass=$(ConvertTo-LdapFilterValue $ObjectClass))" in script


def test_parse_forest_valid() -> None:
    info = parse_forest(_VALID_FOREST)
    assert isinstance(info, ForestInfo)
    assert info.name == "ad.hraedon.com"
    assert info.schema_master == "dc01.ad.hraedon.com"
    assert info.domains == ("ad.hraedon.com",)
    assert info.global_catalogs == ("dc01.ad.hraedon.com", "dc02.ad.hraedon.com")
    assert len(info.trusts) == 1
    assert info.trusts[0].direction == "bidirectional"
    assert info.trusts[0].trust_type == "parent-child"


def test_parse_forest_invalid_kind() -> None:
    with pytest.raises(ValidationError) as exc_info:
        parse_forest({"kind": "wrong"})
    assert exc_info.value.issues[0].code == "invalid_discovery_kind"


def test_parse_forest_missing_field() -> None:
    data = dict(_VALID_FOREST)
    del data["schema_master"]
    with pytest.raises(ValidationError) as exc_info:
        parse_forest(data)
    assert exc_info.value.issues[0].code == "missing_field"


def test_parse_trust_valid() -> None:
    trust = parse_trust({
        "source": "a.example",
        "target": "b.example",
        "direction": "inbound",
        "trust_type": "external",
        "transitive": False,
    })
    assert isinstance(trust, TrustInfo)
    assert trust.transitive is False


def test_parse_trust_invalid_direction() -> None:
    with pytest.raises(ValidationError) as exc_info:
        parse_trust({
            "source": "a",
            "target": "b",
            "direction": "sideways",
            "trust_type": "external",
        })
    assert exc_info.value.issues[0].code == "invalid_trust_direction"


def test_parse_domain_valid() -> None:
    info = parse_domain(_VALID_DOMAIN)
    assert isinstance(info, DomainInfo)
    assert info.dns_name == "ad.hraedon.com"
    assert info.netbios_name == "HRAEDON"
    assert info.domain_controllers == ("dc01.ad.hraedon.com", "dc02.ad.hraedon.com")
    assert info.pdc_emulator == "dc01.ad.hraedon.com"
    assert info.functional_level == "Windows2016Domain"


def test_parse_domain_invalid_kind() -> None:
    with pytest.raises(ValidationError) as exc_info:
        parse_domain({"kind": "wrong"})
    assert exc_info.value.issues[0].code == "invalid_discovery_kind"


def test_parse_sites_valid() -> None:
    sites, subnets = parse_sites(_VALID_SITES)
    assert len(sites) == 1
    assert isinstance(sites[0], SiteInfo)
    assert sites[0].name == "Default-First-Site-Name"
    assert sites[0].subnets == ("192.168.1.0/24",)
    assert len(subnets) == 1
    assert isinstance(subnets[0], SubnetInfo)
    assert subnets[0].cidr == "192.168.1.0/24"


def test_parse_sites_invalid_cidr() -> None:
    bad = dict(_VALID_SITES)
    bad["sites"] = [dict(_VALID_SITES["sites"][0], subnets=["not-a-cidr"])]
    with pytest.raises(ValidationError) as exc_info:
        parse_sites(bad)
    assert exc_info.value.issues[0].code == "invalid_cidr_format"


def test_parse_ous_valid() -> None:
    ous = parse_ous(_VALID_OUS)
    assert len(ous) == 1
    assert isinstance(ous[0], OrganizationalUnit)
    assert ous[0].distinguished_name == "OU=Servers,DC=ad,DC=hraedon,DC=com"
    assert len(ous[0].gpo_links) == 1
    assert ous[0].gpo_links[0].target == "OU=Servers,DC=ad,DC=hraedon,DC=com"


def test_parse_ous_invalid_gpo_link_id() -> None:
    bad = dict(_VALID_OUS)
    bad["ous"] = [dict(_VALID_OUS["ous"][0])]
    bad["ous"][0]["gpo_links"] = [{"target": "x", "enabled": True}]
    with pytest.raises(ValidationError) as exc_info:
        parse_ous(bad)
    assert exc_info.value.issues[0].code == "missing_gpo_link_id"


def test_parse_gpos_discovery_valid() -> None:
    validated = parse_gpos_discovery(_VALID_GPOS)
    assert validated["kind"] == "gpo-studio-discovery-gpos"
    assert len(validated["gpos"]) == 1


def test_parse_gpos_discovery_invalid_guid() -> None:
    bad = dict(_VALID_GPOS)
    bad["gpos"] = [dict(bad["gpos"][0], guid="not-a-guid")]
    with pytest.raises(ValidationError) as exc_info:
        parse_gpos_discovery(bad)
    assert exc_info.value.issues[0].code == "invalid_guid_format"


def test_parse_principals_valid() -> None:
    principals = parse_principals(_VALID_PRINCIPALS)
    assert len(principals) == 1
    principal = principals[0]
    assert isinstance(principal, PrincipalInfo)
    assert principal.object_guid == "11111111-2222-3333-4444-555555555555"
    assert principal.object_sid == "S-1-5-21-1-2-3-1001"
    assert principal.sid_history == ("S-1-5-21-1-2-3-2001",)
    assert principal.object_class == "user"
    assert principal.resolution_state == "resolved"


def test_parse_principals_invalid_sid() -> None:
    bad = dict(_VALID_PRINCIPALS)
    bad["principals"] = [dict(bad["principals"][0], object_sid="bad-sid")]
    with pytest.raises(ValidationError) as exc_info:
        parse_principals(bad)
    assert exc_info.value.issues[0].code == "invalid_sid_format"


def test_parse_principals_invalid_resolution_state() -> None:
    bad = dict(_VALID_PRINCIPALS)
    bad["principals"] = [dict(bad["principals"][0], resolution_state="unknown")]
    with pytest.raises(ValidationError) as exc_info:
        parse_principals(bad)
    assert exc_info.value.issues[0].code == "invalid_resolution_state"


def test_parse_rejects_deeply_nested_json() -> None:
    nested: dict[str, Any] = {"value": "x"}
    for _ in range(70):
        nested = {"child": nested}
    data = {"kind": "gpo-studio-discovery-forest", "name": "x", **nested}
    with pytest.raises(ValidationError) as exc_info:
        parse_forest(data)
    assert exc_info.value.issues[0].code == "json_nesting_too_deep"


def test_store_discovery_cache_and_retrieve(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "discovery.db")
    info = parse_forest(_VALID_FOREST)
    store.cache_discovery(
        "forest",
        info.name,
        json.dumps(info.to_dict(), separators=(",", ":"), sort_keys=True),
        source_dc=info.schema_master,
    )
    fetched = store.get_discovery("forest", info.name)
    assert fetched["name"] == "ad.hraedon.com"
    assert fetched["schema_master"] == "dc01.ad.hraedon.com"


def test_store_discovery_upsert_replaces(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "discovery.db")
    info = parse_forest(_VALID_FOREST)
    payload = json.dumps(info.to_dict())
    store.cache_discovery("forest", info.name, payload)
    updated = dict(info.to_dict())
    updated["schema_master"] = "dc02.ad.hraedon.com"
    store.cache_discovery("forest", info.name, json.dumps(updated))
    fetched = store.get_discovery("forest", info.name)
    assert fetched["schema_master"] == "dc02.ad.hraedon.com"


def test_store_list_discovery(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "discovery.db")
    info = parse_domain(_VALID_DOMAIN)
    store.cache_discovery(
        "domain",
        info.dns_name,
        json.dumps(info.to_dict(), separators=(",", ":"), sort_keys=True),
    )
    items = store.list_discovery("domain")
    assert len(items) == 1
    assert items[0]["dns_name"] == "ad.hraedon.com"


def test_store_search_principals(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "discovery.db")
    principals = parse_principals(_VALID_PRINCIPALS)
    for principal in principals:
        store.cache_discovery(
            "principal",
            principal.object_guid,
            json.dumps(principal.to_dict(), separators=(",", ":"), sort_keys=True),
        )
    results = store.search_principals("alice")
    assert len(results) == 1
    results = store.search_principals("bob")
    assert len(results) == 0
    results = store.search_principals("S-1-5-21-1-2-3-1001")
    assert len(results) == 1


def test_store_discovery_summary(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "discovery.db")
    forest = parse_forest(_VALID_FOREST)
    domain = parse_domain(_VALID_DOMAIN)
    store.cache_discovery("forest", forest.name, json.dumps(forest.to_dict()))
    store.cache_discovery("domain", domain.dns_name, json.dumps(domain.to_dict()))
    summary = store.discovery_summary()
    assert summary == {"forest": 1, "domain": 1}


def test_store_get_discovery_missing(tmp_path) -> None:
    from gpo_studio.model import NotFoundError

    store = WorkspaceStore(tmp_path / "discovery.db")
    with pytest.raises(NotFoundError):
        store.get_discovery("forest", "missing")


def _set_store(tmp_path) -> WorkspaceStore:
    store = WorkspaceStore(tmp_path / "api.db")
    app.state.store = store
    app.state.owns_store = False
    return store


def test_api_get_forest_discovery_script(tmp_path) -> None:
    _set_store(tmp_path)
    with TestClient(app) as client:
        resp = client.get("/api/discovery/forest/script")
        assert resp.status_code == 200
        assert "GetCurrentForest" in resp.json()["script"]


def test_api_import_forest_discovery(tmp_path) -> None:
    _set_store(tmp_path)
    with TestClient(app) as client:
        resp = client.post("/api/discovery/forest", json=_VALID_FOREST)
        assert resp.status_code == 200
        assert resp.json()["cached"] is True
        assert resp.json()["kind"] == "forest"
        summary = client.get("/api/discovery/summary").json()
        assert summary["forest"] == 1


def test_api_import_domain_discovery(tmp_path) -> None:
    _set_store(tmp_path)
    with TestClient(app) as client:
        resp = client.post("/api/discovery/domain", json=_VALID_DOMAIN)
        assert resp.status_code == 200
        assert resp.json()["cached"] is True
        items = client.get(
            "/api/discovery/domain/script", params={"domain": "ad.hraedon.com"}
        ).json()
        assert "ad.hraedon.com" in items["script"]


def test_api_import_sites_discovery(tmp_path) -> None:
    _set_store(tmp_path)
    with TestClient(app) as client:
        resp = client.post("/api/discovery/sites", json=_VALID_SITES)
        assert resp.status_code == 200
        assert resp.json()["sites"] == 1
        assert resp.json()["subnets"] == 1
        summary = client.get("/api/discovery/summary").json()
        assert summary["site"] == 1
        assert summary["subnet"] == 1


def test_api_import_ous_discovery(tmp_path) -> None:
    _set_store(tmp_path)
    with TestClient(app) as client:
        resp = client.post("/api/discovery/ous", json=_VALID_OUS)
        assert resp.status_code == 200
        assert resp.json()["count"] == 1
        script_resp = client.get(
            "/api/discovery/ous/script",
            params={"domain": "ad.hraedon.com", "search_base": "DC=ad,DC=hraedon,DC=com"},
        )
        assert script_resp.status_code == 200
        assert "organizationalUnit" in script_resp.json()["script"]


def test_api_import_gpos_discovery(tmp_path) -> None:
    _set_store(tmp_path)
    with TestClient(app) as client:
        resp = client.post("/api/discovery/gpos", json=_VALID_GPOS)
        assert resp.status_code == 200
        assert resp.json()["count"] == 1


def test_api_import_principals_discovery_and_search(tmp_path) -> None:
    _set_store(tmp_path)
    with TestClient(app) as client:
        resp = client.post("/api/discovery/principals", json=_VALID_PRINCIPALS)
        assert resp.status_code == 200
        assert resp.json()["count"] == 1
        search = client.get("/api/discovery/principals", params={"q": "alice"})
        assert search.status_code == 200
        assert search.json()["count"] == 1
        search = client.get("/api/discovery/principals", params={"q": "bob"})
        assert search.json()["count"] == 0


def test_api_import_forest_discovery_invalid_kind(tmp_path) -> None:
    _set_store(tmp_path)
    with TestClient(app) as client:
        resp = client.post("/api/discovery/forest", json={"kind": "wrong"})
        assert resp.status_code == 422


def test_api_import_principals_discovery_invalid_sid(tmp_path) -> None:
    _set_store(tmp_path)
    bad = dict(_VALID_PRINCIPALS)
    bad["principals"] = [dict(bad["principals"][0], object_sid="bad")]
    with TestClient(app) as client:
        resp = client.post("/api/discovery/principals", json=bad)
        assert resp.status_code == 422


def test_model_to_dict_round_trip() -> None:
    info = parse_forest(_VALID_FOREST)
    d = info.to_dict()
    assert d["name"] == "ad.hraedon.com"
    assert d["trusts"][0]["direction"] == "bidirectional"


# --- Suggestion 2: additional coverage for blockers and edge cases ---


def test_principal_search_script_includes_ldap_escape() -> None:
    """Blocker 1: the generated script must escape LDAP filter special chars."""
    script = discovery_script_principal_search("alice", "ad.hraedon.com", "user")
    # Renamed from Escape-LdapFilter; its filters are executed against an
    # RFC 4515 checker in test_discovery_script_output.py.
    assert "function ConvertTo-LdapFilterValue" in script
    assert "ConvertTo-LdapFilterValue $SearchTerm" in script
    # The old naive single-quote replace must be gone.
    assert "$SearchTerm -replace" not in script


def test_ous_script_uses_regex_matches_for_gplink() -> None:
    """Blockers 2/3: gPLink parsing must use [regex]::Matches, not -split ']]'."""
    script = discovery_script_ous("ad.hraedon.com", "DC=ad,DC=hraedon,DC=com")
    assert "[regex]::Matches" in script
    # Link order is counted from the end of gPLink (#148 live review).
    assert "order = $linkMatches.Count - $i" in script
    # The old broken split must be gone.
    assert "-split '\\]\\]'" not in script


def test_parse_ous_multiple_gpo_links_preserves_order() -> None:
    """Blockers 2/3: multiple gpo_links per OU must parse with correct order."""
    data = {
        "kind": "gpo-studio-discovery-ous",
        "ous": [
            {
                "kind": "gpo-studio-discovery-ou",
                "distinguished_name": "OU=Servers,DC=ad,DC=hraedon,DC=com",
                "name": "Servers",
                "parent_bn": "",
                "description": "",
                "gpo_links": [
                    {
                        "id": "11111111-2222-3333-4444-555555555555",
                        "target": "OU=Servers,DC=ad,DC=hraedon,DC=com",
                        "enabled": True,
                        "enforced": False,
                        "order": 1,
                    },
                    {
                        "id": "22222222-3333-4444-5555-666666666666",
                        "target": "OU=Servers,DC=ad,DC=hraedon,DC=com",
                        "enabled": True,
                        "enforced": True,
                        "order": 2,
                    },
                    {
                        "id": "33333333-4444-5555-6666-777777777777",
                        "target": "OU=Servers,DC=ad,DC=hraedon,DC=com",
                        "enabled": False,
                        "enforced": False,
                        "order": 3,
                    },
                ],
            }
        ],
    }
    ous = parse_ous(data)
    assert len(ous) == 1
    links = ous[0].gpo_links
    assert len(links) == 3
    assert [link.order for link in links] == [1, 2, 3]
    assert links[1].enforced is True
    assert links[2].enabled is False


def test_parse_trust_unknown_direction() -> None:
    """Suggestion 1: 'unknown' must be accepted as a TrustDirection."""
    trust = parse_trust({
        "source": "ad.hraedon.com",
        "target": "mystery.example",
        "direction": "unknown",
        "trust_type": "external",
        "transitive": False,
    })
    assert trust.direction == "unknown"


def test_parse_trust_invalid_type() -> None:
    """Invalid trust_type must be rejected (direction was already tested)."""
    with pytest.raises(ValidationError) as exc_info:
        parse_trust({
            "source": "a",
            "target": "b",
            "direction": "inbound",
            "trust_type": "bogus",
        })
    assert exc_info.value.issues[0].code == "invalid_trust_type"


@pytest.mark.parametrize("state", ["resolved", "ambiguous", "deleted", "inaccessible", "stale"])
def test_parse_principals_resolution_states(state: str) -> None:
    """All declared ResolutionState values must be accepted."""
    data = dict(_VALID_PRINCIPALS)
    data["principals"] = [dict(data["principals"][0], resolution_state=state)]
    principals = parse_principals(data)
    assert principals[0].resolution_state == state


def test_parse_forest_empty_trusts() -> None:
    """A forest with zero trusts must parse to an empty tuple."""
    data = dict(_VALID_FOREST)
    data["trusts"] = []
    info = parse_forest(data)
    assert info.trusts == ()


def test_parse_principals_empty_list() -> None:
    """A principals payload with an empty list must parse to an empty tuple."""
    data = dict(_VALID_PRINCIPALS)
    data["principals"] = []
    principals = parse_principals(data)
    assert principals == ()


def _cache_principal(store: WorkspaceStore, principal_dict: dict[str, Any]) -> None:
    store.cache_discovery(
        "principal",
        principal_dict["object_guid"],
        json.dumps(principal_dict, separators=(",", ":"), sort_keys=True),
    )


_PRINCIPAL_AD = dict(_VALID_PRINCIPALS["principals"][0])
_PRINCIPAL_OTHER = dict(
    _VALID_PRINCIPALS["principals"][0],
    object_guid="22222222-3333-4444-5555-666666666666",
    object_sid="S-1-5-21-1-2-3-1002",
    sam_account_name="alice.other",
    display_name="Alice Otherdomain",
    distinguished_name="CN=alice.other,CN=Users,DC=other,DC=hraedon,DC=com",
    domain="other.hraedon.com",
)


def test_store_search_principals_domain_filter(tmp_path) -> None:
    """Blocker 4 (primary path): domain filter restricts results."""
    store = WorkspaceStore(tmp_path / "discovery.db")
    _cache_principal(store, _PRINCIPAL_AD)
    _cache_principal(store, _PRINCIPAL_OTHER)
    # No domain filter: both principals match "alice".
    results = store.search_principals("alice")
    assert len(results) == 2
    # Domain filter: only the matching domain.
    results = store.search_principals("alice", domain="ad.hraedon.com")
    assert len(results) == 1
    assert results[0]["domain"] == "ad.hraedon.com"
    # Non-matching domain: no results.
    results = store.search_principals("alice", domain="nope.hraedon.com")
    assert len(results) == 0


def test_store_search_principals_domain_filter_fallback(tmp_path) -> None:
    """Blocker 4 (fallback path): domain filter must apply when json_extract is
    unavailable — the SQLite fallback fetches all rows and filters in Python."""

    class _NoJsonExtractConn:
        """Wraps a sqlite3.Connection; raises for json_extract queries."""

        def __init__(self, conn: sqlite3.Connection) -> None:
            self._conn = conn
            self.row_factory = conn.row_factory

        def execute(self, sql: str, params: tuple[object, ...] = ()) -> Any:
            if "json_extract" in sql:
                raise sqlite3.OperationalError("no such function: json_extract")
            return self._conn.execute(sql, params)

        def close(self) -> None:
            self._conn.close()

    store = WorkspaceStore(tmp_path / "discovery.db")
    _cache_principal(store, _PRINCIPAL_AD)
    _cache_principal(store, _PRINCIPAL_OTHER)
    store._connection = _NoJsonExtractConn(store._connection)  # type: ignore[assignment]

    # Without domain filter: both principals match "alice" even in fallback.
    results = store.search_principals("alice")
    assert len(results) == 2
    # With domain filter: only the matching domain (this was the B4 bug).
    results = store.search_principals("alice", domain="ad.hraedon.com")
    assert len(results) == 1
    assert results[0]["domain"] == "ad.hraedon.com"


def test_cached_discovery_is_committed_and_leaves_no_open_transaction(tmp_path) -> None:
    # #148 review: the bare INSERT opened an implicit transaction that was never
    # committed. The cache was lost on close, and the next mutation's BEGIN
    # IMMEDIATE failed and degraded the workspace (every later request 503).
    path = tmp_path / "discovery.db"
    store = WorkspaceStore(path)
    try:
        info = parse_forest(_VALID_FOREST)
        store.cache_discovery(
            "forest", info.name, json.dumps(info.to_dict(), sort_keys=True)
        )
        assert not store._connection.in_transaction
        store.create_gpo("Created after discovery", identity="tester", reason="After discovery")
        assert not store.is_degraded
    finally:
        store.close()
    reopened = WorkspaceStore(path)
    try:
        assert reopened.get_discovery("forest", info.name)["name"] == info.name
    finally:
        reopened.close()


# --- #148 live review: nulls, text types and IPv6 subnets -------------------


def _with(base: dict[str, Any], path: tuple[Any, ...], value: object) -> dict[str, Any]:
    copy = json.loads(json.dumps(base))
    target = copy
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    return copy


@pytest.mark.parametrize(
    ("parse", "base", "path", "read"),
    [
        (parse_domain, _VALID_DOMAIN, ("netbios_name",), lambda r: r.netbios_name),
        (
            lambda d: parse_sites(d)[0][0], _VALID_SITES, ("sites", 0, "description"),
            lambda r: r.description,
        ),
        (
            lambda d: parse_sites(d)[1][0], _VALID_SITES, ("subnets", 0, "description"),
            lambda r: r.description,
        ),
        (lambda d: parse_ous(d)[0], _VALID_OUS, ("ous", 0, "description"), lambda r: r.description),
        (lambda d: parse_ous(d)[0], _VALID_OUS, ("ous", 0, "parent_dn"), lambda r: r.parent_dn),
        (
            lambda d: parse_ous(d)[0].gpo_links[0], _VALID_OUS,
            ("ous", 0, "gpo_links", 0, "target"), lambda r: r.target,
        ),
        *(
            (
                lambda d: parse_principals(d)[0], _VALID_PRINCIPALS, ("principals", 0, field),
                lambda r, f=field: getattr(r, f),
            )
            for field in (
                "sam_account_name", "display_name", "canonical_name", "distinguished_name",
                "domain", "source_dc", "collected_at",
            )
        ),
    ],
)
def test_a_null_optional_field_is_empty_text_not_none(
    parse: Any, base: dict[str, Any], path: tuple[Any, ...], read: Any
) -> None:
    # str(None) stored the text "None" (live: netbios_name, display_name).
    assert read(parse(_with(base, path, None))) == ""


def test_a_null_resolution_state_keeps_its_default() -> None:
    principal = parse_principals(
        _with(_VALID_PRINCIPALS, ("principals", 0, "resolution_state"), None)
    )[0]
    assert principal.resolution_state == "resolved"


@pytest.mark.parametrize("value", [7, 1.5, True, [], {}])
@pytest.mark.parametrize(
    ("parse", "base", "path"),
    [
        (parse_forest, _VALID_FOREST, ("name",)),
        (parse_forest, _VALID_FOREST, ("domains", 0)),
        (parse_domain, _VALID_DOMAIN, ("netbios_name",)),
        (parse_domain, _VALID_DOMAIN, ("functional_level",)),
        (parse_sites, _VALID_SITES, ("sites", 0, "subnets", 0)),
        (parse_sites, _VALID_SITES, ("sites", 0, "site_links", 0)),
        (parse_ous, _VALID_OUS, ("ous", 0, "description")),
        (parse_gpos_discovery, _VALID_GPOS, ("gpos", 0, "guid")),
        (parse_principals, _VALID_PRINCIPALS, ("principals", 0, "display_name")),
        (parse_principals, _VALID_PRINCIPALS, ("principals", 0, "sid_history", 0)),
    ],
)
def test_a_text_field_holding_another_type_is_refused(
    parse: Any, base: dict[str, Any], path: tuple[Any, ...], value: object
) -> None:
    # str() turned these into "7", "[]" and so on.
    with pytest.raises(ValidationError) as exc_info:
        parse(_with(base, path, value))
    assert exc_info.value.issues[0].code in (
        "invalid_string_field", "invalid_guid_format", "invalid_sid_format"
    )


# MS-ADTS "Subnet Object" (learn.microsoft.com/openspecs/windows_protocols/ms-adts/
# 255d4630-f033-4289-8db6-c36060b47e17): its valid and invalid examples first.
VALID_SUBNETS = {
    "10.2.1.0/24": "10.2.1.0/24",
    "10.20.1.0/24": "10.20.1.0/24",
    "A:A:A:A::/64": "a:a:a:a::/64",
    # No host-bit rule for IPv6 (#164 review: strict=True refused this).
    "a:b::c:d:0:0/64": "a:b::c:d:0:0/64",
    "0:0:e0::/48": "0:0:e0::/48",
    "A:b:C::/128": "a:b:c::/128",
    "A:B::F:0/128": "a:b::f:0/128",
    "12AB:0:0:CD30::/60": "12ab:0:0:cd30::/60",
    "A:a:e:b:0:d:e:f/128": "a:a:e:b:0:d:e:f/128",
    "192.0.2.0/24": "192.0.2.0/24",
    "2001:db8:1::/64": "2001:db8:1::/64",
    "::ffff:c000:200/120": "::ffff:c000:200/120",
    "2001:db8::1/64": "2001:db8::1/64",
}
INVALID_SUBNETS = [
    # The spec's invalid examples.
    "10.02.0.0/16", "A:B:0C:D::/64", "A:B:0:0:0:0:E:F/128", "12AB::CD30:0:0:0:0/60",
    "12AB:0:0:CD30::F:0/60", "A:a:e:b::d:e:f/128",
    # Accepted by ip_network, refused by the spec (#164 review).
    "fe80::%eth0/64", "fe80::%\x00/64", "fe80::%\u202e/64", "2001:db8::%x/32",
    "2001:0db8::/64", "::/0", "10.0.0.0/0", "10.0.0.0/255.0.0.0", "::ffff:192.0.2.0/120",
    "10.0.0.0/08", "2001:db8::/032",
    # IPv4: host bits set, or the address equal to its own mask.
    "192.0.2.1/24", "255.0.0.0/8", "128.0.0.0/1", "255.255.255.255/32",
    # Shape.
    "192.0.2.0", "2001:db8::", "300.0.0.0/8", "192.0.2.0/33",
    "2001:db8::/129", "", "192.0.2.0/024x", "10.0.0.0//8", " 10.0.0.0/8", "10.0.0.0/8\n",
    "\u0661\u0660.0.0.0/8", "[2001:db8::]/32",
]


@pytest.mark.parametrize(("name", "stored"), VALID_SUBNETS.items())
def test_ad_subnet_names_are_accepted_and_stored_canonically(name: str, stored: str) -> None:
    sites = _with(_with(_VALID_SITES, ("sites", 0, "subnets"), [name, "192.0.2.0/24"]),
                  ("subnets", 0, "cidr"), name)
    site_info, subnets = parse_sites(sites)
    assert site_info[0].subnets == (stored, "192.0.2.0/24")
    assert subnets[0].cidr == stored


@pytest.mark.parametrize("cidr", INVALID_SUBNETS)
def test_names_ad_would_not_accept_as_subnets_are_refused(cidr: str) -> None:
    with pytest.raises(ValidationError) as exc_info:
        parse_sites(_with(_VALID_SITES, ("subnets", 0, "cidr"), cidr))
    assert exc_info.value.issues[0].code == "invalid_cidr_format"


def test_one_ipv6_subnet_in_two_cases_is_one_cache_record(tmp_path, monkeypatch) -> None:
    store = WorkspaceStore(tmp_path / "subnets.db")
    monkeypatch.setattr(app.state, "store", store, raising=False)
    monkeypatch.setattr(app.state, "owns_store", False, raising=False)
    try:
        client = TestClient(app, base_url="http://127.0.0.1")
        for name in ("2001:DB8:1::/64", "2001:db8:1::/64"):
            body = _with(_VALID_SITES, ("subnets", 0, "cidr"), name)
            assert client.post("/api/discovery/sites", json=body).status_code == 200
        keys = [row[0] for row in store._connection.execute(
            "SELECT key FROM estate_discovery WHERE kind='subnet'"
        )]
        assert keys == ["2001:db8:1::/64"]
    finally:
        store.close()


@pytest.mark.parametrize(
    ("path", "value", "code"),
    [
        (("domain",), None, "missing_field"),
        (("domain",), 5, "invalid_string_field"),
        (("domain",), {"a": "b"}, "invalid_string_field"),
        (("gpos", 0, "name"), 7, "invalid_string_field"),
        (("gpos", 0, "gpc_file_sys_path"), ["x"], "invalid_string_field"),
    ],
)
def test_gpo_inventory_text_fields_are_validated(
    path: tuple[Any, ...], value: object, code: str
) -> None:
    # #164 review: the inventory was cached as given, keyed by str(domain).
    with pytest.raises(ValidationError) as exc_info:
        parse_gpos_discovery(_with(_VALID_GPOS, path, value))
    assert exc_info.value.issues[0].code == code


def test_gpo_inventory_is_cached_by_its_validated_domain(tmp_path, monkeypatch) -> None:
    store = WorkspaceStore(tmp_path / "inventory.db")
    monkeypatch.setattr(app.state, "store", store, raising=False)
    monkeypatch.setattr(app.state, "owns_store", False, raising=False)
    try:
        client = TestClient(app, base_url="http://127.0.0.1")
        body = _with(_with(_VALID_GPOS, ("gpos", 0, "name"), None),
                     ("gpos", 0, "gpc_file_sys_path"), None)
        assert client.post("/api/discovery/gpos", json=body).status_code == 200
        bad = _with(_VALID_GPOS, ("domain",), None)
        assert client.post("/api/discovery/gpos", json=bad).status_code == 422
        rows = list(store._connection.execute(
            "SELECT key, data_json FROM estate_discovery WHERE kind='gpos'"
        ))
        assert [key for key, _data in rows] == [_VALID_GPOS["domain"]]
        (stored,) = [json.loads(data) for _key, data in rows]
        assert stored["gpos"] == [{
            "guid": _VALID_GPOS["gpos"][0]["guid"], "name": "", "gpc_file_sys_path": ""
        }]
    finally:
        store.close()


def _seed_earlier_build_cache(path) -> None:
    """A workspace whose discovery cache an earlier build wrote (no version marker)."""
    store = WorkspaceStore(path)
    store.close()
    conn = sqlite3.connect(path)
    try:
        conn.execute("DELETE FROM workspace_meta WHERE key='discovery_cache_version'")
        guid_only = [{"guid": f"{{{index:08X}-0000-0000-0000-000000000000}}"}
                     for index in range(2600)]
        rows = [
            ("gpos", "None", {"kind": "gpo-studio-discovery-gpos", "domain": None, "gpos": []}),
            # 5,204 nodes as an earlier build stored it; 10,404 once revalidated.
            ("gpos", "big.example.test", {"kind": "gpo-studio-discovery-gpos",
                                          "domain": "big.example.test", "gpos": guid_only}),
            ("site", "Other", {"name": "Other", "description": "",
                               "subnets": ["2001:DB8:1::/64"], "site_links": []}),
            ("subnet", "2001:DB8:1::/64", {"cidr": "2001:DB8:1::/64", "site_name": "Other",
                                           "description": "None"}),
            ("ou", "OU=Two,DC=example,DC=test", {
                "distinguished_name": "OU=Two,DC=example,DC=test", "name": "Two",
                "parent_dn": "", "description": "None",
                "gpo_links": [{"id": "x", "target": "x", "enabled": False, "enforced": False,
                               "order": 1}],
            }),
            ("principal", "11111111-2222-3333-4444-555555555555", {"display_name": "None"}),
        ]
        conn.executemany(
            "INSERT INTO estate_discovery(kind, key, data_json, source_dc, collected_at)"
            " VALUES(?,?,?,'','')",
            [(kind, key, json.dumps(data)) for kind, key, data in rows],
        )
        conn.commit()
    finally:
        conn.close()


def test_upgrade_clears_the_discovery_cache_earlier_builds_wrote(
    tmp_path, monkeypatch
) -> None:
    """#164 review: records earlier builds cached are wrong (inverted enabled
    flags, reversed order, "None"), and some sit under keys re-running
    discovery never overwrites. Opening the workspace clears them once."""
    path = tmp_path / "upgrade.db"
    _seed_earlier_build_cache(path)
    store = WorkspaceStore(path)
    monkeypatch.setattr(app.state, "store", store, raising=False)
    monkeypatch.setattr(app.state, "owns_store", False, raising=False)
    try:
        assert list(store._connection.execute("SELECT kind, key FROM estate_discovery")) == []
        assert store.workspace_meta()["discovery_cache_version"] == "2"
        # Records this build writes are kept: another domain's inventory no
        # longer removes one (the import-time purge did, #164 round 3).
        client = TestClient(app, base_url="http://127.0.0.1")
        for domain in ("big.example.test", "other.example.test"):
            body = _with(_VALID_GPOS, ("domain",), domain)
            body["gpos"] = [{"guid": f"{{{index:08X}-0000-0000-0000-000000000000}}"}
                            for index in range(2600)]
            assert client.post("/api/discovery/gpos", json=body).status_code == 200
        sites = _with(_VALID_SITES, ("subnets", 0, "cidr"), "2001:DB8:1::/64")
        sites["sites"][0]["subnets"] = ["2001:db8:1::/64"]
        assert client.post("/api/discovery/sites", json=sites).status_code == 200
    finally:
        store.close()
    reopened = WorkspaceStore(path)
    try:
        assert sorted(
            (row[0], row[1])
            for row in reopened._connection.execute("SELECT kind, key FROM estate_discovery")
        ) == [
            ("gpos", "big.example.test"),
            ("gpos", "other.example.test"),
            ("site", _VALID_SITES["sites"][0]["name"]),
            ("subnet", "2001:db8:1::/64"),
        ]
    finally:
        reopened.close()


def test_a_newer_builds_discovery_cache_is_left_alone(tmp_path) -> None:
    path = tmp_path / "newer.db"
    store = WorkspaceStore(path)
    store.cache_discovery("gpos", "example.test", json.dumps({"domain": "example.test"}))
    store.close()
    conn = sqlite3.connect(path)
    conn.execute("UPDATE workspace_meta SET value='3' WHERE key='discovery_cache_version'")
    conn.commit()
    conn.close()
    reopened = WorkspaceStore(path)
    try:
        assert [row[0] for row in reopened._connection.execute(
            "SELECT key FROM estate_discovery"
        )] == ["example.test"]
    finally:
        reopened.close()


def test_a_sites_import_is_one_transaction(tmp_path, monkeypatch) -> None:
    """#164 review: sites were committed before subnets were written, so a
    failed subnet write left the new sites behind a 409."""
    store = WorkspaceStore(tmp_path / "atomic-sites.db")
    monkeypatch.setattr(app.state, "store", store, raising=False)
    monkeypatch.setattr(app.state, "owns_store", False, raising=False)
    try:
        client = TestClient(app, base_url="http://127.0.0.1")
        assert client.post("/api/discovery/sites", json=_VALID_SITES).status_code == 200
        store._connection.execute(
            "CREATE TRIGGER fail_subnet BEFORE INSERT ON estate_discovery"
            " WHEN NEW.kind = 'subnet' BEGIN SELECT RAISE(ABORT, 'injected'); END"
        )
        before = list(store._connection.iterdump())
        changed = _with(_with(_VALID_SITES, ("sites", 0, "description"), "changed"),
                        ("subnets", 0, "cidr"), "198.51.100.0/24")
        changed["sites"][0]["subnets"] = ["198.51.100.0/24"]
        response = client.post("/api/discovery/sites", json=changed)
        assert response.status_code >= 400
        assert list(store._connection.iterdump()) == before
        assert not store._connection.in_transaction
        assert not store.is_degraded
    finally:
        store.close()


def test_a_failed_import_drops_nothing(tmp_path, monkeypatch) -> None:
    store = WorkspaceStore(tmp_path / "atomic.db")
    monkeypatch.setattr(app.state, "store", store, raising=False)
    monkeypatch.setattr(app.state, "owns_store", False, raising=False)
    try:
        store.cache_discovery("gpos", "None", json.dumps({"domain": None}))
        client = TestClient(app, base_url="http://127.0.0.1")
        bad = _with(_VALID_GPOS, ("gpos", 0, "name"), 7)
        assert client.post("/api/discovery/gpos", json=bad).status_code == 422
        assert [row[0] for row in store._connection.execute(
            "SELECT key FROM estate_discovery"
        )] == ["None"]
    finally:
        store.close()
