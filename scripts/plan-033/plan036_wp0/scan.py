"""Part 1: the fail-closed scan of every resolved call target, on mypy's analysis.

What it does (`plans/036-change-cost.md`, "The four parts", 1):

- **Resolution.** Every call, every reference to a callable (a function, method
  or class used as a value: `map(open, ...)`, `functools.partial(open, ...)`,
  `key=str.lower`) and every implicit protocol operation in every module of the
  closure is resolved to a fully qualified target with mypy's semantic
  analysis and inferred types. Implicit operations are the ones Python runs
  without a call in the source: property and descriptor access, iteration
  (`__iter__`, `__next__`), `with` (`__enter__`, `__exit__`), operators and
  comparisons, truth tests (`__bool__`, `__len__`), conversions and formatting
  (`str`, `repr`, `format`, f-strings, `print`, `int`, `float`, `len`, `hash`,
  ...), and finalisers (`__del__` of a constructed object).
- **Classification.** A target defined in the repository is not an edge: its
  body is in the closure and is scanned. Every other target, in the standard
  library, a built-in or a third-party distribution, must be on exactly one
  reviewed list: inert (by fully qualified name; by type only for `<json>`), a
  declared edge for that site, or proved
  platform-unreachable under a recorded `sys.platform`/`os.name` guard that is
  false on the controller. Anything else fails, whatever its module.
- **Unresolvable** targets fail unless declared for that site: a receiver of
  type `Any`, `getattr` with a computed name, `__import__`, `exec`, a module
  loaded at run time, code mypy did not analyse.
- **Import machinery.** A write to `sys.meta_path`, `sys.path_hooks` or the
  `builtins` module is refused outright; a change to `sys.path` (or
  `sys.modules`) is an edge that must be declared; a read of `sys.orig_argv`
  is refused (the traced run cannot reproduce it).
- **Completeness.** `assert_complete` proves mypy analysed every closure module
  (present in the build graph, loaded from that file, with a tree and a
  symbol table, not skipped and not hidden by a file-level `# type: ignore`).

How sites are keyed: `<module path>::<scope>::<kind>::<target>::<ordinal>`,
where scope is the enclosing qualified name (`<module>` at top level) and the
ordinal counts identical (module, scope, kind, target) sites in source order.
Line numbers are recorded for the reader and are not part of the key, so an
edit elsewhere in a file does not move every declaration (the gate pins file
digests separately).

The walker visits mypy's node classes explicitly and **fails on any node
class it does not know** rather than skipping it: a construct added to the
language or to the code cannot pass by being invisible.

- **Code other objects run.** A listed method or function that runs its
  arguments', elements' or callbacks' code (`list.remove`, `sorted(key=...)`,
  `Pattern.sub`, `json.loads(object_hook=...)`) is resolved at every call by
  `METHOD_PROTOCOLS` and the iteration and conversion rules, and fails closed
  when taken as a value instead of called (reviews rounds 2 to 4).

**Known limitation: values typed only `object`** (accepted by the coordinator in
review round 4, pending the owner). A truth test, an equality or order
comparison, or a comparison of `sorted(..., key=...)` results on a value whose
static type is plain `object` passes without a site, although at run time the
value can be an instance of any class whose `__bool__`, `__len__`, `__eq__` or
ordering methods run code. Treating every such use as unknown, as `Any` is
treated, adds about 400 sites across the live closure (measured in round 3).
In these lanes such values come from repository code (scanned in the
repository) or from decoded JSON and in-memory containers. Where the container
rules look at elements (set operations, `json.dumps` values, `any`/`all`/`sum`
of a comprehension), an element typed `object` already fails. The residual
bounds any reuse of this scanner beyond these lanes.
"""

from __future__ import annotations

import io
import json
import os
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mypy import build
from mypy.main import process_options
from mypy.modulefinder import BuildSource
from mypy.nodes import (
    ARG_NAMED,
    ARG_NAMED_OPT,
    ARG_OPT,
    ARG_POS,
    ARG_STAR,
    ARG_STAR2,
    AssignmentExpr,
    Block,
    CallExpr,
    ComparisonExpr,
    Decorator,
    DictExpr,
    Expression,
    FuncBase,
    FuncDef,
    IfStmt,
    IndexExpr,
    LambdaExpr,
    ListExpr,
    MemberExpr,
    MypyFile,
    NameExpr,
    Node,
    OpExpr,
    OverloadedFuncDef,
    RefExpr,
    StarExpr,
    StrExpr,
    SymbolNode,
    TupleExpr,
    TypeAlias,
    TypeInfo,
    UnaryExpr,
    Var,
)
from mypy.types import (
    AnyType,
    CallableType,
    Instance,
    LiteralType,
    NoneType,
    Overloaded,
    ParamSpecType,
    TupleType,
    Type,
    TypedDictType,
    TypeType,
    TypeVarTupleType,
    TypeVarType,
    UninhabitedType,
    UnionType,
    get_proper_type,
)

from .argparse_plugin import ArgparsePlugin
from .json_origin import JsonOrigins, ParameterOracle, immutable
from .repo import LISTS_DIR, Wp0Error, sha256_bytes

#: The controller's platform, as `sys.platform` and `os.name` report it. The
#: plan names mvmcc02 (Linux) as the controller; the record states it, and the
#: analysis runs with mypy's `--platform` set to it on every host, so the scan
#: gives the same answer on the Windows CI job.
CONTROLLER_PLATFORM = "linux"
CONTROLLER_OS_NAME = "posix"

#: Module attributes whose *read* is refused (DeepSeek round-5 NB-4).
REFUSED_ATTRIBUTE_READS = frozenset({"sys.orig_argv"})
#: Import-machinery objects: a write to these is refused outright.
REFUSED_WRITE_TARGETS = frozenset({"sys.meta_path", "sys.path_hooks"})
#: Import-machinery objects whose change is a declared edge.
DECLARED_MACHINERY = frozenset({"sys.path", "sys.modules"})

#: Builtins whose call runs a protocol method of its positional arguments.
_CONVERSIONS: dict[str, tuple[str, ...]] = {
    "builtins.str": ("__str__", "__repr__"),
    "builtins.repr": ("__repr__",),
    "builtins.ascii": ("__repr__",),
    "builtins.format": ("__format__",),
    "builtins.print": ("__str__",),
    "builtins.str.format_map": ("__getitem__",),
    "builtins.bool": ("__bool__", "__len__"),
    "builtins.len": ("__len__",),
    "builtins.hash": ("__hash__",),
    "builtins.int": ("__int__", "__index__", "__trunc__"),
    "builtins.float": ("__float__", "__index__"),
    "builtins.abs": ("__abs__",),
    "builtins.round": ("__round__",),
    "builtins.divmod": ("__divmod__",),
    "builtins.bytes": ("__bytes__", "__index__", "__buffer__"),
    "builtins.iter": ("__iter__",),
    "builtins.next": ("__next__",),
    "builtins.reversed": ("__reversed__", "__len__", "__getitem__"),
}
#: Builtins that iterate their positional arguments (all of them, from the index).
_ITERATING: dict[str, int] = {
    "builtins.list": 0, "builtins.tuple": 0, "builtins.set": 0, "builtins.frozenset": 0,
    "builtins.dict": 0, "builtins.sorted": 0, "builtins.enumerate": 0, "builtins.zip": 0,
    "builtins.map": 1, "builtins.filter": 1, "builtins.any": 0, "builtins.all": 0,
    "builtins.sum": 0, "builtins.min": 0, "builtins.max": 0, "builtins.str.join": 0,
    "builtins.bytes.join": 0, "builtins.list.extend": 0, "builtins.set.update": 0,
    "builtins.dict.update": 0, "builtins.set.union": 0, "builtins.set.intersection": 0,
    "builtins.set.difference": 0, "builtins.set.issubset": 0, "builtins.frozenset.union": 0,
}
#: Builtins whose result compares or hashes the iterated elements.
_ELEMENT_OPS: dict[str, tuple[str, ...]] = {
    "builtins.sorted": ("__lt__",), "builtins.min": ("__lt__",), "builtins.max": ("__gt__",),
    "builtins.list.sort": ("__lt__",),
    "builtins.set": ("__hash__", "__eq__"), "builtins.frozenset": ("__hash__", "__eq__"),
}
@dataclass(frozen=True)
class Protocol:
    """The code of *other* objects a method of an in-memory type runs (review round 2,
    Sol B2: `list.remove` runs its elements' `__eq__`; `Pattern.sub` calls a callable
    replacement). Each entry makes the scan resolve those protocol methods on the
    static types involved, so an `Any` there is an unresolvable site.

    `args` maps a positional index, a keyword name or "*" (every argument) to the
    dunders run on that argument (`__call__`: the argument is called back, see
    `callback_argument`; a name starting `?`: only an argument of unknown type is
    a site); `elements` and `values` are run on the receiver's
    element (or key) and value types; `arg_elements` and `arg_values` on those of
    the first argument.
    """

    args: tuple[tuple[int | str, tuple[str, ...]], ...] = ()
    elements: tuple[str, ...] = ()
    values: tuple[str, ...] = ()
    arg_elements: tuple[str, ...] = ()
    arg_values: tuple[str, ...] = ()

    def for_argument(self, position: int | None, keyword: str | None) -> tuple[str, ...]:
        out: list[str] = []
        for key, dunders in self.args:
            if key == "*" or key == position or (keyword is not None and key == keyword):
                out.extend(d for d in dunders if d not in out)
        return tuple(out)


_EQ = ("__eq__",)
_HASH_EQ = ("__hash__", "__eq__")
_ORDER = ("__eq__", "__lt__")
_INDEX = ("__index__",)
_CALL = ("__call__",)
_BUFFER = ("__buffer__",)
_MAPPING_ARG = ("keys", "__getitem__", "__iter__")


def _protocols() -> dict[str, Protocol]:
    table: dict[str, Protocol] = {}
    exception_args = Protocol(args=(("*", ("__str__", "__repr__")),))

    def add(names: Iterable[str], protocol: Protocol) -> None:
        for name in names:
            table[name] = protocol

    membership = Protocol(args=((0, _EQ),), elements=_EQ)
    for kind in ("list", "tuple"):
        add([f"builtins.{kind}.{m}" for m in ("__contains__", "count", "index")],
            Protocol(args=((0, _EQ), (1, _INDEX), (2, _INDEX)), elements=_EQ))
        add([f"builtins.{kind}.{m}" for m in ("__eq__", "__ne__")],
            Protocol(elements=_EQ, arg_elements=_EQ))
        add([f"builtins.{kind}.{m}" for m in ("__lt__", "__le__", "__gt__", "__ge__")],
            Protocol(elements=_ORDER, arg_elements=_ORDER))
        add([f"builtins.{kind}.{m}" for m in ("__getitem__", "__mul__", "__rmul__")],
            Protocol(args=((0, _INDEX),)))
    add(["builtins.list.remove"], membership)
    add(["builtins.tuple.__hash__"], Protocol(elements=("__hash__",)))
    add(["builtins.list.sort"], Protocol(args=(("key", _CALL),), elements=("__lt__",)))
    add([f"builtins.list.{m}" for m in ("__setitem__", "__delitem__", "pop", "insert",
                                         "__imul__")], Protocol(args=((0, _INDEX),)))
    add(["builtins.list.extend", "builtins.list.__iadd__"],
        Protocol(args=((0, ("__iter__",)),)))
    keyed = Protocol(args=((0, _HASH_EQ),), elements=_EQ)
    add([f"builtins.dict.{m}" for m in ("__getitem__", "get", "pop", "__contains__",
                                         "__delitem__", "setdefault", "__setitem__")], keyed)
    add(["builtins.dict.__eq__", "builtins.dict.__ne__"],
        Protocol(elements=_HASH_EQ, values=_EQ, arg_elements=_HASH_EQ, arg_values=_EQ))
    add([f"builtins.dict.{m}" for m in ("update", "__ior__", "__or__", "__ror__")],
        Protocol(args=((0, _MAPPING_ARG),), elements=_EQ, arg_elements=_HASH_EQ))
    add(["builtins.dict.fromkeys"], Protocol(args=((0, ("__iter__",)),),
                                             arg_elements=_HASH_EQ))
    for kind in ("set", "frozenset"):
        add([f"builtins.{kind}.{m}" for m in ("__contains__", "add", "remove", "discard")],
            keyed)
        add([f"builtins.{kind}.{m}" for m in (
            "update", "union", "intersection", "difference", "symmetric_difference",
            "issubset", "issuperset", "isdisjoint", "__or__", "__and__", "__sub__", "__xor__",
            "__ior__", "__iand__", "__isub__", "__ixor__", "__le__", "__ge__", "__lt__",
            "__gt__", "__eq__", "__ne__", "intersection_update", "difference_update",
            "symmetric_difference_update",
        )], Protocol(args=((0, ("__iter__",)),), elements=_HASH_EQ, arg_elements=_HASH_EQ))
    add(["builtins.str.translate"], Protocol(args=((0, ("__getitem__",)),)))
    add([f"builtins.{kind}.{m}" for kind in ("str", "bytes")
         for m in ("__getitem__", "__mul__", "__rmul__")], Protocol(args=((0, _INDEX),)))
    add(["builtins.str.format_map"], Protocol(arg_values=("__format__", "__str__",
                                                          "__repr__")))
    add([f"builtins.bytes.{m}" for m in (
        "__add__", "__contains__", "count", "find", "rfind", "index", "rindex", "replace",
        "startswith", "endswith", "split", "rsplit", "strip", "lstrip", "rstrip", "partition",
        "rpartition", "join", "removeprefix", "removesuffix",
    )], Protocol(args=(("*", _BUFFER + _INDEX),)))
    add(["builtins.bytearray.extend"], Protocol(args=((0, ("__iter__",)),),
                                                 arg_elements=_INDEX))
    add(["builtins.bytearray.__iadd__", "builtins.bytearray.__add__"],
        Protocol(args=((0, _BUFFER),)))
    add(["builtins.int.__round__", "builtins.float.__round__"], Protocol(args=((0, _INDEX),)))
    add(["builtins.map", "builtins.filter"], Protocol(args=((0, _CALL),)))
    add(["builtins.sorted", "builtins.min", "builtins.max"],
        Protocol(args=(("key", _CALL),)))
    # Truth and addition of the elements (review round 3, DeepSeek B3).
    add(["builtins.any", "builtins.all"], Protocol(arg_elements=("__bool__", "__len__")))
    add(["builtins.sum"], Protocol(args=((1, ("__add__", "__radd__")), ("start", (
        "__add__", "__radd__"))), arg_elements=("__add__", "__radd__")))
    add(["builtins.memoryview"], Protocol(args=((0, _BUFFER),)))
    add(["builtins.memoryview.__getitem__", "builtins.memoryview.__setitem__"],
        Protocol(args=((0, _INDEX),)))
    # print(..., file=f) runs f.write; argparse runs `type=`/`action=` callbacks
    # and tests membership in `choices=` at parse time (DeepSeek N1, N2).
    add(["builtins.print"], Protocol(args=(("file", ("?write", "?flush")),)))
    # Round 3 audit (DeepSeek B3): constructors and functions of the reviewed
    # inert list that run an argument's protocol methods.
    add(["builtins.bytearray"], Protocol(args=((0, _INDEX + _BUFFER + ("__iter__",)),),
                                         arg_elements=_INDEX))
    add(["builtins.chr"], Protocol(args=((0, _INDEX),)))
    add(["builtins.range"], Protocol(args=(("*", _INDEX),)))
    add(["builtins.type"], Protocol(args=((1, ("__iter__",)), (2, ("keys", "__getitem__")))))
    add(["builtins.isinstance"], Protocol(args=((1, ("?__instancecheck__",)),)))
    add(["builtins.issubclass"], Protocol(args=((1, ("?__subclasscheck__",)),)))
    add([f"builtins.{name}" for name in ("AttributeError", "Exception", "OSError",
                                         "SystemExit", "TypeError", "ValueError")]
        + ["sqlite3.Error"], exception_args)
    # `__call0__`: called back only with no arguments, or with values the callee
    # makes itself (strings from the command line), never the caller's objects.
    # The factory is called with no arguments: a class there runs nothing of others.
    add(["dataclasses.field"], Protocol(args=(("default_factory", ("__call0__",)),)))
    add(["xml.etree.ElementTree.Element", "xml.etree.ElementTree.SubElement"],
        Protocol(args=(("*", ("__str__", "__contains__")),)))
    add(["xml.etree.ElementTree.XMLParser"], Protocol(args=(("target", _CALL),)))
    add(["xml.etree.ElementTree.tostring"], Protocol(args=((0, ("?iter",)),)))
    add(["xml.sax.saxutils.quoteattr"], Protocol(args=((0, ("?replace",)),
                                                       (1, ("items",)), ("entities", ("items",)))))
    add(["re.compile", "re.escape"], Protocol(args=((0, ("?__buffer__",)),)))
    add(["re.fullmatch", "re.match", "re.search"],
        Protocol(args=((0, ("?__buffer__",)), (1, _BUFFER), ("string", _BUFFER))))
    add(["_hashlib.openssl_sha256"], Protocol(args=((0, _BUFFER), ("string", _BUFFER))))
    add(["_io.BytesIO"], Protocol(args=((0, _BUFFER), ("initial_bytes", _BUFFER))))
    add(["collections.Counter"], Protocol(args=((0, _MAPPING_ARG),), arg_elements=_HASH_EQ))
    add(["contextlib.suppress"], Protocol(args=(("*", ("?__subclasscheck__",)),)))
    add(["ipaddress.ip_address", "ipaddress.ip_network"],
        Protocol(args=((0, ("__str__", "?__index__")), ("strict", ("__bool__",)))))
    add(["ipaddress.summarize_address_range"], Protocol(args=(("*", ("?__lt__",)),)))
    add(["types.MappingProxyType"], Protocol(args=((0, ("?__getitem__",)),)))
    add(["uuid.UUID"], Protocol(args=(("*", ("?replace", "?__index__")),)))
    add(["uuid.uuid5"], Protocol(args=((1, ("?encode",)), ("name", ("?encode",)))))
    add(["zipfile.ZipInfo"], Protocol(args=(("*", ("?__fspath__", "?__index__")),)))
    add(["_stat.S_ISDIR", "_stat.S_ISLNK", "_stat.S_ISREG"], Protocol(args=((0, _INDEX),)))
    add(["datetime.datetime"], Protocol(args=(("*", _INDEX),)))
    add(["pathlib.Path", "pathlib.PurePosixPath", "pathlib.PureWindowsPath",
         "posixpath.normcase"], Protocol(args=(("*", ("__fspath__",)),)))
    # Review round 4 (Sol B4): helpers that read an argument's attributes or
    # format it run its attribute protocol or __repr__.
    add(["dataclasses.fields"], Protocol(args=((0, ("?__dataclass_fields__",)),)))
    add(["typing.get_args"], Protocol(args=((0, ("?__args__",)),)))
    add(["typing.assert_never"], Protocol(args=((0, ("__repr__",)),)))
    add(["functools.wraps"], Protocol(args=((0, ("?__name__",)),)))
    add(["argparse.ArgumentParser"], Protocol(args=(("formatter_class", _CALL),
                                                    ("parents", ("__iter__",)))))
    add(["argparse.ArgumentParser.error"], Protocol(args=((0, ("__str__",)),)))
    add(["argparse.ArgumentParser.parse_args"], Protocol(args=((0, ("__iter__",)),),
                                                         arg_elements=("?__str__",)))
    add(["logging.Logger.error"], Protocol(args=(("*", ("__str__", "__repr__")),)))
    # `type=` is called on command-line strings, `action=` constructed by argparse:
    # a referenced class or function runs nothing of other objects there.
    add(["argparse._ActionsContainer.add_argument"], Protocol(args=(
        ("type", ("__call0__",)), ("action", ("__call0__",)),
        ("choices", ("__contains__", "__iter__")),
    )))
    add(["builtins.BaseException.__init__", "builtins.Exception.__init__"], exception_args)
    add(["re.Pattern.sub", "re.Pattern.subn"],
        Protocol(args=((0, _CALL + _BUFFER), (1, _BUFFER), ("repl", _CALL + _BUFFER),
                       ("string", _BUFFER))))
    add([f"re.Pattern.{m}" for m in ("match", "fullmatch", "search", "finditer", "findall",
                                     "split", "scanner")],
        Protocol(args=((0, _BUFFER), ("string", _BUFFER))))
    add([f"re.Match.{m}" for m in ("group", "start", "end", "span", "__getitem__")],
        Protocol(args=(("*", _INDEX + _HASH_EQ),)))
    add(["_hashlib.HASH.update", "_io.BytesIO.write", "_io.BytesIO.writelines"],
        Protocol(args=((0, _BUFFER),)))
    add(["_io.BytesIO.seek", "_io.BytesIO.truncate", "_io.BytesIO.read"],
        Protocol(args=((0, _INDEX),)))
    element = "xml.etree.ElementTree.Element"
    add([f"{element}.get"], Protocol(args=((0, _HASH_EQ),)))
    add([f"{element}.set"], Protocol(args=((0, _HASH_EQ), (1, ("__str__",)))))
    add([f"{element}.{m}" for m in ("find", "findall", "findtext", "iter", "iterfind")],
        Protocol(args=(("*", _HASH_EQ),)))
    add([f"{element}.__getitem__", f"{element}.insert", f"{element}.__setitem__",
         f"{element}.__delitem__"], Protocol(args=((0, _INDEX),)))
    add([f"{element}.extend"], Protocol(args=((0, ("__iter__",)),)))
    add(["xml.etree.ElementTree.TreeBuilder", "xml.etree.ElementTree.TreeBuilder.__init__"],
        Protocol(args=(("*", _CALL),)))
    add([f"pathlib.PurePath.{m}" for m in (
        "__truediv__", "__rtruediv__", "joinpath", "relative_to", "is_relative_to",
        "with_name", "with_stem", "with_suffix", "match", "full_match", "with_segments",
    )], Protocol(args=(("*", ("__fspath__",)),)))
    add([f"collections.Counter.{m}" for m in ("__sub__", "__add__", "__or__", "__and__",
                                              "update", "subtract")],
        Protocol(args=((0, _MAPPING_ARG),), elements=_HASH_EQ, arg_elements=_HASH_EQ))
    # Abstract receivers: whatever concrete container the value is, a lookup
    # hashes or compares the key or probe against its contents.
    add(["typing.Mapping.__getitem__", "typing.Mapping.get", "typing.Mapping.__contains__"],
        keyed)
    add(["typing.Container.__contains__", "typing.Collection.__contains__",
         "typing.Sequence.__contains__", "typing.Sequence.index", "typing.Sequence.count",
         "typing.AbstractSet.__contains__", "typing.KeysView.__contains__",
         "typing.ItemsView.__contains__", "typing.ValuesView.__contains__"], membership)
    add(["typing.Sequence.__getitem__"], Protocol(args=((0, _INDEX),)))
    add(["builtins.str.format"], Protocol(args=(("*", ("__format__", "__str__", "__repr__")),)))
    add(["copy.deepcopy", "dataclasses.asdict"],
        Protocol(args=(("*", ("__deepcopy__", "__reduce_ex__")),)))
    # replace() builds a new object through the instance's own class.
    add(["dataclasses.replace"], Protocol(args=((0, ("__init__",)),)))
    add(["difflib.unified_diff"], Protocol(args=((0, ("__iter__",)), (1, ("__iter__",))),
                                           arg_elements=_HASH_EQ))
    add(["json.loads", "json.load"], Protocol(args=tuple(
        (name, _CALL) for name in ("object_hook", "object_pairs_hook", "parse_float",
                                   "parse_int", "parse_constant", "cls")
    )))
    add(["json.dumps"], Protocol(args=((0, ("items", "__iter__")), ("default", _CALL),
                                       ("cls", _CALL)), arg_values=("items", "__iter__")))
    add(["base64.b64encode", "base64.b64decode"], Protocol(args=((0, _BUFFER),)))
    add(["_struct.pack"], Protocol(args=(("*", ("__index__", "__float__")),)))
    add(["_struct.unpack", "_struct.unpack_from"], Protocol(args=((1, _BUFFER),)))
    return table


#: Methods of in-memory types that run code of their arguments or elements, and
#: what they run (review round 2). Every inert entry for such a method must have
#: a rule here; tests/test_plan036_scan.py holds a negative probe for each.
METHOD_PROTOCOLS: dict[str, Protocol] = _protocols()
#: Arguments of a method or operator of a value of unknown type that the method
#: may hash or compare (a JSON value's `get`, `count`, `index`, `in`, `[]`).
_ANY_RECEIVER_ARGS = Protocol(args=(("*", _HASH_EQ),))

#: Callees that compare the results of a `key=` callback.
_KEYED_ORDERING = frozenset({"builtins.sorted", "builtins.min", "builtins.max",
                             "builtins.list.sort"})
#: Callees whose *class* arguments are type tests, not callable references.
_TYPE_TEST_CALLEES = frozenset({"builtins.isinstance", "builtins.issubclass"})
#: Builtins whose name argument selects an attribute: resolved when literal.
_ATTRIBUTE_BUILTINS = frozenset(
    {"builtins.getattr", "builtins.setattr", "builtins.hasattr", "builtins.delattr"}
)
#: Every callee the scan knows runs code of other objects (its arguments, their
#: elements, or callables it is handed); one of these taken as a value fails.
RUNS_OTHER_CODE: frozenset[str] = frozenset(
    set(METHOD_PROTOCOLS) | set(_ITERATING) | set(_CONVERSIONS) | set(_ELEMENT_OPS)
    | set(_ATTRIBUTE_BUILTINS)
)
#: Builtins that run code the analysis cannot see; always unresolvable.
_OPAQUE_BUILTINS = frozenset(
    {
        "builtins.__import__", "builtins.exec", "builtins.eval", "builtins.compile",
        "builtins.vars", "builtins.globals", "builtins.locals", "builtins.breakpoint",
        "builtins.input", "builtins.open",
    }
)
_OPAQUE_REASON = {
    "builtins.open": "file I/O",
}
#: Classes whose instance methods are all special-cased elsewhere.
_OBJECT = "builtins.object"
#: Read-only protocols: an implicit dunder on a receiver typed only by one of
#: these runs on a concrete object made by a call classified where it was made
#: (a declared open covers reading the file it opened). Mutable protocols and
#: context managers are excluded (review round 1): a store into an abstract
#: MutableMapping may be a store into os.environ, and an abstract `__exit__` may
#: write an archive's central directory.
_PROTOCOL_DUNDERS_COVERED_AT_PRODUCER = frozenset(
    {
        "typing.Iterator", "typing.Iterable", "typing.Generator", "typing.AsyncIterator",
        "typing.Reversible", "typing.Collection", "typing.Container", "typing.Sized",
        "typing.Sequence", "typing.Mapping", "typing.AbstractSet",
        "typing.KeysView", "typing.ValuesView", "typing.ItemsView", "typing.MappingView",
        "typing.SupportsIndex", "typing.SupportsInt", "typing.SupportsFloat",
        "typing.SupportsAbs", "typing.Hashable",
    }
)


# --- data ---------------------------------------------------------------------


@dataclass(frozen=True)
class Site:
    """One resolved (or unresolvable) target at one place in the closure."""

    module: str  # repository-relative path
    scope: str
    kind: str  # call | reference | implicit | attribute-read | machinery | unresolved
    target: str
    detail: str
    line: int
    ordinal: int = 0
    guard: str | None = None  # the platform guard, when platform-unreachable
    #: For an `<Any>` site: the subject expression's span, and the kind the site
    #: takes if the interprocedural JSON analysis proves the subject is JSON.
    span: tuple[int, int, int, int] | None = field(default=None, compare=False)
    json_kind: str = field(default="", compare=False)

    @property
    def key(self) -> str:
        return f"{self.module}::{self.scope}::{self.kind}::{self.target}::{self.ordinal}"


@dataclass
class Classified:
    site: Site
    status: str  # inert | edge | platform-unreachable | refused | unclassified | unresolvable
    reason: str = ""


#: Declaration kinds (`lists/edges.json`), and what `reaches` must hold for each.
EDGE_KINDS = {
    "file-read": ("repo_files", "repo_dir", "outside_tree", "floor", "in_memory"),
    "file-write": ("outside_tree", "in_memory"),
    "enumerate": ("repo_dir", "outside_tree"),
    "module-load": ("module",),
    "sys-path": ("sys_path_front",),
    "sys-modules": ("outside_tree",),
    "process": ("executable",),
    "env-read": ("env",),
    "clock-or-random": ("outside_tree",),
    "unresolved": ("what",),
}


@dataclass
class Lists:
    """The reviewed classification lists (tier-2 data)."""

    inert_targets: dict[str, str]  # fully qualified target -> reason
    inert_types: dict[str, str]  # class fullname -> reason (all its methods inert)
    edges: dict[str, dict[str, Any]]  # site key -> declaration
    digest: str
    #: Grouped declarations: each covers exact targets in one module (all of
    #: it, or named scopes), with the exact number of sites per target.
    groups: list[dict[str, Any]] = field(default_factory=list)

    def group_for(self, site: Site) -> dict[str, Any] | None:
        """The group covering *site*: one naming its scope wins over a module-wide one."""
        wide = None
        for group in self.groups:
            if group["module"] != site.module or site.target not in group["targets"]:
                continue
            scopes = group.get("scopes", "*")
            if scopes != "*" and site.scope in scopes:
                return group
            if scopes == "*":
                wide = group
        return wide

    @classmethod
    def load(cls, directory: Path = LISTS_DIR) -> Lists:
        inert_raw = (directory / "inert.json").read_bytes()
        edges_raw = (directory / "edges.json").read_bytes()
        inert = json.loads(inert_raw)
        edges = json.loads(edges_raw)
        targets: dict[str, str] = {}
        for group in inert["targets"]:
            for name in group["names"]:
                if name in targets:
                    raise Wp0Error(f"inert.json lists {name} twice")
                targets[name] = group["reason"]
        types = {entry["type"]: entry["reason"] for entry in inert["types"]}
        declared: dict[str, dict[str, Any]] = {}
        for entry in edges["edges"]:
            if entry["site"] in declared:
                raise Wp0Error(f"edges.json declares {entry['site']} twice")
            kind = entry.get("kind")
            if kind not in EDGE_KINDS:
                raise Wp0Error(f"edges.json: {entry['site']} has unknown kind {kind!r}")
            reaches = entry.get("reaches")
            if not isinstance(reaches, dict) or not any(k in reaches for k in EDGE_KINDS[kind]):
                raise Wp0Error(
                    f"edges.json: {entry['site']} ({kind}) must say what it reaches: "
                    f"one of {EDGE_KINDS[kind]}"
                )
            if not str(entry.get("reason", "")).strip():
                raise Wp0Error(f"edges.json: {entry['site']} has no reason")
            declared[entry["site"]] = entry
        groups: list[dict[str, Any]] = []
        claimed: set[tuple[str, str, str]] = set()
        for group in edges.get("groups", []):
            label = f"{group.get('module')} {sorted(group.get('targets', {}))[:2]}"
            kind = group.get("kind")
            if kind not in EDGE_KINDS:
                raise Wp0Error(f"edges.json group {label}: unknown kind {kind!r}")
            reaches = group.get("reaches")
            if not isinstance(reaches, dict) or not any(k in reaches for k in EDGE_KINDS[kind]):
                raise Wp0Error(f"edges.json group {label}: must say what it reaches")
            if not str(group.get("reason", "")).strip():
                raise Wp0Error(f"edges.json group {label}: no reason")
            counted = group.get("targets")
            if not isinstance(counted, dict) or not all(
                isinstance(n, int) and n > 0 for n in counted.values()
            ):
                raise Wp0Error(f"edges.json group {label}: targets must map to site counts")
            scopes = group.get("scopes", "*")
            for target in counted:
                for scope in ([scopes] if scopes == "*" else scopes):
                    key = (group["module"], scope, target)
                    if key in claimed:
                        raise Wp0Error(f"edges.json: {key} is declared twice")
                    claimed.add(key)
            groups.append(group)
        overlap = sorted(
            set(targets)
            & ({key.split("::")[3] for key in declared}
               | {t for group in groups for t in group["targets"]})
        )
        if overlap:
            raise Wp0Error(f"targets both inert and declared: {overlap[:5]}")
        return cls(
            inert_targets=targets,
            inert_types=types,
            edges=declared,
            digest=sha256_bytes(inert_raw + b"\0" + edges_raw),
            groups=groups,
        )

    def declarations_of(self, modules: Iterable[str], kind: str) -> list[dict[str, Any]]:
        """Declarations of *kind* whose site is in one of *modules*."""
        wanted = set(modules)
        return [
            entry for key, entry in sorted(self.edges.items())
            if entry["kind"] == kind and key.split("::")[0] in wanted
        ] + [group for group in self.groups if group["kind"] == kind and group["module"] in wanted]


# --- the mypy build -------------------------------------------------------------


@dataclass
class Analysis:
    root: Path
    modules: dict[str, str]  # repository path -> mypy module name
    graph: Any
    types: dict[Expression, Type]
    unreachable_lines: dict[str, set[int]]  # repository path -> reported lines
    python_version: str
    errors: list[str] = field(default_factory=list)
    #: id() of every function, class and decorator node defined in a closure
    #: module: a reference to one is a repository target (its body is scanned),
    #: whatever its fully qualified name looks like (mypy names a nested
    #: function by its bare name).
    repo_definitions: set[int] = field(default_factory=set)
    #: Plain (undecorated, non-method) repository functions by (path, def line).
    functions: dict[tuple[str, int], FuncDef] = field(default_factory=dict)
    #: id(function) -> its call sites in the closure: (caller path, call, the
    #: function the call is in, or None at module level).
    calls: dict[int, list[tuple[str, CallExpr, FuncDef | None]]] = field(default_factory=dict)
    #: id() of functions defined directly in a class body (methods).
    methods: set[int] = field(default_factory=set)
    #: Plain (undecorated) methods: id -> (path, def line, owner class, name).
    method_defs: dict[int, tuple[str, int, TypeInfo, str]] = field(default_factory=dict)
    #: id(method) -> its resolved call sites: (caller path, call).
    method_calls: dict[int, list[tuple[str, CallExpr]]] = field(default_factory=dict)
    #: Every class defined in an analysed module.
    classes: list[TypeInfo] = field(default_factory=list)
    #: id() of repository functions referenced other than by a direct call.
    escapes: set[int] = field(default_factory=set)
    origins: dict[str, JsonOrigins] = field(default_factory=dict)
    oracle: Any = None


_METHOD_DEFS: dict[int, tuple[str, int, TypeInfo, str]] | None = None
_CLASSES: list[TypeInfo] | None = None


def _collect_definitions(
    statements: Sequence[Node],
    into: set[int],
    functions: dict[tuple[str, int], FuncDef] | None = None,
    path: str = "",
    in_class: bool = False,
    methods: set[int] | None = None,
) -> None:
    for statement in statements:
        if isinstance(statement, FuncDef):
            into.add(id(statement))
            if functions is not None and not in_class:
                functions[(path, statement.line)] = statement
            if in_class and methods is not None:
                methods.add(id(statement))
                if _METHOD_DEFS is not None and statement.info is not None:
                    _METHOD_DEFS[id(statement)] = (
                        path, statement.line, statement.info, statement.name
                    )
            _collect_definitions(statement.body.body, into, functions, path, False, methods)
        elif isinstance(statement, Decorator):
            into.update((id(statement), id(statement.func), id(statement.var)))
            if methods is not None:
                methods.add(id(statement.func))  # decorated: never a JSON candidate
            _collect_definitions(statement.func.body.body, into, functions, path, False, methods)
        elif isinstance(statement, OverloadedFuncDef):
            into.add(id(statement))
            _collect_definitions(list(statement.items), into, functions, path, in_class, methods)
            if statement.impl is not None:
                _collect_definitions([statement.impl], into, functions, path, in_class, methods)
        elif type(statement).__name__ == "ClassDef":
            into.update((id(statement), id(statement.info)))  # type: ignore[attr-defined]
            if _CLASSES is not None:
                _CLASSES.append(statement.info)  # type: ignore[attr-defined]
            _collect_definitions(
                statement.defs.body,  # type: ignore[attr-defined]
                into, functions, path, True, methods,
            )
        else:
            for attr in ("body", "else_body", "handlers", "finally_body", "bodies"):
                value = getattr(statement, attr, None)
                blocks = value if isinstance(value, list) else [value]
                for block in blocks:
                    if isinstance(block, Block):
                        _collect_definitions(block.body, into, functions, path, in_class, methods)


def mypy_module_name(path: str) -> str:
    """The module name mypy analyses *path* under (unique across the repository)."""
    if path.startswith("src/"):
        dotted = path[len("src/") : -len(".py")].replace("/", ".")
        return dotted[: -len(".__init__")] if dotted.endswith(".__init__") else dotted
    stem = Path(path).stem
    if stem.isidentifier():
        return stem
    return "_wp0script_" + "".join(c if c.isalnum() else "_" for c in stem)


def analyse(root: Path, paths: Iterable[str], python_version: str) -> Analysis:
    """Run mypy once over *paths* with the controller's platform, preserving ASTs."""
    paths = sorted(set(paths))
    names: dict[str, str] = {}
    for path in paths:
        name = mypy_module_name(path)
        if name in names.values():
            other = next(p for p, n in names.items() if n == name)
            raise Wp0Error(f"{path} and {other} would both be analysed as {name}")
        names[path] = name
    script_dirs = sorted({str(Path(p).parent) for p in paths if not p.startswith("src/")})
    out, err = io.StringIO(), io.StringIO()
    _sources, options = process_options(
        [
            "--strict",
            "--check-untyped-defs",
            "--warn-unreachable",
            "--platform",
            CONTROLLER_PLATFORM,
            "--python-version",
            python_version,
            "--no-incremental",
            "--cache-dir",
            os.devnull,
            "--follow-imports",
            "normal",
            "--config-file",
            str(LISTS_DIR / "mypy-scan.ini"),
        ],
        stdout=out,
        stderr=err,
        require_targets=False,
    )
    options.preserve_asts = True
    options.export_types = True
    options.mypy_path = [str(root / "src"), *(str(root / d) for d in script_dirs)]
    sources = [
        BuildSource(str(root / path), names[path], None, base_dir=str((root / path).parent))
        for path in paths
    ]
    cwd = os.getcwd()
    os.chdir(root)
    try:
        result = build.build(
            sources, options, stdout=out, stderr=err, extra_plugins=[ArgparsePlugin(options)]
        )
    except Exception as exc:  # mypy's CompileError: a parse error or a blocking error
        raise Wp0Error(f"mypy could not analyse the closure: {exc}") from exc
    finally:
        os.chdir(cwd)
    unreachable: dict[str, set[int]] = {path: set() for path in paths}
    by_file = {str((root / path).resolve()): path for path in paths}
    for file, infos in result.manager.errors.error_info_map.items():
        repo_path = by_file.get(str(Path(root, file).resolve()))
        if repo_path is None:
            continue
        for info in infos:
            if info.code is not None and info.code.code == "unreachable":
                unreachable[repo_path].add(info.line)
    global _METHOD_DEFS, _CLASSES
    definitions: set[int] = set()
    functions: dict[tuple[str, int], FuncDef] = {}
    methods: set[int] = set()
    method_defs: dict[int, tuple[str, int, TypeInfo, str]] = {}
    classes: list[TypeInfo] = []
    _METHOD_DEFS, _CLASSES = method_defs, classes
    try:
        for path, name in names.items():
            state = result.graph.get(name)
            if state is not None and state.tree is not None:
                _collect_definitions(state.tree.defs, definitions, functions, path, False, methods)
    finally:
        _METHOD_DEFS, _CLASSES = None, None
    return Analysis(
        root=root,
        modules=names,
        graph=result.graph,
        types=result.types,
        unreachable_lines=unreachable,
        python_version=python_version,
        errors=list(result.errors),
        repo_definitions=definitions,
        functions=functions,
        methods=methods,
        method_defs=method_defs,
        classes=classes,
    )


def assert_complete(analysis: Analysis) -> list[str]:
    """Problems that make the scan's answer for a module untrustworthy (empty = complete)."""
    problems: list[str] = []
    ignored = analysis.graph and next(iter(analysis.graph.values())).manager.errors.ignored_files
    for path, name in sorted(analysis.modules.items()):
        state = analysis.graph.get(name)
        if state is None:
            problems.append(f"{path}: not in mypy's build graph")
            continue
        if state.path is None or Path(state.path).resolve() != (analysis.root / path).resolve():
            problems.append(f"{path}: mypy analysed {state.path} as {name}")
            continue
        if state.tree is None or not isinstance(state.tree, MypyFile):
            problems.append(f"{path}: no tree (skipped, or not processed)")
            continue
        if state.tree.is_stub or state.tree.is_cache_skeleton:
            problems.append(f"{path}: analysed as a stub or cache skeleton")
        if not state.tree.names:
            problems.append(f"{path}: no symbol table")
        if ignored and state.xpath in ignored:
            problems.append(f"{path}: hidden by a file-level '# type: ignore'")
        if _has_file_level_ignore(analysis.root / path):
            problems.append(f"{path}: carries a file-level '# type: ignore'")
        if getattr(state, "ignore_all", False):
            problems.append(f"{path}: analysed with errors ignored (follow_imports=silent)")
    return problems


def _has_file_level_ignore(path: Path) -> bool:
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#!") or "coding" in line and line.startswith("#"):
            if "type:" in line and "ignore" in line:
                return True
            continue
        if line.startswith("#"):
            if line.replace(" ", "").startswith("#type:ignore"):
                return True
            continue
        return False
    return False


# --- platform guards ----------------------------------------------------------------


def _guard_value(expr: Expression) -> bool | None:
    """The value of a `sys.platform`/`os.name` guard on the controller, or None."""
    if isinstance(expr, UnaryExpr) and expr.op == "not":
        inner = _guard_value(expr.expr)
        return None if inner is None else not inner
    if isinstance(expr, OpExpr) and expr.op in ("and", "or"):
        left, right = _guard_value(expr.left), _guard_value(expr.right)
        # short-circuit: `False and X` and `True or X` decide without X
        if expr.op == "and" and (left is False or right is False):
            return False
        if expr.op == "or" and (left is True or right is True):
            return True
        if left is None or right is None:
            return None
        return (left and right) if expr.op == "and" else (left or right)
    if isinstance(expr, ComparisonExpr) and len(expr.operators) == 1:
        a, b = expr.operands
        op = expr.operators[0]
        subject = _platform_subject(a)
        literal = b
        if subject is None:
            subject, literal = _platform_subject(b), a
        if subject is None or not isinstance(literal, StrExpr) or op not in ("==", "!="):
            return None
        actual = CONTROLLER_PLATFORM if subject == "sys.platform" else CONTROLLER_OS_NAME
        return (actual == literal.value) if op == "==" else (actual != literal.value)
    if (
        isinstance(expr, CallExpr)
        and isinstance(expr.callee, MemberExpr)
        and expr.callee.name == "startswith"
        and _platform_subject(expr.callee.expr) == "sys.platform"
        and len(expr.args) == 1
        and isinstance(expr.args[0], StrExpr)
    ):
        return CONTROLLER_PLATFORM.startswith(expr.args[0].value)
    return None


def _platform_subject(expr: Expression) -> str | None:
    if isinstance(expr, MemberExpr) and isinstance(expr.expr, NameExpr):
        dotted = f"{expr.expr.name}.{expr.name}"
        if expr.fullname in ("sys.platform", "os.name") or dotted in ("sys.platform", "os.name"):
            return "sys.platform" if expr.name == "platform" else "os.name"
    return None


def _guard_text(expr: Expression, value: bool) -> str:
    def text(e: Expression) -> str:
        if isinstance(e, UnaryExpr) and e.op == "not":
            return f"not ({text(e.expr)})"
        if isinstance(e, OpExpr):
            return f"({text(e.left)} {e.op} {text(e.right)})"
        if isinstance(e, ComparisonExpr):
            return f" {e.operators[0]} ".join(text(o) for o in e.operands)
        if isinstance(e, MemberExpr):
            return f"{text(e.expr)}.{e.name}"
        if isinstance(e, NameExpr):
            return e.name
        if isinstance(e, StrExpr):
            return repr(e.value)
        if isinstance(e, CallExpr):
            return f"{text(e.callee)}({', '.join(text(a) for a in e.args)})"
        return type(e).__name__

    return f"{text(expr)} is {value} on {CONTROLLER_PLATFORM}/{CONTROLLER_OS_NAME}"


def _terminates(block: Block) -> bool:
    from mypy.nodes import BreakStmt, ContinueStmt, RaiseStmt, ReturnStmt

    return bool(block.body) and isinstance(
        block.body[-1], (RaiseStmt, ReturnStmt, BreakStmt, ContinueStmt)
    )


# --- type helpers ------------------------------------------------------------------------


def _infos(typ: Type | None) -> list[TypeInfo] | None:
    """The classes a value of *typ* may be an instance of; None when unknown (Any)."""
    if typ is None:
        return None
    proper = get_proper_type(typ)
    if isinstance(proper, Instance):
        return [proper.type]
    if isinstance(proper, UnionType):
        found: list[TypeInfo] = []
        for item in proper.items:
            sub = _infos(item)
            if sub is None:
                return None
            found.extend(sub)
        return found
    if isinstance(proper, (TypeVarType, ParamSpecType, TypeVarTupleType)):
        return _infos(proper.upper_bound)
    if isinstance(proper, TupleType):
        return [proper.partial_fallback.type]
    if isinstance(proper, TypedDictType):
        return [proper.fallback.type]
    if isinstance(proper, LiteralType):
        return [proper.fallback.type]
    if isinstance(proper, (CallableType, Overloaded)):
        return [proper.fallback.type]
    if isinstance(proper, NoneType):
        return []  # an attribute of None is an AttributeError, never a call
    if isinstance(proper, UninhabitedType):
        return []  # the expression never produces a value
    return None


def _class_object_info(typ: Type | None) -> TypeInfo | None:
    """For an expression that is a class object, its TypeInfo."""
    if typ is None:
        return None
    proper = get_proper_type(typ)
    if isinstance(proper, CallableType) and proper.is_type_obj():
        return proper.type_object()
    if isinstance(proper, Overloaded) and proper.is_type_obj():
        return proper.type_object()
    if isinstance(proper, TypeType) and isinstance(proper.item, Instance):
        return proper.item.type
    return None


#: Abstract bases and mixins whose methods typeshed declares once for many
#: concrete classes. A method found on one of these is named after the last
#: concrete class before it in the receiver's MRO: at run time that class (or
#: its C implementation) is what runs, so `dict.update`, `BytesIO.read` or a
#: file's `__exit__` is classified as itself rather than as
#: `MutableMapping.update` or `_IOBase.__exit__` shared with every other
#: subclass. A receiver typed by the abstract class itself keeps the abstract
#: name (its concrete class is unknown).
_ABSTRACT_PREFIXES = ("typing.", "collections.abc.", "_collections_abc.", "_typeshed.")
_ABSTRACT_CLASSES = frozenset({
    "contextlib.AbstractContextManager", "contextlib.AbstractAsyncContextManager",
    "_io._IOBase", "_io._RawIOBase", "_io._BufferedIOBase", "_io._TextIOBase",
    "io.IOBase", "io.RawIOBase", "io.BufferedIOBase", "io.TextIOBase",
})


def _abstract(info: TypeInfo) -> bool:
    return info.fullname.startswith(_ABSTRACT_PREFIXES) or info.fullname in _ABSTRACT_CLASSES


def _lookup(info: TypeInfo, name: str) -> tuple[TypeInfo, SymbolNode | None] | None:
    concrete: TypeInfo | None = None
    for base in info.mro:
        if name in base.names:
            if concrete is not None and _abstract(base):
                return concrete, base.names[name].node
            return base, base.names[name].node
        if not _abstract(base) and base.fullname != _OBJECT:
            concrete = base
    return None


def _is_property(node: SymbolNode | None) -> bool:
    if isinstance(node, Decorator):
        return node.func.is_property
    if isinstance(node, (FuncDef, OverloadedFuncDef)):
        return node.is_property
    return False


def _call_arguments(call: CallExpr) -> list[tuple[Expression, int | None, str | None]]:
    """(argument, position, keyword) of a call's ordinary arguments."""
    arguments: list[tuple[Expression, int | None, str | None]] = []
    position = 0
    for arg, kind, keyword in zip(call.args, call.arg_kinds, call.arg_names, strict=True):
        if kind in (ARG_POS, ARG_OPT):
            arguments.append((arg, position, None))
            position += 1
        elif kind in (ARG_NAMED, ARG_NAMED_OPT):
            arguments.append((arg, None, keyword))
    return arguments


def _member_types(typ: Type | None, which: str) -> list[Type]:
    """The element (or, for a mapping, key) types, or the value types, of *typ*."""
    if typ is None:
        return []
    proper = get_proper_type(typ)
    if isinstance(proper, UnionType):
        return [m for item in proper.items for m in _member_types(item, which)]
    if isinstance(proper, TupleType):
        return list(proper.items) if which == "elements" else []
    if isinstance(proper, Instance):
        from mypy.maptype import map_instance_to_supertype

        wanted = "typing.Mapping" if which == "values" else "typing.Iterable"
        for base in proper.type.mro:
            if base.fullname == wanted:
                mapped = map_instance_to_supertype(proper, base)
                index = 1 if which == "values" else 0
                return [mapped.args[index]] if len(mapped.args) > index else []
    return []


def _item_type(typ: Type | None) -> Type | None:
    """The element type of an iterable, when mypy can name it."""
    if typ is None:
        return None
    proper = get_proper_type(typ)
    if isinstance(proper, Instance):
        for base in proper.type.mro:
            if base.fullname == "typing.Iterable":
                from mypy.maptype import map_instance_to_supertype

                mapped = map_instance_to_supertype(proper, base)
                return mapped.args[0] if mapped.args else None
    if isinstance(proper, TupleType) and proper.items:
        return proper.items[0]
    return None


# --- the walker ----------------------------------------------------------------------------


_CHILDREN: dict[str, tuple[str, ...]] = {
    "ExpressionStmt": ("expr",),
    "ReturnStmt": ("expr",),
    "RaiseStmt": ("expr", "from_expr"),
    "YieldExpr": ("expr",),
    "AwaitExpr": ("expr",),
    "AssignmentExpr": ("value",),
    "SliceExpr": ("begin_index", "end_index", "stride"),
    "ConditionalExpr": ("if_expr", "else_expr"),
    "ListExpr": ("items",),
    "TupleExpr": ("items",),
    "SetExpr": ("items",),
    "TypeApplication": ("expr",),
    "TemplateStrExpr": ("items",),
    "CastExpr": ("expr",),
    "AssertTypeExpr": ("expr",),
}
_LEAVES = frozenset(
    {
        "StrExpr", "BytesExpr", "IntExpr", "FloatExpr", "ComplexExpr", "EllipsisExpr",
        "PassStmt", "BreakStmt", "ContinueStmt", "GlobalDecl", "NonlocalDecl", "TempNode",
        "Import", "ImportFrom", "ImportAll",
    }
)


class Scanner:
    """Walks one module and produces its sites."""

    def __init__(self, analysis: Analysis, path: str, repo_modules: set[str]) -> None:
        self.analysis = analysis
        self.path = path
        self.name = analysis.modules[path]
        state = analysis.graph[self.name]
        self.tree: MypyFile = state.tree
        self.types = analysis.types
        self.repo_modules = repo_modules
        self.unreachable_lines = analysis.unreachable_lines.get(path, set())
        self.scope: list[str] = []
        self.guard: str | None = None  # platform guard text when inside a false branch
        self.type_unreachable = False
        self.class_stack: list[TypeInfo] = []
        self.function_stack: list[FuncDef] = []
        self.sites: list[Site] = []
        self.counts: dict[tuple[str, str, str], int] = {}
        #: id() of class references a reviewed consumer judges (a callback slot, a
        #: comparison): they are not "used as a value" (review round 4).
        self.consumed: set[int] = set()
        if path not in analysis.origins:
            analysis.origins[path] = JsonOrigins.for_file(
                analysis.root / path, path, JsonParameters.of(analysis)
            )
        self.json = analysis.origins[path]
        self.repo_definitions = analysis.repo_definitions

    def is_json(self, expr: Expression | None) -> bool:
        """Does *expr* provably hold a value json.loads produced (see json_origin)?"""
        if expr is None:
            return False
        return self.json.at(expr.line, expr.column, expr.end_line, expr.end_column)

    # -- recording --------------------------------------------------------------

    def _scope_name(self) -> str:
        return ".".join(self.scope) or "<module>"

    def record(
        self,
        kind: str,
        target: str,
        line: int,
        detail: str = "",
        subject: Expression | None = None,
        json_kind: str = "",
    ) -> None:
        scope = self._scope_name()
        guard = self.guard
        if guard is None and self.type_unreachable:
            kind, detail = "unresolved", f"{detail} (in code mypy reports unreachable)".strip()
            subject = None
        span = None
        if subject is not None and subject.end_line is not None and subject.end_column is not None:
            span = (subject.line, subject.column, subject.end_line, subject.end_column)
        self.sites.append(
            Site(self.path, scope, kind, target, detail, line, 0, guard, span, json_kind)
        )

    def is_repo_target(self, fullname: str) -> bool:
        module = fullname
        while module:
            if module in self.repo_modules:
                return True
            module = module.rpartition(".")[0]
        return False

    def target(self, kind: str, fullname: str, line: int, detail: str = "") -> None:
        if self.guard is None and self.is_repo_target(fullname):
            return
        if (kind == "reference" and self.guard is None and fullname in RUNS_OTHER_CODE
                and detail not in ("class used as a value", "alias used as a value")):
            # Review round 3 (Sol B1): the protocol rules apply where a call names
            # its target; a callable that runs other objects' code, taken as a
            # value (`remove = rows.remove`, `key=len`), would run wherever it is
            # called, out of their sight. Fail closed here. A class taken as a
            # value (`default_factory=tuple`, `type(x) is int`) fails where it is
            # called back or called through a variable instead (callback_argument,
            # indirect).
            self.unresolved(
                f"<value of {fullname}>", line,
                f"{fullname}, which runs other objects' code, taken as a value",
            )
            return
        self.record(kind, fullname, line, detail)

    def unresolved(
        self,
        what: str,
        line: int,
        detail: str = "",
        subject: Expression | None = None,
        json_kind: str = "",
    ) -> None:
        self.record("unresolved", what, line, detail, subject, json_kind)

    # -- type queries -----------------------------------------------------------

    def type_of(self, expr: Expression) -> Type | None:
        return self.types.get(expr)

    def implicit(
        self,
        typ: Type | None,
        dunder: str,
        line: int,
        what: str,
        subject: Expression | None = None,
    ) -> None:
        """Resolve protocol method *dunder* on a value of *typ* and classify it."""
        if self.guard is not None:
            self.record("implicit", f"?.{dunder}", line, what)
            return
        infos = _infos(typ)
        if infos is None:
            if self.is_json(subject):
                self.record("implicit", f"<json>.{dunder}", line, f"{what} on a JSON value")
                return
            self.unresolved(
                f"<Any>.{dunder}", line, f"{what} on a value of unknown type", subject, "implicit"
            )
            return
        for info in infos:
            found = _lookup(info, dunder)
            if found is None:
                continue
            owner, _node = found
            if owner.fullname == _OBJECT:
                continue
            self.target("implicit", f"{owner.fullname}.{dunder}", line, what)

    def truth(self, expr: Expression) -> None:
        typ = self.type_of(expr)
        if typ is None:
            if self.guard is None and not self.type_unreachable:
                self.unresolved("<untyped>.__bool__", expr.line, "truth test of an untyped value")
            return
        infos = _infos(typ)
        if infos is None:
            if self.is_json(expr):
                self.record("implicit", "<json>.__bool__", expr.line, "truth test of a JSON value")
                return
            self.unresolved(
                "<Any>.__bool__", expr.line, "truth test of a value of unknown type", expr,
                "implicit",
            )
            return
        for info in infos:
            for dunder in ("__bool__", "__len__"):
                found = _lookup(info, dunder)
                if found and found[0].fullname != _OBJECT:
                    target = f"{found[0].fullname}.{dunder}"
                    self.target("implicit", target, expr.line, "truth test")
                    break

    def iterate(self, expr: Expression, line: int, what: str = "iteration") -> None:
        typ = self.type_of(expr)
        if typ is None:
            if self.guard is None and not self.type_unreachable:
                self.unresolved("<untyped>.__iter__", line, what)
            return
        self.implicit(typ, "__iter__", line, what, subject=expr)
        infos = _infos(typ)
        if not infos:
            return
        from mypy.subtypes import find_member

        proper = get_proper_type(typ)
        if isinstance(proper, Instance):
            member = find_member("__iter__", proper, proper)
            member = get_proper_type(member) if member is not None else None
            if isinstance(member, CallableType):
                self.implicit(member.ret_type, "__next__", line, what)

    def dunder_names(self, typ: Type | None, dunder: str) -> list[str]:
        """The targets `implicit` would name for *dunder* on a value of *typ*."""
        out: list[str] = []
        for info in _infos(typ) or []:
            found = _lookup(info, dunder)
            if found is not None and found[0].fullname != _OBJECT:
                out.append(f"{found[0].fullname}.{dunder}")
        return out

    def protocols(
        self,
        names: Iterable[str],
        receiver: Expression | None,
        args: Sequence[tuple[Expression, int | None, str | None]],
        line: int,
        extra: Protocol | None = None,
    ) -> None:
        """Resolve the protocol methods a call of *names* runs on other objects
        (`METHOD_PROTOCOLS`); *extra* applies a rule regardless of the name."""
        if self.guard is not None or self.type_unreachable:
            return
        specs = [(name, METHOD_PROTOCOLS[name]) for name in dict.fromkeys(names)
                 if name in METHOD_PROTOCOLS]
        if extra is not None:
            specs.append(("a method of a value of unknown type", extra))
        for name, spec in specs:
            what = f"{name} runs it"
            for expr, position, keyword in args:
                for dunder in spec.for_argument(position, keyword):
                    if dunder in ("__call__", "__call0__"):
                        self.callback_argument(expr, line, what,
                                               no_arguments=dunder == "__call0__")
                    elif dunder.startswith("?"):
                        # Only an unknown value is a site: a typed stream's write
                        # goes where its (classified) opening said.
                        if isinstance(get_proper_type(self.type_of(expr)), AnyType):
                            self.unresolved(f"<Any>.{dunder[1:]}", line,
                                            f"{what} on a value of unknown type")
                    else:
                        self.implicit(self.type_of(expr), dunder, line, what, expr)
                if position == 0:
                    self.member_protocols(expr, "elements", spec.arg_elements, line, what)
                    self.member_protocols(expr, "values", spec.arg_values, line, what)
            if receiver is not None:
                self.member_protocols(receiver, "elements", spec.elements, line, what)
                self.member_protocols(receiver, "values", spec.values, line, what)

    def callback_argument(
        self, expr: Expression, line: int, what: str, *, no_arguments: bool = False
    ) -> None:
        """An argument the callee calls back (a sort key, a replacement, a hook).

        Visible code passes: a lambda (its body is scanned), or a direct reference
        to a function, method or class (the reference is classified where it
        stands, and fails if it runs other objects' code). A value that is not
        callable (a `str` replacement, `None`) calls nothing. Anything else (a
        callable held in a variable, a parameter, an attribute or a call result)
        fails closed: the scan cannot see what it will run (review round 3).
        """
        if isinstance(expr, LambdaExpr):
            return
        if isinstance(expr, RefExpr) and isinstance(
            expr.node, (FuncBase, Decorator, TypeInfo, OverloadedFuncDef)
        ):
            if expr.fullname in RUNS_OTHER_CODE and not no_arguments:
                # `map(str, rows)`, `key=len`: called back on values the callee
                # chooses, out of the protocol rules' sight.
                self.unresolved(f"<value of {expr.fullname}>", line,
                                f"{what}: calls back {expr.fullname}, which runs other "
                                "objects' code")
            return
        if isinstance(expr, MemberExpr):
            # `str.casefold`, `obj.method`: a method looked up on a typed class or
            # instance is a reference the scan classifies where it stands.
            receiver = self.type_of(expr.expr)
            owners = [cls] if (cls := _class_object_info(receiver)) is not None else (
                _infos(receiver) or []
            )
            found_all = [_lookup(owner, expr.name) for owner in owners]
            if found_all and all(
                found is not None and isinstance(found[1], (FuncBase, Decorator))
                for found in found_all
            ):
                return
        typ = self.type_of(expr)
        proper = get_proper_type(typ) if typ is not None else None
        if isinstance(proper, NoneType):
            return
        if proper is not None and not isinstance(proper, (AnyType, CallableType, Overloaded)):
            infos = _infos(typ)
            if infos is not None and not any(
                (found := _lookup(info, "__call__")) is not None
                and found[0].fullname != _OBJECT for info in infos
            ):
                return  # not callable: nothing is called back
        self.unresolved("<callback value>", line,
                        f"{what}: a callback whose code the scan cannot see here")

    def key_results(self, key: Expression, line: int, callee: str) -> None:
        proper = get_proper_type(self.type_of(key)) if self.type_of(key) is not None else None
        if not isinstance(proper, CallableType):
            return  # an unknown key is already a failing callback site
        result = get_proper_type(proper.ret_type)
        items = list(result.items) if isinstance(result, TupleType) else [result]
        for item in items:
            for dunder in ("__lt__", "__eq__"):
                self.implicit(item, dunder, line, f"{callee} compares key results")

    def member_protocols(
        self, expr: Expression, which: str, dunders: Sequence[str], line: int, what: str
    ) -> None:
        """*dunders* on the element (or key) or value types of *expr*'s type."""
        if not dunders:
            return
        typ = self.type_of(expr)
        if typ is None or _infos(typ) is None:
            return  # a value of unknown type: its own site is unresolvable or JSON
        members = _member_types(typ, which)
        if which == "elements" and type(expr).__name__ in (
            "GeneratorExpr", "ListComprehension", "SetComprehension"
        ):
            # mypy types a comprehension from its context (`any(x for x in rows)`
            # is a Generator[object]); the elements are what its expression yields.
            generator: Any = getattr(expr, "generator", expr)  # noqa: B009 - mypyc node
            members = [t for t in [self.type_of(generator.left_expr)] if t is not None]
        for member in members:
            proper = get_proper_type(member)
            if isinstance(proper, Instance) and proper.type.fullname == _OBJECT:
                # An element typed only `object` can be anything at run time.
                for dunder in dunders:
                    self.unresolved(f"<object>.{dunder}", line,
                                    f"{what} on an element typed object")
                continue
            for dunder in dunders:
                self.implicit(member, dunder, line, f"{what} on an element")

    def element_ops(self, expr: Expression, dunders: Sequence[str], line: int) -> None:
        if self.is_json(expr) and _infos(self.type_of(expr)) is None:
            for dunder in dunders:
                self.record("implicit", f"<json>.{dunder}", line, "JSON element comparison")
            return
        item = _item_type(self.type_of(expr))
        if item is None:
            return
        for dunder in dunders:
            self.implicit(item, dunder, line, "element comparison or hashing")

    # -- dispatch -----------------------------------------------------------------

    def scan(self) -> list[Site]:
        self.block(self.tree.defs)
        return self.sites

    def visit(self, node: Node | None) -> None:
        if node is None:
            return
        if isinstance(node, MemberExpr) and isinstance(node.expr, RefExpr) and isinstance(
            node.expr.node, (TypeInfo, TypeAlias)
        ):
            # `str.casefold`, `bytes.fromhex(...)`: the class is the receiver of an
            # attribute lookup, not a value that is called later.
            self.consumed.add(id(node.expr))
        kind = type(node).__name__
        handler = getattr(self, "_" + kind, None)
        if handler is not None:
            handler(node)
            return
        if kind in _LEAVES:
            return
        if kind in _CHILDREN:
            for attr in _CHILDREN[kind]:
                value = getattr(node, attr)
                if isinstance(value, list):
                    for item in value:
                        self.visit(item)
                else:
                    self.visit(value)
            return
        self.record(
            "unresolved", f"<node:{kind}>", node.line, "a construct the scan does not model"
        )

    def visit_all(self, nodes: Iterable[Node | None]) -> None:
        for node in nodes:
            self.visit(node)

    # -- statements ---------------------------------------------------------------

    def block(self, statements: Sequence[Node]) -> None:
        saved_guard, saved_unreachable = self.guard, self.type_unreachable
        for statement in statements:
            if statement.line in self.unreachable_lines and self.guard is None:
                self.type_unreachable = True
            self.visit(statement)
            if (
                isinstance(statement, IfStmt)
                and self.guard is None
                and len(statement.expr) == 1
                and (statement.else_body is None or not statement.else_body.body)
            ):
                value = _guard_value(statement.expr[0])
                if value is True and _terminates(statement.body[0]):
                    # `if <true on the controller>: raise/return` -- the rest of
                    # this block never runs on the controller.
                    self.guard = _guard_text(statement.expr[0], value) + " (and its body exits)"
        self.guard, self.type_unreachable = saved_guard, saved_unreachable

    def _Block(self, node: Block) -> None:
        if node.is_unreachable and not node.body:
            return  # mypy's synthetic empty branch: nothing to run
        if node.is_unreachable and self.guard is None:
            # Unreachable to semantic analysis: a sys.platform or sys.version_info
            # condition, or `if TYPE_CHECKING` false branches. The enclosing IfStmt
            # sets the guard for the platform case; anything else is unanalysed.
            self.record("unresolved", "<unanalysed-block>", node.line, "a block mypy skipped")
            return
        self.block(node.body)

    def _IfStmt(self, node: IfStmt) -> None:
        for expr, body in zip(node.expr, node.body, strict=True):
            value = _guard_value(expr)
            self.visit(expr)
            if value is None:
                self.truth(expr)
                self.visit(body)
                continue
            if value:
                self.visit(body)
            else:
                self._guarded(body, _guard_text(expr, value))
            if value is True:
                # every later branch, and the else, is unreachable on the controller
                rest = [*node.body[node.body.index(body) + 1 :]]
                for later in rest:
                    self._guarded(later, _guard_text(expr, value))
                if node.else_body:
                    self._guarded(node.else_body, _guard_text(expr, value))
                return
        if node.else_body:
            self.visit(node.else_body)

    def _guarded(self, body: Block, guard: str) -> None:
        saved = self.guard
        if self.guard is None:
            self.guard = guard
        saved_unreachable = self.type_unreachable
        self.block(body.body)
        self.guard, self.type_unreachable = saved, saved_unreachable

    def _WhileStmt(self, node: Any) -> None:
        self.visit(node.expr)
        self.truth(node.expr)
        self.visit(node.body)
        self.visit(node.else_body)

    def _ForStmt(self, node: Any) -> None:
        self.visit(node.expr)
        typ = self.type_of(node.expr)
        if typ is None:
            if self.guard is None and not self.type_unreachable:
                self.unresolved("<untyped>.__iter__", node.line, "for loop")
        else:
            self.implicit(typ, "__iter__", node.line, "for loop", subject=node.expr)
            if node.inferred_iterator_type is not None:
                # The iterator of a JSON value (a list or dict iterator) is JSON-side.
                self.implicit(
                    node.inferred_iterator_type, "__next__", node.line, "for loop",
                    subject=node.expr,
                )
        self.store(node.index)
        self.visit(node.body)
        self.visit(node.else_body)

    def _WithStmt(self, node: Any) -> None:
        for expr, target in zip(node.expr, node.target, strict=True):
            self.visit(expr)
            typ = self.type_of(expr)
            self.implicit(typ, "__enter__", node.line, "with", subject=expr)
            self.implicit(typ, "__exit__", node.line, "with", subject=expr)
            if target is not None:
                self.store(target)
        self.visit(node.body)

    def _TryStmt(self, node: Any) -> None:
        self.visit(node.body)
        for types_expr in node.types:
            self.type_test(types_expr)
        for var in node.vars:
            if var is not None:
                self.store(var)
        self.visit_all(node.handlers)
        self.visit(node.else_body)
        self.visit(node.finally_body)

    def _AssertStmt(self, node: Any) -> None:
        self.visit(node.expr)
        self.truth(node.expr)
        self.visit(node.msg)

    def _DelStmt(self, node: Any) -> None:
        self.delete(node.expr)

    def _AssignmentStmt(self, node: Any) -> None:
        if node.is_alias_def:
            # `X = A | B`, `X = Literal[...]`: a type alias built by typing at
            # import (typing's __or__/__getitem__ on typing objects and classes).
            self.target("call", "typing.<type alias construction>", node.line, "type alias")
            return
        self.visit(node.rvalue)
        alias = _module_attribute(node.rvalue)
        if alias in DECLARED_MACHINERY | REFUSED_WRITE_TARGETS:
            self.record("machinery", alias, node.line, "import machinery bound to another name")
        for lvalue in node.lvalues:
            if isinstance(lvalue, (TupleExpr, ListExpr)) and not (
                isinstance(node.rvalue, (TupleExpr, ListExpr))
                and len(node.rvalue.items) == len(lvalue.items)
                and not any(isinstance(i, StarExpr) for i in [*lvalue.items, *node.rvalue.items])
            ):
                # `a, b = x, y` unpacks a display of the same length element by
                # element; any other right-hand side runs the iteration protocol.
                self.iterate(node.rvalue, node.line, "unpacking")
            self.store(lvalue)

    def _OperatorAssignmentStmt(self, node: Any) -> None:
        self.visit(node.lvalue)
        self.visit(node.rvalue)
        typ = self.type_of(node.lvalue)
        op = _OPERATOR_DUNDER.get(node.op)
        if op is None:
            self.unresolved(f"<op:{node.op}>", node.line, "augmented assignment")
        else:
            infos = _infos(typ)
            if infos is None and self.is_json(node.lvalue):
                self.record("implicit", f"<json>.__i{op}__", node.line, "augmented assignment")
            elif infos is None:
                self.unresolved(
                    f"<Any>.__i{op}__", node.line, "augmented assignment", node.lvalue, "implicit"
                )
            else:
                for info in infos:
                    found = _lookup(info, f"__i{op}__") or _lookup(info, f"__{op}__")
                    if found and found[0].fullname != _OBJECT:
                        name = f"__i{op}__" if _lookup(info, f"__i{op}__") else f"__{op}__"
                        self.target("implicit", f"{found[0].fullname}.{name}", node.line, "+=")
                        self.protocols([f"{found[0].fullname}.{name}"], node.lvalue,
                                       [(node.rvalue, 0, None)], node.line)
        self.machinery_write(node.lvalue, node.line)
        self.store(node.lvalue, visit=False)

    def _FuncDef(self, node: FuncDef) -> None:
        for argument in node.arguments:
            self.visit(argument.initializer)  # defaults run at definition time
        self.scope.append(node.name)
        self.function_stack.append(node)
        saved_unreachable = self.type_unreachable
        self.block(node.body.body if not node.body.is_unreachable else [])
        if node.body.is_unreachable and self.guard is None:
            self.record("unresolved", "<unanalysed-block>", node.line, "a function mypy skipped")
        self.type_unreachable = saved_unreachable
        self.function_stack.pop()
        self.scope.pop()

    def _Decorator(self, node: Decorator) -> None:
        for decorator in node.original_decorators:
            self.visit(decorator)
            if isinstance(decorator, RefExpr):
                self.call_target_of_ref(decorator, decorator.line, "decorator")
            else:
                typ = self.type_of(decorator)
                if typ is None or isinstance(get_proper_type(typ), AnyType):
                    self.unresolved("<decorator>", decorator.line, "decorator of unknown type")
        self.visit(node.func)

    def _OverloadedFuncDef(self, node: OverloadedFuncDef) -> None:
        self.visit_all(node.items)
        self.visit(node.impl)

    def _ClassDef(self, node: Any) -> None:
        for decorator in node.decorators:
            self.visit(decorator)
            if isinstance(decorator, RefExpr):
                self.call_target_of_ref(decorator, decorator.line, "class decorator")
        for base in [*node.base_type_exprs, *node.removed_base_type_exprs]:
            self.type_test(base)
        for value in node.keywords.values():
            self.visit(value)
        info: TypeInfo = node.info
        metaclass = info.metaclass_type
        if metaclass is not None and metaclass.type.fullname != "builtins.type":
            self.target("implicit", metaclass.type.fullname, node.line, "class creation")
        for base in info.mro[1:]:
            if "__init_subclass__" in base.names and base.fullname != _OBJECT:
                self.target("implicit", f"{base.fullname}.__init_subclass__", node.line, "class")
                break
        self.scope.append(node.name)
        self.class_stack.append(info)
        self.block(node.defs.body)
        self.class_stack.pop()
        self.scope.pop()

    def _MatchStmt(self, node: Any) -> None:
        self.visit(node.subject)
        subject_type = self.type_of(node.subject)
        for pattern, guard, body in zip(node.patterns, node.guards, node.bodies, strict=True):
            self.pattern(pattern, node.subject, subject_type, node.line)
            if guard is not None:
                self.visit(guard)
                self.truth(guard)
            self.visit(body)

    def pattern(
        self, pattern: Any, subject: Expression, subject_type: Type | None, line: int
    ) -> None:
        """A case pattern. Value, capture/wildcard, or and singleton patterns are
        modelled; any other kind fails (none is used in the closure today)."""
        kind = type(pattern).__name__
        if kind == "AsPattern":
            if pattern.pattern is not None:
                self.pattern(pattern.pattern, subject, subject_type, line)
            return
        if kind == "OrPattern":
            for alternative in pattern.patterns:
                self.pattern(alternative, subject, subject_type, line)
            return
        if kind == "ValuePattern":
            # `case <value>:` compares `subject == value`: the subject's __eq__,
            # then (on NotImplemented) the value's reflected __eq__.
            self.visit(pattern.expr)
            self.implicit(subject_type, "__eq__", line, "case value", subject)
            self.implicit(self.type_of(pattern.expr), "__eq__", line, "case value", pattern.expr)
            return
        if kind == "SingletonPattern":
            return  # `case None:` is an identity test
        self.record("unresolved", f"<pattern:{kind}>", line, "a case pattern not modelled")

    def _TypeAliasStmt(self, node: Any) -> None:
        self.record("unresolved", "<node:TypeAliasStmt>", node.line, "type statement")

    # -- stores and deletes -------------------------------------------------------

    def store(self, lvalue: Expression, *, visit: bool = True) -> None:
        if isinstance(lvalue, (TupleExpr, ListExpr)):
            for item in lvalue.items:
                self.store(item)
            return
        if isinstance(lvalue, StarExpr):
            self.store(lvalue.expr)
            return
        if isinstance(lvalue, NameExpr):
            return
        if isinstance(lvalue, MemberExpr):
            self.visit(lvalue.expr)
            self.machinery_write(lvalue, lvalue.line)
            self.member_access(lvalue, setter=True)
            return
        if isinstance(lvalue, IndexExpr):
            self.visit(lvalue.base)
            self.visit(lvalue.index)
            self.machinery_write(lvalue.base, lvalue.line)
            self.item_protocols(lvalue.base, lvalue.index, "__setitem__", lvalue.line)
            self.implicit(
                self.type_of(lvalue.base), "__setitem__", lvalue.line, "item store", lvalue.base
            )
            return
        if visit:
            self.visit(lvalue)

    def item_protocols(self, base: Expression, index: Expression, dunder: str, line: int) -> None:
        unknown = isinstance(get_proper_type(self.type_of(base)), AnyType)
        self.protocols(self.dunder_names(self.type_of(base), dunder), base, [(index, 0, None)],
                       line, _ANY_RECEIVER_ARGS if unknown else None)

    def delete(self, expr: Expression) -> None:
        if isinstance(expr, TupleExpr):
            for item in expr.items:
                self.delete(item)
        elif isinstance(expr, IndexExpr):
            self.visit(expr.base)
            self.visit(expr.index)
            self.machinery_write(expr.base, expr.line)
            self.item_protocols(expr.base, expr.index, "__delitem__", expr.line)
            self.implicit(
                self.type_of(expr.base), "__delitem__", expr.line, "item delete", expr.base
            )
        elif isinstance(expr, MemberExpr):
            self.visit(expr.expr)
            self.machinery_write(expr, expr.line)
        elif not isinstance(expr, NameExpr):
            self.visit(expr)

    def machinery_write(self, expr: Expression, line: int) -> None:
        """A store into, or a mutating call on, an import-machinery object."""
        name = _module_attribute(expr)
        if name is None:
            if isinstance(expr, MemberExpr) and _module_name(expr.expr) == "builtins":
                self.record("machinery", "builtins", line, "write to the builtins module")
            return
        if name in REFUSED_WRITE_TARGETS:
            self.record("machinery", name, line, "write to an import hook list (refused)")
        elif name in DECLARED_MACHINERY:
            self.record("machinery", name, line, "change to the import path or module table")

    # -- expressions ----------------------------------------------------------------

    def _NameExpr(self, node: NameExpr) -> None:
        self.reference(node)

    def _MemberExpr(self, node: MemberExpr) -> None:
        self.visit(node.expr)
        if node.kind is not None and node.fullname and isinstance(node.node, SymbolNode):
            # a module attribute (`os.path.join`, `sys.argv`)
            if node.fullname in REFUSED_ATTRIBUTE_READS:
                self.record("attribute-read", node.fullname, node.line, "refused read")
                return
            if node.fullname in DECLARED_MACHINERY | REFUSED_WRITE_TARGETS:
                return  # a read; mutation and aliasing are caught where they happen
            if isinstance(node.node, Var) and not self.is_repo_target(node.fullname):
                # An object read from another module (`os.environ`, `sys.stdout`):
                # not a call, but whatever it is reaches the code that receives
                # it, so it is classified like a call target.
                self.target("attribute-read", node.fullname, node.line, "module attribute")
                return
            self.reference(node)
            return
        if _module_name(node.expr) is not None and not node.fullname:
            self.unresolved(f"{_module_name(node.expr)}.{node.name}", node.line, "unknown attr")
            return
        self.member_access(node)

    def reference(self, node: RefExpr) -> None:
        """A name used as a value: a callable reference unless it is data or a module."""
        target = node.node
        if target is not None and id(target) in self.repo_definitions:
            self.analysis.escapes.add(id(target))
            return
        if isinstance(target, (FuncBase, Decorator)):
            self.target("reference", node.fullname, node.line, "function used as a value")
        elif isinstance(target, TypeInfo):
            if self.class_value_fails(node, target.fullname):
                return
            self.target("reference", target.fullname, node.line, "class used as a value")
        elif isinstance(target, TypeAlias):
            aliased = get_proper_type(target.target)
            if isinstance(aliased, Instance):
                if self.class_value_fails(node, aliased.type.fullname):
                    return
                self.target("reference", aliased.type.fullname, node.line, "alias used as a value")
        elif isinstance(target, Var) and node.fullname in REFUSED_ATTRIBUTE_READS:
            self.record("attribute-read", node.fullname, node.line, "refused read")
        elif (
            target is None
            and node.fullname == ""
            and self.guard is None
            and isinstance(node, NameExpr)
            and not self.type_unreachable
            and self.type_of(node) is None
        ):
            self.unresolved(f"<name:{node.name}>", node.line, "a name mypy did not bind")

    def class_value_fails(self, node: RefExpr, fullname: str) -> bool:
        """A class whose constructor runs other objects' code, taken as a value and
        not immediately called, fails closed (review round 4, Sol B3: widened
        `factory: Callable[[Any], object] = list`, `type[object]`, conditionals)."""
        if self.guard is not None or fullname not in RUNS_OTHER_CODE or id(node) in self.consumed:
            return False
        self.unresolved(f"<value of {fullname}>", node.line,
                        f"{fullname}, whose constructor runs other objects' code, taken as a "
                        "value")
        return True

    def member_access(self, node: MemberExpr, *, setter: bool = False) -> None:
        """`x.attr` on an instance or class: a property, descriptor or method reference."""
        if self.guard is not None:
            self.record("reference", f"?.{node.name}", node.line, "attribute in guarded code")
            return
        receiver = self.type_of(node.expr)
        if receiver is None:
            if not self.type_unreachable:
                self.unresolved(
                    f"<untyped>.{node.name}", node.line, "attribute of an untyped value"
                )
            return
        cls = _class_object_info(receiver)
        if cls is not None:
            found = _lookup(cls, node.name)
            if found is None:
                found = _lookup_metaclass(cls, node.name)
            if found is None:
                self.unresolved(f"{cls.fullname}.{node.name}", node.line, "no such class attribute")
                return
            owner, member = found
            if isinstance(member, (FuncBase, Decorator)):
                self.target("reference", f"{owner.fullname}.{node.name}", node.line, "class method")
            return
        infos = _infos(receiver)
        if infos is None:
            # Not proved JSON even when it is: a JSON value's method taken as a
            # value (`get = data.get`) would hash and compare whatever it is
            # later called with (review round 3).
            self.unresolved(
                f"<Any>.{node.name}", node.line, "attribute of a value of unknown type",
                node.expr,
            )
            return
        for info in infos:
            found = _lookup(info, node.name)
            if found is None:
                getattr_hook = _lookup(info, "__getattr__")
                if getattr_hook and getattr_hook[0].fullname != _OBJECT:
                    self.target("implicit", f"{getattr_hook[0].fullname}.__getattr__", node.line,
                                f".{node.name}")
                continue
            owner, member = found
            name = f"{owner.fullname}.{node.name}"
            if _is_property(member):
                self.target("implicit", name, node.line, "property set" if setter else "property")
            elif isinstance(member, (FuncBase, Decorator)) and not setter:
                self.target("reference", name, node.line, "bound method used as a value")
            elif isinstance(member, Var) and member.type is not None:
                attr = get_proper_type(member.type)
                if isinstance(attr, Instance) and member.is_initialized_in_class:
                    dunder = "__set__" if setter else "__get__"
                    descriptor = _lookup(attr.type, dunder)
                    if descriptor and descriptor[0].fullname != _OBJECT:
                        target = f"{descriptor[0].fullname}.{dunder}"
                        self.target("implicit", target, node.line, "descriptor")

    def type_test(self, expr: Expression, *, report_any: bool = True) -> None:
        """A class in a type-test position (isinstance, except, a base list): no call."""
        if isinstance(expr, (TupleExpr, ListExpr)):
            for item in expr.items:
                self.type_test(item)
            return
        if isinstance(expr, NameExpr) and isinstance(expr.node, (TypeInfo, TypeAlias)):
            return
        if isinstance(expr, MemberExpr):
            if isinstance(expr.node, (TypeInfo, TypeAlias)):
                return
            if _class_object_info(self.type_of(expr)) is not None and not isinstance(
                expr.node, (FuncBase, Decorator)
            ):
                self.visit(expr.expr)
                return
        if isinstance(expr, IndexExpr):
            # Generic[T], Protocol[...] etc.: __class_getitem__ of the base
            self.visit(expr.index)
            self.type_test(expr.base)
            return
        self.visit(expr)
        if report_any and isinstance(get_proper_type(self.type_of(expr)), AnyType):
            # `except cls:` or a base list naming a class of unknown type runs
            # its metaclass's checks (review round 3, DeepSeek N1).
            self.unresolved("<Any>.__instancecheck__", expr.line,
                            "a type test against a value of unknown type")

    def call_target_of_ref(
        self, ref: RefExpr, line: int, detail: str, call: CallExpr | None = None
    ) -> None:
        """A RefExpr called (or used as a decorator, which calls it)."""
        node = ref.node
        if node is not None and id(node) in self.repo_definitions:
            if call is None:
                self.analysis.escapes.add(id(node))
            else:
                enclosing = self.function_stack[-1] if self.function_stack else None
                self.analysis.calls.setdefault(id(node), []).append((self.path, call, enclosing))
            return
        if isinstance(node, (FuncBase, Decorator)):
            self.target("call", ref.fullname, line, detail)
        elif isinstance(node, TypeInfo):
            self.construct(node, line, detail)
        elif isinstance(node, TypeAlias):
            aliased = get_proper_type(node.target)
            if isinstance(aliased, Instance):
                self.construct(aliased.type, line, detail)
            else:
                self.unresolved(f"<alias:{ref.fullname}>", line, detail)

    def construct(self, info: TypeInfo, line: int, detail: str) -> None:
        self.target("call", info.fullname, line, detail or "constructor")
        found = _lookup(info, "__del__")
        if found and found[0].fullname != _OBJECT:
            self.target("implicit", f"{found[0].fullname}.__del__", line, "finaliser")

    def _CallExpr(self, node: CallExpr) -> None:
        if node.analyzed is not None:
            analyzed = node.analyzed
            special = {
                "CastExpr": "typing.cast",
                "AssertTypeExpr": "typing.assert_type",
                "TypeVarExpr": "typing.TypeVar",
                "ParamSpecExpr": "typing.ParamSpec",
                "TypeVarTupleExpr": "typing.TypeVarTuple",
                "NamedTupleExpr": "typing.NamedTuple",
                "TypedDictExpr": "typing.TypedDict",
                "NewTypeExpr": "typing.NewType",
                "EnumCallExpr": "enum.Enum",
                "DictExpr": "builtins.dict",
            }.get(type(analyzed).__name__)
            if special is None:
                self.unresolved(f"<special:{type(analyzed).__name__}>", node.line, "special form")
                return
            self.target("call", special, node.line, "special form")
            if type(analyzed).__name__ in ("CastExpr", "AssertTypeExpr"):
                self.visit(getattr(analyzed, "expr"))  # noqa: B009 - mypyc node, no common base
            elif type(analyzed).__name__ == "DictExpr":
                self.visit(analyzed)  # dict(a=..., b=...) rewritten by mypy
            return
        callee = node.callee
        line = node.line
        fullname = self.callee_target(callee, node)
        if isinstance(callee, RefExpr) and isinstance(callee.node, TypeAlias):
            aliased = get_proper_type(callee.node.target)
            if isinstance(aliased, Instance):
                # `Factory = list; Factory(xs)`: the rules of the class it names
                # apply (review round 4, Sol B3).
                fullname = aliased.type.fullname
        type_test = fullname in _TYPE_TEST_CALLEES
        if fullname in METHOD_PROTOCOLS:
            spec = METHOD_PROTOCOLS[fullname]
            for arg, position, keyword in _call_arguments(node):
                if isinstance(arg, RefExpr) and {
                    "__call__", "__call0__", "?__subclasscheck__", "?__instancecheck__"
                } & set(spec.for_argument(position, keyword)):
                    # callback_argument judges a callback; a class in a type-test
                    # slot (`contextlib.suppress(OSError)`) is never called.
                    self.consumed.add(id(arg))
        for index, (arg, kind) in enumerate(zip(node.args, node.arg_kinds, strict=True)):
            if type_test and index == 1:
                self.type_test(arg, report_any=False)  # METHOD_PROTOCOLS reports it
            else:
                self.visit(arg)
            if kind == ARG_STAR:
                self.iterate(arg, line, "*args expansion")
            elif kind == ARG_STAR2:
                self.implicit(self.type_of(arg), "keys", line, "**kwargs expansion", arg)
                self.implicit(self.type_of(arg), "__getitem__", line, "**kwargs expansion", arg)
        arguments = _call_arguments(node)
        receiver = callee.expr if isinstance(callee, MemberExpr) else None
        if receiver is not None and isinstance(get_proper_type(self.type_of(receiver)), AnyType):
            # A method of an unknown (or JSON) value: a lookup hashes or compares
            # its arguments whatever the receiver turns out to be.
            self.protocols([], None, arguments, line, _ANY_RECEIVER_ARGS)
        if fullname is None:
            return
        self.protocols([fullname], receiver, arguments, line)
        if fullname not in METHOD_PROTOCOLS and isinstance(callee, RefExpr) and isinstance(
            callee.node, TypeInfo
        ) and any(
            base.fullname == "builtins.BaseException" for base in callee.node.mro
        ):
            # BaseException.__new__ keeps every argument in `args`, and str()/repr()
            # of the exception run their __str__/__repr__: resolved here, where
            # the arguments' types are known.
            self.protocols(["builtins.BaseException.__init__"], None, arguments, line)
        positional = [a for a, k in zip(node.args, node.arg_kinds, strict=True) if k == ARG_POS]
        for dunder_list in (_CONVERSIONS.get(fullname),):
            if dunder_list:
                for arg in positional:
                    for dunder in dunder_list:
                        self.implicit(
                            self.type_of(arg), dunder, line, f"{fullname}() argument", arg
                        )
        if fullname in _ITERATING:
            for arg in positional[_ITERATING[fullname] :]:
                self.iterate(arg, line, f"{fullname}() argument")
                if fullname in _ELEMENT_OPS:
                    self.element_ops(arg, _ELEMENT_OPS[fullname], line)
        if fullname == "builtins.list.sort" and isinstance(callee, MemberExpr):
            self.element_ops(callee.expr, _ELEMENT_OPS[fullname], line)
        if fullname in _KEYED_ORDERING:
            # With `key=`, the key's results are what is compared (review round 3,
            # DeepSeek: `sorted(rows, key=lambda r: (unknown, r))`).
            for arg, _position, keyword in arguments:
                if keyword == "key":
                    self.key_results(arg, line, fullname)
        if fullname in _ATTRIBUTE_BUILTINS:
            self.attribute_builtin(fullname, node)

    def callee_target(self, callee: Expression, call: CallExpr) -> str | None:
        """Classify what *callee* calls; returns the fully qualified target if known."""
        line = call.line
        if isinstance(callee, MemberExpr) and isinstance(callee.expr, RefExpr) and isinstance(
            callee.expr.node, (TypeInfo, TypeAlias)
        ):
            self.consumed.add(id(callee.expr))  # `bytes.fromhex(...)`: a class's method
        if self.guard is not None:
            text = _dotted(callee)
            self.record("call", text or "?", line, "call in guarded code")
            if isinstance(callee, MemberExpr):
                self.visit(callee.expr)
            return None
        if isinstance(callee, NameExpr):
            node = callee.node
            if isinstance(node, (FuncBase, Decorator, TypeInfo, TypeAlias)):
                if callee.fullname in _OPAQUE_BUILTINS:
                    self.record("call", callee.fullname, line, _OPAQUE_REASON.get(
                        callee.fullname, "runs code the analysis cannot see"))
                    return callee.fullname
                self.call_target_of_ref(callee, line, "", call)
                return callee.fullname
            if isinstance(node, Var):
                self.indirect(callee, line, call=call)
                return None
            if node is None and self.type_of(callee) is None:
                if not self.type_unreachable:
                    self.unresolved(f"<name:{callee.name}>", line, "call of an unbound name")
                return None
            self.indirect(callee, line, call=call)
            return None
        if isinstance(callee, MemberExpr):
            if callee.fullname and isinstance(callee.node, SymbolNode) and callee.kind is not None:
                # module attribute: os.path.join, json.loads, subprocess.run
                self.visit(callee.expr)
                node = callee.node
                if isinstance(node, (FuncBase, Decorator, TypeInfo, TypeAlias)):
                    if callee.fullname in _OPAQUE_BUILTINS:
                        self.record("call", callee.fullname, line, "runs code it cannot see")
                        return callee.fullname
                    self.call_target_of_ref(callee, line, "", call)
                    return callee.fullname
                if isinstance(node, Var):
                    self.machinery_value_use(callee)
                    self.indirect(callee, line, call=call)
                    return None
                self.unresolved(callee.fullname, line, "call of a module attribute of unknown kind")
                return None
            module = _module_name(callee.expr)
            self.visit(callee.expr)
            if module is not None and not callee.fullname:
                self.unresolved(f"{module}.{callee.name}", line, "module has no such attribute")
                return None
            receiver = self.type_of(callee.expr)
            self.machinery_method(callee, line)
            if isinstance(callee.expr, CallExpr) and isinstance(callee.expr.callee, NameExpr) and (
                callee.expr.callee.fullname == "builtins.super"
            ):
                return self.super_call(callee, line)
            if receiver is None:
                if not self.type_unreachable:
                    self.unresolved(f"<untyped>.{callee.name}", line, "method of an untyped value")
                return None
            cls = _class_object_info(receiver)
            if cls is not None:
                found = _lookup(cls, callee.name) or _lookup_metaclass(cls, callee.name)
                if found is None:
                    missing = f"{cls.fullname}.{callee.name}"
                    self.unresolved(missing, line, "no such class attribute")
                    return None
                owner, member = found
                name = f"{owner.fullname}.{callee.name}"
                if isinstance(member, TypeInfo):
                    self.construct(member, line, "nested class")
                    return member.fullname
                if isinstance(member, Var):
                    self.indirect(callee, line, call=call)
                    return None
                self.target("call", name, line, "")
                return name
            infos = _infos(receiver)
            if infos is None:
                if self.is_json(callee.expr):
                    self.record("call", f"<json>.{callee.name}", line, "method of a JSON value")
                    return f"<json>.{callee.name}"
                self.unresolved(
                    f"<Any>.{callee.name}", line, "method call on a value of unknown type",
                    callee.expr, "call",
                )
                return None
            resolved: str | None = None
            for info in infos:
                found = _lookup(info, callee.name)
                if found is None:
                    hook = _lookup(info, "__getattr__")
                    if hook and hook[0].fullname != _OBJECT:
                        self.target("implicit", f"{hook[0].fullname}.__getattr__", line,
                                    f".{callee.name}")
                        continue
                    self.unresolved(f"{info.fullname}.{callee.name}", line, "no such method")
                    continue
                owner, member = found
                name = f"{owner.fullname}.{callee.name}"
                if _is_property(member):
                    self.target("implicit", name, line, "property, then called")
                    self.indirect(callee, line, call=call)
                    continue
                if isinstance(member, Var):
                    self.indirect(callee, line, call=call)
                    continue
                if member is not None and id(member) in self.analysis.method_defs:
                    self.analysis.method_calls.setdefault(id(member), []).append((self.path, call))
                self.target("call", name, line, "")
                resolved = name
            return resolved
        if type(callee).__name__ == "SuperExpr" and self.class_stack:
            # `super().name(...)`: called, so the protocol rules apply to the call.
            method: str = getattr(callee, "name")  # noqa: B009 - mypyc node, no common base
            for base in self.class_stack[-1].mro[1:]:
                if method in base.names:
                    member = base.names[method].node
                    if isinstance(member, (FuncBase, Decorator)) and not _is_property(member):
                        name = f"{base.fullname}.{method}"
                        self.target("call", name, line, "super()")
                        return name
                    break
        # anything else: a call of a call result, a subscript, a lambda, ...
        self.visit(callee)
        self.indirect(callee, line, call=call)
        return None

    def super_call(self, callee: MemberExpr, line: int) -> str | None:
        if not self.class_stack:
            self.unresolved("super", line, "super() outside a class")
            return None
        for base in self.class_stack[-1].mro[1:]:
            if callee.name in base.names:
                name = f"{base.fullname}.{callee.name}"
                self.target("call", name, line, "super()")
                return name
        return None

    def indirect(
        self, callee: Expression, line: int, what: str = "", call: CallExpr | None = None
    ) -> None:
        """A call through a value: covered where the callable was referenced.

        A value of callable type can only hold something a reference produced
        (every reference in the closure is classified) or something an external
        call returned (that call is classified). An object with `__call__` is
        resolved like any implicit method; an unknown type is unresolvable.
        """
        typ = self.type_of(callee)
        if typ is None:
            if not self.type_unreachable:
                self.unresolved("<untyped>()", line, "call of an untyped value")
            return
        proper = get_proper_type(typ)
        if isinstance(proper, (CallableType, Overloaded)):
            if proper.is_type_obj():
                info = proper.type_object()
                if call is not None and info.fullname in METHOD_PROTOCOLS:
                    # A class held in a variable (`error_class(message)`): its
                    # constructor's protocol rule applies to this call's arguments.
                    self.protocols([info.fullname], None, _call_arguments(call), line)
                    self.construct(info, line, "class held in a variable")
                    return
                if info.fullname in RUNS_OTHER_CODE:
                    self.unresolved(f"<value of {info.fullname}>", line,
                                    f"{info.fullname}, which runs other objects' code, "
                                    "called through a value")
                    return
                self.construct(info, line, "class held in a variable")
            return
        if isinstance(proper, TypeType) and isinstance(proper.item, Instance):
            self.construct(proper.item.type, line, "type[] value called")
            return
        if isinstance(proper, AnyType):
            self.unresolved("<Any>()", line, f"{what or 'call'} of a value of unknown type")
            return
        self.implicit(typ, "__call__", line, what or "call of an object")

    def attribute_builtin(self, fullname: str, node: CallExpr) -> None:
        if len(node.args) < 2 or not isinstance(node.args[1], StrExpr):
            self.unresolved(
                f"{fullname}(<computed name>)", node.line, "attribute name computed at run time"
            )
            return
        name = node.args[1].value
        receiver = self.type_of(node.args[0])
        if _module_name(node.args[0]) == "builtins" and fullname != "builtins.getattr":
            self.record("machinery", "builtins", node.line, "write to the builtins module")
            return
        infos = _infos(receiver)
        cls = _class_object_info(receiver)
        if infos is None and cls is None:
            self.unresolved(
                f"<Any>.{name}", node.line, f"{fullname} on a value of unknown type",
                node.args[0],
            )
            return
        for info in ([cls] if cls is not None else infos or []):
            found = _lookup(info, name)
            if found is None:
                continue
            owner, member = found
            if _is_property(member):
                self.target("implicit", f"{owner.fullname}.{name}", node.line, fullname)
            elif isinstance(member, (FuncBase, Decorator)) and fullname == "builtins.getattr":
                self.target("reference", f"{owner.fullname}.{name}", node.line, "getattr method")

    def machinery_method(self, callee: MemberExpr, line: int) -> None:
        name = _module_attribute(callee.expr)
        if name is None:
            return
        if name in REFUSED_WRITE_TARGETS and callee.name not in _READ_ONLY_METHODS:
            self.record("machinery", name, line, f".{callee.name}() on an import hook list")
        elif name in DECLARED_MACHINERY and callee.name not in _READ_ONLY_METHODS:
            self.record("machinery", name, line, f".{callee.name}()")

    def machinery_value_use(self, expr: MemberExpr) -> None:
        if expr.fullname in DECLARED_MACHINERY | REFUSED_WRITE_TARGETS:
            self.record("machinery", expr.fullname, expr.line, "import machinery as a value")

    def _OpExpr(self, node: OpExpr) -> None:
        self.visit(node.left)
        self.visit(node.right)
        if node.op in ("and", "or"):
            self.truth(node.left)
            return
        self.method_type(node.method_type, node.op, node.line, node.left, [node.right])
        self.reflected(node.right, node.op, node.line)
        if node.op == "%" and isinstance(get_proper_type(self.type_of(node.left)), Instance):
            items = node.right.items if isinstance(node.right, TupleExpr) else [node.right]
            for item in items:
                for dunder in ("__str__", "__repr__", "__format__"):
                    self.implicit(self.type_of(item), dunder, node.line, "% formatting", item)

    def method_type(
        self,
        method: Type | None,
        op: str,
        line: int,
        operand: Expression,
        others: Sequence[Expression] = (),
    ) -> None:
        dunder = _OPERATOR_DUNDER.get(op)
        names = self.dunder_names(self.type_of(operand), f"__{dunder}__") if dunder else []
        proper_method = get_proper_type(method) if method is not None else None
        definition = None
        if isinstance(proper_method, CallableType):
            definition = proper_method.definition
        elif isinstance(proper_method, Overloaded) and proper_method.items:
            definition = proper_method.items[0].definition
        if definition is not None and definition.fullname:
            names.append(definition.fullname)
        unknown = isinstance(get_proper_type(self.type_of(operand)), AnyType)
        self.protocols(
            names, operand, [(other, index, None) for index, other in enumerate(others)], line,
            _ANY_RECEIVER_ARGS if unknown and op in ("in", "not in", "[]") else None,
        )
        self._method_type(method, op, line, operand)

    def _method_type(self, method: Type | None, op: str, line: int, operand: Expression) -> None:
        """The method an operator runs on *operand* (the left operand, or the
        container for `in`). mypy records it on the node when it can; when it
        does not (an `Any` or a natively-handled operand), it is resolved by
        name on the operand's type, and an `Any` operand is unresolvable unless
        it is proved JSON."""
        if self.guard is not None:
            self.record("implicit", f"?.{op}", line, "operator in guarded code")
            return
        proper = get_proper_type(method) if method is not None else None
        definition = None
        if isinstance(proper, CallableType):
            definition = proper.definition
        elif isinstance(proper, Overloaded) and proper.items:
            definition = proper.items[0].definition
        if definition is not None and definition.fullname:
            if not definition.fullname.startswith("builtins.object.") and not (
                id(definition) in self.repo_definitions
                or self.is_repo_target(definition.fullname)
            ):
                self.target("implicit", definition.fullname, line, f"operator {op}")
            return
        if self.type_unreachable:
            return
        dunder = _OPERATOR_DUNDER.get(op)
        if dunder is None:
            self.unresolved(f"<op:{op}>", line, "an operator the scan does not model")
            return
        self.implicit(self.type_of(operand), f"__{dunder}__", line, f"operator {op}", operand)

    def reflected(self, operand: Expression, op: str, line: int) -> None:
        """The right operand's reflected method, which Python runs when the left
        operand's method returns NotImplemented."""
        name = _REFLECTED.get(op)
        if name is None or self.guard is not None or self.type_unreachable:
            return
        self.implicit(self.type_of(operand), name, line, f"reflected {op}", operand)

    def _RaiseStmt(self, node: Any) -> None:
        for expr in (node.expr, node.from_expr):
            if isinstance(expr, RefExpr) and isinstance(expr.node, (TypeInfo, TypeAlias)):
                self.consumed.add(id(expr))  # `raise ValueError`: instantiated with no arguments
            self.visit(expr)

    def _ComparisonExpr(self, node: ComparisonExpr) -> None:
        for operand in node.operands:
            if isinstance(operand, RefExpr) and isinstance(operand.node, (TypeInfo, TypeAlias)):
                self.consumed.add(id(operand))  # `type(x) is int`: compared, never called
            self.visit(operand)
        for index, op in enumerate(node.operators):
            if op in ("is", "is not"):
                continue
            method = node.method_types[index] if index < len(node.method_types) else None
            if op in ("in", "not in"):
                subject, other = node.operands[index + 1], node.operands[index]
            else:
                subject, other = node.operands[index], node.operands[index + 1]
            self.method_type(method, op, node.line, subject, [other])
            if op not in ("in", "not in"):
                self.reflected(node.operands[index + 1], op, node.line)

    def _UnaryExpr(self, node: UnaryExpr) -> None:
        self.visit(node.expr)
        if node.op == "not":
            self.truth(node.expr)
            return
        self.method_type(node.method_type, node.op, node.line, node.expr)

    def _IndexExpr(self, node: IndexExpr) -> None:
        self.visit(node.base)
        self.visit(node.index)
        if node.analyzed is not None:
            return  # a generic alias at run time: list[int], dict[str, Any]
        if _class_object_info(self.type_of(node.base)) is not None:
            return  # Class[...]: __class_getitem__ of a typing construct
        self.method_type(node.method_type, "[]", node.line, node.base, [node.index])

    def _StarExpr(self, node: StarExpr) -> None:
        self.visit(node.expr)
        self.iterate(node.expr, node.line, "* unpacking")

    def _DictExpr(self, node: DictExpr) -> None:
        for key, value in node.items:
            if key is None:
                self.visit(value)
                self.implicit(self.type_of(value), "keys", node.line, "** in a dict display", value)
                continue
            self.visit(key)
            self.visit(value)

    def _YieldFromExpr(self, node: Any) -> None:
        self.visit(node.expr)
        self.iterate(node.expr, node.line, "yield from")

    def _LambdaExpr(self, node: LambdaExpr) -> None:
        for argument in node.arguments:
            self.visit(argument.initializer)
        self.scope.append("<lambda>")
        self.block(node.body.body)
        self.scope.pop()

    def _comprehension(self, node: Any, outputs: Sequence[Expression]) -> None:
        for index, sequence, conditions in zip(
            node.indices, node.sequences, node.condlists, strict=True
        ):
            self.visit(sequence)
            self.iterate(sequence, node.line, "comprehension")
            self.store(index)
            for condition in conditions:
                self.visit(condition)
                self.truth(condition)
        for output in outputs:
            self.visit(output)

    def _GeneratorExpr(self, node: Any) -> None:
        self._comprehension(node, [node.left_expr])

    def _ListComprehension(self, node: Any) -> None:
        self._GeneratorExpr(node.generator)

    def _SetComprehension(self, node: Any) -> None:
        self._GeneratorExpr(node.generator)

    def _DictionaryComprehension(self, node: Any) -> None:
        self._comprehension(node, [node.key, node.value])

    def _ConditionalExpr(self, node: Any) -> None:
        self.visit(node.cond)
        self.truth(node.cond)
        self.visit(node.if_expr)
        self.visit(node.else_expr)

    def _AssignmentExpr(self, node: AssignmentExpr) -> None:
        self.visit(node.value)

    def _SuperExpr(self, node: Any) -> None:
        if not self.class_stack:
            self.unresolved("super", node.line, "super() outside a class")
            return
        for base in self.class_stack[-1].mro[1:]:
            if node.name in base.names:
                member = base.names[node.name].node
                if _is_property(member):
                    self.target("implicit", f"{base.fullname}.{node.name}", node.line, "super")
                elif isinstance(member, (FuncBase, Decorator)):
                    self.target("reference", f"{base.fullname}.{node.name}", node.line, "super")
                return

    def _RevealExpr(self, node: Any) -> None:
        self.unresolved("reveal_type", node.line, "reveal_type left in code")


_READ_ONLY_METHODS = frozenset({"index", "count", "copy", "get", "keys", "values", "items"})
_REFLECTED = {
    "+": "__radd__", "-": "__rsub__", "*": "__rmul__", "/": "__rtruediv__",
    "//": "__rfloordiv__", "%": "__rmod__", "**": "__rpow__", "<<": "__rlshift__",
    ">>": "__rrshift__", "&": "__rand__", "|": "__ror__", "^": "__rxor__", "@": "__rmatmul__",
    "==": "__eq__", "!=": "__ne__", "<": "__gt__", ">": "__lt__", "<=": "__ge__", ">=": "__le__",
}
_OPERATOR_DUNDER = {
    "+": "add", "-": "sub", "*": "mul", "/": "truediv", "//": "floordiv", "%": "mod",
    "**": "pow", "<<": "lshift", ">>": "rshift", "&": "and", "|": "or", "^": "xor",
    "@": "matmul", "[]": "getitem", "==": "eq", "!=": "ne", "<": "lt", ">": "gt",
    "<=": "le", ">=": "ge", "in": "contains", "not in": "contains",
}


def _lookup_metaclass(info: TypeInfo, name: str) -> tuple[TypeInfo, SymbolNode | None] | None:
    meta = info.metaclass_type
    if meta is None:
        return None
    return _lookup(meta.type, name)


def _module_name(expr: Expression) -> str | None:
    if isinstance(expr, NameExpr) and isinstance(expr.node, MypyFile):
        return expr.node.fullname
    if isinstance(expr, MemberExpr) and isinstance(expr.node, MypyFile):
        return expr.node.fullname
    return None


def _module_attribute(expr: Expression) -> str | None:
    """`sys.path` for the expression `sys.path` (a module attribute), else None."""
    if isinstance(expr, MemberExpr) and _module_name(expr.expr) is not None:
        return f"{_module_name(expr.expr)}.{expr.name}"
    if isinstance(expr, NameExpr) and isinstance(expr.node, Var) and expr.fullname:
        module, _, name = expr.fullname.rpartition(".")
        if module in ("sys", "builtins"):
            return expr.fullname
    return None


def _dotted(expr: Expression) -> str | None:
    if isinstance(expr, NameExpr):
        return expr.fullname or expr.name
    if isinstance(expr, MemberExpr):
        base = _dotted(expr.expr)
        return f"{base}.{expr.name}" if base else None
    return None


# --- classification ----------------------------------------------------------------------


def classify(sites: Iterable[Site], lists: Lists) -> list[Classified]:
    """Every site's status. `unclassified`, `unresolvable` and `refused` fail."""
    out: list[Classified] = []
    for site in sites:
        declared = lists.edges.get(site.key)
        if site.guard is not None:
            out.append(Classified(site, "platform-unreachable", site.guard))
            continue
        if (site.kind == "attribute-read" and site.target in REFUSED_ATTRIBUTE_READS) or (
            site.kind == "machinery" and site.target in REFUSED_WRITE_TARGETS | {"builtins"}
        ):
            out.append(Classified(site, "refused", site.detail))
            continue
        if declared is not None:
            out.append(Classified(site, "edge", declared.get("reason", "")))
            continue
        group = lists.group_for(site)
        if group is not None and site.guard is None:
            out.append(Classified(site, "edge", group["reason"]))
            continue
        if site.kind in ("unresolved", "machinery"):
            out.append(Classified(site, "unresolvable", site.detail))
            continue
        if site.target in _OPAQUE_BUILTINS:
            out.append(Classified(site, "unclassified", site.detail))
            continue
        if site.target in lists.inert_targets:
            out.append(Classified(site, "inert", lists.inert_targets[site.target]))
            continue
        owner = site.target.rpartition(".")[0]
        if owner in lists.inert_types:
            # A type entry covers the type's methods, never its constructor:
            # constructing a TemporaryDirectory or a ZipFile on a path touches
            # the filesystem though every later method is inert. A constructor
            # is inert only if it is listed as a target.
            out.append(Classified(site, "inert", lists.inert_types[owner]))
            continue
        if owner in _PROTOCOL_DUNDERS_COVERED_AT_PRODUCER and site.kind == "implicit":
            out.append(Classified(site, "inert", "protocol method of an abstract type: the "
                                  "concrete object came from a call classified where it was made"))
            continue
        out.append(Classified(site, "unclassified", site.detail))
    return out


FAILING = frozenset({"unclassified", "unresolvable", "refused"})


class JsonParameters(ParameterOracle):
    """Which parameters of repository functions receive only JSON values.

    A greatest fixpoint over the call graph of the analysed closure. A function
    is a candidate when it is a plain function (not a method, so no override
    can receive another class's calls; not decorated, so no wrapper sits
    between the call and the body), has at least one direct call, and is never
    referenced other than by a direct call (so it is never called from a place
    this cannot see). Every candidate parameter starts assumed JSON; each round
    refutes a parameter if any call site passes it something not proved JSON
    under the current assumptions (a `*`/`**` expansion refutes all of its
    parameters, and an omitted argument must have a JSON-literal default).
    Rounds repeat until nothing changes. Recursion over a JSON tree (a function
    passing `node["children"][i]` to itself) is proved; a cycle with no JSON
    source is not, because some call outside the cycle refutes it.

    Every call site in the analysed set constrains a function, including call
    sites in modules another lane loads: more constraints, never fewer.

    The same fixpoint decides which functions are **JSON-clean** (json_origin.py:
    every use of every possibly-JSON name is read-only). A caller may hand a JSON
    value to a function only if the receiving parameter is assumed JSON and the
    callee is clean, so a callee that stores into, appends to or leaks its
    parameter makes the caller dirty in turn. Cleanliness starts assumed for
    every plain function and is refuted round by round with the rest.
    """

    def __init__(self, analysis: Analysis) -> None:
        self.analysis = analysis
        self.assume: dict[tuple[str, int, str], bool] = {}
        self.returns: dict[tuple[str, int], bool] = {}
        self.call_index: dict[tuple[str, tuple[int, int, int, int]], tuple[str, int]] = {}
        #: (caller, call span) -> (call, callee) for direct calls of plain functions.
        self.call_nodes: dict[tuple[str, tuple[int, int, int, int]], tuple[CallExpr, FuncDef]] = {}
        self.clean: dict[tuple[str, int], bool] = {}
        self.immutable: dict[tuple[str, int, str], bool] = {}
        self.solved = False

    @classmethod
    def of(cls, analysis: Analysis) -> JsonParameters:
        if analysis.oracle is None:
            analysis.oracle = cls(analysis)
        oracle: JsonParameters = analysis.oracle
        return oracle

    def is_json_parameter(self, path: str, def_line: int, name: str) -> bool:
        return self.assume.get((path, def_line, name), False)

    def is_json_call(self, path: str, span: tuple[int, int, int, int]) -> bool:
        callee = self.call_index.get((path, span))
        return callee is not None and self.returns.get(callee, False)

    def is_immutable_parameter(self, path: str, def_line: int, name: str) -> bool:
        return self.immutable.get((path, def_line, name), False)

    def returns_proven(self, path: str, def_line: int) -> bool:
        return self.returns.get((path, def_line), False)

    def argument_stays_json(
        self, path: str, call: tuple[int, int, int, int], argument: tuple[int, int, int, int]
    ) -> bool:
        found = self.call_nodes.get((path, call))
        if found is None:
            return False
        call_expr, function = found
        callee = self.call_index[(path, call)]
        if not self.clean.get(callee, False):
            return False
        if any(kind in (ARG_STAR, ARG_STAR2) for kind in call_expr.arg_kinds):
            return False
        position = 0
        for value, kind, keyword in zip(
            call_expr.args, call_expr.arg_kinds, call_expr.arg_names, strict=True
        ):
            span = (value.line, value.column, value.end_line, value.end_column)
            if span == argument:
                if kind == ARG_POS:
                    if position >= len(function.arguments):
                        return False
                    parameter = function.arguments[position]
                    if parameter.kind not in (ARG_POS, ARG_OPT):
                        return False
                else:
                    named = [a for a in function.arguments if a.variable.name == keyword]
                    if len(named) != 1 or named[0].kind in (ARG_STAR, ARG_STAR2):
                        return False
                    parameter = named[0]
                return self.assume.get((callee[0], callee[1], parameter.variable.name), False)
            if kind == ARG_POS:
                position += 1
        return False

    def solve(self) -> None:
        if self.solved:
            return
        analysis = self.analysis
        candidates: dict[tuple[str, int, str], tuple[FuncDef, int]] = {}
        by_id = {id(function): key for key, function in analysis.functions.items()}
        executed = self._executed()
        live_calls: dict[int, list[tuple[str, CallExpr, FuncDef | None]]] = {
            callee: [
                site for site in sites if site[2] is None or id(site[2]) in executed
            ]
            for callee, sites in analysis.calls.items()
        }
        self.live_calls = live_calls
        for method_id, (path, line, owner, name) in analysis.method_defs.items():
            sites = analysis.method_calls.get(method_id, [])
            if not sites or self._overridden(owner, name):
                continue
            for caller, call in sites:
                if call.end_line is not None and call.end_column is not None:
                    span = (call.line, call.column, call.end_line, call.end_column)
                    self.call_index[(caller, span)] = (path, line)
            self.returns[(path, line)] = True
        for (path, line), function in analysis.functions.items():
            for caller, call, _enclosing in analysis.calls.get(id(function), []):
                if call.end_line is not None and call.end_column is not None:
                    span = (call.line, call.column, call.end_line, call.end_column)
                    self.call_index[(caller, span)] = by_id[id(function)]
                    self.call_nodes[(caller, span)] = (call, function)
            if not live_calls.get(id(function)):
                continue
            if not function.is_generator and not function.is_coroutine:
                self.returns[(path, line)] = True
            if id(function) in analysis.escapes:
                continue
            for index, argument in enumerate(function.arguments):
                if argument.kind in (ARG_STAR, ARG_STAR2):
                    continue
                candidates[(path, line, argument.variable.name)] = (function, index)
        self.assume = dict.fromkeys(candidates, True)
        self.clean = dict.fromkeys(analysis.functions, True)
        # Syntactic, so outside the fixpoint: every actual (or the default an
        # omitted argument takes) is a constant or another immutable expression.
        for key, (function, index) in candidates.items():
            self.immutable[key] = all(
                self._actual_is_immutable(caller, call, key[0], function, index)
                for caller, call, _enclosing in live_calls[id(function)]
            )
        changed = True
        while changed:
            changed = False
            for origins in analysis.origins.values():
                origins.reset()
            for (path, line), holds in list(self.clean.items()):
                if holds and not (
                    path in analysis.origins and analysis.origins[path].function_clean_at(line)
                ):
                    self.clean[(path, line)] = False
                    changed = True
            for (path, line), holds in list(self.returns.items()):
                if holds and not (
                    path in analysis.origins and analysis.origins[path].returns_json(line)
                ):
                    self.returns[(path, line)] = False
                    changed = True
            for key, (function, index) in candidates.items():
                if not self.assume[key]:
                    continue
                for caller, call, _enclosing in live_calls[id(function)]:
                    if not self._argument_is_json(caller, call, key[0], function, index):
                        self.assume[key] = False
                        changed = True
                        break
        for origins in analysis.origins.values():
            origins.reset()
        self.solved = True

    def _overridden(self, owner: TypeInfo, name: str) -> bool:
        """Does any analysed subclass of *owner* define *name* again?"""
        return any(
            owner in info.mro[1:] and name in info.names for info in self.analysis.classes
        )

    def _executed(self) -> set[int]:
        """id() of every repository function that can run in a lane process.

        Roots: methods (any could be reached through dynamic dispatch), escaping
        functions, decorated functions, and every function in a script module
        (a finalizer may load a builder at run time and call into it). Then any
        plain function with a direct call from module-level code or from a
        function already in the set. A plain function outside the set has no
        way to run: its call sites constrain nothing, and it is never a JSON
        candidate itself.
        """
        analysis = self.analysis
        executed = set(analysis.methods) | set(analysis.escapes)
        for (path, _line), function in analysis.functions.items():
            if not path.startswith("src/"):
                executed.add(id(function))
        changed = True
        while changed:
            changed = False
            for function in analysis.functions.values():
                if id(function) in executed:
                    continue
                for _caller, _call, enclosing in analysis.calls.get(id(function), []):
                    if enclosing is None or id(enclosing) in executed:
                        executed.add(id(function))
                        changed = True
                        break
        return executed

    def _actual(
        self, call: CallExpr, function: FuncDef, index: int
    ) -> tuple[Expression | None, bool]:
        """The expression passed for parameter *index* (None when omitted), and
        whether the call can be mapped at all (no `*`/`**` expansion)."""
        if any(kind in (ARG_STAR, ARG_STAR2) for kind in call.arg_kinds):
            return None, False
        argument = function.arguments[index]
        positional = [a for a, k in zip(call.args, call.arg_kinds, strict=True) if k == ARG_POS]
        if index < len(positional) and argument.kind in (ARG_POS, ARG_OPT):
            return positional[index], True
        for value, keyword in zip(call.args, call.arg_names, strict=True):
            if keyword == argument.variable.name:
                return value, True
        return None, True

    def _actual_is_immutable(
        self, caller: str, call: CallExpr, callee_path: str, function: FuncDef, index: int
    ) -> bool:
        actual, mapped = self._actual(call, function, index)
        if not mapped:
            return False
        origins = self.analysis.origins
        if actual is None:
            actual, caller = function.arguments[index].initializer, callee_path
        if actual is None or caller not in origins:
            return False
        nodes = origins[caller].by_position.get(
            (actual.line, actual.column, actual.end_line or -1, actual.end_column or -1), []
        )
        return bool(nodes) and all(immutable(node) for node in nodes)

    def _argument_is_json(
        self, caller: str, call: CallExpr, callee_path: str, function: FuncDef, index: int
    ) -> bool:
        if any(kind in (ARG_STAR, ARG_STAR2) for kind in call.arg_kinds):
            return False
        argument = function.arguments[index]
        name = argument.variable.name
        positional = [a for a, k in zip(call.args, call.arg_kinds, strict=True) if k == ARG_POS]
        actual: Expression | None = None
        if index < len(positional) and argument.kind in (ARG_POS, ARG_OPT):
            actual = positional[index]
        else:
            for value, keyword in zip(call.args, call.arg_names, strict=True):
                if keyword == name:
                    actual = value
        origins = self.analysis.origins
        if actual is not None:
            return caller in origins and _json_at(origins[caller], actual)
        default = argument.initializer
        if default is None or callee_path not in origins:
            return False
        return _json_at(origins[callee_path], default)


def _json_at(origins: JsonOrigins, expr: Expression) -> bool:
    return origins.at(expr.line, expr.column, expr.end_line, expr.end_column)


def number_sites(sites: Sequence[Site]) -> list[Site]:
    """Assign each site its ordinal among identical (module, scope, kind, target)."""
    from dataclasses import replace

    counts: dict[tuple[str, str, str, str], int] = {}
    numbered: list[Site] = []
    for site in sites:
        key = (site.module, site.scope, site.kind, site.target)
        numbered.append(replace(site, ordinal=counts.get(key, 0)))
        counts[key] = counts.get(key, 0) + 1
    return numbered


def scan_all(analysis: Analysis) -> list[Site]:
    """Every site in every analysed module, with JSON receivers proved where they can be.

    Always the whole analysed set, never a subset: the interprocedural JSON
    proof depends on every call site, so scanning one lane's modules alone
    could prove a parameter JSON that another analysed module refutes.
    """
    from dataclasses import replace

    repo_modules = set(analysis.modules.values())
    sites: list[Site] = []
    for path in sorted(analysis.modules):
        sites.extend(Scanner(analysis, path, repo_modules).scan())
    oracle = JsonParameters.of(analysis)
    oracle.solve()
    resolved: list[Site] = []
    for site in sites:
        if (
            site.kind == "unresolved"
            and site.span is not None
            and site.json_kind
            and analysis.origins[site.module].at(*site.span)
        ):
            site = replace(
                site,
                kind=site.json_kind,
                target=site.target.replace("<Any>", "<json>", 1),
                detail=site.detail.replace(
                    "a value of unknown type", "a JSON value (proved at its call sites)"
                ),
            )
        resolved.append(site)
    for origins in analysis.origins.values():
        origins.reset()
    return number_sites(resolved)


def scan_modules(analysis: Analysis, paths: Iterable[str]) -> list[Site]:
    """The sites of *paths*, from a scan of the whole analysed set."""
    wanted = set(paths)
    return [site for site in scan_all(analysis) if site.module in wanted]


def unused_declarations(sites: Iterable[Site], lists: Lists, modules: Iterable[str]) -> list[str]:
    """Declarations that no longer match the code (each fails the scan and the gate).

    A site-keyed declaration whose site is gone; a grouped declaration whose
    count for a target differs from the number of sites it now covers (an
    added site and a removed one are both a change a reviewer must see).
    """
    sites = list(sites)
    keys = {site.key for site in sites}
    scoped = set(modules)
    problems = [
        f"{key}: declared, no such site"
        for key in lists.edges if key.split("::")[0] in scoped and key not in keys
    ]
    counts: dict[tuple[int, str], int] = {}
    for site in sites:
        if site.guard is not None or site.key in lists.edges:
            continue
        group = lists.group_for(site)
        if group is not None:
            counts[(id(group), site.target)] = counts.get((id(group), site.target), 0) + 1
    for group in lists.groups:
        if group["module"] not in scoped:
            continue
        for target, declared in sorted(group["targets"].items()):
            found = counts.get((id(group), target), 0)
            if found != declared:
                problems.append(
                    f"{group['module']} {group.get('scopes', '*')} {target}: declared "
                    f"{declared} sites, found {found}"
                )
    return sorted(problems)
