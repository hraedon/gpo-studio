from __future__ import annotations

import pytest

from gpo_studio.model import ConflictError
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
