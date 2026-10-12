"""Keep the round-trip mutation check runnable and run.

``scripts/mutation_check_roundtrip.py`` proves the round-trip and parity tests
fail when the code they name is broken. It runs as its own CI job (about a minute);
these fast checks fail in the ordinary test run when a refactor moves one of
its anchors, or when the job is dropped, rather than leaving a check that has
quietly stopped mutating anything.
"""

from __future__ import annotations

import runpy
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "mutation_check_roundtrip.py"
_SYMBOLS = runpy.run_path(str(SCRIPT))


def test_every_mutant_anchor_occurs_exactly_once() -> None:
    _SYMBOLS["check_anchors"](_SYMBOLS["MUTANTS"])


def test_every_mutant_names_tests_that_exist() -> None:
    for mutant in _SYMBOLS["MUTANTS"]:
        assert mutant.required, mutant.name
        assert mutant.anchor != mutant.replacement, mutant.name
        for node in mutant.required:
            path, _, name = node.partition("::")
            function = name.split("[", 1)[0]
            assert f"def {function}(" in (REPO_ROOT / path).read_text(encoding="utf-8"), node


def test_ci_runs_the_mutation_check() -> None:
    ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    job = ci.split("\n  mutation-check:\n", 1)[1].split("\n  secret-scan:\n", 1)[0]
    assert "uv run --locked python -I scripts/mutation_check_roundtrip.py" in job


def test_a_tmp_root_inside_the_repository_is_refused_before_anything_exists(
    tmp_path: Path,
) -> None:
    """Sol B3 / DeepSeek N6: a sandbox under the checkout copied itself into itself."""
    inside = REPO_ROOT / "scratch-mutation-probe" / "nested"
    assert _SYMBOLS["main"](["--tmp-root", str(inside)]) == 2
    assert not (REPO_ROOT / "scratch-mutation-probe").exists()
    # Spelled through a symlink that resolves inside, it is still refused.
    link = tmp_path / "into-repo"
    link.symlink_to(REPO_ROOT / "tests", target_is_directory=True)
    with pytest.raises(_SYMBOLS["SetupError"], match="inside the repository"):
        _SYMBOLS["resolve_tmp_root"](str(link / "mutants"))
    assert _SYMBOLS["resolve_tmp_root"](str(tmp_path)) == tmp_path.resolve()


def test_the_default_tmp_root_is_refused_when_tmpdir_points_inside(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tempfile

    monkeypatch.setattr(tempfile, "tempdir", str(REPO_ROOT / ".review" / "tmp"))
    with pytest.raises(_SYMBOLS["SetupError"], match="inside the repository"):
        _SYMBOLS["resolve_tmp_root"](None)


def test_a_sandbox_holds_tracked_files_only(tmp_path: Path) -> None:
    """Untracked scratch -- `.review/`, a stray temp dir -- never enters a copy."""
    tracked = set(_SYMBOLS["tracked_files"]())
    assert "scripts/mutation_check_roundtrip.py" in tracked
    assert not any(name.startswith((".venv/", "node_modules/", ".review/")) for name in tracked)
    holder, tree = _SYMBOLS["_make_sandbox"](tmp_path)
    with holder:
        copied = {path.relative_to(tree).as_posix() for path in tree.rglob("*") if path.is_file()}
    assert copied == tracked
