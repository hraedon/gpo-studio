from __future__ import annotations

import xml.etree.ElementTree as ET

from gpo_studio.canonical import semantic_dict
from gpo_studio.export import gpmc_backup_bundle
from gpo_studio.model import GPO, SecurityFilter, WmiFilter
from gpo_studio.store import WorkspaceStore, gpo_from_dict

_GPMC_NS = "http://www.microsoft.com/GroupPolicy/Types"


def _sf(principal: str, **kw: object) -> SecurityFilter:
    return SecurityFilter(id=f"sf-{principal}", principal=principal, **kw)  # type: ignore[arg-type]


def test_security_filters_round_trip() -> None:
    gpo = GPO(
        guid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        name="Security policy",
        security_filters=(
            SecurityFilter(id="sf-1", principal="Domain Admins"),
            SecurityFilter(
                id="sf-2",
                principal="Help Desk",
                permission="read",
                inheritable=False,
            ),
        ),
    )
    restored = gpo_from_dict(gpo.to_dict())
    assert restored.security_filters == gpo.security_filters


def test_wmi_filter_round_trip() -> None:
    gpo = GPO(
        guid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        name="WMI policy",
        wmi_filter=WmiFilter(
            id="wf-1",
            name="Workstation OU filter",
            description="Lab machines only",
            query="SELECT * FROM Win32_OperatingSystem",
            language="WQL",
        ),
    )
    restored = gpo_from_dict(gpo.to_dict())
    assert restored.wmi_filter == gpo.wmi_filter


def test_wmi_filter_none_round_trip() -> None:
    gpo = GPO(
        guid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        name="No WMI",
        wmi_filter=None,
    )
    restored = gpo_from_dict(gpo.to_dict())
    assert restored.wmi_filter is None


def test_custom_domain_round_trip() -> None:
    gpo = GPO(
        guid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        name="Domain policy",
        domain="corp.example.test",
    )
    restored = gpo_from_dict(gpo.to_dict())
    assert restored.domain == "corp.example.test"


def test_default_domain_is_studio_local() -> None:
    gpo = GPO(guid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", name="Default")
    assert gpo.domain == "studio.local"


def test_semantic_dict_includes_security_filters() -> None:
    gpo = GPO(
        guid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        name="Sec policy",
        security_filters=(
            _sf("Help Desk", permission="read"),
            _sf("Domain Admins"),
        ),
    )
    sd = semantic_dict(gpo)
    assert "security_filters" in sd
    principals = [sf["principal"] for sf in sd["security_filters"]]
    assert principals == ["domain admins", "help desk"]
    assert sd["security_filters"][0]["permission"] == "apply"
    assert sd["security_filters"][0]["target_type"] == "group"
    assert sd["security_filters"][0]["sid"] == ""
    assert sd["security_filters"][1]["permission"] == "read"
    assert sd["security_filters"][1]["target_type"] == "group"
    assert sd["security_filters"][1]["sid"] == ""


def test_semantic_dict_includes_domain() -> None:
    gpo = GPO(
        guid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        name="Domain policy",
        domain="corp.example.test",
    )
    assert semantic_dict(gpo)["domain"] == "corp.example.test"


def test_semantic_dict_includes_wmi_filter() -> None:
    gpo = GPO(
        guid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        name="WMI policy",
        wmi_filter=WmiFilter(id="wf-1", name="filter", query="SELECT *"),
    )
    sd = semantic_dict(gpo)
    assert sd["wmi_filter"] is not None
    assert sd["wmi_filter"]["query"] == "SELECT *"


def test_put_security_filter_creates_revision(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "ws.db")
    gpo = store.create_gpo("Filter policy", identity="alice", reason="draft")
    gpo = store.put_security_filter(
        gpo.guid,
        gpo.revision,
        {"principal": "Domain Admins"},
        identity="alice",
        reason="grant apply",
    )
    assert gpo.revision == 2
    assert len(gpo.security_filters) == 1
    assert gpo.security_filters[0].principal == "Domain Admins"
    assert gpo.security_filters[0].permission == "apply"


def test_put_security_filter_replaces_existing(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "ws.db")
    gpo = store.create_gpo("Filter policy", identity="alice", reason="draft")
    gpo = store.put_security_filter(
        gpo.guid,
        gpo.revision,
        {"principal": "Domain Admins"},
        identity="alice",
        reason="grant apply",
    )
    gpo = store.put_security_filter(
        gpo.guid,
        gpo.revision,
        {"principal": "Domain Admins", "permission": "read"},
        identity="alice",
        reason="downgrade to read",
        filter_id=gpo.security_filters[0].id,
    )
    assert len(gpo.security_filters) == 1
    assert gpo.security_filters[0].permission == "read"


def test_delete_security_filter_creates_revision(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "ws.db")
    gpo = store.create_gpo("Filter policy", identity="alice", reason="draft")
    gpo = store.put_security_filter(
        gpo.guid,
        gpo.revision,
        {"principal": "Domain Admins"},
        identity="alice",
        reason="grant apply",
    )
    filter_id = gpo.security_filters[0].id
    gpo = store.delete_security_filter(
        gpo.guid,
        filter_id,
        gpo.revision,
        identity="alice",
        reason="remove filter",
    )
    assert gpo.revision == 3
    assert len(gpo.security_filters) == 0


def test_set_wmi_filter_creates_revision(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "ws.db")
    gpo = store.create_gpo("WMI policy", identity="alice", reason="draft")
    gpo = store.set_wmi_filter(
        gpo.guid,
        gpo.revision,
        {"id": "wf-1", "name": "Workstations", "query": "SELECT * FROM Win32_ComputerSystem"},
        identity="alice",
        reason="scope to workstations",
    )
    assert gpo.revision == 2
    assert gpo.wmi_filter is not None
    assert gpo.wmi_filter.name == "Workstations"
    assert gpo.wmi_filter.query == "SELECT * FROM Win32_ComputerSystem"


def test_set_wmi_filter_empty_clears_filter(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "ws.db")
    gpo = store.create_gpo("WMI policy", identity="alice", reason="draft")
    gpo = store.set_wmi_filter(
        gpo.guid,
        gpo.revision,
        {"id": "wf-1", "name": "Workstations"},
        identity="alice",
        reason="scope to workstations",
    )
    assert gpo.wmi_filter is not None
    gpo = store.set_wmi_filter(
        gpo.guid,
        gpo.revision,
        {},
        identity="alice",
        reason="clear WMI filter",
    )
    assert gpo.wmi_filter is None


def test_set_wmi_filter_none_clears_filter(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "ws.db")
    gpo = store.create_gpo("WMI policy", identity="alice", reason="draft")
    gpo = store.set_wmi_filter(
        gpo.guid,
        gpo.revision,
        {"id": "wf-1", "name": "Workstations"},
        identity="alice",
        reason="scope to workstations",
    )
    gpo = store.set_wmi_filter(
        gpo.guid,
        gpo.revision,
        None,
        identity="alice",
        reason="clear WMI filter",
    )
    assert gpo.wmi_filter is None


def test_update_metadata_accepts_domain(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "ws.db")
    gpo = store.create_gpo("Domain policy", identity="alice", reason="draft")
    gpo = store.update_metadata(
        gpo.guid,
        gpo.revision,
        {"domain": "corp.example.test"},
        identity="alice",
        reason="set target domain",
    )
    assert gpo.domain == "corp.example.test"


def test_gpmc_manifest_xml_uses_gpo_domain() -> None:
    from gpo_studio.export import _build_manifest_xml

    gpo = GPO(
        guid="11111111-2222-3333-4444-555555555555",
        name="Domain test",
        domain="corp.example.test",
    )
    xml_bytes = _build_manifest_xml(gpo)
    root = ET.fromstring(xml_bytes)
    domains = [el.text for el in root.iter(f"{{{_GPMC_NS}}}Domain")]
    assert "corp.example.test" in domains


def test_gpmc_bkup_info_xml_uses_gpo_domain() -> None:
    from gpo_studio.export import _build_bkup_info_xml

    gpo = GPO(
        guid="11111111-2222-3333-4444-555555555555",
        name="Domain test",
        domain="corp.example.test",
    )
    xml_bytes = _build_bkup_info_xml(gpo)
    root = ET.fromstring(xml_bytes)
    domains = [el.text for el in root.iter(f"{{{_GPMC_NS}}}Domain")]
    assert "corp.example.test" in domains


def test_gpmc_backup_bundle_deterministic_with_domain() -> None:
    gpo = GPO(
        guid="11111111-2222-3333-4444-555555555555",
        name="Deterministic test",
        domain="corp.example.test",
    )
    assert gpmc_backup_bundle(gpo) == gpmc_backup_bundle(gpo)


def test_semantic_dict_wmi_filter_none() -> None:
    gpo = GPO(guid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", name="No WMI")
    d = semantic_dict(gpo)
    assert d["wmi_filter"] is None


def test_semantic_hash_canonicalizes_principal_casing() -> None:
    from gpo_studio.canonical import semantic_hash

    gpo_a = GPO(
        guid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        name="A",
        security_filters=(_sf("DOMAIN\\Admins"),),
    )
    gpo_b = GPO(
        guid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        name="A",
        security_filters=(_sf("domain\\admins"),),
    )
    assert semantic_hash(gpo_a) == semantic_hash(gpo_b)


def test_semantic_hash_canonicalizes_filter_order() -> None:
    from gpo_studio.canonical import semantic_hash

    gpo_a = GPO(
        guid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        name="A",
        security_filters=(_sf("B", permission="apply"), _sf("A", permission="read")),
    )
    gpo_b = GPO(
        guid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        name="A",
        security_filters=(_sf("A", permission="read"), _sf("B", permission="apply")),
    )
    assert semantic_hash(gpo_a) == semantic_hash(gpo_b)
