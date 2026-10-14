"""Plan 036 WP-0 part 1: the fail-closed scan, held to the plan's planted probes.

`plans/036-change-cost.md`, "The four parts", 1: every resolved call target,
callable reference and implicit protocol operation must be inert, a declared
edge or proved platform-unreachable, or the scan fails; unresolvable targets
fail unless declared. Each probe below is a module the scan must refuse
(until a declaration covers it), except the false-guarded `ctypes.WinDLL`,
which must classify as platform-unreachable. All probes are analysed in one
mypy build (a few seconds), exactly as the real closure is.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from plan036_support import load

scan = load("scan")
repo = load("repo")

#: name -> source. Every probe is fully annotated so that mypy types it; what
#: makes it fail is the target, never a missing annotation.
FAILING_PROBES = {
    # Review round 1 (Sol B3 / DeepSeek N1): methods of a ZipFile, a DirEntry, a
    # stream or an abstract mutable mapping reach outside memory; none is inert.
    "zip_write": (
        "import zipfile\nfrom pathlib import Path\n\n\n"
        "def f(archive: Path, undeclared_input: Path) -> None:\n"
        "    with zipfile.ZipFile(archive, 'w') as bundle:\n"
        "        bundle.write(undeclared_input)\n"
    ),
    "zip_extractall": (
        "import io\nimport zipfile\nfrom pathlib import Path\n\n\n"
        "def f(data: bytes, where: Path) -> None:\n"
        "    zipfile.ZipFile(io.BytesIO(data)).extractall(where)\n"
    ),
    "direntry_stat": (
        "import os\n\n\ndef f(entry: os.DirEntry[str]) -> int:\n"
        "    return entry.stat().st_size\n"
    ),
    "stream_write": (
        "from typing import BinaryIO\n\n\ndef f(stream: BinaryIO) -> None:\n"
        "    stream.write(b'x')\n"
    ),
    "mutable_mapping_store": (
        "from collections.abc import MutableMapping\n\n\n"
        "def f(env: MutableMapping[str, str]) -> None:\n    env['X'] = '1'\n"
    ),
    "context_manager_exit": (
        "from contextlib import AbstractContextManager\n\n\n"
        "def f(manager: AbstractContextManager[object]) -> None:\n"
        "    with manager:\n        pass\n"
    ),
    "logging_fileconfig": (
        "import logging.config\n\n\ndef f(path: str) -> None:\n"
        "    logging.config.fileConfig(path)\n"
    ),
    "shelve_open": "import shelve\n\n\ndef f(path: str) -> None:\n    shelve.open(path)\n",
    "dbm_open": "import dbm\n\n\ndef f(path: str) -> None:\n    dbm.open(path)\n",
    "mmap_mmap": "import mmap\n\n\ndef f(fd: int) -> None:\n    mmap.mmap(fd, 0)\n",
    "multiprocessing_process": (
        "import multiprocessing\n\n\ndef f() -> None:\n"
        "    multiprocessing.Process(target=print).start()\n"
    ),
    "ctypes_cdll": "import ctypes\n\n\ndef f() -> None:\n    ctypes.CDLL('libc.so.6')\n",
    "ctypes_windll_true_guard": (
        "import ctypes\nimport sys\n\n\ndef f() -> None:\n"
        "    if sys.platform == 'linux':\n        ctypes.WinDLL('x')\n"
    ),
    "ctypes_windll_true_os_name": (
        "import ctypes\nimport os\n\n\ndef f() -> None:\n"
        "    if os.name == 'posix':\n        ctypes.WinDLL('x')\n"
    ),
    "map_open": "def f(paths: list[str]) -> None:\n    list(map(open, paths))\n",
    "partial_open": (
        "import functools\n\n\ndef f() -> None:\n    functools.partial(open, 'x')()\n"
    ),
    "any_method": "from typing import Any\n\n\ndef f(value: Any) -> None:\n    value.anything()\n",
    "getattr_os": "import os\n\n\ndef f(name: str) -> None:\n    getattr(os, name)()\n",
    "with_open": (
        "def f(path: str) -> str:\n    with open(path) as handle:\n        return handle.read()\n"
    ),
    "any_operator": "from typing import Any\n\n\ndef f(value: Any) -> Any:\n    return value + 1\n",
    "csv_iteration": (
        "import csv\n\n\ndef f(lines: list[str]) -> None:\n"
        "    for row in csv.reader(lines):\n        print(row)\n"
    ),
    "orig_argv": "import sys\n\n\ndef f() -> list[str]:\n    return sys.orig_argv\n",
    "meta_path": (
        "import sys\nfrom typing import Any\n\n\ndef f(finder: Any) -> None:\n"
        "    sys.meta_path.append(finder)\n"
    ),
    "builtins_write": (
        "import builtins\n\n\ndef f() -> None:\n    setattr(builtins, 'open', print)\n"
    ),
    "sys_path_insert": "import sys\n\n\ndef f() -> None:\n    sys.path.insert(0, 'x')\n",
    "dunder_import": "def f(name: str) -> object:\n    return __import__(name)\n",
    "exec_call": "def f(code: str) -> None:\n    exec(code)\n",
    "environ_read": "import os\n\n\ndef f() -> str:\n    return os.environ['HOME']\n",
}
UNREACHABLE_PROBES = {
    "ctypes_windll_false_guard": (
        "import ctypes\nimport sys\n\n\ndef f() -> None:\n"
        "    if sys.platform == 'win32':\n        ctypes.WinDLL('x')\n"
    ),
    "ctypes_windll_guard_raises_first": (
        "import sys\n\n\ndef f() -> None:\n    if sys.platform != 'win32':\n"
        "        raise SystemExit(1)\n    import ctypes\n    ctypes.WinDLL('x')\n"
    ),
}
#: Probes the JSON proof and the argparse plugin must and must not discharge.
TYPED_PROBES = {
    "dict_update": (
        "def f(table: dict[str, int]) -> None:\n    table.update({'x': 1})\n"
        "    table.setdefault('y', 2)\n"
    ),
    "json_local": (
        "import json\n\n\ndef f(text: str) -> str:\n    data = json.loads(text)\n"
        "    return str(data['k'].get('x'))\n"
    ),
    "json_through_helpers": (
        "import json\nfrom typing import Any\n\n\ndef load(text: str) -> Any:\n"
        "    return json.loads(text)\n\n\ndef use(record: dict[str, Any]) -> str:\n"
        "    return str(record.get('x'))\n\n\ndef main(text: str) -> str:\n"
        "    return use(load(text))\n"
    ),
    "json_refuted_by_a_caller": (
        "import json\nfrom typing import Any\n\n\ndef use(record: dict[str, Any]) -> str:\n"
        "    return str(record.get('x'))\n\n\ndef main(text: str, other: Any) -> str:\n"
        "    return use(json.loads(text)) + use(other)\n"
    ),
    "json_with_a_hook_is_not_json": (
        "import json\n\n\ndef f(text: str) -> str:\n"
        "    data = json.loads(text, object_hook=dict)\n    return str(data.get('x'))\n"
    ),
    "json_rebound_is_not_json": (
        "import json\nfrom typing import Any\n\n\ndef f(text: str, other: Any) -> str:\n"
        "    data = json.loads(text)\n    data = other\n    return str(data.get('x'))\n"
    ),
    "json_escaping_function": (
        "import json\nfrom typing import Any\n\n\ndef use(record: dict[str, Any]) -> str:\n"
        "    return str(record.get('x'))\n\n\nHANDLERS = [use]\n\n\ndef main(text: str) -> str:\n"
        "    return use(json.loads(text))\n"
    ),
    "argparse_typed": (
        "import argparse\nfrom pathlib import Path\n\n\ndef main() -> str:\n"
        "    parser = argparse.ArgumentParser()\n"
        "    parser.add_argument('run_dir', type=Path)\n"
        "    args = parser.parse_args()\n    return args.run_dir.read_text()\n"
    ),
    "argparse_escaping_parser": (
        "import argparse\nfrom pathlib import Path\n\n\n"
        "def extend(p: argparse.ArgumentParser) -> None:\n"
        "    p.add_argument('--other')\n\n\ndef main() -> str:\n"
        "    parser = argparse.ArgumentParser()\n"
        "    parser.add_argument('run_dir', type=Path)\n    extend(parser)\n"
        "    args = parser.parse_args()\n    return args.run_dir.read_text()\n"
    ),
    "argparse_custom_type": (
        "import argparse\nfrom pathlib import Path\n\n\ndef as_path(text: str) -> Path:\n"
        "    return Path(text)\n\n\ndef main() -> str:\n"
        "    parser = argparse.ArgumentParser()\n"
        "    parser.add_argument('run_dir', type=as_path)\n"
        "    args = parser.parse_args()\n    return args.run_dir.read_text()\n"
    ),
    "argparse_nargs": (
        "import argparse\nfrom pathlib import Path\n\n\ndef main() -> str:\n"
        "    parser = argparse.ArgumentParser()\n"
        "    parser.add_argument('run_dir', type=Path, nargs='?')\n"
        "    args = parser.parse_args()\n    return args.run_dir.read_text()\n"
    ),
    "argparse_args_rebound": (
        "import argparse\nfrom pathlib import Path\nfrom typing import Any\n\n\n"
        "def main(other: Any) -> str:\n"
        "    parser = argparse.ArgumentParser()\n"
        "    parser.add_argument('run_dir', type=Path)\n"
        "    args = parser.parse_args()\n    args = other\n    return args.run_dir.read_text()\n"
    ),
    "argparse_unknown_dest": (
        "import argparse\nfrom pathlib import Path\n\n\ndef main() -> str:\n"
        "    parser = argparse.ArgumentParser()\n"
        "    parser.add_argument('run_dir', type=Path)\n"
        "    args = parser.parse_args()\n    return args.other_dir.read_text()\n"
    ),
    "json_parse_float_is_not_json": (
        "import json\n\n\ndef f(text: str) -> str:\n"
        "    data = json.loads(text, parse_float=str)\n    return str(data.get('x'))\n"
    ),
    "json_overridden_method_is_not_json": (
        "import json\nfrom typing import Any\n\n\nclass Base:\n"
        "    def load(self, text: str) -> Any:\n        return json.loads(text)\n\n\n"
        "class Other(Base):\n    def load(self, text: str) -> Any:\n        return object()\n\n\n"
        "def f(reader: Base, text: str) -> str:\n    return str(reader.load(text).get('x'))\n"
    ),
    "json_param_of_unexecuted_function_is_not_json": (
        "import json\nfrom typing import Any\n\n\ndef unused(record: dict[str, Any]) -> str:\n"
        "    return str(record.get('x'))\n"
    ),
    # Review round 1 (Sol B2, DeepSeek B2): a JSON container is mutable, so
    # anything stored into it is no longer JSON. Each must leave `<Any>` sites.
    "json_contaminated_by_a_store": (
        "import json\nfrom typing import Any\n\n\ndef f(text: str, other: Any) -> str:\n"
        "    data = json.loads(text)\n    data['value'] = other\n"
        "    return str(data['value'].read_text())\n"
    ),
    "json_contaminated_dict_item": (
        "import json\nfrom typing import Any\n\n\ndef f(text: str, other: Any) -> None:\n"
        "    data = json.loads(text)\n    data['x'] = other\n    data['x'].danger()\n"
    ),
    "json_contaminated_by_append": (
        "import json\nfrom typing import Any\n\n\ndef f(text: str, other: Any) -> None:\n"
        "    data = json.loads(text)\n    data.append(other)\n    data[0].danger()\n"
    ),
    "json_contaminated_nested": (
        "import json\nfrom typing import Any\n\n\ndef f(text: str, other: Any) -> None:\n"
        "    data = json.loads(text)\n    data['k']['x'] = other\n    data['k']['x'].danger()\n"
    ),
    "json_contaminated_through_an_alias": (
        "import json\nfrom typing import Any\n\n\ndef f(text: str, other: Any) -> None:\n"
        "    data = json.loads(text)\n    inner = data['k']\n    inner['x'] = other\n"
        "    data['k']['x'].danger()\n"
    ),
    "json_contaminated_by_a_helper": (
        "import json\nfrom typing import Any\n\n\ndef put(record: Any, value: Any) -> None:\n"
        "    record['x'] = value\n\n\ndef f(text: str, other: Any) -> None:\n"
        "    data = json.loads(text)\n    put(data, other)\n    data['x'].danger()\n"
    ),
    "json_contaminated_through_a_returned_alias": (
        "import json\nfrom typing import Any\n\n\ndef pick(record: Any) -> Any:\n"
        "    return record['k']\n\n\ndef f(text: str, other: Any) -> None:\n"
        "    data = json.loads(text)\n    pick(data)['x'] = other\n    data['k']['x'].danger()\n"
    ),
    "json_contaminated_by_setdefault": (
        "import json\nfrom typing import Any\n\n\ndef f(text: str, other: Any) -> None:\n"
        "    data = json.loads(text)\n    data.setdefault('x', other).danger()\n"
    ),
    "json_contaminated_by_update": (
        "import json\nfrom typing import Any\n\n\ndef f(text: str, other: Any) -> None:\n"
        "    data = json.loads(text)\n    data.update({'x': other})\n    data['x'].danger()\n"
    ),
    "json_contaminated_by_a_closure": (
        "import json\nfrom typing import Any\n\n\ndef f(text: str, other: Any) -> None:\n"
        "    data = json.loads(text)\n\n    def g() -> None:\n        data['x'] = other\n\n"
        "    g()\n    data['x'].danger()\n"
    ),
    "json_contaminated_through_a_copy": (
        "import json\nfrom typing import Any\n\n\ndef f(text: str, other: Any) -> None:\n"
        "    data = json.loads(text)\n    rows = list(data)\n    rows[0]['x'] = other\n"
        "    data[0]['x'].danger()\n"
    ),
    "json_contaminated_by_a_get_default": (
        "import json\nfrom typing import Any\n\n\ndef f(text: str, other: Any) -> None:\n"
        "    data = json.loads(text)\n    data.get('x', other).danger()\n"
    ),
    "json_contaminated_through_a_display": (
        "import json\nfrom typing import Any\n\n\ndef f(text: str, other: Any) -> None:\n"
        "    data = json.loads(text)\n    rows = [data['k']]\n    rows[0]['x'] = other\n"
        "    data['k']['x'].danger()\n"
    ),
    "json_contaminated_through_a_comprehension": (
        "import json\nfrom typing import Any\n\n\ndef f(text: str, other: Any) -> None:\n"
        "    data = json.loads(text)\n    rows = [row for row in data]\n"
        "    rows[0]['x'] = other\n    data[0]['x'].danger()\n"
    ),
    "json_fresh_literal_filled_is_not_json": (
        "from typing import Any\n\n\ndef f(other: Any) -> None:\n"
        "    rows = []\n    rows.append(other)\n    rows[0].danger()\n"
    ),
    "json_contaminated_by_sort": (
        "import json\nfrom typing import Any\n\n\ndef f(text: str, other: Any) -> None:\n"
        "    data = json.loads(text)\n    data['rows'].sort(key=other)\n"
        "    data['rows'][0].danger()\n"
    ),
    "json_at_module_level_is_not_json": (
        "import json\n\nCONFIG = json.loads('{}')\n\n\ndef f() -> None:\n"
        "    CONFIG['x'].danger()\n"
    ),
    "json_read_only": (
        "import json\n\n\ndef f(text: str) -> int:\n    data = json.loads(text)\n"
        "    total = 0\n    for key, value in data.items():\n"
        "        if key.startswith('a') and value.get('n'):\n"
        "            total += len(value['items'])\n"
        "    name = data.get('name', '')\n    return total + len(name.strip())\n"
    ),
    "argparse_dest": (
        "import argparse\nfrom pathlib import Path\n\n\ndef main() -> str:\n"
        "    parser = argparse.ArgumentParser()\n"
        "    parser.add_argument('--run', dest='run_dir', type=Path)\n"
        "    args = parser.parse_args()\n    return args.run_dir.read_text()\n"
    ),
}


@pytest.fixture(scope="module")
def scanned(tmp_path_factory: pytest.TempPathFactory) -> tuple[Any, list[Any]]:
    root = tmp_path_factory.mktemp("probes")
    directory = root / "scripts" / "probes"
    directory.mkdir(parents=True)
    for name, source in {**FAILING_PROBES, **UNREACHABLE_PROBES, **TYPED_PROBES}.items():
        (directory / f"{name}.py").write_text(source, encoding="utf-8")
    paths = [f"scripts/probes/{name}.py" for name in
             {**FAILING_PROBES, **UNREACHABLE_PROBES, **TYPED_PROBES}]
    analysis = scan.analyse(root, paths, "3.13")
    assert scan.assert_complete(analysis) == []
    return analysis, scan.scan_all(analysis)


def _statuses(sites: list[Any], probe: str, lists: Any | None = None) -> list[Any]:
    lists = lists or scan.Lists.load()
    return [c for c in scan.classify(sites, lists) if c.site.module.endswith(f"/{probe}.py")]


@pytest.mark.parametrize("probe", sorted(FAILING_PROBES))
def test_each_planted_probe_fails_the_scan(scanned: tuple[Any, list[Any]], probe: str) -> None:
    _analysis, sites = scanned
    failing = [c for c in _statuses(sites, probe) if c.status in scan.FAILING]
    assert failing, f"the scan let {probe} through: {[c.site for c in _statuses(sites, probe)]}"


@pytest.mark.parametrize("probe", sorted(UNREACHABLE_PROBES))
def test_a_false_guarded_windll_is_platform_unreachable(
    scanned: tuple[Any, list[Any]], probe: str
) -> None:
    _analysis, sites = scanned
    statuses = _statuses(sites, probe)
    assert not [c for c in statuses if c.status in scan.FAILING], statuses
    unreachable = [c for c in statuses if c.status == "platform-unreachable"]
    assert unreachable and all("win32" in (c.site.guard or "") for c in unreachable)


def test_the_refusals_are_refusals_not_missing_declarations(
    scanned: tuple[Any, list[Any]],
) -> None:
    """A read of sys.orig_argv, a write to sys.meta_path or to builtins is refused outright:
    no declaration makes it pass."""
    _analysis, sites = scanned
    for probe in ("orig_argv", "meta_path", "builtins_write"):
        assert any(c.status == "refused" for c in _statuses(sites, probe)), probe


def test_a_declaration_discharges_exactly_what_it_counts(scanned: tuple[Any, list[Any]]) -> None:
    analysis, sites = scanned
    base = scan.Lists.load()
    module = "scripts/probes/shelve_open.py"
    failing = [c.site for c in _statuses(sites, "shelve_open") if c.status in scan.FAILING]
    counts: dict[str, int] = {}
    for site in failing:
        counts[site.target] = counts.get(site.target, 0) + 1
    group = {"module": module, "scopes": "*", "kind": "file-read",
             "reaches": {"outside_tree": "a shelf path the caller passes"},
             "reason": "test", "targets": counts}
    lists = scan.Lists(base.inert_targets, base.inert_types, {}, "x", [group])
    assert not [c for c in _statuses(sites, "shelve_open", lists) if c.status in scan.FAILING]
    assert scan.unused_declarations(sites, lists, [module]) == []
    wrong = {**group, "targets": {t: n + 1 for t, n in counts.items()}}
    lists = scan.Lists(base.inert_targets, base.inert_types, {}, "x", [wrong])
    assert scan.unused_declarations(sites, lists, [module]), "an over-count must be reported"


def test_a_sys_path_change_needs_a_declaration(scanned: tuple[Any, list[Any]]) -> None:
    _analysis, sites = scanned
    statuses = _statuses(sites, "sys_path_insert")
    assert any(c.site.target == "sys.path" and c.status == "unresolvable" for c in statuses)


def test_implicit_protocol_methods_are_resolved(scanned: tuple[Any, list[Any]]) -> None:
    """`with` runs __enter__/__exit__ and `for` runs __iter__/__next__ on the operand's type."""
    _analysis, sites = scanned
    with_open = [s for s in sites if s.module.endswith("/with_open.py") and s.kind == "implicit"]
    assert any(s.target.endswith(".__enter__") for s in with_open)
    assert any(s.target.endswith(".__exit__") for s in with_open)
    csv_sites = [s for s in sites if s.module.endswith("/csv_iteration.py")]
    assert any(s.kind == "implicit" and s.target.endswith(".__next__") for s in csv_sites)


def _json_and_any(sites: list[Any], probe: str) -> tuple[set[str], set[str]]:
    targets = [s.target for s in sites if s.module.endswith(f"/{probe}.py")]
    json_sites = {t for t in targets if t.startswith("<json>")}
    return json_sites, {t for t in targets if t.startswith("<Any>")}


@pytest.mark.parametrize("probe", ["json_local", "json_through_helpers", "json_read_only"])
def test_json_values_are_proved(scanned: tuple[Any, list[Any]], probe: str) -> None:
    json_sites, any_sites = _json_and_any(scanned[1], probe)
    assert json_sites and not any_sites, (json_sites, any_sites)


JSON_NEGATIVES = [
    "json_refuted_by_a_caller",
    "json_with_a_hook_is_not_json",
    "json_rebound_is_not_json",
    "json_escaping_function",
    "json_parse_float_is_not_json",
    "json_overridden_method_is_not_json",
    "json_param_of_unexecuted_function_is_not_json",
    "json_contaminated_by_a_store",
    "json_contaminated_dict_item",
    "json_contaminated_by_append",
    "json_contaminated_nested",
    "json_contaminated_through_an_alias",
    "json_contaminated_by_a_helper",
    "json_contaminated_through_a_returned_alias",
    "json_contaminated_by_setdefault",
    "json_contaminated_by_update",
    "json_contaminated_by_a_closure",
    "json_contaminated_through_a_copy",
    "json_contaminated_by_a_get_default",
    "json_at_module_level_is_not_json",
    "json_contaminated_through_a_display",
    "json_contaminated_through_a_comprehension",
    "json_fresh_literal_filled_is_not_json",
    "json_contaminated_by_sort",
]


def _fails(sites: list[Any], probe: str, prefix: str) -> list[Any]:
    """The probe's unproved sites, as the scan classifies them: each must fail."""
    statuses = [c for c in _statuses(sites, probe) if c.site.target.startswith(prefix)]
    assert statuses, f"{probe}: no {prefix} site at all"
    return [c for c in statuses if c.status not in scan.FAILING]


@pytest.mark.parametrize("probe", JSON_NEGATIVES)
def test_json_is_not_assumed_where_it_is_not_proved(
    scanned: tuple[Any, list[Any]], probe: str
) -> None:
    """An Any value the JSON proof cannot prove stays Any, and the scan fails on it."""
    _json_sites, any_sites = _json_and_any(scanned[1], probe)
    assert any_sites, f"{probe}: an unproved value was treated as JSON"
    assert _fails(scanned[1], probe, "<Any>") == [], f"{probe}: an unproved Any site passed"


#: The types inert as a whole. Round 1 reviewed a list of 31; round 2 (Sol B2)
#: found that `list.remove` and `Pattern.sub` run other objects' code, so every
#: type but `<json>` is now listed method by method (tests/test_plan036_protocols.py).
PURE_TYPES = frozenset({"<json>"})


def test_type_level_inert_entries_are_only_the_reviewed_pure_types() -> None:
    types = set(scan.Lists.load().inert_types)
    assert types <= PURE_TYPES, f"type-level entries off the reviewed list: {types - PURE_TYPES}"
    for impure in ("zipfile.ZipFile", "os.DirEntry", "_io._IOBase", "_io._BufferedIOBase",
                   "sqlite3.Cursor", "tempfile.TemporaryDirectory", "typing.MutableMapping"):
        assert impure not in types


def test_the_producer_rule_covers_only_read_only_protocols() -> None:
    """Protocol dunders on an abstract receiver pass only for read-only protocols."""
    covered = scan._PROTOCOL_DUNDERS_COVERED_AT_PRODUCER
    for mutable in ("typing.MutableMapping", "typing.MutableSequence", "typing.MutableSet",
                    "typing.ContextManager", "contextlib.AbstractContextManager"):
        assert mutable not in covered


def test_a_mixin_method_is_named_after_the_concrete_receiver(
    scanned: tuple[Any, list[Any]],
) -> None:
    """`dict.update` is typeshed's `MutableMapping.update`; it is classified as dict's."""
    targets = {s.target for s in scanned[1] if s.module.endswith("/dict_update.py")}
    assert {"builtins.dict.update", "builtins.dict.setdefault"} <= targets, targets
    assert not any(t.startswith("typing.MutableMapping") for t in targets), targets


def test_argparse_values_get_the_type_argparse_guarantees(scanned: tuple[Any, list[Any]]) -> None:
    targets = {s.target for s in scanned[1] if s.module.endswith("/argparse_typed.py")}
    assert "pathlib.Path.read_text" in targets


ARGPARSE_NEGATIVES = [
    "argparse_escaping_parser",
    "argparse_dest",
    "argparse_custom_type",
    "argparse_nargs",
    "argparse_args_rebound",
    "argparse_unknown_dest",
]


@pytest.mark.parametrize("probe", ARGPARSE_NEGATIVES)
def test_argparse_values_stay_any_when_the_parser_is_not_fully_visible(
    scanned: tuple[Any, list[Any]], probe: str
) -> None:
    """A Namespace attribute the plugin cannot type stays Any, and the scan fails on it."""
    targets = {s.target for s in scanned[1] if s.module.endswith(f"/{probe}.py")}
    assert "<Any>.read_text" in targets and "pathlib.Path.read_text" not in targets
    assert _fails(scanned[1], probe, "<Any>") == [], f"{probe}: an untyped Any site passed"


def test_completeness_refuses_a_module_hidden_by_a_file_level_ignore(tmp_path: Path) -> None:
    directory = tmp_path / "scripts" / "probes"
    directory.mkdir(parents=True)
    (directory / "hidden.py").write_text(
        "# type: ignore\nimport shelve\n\n\ndef f(p: str) -> None:\n    shelve.open(p)\n",
        encoding="utf-8",
    )
    analysis = scan.analyse(tmp_path, ["scripts/probes/hidden.py"], "3.13")
    assert any("type: ignore" in p for p in scan.assert_complete(analysis))


def test_a_module_that_does_not_parse_is_refused(tmp_path: Path) -> None:
    directory = tmp_path / "scripts" / "probes"
    directory.mkdir(parents=True)
    (directory / "broken.py").write_text("def f(:\n", encoding="utf-8")
    with pytest.raises(repo.Wp0Error):
        scan.analyse(tmp_path, ["scripts/probes/broken.py"], "3.13")


def test_the_reviewed_lists_load_and_refuse_overlap(tmp_path: Path) -> None:
    lists = scan.Lists.load()
    assert lists.inert_targets and lists.inert_types and lists.groups
    for name in ("inert.json", "edges.json", "exemptions.json", "mypy-scan.ini"):
        (tmp_path / name).write_bytes((repo.LISTS_DIR / name).read_bytes())
    inert = json.loads((tmp_path / "inert.json").read_text(encoding="utf-8"))
    edges = json.loads((tmp_path / "edges.json").read_text(encoding="utf-8"))
    inert["targets"][0]["names"].append(next(iter(edges["groups"][0]["targets"])))
    (tmp_path / "inert.json").write_text(json.dumps(inert), encoding="utf-8")
    with pytest.raises(repo.Wp0Error, match="both inert and declared"):
        scan.Lists.load(tmp_path)


def test_a_declaration_must_say_what_it_reaches(tmp_path: Path) -> None:
    for name in ("inert.json", "edges.json"):
        (tmp_path / name).write_bytes((repo.LISTS_DIR / name).read_bytes())
    edges = json.loads((tmp_path / "edges.json").read_text(encoding="utf-8"))
    edges["groups"][0]["reaches"] = {}
    (tmp_path / "edges.json").write_text(json.dumps(edges), encoding="utf-8")
    with pytest.raises(repo.Wp0Error, match="what it reaches"):
        scan.Lists.load(tmp_path)
