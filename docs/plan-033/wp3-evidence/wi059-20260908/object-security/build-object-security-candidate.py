#!/usr/bin/env python3
"""Build the Plan 034 object-security candidate.

The mixed synthetic candidate exercises propagation codes 0/1/2 for registry
and file security and startup codes 2/3/4 for services.  It is assembled by
the product families and shared security-template serializer.
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
    ServiceSecurity,
    SystemServicesFamily,
)
from gpo_studio.security_template import (
    InfSection,
    SecurityTemplate,
    encode_security_template,
    format_security_template,
)

_REGISTRY_SDDL = "D:PAR(A;CI;KA;;;BA)(A;CI;KR;;;BU)"
_FILE_SDDL = "D:PAR(A;OICI;FA;;;BA)"
_SERVICE_SDDL = "D:PAR(A;;CCDCLCSWRPWPDTLOCRSDRCWDWO;;;BA)"


def _families() -> tuple[RegistrySecurityFamily, FileSystemSecurityFamily, SystemServicesFamily]:
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
                )
                for mode in ("automatic", "manual", "disabled")
            )
        ),
    )


def candidate_sections() -> tuple[InfSection, ...]:
    registry, files, services = _families()
    entries: dict[str, dict[str, str]] = {}
    for family in (registry, files, services):
        entries.update(family.to_template_entries())
    return (
        InfSection(name="Unicode", entries=(("Unicode", "yes"),)),
        InfSection(name="Version", entries=(("signature", '"$CHICAGO$"'), ("Revision", "1"))),
        *(InfSection(name=name, entries=tuple(values.items())) for name, values in entries.items()),
    )


def _expected() -> dict[str, object]:
    sections = candidate_sections()
    return {
        "schema_version": 1,
        "settings": [
            {
                "section": section.name,
                "target": key,
                "code": int(value.split(",", 1)[0]),
                "sddl": value.split(',"', 1)[1][:-1],
            }
            for section in sections
            if section.name not in {"Unicode", "Version"}
            for key, value in section.entries
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    text = format_security_template(SecurityTemplate(sections=candidate_sections())) + "\n"
    (args.output_dir / "candidate.inf").write_bytes(encode_security_template(text))
    (args.output_dir / "expected.json").write_text(
        json.dumps(_expected(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
