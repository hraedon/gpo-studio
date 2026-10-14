"""Which `Any`-typed expressions provably hold a value `json.loads` produced.

`json.loads` and `json.load` (with no `cls`, `object_hook`, `object_pairs_hook`,
`parse_float`, `parse_int` or `parse_constant` argument) return only `dict`,
`list`, `str`, `int`, `float`, `bool` or `None`, and every method and protocol
operation of those types is in-memory. mypy types the result `Any`, so without
this rule every operation the finalizers perform on the JSON they read would
be an unresolvable `Any` receiver. With it, an operation on a value this
module proves is JSON is classified under the reviewed `<json>` inert entry;
an operation on an `Any` value it cannot prove stays unresolvable.

The proof is syntactic, on the standard library's `ast` (complete by
construction), with Python's scoping rules. An expression is JSON when it is:

- a call of a repository function every one of whose `return` statements
  returns JSON (the interprocedural oracle resolves the call and decides);
- a call of `json.loads`/`json.load` (the module bound only by `import json`,
  or the function only by `from json import loads/load`) with exactly one
  positional argument and no keyword argument;
- a subscript of a JSON value;
- `<json>.get(k)`, `.get(k, d)`, `.pop(k)`, `.pop(k, d)`, `.setdefault(k, d)`
  with `d` JSON or a JSON literal;
- `x or y`, `x and y`, `a if c else b` whose value operands are JSON or
  JSON literals;
- a JSON literal: `None`, a `str`/`int`/`float`/`bool` constant, or a list or
  dict display of JSON values (a JSON value is never mutated into a non-JSON
  one by these modules' code, and if it were the mutation site's own target
  would be classified);
- a name whose every binding in the scope it resolves to is one of: a plain
  assignment of a JSON value; a `for`/comprehension target iterating a JSON
  value, or a `(k, v)` target iterating `<json>.items()`; a parameter of a
  function the interprocedural oracle proves receives JSON at every call
  (`scan.JsonParameters`); and a name with any other binding (another
  parameter, `with`, `except`, `import`, augmented assignment, walrus,
  `global`/`nonlocal`, `del`) is not JSON.

Iterating `<json>.items()`, `.values()` or `.keys()` yields JSON (keys are
`str`). A cycle in the bindings is not JSON (fail closed).
"""

from __future__ import annotations

import ast
from pathlib import Path

_GETTERS = frozenset({"get", "pop", "setdefault"})
_ITEM_VIEWS = frozenset({"items", "values", "keys"})
_SCOPES = (
    ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef,
    ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp,
)


class ParameterOracle:
    """Interprocedural facts the module-local proof cannot see (see scan.py)."""

    def is_json_parameter(self, path: str, def_line: int, name: str) -> bool:
        """Does this parameter receive only JSON at every call?"""
        return False

    def is_json_call(self, path: str, span: tuple[int, int, int, int]) -> bool:
        """Does the call at *span* resolve to a function whose returns are all JSON?"""
        return False


class JsonOrigins:
    def __init__(
        self, source: bytes, filename: str, path: str = "", oracle: ParameterOracle | None = None
    ) -> None:
        self.path = path
        self.oracle = oracle or ParameterOracle()
        self.module = ast.parse(source, filename=filename)
        self.parent: dict[int, ast.AST] = {}
        self.by_position: dict[tuple[int, int, int, int], list[ast.expr]] = {}
        for node in ast.walk(self.module):
            for child in ast.iter_child_nodes(node):
                self.parent[id(child)] = node
            if isinstance(node, ast.expr) and node.end_lineno is not None:
                key = (node.lineno, node.col_offset, node.end_lineno, node.end_col_offset or 0)
                self.by_position.setdefault(key, []).append(node)
        self.memo: dict[int, bool] = {}
        self.json_names = self._json_callables()

    @classmethod
    def for_file(
        cls, path: Path, relative: str = "", oracle: ParameterOracle | None = None
    ) -> JsonOrigins:
        return cls(path.read_bytes(), str(path), relative, oracle)

    def reset(self) -> None:
        """Forget memoised answers (the parameter oracle's assumptions changed)."""
        self.memo.clear()

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

    def is_json(self, node: ast.expr) -> bool:
        key = id(node)
        if key in self.memo:
            return self.memo[key]
        self.memo[key] = False  # in progress: a cycle is not a proof
        result = self._is_json(node)
        self.memo[key] = result
        return result

    def _literal(self, node: ast.expr) -> bool:
        if isinstance(node, ast.Constant):
            return node.value is None or isinstance(node.value, (str, int, float, bool))
        if isinstance(node, ast.List):
            return all(self.is_json(e) or self._literal(e) for e in node.elts)
        if isinstance(node, ast.Dict):
            return all(k is not None for k in node.keys) and all(
                self.is_json(v) or self._literal(v) for v in node.values
            ) and all(isinstance(k, ast.Constant) and isinstance(k.value, str) for k in node.keys)
        return False

    def _is_json(self, node: ast.expr) -> bool:
        if self._literal(node):
            return True
        if isinstance(node, ast.Call):
            return self._json_call(node)
        if isinstance(node, ast.Subscript):
            return self.is_json(node.value)
        if isinstance(node, ast.BoolOp):
            return all(self.is_json(value) for value in node.values)
        if isinstance(node, ast.IfExp):
            return self.is_json(node.body) and self.is_json(node.orelse)
        if isinstance(node, ast.Name):
            return self._json_name(node)
        return False

    def _json_call(self, node: ast.Call) -> bool:
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
            and self.is_json(func.value)
        ):
            defaults = node.args[1:]
            return len(node.args) in (1, 2) and all(
                self.is_json(d) or self._literal(d) for d in defaults
            )
        if node.end_lineno is None or node.end_col_offset is None:
            return False
        return self.oracle.is_json_call(
            self.path, (node.lineno, node.col_offset, node.end_lineno, node.end_col_offset)
        )

    def returns_json(self, def_line: int) -> bool:
        """Every `return` in the function defined at *def_line* returns JSON.

        A generator never qualifies (its call returns a generator object).
        Falling off the end returns None, which is JSON.
        """
        function = next(
            (
                node for node in ast.walk(self.module)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.lineno == def_line
            ),
            None,
        )
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

    def _iterates_json(self, iterable: ast.expr) -> str | None:
        """'value' if iterating yields JSON values, 'items' for `<json>.items()`."""
        if (
            isinstance(iterable, ast.Call)
            and isinstance(iterable.func, ast.Attribute)
            and iterable.func.attr in _ITEM_VIEWS
            and not iterable.args
            and not iterable.keywords
            and self.is_json(iterable.func.value)
        ):
            return "items" if iterable.func.attr == "items" else "value"
        return "value" if self.is_json(iterable) else None

    def _json_name(self, name: ast.Name) -> bool:
        scope = self._resolving_scope(name)
        if scope is None:
            return False
        binders = [b for b in _scope_bindings(scope) if b[0] == name.id]
        if not binders:
            return False
        return all(self._binding_is_json(binder, target) for _, binder, target in binders)

    def _binding_is_json(self, binder: ast.AST, target: ast.AST) -> bool:
        if isinstance(binder, ast.arg):
            owner = self.parent.get(id(self.parent.get(id(binder), binder)))
            if isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef)) and binder in (
                *owner.args.posonlyargs, *owner.args.args, *owner.args.kwonlyargs
            ):
                return self.oracle.is_json_parameter(self.path, owner.lineno, binder.arg)
            return False
        if isinstance(binder, ast.Assign):
            if len(binder.targets) != 1 or binder.targets[0] is not target:
                return False
            return self.is_json(binder.value)
        if isinstance(binder, ast.AnnAssign):
            return binder.value is not None and binder.target is target and self.is_json(
                binder.value
            )
        if isinstance(binder, (ast.For, ast.comprehension)):
            kind = self._iterates_json(binder.iter)
            if kind is None:
                return False
            if binder.target is target:
                return kind == "value"
            if kind == "items" and isinstance(binder.target, ast.Tuple):
                return len(binder.target.elts) == 2 and target in binder.target.elts
            return False
        return False

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
