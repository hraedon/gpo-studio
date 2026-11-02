"""A native import's public dict, digests and Studio bundle are the bytes they
were before the source identity was recorded (final-r22 ruling).

The source identity (`BackupInventory.source_*`) is provenance, not policy:
the workspace stores it for a replacing import, and nothing published --
the GPO's public dict, the review and policy digests, the Studio bundle and
its ``manifest.json`` -- carries it. ``fixtures/native_artifacts_5c6ea8a.json``
holds those artifacts' SHA-256 for every native capture as 5c6ea8a's source
produced them (``python tests/native_artifacts.py <5c6ea8a src>``, with the
import's minted ids made deterministic); the current source must produce them
exactly.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from native_artifacts import CAPTURES, ROOT, artifacts

EXPECTED = json.loads(
    (Path(__file__).parent / "fixtures" / "native_artifacts_5c6ea8a.json").read_text(
        encoding="utf-8"
    )
)


def test_every_native_capture_is_compared() -> None:
    assert sorted(EXPECTED) == [capture.relative_to(ROOT).as_posix() for capture in CAPTURES]
    assert len(EXPECTED) >= 19


@pytest.mark.parametrize("capture", CAPTURES, ids=lambda path: path.name)
def test_a_native_import_publishes_the_bytes_it_did_before(capture: Path) -> None:
    assert artifacts(capture) == EXPECTED[capture.relative_to(ROOT).as_posix()]


def test_the_identity_is_recorded_but_not_published() -> None:
    """Control: the identity IS read and stored -- only never published."""
    from gpo_studio.backup import read_backup
    from gpo_studio.model import GPO

    capture = ROOT / "native-gpp-gpmc" / "WI01A-DriveMaps-GPMC"
    (source,) = read_backup(capture).gpos
    assert source.backup_inventory is not None
    assert source.backup_inventory.source_domain_guid == "67c09bd9-0326-42a4-a15f-7d1d878e469e"
    gpo = GPO(guid="g", name="n", backup_inventory=source.backup_inventory)
    assert "source_domain_guid" not in gpo.to_dict()["backup_inventory"]
    assert gpo.to_stored_dict()["backup_inventory"]["source_domain_guid"]


@pytest.mark.parametrize("capture", CAPTURES[:3], ids=lambda path: path.name)
def test_the_plan_carries_no_import_identity(capture: Path) -> None:
    """`apply.ps1` is left out of the pinned bundle digest (it changed on
    purpose after 5c6ea8a), so its share of this file's promise is checked
    here: the source GPO's GUID and its domain's GUID never reach it. The
    domain's DNS name does, by design -- it is the domain the plan binds to."""
    import io
    import zipfile

    from gpo_studio.backup import read_backup
    from gpo_studio.export import export_bundle
    from gpo_studio.model import GPO

    (source,) = read_backup(capture).gpos
    assert source.backup_inventory is not None
    identity = source.backup_inventory
    assert identity.source_gpo_guid and identity.source_domain_guid
    gpo = GPO(
        guid="11111111-2222-4333-8444-555555555555",
        name=source.display_name or "Imported GPO",
        source_guid=source.guid,
        backup_inventory=identity,
        domain=source.domain or "studio.local",
    )
    with zipfile.ZipFile(io.BytesIO(export_bundle(gpo))) as archive:
        plan = archive.read("apply.ps1").decode("utf-8-sig").casefold()
    for value in (source.guid, identity.source_gpo_guid, identity.source_domain_guid):
        assert value.strip("{}").casefold() not in plan, value
