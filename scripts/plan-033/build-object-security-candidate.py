#!/usr/bin/env python3
"""Build the R9 ``candidate.inf`` pair: Studio's object-security shape(s).

Work order R9 (``docs/manual-evidence-requests.md``): does ``secedit`` accept
``object_security.py``'s ``key = value`` entries, or only the native bare
quoted-CSV line? This script emits BOTH shapes as byte-identical-except-rows
candidates so ``secedit /validate`` can give two verdicts:

- ``candidate.inf``               -- what ``to_template_entries()`` emits today
                                     (``_format_object_value``: ``code,"SDDL"``
                                     behind a ``key = value`` line).
- ``candidate-native-shape.inf``  -- the same objects rendered as the corpus's
                                     spec-informed native lines
                                     (``"KEY",code,"SDDL"``).

Values are synthetic throughout; nothing here touches a directory or SYSVOL.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

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

_SDDL = "D:PAR(A;OICI;FA;;;BA)"


def studio_sections() -> tuple[InfSection, ...]:
    """Sections rendered exactly as ``to_template_entries()`` emits them."""
    registry = RegistrySecurityFamily(
        keys=(
            RegistryKeySecurity(
                key_path=r"MACHINE\SOFTWARE\StudioLab\Audit",
                raw_sddl=_SDDL,
                propagation="replace",
            ),
        )
    )
    fs = FileSystemSecurityFamily(
        files=(
            FileSecurity(
                file_path=r"C:\StudioLab\Share",
                raw_sddl=_SDDL,
                propagation="replace",
            ),
        )
    )
    services = SystemServicesFamily(
        services=(
            ServiceSecurity(
                service_name="StudioLabSvc",
                raw_sddl=_SDDL,
                startup_mode="automatic",
            ),
        )
    )
    entries: dict[str, dict[str, str]] = {}
    entries.update(registry.to_template_entries())
    entries.update(fs.to_template_entries())
    entries.update(services.to_template_entries())
    sections = [
        InfSection(name="Unicode", entries=(("Unicode", "yes"),)),
        InfSection(
            name="Version",
            entries=(("signature", "$CHICAGO$"), ("Revision", "1")),
        ),
    ]
    for name, rows in entries.items():
        sections.append(InfSection(name=name, entries=tuple(rows.items())))
    return tuple(sections)


def native_text() -> str:
    """The native bare quoted-CSV rows (the measured GptTmpl.inf shape)."""
    sddl = _SDDL
    lines = [
        "[Unicode]",
        "Unicode=yes",
        "",
        "[Version]",
        'signature="$CHICAGO$"',
        "Revision=1",
        "",
        "[Registry Keys]",
        '"MACHINE\\SOFTWARE\\StudioLab\\Audit",2,"' + sddl + '"',
        "",
        "[File Security]",
        '"C:\\StudioLab\\Share",2,"' + sddl + '"',
        "",
        "[Service General Setting]",
        '"StudioLabSvc",2,"' + sddl + '"',
        "",
    ]
    return "\r\n".join(lines) + "\r\n"


def render(sections: tuple[InfSection, ...]) -> bytes:
    template = SecurityTemplate(sections=sections)
    text = format_security_template(template)
    return encode_security_template(text)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    studio = args.output_dir / "candidate.inf"
    native = args.output_dir / "candidate-native-shape.inf"
    studio.write_bytes(render(studio_sections()))
    native.write_bytes(encode_security_template(native_text()))
    print(f"wrote {studio} ({studio.stat().st_size} bytes)")
    print(f"wrote {native} ({native.stat().st_size} bytes)")

    if "-q" not in sys.argv:
        print("--- candidate.inf ---")
        print(studio.read_bytes().decode("utf-16"))
        print("--- candidate-native-shape.inf ---")
        print(native.read_bytes().decode("utf-16"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
