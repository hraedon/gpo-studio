#!/usr/bin/env python3
"""Build the Plan 034 object-security candidate.

The mixed synthetic candidate exercises propagation codes 0/1/2 for registry
and file security, startup codes 2/3/4 for services, and both `[Group
Membership]` relations (`__Members` with one and with two members, and
`__Memberof`).  It is assembled by the product families and shared
security-template serializer -- the Group Membership rows included, so the lane
reads what `RestrictedGroupsFamily` writes rather than a hand-written
comparator (WI-064).

The families are also validated, and any issue refuses the build: a candidate
the product would report as malformed is not one to certify.  Services carry
parsed descriptors, the shape `from_template` produces (WI-065).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from gpo_studio.object_security import (
    FileSecurity,
    FileSystemSecurityFamily,
    RegistryKeySecurity,
    RegistrySecurityFamily,
    RestrictedGroup,
    RestrictedGroupMember,
    RestrictedGroupsFamily,
    ServiceSecurity,
    SystemServicesFamily,
)
from gpo_studio.sddl import parse_sddl
from gpo_studio.security_template import (
    InfSection,
    SecurityTemplate,
    encode_security_template,
    format_security_template,
)

_REGISTRY_SDDL = "D:PAR(A;CI;KA;;;BA)(A;CI;KR;;;BU)"
_FILE_SDDL = "D:PAR(A;OICI;FA;;;BA)"
_SERVICE_SDDL = "D:PAR(A;;CCDCLCSWRPWPDTLOCRSDRCWDWO;;;BA)"

# BUILTIN aliases only: their SIDs are constants on every Windows host, so the
# expected side can name them exactly and no principal resolution is needed.
# The lane never runs `secedit /configure`, so no membership is changed.
_ADMINISTRATORS = "S-1-5-32-544"
_USERS = "S-1-5-32-545"
_BACKUP_OPERATORS = "S-1-5-32-551"
_REMOTE_DESKTOP_USERS = "S-1-5-32-555"

_Families = tuple[
    RegistrySecurityFamily,
    FileSystemSecurityFamily,
    SystemServicesFamily,
    RestrictedGroupsFamily,
]


def _families() -> _Families:
    return (
        RegistrySecurityFamily(
            keys=tuple(
                RegistryKeySecurity(
                    key_path=rf"MACHINE\Software\GPOStudio\ObjectSecurity\Registry{code}",
                    raw_sddl=_REGISTRY_SDDL,
                    propagation=mode,
                )
                for code, mode in ((0, "propagate"), (1, "do_not_allow_replace"), (2, "replace"))
            )
        ),
        FileSystemSecurityFamily(
            files=tuple(
                FileSecurity(
                    file_path=rf"C:\GPOStudio\ObjectSecurity\File{code}",
                    raw_sddl=_FILE_SDDL,
                    propagation=mode,
                )
                for code, mode in ((0, "propagate"), (1, "do_not_allow_replace"), (2, "replace"))
            )
        ),
        SystemServicesFamily(
            services=tuple(
                ServiceSecurity(
                    service_name=f"GPOStudioObject{mode.title()}",
                    startup_mode=mode,
                    raw_sddl=_SERVICE_SDDL,
                    security_descriptor=parse_sddl(_SERVICE_SDDL),
                )
                for mode in ("automatic", "manual", "disabled")
            )
        ),
        RestrictedGroupsFamily(
            groups=(
                RestrictedGroup(
                    group_sid=_BACKUP_OPERATORS,
                    members=(RestrictedGroupMember(sid=_ADMINISTRATORS),),
                ),
                RestrictedGroup(
                    group_sid=_REMOTE_DESKTOP_USERS,
                    members=(
                        RestrictedGroupMember(sid=_ADMINISTRATORS),
                        RestrictedGroupMember(sid=_BACKUP_OPERATORS),
                    ),
                    member_of=(RestrictedGroupMember(sid=_USERS),),
                ),
            )
        ),
    )


def candidate_issues() -> tuple[str, ...]:
    """Every validation issue the product reports for this candidate."""
    return tuple(
        f"{issue.severity}:{issue.code}:{issue.path}"
        for family in _families()
        for issue in family.validate()
    )


def candidate_sections() -> tuple[InfSection, ...]:
    entries: dict[str, dict[str, str]] = {}
    for family in _families():
        entries.update(family.to_template_entries())
    return (
        InfSection(name="Unicode", entries=(("Unicode", "yes"),)),
        InfSection(name="Version", entries=(("signature", '"$CHICAGO$"'), ("Revision", "1"))),
        *(InfSection(name=name, entries=tuple(values.items())) for name, values in entries.items()),
    )


def _expected_group_membership() -> list[dict[str, object]]:
    """What the model says, independently of how the serializer spells it.

    Taken from the `RestrictedGroup` values, not from the emitted entries, so
    a writer that spelled a key in a form Windows reads differently (WI-064's
    bare SID) is caught by the finalizer's strict candidate parse rather than
    copied into the expectation.
    """
    restricted_groups = _families()[3]
    rows: list[dict[str, object]] = []
    for group in restricted_groups.groups:
        for relation, members in (("members", group.members), ("memberof", group.member_of)):
            if members:
                rows.append(
                    {
                        "group_sid": group.group_sid,
                        "relation": relation,
                        "member_sids": sorted(member.sid for member in members),
                    }
                )
    return rows


def _expected() -> dict[str, object]:
    sections = candidate_sections()
    return {
        "schema_version": 2,
        "group_membership": _expected_group_membership(),
        "settings": [
            {
                "section": section.name,
                "target": key,
                "code": int(value.split(",", 1)[0]),
                "sddl": value.split(',"', 1)[1][:-1],
            }
            for section in sections
            if section.name not in {"Unicode", "Version", "Group Membership"}
            for key, value in section.entries
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    issues = candidate_issues()
    if issues:
        parser.error(f"the product reports issues in this candidate: {list(issues)}")
    args.output_dir.mkdir(parents=True, exist_ok=False)
    text = format_security_template(SecurityTemplate(sections=candidate_sections())) + "\n"
    (args.output_dir / "candidate.inf").write_bytes(encode_security_template(text))
    (args.output_dir / "expected.json").write_text(
        json.dumps(_expected(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
