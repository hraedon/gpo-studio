"""An exhaustive generator of GPOs for the plan writer (final-r11).

Every statement the plan writer has, with every kind of hole it fills: each
registry type and action, the value shapes each type takes (empty strings and
lists included), links in every enabled/enforced combination, security filters
apply-only, read-only and mixed over every target type, descriptions empty,
one-line and multi-line, a WMI filter or none, and every side enablement.
`tests/test_egress.py` requires every plan these produce to pass egress, and the
plan grammar (`egress.PLAN_GRAMMAR`) to hold exactly the line shapes they use.
"""

from __future__ import annotations

import itertools
from collections.abc import Iterator

from gpo_studio.model import GPO, GPOLink, RegistrySetting, SecurityFilter, WmiFilter

GUID = "{0B0D0080-0000-4000-8000-000000000000}"
_VALUES: dict[str, list[object]] = {
    "REG_SZ": ["", "x", "a\nb", "a\r\nb", "x\U000110bd", "it's ’q’"],
    "REG_EXPAND_SZ": ["", "%SystemRoot%\\x"],
    "REG_MULTI_SZ": [[], [""], ["a"], ["a", "b"], ["a\nb", "​"]],
    "REG_DWORD": [0, 4294967295],
    "REG_QWORD": [0, 18446744073709551615],
    "REG_BINARY": ["", "0A", "0A0B0C"],
}


def settings() -> tuple[RegistrySetting, ...]:
    found: list[RegistrySetting] = []
    for side, hive in (("computer", "HKLM"), ("user", "HKCU")):
        for kind, values in _VALUES.items():
            for value in values:
                found.append(
                    RegistrySetting(
                        f"s{len(found)}",
                        side,
                        hive,
                        f"Software\\Corpus\\{kind}",  # type: ignore[arg-type]
                        f"V{len(found)}",
                        kind,
                        value,  # type: ignore[arg-type]
                    )
                )
        found.append(
            RegistrySetting(
                f"s{len(found)}",
                side,
                hive,
                "Software\\Corpus",
                f"Old{len(found)}",  # type: ignore[arg-type]
                "REG_SZ",
                "",
                "delete",
            )
        )
        found.append(
            RegistrySetting(
                f"s{len(found)}",
                side,
                hive,
                "Software\\Gone",
                "",
                "REG_SZ",
                "",  # type: ignore[arg-type]
                "delete_all_values",
            )
        )
    return tuple(found)


def _filters() -> Iterator[tuple[SecurityFilter, ...]]:
    kinds = ("user", "group", "computer")
    yield ()
    for permissions in (("apply",), ("read",), ("apply", "read")):
        yield tuple(
            SecurityFilter(f"f{p}{k}", f"CORPUS\\{p}{k}", permission=p, target_type=k)  # type: ignore[arg-type]
            for p in permissions
            for k in kinds
        )
    # A SID principal, and a name principal with its stored SID: the plan
    # resolves each and checks the stored SID before anything changes.
    yield (
        SecurityFilter("fsid", "S-1-5-11", sid="S-1-5-11"),
        SecurityFilter("fstored", "CORPUS\\stored", sid="S-1-5-21-1-2-3-1001"),
        SecurityFilter("fbare", "S-1-5-21-1-2-3-1002", permission="read"),
    )


def _links() -> Iterator[tuple[GPOLink, ...]]:
    yield ()
    yield tuple(
        GPOLink(f"l{index}", "OU=Corpus,DC=studio,DC=local", enabled=e, enforced=f, order=index + 1)
        for index, (e, f) in enumerate(itertools.product((True, False), repeat=2))
    )


def gpos() -> Iterator[GPO]:
    for filters, links, description, wmi, sides in itertools.product(
        _filters(),
        _links(),
        ("", "One line", "Two\nlines"),
        (None, WmiFilter("w", "Servers", query="SELECT * FROM Win32_OperatingSystem")),
        itertools.product((True, False), repeat=2),
    ):
        yield GPO(
            guid=GUID,
            name="Corpus 'quoted' ‘name’",
            description=description,
            settings=settings(),
            links=links,
            security_filters=filters,
            wmi_filter=wmi,
            computer_enabled=sides[0],
            user_enabled=sides[1],
        )
    yield GPO(guid=GUID, name="Empty")
