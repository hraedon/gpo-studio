"""Plan 036 WP-0 part 1: class machinery runs code of the class's namespace values.

Review round 6 (Sol B1): an ordinary class body, a three-argument `type(...)`
and `@dataclasses.dataclass` ran a caller's descriptor (`__set_name__`,
`__get__`) with no failing scan site. The scan now closes the class rather than
chasing instances: every value bound in a class namespace must be, by its
syntax, a known-inert object (`scan.Scanner.known_inert_value`), and the
generated methods of a dataclass, a NamedTuple and a built-in container are
expanded field by field and element by element.

Each rule has two halves here, the round-5 method: a runtime probe that calls
the operation with a hostile object and shows that the operation runs its
code (so the rule is needed), and a static probe the scan must fail. Controls
show what still passes.
"""

from __future__ import annotations

import abc
import contextlib
import dataclasses
import enum
import functools
import typing
from pathlib import Path
from typing import Any

import pytest
from plan036_support import load

scan = load("scan")

_H = "from __future__ import annotations\nimport abc\nimport dataclasses\nimport enum\n" \
     "import typing\nfrom typing import Any\n\n\n"

#: Sol's round-6 scan probe (`r6_scan_probe.py`), verbatim: each must fail. Its
#: `staticmethod_bound_method` is a control instead (`CONTROLS`): the bound
#: method of a repository class is a direct reference, and staticmethod reads
#: only the repository function's own attributes through it.
SOL_ROUND6 = {
    "classmethod_cls": "class Base:\n    @classmethod\n    def make(cls) -> 'Base':\n"
                       "        return cls()\n",
    "classmethod_typed_cls": "from typing import Self\n\nclass Base:\n    @classmethod\n"
                             "    def make(cls: type[Self]) -> Self:\n        return cls()\n",
    "type_of_value": "def clone(value: object) -> object:\n    return type(value)()\n",
    "self_class": "class Base:\n    def clone(self) -> 'Base':\n        return self.__class__()\n",
    "generic_factory": "from typing import TypeVar\n\nT = TypeVar('T')\n"
                       "def make(cls: type[T]) -> T:\n    return cls()\n",
    "aliased_type_parameter": "def make(cls: type[object]) -> object:\n    factory = cls\n"
                              "    return factory()\n",
    "aliased_type_attribute": "class Holder:\n    factory: type[object]\n\n"
                              "def make(holder: Holder) -> object:\n"
                              "    factory = holder.factory\n    return factory()\n",
    "aliased_classmethod_cls": "class Base:\n    @classmethod\n    def make(cls) -> 'Base':\n"
                               "        factory = cls\n        return factory()\n",
    "partial_parameter_class": "import functools\nfrom typing import TypeVar\n\nT = TypeVar('T')\n"
                               "def make(cls: type[T]) -> T:\n"
                               "    factory = functools.partial(cls)\n    return factory()\n",
    "partial_direct_class": "import functools\n\nclass C:\n    pass\n\ndef make() -> C:\n"
                            "    factory = functools.partial(C)\n    return factory()\n",
    "decorator_classmethod_consumer": "from typing import Any, Callable\n\n"
                                      "def make(fn: Callable[..., Any]) -> classmethod:\n"
                                      "    return classmethod(fn)\n",
    "property_attribute_callable": "from typing import Any, Callable\n\nclass Holder:\n"
                                   "    fn: Callable[..., Any]\n\n"
                                   "def make(holder: Holder) -> property:\n"
                                   "    return property(holder.fn)\n",
    "abcmeta_typed_descriptor": "import abc\n\nclass Descriptor:\n"
                                "    def __set_name__(self, owner: type, name: str) -> None:\n"
                                "        open('hidden-dependency', 'rb')\n\n"
                                "def make(value: Descriptor) -> type:\n"
                                "    return abc.ABCMeta('Made', (), {'x': value})\n",
    "ordinary_class_descriptor": "def make(value: object) -> type:\n    class Made:\n"
                                 "        x = value\n    return Made\n",
    "type_constructor_descriptor": "def make(value: object) -> type:\n"
                                   "    return type('Made', (), {'x': value})\n",
    "dataclass_descriptor_get": "import dataclasses\n\nclass Descriptor:\n"
                                "    def __set_name__(self, owner: type, name: str) -> None:\n"
                                "        pass\n"
                                "    def __get__(self, obj: object, owner: type | None = None) "
                                "-> object:\n        return self\n\n"
                                "@dataclasses.dataclass\nclass Made:\n"
                                "    x: object = Descriptor()\n",
}

#: One static probe per class-machinery rule (review round 6): each must fail.
RULES = {
    # class bodies
    "body_parameter": _H + "def f(v: int) -> type:\n    class C:\n        x = v\n    return C\n",
    "body_call_result": _H + "def g() -> int:\n    return 1\n\n\nclass C:\n    x = g()\n",
    "body_module_constant": _H + "LIMIT = 3\n\n\nclass C:\n    x = LIMIT\n",
    "body_walrus": _H + "class C:\n    (x := 3)\n",
    "body_for_loop": _H + "class C:\n    for i in range(2):\n        pass\n",
    "body_import": _H + "class C:\n    import os\n",
    "body_unpacking": _H + "class C:\n    a, b = 1, 2\n",
    "body_repository_decorator": _H + "def deco(fn: Any) -> Any:\n    return fn\n\n\n"
                                      "class C:\n    @deco\n    def m(self) -> None:\n"
                                      "        pass\n",
    "body_slots_value": _H + "def f(names: tuple[str, ...]) -> type:\n    class C:\n"
                             "        __slots__ = names\n    return C\n",
    "body_enum_member": _H + "def f(v: int) -> type:\n    class E(enum.Enum):\n"
                             "        A = v\n    return E\n",
    "body_namedtuple_default": _H + "def f(v: int) -> type:\n"
                                    "    class N(typing.NamedTuple):\n        x: int = v\n"
                                    "    return N\n",
    "body_abc_value": _H + "def f(v: object) -> type:\n    class A(abc.ABC):\n        x = v\n"
                           "    return A\n",
    "body_class_keyword": _H + "class Base:\n    def __init_subclass__(cls, **kw: Any) -> None:\n"
                               "        pass\n\n\ndef f(v: object) -> type:\n"
                               "    class C(Base, key=v):\n        pass\n    return C\n",
    # dataclasses
    "dataclass_default_value": _H + "def f(v: object) -> type:\n    @dataclasses.dataclass\n"
                                    "    class D:\n        x: object = v\n    return D\n",
    "dataclass_field_default": _H + "def f(v: object) -> type:\n    @dataclasses.dataclass\n"
                                    "    class D:\n        x: object = dataclasses.field("
                                    "default=v)\n    return D\n",
    "dataclass_repr_object_field": _H + "@dataclasses.dataclass\nclass D:\n    x: object\n\n\n"
                                        "def f(d: D) -> str:\n    return repr(d)\n",
    "dataclass_fstring_object_field": _H + "@dataclasses.dataclass\nclass D:\n    x: object\n"
                                           "\n\ndef f(d: D) -> str:\n    return f'{d}'\n",
    "dataclass_eq_any_field": _H + "@dataclasses.dataclass\nclass D:\n    x: Any\n\n\n"
                                   "def f(a: D, b: D) -> bool:\n    return a == b\n",
    "dataclass_hash_any_field": _H + "@dataclasses.dataclass(frozen=True)\nclass D:\n    x: Any\n"
                                     "\n\ndef f(d: D) -> int:\n    return hash(d)\n",
    "dataclass_order_any_field": _H + "@dataclasses.dataclass(order=True)\nclass D:\n    x: Any\n"
                                      "\n\ndef f(a: D, b: D) -> bool:\n    return a < b\n",
    "dataclass_nested_object_field": _H + "@dataclasses.dataclass\nclass Inner:\n    x: object\n"
                                          "\n\n@dataclasses.dataclass\nclass Outer:\n"
                                          "    inner: tuple[Inner, ...]\n\n\n"
                                          "def f(o: Outer) -> str:\n    return str(o)\n",
    "dataclass_repr_over_base_repr": _H + "class Base:\n    def __repr__(self) -> str:\n"
                                          "        return 'base'\n\n\n"
                                          "@dataclasses.dataclass\nclass D(Base):\n"
                                          "    x: object\n\n\n"
                                          "def f(d: D) -> str:\n    return repr(d)\n",
    # metaclass calls
    "type_value_namespace": _H + "def f(ns: dict[str, int]) -> type:\n"
                                 "    return type('C', (), ns)\n",
    "type_value_base": _H + "def f(base: type) -> type:\n    return type('C', (base,), {})\n",
    "abcmeta_display_value": _H + "def f(v: int) -> type:\n"
                                  "    return abc.ABCMeta('C', (), {'x': v})\n",
    # typing subscripts and aliases
    "annotated_value": _H + "def f(v: str) -> object:\n    return typing.Annotated[int, v]\n",
    "alias_value_argument": _H + "Meta = 'x'\nAlias = typing.Annotated[int, Meta]\n",
    # containers and displays
    "repr_list_of_object": _H + "def f(xs: list[object]) -> str:\n    return repr(xs)\n",
    "fstring_dict_of_any": _H + "def f(d: dict[str, Any]) -> str:\n    return f'{d}'\n",
    "tuple_key_with_any": _H + "def f(d: dict[tuple[str, Any], int], k: Any) -> bool:\n"
                              "    return ('x', k) in d\n",
    "set_display_object": _H + "def f(v: object) -> set[object]:\n    return {v}\n",
    "dict_display_object_key": _H + "def f(v: object) -> dict[object, int]:\n    return {v: 1}\n",
    "dict_comprehension_any_key": _H + "def f(xs: list[Any]) -> dict[Any, int]:\n"
                                       "    return {x: 1 for x in xs}\n",
    "set_comprehension_any": _H + "def f(xs: list[Any]) -> set[Any]:\n    return {x for x in xs}\n",
    "sorted_tuples_with_any": _H + "def f(xs: list[tuple[str, Any]]) -> object:\n"
                                   "    return sorted(xs)\n",
}

#: What still passes: the shapes the live closure's classes use.
CONTROLS = {
    "control_literals": _H + "class C:\n    '''Doc.'''\n    a = 1\n    b = -2\n    c = 'x'\n"
                             "    d = (1, 'y', None)\n    e = {'k': [1.5, b'x']}\n    f: int\n",
    "control_methods": _H + "class C:\n    @property\n    def p(self) -> int:\n        return 1\n\n"
                            "    @staticmethod\n    def s() -> int:\n        return 1\n",
    "staticmethod_bound_method": "class Holder:\n    def fn(self) -> None:\n        pass\n\n"
                                 "def make(holder: Holder) -> staticmethod:\n"
                                 "    return staticmethod(holder.fn)\n",
    "control_dataclass": _H + "@dataclasses.dataclass(frozen=True)\nclass D:\n    a: str\n"
                              "    b: tuple[str, ...] = ()\n"
                              "    c: list[int] = dataclasses.field(default_factory=list)\n"
                              "    d: int = dataclasses.field(default=0, compare=False)\n\n\n"
                              "def f(x: D, y: D) -> object:\n"
                              "    return (repr(x), x == y, {x}, f'{x}')\n",
    "control_dataclass_own_repr": _H + "@dataclasses.dataclass\nclass D:\n    x: object\n\n"
                                       "    def __repr__(self) -> str:\n        return 'D'\n\n\n"
                                       "def f(d: D) -> str:\n    return repr(d)\n",
    "control_literal_alias": _H + "class C:\n    Kind = typing.Literal['a', 'b']\n",
    "control_nested_plain_class": _H + "class C:\n    class Inner:\n        pass\n",
    "control_namedtuple": _H + "class N(typing.NamedTuple):\n    x: int\n    y: str = ''\n",
    "control_slots": _H + "class C:\n    __slots__ = ('a', 'b')\n",
    "control_type_display": _H + "class Base:\n    pass\n\n\ndef g() -> int:\n    return 1\n"
                                 "\n\ndef f() -> type:\n"
                                 "    return type('C', (Base,), {'x': 1, 'f': g})\n",
    # With key=, only the key's results are compared (the elements are not).
    "control_keyed_sort": _H + "def f(rows: list[tuple[str, Any]]) -> object:\n"
                               "    rows.sort(key=lambda r: r[0])\n"
                               "    return sorted(rows, key=lambda r: r[0])\n",
}
#: Shapes the class-namespace rule accepts, in classes whose other machinery is
#: not on the reviewed lists (no live lane defines an Enum, a property setter or
#: a classmethod): they fail for that, never with a round-6 site.
NAMESPACE_CONTROLS = {
    "namespace_enum": _H + "class E(enum.Enum):\n    A = 1\n    B = enum.auto()\n",
    "namespace_setter_classmethod": _H + "class C:\n    @property\n    def p(self) -> int:\n"
                                         "        return 1\n\n    @p.setter\n"
                                         "    def p(self, v: int) -> None:\n        pass\n\n"
                                         "    @classmethod\n    def c(cls) -> int:\n"
                                         "        return 1\n",
}
_ROUND6_TARGETS = ("<class namespace value>", "<class base value>", "<class keyword value>",
                   "<type argument value>")


@pytest.fixture(scope="module")
def classified(tmp_path_factory: pytest.TempPathFactory) -> list[Any]:
    root = tmp_path_factory.mktemp("classes")
    directory = root / "scripts" / "probes"
    directory.mkdir(parents=True)
    paths = []
    for name, source in {**SOL_ROUND6, **RULES, **CONTROLS, **NAMESPACE_CONTROLS}.items():
        (directory / f"{name}.py").write_text(source, encoding="utf-8")
        paths.append(f"scripts/probes/{name}.py")
    analysis = scan.analyse(root, paths, "3.14")
    assert scan.assert_complete(analysis) == []
    return list(scan.classify(scan.scan_all(analysis), scan.Lists.load()))


def _rows(classified: list[Any], probe: str) -> list[Any]:
    return [c for c in classified if c.site.module == f"scripts/probes/{probe}.py"]


@pytest.mark.parametrize("probe", sorted(SOL_ROUND6))
def test_sols_round_6_probes_fail(classified: list[Any], probe: str) -> None:
    rows = _rows(classified, probe)
    assert any(c.status in scan.FAILING for c in rows), [
        (c.status, c.site.target, c.site.detail) for c in rows]


@pytest.mark.parametrize("probe", sorted(RULES))
def test_each_class_machinery_rule_fails_closed(classified: list[Any], probe: str) -> None:
    rows = _rows(classified, probe)
    assert any(c.status in scan.FAILING for c in rows), [
        (c.status, c.site.target, c.site.detail) for c in rows]


@pytest.mark.parametrize("probe", sorted(CONTROLS))
def test_known_inert_class_shapes_pass(classified: list[Any], probe: str) -> None:
    rows = _rows(classified, probe)
    failing = [(c.status, c.site.target, c.site.detail) for c in rows
               if c.status in scan.FAILING]
    assert not failing, failing


@pytest.mark.parametrize("probe", sorted(NAMESPACE_CONTROLS))
def test_the_namespace_rule_accepts_known_inert_shapes(classified: list[Any], probe: str) -> None:
    rows = _rows(classified, probe)
    assert rows
    flagged = [(c.site.target, c.site.detail) for c in rows
               if c.site.target in _ROUND6_TARGETS or "of a field or element" in c.site.detail]
    assert not flagged, flagged


def test_the_class_namespace_site_names_the_class_and_the_value(classified: list[Any]) -> None:
    rows = [c for c in _rows(classified, "ordinary_class_descriptor")
            if c.site.target == "<class namespace value>"]
    assert len(rows) == 1 and "class Made" in rows[0].site.detail
    assert "binds x" in rows[0].site.detail and rows[0].status == "unresolvable"


# --- the runtime half: each operation runs the hostile object's code -------------------

class _Hostile:
    """Logs every protocol and attribute hook class machinery could reach."""

    def __init__(self) -> None:
        self.log: list[str] = []

    def __set_name__(self, owner: type, name: str) -> None:
        self.log.append("__set_name__")

    def __get__(self, obj: object, owner: type | None = None) -> object:
        self.log.append("__get__")
        return self

    def __getattr__(self, name: str) -> object:
        if name != "log":
            self.log.append(f"getattr:{name}")
        raise AttributeError(name)

    def __hash__(self) -> int:
        self.log.append("__hash__")
        return 1

    def __eq__(self, other: object) -> bool:
        self.log.append("__eq__")
        return NotImplemented

    def __lt__(self, other: object) -> bool:
        self.log.append("__lt__")
        return NotImplemented

    def __repr__(self) -> str:
        self.log.append("__repr__")
        return "H"

    def __iter__(self) -> Any:
        self.log.append("__iter__")
        return iter(("a",))


def _body(h: _Hostile) -> None:
    class C:
        x = h


def _type_call(h: _Hostile) -> None:
    type("C", (), {"x": h})


def _abcmeta_call(h: _Hostile) -> None:
    abc.ABCMeta("C", (), {"x": h})


def _abc_body(h: _Hostile) -> None:
    class C(abc.ABC):  # noqa: B024 - the probe is the namespace value
        x = h


def _dataclass_default(h: _Hostile) -> None:
    @dataclasses.dataclass
    class D:
        x: object = h


def _dataclass_field_default(h: _Hostile) -> None:
    @dataclasses.dataclass
    class D:
        x: object = dataclasses.field(default=h)


def _enum_member(h: _Hostile) -> None:
    class E(enum.Enum):
        A = h


def _slots(h: _Hostile) -> None:
    class C:
        __slots__ = h  # type: ignore[assignment]


def _namedtuple_default(h: _Hostile) -> None:
    class N(typing.NamedTuple):
        x: object = h


def _dataclass_repr(h: _Hostile) -> None:
    @dataclasses.dataclass
    class D:
        x: object

    repr(D(h))


def _dataclass_eq(h: _Hostile) -> None:
    @dataclasses.dataclass
    class D:
        x: object

    _ = D(h) == D(h)


def _dataclass_order(h: _Hostile) -> None:
    @dataclasses.dataclass(order=True)
    class D:
        x: object

    with contextlib.suppress(TypeError):
        _ = D(h) < D(_Hostile())


def _dataclass_repr_over_base(h: _Hostile) -> None:
    class Base:
        def __repr__(self) -> str:
            return "base"

    @dataclasses.dataclass
    class D(Base):
        x: object

    repr(D(h))


def _frozen_hash(h: _Hostile) -> None:
    @dataclasses.dataclass(frozen=True)
    class D:
        x: object

    hash(D(h))


def _list_repr(h: _Hostile) -> None:
    _ = f"{[h]}"


def _tuple_hash(h: _Hostile) -> None:
    hash(("k", h))


def _set_display(h: _Hostile) -> None:
    {h}  # noqa: B018 - the probe is the display


def _annotated(h: _Hostile) -> None:
    typing.Annotated[int, h]  # noqa: B018 - the probe is the subscript


def _union(h: _Hostile) -> None:
    typing.Union[int, h]  # type: ignore[valid-type]  # noqa: B018, UP007


def _cached_property_control(h: _Hostile) -> None:
    class C:
        @functools.cached_property
        def v(self) -> int:
            return 1

    C().v  # noqa: B018 - no hostile value reaches it


#: operation -> what the hostile object must log (the rule each one grounds).
RUNTIME = {
    _body: ["__set_name__"],
    _type_call: ["__set_name__"],
    _abcmeta_call: ["__set_name__", "getattr:__isabstractmethod__"],
    _abc_body: ["__set_name__", "getattr:__isabstractmethod__"],
    _dataclass_default: ["__set_name__", "__get__", "__repr__"],
    _dataclass_field_default: ["__set_name__", "__repr__"],
    _enum_member: ["__set_name__"],
    _slots: ["__iter__"],
    _namedtuple_default: ["__set_name__"],
    _dataclass_repr: ["__repr__"],
    _dataclass_eq: ["__eq__"],
    _dataclass_order: ["__eq__"],
    _frozen_hash: ["__hash__"],
    _dataclass_repr_over_base: ["__repr__"],
    _list_repr: ["__repr__"],
    _tuple_hash: ["__hash__"],
    _set_display: ["__hash__"],
    _annotated: ["__hash__"],
    _union: ["__eq__", "__hash__"],
}


@pytest.mark.parametrize("operation", list(RUNTIME), ids=lambda f: f.__name__)
def test_each_rule_guards_an_operation_that_runs_the_value(operation: Any) -> None:
    hostile = _Hostile()
    operation(hostile)
    missing = [event for event in RUNTIME[operation] if event not in hostile.log]
    assert not missing, (operation.__name__, hostile.log)


def test_the_cached_property_control_reaches_no_caller_object() -> None:
    hostile = _Hostile()
    _cached_property_control(hostile)
    assert hostile.log == []


def test_the_live_closure_classes_use_only_known_inert_shapes() -> None:
    """The three module constants bound as dataclass defaults are the live
    closure's only class namespace values that are not known-inert by syntax;
    they are declared, by exact count and scope, in lists/edges.json."""
    lists = scan.Lists.load()
    declared = [(g["module"], tuple(g["scopes"]), g["targets"]["<class namespace value>"])
                for g in lists.groups if "<class namespace value>" in g["targets"]]
    assert sorted(declared) == [
        ("src/gpo_studio/oracle_evidence.py", ("XmlSemanticResult",), 1),
        ("src/gpo_studio/workspace_ops.py", ("BackupMetadata",), 2),
    ]


def test_the_probe_sources_are_valid_python(tmp_path: Path) -> None:
    for name, source in {**SOL_ROUND6, **RULES, **CONTROLS, **NAMESPACE_CONTROLS}.items():
        compile(source, f"{name}.py", "exec")
