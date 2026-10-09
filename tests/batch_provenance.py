"""Scope provenance for requalification batch manifests.

run-requal-batch.sh can run its containment layer on a TEST stand-in (its
`--test-scope-tool` seam, for CI). Such a batch proves the driver's control
flow, never Windows behaviour, and every progress row it writes records
`test_scope_tool: true`. A manifest built from those rows must not be
accepted as evidence, so every batch-manifest gate calls this.

The rules:

* a run or successor carrying `test_scope_tool` anything but the boolean
  `false` is refused;
* manifests at schema_version 2 or later -- built from rows the driver now
  always marks -- must carry `test_scope_tool: false` on every run and
  successor; a missing field is refused;
* schema_version 1 manifests predate the field and may omit it, but may not
  carry a true marker either.

exec_failed_problems holds the one row field schema 3 added: `exec_failed`,
true when the lane's command never started (not found, not executable) --
which its 127 or 126 exit status alone cannot say. Schema 3 manifests must
carry it as a boolean on every run, not-passed row and successor, and a
passing run cannot have it true; earlier manifests predate it.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

#: The first manifest schema whose rows must state their scope provenance.
SCOPE_PROVENANCE_SCHEMA = 2
#: The first manifest schema whose rows must state `exec_failed`.
EXEC_FAILED_SCHEMA = 3
#: Every batch manifest the repository banks.
MANIFESTS = "docs/plan-033/*-batch.json"


def scope_provenance_problems(manifest: Mapping[str, Any]) -> list[str]:
    """Everything that makes `manifest` unacceptable on scope provenance."""
    problems: list[str] = []
    schema = manifest.get("schema_version")
    if type(schema) is not int:
        return [f"schema_version is {schema!r}, not an integer"]
    rows = [*manifest.get("runs", []), *manifest.get("successors", [])]
    for row in rows:
        name = row.get("name", "<unnamed>")
        if "test_scope_tool" in row:
            if row["test_scope_tool"] is not False:
                problems.append(
                    f"{name}: test_scope_tool is {row['test_scope_tool']!r} -- produced on the "
                    "driver's test scope stand-in, never evidence"
                )
        elif schema >= SCOPE_PROVENANCE_SCHEMA:
            problems.append(
                f"{name}: no test_scope_tool field; schema {schema} manifests must state "
                "test_scope_tool: false"
            )
    return problems


def exec_failed_problems(manifest: Mapping[str, Any]) -> list[str]:
    """Everything that makes `manifest` unacceptable on `exec_failed`."""
    schema = manifest.get("schema_version")
    if type(schema) is not int:
        return [f"schema_version is {schema!r}, not an integer"]
    problems: list[str] = []
    for where in ("runs", "not_passed", "successors"):
        for row in manifest.get(where) or []:
            name = row.get("name", "<unnamed>")
            if "exec_failed" not in row:
                if schema >= EXEC_FAILED_SCHEMA:
                    problems.append(
                        f"{name}: no exec_failed field; schema {schema} manifests must state it"
                    )
                continue
            if type(row["exec_failed"]) is not bool:
                problems.append(f"{name}: exec_failed is {row['exec_failed']!r}, not a boolean")
            elif row["exec_failed"] and where == "runs":
                problems.append(f"{name}: a passing run whose command never started")
    return problems


def manifest_paths(repo_root: Path) -> list[Path]:
    return sorted(repo_root.glob(MANIFESTS))
