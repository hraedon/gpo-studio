"""Load Plan 036 WP-0's tooling package (`scripts/plan-033/plan036_wp0/`) for tests.

The package sits in a directory whose name is not an identifier, so it is
loaded by path and registered as `plan036_wp0`; its submodules then import
normally. Nothing here touches `sys.path`.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE = REPO_ROOT / "scripts" / "plan-033" / "plan036_wp0"


def load(submodule: str) -> ModuleType:
    """`plan036_wp0.<submodule>`, loading the package by path the first time."""
    if "plan036_wp0" not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            "plan036_wp0", PACKAGE / "__init__.py", submodule_search_locations=[str(PACKAGE)]
        )
        assert spec is not None and spec.loader is not None
        package = importlib.util.module_from_spec(spec)
        sys.modules["plan036_wp0"] = package
        spec.loader.exec_module(package)
    return importlib.import_module(f"plan036_wp0.{submodule}")


def git_env() -> dict[str, str]:
    """The hermetic git environment (review round 6, Sol B2) for a test's own git
    commands: no system or global configuration, no home directory, the
    tooling's pinned command-scope settings. A test repository sets its own
    identity in its local configuration."""
    environment: dict[str, str] = load("repo").git_environment()
    return environment
