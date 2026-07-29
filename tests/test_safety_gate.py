"""Regression tests for the static safety gate itself.

The gate in ``scripts/check_safety.py`` enforces the load-bearing charter claim
that the web process never writes to AD or SYSVOL.  On 2026-07-27 it changed
from scanning all of ``src/`` to scanning the modules transitively reachable
from ``api.py``, plus a ``CATEGORY_EXEMPTIONS`` map for lab and release tooling
that never runs in a request path.

That change is only safe because an exemption is honoured *while the module
stays unreachable*.  Nothing committed proved that property held, so these
tests pin it: the exemption must collapse the moment an exempt module is
imported into the web process.

See ``docs/gate-decision-2026-07-29-static-safety.md``.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
GATE_PATH = REPO_ROOT / "scripts" / "check_safety.py"


def _load_gate() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_safety", GATE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def gate() -> ModuleType:
    return _load_gate()


def _trees(sources: dict[str, str]) -> dict[str, ast.Module]:
    return {name: ast.parse(src) for name, src in sources.items()}


def test_gate_passes_against_the_real_tree(gate: ModuleType) -> None:
    """The committed tree must satisfy its own gate."""
    assert gate.main() == 0


def test_exempt_module_is_currently_unreachable(gate: ModuleType) -> None:
    """The premise behind every exemption: exempt modules are not in the web process.

    If this fails, an exemption has silently become a charter hole and the
    fail-closed check below is the only thing standing between it and CI.
    """
    trees: dict[str, ast.Module] = {}
    for py_file in sorted((REPO_ROOT / "src" / "gpo_studio").rglob("*.py")):
        trees[py_file.stem] = ast.parse(py_file.read_text(), filename=str(py_file))

    reachable = gate._web_process_modules(trees)
    for category, exempt in gate.CATEGORY_EXEMPTIONS.items():
        assert not (exempt & reachable), (
            f"{sorted(exempt & reachable)} is exempt from '{category}' "
            f"but reachable from {gate.WEB_PROCESS_ENTRYPOINT}.py"
        )


def test_exemption_fails_closed_when_module_becomes_reachable(gate: ModuleType) -> None:
    """The negative test: importing an exempt module into the web process fails the gate.

    This is the property that makes the reachability-scoped gate strictly
    stronger than the scan-everything version it replaced.
    """
    category, exempt = next(iter(gate.CATEGORY_EXEMPTIONS.items()))
    exempt_module = sorted(exempt)[0]

    trees = _trees(
        {
            gate.WEB_PROCESS_ENTRYPOINT: f"from gpo_studio.{exempt_module} import thing",
            exempt_module: "import subprocess",
        }
    )

    violations = gate._check_exempt_unreachable(gate._web_process_modules(trees))

    assert violations, (
        f"{exempt_module} was imported into {gate.WEB_PROCESS_ENTRYPOINT}.py "
        f"but the gate did not object"
    )
    assert exempt_module in violations[0]
    assert category in violations[0]


def test_exemption_survives_only_direct_unreachability(gate: ModuleType) -> None:
    """Reachability is transitive: an exempt module pulled in indirectly still fails."""
    category, exempt = next(iter(gate.CATEGORY_EXEMPTIONS.items()))
    exempt_module = sorted(exempt)[0]

    trees = _trees(
        {
            gate.WEB_PROCESS_ENTRYPOINT: "from gpo_studio.middle import thing",
            "middle": f"from gpo_studio.{exempt_module} import other",
            exempt_module: "import subprocess",
        }
    )

    assert gate._check_exempt_unreachable(gate._web_process_modules(trees))


def test_exemption_is_scoped_to_its_category(gate: ModuleType) -> None:
    """An exemption for one category must not license a different forbidden import.

    ``oracle_evidence`` may shell out to git; it may not import an AD/SMB
    client.
    """
    category, exempt = next(iter(gate.CATEGORY_EXEMPTIONS.items()))
    exempt_module = sorted(exempt)[0]

    other_category = next(c for c in gate.FORBIDDEN_IMPORTS if c != category)
    forbidden = gate.FORBIDDEN_IMPORTS[other_category][0]

    tree = ast.parse(f"import {forbidden}")
    violations = gate._check_imports(tree, Path(f"{exempt_module}.py"))

    assert violations, f"{exempt_module}.py must not be exempt from '{other_category}'"
    assert other_category in violations[0]


def test_unexempt_module_may_not_shell_out(gate: ModuleType) -> None:
    """The baseline the exemption carves out of: shell execution is otherwise refused."""
    tree = ast.parse("import subprocess")
    violations = gate._check_imports(tree, Path("api.py"))

    assert violations
    assert "Shell execution" in violations[0]
