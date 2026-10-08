"""A lane binds the code its candidate's bytes flow through (batch 2, review P1).

Every lane verdict records `(commit, path, sha256)` for the files its finalizer
tables name, and `test_a_live_verdict_still_binds_the_harness_that_ships`
expires a verdict when one of those files changes. A module the candidate
depends on but the tables omit can change under a live verdict unnoticed --
which is what the first cut of `deterministic_zip.py` did: every archive went
through it and no lane bound it.

The rule checked here, derived from the builders' own imports rather than
restated: a builder that imports `gpo_studio.export` (whose GPMC backup is
written by `deterministic_zip`) must have `export.py` AND
`deterministic_zip.py` bound by its finalizer; one that imports
`gpo_studio.deterministic_zip` directly must have the helper bound.
"""

from __future__ import annotations

import ast
import runpy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ORACLE = ROOT / "scripts" / "windows-oracle"


def _tables(finalizer: Path) -> dict[str, str]:
    symbols = runpy.run_path(str(finalizer))
    merged: dict[str, str] = {}
    for keyed, flat in (
        ("TRANSPORT_DEPLOYED_FILES", "DEPLOYED_FILES"),
        ("TRANSPORT_LOCAL_FILES", "LOCAL_FILES"),
    ):
        if keyed in symbols:
            merged.update(symbols[keyed]["psdirect"])
        elif flat in symbols:
            merged.update(symbols[flat])
    return merged


def _gpo_studio_imports(builder: Path) -> set[str]:
    tree = ast.parse(builder.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith(
            "gpo_studio."
        ):
            found.add(node.module.removeprefix("gpo_studio."))
        elif isinstance(node, ast.Import):
            found.update(
                alias.name.removeprefix("gpo_studio.")
                for alias in node.names
                if alias.name.startswith("gpo_studio.")
            )
    return found


def _lanes() -> list[tuple[str, dict[str, str], set[str]]]:
    lanes = []
    for finalizer in sorted(ORACLE.glob("finalize_*.py")):
        tables = _tables(finalizer)
        builders = [path for path in tables.values() if Path(path).name.startswith("build-")]
        imports: set[str] = set()
        for builder in builders:
            imports |= _gpo_studio_imports(ROOT / builder)
        lanes.append((finalizer.name, tables, imports))
    return lanes


LANES = _lanes()


@pytest.mark.parametrize(
    ("finalizer", "tables", "imports"), LANES, ids=[lane[0] for lane in LANES]
)
def test_a_lane_binds_the_archive_code_its_candidate_flows_through(
    finalizer: str, tables: dict[str, str], imports: set[str]
) -> None:
    required: set[str] = set()
    if "export" in imports:
        required |= {"export.py", "deterministic_zip.py"}
    if "deterministic_zip" in imports:
        required.add("deterministic_zip.py")
    missing = {
        name for name in required if tables.get(name) != f"src/gpo_studio/{name}"
    }
    assert not missing, f"{finalizer} does not bind {sorted(missing)}"


def test_the_rule_reaches_every_archive_writing_lane() -> None:
    """The control: the rule above is not vacuous for the lanes it is about."""
    covered = {
        finalizer
        for finalizer, _tables_, imports in LANES
        if imports & {"export", "deterministic_zip"}
    }
    assert covered >= {
        "finalize_wp1b_run.py",
        "finalize_wp2_import_run.py",
        "finalize_publication_run.py",
        "finalize_scripts_backup_run.py",
        "finalize_firewall_run.py",
        "finalize_report_parity_run.py",
        "finalize_fdeploy_run.py",
        "finalize_endpoint_run.py",
    }


def test_a_helper_edit_would_expire_a_verdict_that_binds_it(tmp_path: Path) -> None:
    """What binding buys, through the gate's own `_recorded_vs_tree`.

    A synthetic fdeploy verdict records the shipping helper's hash: no drift.
    The same verdict against a tree whose helper was edited: drift, naming
    `deterministic_zip.py` -- the live-verdict gate's failure.
    """
    import hashlib
    import importlib.util
    import json
    import shutil

    spec = importlib.util.spec_from_file_location(
        "committed_evidence_for_binding", ROOT / "tests" / "test_committed_evidence.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    evidence = module.__dict__
    helper = "src/gpo_studio/deterministic_zip.py"
    digest = hashlib.sha256((ROOT / helper).read_bytes()).hexdigest()
    verdict_dir = tmp_path / "evidence"
    verdict_dir.mkdir()
    (verdict_dir / "verdict.json").write_text(
        json.dumps({"source": {"files": {"deterministic_zip.py": digest}}}), encoding="utf-8"
    )
    evidence["EVIDENCE"] = verdict_dir
    recorded_vs_tree = evidence["_recorded_vs_tree"]
    assert recorded_vs_tree("verdict.json", "finalize_fdeploy_run.py") == []

    edited_repo = tmp_path / "repo"
    (edited_repo / "src" / "gpo_studio").mkdir(parents=True)
    shutil.copyfile(ROOT / helper, edited_repo / helper)
    with (edited_repo / helper).open("a", encoding="utf-8") as stream:
        stream.write("\n# an edit\n")
    evidence["REPO_ROOT"] = edited_repo
    drifted = recorded_vs_tree("verdict.json", "finalize_fdeploy_run.py")
    assert [name for name, _recorded, _actual in drifted] == ["deterministic_zip.py"]
