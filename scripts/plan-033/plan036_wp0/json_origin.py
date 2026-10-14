"""Which `Any`-typed expressions provably hold a value `json.loads` produced.

`json.loads` and `json.load` (with no `cls`, `object_hook`, `object_pairs_hook`,
`parse_float`, `parse_int` or `parse_constant` argument) return only `dict`,
`list`, `str`, `int`, `float`, `bool` or `None`, and every method and protocol
operation of those types is in-memory. mypy types the result `Any`, so without
this rule every operation on the JSON a finalizer reads would be an
unresolvable `Any` receiver. With it, an operation on a value this module
proves is JSON is classified under the reviewed `<json>` inert entry; an
operation on an `Any` value it cannot prove stays unresolvable and fails
unless declared.

**The proof is narrowed so that mutation cannot defeat it** (review round 1,
Sol B2 / DeepSeek B2: `data["x"] = other; data["x"].danger()` was inert). A
JSON container is mutable, and anything stored into it, or into any
sub-object reachable through an alias, would no longer be JSON. So a value is
JSON only inside a **JSON-clean function**: a function in which every use of
every *policed* name is read-only. A name is policed when at least one of its
bindings could hold a JSON value (an assignment or unpacking of one, a loop or
comprehension over one or over its `.items()`/`.values()`/`.keys()`, a
parameter the oracle assumes JSON). A use of a policed name is clean only in
these contexts, followed through the value it produces:

- `x[k]` (a load: its result is followed further), `x[k]` as another
  object's subscript index, `x.get(k)` / `x.get(k, d)` (followed further),
  `x.count(..)` / `x.index(..)` / a `_READ_METHODS` method, and `x.items()` /
  `.values()` / `.keys()` only as the iterable of a `for` or a comprehension
  (whose targets are policed in turn); `x` as the key of a JSON value's
  `.get`/`.count`/`.index`;
- a truth test (including a comprehension's `if`), a comparison, `not x`,
  formatting in an f-string;
- an element of a list, tuple, set or dict display, or the element of a
  comprehension (followed further: the new container holds the value, so a
  name bound to it is policed in turn);
- `y = x` or `a, b = x` with plain-name targets (which are policed in turn),
  and the iterable of a `for` or a comprehension; an arithmetic operand
  (`-`, `/`, `%`, ...), and an operand of `+`, `*`, `|` (whose result may
  share items, so it is followed further);
- `x if c else y`, `x or y` (followed further), `return x` only from a
  function whose returns the oracle proves JSON (so its callers' bindings are
  policed);
- `return x` from any other function when `x` cannot alias the function's
  parameters (JSON the function decoded itself dies with the call; a
  parameter's value belongs to a caller that would not police the result);
- an argument to `len`, `str`, `repr`, `bool`, `int`, `float`, `isinstance`,
  `print`, `hash`, `abs`, `round`, `any`, `all`, `sum` (unrebound built-ins
  that return no part of their argument) or `json.dumps` (no `cls` or
  `default`), or to a repository function whose receiving parameter the
  oracle proves JSON, which requires that function to be JSON-clean in turn
  (and when that function's returns are JSON, its call is followed further).

A name is policed when it is bound from a value that may alias JSON or
another policed name (a least fixpoint, so a cycle through bindings still
reaches its source). A value that is immutable needs no policing: a constant,
an f-string, a comparison, a `_READ_METHODS` result other than a list, and a
parameter that receives only such values at every call
(`is_immutable_parameter`).

Every other use fails the function: a store or `del` through it (`x[k] = v`,
`x[k] += v`), any other method (`append`, `update`, `setdefault`, `pop`,
`copy`, `sort`), an argument to any other call (copying built-ins such as
`list`, `sorted`, `enumerate`, `max` hand out the same sub-objects),
`yield`, `with`, a starred use, `x += ...`-style in-place `+`/`*`/`|`
with `x` as the operand, a read from a nested function
or lambda (a closure may run at any later time), and a `nonlocal`
declaration of the name. The interprocedural facts (parameters, returns,
cleanliness of callees) are a greatest fixpoint in scan.py's
`JsonParameters`; a function refuted there stays refuted (fail closed).

The expressions that are JSON (in a JSON-clean function):

- a call of `json.loads`/`json.load` (the module bound only by `import json`,
  or the function only by `from json import loads/load`) with exactly one
  positional argument and no keyword argument;
- a call of a repository function every one of whose `return` statements
  returns JSON (the interprocedural oracle resolves the call and decides);
- a subscript of a JSON value; `<json>.get(k)`, `.get(k, d)` with `d` JSON or
  a JSON literal; `<json>.count(..)`, `.index(..)`, a `str`/`int`/`float`
  method returning a new string, number, bool or list of strings
  (`_READ_METHODS`); `<json>.items()`/`.values()`/`.keys()` (a view, which a
  clean function may only iterate);
- `x or y`, `x and y`, `a if c else b` whose value operands are JSON or
  JSON literals; a JSON literal;
- a name whose every binding is one of: a plain assignment of a JSON value
  other than a display (a fresh `[]` or `{}` is not policed, so it may be
  filled with anything); a
  `for`/comprehension target iterating a JSON value, or a `(k, v)` target
  iterating `<json>.items()`; a parameter the oracle proves receives JSON at
  every call. Any other binding (another parameter, `with`, `except`,
  `import`, augmented assignment, walrus, `global`/`nonlocal`, `del`) makes
  the name not JSON.

A cycle in the bindings is not JSON (fail closed). A name bound at module or
class level is never JSON (any code can change it), nor is the result of a
repository call made there (no cleanliness proof covers its arguments); only an
expression rooted directly in a `json.loads` call can be JSON there, and that
value is fresh.
"""

from __future__ import annotations

import ast
from pathlib import Path

_GETTERS = frozenset({"get"})
#: Operators whose result can be a container holding its operands' items.
_ALIASING_OPERATORS = (ast.Add, ast.Mult, ast.BitOr)
_ITEM_VIEWS = frozenset({"items", "values", "keys"})
_COUNTERS = frozenset({"count", "index"})
#: Methods of `str`, `int` and `float` that return a new `str`, `int`, `bool` or
#: list of new strings (a JSON value sharing nothing with the receiver). None of
#: these names exists on `dict` or `list`, so on any JSON value they either run
#: that pure method or raise AttributeError.
_READ_METHODS = frozenset({
    "lower", "upper", "casefold", "title", "capitalize", "swapcase", "strip", "lstrip",
    "rstrip", "split", "rsplit", "splitlines", "startswith",
    "endswith", "find", "rfind", "rindex", "replace", "removeprefix", "removesuffix",
    "zfill", "center", "ljust", "rjust", "expandtabs", "isdigit", "isdecimal",
    "isnumeric", "isalpha", "isalnum", "isspace", "islower", "isupper", "istitle",
    "isidentifier", "isprintable", "isascii", "bit_length", "is_integer",
})
#: json.dumps keywords that cannot hand the value to other code.
_DUMPS_KEYWORDS = frozenset({
    "indent", "sort_keys", "ensure_ascii", "separators", "allow_nan", "skipkeys",
})
#: Built-ins whose result contains no part of their argument.
_PURE_BUILTINS = frozenset({
    "len", "str", "repr", "bool", "int", "float", "isinstance", "print", "hash", "abs",
    "round", "any", "all", "sum",
})
_COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
_SCOPES = (
    ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef,
    ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp,
)
Span = tuple[int, int, int, int]


class ParameterOracle:
    """Interprocedural facts the module-local proof cannot see (see scan.py)."""

    def is_json_parameter(self, path: str, def_line: int, name: str) -> bool:
        """Does this parameter receive only JSON at every call (and stay clean)?"""
        return False

    def is_json_call(self, path: str, span: Span) -> bool:
        """Does the call at *span* resolve to a function whose returns are all JSON?"""
        return False

    def returns_proven(self, path: str, def_line: int) -> bool:
        """Are all returns of the function defined at *def_line* proved JSON?"""
        return False

    def is_immutable_parameter(self, path: str, def_line: int, name: str) -> bool:
        """Does this JSON parameter receive only immutable values (str, number,
        bool, None) at every call, so that it needs no policing?"""
        return False

    def argument_stays_json(self, path: str, call: Span, argument: Span) -> bool:
        """Is *argument* of the call at *call* received by a JSON-proven parameter?"""
        return False


def _span(node: ast.expr) -> Span | None:
    if node.end_lineno is None or node.end_col_offset is None:
        return None
    return (node.lineno, node.col_offset, node.end_lineno, node.end_col_offset)


class JsonOrigins:
    def __init__(
        self, source: bytes, filename: str, path: str = "", oracle: ParameterOracle | None = None
    ) -> None:
        self.path = path
        self.oracle = oracle or ParameterOracle()
        self.module = ast.parse(source, filename=filename)
        self.parent: dict[int, ast.AST] = {}
        self.by_position: dict[Span, list[ast.expr]] = {}
        for node in ast.walk(self.module):
            for child in ast.iter_child_nodes(node):
                self.parent[id(child)] = node
            if isinstance(node, ast.expr) and node.end_lineno is not None:
                key = (node.lineno, node.col_offset, node.end_lineno, node.end_col_offset or 0)
                self.by_position.setdefault(key, []).append(node)
        self.memo: dict[int, bool] = {}
        self.raw_memo: dict[int, bool] = {}
        self.clean_memo: dict[int, bool] = {}
        self.json_names = self._json_callables()

    @classmethod
    def for_file(
        cls, path: Path, relative: str = "", oracle: ParameterOracle | None = None
    ) -> JsonOrigins:
        return cls(path.read_bytes(), str(path), relative, oracle)

    def reset(self) -> None:
        """Forget memoised answers (the parameter oracle's assumptions changed)."""
        self.memo.clear()
        self.raw_memo.clear()
        self.clean_memo.clear()

    # -- the module's json bindings ------------------------------------------------

    def _json_callables(self) -> dict[str, str]:
        """Module-level names that mean json.loads / json.load / the json module."""
        bindings: dict[str, list[ast.AST]] = {}
        for node in ast.walk(self.module):
            for name, binder in _binders(node):
                bindings.setdefault(name, []).append(binder)
        meaning: dict[str, str] = {}
        for name, binders in bindings.items():
            if len(binders) != 1:
                continue
            binder = binders[0]
            if isinstance(binder, ast.Import):
                for alias in binder.names:
                    if alias.name == "json" and (alias.asname or "json") == name:
                        meaning[name] = "json"
            elif (
                isinstance(binder, ast.ImportFrom) and binder.module == "json" and not binder.level
            ):
                for alias in binder.names:
                    if alias.name in ("loads", "load") and (alias.asname or alias.name) == name:
                        meaning[name] = f"json.{alias.name}"
        return meaning

    # -- queries ------------------------------------------------------------------------

    def at(self, line: int, column: int, end_line: int | None, end_column: int | None) -> bool:
        """Is the expression spanning exactly this range JSON?"""
        if end_line is None or end_column is None:
            return False
        nodes = self.by_position.get((line, column, end_line, end_column), [])
        return bool(nodes) and all(self.is_json(node) for node in nodes)

    def is_json(self, node: ast.expr, *, raw: bool = False) -> bool:
        """JSON (in a JSON-clean function); *raw* skips the cleanliness requirement,
        which only decides which names are policed."""
        memo = self.raw_memo if raw else self.memo
        key = id(node)
        if key in memo:
            return memo[key]
        memo[key] = False  # in progress: a cycle is not a proof
        result = self._is_json(node, raw)
        memo[key] = result
        return result

    def _literal(self, node: ast.expr, raw: bool) -> bool:
        if isinstance(node, ast.Constant):
            return node.value is None or isinstance(node.value, (str, int, float, bool))
        if isinstance(node, ast.List):
            return all(self._literal(e, raw) for e in node.elts)
        if isinstance(node, ast.Dict):
            return all(
                isinstance(k, ast.Constant) and isinstance(k.value, str) for k in node.keys
            ) and all(self._literal(v, raw) for v in node.values)
        return False

    def _is_json(self, node: ast.expr, raw: bool) -> bool:
        if self._literal(node, raw):
            return True
        if isinstance(node, ast.Call):
            return self._json_call(node, raw)
        if isinstance(node, ast.Subscript):
            return self.is_json(node.value, raw=raw)
        if isinstance(node, ast.BoolOp):
            return all(self.is_json(value, raw=raw) for value in node.values)
        if isinstance(node, ast.IfExp):
            return self.is_json(node.body, raw=raw) and self.is_json(node.orelse, raw=raw)
        if isinstance(node, ast.Name):
            return self._json_name(node, raw)
        return False

    def _json_call(self, node: ast.Call, raw: bool) -> bool:
        func = node.func
        decoder = None
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            if self.json_names.get(func.value.id) == "json" and func.attr in ("loads", "load"):
                decoder = func.attr
        elif isinstance(func, ast.Name) and self.json_names.get(func.id) in (
            "json.loads", "json.load"
        ):
            decoder = func.id
        if decoder is not None:
            return len(node.args) == 1 and not node.keywords and not isinstance(
                node.args[0], ast.Starred
            )
        if (
            isinstance(func, ast.Attribute)
            and func.attr in _GETTERS
            and not node.keywords
            and self.is_json(func.value, raw=raw)
        ):
            defaults = node.args[1:]
            return len(node.args) in (1, 2) and all(
                self.is_json(d, raw=raw) or self._literal(d, raw) for d in defaults
            )
        if (
            isinstance(func, ast.Attribute)
            and (
                func.attr in _READ_METHODS
                or func.attr in _COUNTERS
                or (func.attr in _ITEM_VIEWS and not node.args and not node.keywords)
            )
            and self.is_json(func.value, raw=raw)
        ):
            # A new str/int/bool/list of str, or a view whose iteration yields
            # JSON values (only a `for` or comprehension may hold one: see
            # _use_is_clean).
            return True
        span = _span(node)
        if span is None or not self.oracle.is_json_call(self.path, span):
            return False
        if raw:
            return True
        # The callee may return an alias of an argument: the caller must be clean.
        function = self._function_of(node)
        return function is not None and self.function_clean(function)

    def returns_json(self, def_line: int) -> bool:
        """Every `return` in the function defined at *def_line* returns JSON.

        A generator never qualifies (its call returns a generator object).
        Falling off the end returns None, which is JSON.
        """
        function = self._function_at(def_line)
        if not isinstance(function, ast.FunctionDef):
            return False
        own = _own_nodes(function)
        if any(isinstance(node, (ast.Yield, ast.YieldFrom, ast.Await)) for node in own):
            return False
        return all(
            node.value is None or self.is_json(node.value)
            for node in own
            if isinstance(node, ast.Return)
        )

    def function_clean_at(self, def_line: int) -> bool:
        function = self._function_at(def_line)
        return function is not None and self.function_clean(function)

    def _function_at(self, def_line: int) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
        for node in ast.walk(self.module):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
                node.lineno == def_line
            ):
                return node
        return None

    def _iterates_json(self, iterable: ast.expr, raw: bool) -> str | None:
        """'value' if iterating yields JSON values, 'items' for `<json>.items()`."""
        if (
            isinstance(iterable, ast.Call)
            and isinstance(iterable.func, ast.Attribute)
            and iterable.func.attr in _ITEM_VIEWS
            and not iterable.args
            and not iterable.keywords
            and self.is_json(iterable.func.value, raw=raw)
        ):
            return "items" if iterable.func.attr == "items" else "value"
        return "value" if self.is_json(iterable, raw=raw) else None

    def _json_name(self, name: ast.Name, raw: bool) -> bool:
        scope = self._resolving_scope(name)
        if scope is None or isinstance(scope, (ast.Module, ast.ClassDef)):
            return False  # module and class names can be mutated from anywhere
        function = self._enclosing_function(scope)
        if function is None:
            return False
        binders = [b for b in _scope_bindings(scope) if b[0] == name.id]
        if not binders:
            return False
        if not all(self._binding_is_json(binder, target, raw) for _, binder, target in binders):
            return False
        return raw or self.function_clean(function)

    def _binding_is_json(self, binder: ast.AST, target: ast.AST, raw: bool) -> bool:
        if isinstance(binder, ast.arg):
            owner = self.parent.get(id(self.parent.get(id(binder), binder)))
            if isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef)) and binder in (
                *owner.args.posonlyargs, *owner.args.args, *owner.args.kwonlyargs
            ):
                return self.oracle.is_json_parameter(self.path, owner.lineno, binder.arg)
            return False
        if isinstance(binder, (ast.Assign, ast.AnnAssign)) and isinstance(
            binder.value, _DISPLAYS
        ):
            # A fresh container is not policed (it aliases no JSON), so it may be
            # filled with anything: never JSON.
            return False
        if isinstance(binder, ast.Assign):
            if len(binder.targets) != 1 or binder.targets[0] is not target:
                return False
            return self.is_json(binder.value, raw=raw)
        if isinstance(binder, ast.AnnAssign):
            return binder.value is not None and binder.target is target and self.is_json(
                binder.value, raw=raw
            )
        if isinstance(binder, (ast.For, ast.comprehension)):
            kind = self._iterates_json(binder.iter, raw)
            if kind is None:
                return False
            if binder.target is target:
                return kind == "value"
            if kind == "items" and isinstance(binder.target, ast.Tuple):
                return len(binder.target.elts) == 2 and target in binder.target.elts
            return False
        return False

    # -- cleanliness ------------------------------------------------------------------------

    def _function_of(self, node: ast.AST) -> ast.AST | None:
        """The function (def or lambda) whose body evaluates *node*."""
        current = self.parent.get(id(node))
        while current is not None and not isinstance(current, _FUNCTIONS):
            if isinstance(current, (ast.Module, ast.ClassDef)):
                return None
            current = self.parent.get(id(current))
        return current

    def _enclosing_function(self, scope: ast.AST) -> ast.AST | None:
        node: ast.AST | None = scope
        while node is not None and not isinstance(node, _FUNCTIONS):
            if isinstance(node, (ast.Module, ast.ClassDef)):
                return None
            node = self.parent.get(id(node))
        return node

    def _may_alias(
        self, node: ast.expr | None, policed: set[tuple[int, str]], sources: bool = True
    ) -> bool:
        """Could *node*'s value be, or contain, part of a JSON value (or of a
        policed name's value)? With *sources* false, only the policed names count
        (plus a call that may return an alias of its arguments)."""
        if node is None:
            return False
        if isinstance(node, ast.Starred):
            return self._may_alias(node.value, policed, sources)
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            return any(self._may_alias(e, policed, sources) for e in node.elts)
        if isinstance(node, ast.Dict):
            return any(self._may_alias(k, policed, sources) for k in node.keys) or any(
                self._may_alias(v, policed, sources) for v in node.values
            )
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
            return self._may_alias(node.elt, policed, sources)
        if isinstance(node, ast.DictComp):
            return self._may_alias(node.key, policed, sources) or self._may_alias(
                node.value, policed, sources
            )
        if sources and self.is_json(node, raw=True):
            return not _immutable(node)
        if not sources and isinstance(node, ast.Call):
            span = _span(node)
            if span is not None and self.oracle.is_json_call(self.path, span):
                return any(self._may_alias(a, policed, sources) for a in node.args) or any(
                    self._may_alias(k.value, policed, sources) for k in node.keywords
                )
        if isinstance(node, ast.Name):
            owner = self._resolving_scope(node)
            return owner is not None and (id(owner), node.id) in policed
        if isinstance(node, ast.Subscript):
            return self._may_alias(node.value, policed, sources)
        if isinstance(node, (ast.BoolOp,)):
            return any(self._may_alias(value, policed, sources) for value in node.values)
        if isinstance(node, ast.IfExp):
            return self._may_alias(node.body, policed, sources) or self._may_alias(
                node.orelse, policed, sources
            )
        if isinstance(node, ast.BinOp) and isinstance(node.op, _ALIASING_OPERATORS):
            return self._may_alias(node.left, policed, sources) or self._may_alias(
                node.right, policed, sources
            )
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in _GETTERS
        ):
            return self._may_alias(node.func.value, policed, sources) or any(
                self._may_alias(argument, policed, sources) for argument in node.args[1:]
            )
        return False

    def _policed(self, scopes: list[ast.AST], sources: bool) -> set[tuple[int, str]]:
        """The least set of names that may hold part of a JSON value: a name is
        policed when any of its bindings may alias JSON or a policed name.
        With *sources* false, only aliases of the function's parameters."""
        policed: set[tuple[int, str]] = set()
        while True:
            grown = {
                (id(scope), name)
                for scope in scopes
                for name, binder, _target in _scope_bindings(scope)
                if self._binder_may_alias(binder, policed, sources)
            }
            if grown == policed:
                return policed
            policed = grown

    def _binder_may_alias(
        self, binder: ast.AST, policed: set[tuple[int, str]], sources: bool = True
    ) -> bool:
        if isinstance(binder, ast.arg):
            owner = self.parent.get(id(self.parent.get(id(binder), binder)))
            return isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
                self.oracle.is_json_parameter(self.path, owner.lineno, binder.arg)
                and not self.oracle.is_immutable_parameter(self.path, owner.lineno, binder.arg)
            )
        if isinstance(binder, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
            return self._may_alias(binder.value, policed, sources)
        if isinstance(binder, (ast.For, ast.comprehension)):
            iterable = binder.iter
            if (
                isinstance(iterable, ast.Call)
                and isinstance(iterable.func, ast.Attribute)
                and iterable.func.attr in _ITEM_VIEWS
            ):
                return self._may_alias(iterable.func.value, policed, sources)
            return self._may_alias(iterable, policed, sources)
        return False

    def function_clean(self, function: ast.AST) -> bool:
        """Is every use of every policed name of *function* read-only (module docstring)?"""
        key = id(function)
        if key in self.clean_memo:
            return self.clean_memo[key]
        self.clean_memo[key] = False  # in progress: fail closed
        result = self._function_clean(function)
        self.clean_memo[key] = result
        return result

    def _function_clean(self, function: ast.AST) -> bool:
        scopes = [function] + [
            node for node in ast.walk(function)
            if isinstance(node, _COMPREHENSIONS) and self._enclosing_function(node) is function
        ]
        policed = self._policed(scopes, sources=True)
        if not policed:
            return True
        names = {name for _, name in policed}
        for node in ast.walk(function):
            if isinstance(node, ast.Nonlocal) and names & set(node.names):
                return False
            if not (isinstance(node, ast.Name) and node.id in names):
                continue
            if not isinstance(node.ctx, ast.Load):
                continue
            owner = self._resolving_scope(node)
            if owner is None or (id(owner), node.id) not in policed:
                continue
            if self._function_of(node) is not function:
                return False  # read by a closure: it may run at any later time
            if not self._use_is_clean(node, function, scopes):
                return False
        return True

    def _use_is_clean(self, node: ast.expr, function: ast.AST, scopes: list[ast.AST]) -> bool:
        current: ast.AST = node
        while True:
            parent = self.parent.get(id(current))
            if parent is None:
                return False
            if isinstance(parent, ast.Subscript):
                if parent.slice is current:
                    return True  # used as another object's key
                if not isinstance(parent.ctx, ast.Load):
                    return False  # a store or del through the value
                current = parent
                continue
            if isinstance(parent, ast.Attribute):
                call = self.parent.get(id(parent))
                if not (isinstance(call, ast.Call) and call.func is parent and not call.keywords):
                    return False
                if parent.attr in _GETTERS:
                    current = call
                    continue
                if parent.attr in _COUNTERS or parent.attr in _READ_METHODS:
                    return True
                if parent.attr in _ITEM_VIEWS and not call.args:
                    holder = self.parent.get(id(call))
                    return isinstance(holder, (ast.For, ast.comprehension)) and (
                        holder.iter is call
                    )
                return False
            if isinstance(parent, ast.Call):
                if parent.func is current:
                    return False
                verdict = self._argument_is_clean(parent, current)
                if verdict == "follow":
                    current = parent
                    continue
                return verdict == "clean"
            if isinstance(parent, (ast.Compare, ast.UnaryOp, ast.FormattedValue)):
                return True
            if isinstance(parent, ast.BinOp):
                if not isinstance(parent.op, _ALIASING_OPERATORS):
                    return True  # arithmetic or %-formatting: no container of its operands
                current = parent  # list + list, list * n, dict | dict share the items
                continue
            if isinstance(parent, ast.AugAssign):
                # `x += v` extends a list in place (any alias of x sees v's items).
                return parent.value is current and not isinstance(
                    parent.op, _ALIASING_OPERATORS
                )
            if isinstance(parent, (ast.If, ast.While, ast.Assert)):
                return parent.test is current or getattr(parent, "msg", None) is current
            if isinstance(parent, ast.Expr):
                return True
            if isinstance(parent, ast.For):
                return parent.iter is current
            if isinstance(parent, ast.comprehension):
                return parent.iter is current or current in parent.ifs
            if isinstance(parent, (ast.List, ast.Tuple, ast.Set, ast.Dict, ast.Starred)):
                if isinstance(parent, ast.Starred) and not isinstance(
                    self.parent.get(id(parent)), (ast.List, ast.Tuple, ast.Set)
                ):
                    return False
                current = parent  # a new container holding the value: followed
                continue
            if isinstance(parent, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
                if parent.elt is not current:
                    return False
                current = parent
                continue
            if isinstance(parent, ast.DictComp):
                if current not in (parent.key, parent.value):
                    return False
                current = parent
                continue
            if isinstance(parent, ast.IfExp):
                if parent.test is current:
                    return True
                current = parent
                continue
            if isinstance(parent, ast.BoolOp):
                current = parent
                continue
            if isinstance(parent, ast.Assign):
                return parent.value is current and all(
                    _plain_targets(target) for target in parent.targets
                )
            if isinstance(parent, ast.AnnAssign):
                return parent.value is current and isinstance(parent.target, ast.Name)
            if isinstance(parent, ast.Return):
                if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    return False
                if self.oracle.returns_proven(self.path, function.lineno):
                    return True  # the callers police the call's value
                # Otherwise the value must not alias anything that outlives the
                # call: JSON this function decoded dies with it; a parameter's
                # value belongs to a caller that would not police the result.
                return not self._may_alias(
                    parent.value, self._policed(scopes, sources=False), sources=False
                )
            return False

    def _argument_is_clean(self, call: ast.Call, argument: ast.AST) -> str:
        """'clean', 'dirty', or 'follow' (the call's result may alias the argument)."""
        if isinstance(argument, ast.Starred) or argument not in call.args:
            return "dirty"
        func = call.func
        if (
            isinstance(func, ast.Name)
            and func.id in _PURE_BUILTINS
            and self._resolving_scope(func) is None
        ):
            return "clean"
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "dumps"
            and isinstance(func.value, ast.Name)
            and self.json_names.get(func.value.id) == "json"
            and all(keyword.arg in _DUMPS_KEYWORDS for keyword in call.keywords)
        ):
            return "clean"
        if (
            isinstance(func, ast.Attribute)
            and func.attr in _GETTERS | _COUNTERS
            and not call.keywords
            and self.is_json(func.value, raw=True)
        ):
            if call.args[0] is argument:
                return "clean"  # a lookup key or a value compared
            return "follow" if func.attr in _GETTERS else "dirty"  # get's default
        call_span = _span(call)
        argument_span = _span(argument) if isinstance(argument, ast.expr) else None
        if call_span is None or argument_span is None:
            return "dirty"
        if not self.oracle.argument_stays_json(self.path, call_span, argument_span):
            return "dirty"
        # The callee may return the argument (or part of it): police the result.
        return "follow" if self.oracle.is_json_call(self.path, call_span) else "clean"

    def _resolving_scope(self, name: ast.Name) -> ast.AST | None:
        """The scope whose binding *name* refers to (Python's LEGB, no class scope)."""
        node: ast.AST = name
        first = True
        while True:
            parent = self.parent.get(id(node))
            if parent is None:
                return self.module if _bound_in(self.module, name.id) else None
            if isinstance(parent, _SCOPES):
                in_class = isinstance(parent, ast.ClassDef)
                if (first or not in_class) and _bound_in(parent, name.id):
                    if _declared_global(parent, name.id):
                        return self.module
                    return parent
                if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                    first = False
            node = parent


_DISPLAYS = (ast.List, ast.Dict, ast.Tuple, ast.Set)
#: Read methods whose result is an immutable str, int or bool.
_SCALAR_METHODS = (_READ_METHODS - {"split", "rsplit", "splitlines"}) | _COUNTERS


def immutable(node: ast.expr) -> bool:
    """Public form of `_immutable` for the interprocedural oracle."""
    return _immutable(node)


def _immutable(node: ast.expr) -> bool:
    """A JSON-proved expression whose value is immutable (so needs no policing)."""
    if isinstance(node, (ast.Constant, ast.JoinedStr, ast.Compare)):
        return True
    if isinstance(node, ast.UnaryOp):
        return True
    if isinstance(node, ast.Call):
        return isinstance(node.func, ast.Attribute) and node.func.attr in _SCALAR_METHODS
    if isinstance(node, ast.BoolOp):
        return all(_immutable(value) for value in node.values)
    if isinstance(node, ast.IfExp):
        return _immutable(node.body) and _immutable(node.orelse)
    return False


def _plain_targets(target: ast.AST) -> bool:
    """A name, or a tuple/list of names: every name it binds is policed in turn."""
    if isinstance(target, ast.Name):
        return True
    if isinstance(target, (ast.Tuple, ast.List)):
        return all(isinstance(element, ast.Name) for element in target.elts)
    return False


def _binders(node: ast.AST) -> list[tuple[str, ast.AST]]:
    """Names *node* binds directly, with the binding statement."""
    found: list[tuple[str, ast.AST]] = []
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        for alias in node.names:
            found.append(((alias.asname or alias.name).split(".")[0], node))
    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        found.append((node.name, node))
    elif isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load):
        found.append((node.id, node))
    return found


def _own_nodes(scope: ast.AST) -> list[ast.AST]:
    """Nodes in *scope*'s own namespace (nested scopes excluded, except the parts
    of a nested construct evaluated here: decorators, defaults, the first
    iterable of a comprehension)."""
    out: list[ast.AST] = []
    if isinstance(scope, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
        roots: list[ast.AST] = []
        for index, generator in enumerate(scope.generators):
            roots.append(generator.target)
            roots.extend(generator.ifs)
            roots.append(generator)
            if index:
                roots.append(generator.iter)
        roots.extend(
            [scope.key, scope.value] if isinstance(scope, ast.DictComp) else [scope.elt]
        )
    elif isinstance(scope, ast.Lambda):
        roots = [scope.args, scope.body]
    elif isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
        roots = [scope.args, *scope.body]
    else:
        roots = list(getattr(scope, "body", []))
    stack = list(roots)
    while stack:
        node = stack.pop()
        out.append(node)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            stack.extend(node.decorator_list)
            if not isinstance(node, ast.ClassDef):
                stack.extend(d for d in node.args.defaults if d is not None)
                stack.extend(d for d in node.args.kw_defaults if d is not None)
            continue
        if isinstance(node, ast.Lambda):
            stack.extend(node.args.defaults)
            continue
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            stack.append(node.generators[0].iter)
            continue
        stack.extend(ast.iter_child_nodes(node))
    return out


def _targets(node: ast.AST) -> list[ast.AST]:
    if isinstance(node, (ast.Assign, ast.Delete)):
        return list(node.targets)
    if isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
        return [node.target]
    if isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
        return [node.target]
    if isinstance(node, (ast.With, ast.AsyncWith)):
        return [item.optional_vars for item in node.items if item.optional_vars is not None]
    return []


_BINDINGS_CACHE: dict[int, tuple[ast.AST, list[tuple[str, ast.AST, ast.AST]]]] = {}


def _scope_bindings(scope: ast.AST) -> list[tuple[str, ast.AST, ast.AST]]:
    """(name, binding statement, target node) for every binding in *scope*."""
    cached = _BINDINGS_CACHE.get(id(scope))
    if cached is not None and cached[0] is scope:
        return cached[1]
    own = _own_nodes(scope)
    owner: dict[int, ast.AST] = {}
    for node in own:
        for target in _targets(node):
            for child in ast.walk(target):
                owner[id(child)] = node
    found: list[tuple[str, ast.AST, ast.AST]] = []
    for node in own:
        if isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load):
            found.append((node.id, owner.get(id(node), node), node))
        elif isinstance(node, ast.arg):
            found.append((node.arg, node, node))
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                found.append(((alias.asname or alias.name).split(".")[0], node, node))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node is not scope:
                found.append((node.name, node, node))
        elif isinstance(node, ast.ExceptHandler):
            if node.name:
                found.append((node.name, node, node))
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            for name in node.names:
                found.append((name, node, node))
        elif isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
            found.append((node.name, node, node))
        elif isinstance(node, ast.MatchMapping) and node.rest:
            found.append((node.rest, node, node))
    if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        arguments = scope.args
        for arg in [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs,
                    arguments.vararg, arguments.kwarg]:
            if arg is not None:
                found.append((arg.arg, arg, arg))
    _BINDINGS_CACHE[id(scope)] = (scope, found)
    return found


def _bound_in(scope: ast.AST, name: str) -> bool:
    return any(entry[0] == name for entry in _scope_bindings(scope))


def _declared_global(scope: ast.AST, name: str) -> bool:
    return any(
        isinstance(node, ast.Global) and name in node.names for node in _own_nodes(scope)
    )
