"""What a release changes after its freeze commit never reaches a distribution.

The final-tag gate lets the banking and approval commits after the freeze
commit F add a batch's evidence and modify a closed list of records
(``FREEZE_RECORDS`` in ``scripts/check_release_manifest.py``). Sol's final-r1 B2
probe showed that two of those records reached the published artifacts:
README.md is the wheel's long description, and the sdist carried every record
and the whole evidence tree. README.md and CHANGELOG.md are now frozen at F,
and ``pyproject.toml`` excludes every post-freeze path from the sdist.

This builds the wheel and sdist from a copy of the tree, and again after
changing every record the gate allows and adding a batch (packs, a
``conftest.py`` among them, manifest, note and post-batch check), and requires
identical bytes. A change to README.md, the control, does change the wheel.
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]
EPOCH = "315532800"


def _gate() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "check_release_manifest_artifacts", ROOT / "scripts" / "check_release_manifest.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gate = _gate()


def _tree_copy(target: Path) -> None:
    """Every tracked file, but the banked evidence.

    Tracked only, so the copy does not depend on what else lies in the worktree
    (Sol final-r2 N2: untracked review scratch made one run copy 7.7 GB). The
    evidence packs are left out only to keep the copy small: the changed tree
    adds a pack of its own, so the exclusion is still exercised.
    """
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--cached"],
        cwd=ROOT, capture_output=True, check=True,
    ).stdout.decode("utf-8").split("\0")
    for relative in filter(None, listed):
        if re.match(r"docs/plan-033/[^/]+-evidence/", relative):
            continue
        source = ROOT / relative
        if not source.is_file() or source.is_symlink():
            continue
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)


def _build(tree: Path, out: Path) -> dict[str, bytes]:
    out.mkdir()
    env = {**os.environ, "SOURCE_DATE_EPOCH": EPOCH}
    subprocess.run(
        [sys.executable, "-I", "-c",
         "import sys, hatchling.build as b; b.build_sdist(sys.argv[1]); b.build_wheel(sys.argv[1])",
         str(out)],
        cwd=tree, env=env, capture_output=True, check=True,
    )
    built = {path.name: path.read_bytes() for path in out.iterdir()}
    assert sorted(name.rsplit(".", 1)[-1] for name in built) == ["gz", "whl"], sorted(built)
    return built


def _version_base() -> str:
    text = (ROOT / "src" / "gpo_studio" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__ = "(\d+\.\d+\.\d+)', text, re.M)
    assert match is not None
    return match.group(1)


#: A batch as the bank tool writes it, under a label no batch has used.
LABEL = "final-20991231"
BATCH_FILES = {
    f"docs/plan-033/wp6-evidence/{LABEL}/endpoint/verification.json": b"{}\n",
    f"docs/plan-033/wp6-evidence/{LABEL}/endpoint/controller.log": b"ran\r\n",
    f"docs/plan-033/wp6-evidence/{LABEL}/endpoint/conftest.py": b"raise SystemExit(1)\n",
    f"docs/plan-033/wp0-evidence/{LABEL}/wp0/manifest.json": b"{}\n",
    f"docs/plan-033/{LABEL}-batch.json": b"{}\n",
    f"docs/plan-033/{LABEL}-batch.md": b"# batch\n",
    f"docs/plan-033/{LABEL}-cleanup/collector.ps1": b"Get-ADObject\n",
    f"docs/plan-033/{LABEL}-cleanup/directory.json": b"{}\n",
}


def _changed_paths(tree: Path) -> list[str]:
    records = sorted(gate._records(_version_base()))
    results = sorted(
        path.relative_to(tree).as_posix()
        for path in (tree / "docs" / "plan-033").glob("*-results.md")
    )
    assert results, "no results documents in the copy"
    return records + results


@pytest.fixture(scope="module")
def builds(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict[str, bytes]]:
    base = tmp_path_factory.mktemp("artifacts")
    frozen, banked, readme = base / "frozen", base / "banked", base / "readme"
    _tree_copy(frozen)
    shutil.copytree(frozen, banked)
    shutil.copytree(frozen, readme)
    for relative in _changed_paths(banked):
        path = banked / relative
        assert path.is_file(), relative
        path.write_bytes(path.read_bytes() + b"\nPOST-FREEZE-ALLOWLIST-PROBE\n")
    for relative, data in BATCH_FILES.items():
        path = banked / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    (readme / "README.md").write_bytes(
        (readme / "README.md").read_bytes() + b"\nPOST-FREEZE-ALLOWLIST-PROBE\n"
    )
    return {
        "frozen": _build(frozen, base / "out-frozen"),
        "banked": _build(banked, base / "out-banked"),
        "readme": _build(readme, base / "out-readme"),
    }


def test_records_and_evidence_changed_after_the_freeze_leave_both_distributions_alone(
    builds: dict[str, dict[str, bytes]],
) -> None:
    assert builds["frozen"].keys() == builds["banked"].keys()
    differ = sorted(n for n in builds["frozen"] if builds["frozen"][n] != builds["banked"][n])
    assert not differ, differ


def test_no_post_freeze_path_is_in_the_sdist(builds: dict[str, dict[str, bytes]]) -> None:
    (name,) = [n for n in builds["banked"] if n.endswith(".tar.gz")]
    import io

    with tarfile.open(fileobj=io.BytesIO(builds["banked"][name])) as archive:
        members = {member.name.split("/", 1)[1] for member in archive.getmembers()}
    forbidden = {*_changed_paths(ROOT), *BATCH_FILES}
    assert not forbidden & members, sorted(forbidden & members)
    assert not [m for m in members if re.match(r"docs/plan-033/[^/]+-evidence/", m)]
    # What CI requires in the sdist is still there, and frozen at F.
    for required in ("README.md", "LICENSE", "pyproject.toml", "AGENTS.md", "SECURITY.md",
                     "CHANGELOG.md", "CONTRIBUTING.md"):
        assert required in members, required


def test_the_control_a_readme_change_reaches_the_wheel(
    builds: dict[str, dict[str, bytes]],
) -> None:
    """Why README.md is frozen at F rather than a post-freeze record."""
    (wheel,) = [n for n in builds["frozen"] if n.endswith(".whl")]
    assert builds["frozen"][wheel] != builds["readme"][wheel]
    for record in gate._records(_version_base()):
        assert record not in ("README.md", "CHANGELOG.md"), record
