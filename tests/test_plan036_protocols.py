"""Plan 036 WP-0: in-memory methods that run other objects' code (review round 2).

Sol B2: whole-type inert entries covered `list.remove` (which runs its elements'
`__eq__`) and `Pattern.sub` (which calls a callable replacement), so an `Any`
there ran code no scan site named. Since round 2 only `<json>` is inert as a
whole; every other type is listed method by method, and a listed method that
runs code of its arguments, elements or callables is marked
`runs_code_of_others` in inert.json and has a rule in `scan.METHOD_PROTOCOLS`
(or the older iteration and conversion rules). Each such method has a negative
probe here: called with an `Any` where it runs code, it must leave a failing
site that names it.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import Any

import pytest
from plan036_support import PACKAGE, load

scan = load("scan")

PARAMETERS = (
    "a: Any, s: str, b: bytes, xs: list[Any], ls: list[str], ts: tuple[Any, ...], "
    "tt: tuple[str, ...], d: dict[Any, Any], ds: dict[str, int], st: set[Any], "
    "ss: set[str], fs: frozenset[Any], fss: frozenset[str], ba: bytearray, p: PurePath, "
    "pat: re.Pattern[str], m: re.Match[str], e: ET.Element, c: Counter[str]"
)
HEADER = (
    "import base64\nimport copy\nimport dataclasses\nimport difflib\nimport hashlib\n"
    "import json\nimport re\nimport struct\nfrom collections import Counter\n"
    "from pathlib import PurePath\nfrom typing import Any\n"
    "from xml.etree import ElementTree as ET\n"
)

#: target -> a statement that runs that target with an `Any` where it runs code.
PROBES = {
    "_hashlib.HASH.update": "hashlib.sha256().update(a)",
    "builtins.BaseException.__init__": "ValueError(a)",
    "builtins.bytearray.extend": "ba.extend(a)",
    "builtins.bytes.__add__": "b.__add__(a)",
    "builtins.bytes.__contains__": "b.__contains__(a)",
    "builtins.bytes.__getitem__": "b.__getitem__(a)",
    "builtins.bytes.count": "b.count(a)",
    "builtins.bytes.find": "b.find(a)",
    "builtins.bytes.replace": "b.replace(a, b)",
    "builtins.bytes.startswith": "b.startswith(a)",
    "builtins.dict.__delitem__": "d.__delitem__(s)",
    "builtins.dict.__eq__": "ds.__eq__(d)",
    "builtins.dict.__getitem__": "ds.__getitem__(a)",
    "builtins.dict.__ior__": "ds.__ior__(a)",
    "builtins.dict.__or__": "ds.__or__(a)",
    "builtins.dict.__ror__": "ds.__ror__(a)",
    "builtins.dict.__setitem__": "ds.__setitem__(a, 1)",
    "builtins.dict.fromkeys": "dict.fromkeys(a)",
    "builtins.dict.get": "ds.get(a)",
    "builtins.dict.pop": "ds.pop(a)",
    "builtins.dict.setdefault": "ds.setdefault(a, 1)",
    "builtins.dict.update": "ds.update(a)",
    "builtins.frozenset.__contains__": "fss.__contains__(a)",
    "builtins.frozenset.__eq__": "fss.__eq__(fs)",
    "builtins.frozenset.__ge__": "fss.__ge__(fs)",
    "builtins.frozenset.__le__": "fss.__le__(fs)",
    "builtins.frozenset.__or__": "fss.__or__(fs)",
    "builtins.frozenset.__sub__": "fss.__sub__(fs)",
    "builtins.list.__contains__": "ls.__contains__(a)",
    "builtins.list.__eq__": "ls.__eq__(xs)",
    "builtins.list.__getitem__": "ls.__getitem__(a)",
    "builtins.list.__iadd__": "ls.__iadd__(a)",
    "builtins.list.__mul__": "ls.__mul__(a)",
    "builtins.list.__setitem__": "ls.__setitem__(a, s)",
    "builtins.list.count": "ls.count(a)",
    "builtins.list.extend": "ls.extend(a)",
    "builtins.list.index": "ls.index(a)",
    "builtins.list.insert": "ls.insert(a, s)",
    "builtins.list.pop": "ls.pop(a)",
    "builtins.list.remove": "xs.remove(s)",
    "builtins.list.sort": "ls.sort(key=a)",
    "builtins.set.__contains__": "ss.__contains__(a)",
    "builtins.set.__eq__": "ss.__eq__(st)",
    "builtins.set.__ge__": "ss.__ge__(st)",
    "builtins.set.__ior__": "ss.__ior__(st)",
    "builtins.set.__le__": "ss.__le__(st)",
    "builtins.set.__or__": "ss.__or__(st)",
    "builtins.set.__sub__": "ss.__sub__(st)",
    "builtins.set.__xor__": "ss.__xor__(st)",
    "builtins.set.add": "ss.add(a)",
    "builtins.set.issubset": "ss.issubset(a)",
    "builtins.set.update": "ss.update(a)",
    "builtins.str.__getitem__": "s.__getitem__(a)",
    "builtins.str.__mul__": "s.__mul__(a)",
    "builtins.str.format": "s.format(x=a)",
    "builtins.str.join": "s.join(a)",
    "builtins.tuple.__contains__": "tt.__contains__(a)",
    "builtins.tuple.__eq__": "tt.__eq__(ts)",
    "builtins.tuple.__getitem__": "tt.__getitem__(a)",
    "builtins.tuple.__hash__": "ts.__hash__()",
    "builtins.tuple.__lt__": "tt.__lt__(ts)",
    "builtins.tuple.index": "tt.index(a)",
    "collections.Counter.__sub__": "c.__sub__(a)",
    "pathlib.PurePath.__rtruediv__": "p.__rtruediv__(a)",
    "pathlib.PurePath.__truediv__": "p.__truediv__(a)",
    "pathlib.PurePath.joinpath": "p.joinpath(a)",
    "pathlib.PurePath.relative_to": "p.relative_to(a)",
    "pathlib.PurePath.with_name": "p.with_name(a)",
    "re.Match.end": "m.end(a)",
    "re.Match.group": "m.group(a)",
    "re.Match.start": "m.start(a)",
    "re.Pattern.finditer": "pat.finditer(a)",
    "re.Pattern.fullmatch": "pat.fullmatch(a)",
    "re.Pattern.match": "pat.match(a)",
    "re.Pattern.search": "pat.search(a)",
    "re.Pattern.sub": "pat.sub(a, s)",
    "xml.etree.ElementTree.Element.__getitem__": "e.__getitem__(a)",
    "xml.etree.ElementTree.Element.extend": "e.extend(a)",
    "xml.etree.ElementTree.Element.find": "e.find(a)",
    "xml.etree.ElementTree.Element.findall": "e.findall(a)",
    "xml.etree.ElementTree.Element.findtext": "e.findtext(a)",
    "xml.etree.ElementTree.Element.get": "e.get(a)",
    "xml.etree.ElementTree.Element.insert": "e.insert(a, e)",
    "xml.etree.ElementTree.Element.iter": "e.iter(a)",
    "xml.etree.ElementTree.Element.set": "e.set('k', a)",
    "xml.etree.ElementTree.TreeBuilder.__init__": "ET.TreeBuilder(element_factory=a)",
    "builtins.map": "map(a, ls)",
    "builtins.max": "max(ls, key=a)",
    "builtins.sorted": "sorted(ls, key=a)",
    "copy.deepcopy": "copy.deepcopy(a)",
    "dataclasses.asdict": "dataclasses.asdict(a)",
    "dataclasses.replace": "dataclasses.replace(a)",
    "_struct.pack": "struct.pack('i', a)",
    "_struct.unpack": "struct.unpack('i', a)",
    "_struct.unpack_from": "struct.unpack_from('i', a)",
    "base64.b64decode": "base64.b64decode(a)",
    "base64.b64encode": "base64.b64encode(a)",
    "difflib.unified_diff": "difflib.unified_diff(a, ls)",
    "json.dumps": "json.dumps(ds, default=a)",
}

#: Sol's round-2 probes (`probe_scan_round2.py`): each must fail the scan.
SOL_ROUND2 = {
    "list_remove_any": "from typing import Any\n\n\ndef f(rows: list[Any], value: Any) -> None:\n"
    "    rows.remove(value)\n",
    "regex_sub_any": "import re\nfrom typing import Any\n\n\n"
    "def f(pattern: re.Pattern[str], replacement: Any, text: str) -> str:\n"
    "    return pattern.sub(replacement, text)\n",
    "treebuilder_factory_any": "from typing import Any\n"
    "from xml.etree import ElementTree as ET\n\n\n"
    "def f(factory: Any) -> None:\n    builder = ET.TreeBuilder(element_factory=factory)\n"
    "    builder.start('x', {})\n",
    "list_extend_any": "from typing import Any\n\n\ndef f(rows: list[int], source: Any) -> None:\n"
    "    rows.extend(source)\n",
    "dict_update_any": "from typing import Any\n\n\n"
    "def f(rows: dict[str, int], source: Any) -> None:\n    rows.update(source)\n",
    "str_format_map_any": "from typing import Any\n\n\n"
    "def f(template: str, source: Any) -> str:\n    return template.format_map(source)\n",
    "list_sort_any": "from typing import Any\n\n\ndef f(rows: list[Any], key: Any) -> None:\n"
    "    rows.sort(key=key)\n",
    "json_get_any_key": "import json\nfrom typing import Any\n\n\n"
    "def f(text: str, key: Any) -> None:\n    data = json.loads(text)\n    data.get(key)\n",
    "json_contains_any": "import json\nfrom typing import Any\n\n\n"
    "def f(text: str, key: Any) -> bool:\n    data = json.loads(text)\n    return key in data\n",
}


def _module(probes: dict[str, str]) -> str:
    lines = [HEADER]
    for index, statement in enumerate(probes.values()):
        lines.append(f"\n\ndef probe_{index}({PARAMETERS}) -> None:\n    {statement}\n")
    return "".join(lines)


@pytest.fixture(scope="module")
def classified(tmp_path_factory: pytest.TempPathFactory) -> list[Any]:
    root = tmp_path_factory.mktemp("protocols")
    directory = root / "scripts" / "probes"
    directory.mkdir(parents=True)
    (directory / "methods.py").write_text(_module(PROBES), encoding="utf-8")
    for name, source in SOL_ROUND2.items():
        (directory / f"{name}.py").write_text(source, encoding="utf-8")
    paths = ["scripts/probes/methods.py", *(f"scripts/probes/{n}.py" for n in SOL_ROUND2)]
    analysis = scan.analyse(root, paths, "3.14")
    assert scan.assert_complete(analysis) == []
    return list(scan.classify(scan.scan_all(analysis), scan.Lists.load()))


def _inert() -> dict[str, Any]:
    data: dict[str, Any] = json.loads((PACKAGE / "lists" / "inert.json").read_text("utf-8"))
    return data


def _rules() -> set[str]:
    return (set(scan.METHOD_PROTOCOLS) | set(scan._ITERATING) | set(scan._CONVERSIONS)
            | set(scan._ELEMENT_OPS))


def test_only_json_values_are_inert_as_a_whole() -> None:
    assert [entry["type"] for entry in _inert()["types"]] == ["<json>"]


def test_every_listed_method_that_runs_other_code_has_a_rule_and_a_probe() -> None:
    runs = {name for group in _inert()["targets"] if group.get("runs_code_of_others")
            for name in group["names"]}
    assert runs, "inert.json marks no method that runs other objects' code"
    assert runs <= _rules(), sorted(runs - _rules())
    assert runs <= set(PROBES), sorted(runs - set(PROBES))
    listed = {name for group in _inert()["targets"] for name in group["names"]}
    unmarked = sorted((set(scan.METHOD_PROTOCOLS) & listed) - runs - set(PROBES))
    assert not unmarked, f"inert targets with a protocol rule but no probe: {unmarked}"


def test_no_method_listed_as_running_no_other_code_has_a_rule() -> None:
    """A method in a group that says it runs no code of other objects must not be
    one the scan knows runs some (the two lists would contradict each other)."""
    for group in _inert()["targets"]:
        if "methods that run no code of other objects" in group["reason"]:
            clash = sorted(set(group["names"]) & _rules())
            assert not clash, clash


@pytest.mark.parametrize("target", sorted(PROBES))
def test_an_unknown_operand_of_a_code_running_method_fails(
    classified: list[Any], target: str
) -> None:
    index = list(PROBES).index(target)
    rows = [c for c in classified if c.site.module.endswith("/methods.py")
            and c.site.scope == f"probe_{index}"]
    named = target.removesuffix(".__init__")  # a constructor call names the class
    failing = [c for c in rows if c.status in scan.FAILING and named in c.site.detail]
    assert failing, (target, [(c.status, c.site.target, c.site.detail) for c in rows])


@pytest.mark.parametrize("probe", sorted(SOL_ROUND2))
def test_sols_round_2_probes_fail(classified: list[Any], probe: str) -> None:
    rows = [c for c in classified if c.site.module.endswith(f"/{probe}.py")]
    assert any(c.status in scan.FAILING for c in rows), [
        (c.status, c.site.target) for c in rows
    ]


def test_the_probe_module_is_valid_python() -> None:
    compile(textwrap.dedent(_module(PROBES)), "methods.py", "exec")
    assert Path(PACKAGE / "lists" / "inert.json").is_file()
