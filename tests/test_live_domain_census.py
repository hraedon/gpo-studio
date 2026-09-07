"""Self-checking contract for the live-domain census fixtures (R6/R8).

These are verbatim byte copies of the 2026-09-02 read-only production census
(one extension-list CSV over 26 GPOs; three GPOs' GPT.INI + AD-attribute
shape + SYSVOL anatomy). The first job of this module is the hash bind:
every file must still digest to the SHA-256 banked in its provenance, or the
fixture has been smudged or corrupted. The rest pins the measured facts as
committed numbers so drift in either direction is loud.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
from pathlib import Path

_FIXTURES = Path(__file__).parent / "fixtures" / "live-domain-census"

_GUID = re.compile(r"^\{[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}"
                   r"-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\}$")

#: Committed R6 fact: the two GPP XML root-element clsids that
#: ``gpmc_interop`` once listed as known CSE GUIDs appear in NO production
#: extension list -- 0 of 26 GPOs, machine or user side (registry row R6).
_ABSENT_GPP_CLSIDS = (
    "{3125E937-EB16-4b4c-9934-544FC6D24D26}",  # "GPP Groups"
    "{A3CC7818-8A30-4e0c-91C5-A4EA4B5A8DAB}",  # "GPP Registry"
)


def _read_csv(path: Path) -> tuple[list[str], list[list[str]]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    return rows[0], rows[1:]


def test_every_census_file_digests_to_its_banked_hash() -> None:
    provenance = json.loads(
        (_FIXTURES / "provenance.json").read_text(encoding="utf-8")
    )
    files = provenance["files"]
    assert len(files) == 10
    for rel, meta in sorted(files.items()):
        data = (_FIXTURES / rel).read_bytes()
        assert hashlib.sha256(data).hexdigest() == meta["sha256"], rel


def test_r6_census_shape_and_the_absent_gpp_clsids() -> None:
    header, rows = _read_csv(_FIXTURES / "r06-cse-census" / "extension-lists.csv")
    assert header == ["machine", "user"]
    assert len(rows) == 26  # one row per production groupPolicyContainer

    with_machine = [r for r in rows if r[0]]
    with_user = [r for r in rows if r[1]]
    with_neither = [r for r in rows if not r[0] and not r[1]]
    assert (len(with_machine), len(with_user), len(with_neither)) == (13, 13, 3)

    guids: set[str] = set()
    for row in rows:
        assert len(row) == 2
        for cell in row:
            for token in re.findall(r"\{[^{}]*\}", cell):
                assert _GUID.match(token), token
                guids.add(token.upper())
    assert len(guids) == 33
    # The committed R6 fact, as a fact: neither GPP clsid is present.
    for clsid in _ABSENT_GPP_CLSIDS:
        assert clsid.upper() not in guids


def test_r8_gpt_ini_versions_and_packed_halves() -> None:
    versions = {}
    for gpo, version in (("gpo1", 42), ("gpo2", 131082), ("gpo3", 0)):
        # utf-8-sig decode without newline translation: CRLF is part of
        # the captured shape and the hash bind.
        text = (_FIXTURES / "r08-gpo-anatomy" / f"{gpo}-GPT.INI").read_bytes().decode(
            "utf-8-sig"
        )
        assert text == f"[General]\r\nVersion={version}\r\n"
        versions[gpo] = version
    # gpo2's packed field (R5's arithmetic, live): user 2 * 65536 + machine 10.
    assert versions["gpo2"] == 0x0002000A
    assert versions["gpo2"] == 2 * 65536 + 10


def test_r8_attribute_and_sysvol_anatomy() -> None:
    for gpo in ("gpo1", "gpo2", "gpo3"):
        attrs_header, attrs = _read_csv(
            _FIXTURES / "r08-gpo-anatomy" / f"{gpo}-attributes.csv"
        )
        assert attrs_header == ["attribute", "populated", "kind"]
        populated = {row[0]: row[1] for row in attrs}
        assert all(v in ("True", "False") for v in populated.values())
        assert all(row[2] for row in attrs)

        sysvol_header, sysvol = _read_csv(
            _FIXTURES / "r08-gpo-anatomy" / f"{gpo}-sysvol.csv"
        )
        assert sysvol_header == ["relative", "bytes"]
        assert all(int(row[1]) > 0 for row in sysvol)

    # The three anatomies are deliberately diverse (registry row R8):
    # policy-heavy, GPP-groups both sides, and never-edited.
    _, gpo1_sysvol = _read_csv(_FIXTURES / "r08-gpo-anatomy" / "gpo1-sysvol.csv")
    gpo1_paths = " ".join(row[0].lower() for row in gpo1_sysvol)
    assert "\\machine\\registry.pol" in gpo1_paths
    assert "\\secedit\\gpttmpl.inf" in gpo1_paths
    assert "scheduledtasks\\scheduledtasks.xml" in gpo1_paths

    _, gpo2_sysvol = _read_csv(_FIXTURES / "r08-gpo-anatomy" / "gpo2-sysvol.csv")
    gpo2_paths = " ".join(row[0].lower() for row in gpo2_sysvol)
    assert "\\machine\\preferences\\groups\\groups.xml" in gpo2_paths
    assert "\\user\\preferences\\groups\\groups.xml" in gpo2_paths

    # The WMI-filter association publication.py models is real here: only
    # gpo3 carries gPCWQLFilter; gpo2 is the only one with a user list.
    for gpo, attribute, expect in (
        ("gpo1", "gPCUserExtensionNames", None),
        ("gpo2", "gPCUserExtensionNames", "True"),
        ("gpo3", "gPCWQLFilter", "True"),
    ):
        _, attrs = _read_csv(_FIXTURES / "r08-gpo-anatomy" / f"{gpo}-attributes.csv")
        populated = {row[0]: row[1] for row in attrs}
        assert populated.get(attribute) == expect, (gpo, attribute)
