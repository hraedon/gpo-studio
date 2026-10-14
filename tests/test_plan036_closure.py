"""Plan 036 WP-0 part 1: the static import closure.

Re-derived from the tree, never traced: imports at any depth, every enclosing
package's `__init__.py`, the interpreter's resolution order for the process
(the script's directory first, so a repository file can shadow a standard
library module), and declared module loads joining the loading process. An
import only under `if TYPE_CHECKING:` never runs and is left out, but only
when the guard is exactly `typing.TYPE_CHECKING`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from plan036_support import REPO_ROOT, load

closure = load("closure")
lanes = load("lanes")
record = load("record")
scan = load("scan")
repo_mod = load("repo")


def _write(root: Path, files: dict[str, str]) -> None:
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def test_imports_at_any_depth_and_every_package_init(tmp_path: Path) -> None:
    _write(tmp_path, {
        "scripts/tool/run.py": "def main():\n    from pkg.sub import thing\n",
        "src/pkg/__init__.py": "",
        "src/pkg/sub/__init__.py": "from .leaf import thing\n",
        "src/pkg/sub/leaf.py": "thing = 1\n",
    })
    process = closure.derive_process(tmp_path, "scripts/tool/run.py")
    assert set(process.modules) == {
        "scripts/tool/run.py", "src/pkg/__init__.py", "src/pkg/sub/__init__.py",
        "src/pkg/sub/leaf.py",
    }


REAL_GUARDS = {
    "typing": "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n",
    "typing aliased": "from typing import TYPE_CHECKING as TC\nif TC:\n",
    "typing module": "import typing\nif typing.TYPE_CHECKING:\n",
    "typing module aliased": "import typing as t\nif t.TYPE_CHECKING:\n",
    "typing_extensions": "from typing_extensions import TYPE_CHECKING\nif TYPE_CHECKING:\n",
    "typing_extensions aliased": (
        "from typing_extensions import TYPE_CHECKING as TC\nif TC:\n"
    ),
    "typing_extensions module": "import typing_extensions\nif typing_extensions.TYPE_CHECKING:\n",
    "typing_extensions module aliased": "import typing_extensions as te\nif te.TYPE_CHECKING:\n",
}
FAKE_GUARDS = {
    "local constant": "TYPE_CHECKING = True\nif TYPE_CHECKING:\n",
    "imported from elsewhere": "from settings import TYPE_CHECKING\nif TYPE_CHECKING:\n",
    "rebound after import": (
        "from typing import TYPE_CHECKING\nTYPE_CHECKING = True\nif TYPE_CHECKING:\n"
    ),
    "aliased name reused": (
        "from typing import TYPE_CHECKING as TC\nTC = 1\nif TC:\n"
    ),
    "attribute of another module": "import settings\nif settings.TYPE_CHECKING:\n",
    "typing rebound": "import typing\ntyping = object()\nif typing.TYPE_CHECKING:\n",
    "a different typing attribute": "import typing\nif typing.TYPE_CHECKING_X:\n",
}


def _guarded_tree(tmp_path: Path, guard: str) -> object:
    _write(tmp_path, {
        "scripts/tool/run.py": "import pkg.a\n",
        "src/pkg/__init__.py": "",
        "src/pkg/a.py": (
            guard + "    from . import only_types\nelse:\n    from . import at_run_time\n"
        ),
        "src/pkg/only_types.py": "",
        "src/pkg/at_run_time.py": "",
    })
    return closure.derive_process(tmp_path, "scripts/tool/run.py")


@pytest.mark.parametrize("spelling", sorted(REAL_GUARDS))
def test_imports_under_the_real_type_checking_guard_are_left_out(
    tmp_path: Path, spelling: str
) -> None:
    process = _guarded_tree(tmp_path, REAL_GUARDS[spelling])
    assert "src/pkg/only_types.py" not in process.modules, spelling
    assert "src/pkg/at_run_time.py" in process.modules  # the else branch runs
    assert any("only_types" in module for _, _, module in process.type_only)


@pytest.mark.parametrize("spelling", sorted(FAKE_GUARDS))
def test_a_fake_type_checking_guard_is_not_honoured(tmp_path: Path, spelling: str) -> None:
    process = _guarded_tree(tmp_path, FAKE_GUARDS[spelling])
    assert "src/pkg/only_types.py" in process.modules, spelling
    assert process.type_only == []


def test_the_scripts_directory_comes_first_and_can_shadow_the_standard_library(
    tmp_path: Path,
) -> None:
    _write(tmp_path, {
        "scripts/tool/run.py": "import json\nimport helper\n",
        "scripts/tool/json.py": "",
        "scripts/tool/helper.py": "import os\n",
    })
    process = closure.derive_process(tmp_path, "scripts/tool/run.py")
    assert "scripts/tool/json.py" in process.modules
    assert "scripts/tool/helper.py" in process.modules
    assert "os" in process.external


def test_a_namespace_package_is_refused(tmp_path: Path) -> None:
    _write(tmp_path, {"scripts/tool/run.py": "import nsp.mod\n", "src/nsp/mod.py": ""})
    with pytest.raises(repo_mod.Wp0Error, match="namespace package"):
        closure.derive_process(tmp_path, "scripts/tool/run.py")


def test_a_declared_module_load_joins_the_loading_process(tmp_path: Path) -> None:
    _write(tmp_path, {
        "scripts/fin/run.py": "import runpy\n",
        "scripts/build/builder.py": "import pkg\n",
        "src/pkg/__init__.py": "",
    })
    process = closure.derive_process(tmp_path, "scripts/fin/run.py",
                                     loaded=("scripts/build/builder.py",))
    assert {"scripts/build/builder.py", "src/pkg/__init__.py"} <= set(process.modules)


def _lane_modules(name: str) -> set[str]:
    lane = next(lane for lane in lanes.live_lanes(REPO_ROOT) if lane.name == name)
    processes = record.lane_processes(REPO_ROOT, lane, scan.Lists.load())
    return {path for process in processes.values() for path in process.modules}


def test_the_rsop_lanes_closure_holds_what_their_tables_never_bound() -> None:
    """The plan's headline: the RSoP lanes do not bind rsop.py, som.py or model.py,
    which their builder imports and calls; the derived closure has all three."""
    modules = _lane_modules("lsdou-precedence")
    assert {"src/gpo_studio/rsop.py", "src/gpo_studio/som.py", "src/gpo_studio/model.py",
            "src/gpo_studio/__init__.py"} <= modules


@pytest.mark.parametrize("name", ["wp1b", "wp2", "endpoint", "fdeploy", "firewall", "lifecycle",
                                  "publication", "report-parity", "scripts-metadata"])
def test_the_nine_lanes_import_ilt(name: str) -> None:
    assert "src/gpo_studio/ilt.py" in _lane_modules(name)


def test_wp0s_closure_includes_the_package_init() -> None:
    assert "src/gpo_studio/__init__.py" in _lane_modules("wp0")
