#!/usr/bin/env python3
"""Build the Plan 034 fdeploy-lane candidate.

The 2026-10-07 ruling gave ``fdeploy.py`` a lane: **banked R3 bytes go through
``Import-GPO``, then ``Backup-GPO``/``Get-GPOReport`` are compared with the
parse.** This builder emits the two artifacts that lane consumes:

* ``fdeploy-cases.zip`` -- one GPMC-format backup per case, for the guest to
  import into a disposable GPO; and
* ``expected.json`` -- per case, the bytes' hashes and encoding facts, what
  Studio's reader must claim about them, and what Windows' report must render.
  It never travels to the guest.

**The cases.** ``r3-flags-1021`` carries R3's ``fdeploy.ini`` and
``fdeploy1.ini`` byte for byte: GPMC wrote them on 2026-09-04, and they are
rebuilt here from the committed transcripts and refused unless they hash to
the provenance record's ``raw_sha256``. The other three change only the four
ASCII digits of ``Flags`` (1020, 1023, 3069), exactly as the 2026-10-08 probe
did. Those three files were written by this builder, not by Windows; what they
add is Windows' reading of each value through ``Import-GPO``. The values are
the ones whose report rendering the probe saw carry the ``FullPath`` as
``DestinationPath``; see ``EXCLUDED_FLAGS`` for the rest and why.

**The backup skeleton** follows the native backup the 2026-10-08 probe took
of a GPO carrying these files (``Backup-GPO`` on LabMS01): the Folder
Redirection extension entry with its ``FRValidateSettings`` re-evaluation
hook, the file-path list, the generic directory entry for ``Documents &
Settings``, and the extension pair R3 measured on the GPO object --
sanitized to synthetic identity (``synthetic.test``, uuid5 GUIDs, ``UNKNOWN``
controller) and carrying Studio's domain-neutral security descriptor, which
``Import-GPO`` already accepted in the publication and WP-2 lanes.

**What the expectation is built from.** The reader expectation (folder GUID,
principal, ``FullPath``, ``Flags``) is the R3 provenance record's values, held
as constants here; the builder refuses to build unless Studio's parse of every
staged case equals them, so ``expected.json`` is not just Studio describing
itself. Windows' option rendering per ``Flags`` value from the probe is carried
as **recorded** data for WI-066 and is never asserted: ``fdeploy.py`` decodes
no bit, so there is no Studio claim to hold it against.

Invocation (deterministic: same inputs -> byte-identical outputs):

    python scripts/plan-033/build-fdeploy-candidate.py <output-dir>
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from uuid import NAMESPACE_DNS, NAMESPACE_URL, uuid5
from xml.sax.saxutils import quoteattr

from gpo_studio.backup import read_backup
from gpo_studio.deterministic_zip import deterministic_zip
from gpo_studio.fdeploy import (
    FDEPLOY_POLICY_PATH,
    FLAGS_ARE_UNDECODED,
    FOLDER_REDIRECTION_CSE_GUID,
    native_digest,
    read_fdeploy,
    validate_fdeploy,
)
from gpo_studio.fdeploy_parity import encoding_facts, reader_claims

ARCHIVE_NAME = "fdeploy-cases.zip"
EXPECTATION_NAME = "expected.json"
REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = "tests/fixtures/native-folder-redirection-gpmc"

_MANIFEST_NS = "http://www.microsoft.com/GroupPolicy/GPOOperations/Manifest"
_BACKUP_NS = "http://www.microsoft.com/GroupPolicy/GPOOperations"

#: The facts R3's provenance record banks, restated so the expectation does
#: not come from the parser it grades. Held equal to the fixture by the build.
R3_FOLDER_GUID = "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}"
R3_PRINCIPAL = "s-1-1-0"
R3_FULL_PATH = r"\\zz-studio-fileserver\zzredir\%USERNAME%\Documents"
R3_FLAGS = 1021

#: The pair R3 measured on gPCUserExtensionNames: the Folder Redirection CSE
#: and its tool extension, in that order, one bracket group.
USER_EXTENSION_PAIR = (
    f"[{FOLDER_REDIRECTION_CSE_GUID}{{88E729D6-BDC1-11D1-BD2A-00C04FB9603F}}]"
)

DOMAIN = "synthetic.test"
#: Byte-identical to ``gpo_studio.export._DOMAIN_NEUTRAL_SECURITY_DESCRIPTOR``
#: (held equal by tests/test_fdeploy_lane.py). Copied, not imported, so the
#: builder does not take a dependency on export.py, which two lanes bind.
DOMAIN_NEUTRAL_SD = (
    "01 00 04 80 14 00 00 00 24 00 00 00 00 00 00 00 34 00 00 00 "
    "01 02 00 00 00 00 00 05 20 00 00 00 20 02 00 00 01 02 00 00 "
    "00 00 00 05 20 00 00 00 20 02 00 00 02 00 34 00 02 00 00 00 "
    "00 00 14 00 00 00 00 10 01 01 00 00 00 00 00 05 12 00 00 00 "
    "00 00 18 00 00 00 00 10 01 02 00 00 00 00 00 05 20 00 00 00 "
    "20 02 00 00"
)
_BACKUP_TIME = "2026-10-08T00:00:00"
_REGISTRY_CSE_GUID = "{35378EAC-683F-11D2-A89A-00C04FBBCFA2}"
_FILE_COPY_EXTENSION_GUID = "{F15C46CD-82A0-4C2D-A210-5D0D3182A418}"

#: (case id, Flags value). The first is R3 verbatim. A case that disappears
#: must be a reviewed edit here: the finalizer refuses any other set.
CASES: tuple[tuple[str, int], ...] = (
    ("r3-flags-1021", 1021),
    ("r3-flags-1020", 1020),
    ("r3-flags-1023", 1023),
    ("r3-flags-3069", 3069),
)
REQUIRED_CASE_IDS: tuple[str, ...] = tuple(case_id for case_id, _ in CASES)
VERBATIM_CASE = "r3-flags-1021"

#: Windows' option rendering for each case's Flags value, as the 2026-10-08
#: probe recorded it (Get-GPOReport over a GPO whose fdeploy files were written
#: to SYSVOL directly; capture at gpo-studio-evidence/inbox/fdeploy-flags-20261008,
#: not banked in this repository). RECORDED, NOT ASSERTED: the finalizer notes
#: whether the lane's fresh rendering agrees, as data for WI-066.
_BASE_OPTIONS = {
    "GrantExclusiveRights": "false",
    "MoveContents": "true",
    "FollowParent": "false",
    "ApplyToDownLevel": "false",
    "DoNotCare": "false",
    "RedirectToLocal": "false",
    "PolicyRemovalBehavior": "RestoreContents",
    "ConfigurationControl": "GP",
    "PrimaryComputerEvaluation": "PrimaryComputerPolicyDisabled",
}
PROBE_20261008_OPTIONS: dict[int, dict[str, str]] = {
    1021: dict(_BASE_OPTIONS),
    1020: {**_BASE_OPTIONS, "MoveContents": "false"},
    1023: {**_BASE_OPTIONS, "FollowParent": "true"},
    3069: {**_BASE_OPTIONS, "RedirectToLocal": "true"},
}

#: Flags values the probe measured that this lane deliberately does not run,
#: and why. Each would fail the reader/report agreement by construction, which
#: is itself the finding: for these values FullPath is not what Windows calls
#: the destination, or Windows cannot read the setting at all.
EXCLUDED_FLAGS: dict[str, str] = {
    "0, 509, 893, 957, 989": (
        "report renders no Folder: ExtensionData carries 'FRSettingRead failed "
        "with -2147467259'"
    ),
    "765, 2045": (
        "report renders the Folder with an empty DestinationPath while the file "
        "carries FullPath"
    ),
    "1005, 1013, 1017, 5117": (
        "rendered DestinationPath = FullPath in the probe; not run, to keep the "
        "lane to the values the ruling named"
    ),
}


def _transcript_bytes(repo: Path, name: str) -> bytes:
    """Rebuild native bytes from a banked transcript, as its provenance says."""
    transcript = (repo / FIXTURE_DIR / name).read_text(encoding="ascii")
    header, counters, content = transcript.split("\n", 2)
    if header != "first 4 bytes: FF FE 0D 00":
        raise ValueError(f"{name}: unexpected transcript header")
    match = re.search(r"size: (\d+) bytes", counters)
    if match is None:
        raise ValueError(f"{name}: transcript names no size")
    native = b"\xff\xfe" + content.replace("\n", "\r\n").encode("utf-16-le")
    if len(native) != int(match.group(1)):
        raise ValueError(f"{name}: rebuilt {len(native)} bytes, transcript says {match.group(1)}")
    return native


def r3_bytes(repo: Path = REPO_ROOT) -> tuple[bytes, bytes]:
    """(marker, policy): R3's two files, refused unless they hash to provenance."""
    provenance = json.loads((repo / FIXTURE_DIR / "provenance.json").read_text("utf-8"))
    out: list[bytes] = []
    for name in ("fdeploy.ini.txt", "fdeploy1.ini.txt"):
        data = _transcript_bytes(repo, name)
        record = provenance["files"][name]
        facts = encoding_facts(data)
        if (
            facts["sha256"] != record["raw_sha256"]
            or facts["size"] != record["measured_total_bytes"]
            or facts["cr_count"] != record["measured_cr_count"]
            or facts["lf_count"] != record["measured_lf_count"]
            or facts["crlf_only"] is not record["measured_crlf_only"]
        ):
            raise ValueError(f"{name}: rebuilt bytes disagree with the provenance record")
        out.append(data)
    return out[0], out[1]


def case_policy_bytes(r3_policy: bytes, flags: int) -> bytes:
    """R3's policy with only the Flags digits replaced (the probe's construction)."""
    old = f"Flags={R3_FLAGS}\r\n".encode("utf-16-le")
    new = f"Flags={flags}\r\n".encode("utf-16-le")
    if r3_policy.count(old) != 1:
        raise ValueError("R3 policy does not carry exactly one Flags=1021 line")
    return r3_policy.replace(old, new)


def _guid(kind: str, case_id: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"gpo-studio/fdeploy-lane/{kind}/{case_id}"))


def case_identity(case_id: str) -> dict[str, str]:
    """Synthetic, deterministic identity for one case's source backup."""
    return {
        "source_gpo_id": "{" + _guid("source", case_id) + "}",
        "backup_id": "{" + _guid("backup", case_id).upper() + "}",
        "display_name": f"zz-studio-fdeploy-{case_id}",
    }


def _cdata(value: str) -> str:
    if "]]>" in value:
        raise ValueError("value cannot be carried in CDATA")
    return f"<![CDATA[{value}]]>"


def _domain(identity: dict[str, str]) -> str:
    return identity.get("domain", DOMAIN)


def _backup_inst(identity: dict[str, str]) -> str:
    domain = _domain(identity)
    domain_guid = "{" + str(uuid5(NAMESPACE_DNS, domain.casefold())) + "}"
    fields = (
        ("GPOGuid", identity["source_gpo_id"]),
        ("GPODomain", domain),
        ("GPODomainGuid", domain_guid),
        ("GPODomainController", "UNKNOWN"),
        ("BackupTime", _BACKUP_TIME),
        ("ID", identity["backup_id"]),
        ("Comment", ""),
        ("GPODisplayName", identity["display_name"]),
    )
    return "".join(f"<{name}>{_cdata(value)}</{name}>" for name, value in fields)


def manifest_xml(identity: dict[str, str]) -> bytes:
    return (
        f'<Backups xmlns="{_MANIFEST_NS}" xmlns:mfst="{_MANIFEST_NS}" mfst:version="1.0">'
        f"<BackupInst>{_backup_inst(identity)}</BackupInst></Backups>"
    ).encode()


def bkup_info_xml(identity: dict[str, str]) -> bytes:
    return (
        f'<BackupInst xmlns="{_MANIFEST_NS}">{_backup_inst(identity)}</BackupInst>\r\n'
    ).encode()


def backup_xml(identity: dict[str, str]) -> bytes:
    """``Backup.xml`` in the shape Windows wrote for the probe GPO, sanitized."""
    gpo = identity["source_gpo_id"]

    def source(relative: str) -> str:
        return rf"\\UNKNOWN\SYSVOL\{_domain(identity)}\Policies\{gpo}\{relative}"

    settings = r"Documents & Settings"

    def file_ref(name: str) -> str:
        return (
            "<FSObjectFile"
            f" bkp:Path={quoteattr(rf'%GPO_USER_FSPATH%\{settings}\{name}')}"
            f" bkp:SourceExpandedPath={quoteattr(source(rf'User\{settings}\{name}'))}"
            f" bkp:Location={quoteattr(rf'DomainSysvol\GPO\User\{settings}\{name}')}/>"
        )

    core = "".join((
        f"<ID>{_cdata(gpo)}</ID>",
        f"<Domain>{_cdata(_domain(identity))}</Domain>",
        f"<SecurityDescriptor>{DOMAIN_NEUTRAL_SD}</SecurityDescriptor>",
        f"<DisplayName>{_cdata(identity['display_name'])}</DisplayName>",
        f"<Options>{_cdata('0')}</Options>",
        f"<UserVersionNumber>{_cdata('65537')}</UserVersionNumber>",
        f"<MachineVersionNumber>{_cdata('0')}</MachineVersionNumber>",
        "<MachineExtensionGuids/>",
        f"<UserExtensionGuids>{_cdata(USER_EXTENSION_PAIR)}</UserExtensionGuids>",
        "<WMIFilter/>",
    ))
    text = (
        '<?xml version="1.0" encoding="utf-8"?>'
        "<!-- Copyright (c) Microsoft Corporation.  All rights reserved. -->"
        '<GroupPolicyBackupScheme bkp:version="2.0" bkp:type="GroupPolicyBackupTemplate"'
        f' xmlns:bkp="{_BACKUP_NS}" xmlns="{_BACKUP_NS}">\r\n'
        "    <GroupPolicyObject><SecurityGroups/>"
        f"<FilePaths><Path>{_cdata(R3_FULL_PATH)}</Path></FilePaths>"
        f"<GroupPolicyCoreSettings>{core}</GroupPolicyCoreSettings>\r\n"
        f'        <GroupPolicyExtension bkp:ID="{_REGISTRY_CSE_GUID}" bkp:DescName="Registry">'
        f"<FSObjectFile bkp:Path={quoteattr(r'%GPO_FSPATH%\Adm\*.*')}"
        f" bkp:SourceExpandedPath={quoteattr(source(r'Adm\*.*'))}/>"
        "</GroupPolicyExtension>\r\n"
        f'        <GroupPolicyExtension bkp:ID="{FOLDER_REDIRECTION_CSE_GUID}"'
        ' bkp:DescName="Folder Redirection">'
        f"<FSObjectFile bkp:Path={quoteattr(rf'%GPO_USER_FSPATH%\{settings}\*')}"
        f" bkp:SourceExpandedPath={quoteattr(source(rf'User\{settings}\*'))}"
        ' bkp:ReEvaluateFunction="FRValidateSettings">'
        f"{file_ref('fdeploy1.ini')}{file_ref('fdeploy.ini')}"
        "</FSObjectFile></GroupPolicyExtension>\r\n"
        f'    <GroupPolicyExtension bkp:ID="{_FILE_COPY_EXTENSION_GUID}"'
        ' bkp:DescName="Unknown Extension">'
        f"<FSObjectDir bkp:Path={quoteattr(rf'%GPO_USER_FSPATH%\{settings}')}"
        f" bkp:SourceExpandedPath={quoteattr(source(rf'User\{settings}'))}"
        f" bkp:Location={quoteattr(rf'DomainSysvol\GPO\User\{settings}')}/>"
        "</GroupPolicyExtension></GroupPolicyObject>\r\n"
        "</GroupPolicyBackupScheme>\r\n"
    )
    return text.encode("utf-8")


def _text(elem: ET.Element | None) -> str:
    return (elem.text or "").strip() if elem is not None else ""


#: The identity fields a ``BackupInst`` (manifest entry or ``bkupInfo.xml``)
#: must carry, as the probe's native backup carries them.
BACKUP_INST_FIELDS = ("GPOGuid", "GPODomain", "ID", "GPODisplayName")


def backup_inst_fields(element: ET.Element) -> dict[str, str] | str:
    """The identity fields of one ``BackupInst``, or why it has none to give."""
    if element.tag != f"{{{_MANIFEST_NS}}}BackupInst":
        return f"not a BackupInst: {element.tag}"
    fields = {
        name: _text(element.find(f"{{{_MANIFEST_NS}}}{name}")) for name in BACKUP_INST_FIELDS
    }
    empty = [name for name, value in fields.items() if not value]
    if empty:
        return f"BackupInst lacks {', '.join(empty)}"
    return fields


def backup_info(backup: Path, backup_id: str) -> dict[str, str] | str:
    """Parse ``{ID}/bkupInfo.xml``: its identity fields, or why they are unusable."""
    try:
        root = ET.fromstring((backup / backup_id / "bkupInfo.xml").read_bytes())
    except (OSError, ET.ParseError) as exc:
        return f"bkupInfo.xml unreadable: {exc}"
    return backup_inst_fields(root)


def same_guid(left: str, right: str) -> bool:
    return left.strip().strip("{}").casefold() == right.strip().strip("{}").casefold()


def import_readiness(backup: Path) -> tuple[str, str] | str:
    """Return (backup id, GPO id), or the reason Import-GPO cannot take it.

    The same offline check the report-parity lane makes of a native backup,
    restated here so this lane binds no other lane's builder -- plus what that
    check lacked (review finding 3): ``bkupInfo.xml`` is parsed, every identity
    field in it and in the manifest entry must be present, and the two must
    name the same GPO, backup ID, domain and display name.
    """
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
    for required in ("Backup.xml", "bkupInfo.xml"):
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
    listed = backup_inst_fields(insts[0])
    if isinstance(listed, str):
        return f"manifest.xml: {listed}"
    info = backup_info(backup, backup_id)
    if isinstance(info, str):
        return info
    if not (
        same_guid(info["GPOGuid"], listed["GPOGuid"])
        and same_guid(info["ID"], listed["ID"])
        and info["GPODomain"].casefold() == listed["GPODomain"].casefold()
        and info["GPODisplayName"] == listed["GPODisplayName"]
    ):
        return "bkupInfo.xml names a different GPO, backup, domain or name than manifest.xml"
    return backup_id, gpo_id


def expected_reader(flags: int) -> list[dict[str, object]]:
    return [{
        "folder_guid": R3_FOLDER_GUID,
        "principal": R3_PRINCIPAL,
        "full_path": R3_FULL_PATH,
        "flags": flags,
        "flags_text": str(flags),
    }]


def expected_report() -> list[list[str]]:
    """``(Folder Id, SecurityGroup SID, DestinationPath)`` rows Windows must render."""
    return [[R3_FOLDER_GUID, R3_PRINCIPAL, R3_FULL_PATH]]


def stage_case(root: Path, identity: dict[str, str], marker: bytes, policy: bytes) -> None:
    backup_root = root / identity["backup_id"]
    settings = backup_root / "DomainSysvol" / "GPO" / "User" / "Documents & Settings"
    settings.mkdir(parents=True)
    (root / "manifest.xml").write_bytes(manifest_xml(identity))
    (backup_root / "Backup.xml").write_bytes(backup_xml(identity))
    (backup_root / "bkupInfo.xml").write_bytes(bkup_info_xml(identity))
    (settings / "fdeploy.ini").write_bytes(marker)
    (settings / "fdeploy1.ini").write_bytes(policy)


def check_staged(case_id: str, staged: Path, identity: dict[str, str], flags: int,
                 policy: bytes) -> None:
    """Refuse a case Studio itself would not read as the expectation says."""
    readiness = import_readiness(staged)
    if isinstance(readiness, str):
        raise ValueError(f"{case_id}: not import-ready: {readiness}")
    if readiness != (identity["backup_id"], identity["source_gpo_id"]):
        raise ValueError(f"{case_id}: staged identity differs from the case identity")
    backup = read_backup(staged)
    if len(backup.gpos) != 1 or backup.gpos[0].fdeploy is None:
        raise ValueError(f"{case_id}: Studio does not read one GPO carrying {FDEPLOY_POLICY_PATH}")
    document = backup.gpos[0].fdeploy
    claims = [claim.to_json() for claim in reader_claims(document)]
    if claims != expected_reader(flags):
        raise ValueError(
            f"{case_id}: Studio reads {claims}, provenance says {expected_reader(flags)}"
        )
    if validate_fdeploy(document) or document.parse_warnings:
        raise ValueError(f"{case_id}: the staged document has structural findings")
    if native_digest(document)[0] != hashlib.sha256(policy).hexdigest():
        raise ValueError(f"{case_id}: the parse does not hash to the staged bytes")
    if read_fdeploy(policy).raw_text != document.raw_text:
        raise ValueError(f"{case_id}: read_backup and read_fdeploy disagree")


def case_dir(case_id: str) -> str:
    """The short directory a case travels in: ``c1``, ``c2``, ... (MAX_PATH)."""
    return f"c{REQUIRED_CASE_IDS.index(case_id) + 1}"


#: The guest run directory at its longest, composed from the formats the
#: driver and guest script use (tests/test_fdeploy_lane.py holds them equal):
#: ``GUEST_ROOT="C:\gpo-studio\fd\\$STAMP"``, ``GUEST_OUT="$GUEST_ROOT\o"``, the
#: stamp ``date +%Y%m%d%H%M%S`` plus ``-$$`` (a Linux PID, at most 7 digits),
#: and the guest's ``fd-<yyyyMMddHHmmss>-<4 digits>`` run id.
GUEST_ROOT_PREFIX = "C:\\gpo-studio\\fd"
GUEST_OUT_LEAF = "o"
GUEST_STAMP_WORST = "20261008123456-" + "9" * 7
GUEST_RUN_ID_WORST = "fd-20261008123456-9999"
GUEST_WORK_WORST = "\\".join(
    (GUEST_ROOT_PREFIX, GUEST_STAMP_WORST, GUEST_OUT_LEAF, GUEST_RUN_ID_WORST)
)
#: Windows PowerShell 5.1 fails at MAX_PATH (260); the report-parity lane's
#: Expand-Archive did. 200 leaves room for anything this does not model.
GUEST_PATH_LIMIT = 200
#: What Backup-GPO writes below a case's backup directory, at its deepest.
_REBACKUP_TAIL = (
    "{00000000-0000-0000-0000-000000000000}\\DomainSysvol\\GPO\\User\\"
    "Documents & Settings\\fdeploy1.ini"
)


def longest_guest_path(archive_names: list[str], case_dirs: list[str]) -> str:
    """The longest path the guest touches: the expanded input or a re-export."""
    work = GUEST_WORK_WORST
    paths = [f"{work}\\input\\" + name.replace("/", "\\") for name in archive_names]
    paths += [f"{work}\\backups\\{d}\\{_REBACKUP_TAIL}" for d in case_dirs]
    paths += [f"{work}\\commands\\{d}\\import.stderr.txt" for d in case_dirs]
    return max(paths, key=len)


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
    marker, r3_policy = r3_bytes(repo)
    r3_claims = [c.to_json() for c in reader_claims(read_fdeploy(r3_policy))]
    if r3_claims != expected_reader(R3_FLAGS):
        raise ValueError(f"R3 constants disagree with the banked capture: {r3_claims}")
    if len(set(REQUIRED_CASE_IDS)) != len(CASES):
        raise ValueError("case list is inconsistent")
    cases: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory() as tmp:
        staging = Path(tmp) / "cases"
        staging.mkdir()
        for case_id, flags in CASES:
            policy = case_policy_bytes(r3_policy, flags)
            if (case_id == VERBATIM_CASE) != (policy == r3_policy):
                raise ValueError(f"{case_id}: verbatim flag disagrees with the bytes")
            identity = case_identity(case_id)
            staged = staging / case_dir(case_id)
            stage_case(staged, identity, marker, policy)
            check_staged(case_id, staged, identity, flags, policy)
            cases.append({
                "case_id": case_id,
                "case_dir": case_dir(case_id),
                "flags": flags,
                "verbatim_r3": policy == r3_policy,
                **identity,
                "fdeploy1": encoding_facts(policy),
                "marker": encoding_facts(marker),
                "expected_reader": expected_reader(flags),
                "expected_report": expected_report(),
                "probe_20261008_options": PROBE_20261008_OPTIONS[flags],
            })
        archive = _zip(staging.parent)
    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        names = bundle.namelist()
    longest = longest_guest_path(names, [case_dir(case_id) for case_id in REQUIRED_CASE_IDS])
    if len(longest) > GUEST_PATH_LIMIT:
        raise ValueError(
            f"guest path of {len(longest)} chars exceeds {GUEST_PATH_LIMIT}: {longest}"
        )
    (out / ARCHIVE_NAME).write_bytes(archive)
    expectation: dict[str, object] = {
        "schema_version": 1,
        "cases": cases,
        "domain": DOMAIN,
        "longest_guest_path": len(longest),
        "user_extension_pair": USER_EXTENSION_PAIR,
        "flags_note": FLAGS_ARE_UNDECODED,
        "excluded_flags": EXCLUDED_FLAGS,
        "recorded_not_asserted": [
            "Windows option rendering per Flags value (WI-066 data)",
            "gPCUserExtensionNames after Import-GPO",
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
    assert isinstance(cases, list)
    print(f"cases={len(cases)}")
    for case in cases:
        print(f"  {case['case_id']}: flags={case['flags']} verbatim_r3={case['verbatim_r3']} "
              f"fdeploy1_sha256={case['fdeploy1']['sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
