#!/usr/bin/env python3
"""Build the R10 candidate bundle: ``studio-scripts-backup.zip``.

Work order R10 (``docs/manual-evidence-requests.md``, "Does Windows accept a
Studio-written ``scripts.ini``?") is gated on this artifact existing: import a
Studio-produced GPMC backup carrying ``Machine\\Scripts\\scripts.ini`` plus the
Scripts CSE GUID, let GPMC render it, and back it up again. This script
produces exactly that bundle through the real export path,
``gpo_studio.export.gpmc_backup_bundle`` -- the code whose scripts.ini /
psscripts.ini output reproduces the banked R2 WS2025 GPMC captures
byte-for-byte in ``tests/test_native_scripts_export.py``.

Contents (synthetic throughout; nothing here touches a directory or SYSVOL):

* computer side only -- the work order's probe imports a backup carrying
  ``Machine\\Scripts\\scripts.ini``; no user-side variant is asked for;
* two legacy ``.cmd`` startup entries and one PowerShell ``.ps1`` startup
  entry -- precisely the three entries the R10 operator is told to look for
  in the GPMC editor;
* the non-default PowerShell ordering (``[ScriptsConfig]
  StartExecutePSFirst=true``) -- the dropdown value R10 asks about;
* entry names and parameters matching the banked R2 captures, so
  ``scripts.ini`` and ``psscripts.ini`` land byte-identical to what GPMC
  itself wrote (the strongest hand-off: Windows re-emitting them after import
  diffs against its own measured output).

States with no measured native encoding are refused, not approximated: the
bundle is built through ``gpmc_backup_bundle``, whose
``_native_scripts_refusal`` raises on any unencodable script state (async
entries, sync flags, PowerShell switches). This script surfaces that refusal
loudly on stderr with exit code 2 rather than bypassing it.

Invocation (deterministic: same inputs -> byte-identical zip):

    python scripts/plan-033/build-scripts-backup-candidate.py <output-dir>

writes ``<output-dir>/studio-scripts-backup.zip`` -- the file name the R10
steps already reference -- and prints a per-member byte manifest plus the
archive SHA-256. Verify before sending, per the work order's WP-2 convention:

    Get-FileHash <output-dir>\\studio-scripts-backup.zip -Algorithm SHA256
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import zipfile
from pathlib import Path

from gpo_studio.export import gpmc_backup_bundle, native_backup_id
from gpo_studio.model import GPO, ValidationError
from gpo_studio.script_policy import PowerShellScriptEntry, ScriptEntry, ScriptPolicy

#: The artifact name the R10 steps expect (work order: "once you have
#: ``studio-scripts-backup.zip``").
ARCHIVE_NAME = "studio-scripts-backup.zip"

# Fixed synthetic identity, per the R9/WP-2 builder conventions: fixed GUID,
# zz-studio-evidence-* display name (the family the work order itself uses for
# R10's lab GPO), synthetic.test domain, no estate identifiers anywhere.
_GPO = GPO(
    guid="22222222-3333-4444-5555-666666666666",
    name="zz-studio-evidence-10-scripts",
    domain="synthetic.test",
)

# Computer-side Scripts policy: the R10 oracle's "two .cmd entries and one
# .ps1 entry", with the non-default PowerShell ordering. Names and parameters
# are the banked R2 capture's, so both INIs are byte-identical to the
# measurement instead of merely shape-compatible.
_SCRIPTS: dict[str, ScriptPolicy] = {
    "computer": ScriptPolicy(
        startup=(
            ScriptEntry(
                script_id="r10-startup-0",
                artifact_id="r10-artifact-0",
                original_name="zz-studio-marker.cmd",
                parameters="/c alpha beta",
            ),
            ScriptEntry(
                script_id="r10-startup-1",
                artifact_id="r10-artifact-1",
                original_name="zz-studio-second.cmd",
            ),
        ),
        powershell_startup=(
            PowerShellScriptEntry(
                script_id="r10-startup-ps-0",
                artifact_id="r10-ps-artifact-0",
                original_name="zz-studio-marker.ps1",
                parameters="-Mode Alpha",
            ),
        ),
        powershell_order="run_windows_powershell_scripts_first",
    ),
}


def build_bundle() -> bytes:
    """Return the deterministic R10 zip bytes via the real export path.

    No local re-encoding happens here: everything (INIs, Backup.xml extension
    registration, manifest, zip layout) comes from ``gpmc_backup_bundle``, so
    the bundle cannot drift from what the export tests pin.
    """
    return gpmc_backup_bundle(_GPO, scripts=_SCRIPTS)


def _print_manifest(archive_path: Path, data: bytes) -> None:
    print(f"backup id: {native_backup_id(_GPO)}")
    print(f"archive: {archive_path} ({len(data)} bytes)")
    print(f"archive sha256: {hashlib.sha256(data).hexdigest()}")
    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
        print(f"members ({len(names)}):")
        for name in names:
            member = archive.read(name)
            digest = hashlib.sha256(member).hexdigest()
            print(f"  {name}  {len(member)} bytes  sha256:{digest}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    try:
        data = build_bundle()
    except ValidationError as error:
        # Surface the export path's own refusal (no measured native encoding
        # for some state in _SCRIPTS); never write a partial artifact.
        print("REFUSED: the export path declined to encode this candidate:", file=sys.stderr)
        for issue in error.issues:
            print(f"  [{issue.code}] {issue.path}: {issue.message}", file=sys.stderr)
        return 2

    archive_path = args.output_dir / ARCHIVE_NAME
    archive_path.write_bytes(data)
    print(f"wrote {archive_path} ({archive_path.stat().st_size} bytes)")
    _print_manifest(archive_path, data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
