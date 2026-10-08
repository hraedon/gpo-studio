#!/usr/bin/env python3
"""Copy the native firewall capture with recorded, synthetic identities.

Domain SID and security-descriptor hex handling follow sanitize-gpp-fixtures.py.
Unlike that older sanitizer, this also replaces lab domain/host/GPO identities.
The input directory stays outside the repository. No original identity is recorded.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

SYNTHETIC_SID = "S-1-5-21-0000000000-0000000000-0000000000"


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sanitize(source: Path, destination: Path) -> None:
    if destination.exists():
        raise ValueError("Destination must not already exist")
    capture = json.loads((source / "capture.json").read_text(encoding="utf-8-sig"))
    info = ET.fromstring(next(source.rglob("bkupInfo.xml")).read_bytes())
    fields = {e.tag.rsplit("}", 1)[-1]: e.text or "" for e in info.iter()}
    replacements = {
        fields["GPODomain"]: "studio.example",
        fields["GPODomainController"]: "zz-studio-dc.studio.example",
        fields["GPODisplayName"]: "zz-studio-firewall-native",
        capture["run_id"]: "firewall-capture-synthetic-20261008",
        fields["GPOGuid"].strip("{}"): "11111111-1111-4111-8111-111111111111",
        fields["GPODomainGuid"].strip("{}"): "22222222-2222-4222-8222-222222222222",
        fields["ID"].strip("{}"): "33333333-3333-4333-8333-333333333333",
    }
    backup = ET.fromstring(next(source.rglob("Backup.xml")).read_bytes())
    netbios_names = set()
    for group in backup.iter():
        if group.tag.rsplit("}", 1)[-1] != "Group":
            continue
        children = {e.tag.rsplit("}", 1)[-1]: e.text or "" for e in group}
        if children.get("DnsDomainName", "").casefold() != fields["GPODomain"].casefold():
            continue
        replacements[children["NetBIOSDomainName"]] = "STUDIO"
        netbios_names.add(children["NetBIOSDomainName"].casefold())
        replacements[children["SamAccountName"]] = f"zz-studio-group-{len(replacements)}"
    # The descriptor's localized group names need no interpretation; domain
    # names are replaced globally while well-known principal names are retained.
    ordered = sorted(
        ((old, new) for old, new in replacements.items() if old.casefold() != new.casefold()),
        key=lambda item: len(item[0]),
        reverse=True,
    )

    def replace(text: str) -> str:
        for old, new in ordered:
            pattern = re.escape(old)
            if old.casefold() in netbios_names:
                pattern = r"\b" + pattern + r"\b"
            text = re.sub(
                pattern, lambda _, replacement=new: replacement, text, flags=re.IGNORECASE
            )
        return text

    native = base64.b64decode(capture["registry_pol_base64"], validate=True)
    decoded = native[8:].decode("utf-16le")
    if replace(decoded) != decoded or re.search(r"S-1-5-21-\d+-\d+-\d+", decoded):
        raise ValueError("Registry.pol contains estate identifiers; cannot preserve bytes")
    entries = []
    for path in sorted(source.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(source).as_posix()
        target = destination / replace(relative)
        raw = path.read_bytes()
        applied = []
        if path.suffix.lower() == ".pol":
            if raw != native:
                raise ValueError("Backup Registry.pol differs from capture")
            output = raw
        else:
            encoding = (
                "utf-16le"
                if raw.startswith(b"\xff\xfe")
                else "utf-16be"
                if raw.startswith(b"\xfe\xff")
                else "utf-8"
            )
            text = raw.decode(encoding)
            updated = replace(text)
            if updated != text:
                applied.append("replace-domain-host-account-gpo-and-backup-identities")
            sid_updated = re.sub(r"S-1-5-21-\d+-\d+-\d+", SYNTHETIC_SID, updated)
            if sid_updated != updated:
                applied.append("replace-domain-sid-prefix")
            updated = sid_updated
            hex_updated = re.sub(
                r"(<SecurityDescriptor>)((?:[0-9a-fA-F]{2}\s)+[0-9a-fA-F]{2})(</SecurityDescriptor>)",
                r"\g<1>01 00 04 80 00 00 00 00\g<3>",
                updated,
            )
            if hex_updated != updated:
                applied.append("replace-security-descriptor-hex-with-placeholder")
            output = hex_updated.encode(encoding) if applied else raw
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(output)
        entries.append(
            {
                "relative_path": target.relative_to(destination).as_posix(),
                "raw_sha256": sha256(raw),
                "sanitized_sha256": sha256(output),
                "transformations_applied": applied,
            }
        )
    (destination / "Registry.pol").write_bytes(native)
    script = Path("scripts/plan-033/capture-firewall-native.ps1")
    provenance = {
        "origin": "Native NetSecurity -PolicyStore authoring into an unlinked disposable GPO; "
        "WS2025 member server build 26100. capture.json, Get-GPOReport XML and Backup-GPO.",
        "capture_date": "2026-10-08",
        "capture_script": script.as_posix(),
        "capture_script_sha256": sha256((REPO_ROOT / script).read_bytes()),
        "sanitizer": "scripts/plan-033/sanitize-firewall-fixtures.py",
        "sanitisation": "Domain, DC, domain account, GPO, domain GUID, backup GUID and run "
        "identities replaced with synthetic values. Domain SID prefixes replaced "
        "using the existing GPP convention; backup descriptor hex replaced by "
        "the existing nonfunctional placeholder. Well-known SIDs/CSE/tool GUIDs, "
        "synthetic rule names, paths and RFC documentation addresses retained. "
        "Raw inbox remains outside the repository.",
        "registry_pol": {
            "sha256": sha256(native),
            "bytes": len(native),
            "records": 25,
            "rules": 13,
            "identity_audit": "All keys/names/values inspected: no estate identifiers. "
            "Capture base64, backup and top-level Registry.pol are identical.",
        },
        "ordering": "Windows orders root, Domain profile, FirewallRules, Private profile. "
        "Within profile keys it retains authoring order. registry_pol.serialize "
        "sorts keys/value names; compare native record sets and individual record "
        "bytes, not whole-file byte order. Token order inside each rule is native.",
        "normalizations": [
            {
                "name": "icmpv4-port-filter-rpc",
                "rule": "StudioFwProbe-03",
                "wire": "ICMP4=8:0, no LPort token",
                "readback": "local_port=RPC",
            },
            {
                "name": "rpc-endpoint-map-spelling",
                "rule": "StudioFwProbe-09",
                "wire": "LPort=RPC-EPMap",
                "readback": "local_port=RPCEPMap",
            },
            {
                "name": "wired-interface-spelling",
                "rule": "StudioFwProbe-11",
                "wire": "IFType=Lan",
                "readback": "interface_type=Wired",
            },
            {
                "name": "authenticated-bypass-action",
                "rule": "StudioFwProbe-13",
                "wire": "Action=ByPass; Security=Authenticate; RMauth=D:(A;;CC;;;WD)",
                "readback": "action=Allow; authentication=Required; override_block_rules=True",
            },
            {
                "name": "ipv4-subnet-mask-form",
                "rule": "StudioFwProbe-05",
                "authored": "192.0.2.0/24",
                "wire_and_readback": "192.0.2.0/255.255.255.0",
            },
            {
                "name": "local-subnet-family-expansion",
                "rule": "StudioFwProbe-08",
                "wire": "RA4=LocalSubnet|RA6=LocalSubnet",
                "readback": "remote_address=LocalSubnet",
            },
        ],
        "limitations": "Native capture grounds codec tests; no Studio write/import lane, "
        "endpoint application evidence or operator surface. Sanitized backups "
        "are fixture evidence, not deployable security descriptors.",
        "files": entries,
    }
    (destination / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    sanitize(args.raw_dir, args.out_dir)
