from __future__ import annotations

import uuid
from dataclasses import replace

import pytest

from gpo_studio.gpp import (
    GppCollection,
    GppGroup,
    GppGroupMember,
    GppRegistry,
    GppRegistryValue,
)
from gpo_studio.model import (
    GPO,
    ConflictError,
    CseMetadataEntry,
    NotFoundError,
    RegistrySetting,
    SecurityFilter,
    ValidationError,
    WmiFilter,
)
from gpo_studio.store import WorkspaceStore


def test_mutations_are_versioned_and_stale_writes_fail(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "workspace.db")
    gpo = store.create_gpo("Synthetic policy", identity="alice", reason="initial draft")
    updated = store.update_metadata(
        gpo.guid,
        1,
        {"description": "A test policy"},
        identity="bob",
        reason="document purpose",
    )
    assert updated.revision == 2
    assert updated.description == "A test policy"
    with pytest.raises(ConflictError, match="current revision is 2"):
        store.update_metadata(
            gpo.guid,
            1,
            {"description": "stale"},
            identity="mallory",
            reason="stale browser tab",
        )
    history = store.revisions(gpo.guid)
    assert [item.revision for item in history] == [2, 1]
    assert history[0].actor == "bob"


def test_setting_link_and_restore_workflow(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "workspace.db")
    gpo = store.create_gpo("Synthetic policy", identity="alice", reason="draft")
    gpo = store.put_setting(
        gpo.guid,
        gpo.revision,
        {
            "side": "computer",
            "hive": "HKLM",
            "key": r"Software\Policies\Synthetic",
            "value_name": "Enabled",
            "registry_type": "REG_DWORD",
            "value": 1,
        },
        identity="alice",
        reason="enable synthetic feature",
    )
    assert len(gpo.settings) == 1
    gpo = store.put_link(
        gpo.guid,
        gpo.revision,
        {"target": "OU=Workstations,DC=example,DC=test", "order": 1},
        identity="alice",
        reason="stage rollout",
    )
    assert len(gpo.links) == 1
    restored = store.restore_revision(
        gpo.guid,
        1,
        gpo.revision,
        identity="alice",
        reason="restart draft",
    )
    assert restored.revision == 4
    assert restored.settings == ()
    assert restored.links == ()


def test_create_gpo_with_security_filters_and_wmi_filter(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "workspace.db")
    security_filters = (
        SecurityFilter(
            id="sf-1",
            principal="Domain Admins",
            permission="apply",
            inheritable=True,
            target_type="group",
        ),
        SecurityFilter(
            id="sf-2",
            principal="DOMAIN\\SvcAccount",
            permission="read",
            inheritable=False,
            target_type="user",
        ),
    )
    wmi_filter = WmiFilter(
        id="wmi-1",
        name="WorkstationFilter",
        description="Lab machines only",
        query="SELECT * FROM Win32_OperatingSystem",
    )
    gpo = store.create_gpo(
        "Filter policy",
        identity="alice",
        reason="create with filters",
        security_filters=security_filters,
        wmi_filter=wmi_filter,
    )
    assert gpo.security_filters == security_filters
    assert gpo.wmi_filter == wmi_filter
    fetched = store.get_gpo(gpo.guid)
    assert fetched.security_filters == security_filters
    assert fetched.wmi_filter == wmi_filter


def test_empty_name_blocks_ready_transition(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "workspace.db")
    gpo = store.create_gpo("", identity="alice", reason="draft")
    with pytest.raises(ValidationError) as exc_info:
        store.update_metadata(
            gpo.guid,
            gpo.revision,
            {"status": "ready"},
            identity="alice",
            reason="attempt ready",
        )
    assert any(i.code == "name_required" for i in exc_info.value.issues)


def test_cse_metadata_blocks_ready_transition(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "workspace.db")
    gpo = store.create_gpo(
        "CSE policy",
        identity="alice",
        reason="import with cse",
        cse_metadata=(
            CseMetadataEntry(
                guid="{35378EAC-683F-11D2-A89E-00C04FBBCFA2}",
                side="machine",
            ),
        ),
    )
    with pytest.raises(ValidationError) as exc_info:
        store.update_metadata(
            gpo.guid,
            gpo.revision,
            {"status": "ready"},
            identity="alice",
            reason="attempt ready",
        )
    assert any(
        i.code == "ready_blocked_unknown_cse" for i in exc_info.value.issues
    )


def test_clean_gpo_can_transition_to_ready(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "workspace.db")
    gpo = store.create_gpo("Clean policy", identity="alice", reason="draft")
    ready = store.update_metadata(
        gpo.guid,
        gpo.revision,
        {"status": "ready"},
        identity="alice",
        reason="mark ready",
    )
    assert ready.status == "ready"
    assert ready.revision == gpo.revision + 1


def test_ready_to_ready_is_allowed(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "workspace.db")
    gpo = store.create_gpo("Ready policy", identity="alice", reason="draft")
    ready = store.update_metadata(
        gpo.guid,
        gpo.revision,
        {"status": "ready"},
        identity="alice",
        reason="mark ready",
    )
    assert ready.status == "ready"
    again = store.update_metadata(
        ready.guid,
        ready.revision,
        {"status": "ready"},
        identity="alice",
        reason="reaffirm ready",
    )
    assert again.status == "ready"
    assert again.revision == ready.revision + 1


def test_warning_only_issues_allow_ready_transition(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "workspace.db")
    gpo = store.create_gpo("Warning policy", identity="alice", reason="draft")
    gpo = store.put_setting(
        gpo.guid,
        gpo.revision,
        {
            "side": "computer",
            "hive": "HKLM",
            "key": r"Software\Policies\Test",
            "value_name": "Enabled",
            "registry_type": "REG_DWORD",
            "value": 1,
        },
        identity="alice",
        reason="add setting",
    )
    ready = store.update_metadata(
        gpo.guid,
        gpo.revision,
        {"computer_enabled": False, "status": "ready"},
        identity="alice",
        reason="mark ready",
    )
    assert ready.status == "ready"
    assert ready.computer_enabled is False


def test_import_rejects_gpo_with_empty_name(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "workspace.db")
    gpo = GPO(guid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", name="   ")
    summary = store.import_baseline_gpos(
        [gpo], identity="alice", reason="import baseline"
    )
    assert summary["rejected"] == 1
    assert summary["imported"] == 0
    with pytest.raises(NotFoundError):
        store.get_gpo("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")


def test_import_rejects_ready_gpo_with_cse_metadata(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "workspace.db")
    gpo = GPO(
        guid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        name="CSE ready policy",
        status="ready",
        cse_metadata=(
            CseMetadataEntry(
                guid="{35378EAC-683F-11D2-A89A-00C04FBBCFA2}",
                side="machine",
            ),
        ),
    )
    summary = store.import_baseline_gpos(
        [gpo], identity="alice", reason="import baseline"
    )
    assert summary["rejected"] == 1
    assert summary["imported"] == 0
    with pytest.raises(NotFoundError):
        store.get_gpo("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")


def test_import_valid_gpo_imports_normally(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "workspace.db")
    gpo = GPO(guid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", name="Valid policy")
    summary = store.import_baseline_gpos(
        [gpo], identity="alice", reason="import baseline"
    )
    assert summary["imported"] == 1
    assert summary["rejected"] == 0
    fetched = store.get_gpo("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    assert fetched.name == "Valid policy"


def test_restore_revision_rejects_invalid_snapshot(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "workspace.db")
    gpo = store.create_gpo("", identity="alice", reason="draft")
    gpo = store.update_metadata(
        gpo.guid,
        gpo.revision,
        {"name": "Valid policy"},
        identity="alice",
        reason="fix empty name",
    )
    with pytest.raises(ValidationError) as exc_info:
        store.restore_revision(
            gpo.guid,
            1,
            gpo.revision,
            identity="alice",
            reason="restore invalid snapshot",
        )
    assert any(i.code == "name_required" for i in exc_info.value.issues)


def test_put_setting_rejects_invalid_side_hive_combination(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "workspace.db")
    gpo = store.create_gpo("Side policy", identity="alice", reason="draft")
    with pytest.raises(ValidationError) as exc_info:
        store.put_setting(
            gpo.guid,
            gpo.revision,
            {
                "side": "computer",
                "hive": "HKCU",
                "key": r"Software\Policies\Synthetic",
                "value_name": "Enabled",
                "registry_type": "REG_DWORD",
                "value": 1,
            },
            identity="alice",
            reason="invalid side/hive",
        )
    assert any(i.code == "side_hive_mismatch" for i in exc_info.value.issues)


def test_put_setting_accepts_valid_computer_hklm_setting(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "workspace.db")
    gpo = store.create_gpo("Valid policy", identity="alice", reason="draft")
    updated = store.put_setting(
        gpo.guid,
        gpo.revision,
        {
            "side": "computer",
            "hive": "HKLM",
            "key": r"Software\Policies\Synthetic",
            "value_name": "Enabled",
            "registry_type": "REG_DWORD",
            "value": 1,
        },
        identity="alice",
        reason="valid computer setting",
    )
    assert updated.revision == gpo.revision + 1
    assert len(updated.settings) == 1
    assert updated.settings[0].side == "computer"
    assert updated.settings[0].hive == "HKLM"


def test_put_settings_rejects_batch_with_invalid_setting(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "workspace.db")
    gpo = store.create_gpo("Batch policy", identity="alice", reason="draft")
    valid_setting = RegistrySetting(
        id="valid-1",
        side="computer",
        hive="HKLM",
        key=r"Software\Policies\Synthetic",
        value_name="Enabled",
        registry_type="REG_DWORD",
        value=1,
    )
    invalid_setting = RegistrySetting(
        id="invalid-1",
        side="user",
        hive="HKLM",
        key=r"Software\Policies\Synthetic",
        value_name="Enabled",
        registry_type="REG_DWORD",
        value=1,
    )
    with pytest.raises(ValidationError) as exc_info:
        store.put_settings(
            gpo.guid,
            gpo.revision,
            [valid_setting, invalid_setting],
            identity="alice",
            reason="mixed batch",
        )
    assert any(i.code == "side_hive_mismatch" for i in exc_info.value.issues)


def _sample_gpp_group(group_id: str = "") -> GppGroup:
    return GppGroup(
        name="Administrators",
        sid="S-1-5-32-544",
        action="update",
        members=(
            GppGroupMember(
                sid="S-1-5-21-1-2-3-500",
                name="DOMAIN\\Domain Admins",
                action="add",
                id="m1" if group_id else "",
            ),
        ),
        id=group_id,
    )


def test_put_gpp_group_auto_creates_collection(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "workspace.db")
    gpo = store.create_gpo("GPP policy", identity="alice", reason="draft")
    updated = store.put_gpp_group(
        gpo.guid,
        gpo.revision,
        "computer",
        _sample_gpp_group("g1"),
        identity="alice",
        reason="add group",
    )
    assert updated.revision == gpo.revision + 1
    assert len(updated.gpp_collections) == 1
    collection = updated.gpp_collections[0]
    assert collection.scope == "computer"
    assert len(collection.groups) == 1
    assert collection.groups[0].name == "Administrators"
    assert collection.groups[0].id == "g1"


def test_put_gpp_group_auto_generates_id(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "workspace.db")
    gpo = store.create_gpo("GPP policy", identity="alice", reason="draft")
    updated = store.put_gpp_group(
        gpo.guid,
        gpo.revision,
        "computer",
        _sample_gpp_group(""),
        identity="alice",
        reason="add group",
    )
    group = updated.gpp_collections[0].groups[0]
    assert group.id != ""
    assert len(group.id) > 0


def test_put_gpp_group_replaces_existing_by_id(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "workspace.db")
    gpo = store.create_gpo("GPP policy", identity="alice", reason="draft")
    gpo = store.put_gpp_group(
        gpo.guid,
        gpo.revision,
        "computer",
        _sample_gpp_group("g1"),
        identity="alice",
        reason="add group",
    )
    assert len(gpo.gpp_collections[0].groups) == 1
    updated_group = GppGroup(
        name="Operators",
        sid="S-1-5-32-547",
        action="update",
        id="g1",
    )
    gpo = store.put_gpp_group(
        gpo.guid,
        gpo.revision,
        "computer",
        updated_group,
        identity="alice",
        reason="replace group",
    )
    assert len(gpo.gpp_collections[0].groups) == 1
    assert gpo.gpp_collections[0].groups[0].name == "Operators"


def test_put_gpp_group_does_not_create_duplicate_scope(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "workspace.db")
    gpo = store.create_gpo("GPP policy", identity="alice", reason="draft")
    gpo = store.put_gpp_group(
        gpo.guid,
        gpo.revision,
        "computer",
        _sample_gpp_group("g1"),
        identity="alice",
        reason="add group",
    )
    gpo = store.put_gpp_group(
        gpo.guid,
        gpo.revision,
        "computer",
        GppGroup(name="Users", sid="S-1-5-32-545", id="g2"),
        identity="alice",
        reason="add second group",
    )
    assert len(gpo.gpp_collections) == 1
    assert gpo.gpp_collections[0].scope == "computer"
    assert len(gpo.gpp_collections[0].groups) == 2


def test_delete_gpp_group(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "workspace.db")
    gpo = store.create_gpo("GPP policy", identity="alice", reason="draft")
    gpo = store.put_gpp_group(
        gpo.guid,
        gpo.revision,
        "computer",
        _sample_gpp_group("g1"),
        identity="alice",
        reason="add group",
    )
    gpo = store.delete_gpp_group(
        gpo.guid,
        gpo.revision,
        "computer",
        "g1",
        identity="alice",
        reason="remove group",
    )
    assert len(gpo.gpp_collections) == 0


def test_delete_gpp_group_not_found(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "workspace.db")
    gpo = store.create_gpo("GPP policy", identity="alice", reason="draft")
    with pytest.raises(NotFoundError, match="was not found"):
        store.delete_gpp_group(
            gpo.guid,
            gpo.revision,
            "computer",
            "nonexistent",
            identity="alice",
            reason="remove",
        )


def test_put_gpp_registry_crud(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "workspace.db")
    gpo = store.create_gpo("GPP policy", identity="alice", reason="draft")
    reg = GppRegistry(
        key=r"Software\Policies\Test",
        action="update",
        values=(
            GppRegistryValue(
                name="Enabled", value=1, registry_type="REG_DWORD", id="v1"
            ),
        ),
        id="r1",
    )
    gpo = store.put_gpp_registry(
        gpo.guid,
        gpo.revision,
        "computer",
        reg,
        identity="alice",
        reason="add registry",
    )
    assert len(gpo.gpp_collections[0].registry) == 1
    assert gpo.gpp_collections[0].registry[0].key == r"Software\Policies\Test"
    gpo = store.delete_gpp_registry(
        gpo.guid,
        gpo.revision,
        "computer",
        "r1",
        identity="alice",
        reason="remove registry",
    )
    assert len(gpo.gpp_collections) == 0


def test_put_gpp_member_crud(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "workspace.db")
    gpo = store.create_gpo("GPP policy", identity="alice", reason="draft")
    gpo = store.put_gpp_group(
        gpo.guid,
        gpo.revision,
        "computer",
        GppGroup(name="Administrators", sid="S-1-5-32-544", id="g1"),
        identity="alice",
        reason="add group",
    )
    member = GppGroupMember(
        sid="S-1-5-21-1-2-3-500",
        name="DOMAIN\\Domain Admins",
        action="add",
        id="mem1",
    )
    gpo = store.put_gpp_member(
        gpo.guid,
        gpo.revision,
        "computer",
        "g1",
        member,
        identity="alice",
        reason="add member",
    )
    assert len(gpo.gpp_collections[0].groups[0].members) == 1
    assert gpo.gpp_collections[0].groups[0].members[0].sid == "S-1-5-21-1-2-3-500"
    updated_member = GppGroupMember(
        sid="S-1-5-21-1-2-3-500",
        name="DOMAIN\\Admins",
        action="add",
        id="mem1",
    )
    gpo = store.put_gpp_member(
        gpo.guid,
        gpo.revision,
        "computer",
        "g1",
        updated_member,
        identity="alice",
        reason="update member",
    )
    assert len(gpo.gpp_collections[0].groups[0].members) == 1
    assert gpo.gpp_collections[0].groups[0].members[0].name == "DOMAIN\\Admins"
    gpo = store.delete_gpp_member(
        gpo.guid,
        gpo.revision,
        "computer",
        "g1",
        "mem1",
        identity="alice",
        reason="remove member",
    )
    assert len(gpo.gpp_collections[0].groups[0].members) == 0


def test_gpp_validation_rejects_empty_group_name(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "workspace.db")
    gpo = store.create_gpo("GPP policy", identity="alice", reason="draft")
    with pytest.raises(ValidationError) as exc_info:
        store.put_gpp_group(
            gpo.guid,
            gpo.revision,
            "computer",
            GppGroup(name="   ", id="g1"),
            identity="alice",
            reason="invalid group",
        )
    assert any(i.code == "empty_gpp_group_name" for i in exc_info.value.issues)


def test_gpp_stale_revision_raises_conflict(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "workspace.db")
    gpo = store.create_gpo("GPP policy", identity="alice", reason="draft")
    with pytest.raises(ConflictError, match="current revision is"):
        store.put_gpp_group(
            gpo.guid,
            gpo.revision + 999,
            "computer",
            _sample_gpp_group("g1"),
            identity="alice",
            reason="stale",
        )


def test_put_gpp_group_edit_preserves_order(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "workspace.db")
    gpo = store.create_gpo("GPP policy", identity="alice", reason="draft")
    for gid in ("g1", "g2", "g3"):
        gpo = store.put_gpp_group(
            gpo.guid,
            gpo.revision,
            "computer",
            GppGroup(name=f"Group-{gid}", id=gid),
            identity="alice",
            reason="add group",
        )
    assert [g.id for g in gpo.gpp_collections[0].groups] == ["g1", "g2", "g3"]
    gpo = store.put_gpp_group(
        gpo.guid,
        gpo.revision,
        "computer",
        GppGroup(name="Group-g2", id="g2", description="updated"),
        identity="alice",
        reason="edit middle group",
    )
    assert [g.id for g in gpo.gpp_collections[0].groups] == ["g1", "g2", "g3"]
    assert gpo.gpp_collections[0].groups[1].description == "updated"


def test_put_gpp_registry_edit_preserves_order(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "workspace.db")
    gpo = store.create_gpo("GPP policy", identity="alice", reason="draft")
    for rid in ("r1", "r2", "r3"):
        gpo = store.put_gpp_registry(
            gpo.guid,
            gpo.revision,
            "computer",
            GppRegistry(key=f"Key-{rid}", id=rid),
            identity="alice",
            reason="add registry",
        )
    assert [r.id for r in gpo.gpp_collections[0].registry] == ["r1", "r2", "r3"]
    gpo = store.put_gpp_registry(
        gpo.guid,
        gpo.revision,
        "computer",
        GppRegistry(key="Key-r2", id="r2", action="replace"),
        identity="alice",
        reason="edit middle registry",
    )
    assert [r.id for r in gpo.gpp_collections[0].registry] == ["r1", "r2", "r3"]
    assert gpo.gpp_collections[0].registry[1].action == "replace"


def test_put_gpp_member_edit_preserves_order(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "workspace.db")
    gpo = store.create_gpo("GPP policy", identity="alice", reason="draft")
    group = GppGroup(
        name="Administrators",
        sid="S-1-5-32-544",
        id="g1",
        members=(
            GppGroupMember(sid="S-1", name="m1", id="mem1"),
            GppGroupMember(sid="S-2", name="m2", id="mem2"),
            GppGroupMember(sid="S-3", name="m3", id="mem3"),
        ),
    )
    gpo = store.put_gpp_group(
        gpo.guid,
        gpo.revision,
        "computer",
        group,
        identity="alice",
        reason="add group with members",
    )
    assert [m.id for m in gpo.gpp_collections[0].groups[0].members] == [
        "mem1",
        "mem2",
        "mem3",
    ]
    gpo = store.put_gpp_member(
        gpo.guid,
        gpo.revision,
        "computer",
        "g1",
        GppGroupMember(sid="S-2", name="m2-renamed", id="mem2"),
        identity="alice",
        reason="edit middle member",
    )
    assert [m.id for m in gpo.gpp_collections[0].groups[0].members] == [
        "mem1",
        "mem2",
        "mem3",
    ]
    assert gpo.gpp_collections[0].groups[0].members[1].name == "m2-renamed"


def test_put_gpp_group_assigns_nested_member_ids(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "workspace.db")
    gpo = store.create_gpo("GPP policy", identity="alice", reason="draft")
    group = GppGroup(
        name="Administrators",
        sid="S-1-5-32-544",
        members=(
            GppGroupMember(sid="S-1", name="m1"),
            GppGroupMember(sid="S-2", name="m2"),
        ),
    )
    gpo = store.put_gpp_group(
        gpo.guid,
        gpo.revision,
        "computer",
        group,
        identity="alice",
        reason="add group",
    )
    members = gpo.gpp_collections[0].groups[0].members
    assert len(members) == 2
    assert members[0].id != ""
    assert members[1].id != ""
    assert members[0].id != members[1].id


def test_put_gpp_registry_assigns_nested_value_ids(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "workspace.db")
    gpo = store.create_gpo("GPP policy", identity="alice", reason="draft")
    reg = GppRegistry(
        key="K",
        values=(
            GppRegistryValue(name="V1", value="x"),
            GppRegistryValue(name="V2", value="y"),
        ),
    )
    gpo = store.put_gpp_registry(
        gpo.guid,
        gpo.revision,
        "computer",
        reg,
        identity="alice",
        reason="add registry",
    )
    values = gpo.gpp_collections[0].registry[0].values
    assert len(values) == 2
    assert values[0].id != ""
    assert values[1].id != ""
    assert values[0].id != values[1].id


def test_delete_gpp_group_empty_id_raises(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "workspace.db")
    gpo = store.create_gpo("GPP policy", identity="alice", reason="draft")
    with pytest.raises(ValidationError) as exc_info:
        store.delete_gpp_group(
            gpo.guid,
            gpo.revision,
            "computer",
            "",
            identity="alice",
            reason="delete",
        )
    assert any(i.code == "empty_gpp_group_id" for i in exc_info.value.issues)


def test_delete_gpp_registry_empty_id_raises(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "workspace.db")
    gpo = store.create_gpo("GPP policy", identity="alice", reason="draft")
    with pytest.raises(ValidationError) as exc_info:
        store.delete_gpp_registry(
            gpo.guid,
            gpo.revision,
            "computer",
            "",
            identity="alice",
            reason="delete",
        )
    assert any(i.code == "empty_gpp_registry_id" for i in exc_info.value.issues)


def test_delete_gpp_member_empty_id_raises(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "workspace.db")
    gpo = store.create_gpo("GPP policy", identity="alice", reason="draft")
    with pytest.raises(ValidationError) as exc_info:
        store.delete_gpp_member(
            gpo.guid,
            gpo.revision,
            "computer",
            "g1",
            "",
            identity="alice",
            reason="delete",
        )
    assert any(i.code == "empty_gpp_member_id" for i in exc_info.value.issues)


def test_delete_last_gpp_group_drops_empty_collection(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "workspace.db")
    gpo = store.create_gpo("GPP policy", identity="alice", reason="draft")
    gpo = store.put_gpp_group(
        gpo.guid,
        gpo.revision,
        "computer",
        GppGroup(name="G1", id="g1"),
        identity="alice",
        reason="add group",
    )
    assert len(gpo.gpp_collections) == 1
    gpo = store.delete_gpp_group(
        gpo.guid,
        gpo.revision,
        "computer",
        "g1",
        identity="alice",
        reason="delete group",
    )
    assert len(gpo.gpp_collections) == 0


def test_delete_last_gpp_registry_drops_empty_collection(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "workspace.db")
    gpo = store.create_gpo("GPP policy", identity="alice", reason="draft")
    gpo = store.put_gpp_registry(
        gpo.guid,
        gpo.revision,
        "computer",
        GppRegistry(key="K", id="r1"),
        identity="alice",
        reason="add registry",
    )
    assert len(gpo.gpp_collections) == 1
    gpo = store.delete_gpp_registry(
        gpo.guid,
        gpo.revision,
        "computer",
        "r1",
        identity="alice",
        reason="delete registry",
    )
    assert len(gpo.gpp_collections) == 0


def test_delete_gpp_group_keeps_collection_with_remaining_registry(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "workspace.db")
    gpo = store.create_gpo("GPP policy", identity="alice", reason="draft")
    gpo = store.put_gpp_group(
        gpo.guid,
        gpo.revision,
        "computer",
        GppGroup(name="G1", id="g1"),
        identity="alice",
        reason="add group",
    )
    gpo = store.put_gpp_registry(
        gpo.guid,
        gpo.revision,
        "computer",
        GppRegistry(key="K", id="r1"),
        identity="alice",
        reason="add registry",
    )
    gpo = store.delete_gpp_group(
        gpo.guid,
        gpo.revision,
        "computer",
        "g1",
        identity="alice",
        reason="delete group",
    )
    assert len(gpo.gpp_collections) == 1
    assert len(gpo.gpp_collections[0].groups) == 0
    assert len(gpo.gpp_collections[0].registry) == 1


def test_delete_gpp_member_removes_exactly_one_with_duplicate_id(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "workspace.db")
    gpo = store.create_gpo("GPP policy", identity="alice", reason="draft")
    gpo = store.put_gpp_group(
        gpo.guid,
        gpo.revision,
        "computer",
        GppGroup(
            name="Administrators",
            sid="S-1-5-32-544",
            id="g1",
            members=(
                GppGroupMember(sid="S-1", name="m1", id="dup"),
            ),
        ),
        identity="alice",
        reason="add group",
    )

    def inject_duplicate(gpo: GPO) -> GPO:
        collection = gpo.gpp_collections[0]
        group = collection.groups[0]
        new_member = GppGroupMember(sid="S-2", name="m2", id="dup")
        new_group = replace(group, members=group.members + (new_member,))
        new_collection = replace(collection, groups=(new_group,))
        return replace(gpo, gpp_collections=(new_collection,))

    gpo = store._mutate(
        gpo.guid,
        gpo.revision,
        inject_duplicate,
        identity="alice",
        reason="inject duplicate member",
    )
    members = gpo.gpp_collections[0].groups[0].members
    assert len(members) == 2
    assert members[0].id == "dup"
    assert members[1].id == "dup"

    gpo = store.delete_gpp_member(
        gpo.guid,
        gpo.revision,
        "computer",
        "g1",
        "dup",
        identity="alice",
        reason="delete one member",
    )
    members = gpo.gpp_collections[0].groups[0].members
    assert len(members) == 1


def test_put_gpp_registry_value_crud(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "workspace.db")
    gpo = store.create_gpo("GPP policy", identity="alice", reason="draft")
    gpo = store.put_gpp_registry(
        gpo.guid,
        gpo.revision,
        "computer",
        GppRegistry(
            key=r"Software\Policies\Test",
            id="r1",
            values=(
                GppRegistryValue(name="V1", value="a", id="v1"),
                GppRegistryValue(name="V2", value="b", id="v2"),
                GppRegistryValue(name="V3", value="c", id="v3"),
            ),
        ),
        identity="alice",
        reason="add registry",
    )

    gpo = store.put_gpp_registry_value(
        gpo.guid,
        gpo.revision,
        "computer",
        "r1",
        GppRegistryValue(name="V4", value="d"),
        identity="alice",
        reason="add value",
    )
    values = gpo.gpp_collections[0].registry[0].values
    assert len(values) == 4
    assert values[3].name == "V4"
    assert values[3].id != ""

    gpo = store.put_gpp_registry_value(
        gpo.guid,
        gpo.revision,
        "computer",
        "r1",
        GppRegistryValue(name="V2", value="updated", id="v2"),
        identity="alice",
        reason="update value",
    )
    values = gpo.gpp_collections[0].registry[0].values
    assert len(values) == 4
    assert [v.id for v in values] == ["v1", "v2", "v3", values[3].id]
    assert values[1].value == "updated"

    gpo = store.delete_gpp_registry_value(
        gpo.guid,
        gpo.revision,
        "computer",
        "r1",
        "v2",
        identity="alice",
        reason="delete value",
    )
    values = gpo.gpp_collections[0].registry[0].values
    assert len(values) == 3
    assert all(v.id != "v2" for v in values)


def test_put_gpp_registry_value_auto_generates_id(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "workspace.db")
    gpo = store.create_gpo("GPP policy", identity="alice", reason="draft")
    gpo = store.put_gpp_registry(
        gpo.guid,
        gpo.revision,
        "computer",
        GppRegistry(key=r"Software\Policies\Test", id="r1"),
        identity="alice",
        reason="add registry",
    )
    gpo = store.put_gpp_registry_value(
        gpo.guid,
        gpo.revision,
        "computer",
        "r1",
        GppRegistryValue(name="V1", value="x"),
        identity="alice",
        reason="add value",
    )
    value = gpo.gpp_collections[0].registry[0].values[0]
    assert value.id != ""
    assert len(value.id) > 0


def test_put_gpp_registry_value_empty_name_raises(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "workspace.db")
    gpo = store.create_gpo("GPP policy", identity="alice", reason="draft")
    gpo = store.put_gpp_registry(
        gpo.guid,
        gpo.revision,
        "computer",
        GppRegistry(key=r"Software\Policies\Test", id="r1"),
        identity="alice",
        reason="add registry",
    )
    with pytest.raises(ValidationError) as exc_info:
        store.put_gpp_registry_value(
            gpo.guid,
            gpo.revision,
            "computer",
            "r1",
            GppRegistryValue(name="   ", value="x"),
            identity="alice",
            reason="invalid value",
        )
    assert any(i.code == "empty_gpp_registry_value_name" for i in exc_info.value.issues)


def test_delete_gpp_registry_value_empty_id_raises(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "workspace.db")
    gpo = store.create_gpo("GPP policy", identity="alice", reason="draft")
    with pytest.raises(ValidationError) as exc_info:
        store.delete_gpp_registry_value(
            gpo.guid,
            gpo.revision,
            "computer",
            "r1",
            "",
            identity="alice",
            reason="delete",
        )
    assert any(i.code == "empty_gpp_registry_value_id" for i in exc_info.value.issues)


def test_gpo_from_dict_assigns_deterministic_legacy_gpp_ids(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "workspace.db")
    gpo = store.create_gpo(
        "Legacy GPP policy",
        identity="alice",
        reason="draft",
        gpp_collections=(
            GppCollection(
                scope="computer",
                groups=(
                    GppGroup(
                        name="Administrators",
                        sid="S-1-5-32-544",
                        id="",
                        members=(
                            GppGroupMember(
                                sid="S-1-5-21-1-2-3-500",
                                name="STUDIO\\Domain Admins",
                                action="add",
                                id="",
                            ),
                        ),
                    ),
                ),
                registry=(
                    GppRegistry(
                        key=r"Software\Policies\Test",
                        id="",
                        values=(
                            GppRegistryValue(name="V1", value="x", id=""),
                        ),
                    ),
                ),
            ),
        ),
    )

    loaded = store.get_gpo(gpo.guid)
    collection = loaded.gpp_collections[0]
    group = collection.groups[0]
    assert group.id != ""
    assert group.id == str(
        uuid.uuid5(uuid.NAMESPACE_URL, f"{gpo.guid}/computer/group/0")
    )
    member = group.members[0]
    assert member.id != ""
    assert member.id == str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"{gpo.guid}/computer/group/0/member/0",
        )
    )
    registry = collection.registry[0]
    assert registry.id != ""
    assert registry.id == str(
        uuid.uuid5(uuid.NAMESPACE_URL, f"{gpo.guid}/computer/registry/0")
    )
    value = registry.values[0]
    assert value.id != ""
    assert value.id == str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"{gpo.guid}/computer/registry/0/value/0",
        )
    )

    loaded_again = store.get_gpo(gpo.guid)
    assert (
        loaded_again.gpp_collections[0].groups[0].id == group.id
    )
    assert (
        loaded_again.gpp_collections[0].groups[0].members[0].id == member.id
    )
    assert (
        loaded_again.gpp_collections[0].registry[0].id == registry.id
    )
    assert (
        loaded_again.gpp_collections[0].registry[0].values[0].id == value.id
    )

    updated = store.put_gpp_group(
        gpo.guid,
        loaded.revision,
        "computer",
        replace(group, name="Administrators-Updated"),
        identity="alice",
        reason="edit legacy group",
        must_exist=True,
    )
    assert updated.gpp_collections[0].groups[0].name == "Administrators-Updated"
    assert updated.gpp_collections[0].groups[0].id == group.id


def test_gpo_from_dict_preserves_existing_gpp_ids(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "workspace.db")
    gpo = store.create_gpo(
        "Stable GPP policy",
        identity="alice",
        reason="draft",
        gpp_collections=(
            GppCollection(
                scope="computer",
                groups=(
                    GppGroup(name="Admins", sid="S-1-5-32-544", id="existing-g1"),
                ),
                registry=(),
            ),
        ),
    )
    loaded = store.get_gpo(gpo.guid)
    assert loaded.gpp_collections[0].groups[0].id == "existing-g1"


def test_put_gpp_group_must_exist_raises_not_found(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "workspace.db")
    gpo = store.create_gpo("GPP policy", identity="alice", reason="draft")
    with pytest.raises(NotFoundError, match="not found"):
        store.put_gpp_group(
            gpo.guid,
            gpo.revision,
            "computer",
            GppGroup(name="Admins", id="nonexistent"),
            identity="alice",
            reason="edit",
            must_exist=True,
        )


def test_put_gpp_group_must_exist_false_appends(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "workspace.db")
    gpo = store.create_gpo("GPP policy", identity="alice", reason="draft")
    updated = store.put_gpp_group(
        gpo.guid,
        gpo.revision,
        "computer",
        GppGroup(name="Admins", id="new-id"),
        identity="alice",
        reason="add",
        must_exist=False,
    )
    assert len(updated.gpp_collections[0].groups) == 1


def test_put_gpp_group_must_exist_updates_existing(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "workspace.db")
    gpo = store.create_gpo("GPP policy", identity="alice", reason="draft")
    gpo = store.put_gpp_group(
        gpo.guid,
        gpo.revision,
        "computer",
        GppGroup(name="Admins", id="g1"),
        identity="alice",
        reason="add",
    )
    updated = store.put_gpp_group(
        gpo.guid,
        gpo.revision,
        "computer",
        GppGroup(name="Admins2", id="g1"),
        identity="alice",
        reason="edit",
        must_exist=True,
    )
    assert updated.gpp_collections[0].groups[0].name == "Admins2"


def test_put_gpp_registry_must_exist_raises_not_found(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "workspace.db")
    gpo = store.create_gpo("GPP policy", identity="alice", reason="draft")
    with pytest.raises(NotFoundError, match="not found"):
        store.put_gpp_registry(
            gpo.guid,
            gpo.revision,
            "computer",
            GppRegistry(key=r"Software\Policies\Test", id="nonexistent"),
            identity="alice",
            reason="edit",
            must_exist=True,
        )


def test_put_gpp_registry_must_exist_updates_existing(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "workspace.db")
    gpo = store.create_gpo("GPP policy", identity="alice", reason="draft")
    gpo = store.put_gpp_registry(
        gpo.guid,
        gpo.revision,
        "computer",
        GppRegistry(key=r"Software\Policies\Test", id="r1"),
        identity="alice",
        reason="add",
    )
    updated = store.put_gpp_registry(
        gpo.guid,
        gpo.revision,
        "computer",
        GppRegistry(key=r"Software\Policies\Updated", id="r1"),
        identity="alice",
        reason="edit",
        must_exist=True,
    )
    assert updated.gpp_collections[0].registry[0].key == r"Software\Policies\Updated"


def test_put_gpp_member_must_exist_raises_not_found(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "workspace.db")
    gpo = store.create_gpo("GPP policy", identity="alice", reason="draft")
    gpo = store.put_gpp_group(
        gpo.guid,
        gpo.revision,
        "computer",
        GppGroup(name="Admins", sid="S-1-5-32-544", id="g1"),
        identity="alice",
        reason="add group",
    )
    with pytest.raises(NotFoundError, match="not found"):
        store.put_gpp_member(
            gpo.guid,
            gpo.revision,
            "computer",
            "g1",
            GppGroupMember(sid="S-1-5-21-1-2-3-500", id="nonexistent"),
            identity="alice",
            reason="edit",
            must_exist=True,
        )


def test_put_gpp_member_must_exist_updates_existing(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "workspace.db")
    gpo = store.create_gpo("GPP policy", identity="alice", reason="draft")
    gpo = store.put_gpp_group(
        gpo.guid,
        gpo.revision,
        "computer",
        GppGroup(name="Admins", sid="S-1-5-32-544", id="g1"),
        identity="alice",
        reason="add group",
    )
    gpo = store.put_gpp_member(
        gpo.guid,
        gpo.revision,
        "computer",
        "g1",
        GppGroupMember(sid="S-1-5-21-1-2-3-500", id="m1"),
        identity="alice",
        reason="add member",
    )
    updated = store.put_gpp_member(
        gpo.guid,
        gpo.revision,
        "computer",
        "g1",
        GppGroupMember(sid="S-1-5-21-1-2-3-500", name="Updated", id="m1"),
        identity="alice",
        reason="edit member",
        must_exist=True,
    )
    assert (
        updated.gpp_collections[0].groups[0].members[0].name == "Updated"
    )


def test_put_gpp_registry_value_must_exist_raises_not_found(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "workspace.db")
    gpo = store.create_gpo("GPP policy", identity="alice", reason="draft")
    gpo = store.put_gpp_registry(
        gpo.guid,
        gpo.revision,
        "computer",
        GppRegistry(key=r"Software\Policies\Test", id="r1"),
        identity="alice",
        reason="add registry",
    )
    with pytest.raises(NotFoundError, match="not found"):
        store.put_gpp_registry_value(
            gpo.guid,
            gpo.revision,
            "computer",
            "r1",
            GppRegistryValue(name="V1", value="x", id="nonexistent"),
            identity="alice",
            reason="edit",
            must_exist=True,
        )


def test_put_gpp_registry_value_must_exist_updates_existing(tmp_path) -> None:
    store = WorkspaceStore(tmp_path / "workspace.db")
    gpo = store.create_gpo("GPP policy", identity="alice", reason="draft")
    gpo = store.put_gpp_registry(
        gpo.guid,
        gpo.revision,
        "computer",
        GppRegistry(
            key=r"Software\Policies\Test",
            id="r1",
            values=(GppRegistryValue(name="V1", value="x", id="v1"),),
        ),
        identity="alice",
        reason="add registry",
    )
    updated = store.put_gpp_registry_value(
        gpo.guid,
        gpo.revision,
        "computer",
        "r1",
        GppRegistryValue(name="V1", value="updated", id="v1"),
        identity="alice",
        reason="edit value",
        must_exist=True,
    )
    assert (
        updated.gpp_collections[0].registry[0].values[0].value == "updated"
    )
