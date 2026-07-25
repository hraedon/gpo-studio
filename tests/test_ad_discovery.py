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
    assert SCHEMA_VERSION == 3


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
    assert "objectClass=$ObjectClass" in script


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
    assert "function Escape-LdapFilter" in script
    assert "Escape-LdapFilter $SearchTerm" in script
    # The old naive single-quote replace must be gone.
    assert "$SearchTerm -replace" not in script


def test_ous_script_uses_regex_matches_for_gplink() -> None:
    """Blockers 2/3: gPLink parsing must use [regex]::Matches, not -split ']]'."""
    script = discovery_script_ous("ad.hraedon.com", "DC=ad,DC=hraedon,DC=com")
    assert "[regex]::Matches" in script
    assert "$order" in script
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
