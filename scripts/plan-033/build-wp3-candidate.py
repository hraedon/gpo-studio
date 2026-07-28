#!/usr/bin/env python3
"""Build the deterministic synthetic security-template WP-3 candidate."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from gpo_studio.security_template import (
    InfSection,
    SecurityTemplate,
    encode_security_template,
    format_security_template,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)

    sections = (
        InfSection(name="Unicode", entries=(("Unicode", "yes"),)),
        InfSection(
            name="System Access",
            entries=(
                ("MinimumPasswordAge", "1"),
                ("MaximumPasswordAge", "42"),
                ("MinimumPasswordLength", "14"),
                ("PasswordComplexity", "1"),
                ("PasswordHistorySize", "12"),
                ("LockoutBadCount", "5"),
                ("ResetLockoutCount", "30"),
                ("LockoutDuration", "30"),
            ),
        ),
        InfSection(
            name="Event Audit",
            entries=(
                ("AuditSystemEvents", "3"),
                ("AuditLogonEvents", "1"),
                ("AuditPolicyChange", "3"),
            ),
        ),
        InfSection(
            name="Privilege Rights",
            entries=(
                (
                    "SeBackupPrivilege",
                    "*S-1-5-32-544,*S-1-5-32-551",
                ),
                ("SeRestorePrivilege", "*S-1-5-32-544"),
            ),
        ),
        InfSection(
            name="Version",
            entries=(
                ("signature", '"$CHICAGO$"'),
                ("Revision", "1"),
            ),
        ),
    )
    text = format_security_template(SecurityTemplate(sections=sections)) + "\n"
    (args.output_dir / "candidate.inf").write_bytes(encode_security_template(text))

    expected = {
        "schema_version": 1,
        "settings": [
            {"section": section.name, "key": key, "value": value}
            for section in sections
            if section.name not in {"Unicode", "Version"}
            for key, value in section.entries
        ],
    }
    (args.output_dir / "expected.json").write_text(
        json.dumps(expected, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
