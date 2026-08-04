#!/usr/bin/env python3
"""Build the deterministic synthetic security-template WP-3 candidate.

## Why `Registry Values` is here and the rest of the expansion is not

`security_template.py` knows eleven sections; this candidate covers four of
them. The three that were already certified are plain key/value or
principal-list shapes, and `Registry Values` joins them because a measured
round trip says it needs nothing new
(`docs/plan-033/wp3-expansion-design.md`): all four registry types come back
byte-identical, and the two transformations `secedit` does apply -- lower-casing
the path and reordering the entries -- are both absorbed by
`SecurityTemplate.get_value`, which folds case and looks up by key.

The sections still absent are absent for measured reasons, not for want of
rows: `Group Membership` returns SIDs where names were authored (including a
machine-specific one), `Registry Keys` / `File Security` /
`Service General Setting` are re-keyed by ordinal index on export, and
`Kerberos Policy` exports empty on a member server because it is meaningful
only on a domain controller.
"""

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
        # One row per registry type, not one row for the section. The type
        # prefix is the part most likely to be wrong -- 1 = REG_SZ,
        # 2 = REG_EXPAND_SZ, 4 = REG_DWORD, 7 = REG_MULTI_SZ -- and a single
        # row would only exercise whichever one it happened to use.
        #
        # The REG_EXPAND_SZ row deliberately carries an unexpanded
        # `%SystemRoot%`: a template that expanded it before writing would be
        # wrong in a way no DWORD row could reveal.
        InfSection(
            name="Registry Values",
            entries=(
                ("MACHINE\\Software\\StudioLab\\Sz", '1,"studio sz value"'),
                ("MACHINE\\Software\\StudioLab\\ExpandSz", '2,"%SystemRoot%\\studio"'),
                ("MACHINE\\Software\\StudioLab\\Dword", "4,1"),
                ("MACHINE\\Software\\StudioLab\\MultiSz", "7,alpha,beta"),
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
