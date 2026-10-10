"""Keep the round-trip mutation check runnable and run.

``scripts/mutation_check_roundtrip.py`` proves the round-trip and parity tests
fail when the code they name is broken. It runs as its own CI job (about 40 s);
these fast checks fail in the ordinary test run when a refactor moves one of
its anchors, or when the job is dropped, rather than leaving a check that has
quietly stopped mutating anything.
"""

from __future__ import annotations

import runpy
from pathlib import Path

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
    assert "uv run --locked python scripts/mutation_check_roundtrip.py" in job
