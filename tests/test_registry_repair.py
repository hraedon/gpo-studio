"""Reviewed, registry-only recovery preserves chosen rows and source history."""

from __future__ import annotations

import json
from contextlib import closing
from dataclasses import asdict, replace
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from gpo_studio.admx import AdmxCatalogue, PolicyDefinition
from gpo_studio.api import app
from gpo_studio.model import (
    GPO,
    BackupInventory,
    ConflictError,
    GPOLink,
    RegistrySetting,
    SecurityFilter,
    ValidationError,
    WmiFilter,
)
from gpo_studio.registry_repair import (
    RepairChoice,
    editing_warnings,
    repair_preview,
    repaired_settings,
    row_identity,
)
from gpo_studio.store import WorkspaceStore

AUDIT = {"identity": "synthetic-reviewer", "reason": "Reviewed registry-only recovery"}


def setting(identity: str, index: int, **changes: object) -> RegistrySetting:
    data = dict(
        id=identity,
        side="computer",
        hive="HKLM",
        key=f"Software\\Policies\\Synthetic{index}",
        value_name="Value",
        registry_type="REG_SZ",
        value=f"Exact {index} ",
        comment=f"Original row {index}",
    )
    data.update(changes)
    return RegistrySetting(**data)


def historical_source(store: WorkspaceStore) -> GPO:
    source = store.create_gpo(
        "Synthetic historical source",
        "Original description",
        **AUDIT,
        domain="synthetic.test",
        user_enabled=False,
        links=(GPOLink("link", "OU=Synthetic,DC=synthetic,DC=test"),),
        security_filters=(SecurityFilter("filter", "SYNTHETIC\\Readers"),),
        wmi_filter=WmiFilter(
            "wmi", "Synthetic filter", query="SELECT * FROM Win32_OperatingSystem"
        ),
    )
    source = replace(
        source,
        settings=(
            setting("duplicate", 0, registry_type="REG_QWORD", value=18446744073709551615),
            setting("duplicate", 1),
            setting("admx-synthetic:Owner-user-computer-state", 2),
            setting(f"admx2:{b'synthetic:Side'.hex()}:user:state::0", 3),
            setting("admx2:invalid", 4),
            setting("forked-admx-synthetic:Copied-computer-state", 5),
            setting("imported-admx-synthetic:Imported-computer-state", 6),
            setting("raw-list", 7, registry_type="REG_MULTI_SZ", value=[" ", "Exact", "é"]),
        ),
    )
    # Historical data cannot be authored through the complete-head guard now.
    payload = json.dumps(source.to_stored_dict())
    store._connection.execute(
        "UPDATE gpos SET snapshot_json=? WHERE guid=?", (payload, source.guid)
    )
    store._connection.execute(
        "UPDATE revisions SET snapshot_json=? WHERE gpo_guid=?", (payload, source.guid)
    )
    store._connection.commit()
    return store.get_gpo(source.guid)


def keep_all(source: GPO) -> tuple[RepairChoice, ...]:
    return tuple(RepairChoice(index, "keep_raw") for index in range(len(source.settings)))


def test_preview_is_exact_and_reports_every_identity_without_catalogue_inference(tmp_path: Path):
    with closing(WorkspaceStore(tmp_path / "workspace.db")) as store:
        source = historical_source(store)
        before = tuple(store._connection.iterdump())
        inspected = store.inspect_registry_repair(source.guid)
        preview = store.preview_registry_repair(
            source.guid, source.revision, keep_all(source), name="Synthetic repaired", **AUDIT
        )
        assert preview.source == inspected
        assert tuple(store._connection.iterdump()) == before
        assert [row.row for row in inspected.rows] == list(range(8))
        assert "18446744073709551615" in inspected.rows[0].record_json
        for row, original in zip(inspected.rows, source.settings, strict=True):
            assert json.loads(row.record_json) == asdict(original)
        assert inspected.rows[2].identity.claims == (
            ("synthetic:Owner", "user"),
            ("synthetic:Owner-user", "computer"),
        )
        assert inspected.rows[3].identity.warnings == ("owner_side_mismatch",)
        assert inspected.omitted_components["links"] == 1
        assert inspected.omitted_components["security_filters"] == 1
        assert inspected.omitted_components["wmi_filter"] == 1
        assert {warning.code for warning in inspected.warnings} == {
            "duplicate_editor_id",
            "ambiguous_owner",
            "owner_side_mismatch",
            "malformed_owner",
            "unowned_copied_row",
        }


def test_apply_preserves_every_chosen_native_field_order_and_source_history(tmp_path: Path):
    with closing(WorkspaceStore(tmp_path / "workspace.db")) as store:
        source = historical_source(store)
        before = store.revisions(source.guid)
        choices = tuple(
            RepairChoice(index, "omit" if index in (1, 4) else "keep_raw") for index in range(8)
        )
        preview = store.preview_registry_repair(
            source.guid, 1, choices, name="Synthetic repaired", **AUDIT
        )
        repaired = store.apply_registry_repair(
            source.guid,
            1,
            choices,
            name="Synthetic repaired",
            **AUDIT,
            review_sha256=preview.review_sha256,
        )
        assert repaired.guid != source.guid
        assert repaired.revision == 1 and repaired.status == "draft"
        assert repaired.domain == source.domain and not repaired.user_enabled
        assert (
            repaired.links == () and repaired.security_filters == () and repaired.wmi_filter is None
        )
        assert not repaired.source_guid  # not a claim of a discovered AD source
        assert source.guid in repaired.description and "revision 1" in repaired.description
        assert store.get_gpo(source.guid) == source
        assert store.revisions(source.guid) == before
        expected = [s for index, s in enumerate(source.settings) if index not in (1, 4)]
        assert len({s.id for s in repaired.settings}) == len(expected)
        for actual, original in zip(repaired.settings, expected, strict=True):
            assert UUID(actual.id).version == 4
            assert replace(actual, id=original.id) == original
        revision = store.revisions(repaired.guid)[0]
        assert revision.actor == AUDIT["identity"] and revision.reason == AUDIT["reason"]
        assert revision.snapshot["settings"] == [asdict(s) for s in repaired.settings]


@pytest.mark.parametrize(
    "choices",
    [
        (),
        (RepairChoice(0, "keep_raw"), RepairChoice(0, "omit")),
        (RepairChoice(-1, "keep_raw"),),
        (RepairChoice(1, "keep_raw"),),
        (RepairChoice(True, "keep_raw"),),
        (RepairChoice(0, "guess"),),
    ],
)
def test_choices_must_cover_each_row_exactly_once(choices):
    source = GPO("synthetic", "Synthetic", settings=(setting("same", 0),))
    with pytest.raises(ValidationError):
        repair_preview(source, choices, name="Synthetic repaired", actor="t", reason="r")


@pytest.mark.parametrize("field", ["name", "identity", "reason", "choices", "source"])
def test_apply_refuses_changed_review_facts_without_writing(tmp_path: Path, field: str):
    with closing(WorkspaceStore(tmp_path / "workspace.db")) as store:
        source = historical_source(store)
        choices = keep_all(source)
        preview = store.preview_registry_repair(source.guid, 1, choices, name="Synthetic", **AUDIT)
        kwargs = dict(name="Synthetic", **AUDIT, review_sha256=preview.review_sha256)
        if field == "choices":
            choices = (RepairChoice(0, "omit"), *choices[1:])
        elif field == "source":
            # Even an external edit which forgets to advance the revision is
            # caught by recomputing the reviewed facts under the store lock.
            data = source.to_stored_dict()
            data["settings"][0]["value"] = 3
            store._connection.execute("UPDATE gpos SET snapshot_json=?", (json.dumps(data),))
            store._connection.commit()
        else:
            kwargs[field] = "Changed reviewed input"
        before = tuple(store._connection.iterdump())
        with pytest.raises(ConflictError, match="fresh preview"):
            store.apply_registry_repair(source.guid, 1, choices, **kwargs)
        assert tuple(store._connection.iterdump()) == before


def test_stale_preview_cannot_remap_ordinals_to_a_new_source_revision(tmp_path: Path):
    with closing(WorkspaceStore(tmp_path / "workspace.db")) as store:
        source = store.create_gpo("Synthetic clean", **AUDIT)
        preview = store.preview_registry_repair(source.guid, 1, (), name="Recovered", **AUDIT)
        store.update_metadata(source.guid, 1, {"description": "New revision"}, **AUDIT)
        before = tuple(store._connection.iterdump())
        for operation in (store.preview_registry_repair, store.apply_registry_repair):
            extra = (
                {"review_sha256": preview.review_sha256}
                if operation == store.apply_registry_repair
                else {}
            )
            with pytest.raises(ConflictError):
                operation(source.guid, 1, (), name="Recovered", **AUDIT, **extra)
        assert tuple(store._connection.iterdump()) == before


def test_failed_creation_rolls_back_without_touching_source(tmp_path: Path, monkeypatch):
    with closing(WorkspaceStore(tmp_path / "workspace.db")) as store:
        source = historical_source(store)
        choices = keep_all(source)
        preview = store.preview_registry_repair(source.guid, 1, choices, name="Recovered", **AUDIT)
        before = tuple(store._connection.iterdump())
        original = store._store_documents

        def fail(*args):
            original(*args)
            raise OSError("Synthetic interrupted recovery")

        with monkeypatch.context() as fault:
            fault.setattr(store, "_store_documents", fail)
            with pytest.raises(OSError):
                store.apply_registry_repair(
                    source.guid,
                    1,
                    choices,
                    name="Recovered",
                    **AUDIT,
                    review_sha256=preview.review_sha256,
                )
        assert not store._connection.in_transaction
        assert tuple(store._connection.iterdump()) == before
        assert (
            store.apply_registry_repair(
                source.guid,
                1,
                choices,
                name="Recovered",
                **AUDIT,
                review_sha256=preview.review_sha256,
            ).revision
            == 1
        )


def test_recovery_does_not_guess_from_replaced_stored_values(tmp_path: Path):
    with closing(WorkspaceStore(tmp_path / "workspace.db")) as store:
        source = historical_source(store)
        data = source.to_stored_dict()
        data["settings"][0]["value"] = {"wrong": "type"}
        store._connection.execute("UPDATE gpos SET snapshot_json=?", (json.dumps(data),))
        store._connection.commit()
        assert editing_warnings(store.get_gpo(source.guid))
        with pytest.raises(ValidationError):
            store.inspect_registry_repair(source.guid)


def test_digest_excludes_stored_only_provenance_and_clones_mutable_native_values():
    source = GPO(
        "synthetic",
        "Synthetic",
        settings=(setting("raw", 0, registry_type="REG_MULTI_SZ", value=[" ", "Original"]),),
        backup_inventory=BackupInventory("", source_domain="private.invalid"),
    )
    args = dict(name="Recovered", actor="t", reason="r")
    first = repair_preview(source, keep_all(source), **args)
    changed = replace(
        source, backup_inventory=replace(source.backup_inventory, source_domain="changed.invalid")
    )
    assert repair_preview(changed, keep_all(source), **args).review_sha256 == first.review_sha256
    assert "private.invalid" not in json.dumps(asdict(first))
    copied = repaired_settings(source, keep_all(source))
    copied[0].value.append("New")
    assert source.settings[0].value == [" ", "Original"]


@pytest.mark.parametrize("identity", ["x" * 8193, "admx-owner" + "-user-frame" * 33])
def test_pathological_identity_stays_raw_without_guessing(identity: str):
    assert row_identity(setting(identity, 0)).warnings == ("identity_analysis_limit",)


def test_api_requires_explicit_scope_and_keeps_warnings_separate_from_exports(
    tmp_path, monkeypatch
):
    with closing(WorkspaceStore(tmp_path / "workspace.db")) as store:
        source = historical_source(store)
        monkeypatch.setattr(app.state, "store", store, raising=False)
        monkeypatch.setattr(app.state, "owns_store", False, raising=False)
        client = TestClient(app, base_url="http://127.0.0.1")
        base = f"/api/gpos/{source.guid}"
        payload = client.get(base).json()
        assert payload["editing_warnings"]
        assert not [i for i in payload["validation"] if i["severity"] == "error"]
        export = client.get(base + "/export.zip")
        assert export.status_code == 200
        before = tuple(store._connection.iterdump())
        assert client.get(base + "/registry-repair").status_code == 200
        body = dict(
            expected_revision=1,
            name="Synthetic recovered",
            actor="api-reviewer",
            reason="Reviewed",
            choices=[asdict(c) for c in keep_all(source)],
        )
        for acknowledgement in (None, False, "true", 1):
            request = dict(body)
            if acknowledgement is not None:
                request["registry_only"] = acknowledgement
            assert client.post(base + "/registry-repair/preview", json=request).status_code == 422
        body["registry_only"] = True
        preview = client.post(base + "/registry-repair/preview", json=body)
        assert preview.status_code == 200, preview.text
        assert tuple(store._connection.iterdump()) == before
        result = client.post(
            base + "/registry-repair/apply",
            json={
                **body,
                "review_sha256": preview.json()["review_sha256"],
            },
        )
        assert result.status_code == 201, result.text
        assert result.json()["gpo"]["revision"] == 1
        assert client.get(base + "/export.zip").content == export.content
        assert client.get(base).json() == payload


def test_catalogue_changes_and_raw_side_edits_cannot_disambiguate_a_historical_owner(
    tmp_path, monkeypatch
):
    with closing(WorkspaceStore(tmp_path / "workspace.db")) as store:
        source = historical_source(store)
        monkeypatch.setattr(app.state, "store", store, raising=False)
        monkeypatch.setattr(app.state, "owns_store", False, raising=False)
        client = TestClient(app, base_url="http://127.0.0.1")
        path = f"/api/gpos/{source.guid}/registry-repair"
        original = client.get(path).json()
        matching = PolicyDefinition(
            "Owner-user",
            "Machine",
            source.settings[2].key,
            "Synthetic current owner",
            "",
            "",
            namespace="synthetic",
            value_name=source.settings[2].value_name,
        )
        for policies in ((), (matching,), (replace(matching, key="Software\\Moved"),)):
            monkeypatch.setattr(
                app.state, "admx_catalogue", AdmxCatalogue(policies=policies), raising=False
            )
            assert client.get(path).json() == original
        data = source.to_stored_dict()
        data["settings"][2].update(side="user", hive="HKCU")
        store._connection.execute("UPDATE gpos SET snapshot_json=?", (json.dumps(data),))
        store._connection.commit()
        moved = client.get(path).json()
        assert moved["rows"][2]["identity"] == original["rows"][2]["identity"]
        assert len(moved["rows"][2]["identity"]["claims"]) == 2


def test_identity_warnings_do_not_prevent_a_clean_historical_restore(tmp_path, monkeypatch):
    with closing(WorkspaceStore(tmp_path / "workspace.db")) as store:
        clean = store.create_gpo("Synthetic clean history", **AUDIT)
        current = store.update_metadata(clean.guid, 1, {"description": "Later head"}, **AUDIT)
        data = current.to_stored_dict()
        data["settings"] = [asdict(setting("duplicate", index)) for index in range(2)]
        payload = json.dumps(data)
        store._connection.execute("UPDATE gpos SET snapshot_json=?", (payload,))
        store._connection.execute(
            "UPDATE revisions SET snapshot_json=? WHERE revision=2", (payload,)
        )
        store._connection.commit()
        old_revision = store.get_revision(clean.guid, 2)
        monkeypatch.setattr(app.state, "store", store, raising=False)
        monkeypatch.setattr(app.state, "owns_store", False, raising=False)
        client = TestClient(app, base_url="http://127.0.0.1")
        assert client.get(f"/api/gpos/{clean.guid}").json()["editing_warnings"]
        result = client.post(
            f"/api/gpos/{clean.guid}/revisions/1/restore",
            json={
                "expected_revision": 2,
                "actor": "synthetic-reviewer",
                "reason": "Restore clean revision",
            },
        )
        assert result.status_code == 200, result.text
        assert result.json()["gpo"]["revision"] == 3
        assert result.json()["editing_warnings"] == []
        assert store.get_revision(clean.guid, 2) == old_revision
