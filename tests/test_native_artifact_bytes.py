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
