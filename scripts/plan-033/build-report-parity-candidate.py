#!/usr/bin/env python3
"""Build the Plan 034 report-parity candidate.

The lane asks one question: **does Studio's import of a GPMC backup inventory
the same settings as Windows' own fresh ``Get-GPOReport -ReportType Xml`` of
that backup?** The offline differ (``gpo_studio.report_parity``) answers it
against the report ``Backup-GPO`` wrote at capture time; the lane re-asks it of
a report Windows generates now, from a GPO the guest imports the backup into.

This builder emits two artifacts:

* ``report-parity-cases.zip`` -- every Windows-produced backup in the corpus,
  one directory per case, for the guest to import; and
* ``expected.json`` -- for each case, Studio's inventory of the same bytes,
  the inventory of the capture-time report, and the known divergences the
  offline compare names. It also carries the registry values the guest-authored
  case must show.

``expected.json`` never travels to the guest: the guest is the thing being
measured, so an expectation it could read would make the lane a comparison of
Windows with itself. The finalizer hash-binds both artifacts (WI-025).

**Import readiness.** Each case is checked offline before it is packaged: a
native manifest naming exactly one backup with a GPO GUID and backup ID, the
``{ID}`` directory holding ``Backup.xml`` whose core ID matches the manifest,
``bkupInfo.xml``, ``gpreport.xml`` and a ``DomainSysvol/GPO`` tree. A case
that fails is excluded with its reason recorded in ``expected.json``.

**One recorded transformation.** The WI-01A fixtures were sanitized
(``scripts/plan-033/sanitize-gpp-fixtures.py``) and their ``Backup.xml``
``SecurityDescriptor`` became the 8-byte placeholder ``01 00 04 80 00 00 00
00``, which is not a complete self-relative descriptor. ``Import-GPO`` imports
settings, not security, but whether it parses the field is unmeasured. Rather
than spend an estate run finding out, the candidate copy carries the
domain-neutral descriptor Studio's own exports carry (accepted by
``Import-GPO`` in the publication and WP-2 lanes), and the case records
``restore-importable-security-descriptor``. The descriptor is outside every
inventory this lane compares.

Invocation (deterministic: same inputs -> byte-identical outputs):

    python scripts/plan-033/build-report-parity-candidate.py <output-dir>
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Callable
from pathlib import Path

from gpo_studio.deterministic_zip import deterministic_zip
from gpo_studio.report_parity import (
    FamilyInventory,
    Inventory,
    InventoryItem,
    Side,
    classify,
    compare,
    studio_gpo_from_backup,
    studio_inventory,
    windows_inventory,
)

ARCHIVE_NAME = "report-parity-cases.zip"
EXPECTATION_NAME = "expected.json"
REPO_ROOT = Path(__file__).resolve().parents[2]


def _ordinal(base: Path) -> Callable[[Path], tuple[str, ...]]:
    """Sort key: the path's components below ``base``, compared ordinally.

    Never sort ``Path`` objects themselves. ``WindowsPath`` compares
    case-insensitively and ``PosixPath`` does not, so ``sorted(root.rglob("*"))``
    puts ``bkupInfo.xml`` before ``DomainSysvol/`` on Windows and after it on
    Linux. The archive's member order, and so its bytes, then depended on the
    controller's OS, and the finalizer's byte-identical rebuild check failed on
    a Windows checkout of an unchanged tree. Component tuples of ``str`` order
    the same everywhere, and match what a POSIX ``Path`` sort produced.
    """
    return lambda path: path.relative_to(base).parts

_MANIFEST_NS = "http://www.microsoft.com/GroupPolicy/GPOOperations/Manifest"
_BACKUP_NS = "http://www.microsoft.com/GroupPolicy/GPOOperations"
_PLACEHOLDER_SD = "01 00 04 80 00 00 00 00"
#: Byte-identical to ``gpo_studio.export._DOMAIN_NEUTRAL_SECURITY_DESCRIPTOR``
#: (held equal by tests/test_report_parity_oracle.py). Copied rather than
#: imported so this builder does not take a dependency on export.py.
DOMAIN_NEUTRAL_SD = (
    "01 00 04 80 14 00 00 00 24 00 00 00 00 00 00 00 34 00 00 00 "
    "01 02 00 00 00 00 00 05 20 00 00 00 20 02 00 00 01 02 00 00 "
    "00 00 00 05 20 00 00 00 20 02 00 00 02 00 34 00 02 00 00 00 "
    "00 00 14 00 00 00 00 10 01 01 00 00 00 00 00 05 12 00 00 00 "
    "00 00 18 00 00 00 00 10 01 02 00 00 00 00 00 05 20 00 00 00 "
    "20 02 00 00"
)
SD_TRANSFORMATION = "restore-importable-security-descriptor"

#: The guest-authored case: Set-GPRegistryValue on a key no ADMX template
#: describes, on both sides. run-report-parity.ps1 carries the same four values
#: independently; the finalizer checks Windows' report against this copy.
AUTHORED_KEY = r"Software\Policies\GPOStudio\ReportParity"
AUTHORED_VALUES: tuple[tuple[str, str, str, str], ...] = (
    ("computer", "MachineString", "String", "report-parity-machine"),
    ("computer", "MachineDword", "Number", "4242"),
    ("user", "UserString", "String", "report-parity-user"),
    ("user", "UserDword", "Number", "2424"),
)


#: The complete corpus, in build order. A case that disappears (a deleted
#: fixture, a narrowed glob) must be a reviewed edit here, not a silently
#: shorter candidate: the builder refuses to build, and the finalizer refuses a
#: candidate that does not carry exactly these cases.
REQUIRED_CASE_IDS: tuple[str, ...] = (
    "native-WI01A-DriveMaps-GPMC",
    "native-WI01A-EnvVars-GPMC",
    "native-WI01A-Files-GPMC",
    "native-WI01A-Folders-GPMC",
    "native-WI01A-IniFiles-GPMC",
    "native-WI01A-LocalGroups-GPMC",
    "native-WI01A-MixedCSE-GPMC",
    "native-WI01A-NestedILT-GPMC",
    "native-WI01A-OS-ILT",
    "native-WI01A-Power-GPMC",
    "native-WI01A-Printers-GPMC",
    "native-WI01A-SchedTasks-GPMC",
    "native-WI01A-SchedTasksFull-GPMC",
    "native-WI01A-Services-GPMC",
    "native-WI01A-ServicesRecovery-GPMC",
    "native-WI01A-Shortcuts-GPMC",
    # Batch 2 (WI-075): the GPP Registry native captures, so the family gets
    # report-parity certification. Their own corpus root keeps their
    # sanitization record separate; they enter here, after the GPMC-editor set.
    "native-WI01A-Registry-GPMC",
    "native-WI01A-RegistryMatrix-GPMC",
    "native-WI01A-RegistryShapes-GPMC",
    "evidence-wi059-20260908-wp0-backup",
    "evidence-wi059-20260908-scripts-metadata-rebackup",
    "evidence-wi059-20260908-wp1b-drives-user-rebackup",
    "evidence-wi059-20260908-wp1b-groups-machine-rebackup",
    "evidence-wi059-20260908-wp1b-localusers-machine-rebackup",
    "evidence-wi059-20260908-wp1b-mixed-all-rebackup",
    "evidence-wi059-20260908-wp1b-registry-both-rebackup",
    "evidence-wi059-20260908-wp1b-scheduledtasks-machine-rebackup",
    "evidence-wi059-20260908-wp1b-services-machine-rebackup",
    "evidence-wi059-20260908-wp2-rebackup",
    "evidence-backup-report-20260908-scripts-metadata-rebackup",
)

#: Cases that must agree with Windows with NO divergence at all, known or
#: otherwise. Until 1.1.0 each passed only on a pinned work-item divergence:
#: WI-072 on Power Options (the GlobalPowerOptionsV2 power plan was retained on
#: import and dropped on write) and WI-073 on both scheduled-task captures
#: (TaskV2 and ImmediateTaskV2 were written grouped, not interleaved). Both are
#: fixed in gpp.py and their allowances are gone from KNOWN_DIVERGENCES, so a
#: regression is already unexplained. This pin also stops one from passing by
#: being re-allowed as "known": the builder refuses, and the finalizer, which
#: rebuilds this candidate byte for byte, cannot pass a candidate it refuses.
MUST_AGREE_CASE_IDS: frozenset[str] = frozenset({
    "native-WI01A-Power-GPMC",
    "native-WI01A-SchedTasks-GPMC",
    "native-WI01A-SchedTasksFull-GPMC",
})


def corpus(repo: Path = REPO_ROOT) -> list[tuple[str, Path]]:
    """(case id, backup directory) for every Windows-produced backup.

    The same corpus as tests/test_backup_report_inventory.py: the WI-01A GPMC
    captures and the WI-059 native rebackups. Studio-produced candidate trees
    are excluded, since they are inputs rather than Windows output.
    """
    native = sorted(
        (repo / "tests/fixtures/native-gpp-gpmc").glob("*/manifest.xml"), key=_ordinal(repo)
    ) + sorted(
        (repo / "tests/fixtures/native-gpp-registry-gpmc").glob("*/manifest.xml"),
        key=_ordinal(repo),
    )
    evidence = sorted(
        (
            p for p in (repo / "docs/plan-033").glob("*-evidence/wi059-20260908/**/manifest.xml")
            if "rebackup" in p.parts or "backup" in p.parts
        ),
        key=_ordinal(repo),
    ) + [
        repo / "docs/plan-033/wp1b-evidence/backup-report-20260908/scripts-metadata/rebackup"
        / "manifest.xml"
    ]
    cases = [(f"native-{m.parent.name}", m.parent) for m in native]
    for manifest in evidence:
        parts = manifest.parent.relative_to(repo / "docs/plan-033").parts
        cases.append(("evidence-" + "-".join(parts[1:]), manifest.parent))
    ids = [case_id for case_id, _ in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("case ids collide")
    return cases


def _text(elem: ET.Element | None) -> str:
    return (elem.text or "").strip() if elem is not None else ""


def import_readiness(backup: Path) -> tuple[str, str] | str:
    """Return (backup id, GPO id), or the reason Import-GPO cannot take it."""
    try:
        manifest = ET.fromstring((backup / "manifest.xml").read_bytes())
    except (OSError, ET.ParseError) as exc:
        return f"manifest.xml unreadable: {exc}"
    insts = manifest.findall(f"{{{_MANIFEST_NS}}}BackupInst")
    if manifest.tag != f"{{{_MANIFEST_NS}}}Backups" or len(insts) != 1:
        return "manifest.xml is not a native single-backup manifest"
    backup_id = _text(insts[0].find(f"{{{_MANIFEST_NS}}}ID"))
    gpo_id = _text(insts[0].find(f"{{{_MANIFEST_NS}}}GPOGuid"))
    if not backup_id or not gpo_id:
        return "manifest.xml lacks a backup ID or GPO GUID"
    root = backup / backup_id
    for required in ("Backup.xml", "bkupInfo.xml", "gpreport.xml"):
        if not (root / required).is_file():
            return f"{backup_id}/{required} is missing"
    if not (root / "DomainSysvol" / "GPO").is_dir():
        return f"{backup_id}/DomainSysvol/GPO is missing"
    try:
        core = ET.fromstring((root / "Backup.xml").read_bytes()).find(
            f".//{{{_BACKUP_NS}}}GroupPolicyCoreSettings/{{{_BACKUP_NS}}}ID"
        )
    except ET.ParseError as exc:
        return f"Backup.xml unreadable: {exc}"
    if _text(core).casefold() != gpo_id.casefold():
        return "Backup.xml core ID does not match the manifest GPO GUID"
    return backup_id, gpo_id


def stage_case(source: Path, target: Path, backup_id: str) -> list[str]:
    """Copy one backup, applying (and returning) the recorded transformations."""
    shutil.copytree(source, target)
    backup_xml = target / backup_id / "Backup.xml"
    data = backup_xml.read_bytes()
    placeholder = f"<SecurityDescriptor>{_PLACEHOLDER_SD}</SecurityDescriptor>".encode()
    if placeholder in data:
        backup_xml.write_bytes(data.replace(
            placeholder, f"<SecurityDescriptor>{DOMAIN_NEUTRAL_SD}</SecurityDescriptor>".encode()
        ))
        return [SD_TRANSFORMATION]
    return []


def authored_windows_inventory() -> Inventory:
    """What Windows' report of the guest-authored GPO must list, as a multiset."""
    families = []
    sides: tuple[Side, ...] = ("computer", "user")
    for side in sides:
        items = tuple(sorted(
            InventoryItem(
                element="RegistrySetting", key=AUTHORED_KEY, name=name, value=f"{kind}:{value}",
            )
            for value_side, name, kind, value in AUTHORED_VALUES
            if value_side == side
        ))
        families.append(FamilyInventory(side=side, family="RegistrySettings", items=items))
    return Inventory(families=tuple(families))


#: Where the guest extracts the archive, at its longest: the driver's run root
#: ``C:\gpo-studio\rp\<yymmddHHMMSS>``, then ``out\run\in``. Windows
#: PowerShell 5.1's ``Expand-Archive`` is bound by MAX_PATH (260); the first
#: estate run lost every case to it with a 110-character root and 55-character
#: case directories. The driver and guest script are held to this prefix by
#: tests/test_report_parity_oracle.py.
GUEST_EXTRACT_PREFIX = "C:\\gpo-studio\\rp\\000000000000\\out\\run\\in\\"
#: The longest guest-side path any archive member may produce. Well under
#: MAX_PATH, leaving room for a longer run root before anything breaks.
MAX_GUEST_PATH = 200
#: Archive member listing each case directory and the case it holds; the guest
#: drives its loop from it and fails if a listed directory did not extract.
CASE_INDEX = "cases/index.tsv"


def case_dir(case_id: str) -> str:
    """The short archive directory for a case: its position in the corpus."""
    return f"c{REQUIRED_CASE_IDS.index(case_id) + 1:02d}"


def longest_guest_path(archive: bytes) -> tuple[int, str]:
    """(length, member) of the longest path extraction produces on the guest."""
    with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
        paths = [GUEST_EXTRACT_PREFIX + name.replace("/", "\\") for name in zipped.namelist()]
    longest = max(paths, key=len)
    return len(longest), longest


def _zip(root: Path) -> bytes:
    """The candidate archive, byte-identical on every controller platform.

    `gpo_studio.deterministic_zip` fixes member order, timestamps, host byte,
    attributes and compression (STORED: Windows and Linux deflate differ), so
    the exact-hash rebuild check holds on a Windows checkout too.
    """
    return deterministic_zip({
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    })


def build(out: Path, repo: Path = REPO_ROOT) -> dict[str, object]:
    found = tuple(case_id for case_id, _ in corpus(repo))
    if found != REQUIRED_CASE_IDS:
        missing = sorted(set(REQUIRED_CASE_IDS) - set(found))
        extra = sorted(set(found) - set(REQUIRED_CASE_IDS))
        raise ValueError(f"corpus differs from REQUIRED_CASE_IDS: missing {missing}, extra {extra}")
    cases: list[dict[str, object]] = []
    excluded: list[dict[str, str]] = []
    with tempfile.TemporaryDirectory() as tmp:
        staging = Path(tmp) / "cases"
        staging.mkdir()
        for case_id, source in corpus(repo):
            readiness = import_readiness(source)
            if isinstance(readiness, str):
                excluded.append({"case_id": case_id, "reason": readiness})
                continue
            backup_id, gpo_id = readiness
            staged = staging / case_dir(case_id)
            transformations = stage_case(source, staged, backup_id)
            gpo = studio_gpo_from_backup(staged)
            assert gpo.backup_inventory is not None
            report = base64.b64decode(gpo.backup_inventory.report_xml_base64)
            ours = studio_inventory(gpo)
            backup_report = windows_inventory(report)
            known, unexplained = classify(compare(backup_report, ours))
            if unexplained:
                raise ValueError(
                    f"{case_id}: offline divergence nothing names: "
                    + "; ".join(d.describe() for d in unexplained)
                )
            if case_id in MUST_AGREE_CASE_IDS and known:
                raise ValueError(
                    f"{case_id}: must agree with Windows exactly (WI-072/WI-073), "
                    f"but shows known divergence(s) {sorted(known)}"
                )
            cases.append({
                "case_id": case_id,
                "dir": case_dir(case_id),
                "source": source.relative_to(repo).as_posix(),
                "backup_id": backup_id,
                "source_gpo_id": gpo_id,
                "transformations": transformations,
                "studio_inventory": ours.to_json(),
                "backup_report_inventory": backup_report.to_json(),
                "backup_report_sha256": hashlib.sha256(report).hexdigest(),
                "expected_known": sorted(known),
            })
        (staging / "index.tsv").write_bytes("".join(
            f"{case['dir']}\t{case['case_id']}\n" for case in cases
        ).encode("ascii"))
        archive = _zip(staging.parent)
    length, longest = longest_guest_path(archive)
    if length > MAX_GUEST_PATH:
        raise ValueError(
            f"guest path of {length} characters exceeds {MAX_GUEST_PATH}: {longest}"
        )
    (out / ARCHIVE_NAME).write_bytes(archive)
    expectation: dict[str, object] = {
        "schema_version": 1,
        "cases": cases,
        "excluded": excluded,
        "authored": {
            "key": AUTHORED_KEY,
            "values": [list(v) for v in AUTHORED_VALUES],
            "windows_inventory": authored_windows_inventory().to_json(),
        },
        "exclusions": [
            "admx-policy-rendering",
            "scripts-not-modeled",
            "links-filters-wmi (lifecycle lane)",
            "preference Properties attributes (identity, uid and action only)",
        ],
    }
    (out / EXPECTATION_NAME).write_bytes(
        json.dumps(expectation, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    )
    return expectation


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    try:
        expectation = build(out)
    except ValueError as exc:
        print(f"candidate refused: {exc}", file=sys.stderr)
        return 2
    for name in (ARCHIVE_NAME, EXPECTATION_NAME):
        print(f"{name} sha256={hashlib.sha256((out / name).read_bytes()).hexdigest()}")
    cases = expectation["cases"]
    excluded = expectation["excluded"]
    assert isinstance(cases, list) and isinstance(excluded, list)
    print(f"cases={len(cases)} excluded={len(excluded)}")
    for case in cases:
        print(f"  {case['case_id']}: known={case['expected_known']} "
              f"transformations={case['transformations']}")
    for entry in excluded:
        print(f"  EXCLUDED {entry['case_id']}: {entry['reason']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
