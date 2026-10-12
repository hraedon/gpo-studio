"""The project build uses uv.lock's backend, and the audit covers every platform.

`uv sync --locked` locked the application graph, but its isolated editable build
resolved `[build-system].requires = ["hatchling"]` from the index at `*` (Sol
B1, reproduced offline). The fix turns build isolation off for gpo-studio and
makes the locked `build-backend` group a default group, so the backend comes
from the lock. `scripts/check_locked_build_backend.py` proves it by running uv
offline (CI runs it); these fast checks hold the configuration it depends on.
"""

from __future__ import annotations

import runpy
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
LOCK = tomllib.loads((REPO_ROOT / "uv.lock").read_text(encoding="utf-8"))
CHECK = runpy.run_path(str(REPO_ROOT / "scripts" / "check_locked_build_backend.py"))
PLATFORMS = runpy.run_path(str(REPO_ROOT / "scripts" / "requirements_all_platforms.py"))


def _name(requirement: str) -> str:
    for stop in "<>=!~[ ;":
        requirement = requirement.split(stop, 1)[0]
    return requirement.strip().lower()


def test_the_project_is_built_without_isolation_from_a_default_locked_group() -> None:
    tool = PYPROJECT["tool"]["uv"]
    # Off for every build: a dependency's sdist would resolve its build requirements
    # from the index in an isolated environment (Sol r9 B2).
    assert tool["no-build-isolation"] is True
    assert "no-build-isolation-package" not in tool
    # And no dependency is built at all: every locked dependency is listed.
    assert "hatchling" in tool["no-build-package"]
    assert "build-backend" in tool["default-groups"]


def test_the_locked_group_holds_every_build_system_requirement() -> None:
    required = {_name(item) for item in PYPROJECT["build-system"]["requires"]}
    group = {_name(item) for item in PYPROJECT["dependency-groups"]["build-backend"]}
    assert required <= group, f"{required - group} is built from the index, not the lock"
    # hatchling asks for editables when it builds an editable install.
    assert "editables" in group


def test_the_lock_resolves_the_backend_closure() -> None:
    closure = CHECK["backend_closure"](LOCK)
    assert {"hatchling", "editables"} <= closure.keys()
    hatchling = next(p for p in LOCK["package"] if p["name"] == "hatchling")
    assert closure["hatchling"] == hatchling["version"]


def test_the_release_group_includes_the_backend() -> None:
    release = PYPROJECT["dependency-groups"]["release"]
    assert {"include-group": "build-backend"} in release


def test_ci_runs_the_offline_backend_proof() -> None:
    ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "uv run --locked python -I scripts/check_locked_build_backend.py" in ci


def test_the_backend_proof_refuses_a_tmp_root_inside_the_repository(tmp_path: Path) -> None:
    assert CHECK["main"](["--tmp-root", str(REPO_ROOT / "scratch-inside")]) == 1
    assert not (REPO_ROOT / "scratch-inside").exists()


_LOCK_SAMPLE = """\
cffi==2.1.0 ; platform_python_implementation != 'PyPy' \\
    --hash=sha256:aa
colorama==0.4.6 ; sys_platform == 'win32' \\
    --hash=sha256:bb \\
    --hash=sha256:cc
    # via click
fastapi==0.139.0 \\
    --hash=sha256:dd
"""


def test_the_audit_input_keeps_every_platform_s_packages_and_hashes() -> None:
    """Sol N3: pip-audit on Linux skipped `colorama ; sys_platform == 'win32'`."""
    out = PLATFORMS["strip_markers"](_LOCK_SAMPLE)
    assert ";" not in out
    assert "colorama==0.4.6 \\\n" in out and "cffi==2.1.0 \\\n" in out
    assert out.count("--hash=sha256:") == _LOCK_SAMPLE.count("--hash=sha256:")
    assert "fastapi==0.139.0 \\\n" in out


def test_the_audit_reads_the_marker_free_file() -> None:
    ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    audit = ci.split("- name: Audit shipped dependencies", 1)[1].split("\n  sbom:", 1)[0]
    assert "python -I scripts/requirements_all_platforms.py" in audit
    assert '--requirement "$RUNNER_TEMP/locked/requirements-all-platforms.txt"' in audit
