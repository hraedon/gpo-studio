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


def test_type_checking_imports_are_left_out_only_under_the_real_guard(tmp_path: Path) -> None:
    _write(tmp_path, {
        "scripts/tool/run.py": "import pkg.a\nimport pkg.b\n",
        "src/pkg/__init__.py": "",
        "src/pkg/a.py": "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n"
                        "    from . import only_types\nelse:\n    from . import at_run_time\n",
        "src/pkg/b.py": "TYPE_CHECKING = True\nif TYPE_CHECKING:\n    from . import rebound\n",
        "src/pkg/only_types.py": "",
        "src/pkg/at_run_time.py": "",
        "src/pkg/rebound.py": "",
    })
    process = closure.derive_process(tmp_path, "scripts/tool/run.py")
    assert "src/pkg/only_types.py" not in process.modules
    assert "src/pkg/at_run_time.py" in process.modules
    assert "src/pkg/rebound.py" in process.modules  # not typing's constant: a run-time import
    assert any("only_types" in module for _, _, module in process.type_only)


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
