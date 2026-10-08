"""Batch manifests refuse rows produced on the driver's test scope stand-in.

Review round 4: the driver marks such rows `test_scope_tool: true`, but no
manifest gate read the marker -- a marked row passed every acceptance check.
batch_provenance.scope_provenance_problems is now the one rule, applied to
every banked manifest here (so a new manifest is covered without anyone
remembering to) and by each manifest's own test module.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from batch_provenance import manifest_paths, scope_provenance_problems

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFESTS = manifest_paths(REPO_ROOT)


def _load(path: Path) -> dict[str, Any]:
    return dict(json.loads(path.read_text(encoding="utf-8")))


def test_the_banked_manifests_are_all_found() -> None:
    names = {p.name for p in MANIFESTS}
    assert {
        "backup-report-batch.json",
        "plan034-batch.json",
        "wi059-batch.json",
        "wi062-batch.json",
    } <= names


@pytest.mark.parametrize("path", MANIFESTS, ids=lambda p: p.name)
def test_every_banked_manifest_passes_the_provenance_gate(path: Path) -> None:
    assert scope_provenance_problems(_load(path)) == []


@pytest.mark.parametrize("path", MANIFESTS, ids=lambda p: p.name)
@pytest.mark.parametrize("where", ["runs", "successors"])
def test_a_marked_row_fails_every_manifest(path: Path, where: str) -> None:
    """Negative control on the real manifests: mark one row and the gate fails."""
    manifest = copy.deepcopy(_load(path))
    rows = manifest.get(where) or []
    if not rows:
        pytest.skip(f"{path.name} has no {where}")
    rows[0]["test_scope_tool"] = True
    problems = scope_provenance_problems(manifest)
    assert len(problems) == 1 and "never evidence" in problems[0]


@pytest.mark.parametrize("value", [True, "false", 0, None, "true"])
def test_anything_but_boolean_false_is_refused(value: object) -> None:
    manifest = {"schema_version": 1, "runs": [{"name": "x", "test_scope_tool": value}]}
    assert scope_provenance_problems(manifest)


def test_a_schema_2_manifest_must_state_false_on_every_row() -> None:
    manifest: dict[str, Any] = {
        "schema_version": 2,
        "runs": [{"name": "a", "test_scope_tool": False}, {"name": "b"}],
        "successors": [{"name": "c"}],
    }
    problems = scope_provenance_problems(manifest)
    assert [p.split(":")[0] for p in problems] == ["b", "c"]
    manifest["runs"][1]["test_scope_tool"] = False
    manifest["successors"][0]["test_scope_tool"] = False
    assert scope_provenance_problems(manifest) == []


def test_a_schema_1_manifest_may_predate_the_field() -> None:
    assert scope_provenance_problems({"schema_version": 1, "runs": [{"name": "a"}]}) == []


def test_a_manifest_without_an_integer_schema_is_refused() -> None:
    assert scope_provenance_problems({"runs": []})
    assert scope_provenance_problems({"schema_version": "2", "runs": []})
