"""Every banked batch keeps its exact bytes, whatever its label.

A batch manifest hashes its packs' native bytes (CRLF logs, UTF-16LE reports),
so a checkout must never convert them. Each batch used to add its own
``.gitattributes`` rule when it was banked. The final-tag release gate now holds
``.gitattributes`` byte-identical to the freeze commit, so the 1.1.0 final
batch, banked after F, could not add one: the rule is a pattern instead
(``docs/plan-033/*-evidence/*/*/**`` and ``docs/plan-033/*-cleanup/**``).
These tests ask git for the attributes of a batch label that does not exist,
in every family the bank tool writes to.
"""

from __future__ import annotations

import runpy
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL = runpy.run_path(str(ROOT / "scripts/plan-033/bank-requal-batch.py"))
#: A label no batch has used; the test is about what a NEW batch gets.
NEW_LABEL = "final-20991231"


def _attributes(paths: list[str]) -> dict[str, dict[str, str]]:
    out = subprocess.run(
        ["git", "check-attr", "text", "whitespace", "eol", "--", *paths],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout
    found: dict[str, dict[str, str]] = {}
    for line in out.splitlines():
        path, attribute, value = line.rsplit(": ", 2)
        found.setdefault(path, {})[attribute] = value
    return found


def _new_batch_paths() -> list[str]:
    paths = []
    for lane, family in sorted(TOOL["FAMILY"].items()):
        pack = f"docs/plan-033/{family}/{NEW_LABEL}/{lane}"
        paths += [
            f"{pack}/verification.json",
            f"{pack}/manifest.json",
            f"{pack}/controller.log",
            f"{pack}/controller-candidate/GPO/Machine/registry.pol",
            f"{pack}/observe/gpresult.xml",
        ]
    return paths + [
        f"docs/plan-033/{NEW_LABEL}-cleanup/collector.ps1",
        f"docs/plan-033/{NEW_LABEL}-cleanup/directory.json",
    ]


def test_a_new_batch_label_is_byte_exact_in_every_family() -> None:
    attributes = _attributes(_new_batch_paths())
    wrong = {
        path: values
        for path, values in attributes.items()
        if values != {"text": "unset", "whitespace": "unset", "eol": "unspecified"}
    }
    assert not wrong, wrong


def test_the_bank_tool_writes_where_the_rule_reaches() -> None:
    """The rule's shape is the bank tool's layout: <family>-evidence/<label>/<lane>/."""
    assert all(family.endswith("-evidence") for family in TOOL["FAMILY"].values())
    assert TOOL["_LABEL"].fullmatch(NEW_LABEL)


def _tracked_under_the_rule() -> list[str]:
    files = subprocess.run(
        ["git", "ls-files", "-z", "--", "docs/plan-033"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.split("\0")
    covered = []
    for path in filter(None, files):
        parts = path.split("/")
        in_pack = len(parts) >= 6 and parts[2].endswith("-evidence")
        in_cleanup = len(parts) >= 4 and parts[2].endswith("-cleanup")
        if in_pack or in_cleanup:
            covered.append(path)
    return covered


def test_every_tracked_file_under_the_rule_is_byte_exact() -> None:
    """No tracked pack or cleanup file is converted on checkout.

    When the pattern was added, every file it matches already had ``-text
    -whitespace`` from its batch's own rule, so it changed no checkout's bytes;
    this keeps a later, more specific rule from turning conversion back on.
    """
    covered = _tracked_under_the_rule()
    assert len(covered) > 1000, "the guard is not looking at the banked packs"
    attributes = _attributes(covered)
    converted = sorted(
        path for path, values in attributes.items()
        if values["text"] != "unset" or values["whitespace"] != "unset"
    )
    assert not converted, converted[:20]


@pytest.mark.parametrize(
    "path",
    [
        "docs/plan-033/wp6-evidence/verdict-rsop-observe-20260907221946-2994.json",
        "docs/plan-033/fixture-recipe-v1.schema.json",
        "scripts/plan-033/run-requal-batch.sh",
    ],
)
def test_the_rule_reaches_nothing_outside_packs_and_cleanups(path: str) -> None:
    """Flat verdicts, schemas and tools keep their own rules (the pattern needs depth)."""
    whitespace = _attributes([path])[path]["whitespace"]
    assert whitespace == "unspecified", path
