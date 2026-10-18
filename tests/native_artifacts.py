"""The artifacts a native GPMC capture's import produces, for the byte-identity
test (final-r22): the GPO the backup import builds -- by the same calls the
route makes, with a fixed GUID and times -- and the SHA-256 of its public dict,
both digests and the Studio bundle with its manifest.

Kept free of anything newer than the code it is compared with, so the
expected values in ``native_artifacts.json`` could be produced by running
`artifacts` against 5c6ea8a's source (`python tests/native_artifacts.py
<src-dir>`), and the test runs it against the current one.
"""

from __future__ import annotations

import hashlib
import io
import itertools
import json
import sys
import uuid
import zipfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent / "fixtures"
CAPTURES = sorted(
    path
    for family in ("native-gpp-gpmc", "native-gpp-registry-gpmc")
    for path in (ROOT / family).iterdir()
    if path.is_dir()
)
GUID = "11111111-2222-4333-8444-555555555555"
TIME = "2026-10-18T00:00:00+00:00"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def artifacts(capture: Path) -> dict[str, str]:
    """The artifacts' digests, with every id the import mints made
    deterministic (a counter in place of ``uuid.uuid4``)."""
    counter = itertools.count(1)
    real = uuid.uuid4
    uuid.uuid4 = lambda: uuid.UUID(int=next(counter), version=4)
    try:
        return _artifacts(capture)
    finally:
        uuid.uuid4 = real


def _artifacts(capture: Path) -> dict[str, str]:
    from gpo_studio import gpp_provenance
    from gpo_studio.backup import read_backup
    from gpo_studio.canonical import policy_semantic_sha256, review_model_sha256
    from gpo_studio.export import export_bundle
    from gpo_studio.import_export import (
        backup_security_filters_to_model,
        backup_wmi_filter_to_model,
        collect_cse_metadata,
        collect_gpp_collections,
        extract_side_settings,
    )
    from gpo_studio.model import GPO

    backup = read_backup(capture)
    (source,) = backup.gpos
    root = source.content_root
    assert root is not None
    gpo = GPO(
        guid=GUID,
        name=source.display_name or "Imported GPO",
        description=f"Imported from GPMC backup {backup.backup_id}",
        status="archived",
        revision=1,
        settings=tuple(
            extract_side_settings(root, "computer") + extract_side_settings(root, "user")
        ),
        source_guid=source.guid,
        cse_metadata=collect_cse_metadata(source),
        backup_inventory=source.backup_inventory,
        fdeploy=source.fdeploy,
        domain=source.domain or "studio.local",
        security_filters=backup_security_filters_to_model(source.security_filters),
        wmi_filter=backup_wmi_filter_to_model(source.wmi_filter),
        gpp_collections=tuple(
            gpp_provenance.mint(collection) for collection in collect_gpp_collections(root)
        ),
        computer_enabled=source.computer_enabled,
        user_enabled=source.user_enabled,
        created_at=TIME,
        updated_at=TIME,
    )
    out: dict[str, str] = {
        "dict": _sha(json.dumps(gpo.to_dict(), sort_keys=True).encode()),
        "policy": policy_semantic_sha256(gpo),
        "review": review_model_sha256(gpo),
    }
    try:
        bundle = export_bundle(gpo)
    except Exception as error:  # noqa: BLE001 - the refusal is the artifact
        out["bundle"] = f"refused: {type(error).__name__}"
    else:
        out["bundle"] = _sha(bundle)
        with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
            out["manifest"] = _sha(archive.read("manifest.json"))
    return out


def main(argv: list[str]) -> None:
    sys.path.insert(0, argv[1])
    result: dict[str, Any] = {
        capture.relative_to(ROOT).as_posix(): artifacts(capture) for capture in CAPTURES
    }
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main(sys.argv)
