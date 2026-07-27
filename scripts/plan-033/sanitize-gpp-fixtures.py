#!/usr/bin/env python3
"""Recorded sanitizer for Plan 033 WP-1A native GPP backup fixtures.

Sanitization rules:
  replace-domain-sid   – replace the real domain SID prefix with a synthetic one
  replace-sd-hex       – replace SecurityDescriptor hex blobs with a minimal placeholder
  replace-gpreport-sid – catch-all: replace any S-1-5-21-… SID in gpreport.xml

Intentionally retained (allowed lab identifiers per identifier gate policy):
  - Domain names: ad.hraedon.com, HRAENET
  - DC hostname: mvmdc03.ad.hraedon.com
  - Interactive user: HRAENET\\sxmerrip (GPMC author, in RegistrationInfo/Author)
  - Service accounts: HRAENET\\svc-gpolens
  - Lab groups: HRAENET\\lab-admins, HRAENET\\dev-team
  - Placeholder identities: pmerritt@ad.hraedon.com, mail.hraedon.com
    (synthetic values used in SendEmail action; not real principals)
  These are homelab identifiers explicitly allowed by the identifier gate.

Known limitations:
  - The real domain SID prefix is supplied via the
    GPO_STUDIO_REAL_SID_PREFIX environment variable at sanitization time;
    it is never committed. Re-running from raw requires the same env var.
  - Generic SID replacement (replace-gpreport-sid) applies only to files
    named gpreport*.xml. Other XML files get only the domain-prefix rule.
  - Undecodable files (binary) are copied unchanged. No binary GPP artifacts
    are expected in these fixtures, but a future corpus should audit this.
  - Security descriptors in gpreport.xml have their SIDs rewritten but remain
    structurally present. They are non-functional after SID replacement.
  - Output directories are not cleared before writing; stale files from a
    previous run could persist outside the sanitization record.

Usage (copy mode):
  python scripts/plan-033/sanitize-gpp-fixtures.py --raw-dir RAW --out-dir OUT

Usage (in-place mode):
  python scripts/plan-033/sanitize-gpp-fixtures.py --in-place DIR
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

REAL_SID_PREFIX = os.environ.get("GPO_STUDIO_REAL_SID_PREFIX", "")
SYNTHETIC_SID_PREFIX = "S-1-5-21-0000000000-0000000000-0000000000"
SD_PLACEHOLDER = "01 00 04 80 00 00 00 00"

_SD_HEX_RE = re.compile(
    r"(<SecurityDescriptor>)"
    r"((?:[0-9a-fA-F]{2}\s)+[0-9a-fA-F]{2})"
    r"(</SecurityDescriptor>)"
)
_GENERIC_SID_RE = re.compile(r"S-1-5-21-\d+-\d+-\d+")

_BOM_MAP: list[tuple[bytes, str]] = [
    (b"\xff\xfe", "utf-16-le"),
    (b"\xfe\xff", "utf-16-be"),
    (b"\xef\xbb\xbf", "utf-8-sig"),
]


def _detect_encoding(raw: bytes) -> str:
    for bom, encoding in _BOM_MAP:
        if raw.startswith(bom):
            return encoding
    return "utf-8"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sanitize_text(text: str, filename: str) -> tuple[str, list[str]]:
    applied: list[str] = []

    if REAL_SID_PREFIX and REAL_SID_PREFIX in text:
        text = text.replace(REAL_SID_PREFIX, SYNTHETIC_SID_PREFIX)
        applied.append("replace-domain-sid")

    new_text = _SD_HEX_RE.sub(rf"\g<1>{SD_PLACEHOLDER}\g<3>", text)
    if new_text != text:
        text = new_text
        applied.append("replace-sd-hex")

    if filename == "gpreport.xml":
        new_text = _GENERIC_SID_RE.sub(SYNTHETIC_SID_PREFIX, text)
        if new_text != text:
            text = new_text
            applied.append("replace-gpreport-sid")

    return text, applied


def sanitize_file(src: Path, dst: Path) -> tuple[str, str, list[str]]:
    raw = src.read_bytes()
    raw_hash = _sha256(raw)
    encoding = _detect_encoding(raw)

    try:
        text = raw.decode(encoding)
    except (UnicodeDecodeError, LookupError):
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        return raw_hash, raw_hash, []

    sanitized, applied = sanitize_text(text, src.name)

    out_bytes = sanitized.encode(encoding) if applied else raw

    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(out_bytes)
    return raw_hash, _sha256(out_bytes), applied


def run_copy(raw_dir: Path, out_dir: Path) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for src in sorted(raw_dir.rglob("*")):
        if src.is_dir():
            continue
        rel = src.relative_to(raw_dir)
        dst = out_dir / rel
        raw_hash, san_hash, applied = sanitize_file(src, dst)
        entries.append({
            "relative_path": str(rel),
            "raw_sha256": raw_hash,
            "sanitized_sha256": san_hash,
            "transformations_applied": applied,
        })
    return entries


def run_in_place(target_dir: Path) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for path in sorted(target_dir.rglob("*")):
        if path.is_dir():
            continue
        rel = path.relative_to(target_dir)
        raw = path.read_bytes()
        raw_hash = _sha256(raw)
        encoding = _detect_encoding(raw)

        try:
            text = raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue

        sanitized, applied = sanitize_text(text, path.name)
        if not applied:
            continue

        out_bytes = sanitized.encode(encoding)
        path.write_bytes(out_bytes)
        entries.append({
            "relative_path": str(rel),
            "raw_sha256": raw_hash,
            "sanitized_sha256": _sha256(out_bytes),
            "transformations_applied": applied,
        })
    return entries


def write_record(
    out_dir: Path,
    source: str,
    entries: list[dict[str, object]],
) -> Path:
    record = {
        "timestamp": datetime.now(UTC).isoformat(timespec="seconds"),
        "source": source,
        "files": entries,
    }
    record_path = out_dir / "sanitization-record.json"
    record_path.write_text(
        json.dumps(record, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    return record_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Recorded sanitizer for native GPP backup fixtures.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--raw-dir",
        type=Path,
        help="Raw (unsanitized) backup tree to copy and sanitize.",
    )
    group.add_argument(
        "--in-place",
        type=Path,
        metavar="DIR",
        help="Sanitize an existing fixture tree in place.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        help="Output directory (required with --raw-dir).",
    )
    args = parser.parse_args(argv)

    if args.raw_dir:
        if not args.out_dir:
            parser.error("--out-dir is required with --raw-dir")
        if not args.raw_dir.is_dir():
            print(f"error: raw dir does not exist: {args.raw_dir}", file=sys.stderr)
            return 1
        entries = run_copy(args.raw_dir, args.out_dir)
        record_path = write_record(args.out_dir, str(args.raw_dir), entries)
    else:
        assert args.in_place is not None
        if not args.in_place.is_dir():
            print(f"error: directory does not exist: {args.in_place}", file=sys.stderr)
            return 1
        entries = run_in_place(args.in_place)
        record_path = write_record(args.in_place, str(args.in_place), entries)

    changed = [e for e in entries if e["transformations_applied"]]
    print(f"Sanitized {len(changed)}/{len(entries)} file(s).")
    for e in changed:
        rules = ", ".join(e["transformations_applied"])  # type: ignore[arg-type]
        print(f"  {e['relative_path']}: {rules}")
    print(f"Record: {record_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
