#!/usr/bin/env python3
"""Build the deterministic synthetic security-template WP-3 candidate.

## Why `Registry Values` is here and the rest of the expansion is not

`security_template.py` knows eleven sections. The default member-server
candidate covers the policy-family sections that the existing lane can observe,
plus its Group Membership comparator control. `Registry Values` is included
because a measured round trip says it needs nothing new
(`docs/plan-033/wp3-expansion-design.md`): all four registry types come back
byte-identical, and the two transformations `secedit` does apply -- lower-casing
the path and reordering the entries -- are both absorbed by
`SecurityTemplate.get_value`, which folds case and looks up by key.

`--include-kerberos` adds the complete Kerberos family for a domain-controller
run; it is deliberately off by default because that section exports empty on a
member server. The remaining absent sections are re-keyed by ordinal index on
export and are outside `policy_families.py`.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from gpo_studio.policy_families import (
    SE_BACKUP,
    SE_RESTORE,
    AccountPolicyFamily,
    AuditPolicyFamily,
    LockoutPolicy,
    PasswordPolicy,
    PrivilegeRight,
    SecurityOption,
    SecurityOptionsFamily,
    UserRightsFamily,
)
from gpo_studio.security_template import (
    InfSection,
    SecurityTemplate,
    encode_security_template,
    format_security_template,
)


def _merge_family_entries(
    *families: dict[str, dict[str, str]],
) -> dict[str, dict[str, str]]:
    merged: dict[str, dict[str, str]] = {}
    for family in families:
        for section, entries in family.items():
            target = merged.setdefault(section, {})
            overlap = target.keys() & entries.keys()
            if overlap:
                raise ValueError(
                    f"duplicate policy-family entries in {section}: {sorted(overlap)}"
                )
            target.update(entries)
    return merged


def _candidate_sections(*, include_kerberos: bool) -> tuple[InfSection, ...]:
    account = AccountPolicyFamily(
        password=PasswordPolicy(
            minimum_password_age_days=1,
            maximum_password_age_days=42,
            minimum_password_length=14,
            password_complexity_enabled=True,
            password_history_size=12,
        ),
        lockout=LockoutPolicy(
            lockout_threshold=5,
            lockout_duration_minutes=30,
            lockout_window_minutes=30,
        ),
    ).to_template_entries()
    if not include_kerberos:
        del account["Kerberos Policy"]

    family_entries = _merge_family_entries(
        account,
        AuditPolicyFamily(
            system_events="success_and_failure",
            logon_events="success",
            policy_change="success_and_failure",
        ).to_template_entries(),
        UserRightsFamily(
            assignments=(
                PrivilegeRight(
                    name=SE_BACKUP,
                    principals=("*S-1-5-32-544", "*S-1-5-32-551"),
                ),
                PrivilegeRight(
                    name=SE_RESTORE,
                    principals=("*S-1-5-32-544",),
                ),
            )
        ).to_template_entries(),
        SecurityOptionsFamily(
            options=(
                SecurityOption(
                    key="MACHINE\\Software\\StudioLab\\Sz",
                    value='1,"studio sz value"',
                ),
                SecurityOption(
                    key="MACHINE\\Software\\StudioLab\\ExpandSz",
                    value='2,"%SystemRoot%\\studio"',
                ),
                SecurityOption(
                    key="MACHINE\\Software\\StudioLab\\Dword",
                    value="4,1",
                ),
                SecurityOption(
                    key="MACHINE\\Software\\StudioLab\\MultiSz",
                    value="7,alpha,beta",
                ),
            )
        ).to_template_entries(),
    )

    authored_sections = tuple(
        InfSection(name=name, entries=tuple(entries.items()))
        for name, entries in family_entries.items()
    )
    return (
        InfSection(name="Unicode", entries=(("Unicode", "yes"),)),
        *authored_sections,
        # Group Membership remains a security-template comparator control; it
        # is not emitted by policy_families.py.
        InfSection(
            name="Group Membership",
            entries=(
                ("*S-1-5-32-551__Members", "*S-1-5-32-544"),
                ("Power Users__Members", "Administrator"),
            ),
        ),
        InfSection(
            name="Version",
            entries=(("signature", '"$CHICAGO$"'), ("Revision", "1")),
        ),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_dir", type=Path)
    parser.add_argument(
        "--include-kerberos",
        action="store_true",
        help="include the DC-only Kerberos Policy family in the candidate",
    )
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)

    sections = _candidate_sections(include_kerberos=args.include_kerberos)
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
