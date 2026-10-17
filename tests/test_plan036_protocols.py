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
    "pat: re.Pattern[str], m: re.Match[str], e: ET.Element, c: Counter[str], "
    "ap: argparse.ArgumentParser, lg: logging.Logger, mv: memoryview, cb: Callable[..., Any]"
)
HEADER = (
    "import abc\nimport argparse\nimport contextlib\nimport datetime\nimport io\nimport ipaddress\n"
    "import logging\nimport os\nimport pathlib\nimport sqlite3\nimport stat\nimport types\n"
    "import functools\nimport typing\nimport uuid\nimport xml.sax.saxutils\nimport zipfile\n"
    "import base64\nimport copy\nimport dataclasses\nimport difflib\nimport hashlib\n"
    "import json\nimport re\nimport struct\nfrom collections import Counter\n"
    "from pathlib import PurePath\nfrom typing import Any, Callable\n"
    "from xml.etree import ElementTree as ET\n"
)

#: target -> a statement that runs that target with an `Any` where it runs code.
PROBES = {
    "_hashlib.HASH.update": "hashlib.sha256().update(a)",
    "builtins.BaseException.__init__": "BaseException.__init__(ValueError(), a)",
    "builtins.bytearray.extend": "ba.extend(a)",
    "builtins.bytes.__add__": "b.__add__(a)",
    "builtins.bytes.__contains__": "b.__contains__(a)",
    "builtins.bytes.__getitem__": "b.__getitem__(a)",
    "builtins.bytes.count": "b.count(a)",
    "builtins.bytes.find": "b.find(a)",
    "builtins.bytes.replace": "b.replace(a, b)",
    "builtins.bytes.startswith": "b.startswith(a)",
    "builtins.dict.__contains__": "ds.__contains__(a)",
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
    "builtins.list.__lt__": "ls.__lt__(xs)",
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
    "json.loads": "json.loads(s, object_hook=a)",
    # Round 3 audit (DeepSeek B3): every remaining entry that runs an argument's code.
    "_hashlib.openssl_sha256": "hashlib.sha256(a)",
    "_io.BytesIO": "io.BytesIO(a)",
    "_stat.S_ISDIR": "stat.S_ISDIR(a)",
    "_stat.S_ISLNK": "stat.S_ISLNK(a)",
    "_stat.S_ISREG": "stat.S_ISREG(a)",
    "argparse.ArgumentParser": "argparse.ArgumentParser(formatter_class=a)",
    "argparse.ArgumentParser.error": "ap.error(a)",
    "argparse.ArgumentParser.parse_args": "ap.parse_args(a)",
    "argparse._ActionsContainer.add_argument": "ap.add_argument('--x', type=a)",
    "builtins.AttributeError": "AttributeError(a)",
    "builtins.Exception": "Exception(a)",
    "builtins.OSError": "OSError(a)",
    "builtins.SystemExit": "SystemExit(a)",
    "builtins.TypeError": "TypeError(a)",
    "builtins.ValueError": "ValueError(a)",
    "sqlite3.Error": "sqlite3.Error(a)",
    "builtins.all": "all(xs)",
    "builtins.any": "any(xs)",
    "builtins.sum": "sum(xs)",
    "builtins.bool": "bool(a)",
    "builtins.bytearray": "bytearray(a)",
    "builtins.bytes": "bytes(a)",
    "builtins.chr": "chr(a)",
    "builtins.dict": "dict(a)",
    "builtins.enumerate": "enumerate(a)",
    "builtins.float": "float(a)",
    "builtins.frozenset": "frozenset(a)",
    "builtins.int": "int(a)",
    "builtins.iter": "iter(a)",
    "builtins.len": "len(a)",
    "builtins.list": "list(a)",
    "builtins.next": "next(a)",
    "builtins.repr": "repr(a)",
    "builtins.reversed": "reversed(a)",
    "builtins.set": "set(a)",
    "builtins.str": "str(a)",
    "builtins.tuple": "tuple(a)",
    "builtins.zip": "zip(a)",
    "builtins.getattr": "getattr(a, 'x')",
    "builtins.hasattr": "hasattr(a, 'x')",
    "builtins.isinstance": "isinstance(s, a)",
    "builtins.memoryview": "memoryview(a)",
    "builtins.memoryview.__getitem__": "mv.__getitem__(a)",
    "builtins.print": "print(s, file=a)",
    "builtins.range": "range(a)",
    "builtins.type": "type('T', a, {})",
    "collections.Counter": "Counter(a)",
    "contextlib.suppress": "contextlib.suppress(a)",
    "dataclasses.field": "dataclasses.field(default_factory=a)",
    "datetime.datetime": "datetime.datetime(a, 1, 1)",
    "ipaddress.ip_address": "ipaddress.ip_address(a)",
    "ipaddress.ip_network": "ipaddress.ip_network(a)",
    "ipaddress.summarize_address_range": "ipaddress.summarize_address_range(a, a)",
    "logging.Logger.error": "lg.error(s, a)",
    "pathlib.Path": "pathlib.Path(a)",
    "pathlib.PurePosixPath": "pathlib.PurePosixPath(a)",
    "pathlib.PureWindowsPath": "pathlib.PureWindowsPath(a)",
    "posixpath.normcase": "os.path.normcase(a)",
    "re.compile": "re.compile(a)",
    "re.escape": "re.escape(a)",
    "re.fullmatch": "re.fullmatch(s, a)",
    "re.match": "re.match(s, a)",
    "re.search": "re.search(s, a)",
    "types.MappingProxyType": "types.MappingProxyType(a)",
    "uuid.UUID": "uuid.UUID(a)",
    "uuid.uuid5": "uuid.uuid5(uuid.NAMESPACE_URL, a)",
    "xml.etree.ElementTree.Element": "ET.Element('t', a)",
    "xml.etree.ElementTree.SubElement": "ET.SubElement(e, 't', a)",
    "xml.etree.ElementTree.XMLParser": "ET.XMLParser(target=a)",
    "xml.etree.ElementTree.tostring": "ET.tostring(a)",
    "xml.etree.ElementTree.XML": "ET.fromstring(a)",
    # Round 4 (DeepSeek N3): typeshed types these arguments as protocols.
    "builtins.str.count": "s.count(s, a)",
    "builtins.str.endswith": "s.endswith(s, a)",
    "builtins.str.index": "s.index(s, a)",
    "builtins.str.replace": "s.replace(s, s, a)",
    "builtins.str.rsplit": "s.rsplit(s, a)",
    "builtins.str.split": "s.split(s, a)",
    "builtins.str.startswith": "s.startswith(s, a)",
    "builtins.bytes.hex": "b.hex(s, a)",
    "builtins.bytes.fromhex": "bytes.fromhex(a)",
    "xml.etree.ElementTree.TreeBuilder.start": "ET.TreeBuilder().start(a, {})",
    "xml.sax.saxutils.quoteattr": "xml.sax.saxutils.quoteattr(a)",
    "zipfile.ZipInfo": "zipfile.ZipInfo(a)",
    # Round 4 audit (Sol B4): attribute protocol and __repr__ of an argument.
    "dataclasses.fields": "dataclasses.fields(a)",
    "typing.get_args": "typing.get_args(a)",
    "typing.assert_never": "typing.assert_never(a)",
    "functools.wraps": "functools.wraps(cb)",
    # Round 5 (Sol B2): consumers a runtime probe showed running caller code.
    "builtins.property": "property(cb)",
    "builtins.staticmethod": "staticmethod(cb)",
    "contextlib.contextmanager": "contextlib.contextmanager(cb)",
    "abc.ABCMeta": "abc.ABCMeta('M', (), d)",
    "uuid.UUID.__eq__": "uuid.UUID(int=1).__eq__(a)",
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


_H = "import functools\nimport json\nimport re\nfrom typing import Any, Callable\n\n\n"
#: Sol's round-3 probes (`probe_protocol_alias_round3.py`) and variants: a callable
#: that runs other objects' code, reached through a value, or a callback the scan
#: cannot see. Each must fail the scan.
SOL_ROUND3 = {
    "r3_bound_variable": _H + "def f(rows: list[Any], value: Any) -> None:\n"
    "    remove = rows.remove\n    remove(value)\n",
    "r3_unbound_variable": _H + "def f(rows: list[Any], value: Any) -> None:\n"
    "    remove: Callable[[list[Any], Any], None] = list.remove\n    remove(rows, value)\n",
    "r3_partial_bound": _H + "def f(rows: list[Any], value: Any) -> None:\n"
    "    remove = functools.partial(rows.remove, value)\n    remove()\n",
    "r3_getattr_literal": _H + "def f(rows: list[str], value: str) -> None:\n"
    "    getattr(rows, 'remove')(value)\n",
    "r3_regex_bound_variable": _H + "def f(p: re.Pattern[str], r: Any, t: str) -> str:\n"
    "    substitute = p.sub\n    return substitute(r, t)\n",
    "r3_sorted_alias": _H + "def f(values: list[str], cb: Callable[[str], Any]) -> None:\n"
    "    order = sorted\n    order(values, key=cb)\n",
    "r3_lambda_wrapper": _H + "def f(rows: list[Any], value: Any) -> None:\n"
    "    g = lambda v: rows.remove(v)\n    g(value)\n",
    "r3_class_alias": _H + "def f(value: Any) -> int:\n    convert = int\n"
    "    return convert(value)\n",
    "r3_map_conversion": _H + "def f(rows: list[Any]) -> list[str]:\n"
    "    return list(map(str, rows))\n",
    "r3_key_len": _H + "def f(rows: list[Any]) -> Any:\n    return max(rows, key=len)\n",
    "r3_callback_parameter": _H + "def f(rows: list[str], cb: Callable[[str], int]) -> None:\n"
    "    rows.sort(key=cb)\n",
    "r3_json_value_method": _H + "def f(text: str, key: Any) -> None:\n"
    "    data = json.loads(text)\n    get = data.get\n    get(key)\n",
    "r3_super_exception": _H + "class Failure(Exception):\n"
    "    def __init__(self, value: Any) -> None:\n        super().__init__(value)\n",
    **{
        f"r3_json_{hook}": _H + f"def f(text: str, cb: Callable[..., Any]) -> None:\n"
        f"    json.loads(text, {hook}=cb)\n"
        for hook in ("object_hook", "object_pairs_hook", "parse_float", "parse_int",
                     "parse_constant")
    },
    "r3_json_load_hook": _H + "def f(stream: Any, cb: Callable[..., Any]) -> None:\n"
    "    json.load(stream, object_hook=cb)\n",
}
#: DeepSeek's round-3 probes (`.review/tmp/probes/`, review round 3): each must fail.
DS_ROUND3 = {
    'ds_alias_bytes_replace': (
        'from typing import Any\n'
        '\n'
        '\n'
        'def f(b: bytes, o: Any) -> bytes:\n'
        '    g = b.replace\n'
        '    return g(b, o)\n'
    ),
    'ds_alias_dumps': (
        'import json\n'
        'from typing import Any\n'
        '\n'
        '\n'
        'def f(ds: dict[str, int], default: Any) -> str:\n'
        '    g = json.dumps\n'
        '    return g(ds, default=default)\n'
    ),
    'ds_alias_remove': (
        'from typing import Any\n'
        '\n'
        '\n'
        'def f(xs: list[Any], s: Any) -> None:\n'
        '    g = list.remove\n'
        '    g(xs, s)\n'
    ),
    'ds_alias_sorted': (
        'from typing import Any\n'
        '\n'
        '\n'
        'def f(rows: list[str], key: Any) -> None:\n'
        '    g = sorted\n'
        '    g(rows, key=key)\n'
    ),
    'ds_bound_remove': (
        'from typing import Any\n'
        '\n'
        '\n'
        'def f(xs: list[Any], s: Any) -> None:\n'
        '    m = xs.remove\n'
        '    m(s)\n'
    ),
    'ds_bound_sort': (
        'from typing import Any\n'
        '\n'
        '\n'
        'def f(rows: list[str], key: Any) -> None:\n'
        '    m = rows.sort\n'
        '    m(key=key)\n'
    ),
    'ds_bound_str_format': (
        'from typing import Any\n'
        '\n'
        '\n'
        'def f(s: str, x: Any) -> str:\n'
        '    m = s.format\n'
        '    return m(x=x)\n'
    ),
    'ds_bound_sub': (
        'import re\n'
        'from typing import Any\n'
        '\n'
        '\n'
        'def f(pat: re.Pattern[str], repl: Any, text: str) -> str:\n'
        '    m = pat.sub\n'
        '    return m(repl, text)\n'
    ),
    'ds_dict_get_bound': (
        'from typing import Any\n'
        '\n'
        '\n'
        'def f(rows: dict[str, int], key: Any) -> Any:\n'
        '    m = rows.get\n'
        '    return m(key)\n'
    ),
    'ds_partial_remove': (
        'from functools import partial\n'
        'from typing import Any\n'
        '\n'
        '\n'
        'def f(xs: list[Any], s: Any) -> None:\n'
        '    p = partial(list.remove, xs)\n'
        '    p(s)\n'
    ),
    'ds_partial_sorted': (
        'from functools import partial\n'
        'from typing import Any\n'
        '\n'
        '\n'
        'def f(rows: list[str], key: Any) -> list[str]:\n'
        '    p = partial(sorted, rows)\n'
        '    return p(key=key)\n'
    ),
    'ds_method_via_getattr_literal': (
        'from typing import Any\n'
        '\n'
        '\n'
        'def f(xs: list[Any], s: Any) -> None:\n'
        '    getattr(xs, "remove")(s)\n'
    ),
    'ds_all_any': (
        'from typing import Any\n'
        '\n'
        '\n'
        'def f(xs: list[Any]) -> bool:\n'
        '    return all(xs)\n'
    ),
    'ds_any_any': (
        'from typing import Any\n'
        '\n'
        '\n'
        'def f(xs: list[Any]) -> bool:\n'
        '    return any(xs)\n'
    ),
    'ds_sum_any': (
        'from typing import Any\n'
        '\n'
        '\n'
        'def f(xs: list[Any]) -> Any:\n'
        '    return sum(xs)\n'
    ),
    'ds_memoryview_ctor': (
        'from typing import Any\n'
        '\n'
        '\n'
        'def f(x: Any) -> memoryview:\n'
        '    return memoryview(x)\n'
    ),
    'ds_memoryview_getitem': (
        'from typing import Any\n'
        '\n'
        '\n'
        'def f(mv: memoryview, i: Any) -> int:\n'
        '    return mv[i]\n'
    ),
    'ds_print_file_any': (
        'from typing import Any\n'
        '\n'
        '\n'
        'def f(file: Any) -> None:\n'
        '    print("hello", file=file)\n'
    ),
    'ds_isinstance_any': (
        'from typing import Any\n'
        '\n'
        '\n'
        'def f(x: Any, cls: Any) -> bool:\n'
        '    return isinstance(x, cls)\n'
    ),
    'ds_argparse_type': (
        'import argparse\n'
        'from typing import Any\n'
        '\n'
        '\n'
        'def f(parser: argparse.ArgumentParser, conv: Any) -> argparse.Namespace:\n'
        '    parser.add_argument("--x", type=conv)\n'
        '    return parser.parse_args([])\n'
    ),
    'ds_sorted_key_lambda_any': (
        'from typing import Any\n'
        '\n'
        '\n'
        'def f(rows: list[str], key: Any) -> list[str]:\n'
        '    return sorted(rows, key=lambda r: (key, r))\n'
    ),
}


#: Sol's round-4 probes (`r4_scan_probes.py`) and a variant: each must fail.
SOL_ROUND4 = {
    'r4_widened_class_callable': (
        'from collections.abc import Callable\n'
        'from typing import Any\n'
        '\n'
        '\n'
        'def f(xs: Any) -> object:\n'
        '    factory: Callable[[Any], object] = list\n'
        '    return factory(xs)\n'
    ),
    'r4_widened_class_type': (
        'from typing import Any\n'
        '\n'
        '\n'
        'def f(xs: Any) -> object:\n'
        '    factory: type[object] = list\n'
        '    return factory(xs)\n'
    ),
    'r4_class_alias': (
        'from typing import Any\n'
        '\n'
        'Factory = list\n'
        '\n'
        '\n'
        'def f(xs: Any) -> object:\n'
        '    return Factory(xs)\n'
    ),
    'r4_generic_class_alias': (
        'from typing import Any\n'
        '\n'
        'Factory = list[Any]\n'
        '\n'
        '\n'
        'def f(xs: Any) -> object:\n'
        '    return Factory(xs)\n'
    ),
    'r4_conditional_class': (
        'from typing import Any\n'
        '\n'
        '\n'
        'def f(xs: Any, flag: bool) -> object:\n'
        '    factory = list if flag else tuple\n'
        '    return factory(xs)\n'
    ),
    'r4_json_unbound_mutator': (
        'import json\n'
        'from typing import Any\n'
        '\n'
        '\n'
        'def f(text: str, other: Any) -> None:\n'
        '    data = json.loads(text)\n'
        "    dict.__setitem__(data, 'x', other)\n"
        "    data['x'].danger()\n"
    ),
    'r4_json_operator_mutator': (
        'import json\n'
        'from typing import Any\n'
        '\n'
        '\n'
        'def f(text: str, other: Any) -> None:\n'
        '    data = json.loads(text)\n'
        "    data |= {'x': other}\n"
        "    data['x'].danger()\n"
    ),
    'r4_json_hook_alias': (
        'import json\n'
        'from collections.abc import Callable\n'
        'from typing import Any\n'
        '\n'
        '\n'
        'def f(text: str, hook: Callable[[dict[str, Any]], Any]) -> Any:\n'
        '    decoder = json.loads\n'
        '    return decoder(text, object_hook=hook)\n'
    ),
    'r4_dataclasses_fields_any': (
        'import dataclasses\n'
        'from typing import Any\n'
        '\n'
        '\n'
        'def f(value: Any) -> object:\n'
        '    return dataclasses.fields(value)\n'
    ),
    'r4_defaultdict_factory': (
        'import collections\n'
        'from typing import Any\n'
        '\n'
        '\n'
        'def f() -> object:\n'
        '    return collections.defaultdict(list)\n'
    ),
}


#: DeepSeek's round-4 probes (cb_call.py, deco_cb.py, obj_call.py, ...): each must fail.
DS_ROUND4 = {
    'ds4_object_str': ('def f(o: object) -> str:\n' '    return str(o)\n'),
    'ds4_object_fstring': ('def f(o: object) -> str:\n' '    return f"{o}"\n'),
    'ds4_object_hash': ('def f(o: object) -> int:\n' '    return hash(o)\n'),
    'ds4_cb_call': (
        'from typing import Any, Callable\n'
        '\n'
        '\n'
        'def f(cb: Callable[[Any], Any], x: Any) -> None:\n'
        '    cb(x)\n'
    ),
    'ds4_deco_cb': (
        'from typing import Any, Callable\n'
        '\n'
        '\n'
        'def f(dec: Callable[[Any], Any]) -> None:\n'
        '    @dec\n'
        '    def g() -> None:\n'
        '        pass\n'
    ),
    'ds4_deco_class_cb': (
        'from typing import Any, Callable\n'
        '\n'
        '\n'
        'def f(dec: Callable[[Any], Any]) -> None:\n'
        '    @dec\n'
        '    class C:\n'
        '        pass\n'
    ),
    'ds4_deco_any': (
        'from typing import Any\n'
        '\n'
        '\n'
        'def f(dec: Any) -> None:\n'
        '    @dec\n'
        '    def g() -> None:\n'
        '        pass\n'
    ),
    'ds4_deco_class_any': (
        'from typing import Any\n'
        '\n'
        '\n'
        'def f(dec: Any) -> None:\n'
        '    @dec\n'
        '    class C:\n'
        '        pass\n'
    ),
    'ds4_obj_call': (
        'def f(o: object, x: object) -> None:\n'
        '    o()  # type: ignore[operator]\n'
    ),
    'ds4_attribute_callable': (
        'from typing import Any, Callable\n'
        '\n'
        '\n'
        'class Holder:\n'
        '    hook: Callable[[Any], Any]\n'
        '\n'
        '\n'
        'def f(h: Holder, x: Any) -> None:\n'
        '    h.hook(x)\n'
    ),
}


#: Sol's round-5 probes (`r5_scan_probe.py`): each must fail.
SOL_ROUND5 = {
    'r5_decorator_factory_identity': (
        'from typing import Any, Callable\n'
        '\n'
        'def identity(dec: Callable[[Any], Any]) -> Callable[[Any], Any]:\n'
        '    return dec\n'
        '\n'
        'def outer(dec: Callable[[Any], Any]) -> None:\n'
        '    @identity(dec)\n'
        '    def inner() -> None:\n'
        '        pass\n'
    ),
    'r5_wraps_callable_object': (
        'import functools\n'
        'from typing import Any, Callable\n'
        '\n'
        'def outer(wrapped: Callable[..., Any]) -> None:\n'
        '    @functools.wraps(wrapped)\n'
        '    def inner() -> None:\n'
        '        pass\n'
    ),
    'r5_contextmanager_callable_object': (
        'import contextlib\n'
        'from typing import Any, Callable\n'
        '\n'
        'def make(fn: Callable[..., Any]) -> object:\n'
        '    return contextlib.contextmanager(fn)\n'
    ),
    'r5_property_callable_object': (
        'from typing import Any, Callable\n'
        '\n'
        'def make(fn: Callable[..., Any]) -> property:\n'
        '    return property(fn)\n'
    ),
    'r5_abcmeta_descriptor': (
        'import abc\n'
        'from typing import Any\n'
        '\n'
        'def make(namespace: dict[str, Any]) -> type:\n'
        '    return abc.ABCMeta("Made", (), namespace)\n'
    ),
    'r5_type_list_parameter': (
        'from typing import Any\n'
        '\n'
        'def make(factory: type[list[Any]], value: Any) -> list[Any]:\n'
        '    return factory(value)\n'
    ),
    'r5_type_object_parameter': (
        'from typing import Any\n'
        '\n'
        'def make(factory: type[object], value: Any) -> object:\n'
        '    return factory(value)\n'
    ),
}


#: Visible callbacks and harmless class values: these must not fail.
ROUND3_CONTROLS = {
    "r3c_lambda_key": _H + "def f(rows: list[str]) -> list[str]:\n"
    "    return sorted(rows, key=lambda v: v.casefold())\n",
    "r3c_method_key": _H + "def f(rows: list[str]) -> list[str]:\n"
    "    return sorted(rows, key=str.casefold)\n",
    "r3c_default_factory": "import dataclasses\n\n\n@dataclasses.dataclass\nclass C:\n"
    "    items: tuple[str, ...] = dataclasses.field(default_factory=tuple)\n",
    "r3c_type_identity": _H + "def f(value: object) -> bool:\n    return type(value) is int\n",
    # The accepted residual (scan.py's docstring): truth tests and comparisons only.
    "r4c_object_residual": (
        "def f(a: object, b: object) -> bool:\n    return bool(a == b) if a else False\n"
    ),
    'r4c_decorators': (
        'import dataclasses\n'
        'import functools\n'
        'from typing import Any, Callable\n'
        '\n'
        '\n'
        '@dataclasses.dataclass(frozen=True)\n'
        'class C:\n'
        '    x: int = 0\n'
        '\n'
        '    @property\n'
        '    def y(self) -> int:\n'
        '        return self.x\n'
        '\n'
        '\n'
        'def helper() -> None:\n'
        '    pass\n'
        '\n'
        '\n'
        'def wrap() -> Callable[..., Any]:\n'
        '    @functools.wraps(helper)\n'
        '    def inner(*args: Any) -> Any:\n'
        '        return None\n'
        '    return inner\n'
    ),
    'r4c_repo_function_value': (
        'def helper(x: int) -> int:\n'
        '    return x + 1\n'
        '\n'
        '\n'
        'def f() -> int:\n'
        '    g = helper\n'
        '    return g(1)\n'
    ),
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
    extra = {**SOL_ROUND2, **SOL_ROUND3, **DS_ROUND3, **SOL_ROUND4, **DS_ROUND4, **SOL_ROUND5,
             **ROUND3_CONTROLS}
    for name, source in extra.items():
        (directory / f"{name}.py").write_text(source, encoding="utf-8")
    paths = ["scripts/probes/methods.py", *(f"scripts/probes/{n}.py" for n in extra)]
    analysis = scan.analyse(root, paths, "3.14")
    assert scan.assert_complete(analysis) == []
    return list(scan.classify(scan.scan_all(analysis), scan.Lists.load()))


def _inert() -> dict[str, Any]:
    data: dict[str, Any] = json.loads((PACKAGE / "lists" / "inert.json").read_text("utf-8"))
    return data


def _rules() -> set[str]:
    return set(scan.RUNS_OTHER_CODE)


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


@pytest.mark.parametrize("probe", sorted({**SOL_ROUND3, **DS_ROUND3, **SOL_ROUND4,
                                          **DS_ROUND4, **SOL_ROUND5}))
def test_a_code_running_callable_reached_through_a_value_fails(
    classified: list[Any], probe: str
) -> None:
    """Review round 3, Sol B1: aliases, bound and unbound methods, partial, getattr,
    lambda wrappers, class aliases, callbacks held in values, and json.loads hooks."""
    rows = [c for c in classified if c.site.module.endswith(f"/{probe}.py")]
    assert any(c.status in scan.FAILING for c in rows), [
        (c.status, c.site.target, c.site.detail) for c in rows
    ]


@pytest.mark.parametrize("probe", sorted(ROUND3_CONTROLS))
def test_visible_callbacks_and_class_values_pass(classified: list[Any], probe: str) -> None:
    rows = [c for c in classified if c.site.module.endswith(f"/{probe}.py")]
    assert rows and not [c for c in rows if c.status in scan.FAILING], [
        (c.status, c.site.target, c.site.detail) for c in rows if c.status in scan.FAILING
    ]


@pytest.mark.parametrize("probe", sorted(SOL_ROUND2))
def test_sols_round_2_probes_fail(classified: list[Any], probe: str) -> None:
    rows = [c for c in classified if c.site.module.endswith(f"/{probe}.py")]
    assert any(c.status in scan.FAILING for c in rows), [
        (c.status, c.site.target) for c in rows
    ]


def test_the_probe_module_is_valid_python() -> None:
    compile(textwrap.dedent(_module(PROBES)), "methods.py", "exec")
    assert Path(PACKAGE / "lists" / "inert.json").is_file()


# --- an independent check: what typeshed's signatures say (DeepSeek round 4, N3) -----------

#: Entries whose typeshed signature takes a protocol, `object`, `Any` or a callable,
#: reviewed as running none of that argument's code at the call, with the reason.
#: Every other entry with such a signature must be flagged `runs_code_of_others`.
REVIEWED_NO_CALL = {
    # Re-audited in round 5 by calling each with a hostile object that logs every
    # attribute read and protocol call (Sol's method): these ran none.
    "builtins.id": "returns the object's identity",
    "typing.cast": "returns its second argument unchanged",
    "xml.etree.ElementTree.Element.__deepcopy__": "the memo dict the copy machinery passes",
    "xml.etree.ElementTree.Element.append": "C Element checks the argument is an Element",
    **{f"builtins.{kind}.{op}": "rich comparison in C: a foreign operand returns NotImplemented"
       for kind in ("bytes", "float", "int", "str") for op in ("__eq__", "__ne__")},
}


def _signature_takes_code(root: Path, names: list[str]) -> dict[str, bool | None]:
    """{name: does a parameter's typeshed type admit caller code?} (None: unresolved)."""
    import importlib.util

    from mypy.nodes import Decorator, FuncDef, OverloadedFuncDef, TypeInfo
    from mypy.types import (
        AnyType,
        CallableType,
        Instance,
        Overloaded,
        TupleType,
        TypeVarType,
        UnionType,
        get_proper_type,
    )

    def prefixes(name: str) -> list[tuple[str, list[str]]]:
        parts = name.split(".")
        return [(".".join(parts[:i]), parts[i:]) for i in range(len(parts) - 1, 0, -1)]

    def is_module(name: str) -> bool:
        try:
            return all(p.isidentifier() for p in name.split(".")) and (
                importlib.util.find_spec(name) is not None)
        except (ImportError, ValueError):
            return False

    modules = sorted({m for n in names for m, _ in prefixes(n) if is_module(m)})
    directory = root / "scripts" / "probes"
    directory.mkdir(parents=True)
    (directory / "signatures.py").write_text("".join(f"import {m}\n" for m in modules), "utf-8")
    graph = scan.analyse(root, ["scripts/probes/signatures.py"], "3.14").graph

    def lookup(name: str) -> Any:
        for module, rest in prefixes(name):
            state = graph.get(module)
            if state is None or state.tree is None or rest[0] not in state.tree.names:
                continue
            node = state.tree.names[rest[0]].node
            for part in rest[1:]:
                node = next((b.names[part].node for b in node.mro if part in b.names), None) \
                    if isinstance(node, TypeInfo) else None
            if node is not None:
                return node
        return None

    def signatures(node: Any) -> list[Any]:
        if isinstance(node, Decorator):
            node = node.func
        if isinstance(node, FuncDef):
            return [node.type] if node.type is not None else []
        if isinstance(node, OverloadedFuncDef):
            return [(i.func if isinstance(i, Decorator) else i).type for i in node.items]
        if isinstance(node, TypeInfo):
            for method in ("__init__", "__new__"):
                for base in node.mro:
                    if method in base.names and base.fullname != "builtins.object":
                        return signatures(base.names[method].node)
        return []

    def takes(t: Any, depth: int = 0) -> bool:
        t = get_proper_type(t)
        if depth > 4:
            return False
        if isinstance(t, (AnyType, CallableType, Overloaded)):
            return True
        if isinstance(t, UnionType):
            return any(takes(i, depth + 1) for i in t.items)
        if isinstance(t, TypeVarType):
            return t.id.is_meta_var() and takes(t.upper_bound, depth + 1)
        if isinstance(t, Instance):
            return t.type.fullname == "builtins.object" or t.type.is_protocol or any(
                takes(x, depth + 1) for x in t.args)
        if isinstance(t, TupleType):
            return any(takes(i, depth + 1) for i in t.items)
        return False

    result: dict[str, bool | None] = {}
    for name in names:
        node = lookup(name)
        if node is None:
            result[name] = None
            continue
        owner = lookup(name.rsplit(".", 1)[0]) if "." in name else None
        skip_first = isinstance(owner, TypeInfo) or isinstance(node, TypeInfo)
        result[name] = any(
            takes(arg) for t in signatures(node) if t is not None
            for arg in list(get_proper_type(t).arg_types)[1 if skip_first else 0:]
        )
    return result


def test_typeshed_signatures_agree_with_the_flags(tmp_path: Path) -> None:
    """Independent of the flags and the rules: an inert entry whose typeshed signature
    takes a protocol, `object`, `Any` or a callable must be flagged (with a rule and a
    probe, above) or reviewed in REVIEWED_NO_CALL. The other direction is not
    checked: a flag also covers element protocols no signature shows."""
    groups = _inert()["targets"]
    flagged = {n for g in groups if g.get("runs_code_of_others") for n in g["names"]}
    names = sorted(n for g in groups for n in g["names"])
    derived = _signature_takes_code(tmp_path, names)
    unresolved = sorted(n for n, v in derived.items() if v is None)
    assert unresolved == ["typing.<type alias construction>"], unresolved
    unflagged = sorted(n for n, v in derived.items() if v and n not in flagged)
    assert set(unflagged) == set(REVIEWED_NO_CALL), (
        sorted(set(unflagged) - set(REVIEWED_NO_CALL)),
        sorted(set(REVIEWED_NO_CALL) - set(unflagged)),
    )
