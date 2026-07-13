from __future__ import annotations

import pytest

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
