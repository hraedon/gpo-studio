"""A mypy plugin that types `args.<dest>` from the parser built in the same function.

Every builder and finalizer reads its command line through `argparse`, whose
`Namespace` attributes mypy types as `Any`. Left alone, every path a lane reads
(`args.run_dir / "result.json"`) is an `Any` receiver: unresolvable, so the
scan would fail on hundreds of sites whose real target is a plain
`pathlib.Path` method. This plugin gives those attributes the type argparse
itself guarantees, so the scan resolves the real target and classifies it.

Every decision that matters for soundness is made on the standard library's
`ast` of the function (complete by construction), not on mypy's tree. The
attribute is typed only when all of these hold, and is otherwise left `Any`
(unresolvable, so the scan fails closed):

- the receiver is a local name bound exactly once in the enclosing function
  (any binding form counts: assignment, `for`, `with`, walrus, `del`,
  `global`), by `args = <parser>.parse_args(...)`;
- `<parser>` is a local name bound exactly once, by
  `<parser> = argparse.ArgumentParser(...)` (or `ArgumentParser(...)` imported
  from argparse), and every other occurrence of the name in the function,
  nested scopes included, is `<parser>.<method>(...)` with `<method>` one of
  `add_argument`, `parse_args`, `error`, `print_help`: the parser never escapes,
  so no argument can be added where this cannot see it;
- exactly one `add_argument` call has that destination (the first `--long`
  flag, or the positional name), with no `dest=`, `nargs=` or `const=`, and
  its type is one argparse fixes: `type=` one of `int`, `float`, `str`,
  `Path` (bound by `from pathlib import Path` and nothing else in the module),
  or no `type=` (`str`), or `action="store_true"`/`"store_false"` (`bool`);
  an optional argument that is not `required=True` adds its default's type
  (`None` when there is none; a string default is converted by `type=`, as
  argparse does; any other default is typed by mypy, and if mypy cannot type
  it the attribute stays `Any`).
"""

from __future__ import annotations

import ast
import functools
from collections.abc import Callable
from pathlib import Path

from mypy.nodes import CallExpr, Expression, MemberExpr, NameExpr, Node
from mypy.plugin import AttributeContext, Plugin
from mypy.typeops import make_simplified_union
from mypy.types import AnyType, NoneType, Type, UnionType, get_proper_type

_PARSER_METHODS = frozenset({"add_argument", "parse_args", "error", "print_help"})
_BUILTIN_TYPES = {"int": "builtins.int", "float": "builtins.float", "str": "builtins.str"}


@functools.lru_cache(maxsize=256)
def _module_ast(path: str) -> ast.Module:
    return ast.parse(Path(path).read_bytes(), filename=path)


def _function_at(module: ast.Module, line: int) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    for node in ast.walk(module):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.lineno == line:
            return node
    return None


def _bindings(function: ast.AST, name: str) -> list[ast.AST]:
    """Every node in *function* that binds *name* (Store/Del contexts, global, nonlocal)."""
    return [node for node in ast.walk(function) if _binds(node, function, name)]


def _binds(node: ast.AST, function: ast.AST, name: str) -> bool:
    if isinstance(node, ast.Name):
        return node.id == name and not isinstance(node.ctx, ast.Load)
    if isinstance(node, (ast.Global, ast.Nonlocal)):
        return name in node.names
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return node.name == name and node is not function
    if isinstance(node, ast.arg):
        return node.arg == name
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        return any((alias.asname or alias.name).split(".")[0] == name for alias in node.names)
    return False


def _assigned_value(function: ast.AST, name: str) -> ast.expr | None:
    """The value of the single `name = <value>` binding, or None."""
    bindings = _bindings(function, name)
    if len(bindings) != 1:
        return None
    for node in ast.walk(function):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and node.targets[0] is bindings[0]
        ):
            return node.value
    return None


def _module_binds_only_import(module: ast.Module, name: str, source: str, attr: str) -> bool:
    """*name* is bound in the module only by `from <source> import <attr>`."""
    bindings = _bindings(module, name)
    if len(bindings) != 1 or not isinstance(bindings[0], ast.ImportFrom):
        return False
    node = bindings[0]
    return node.module == source and any(
        alias.name == attr and (alias.asname or alias.name) == name for alias in node.names
    )


def _is_argument_parser(module: ast.Module, value: ast.expr | None) -> bool:
    if not isinstance(value, ast.Call):
        return False
    func = value.func
    if isinstance(func, ast.Attribute) and func.attr == "ArgumentParser":
        return (
            isinstance(func.value, ast.Name)
            and func.value.id == "argparse"
            and not _bindings(module, "argparse")[1:]
        )
    if isinstance(func, ast.Name) and func.id == "ArgumentParser":
        return _module_binds_only_import(module, "ArgumentParser", "argparse", "ArgumentParser")
    return False


def _dest(call: ast.Call) -> str | None:
    flags = [
        a.value for a in call.args if isinstance(a, ast.Constant) and isinstance(a.value, str)
    ]
    if not flags or len(flags) != len(call.args):
        return None
    long = [f for f in flags if f.startswith("--")]
    if long:
        return long[0][2:].replace("-", "_")
    return None if flags[0].startswith("-") else flags[0]


def _parser_calls(function: ast.AST, parser: str) -> list[ast.Call] | None:
    """`parser.add_argument(...)` calls, or None if the parser is used any other way."""
    allowed: set[int] = set()
    calls: list[ast.Call] = []
    for node in ast.walk(function):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == parser
        ):
            if node.func.attr not in _PARSER_METHODS:
                return None
            allowed.add(id(node.func.value))
            if node.func.attr == "add_argument":
                calls.append(node)
    for node in ast.walk(function):
        if (
            isinstance(node, ast.Name)
            and node.id == parser
            and isinstance(node.ctx, ast.Load)
            and id(node) not in allowed
        ):
            return None
    return calls


def _attribute_type(ctx: AttributeContext) -> Type:
    member = ctx.context
    default = ctx.default_attr_type
    if not isinstance(member, MemberExpr) or not isinstance(member.expr, NameExpr):
        return default
    scope = getattr(ctx.api, "scope", None)
    function_node = scope.current_function() if scope is not None else None
    path = getattr(ctx.api, "path", None)
    if function_node is None or not path:
        return default
    module = _module_ast(path)
    function = _function_at(module, function_node.line)
    if function is None:
        return default
    receiver = member.expr.name
    parse = _assigned_value(function, receiver)
    if not (
        isinstance(parse, ast.Call)
        and isinstance(parse.func, ast.Attribute)
        and parse.func.attr == "parse_args"
        and isinstance(parse.func.value, ast.Name)
    ):
        return default
    parser = parse.func.value.id
    if not _is_argument_parser(module, _assigned_value(function, parser)):
        return default
    calls = _parser_calls(function, parser)
    if calls is None:
        return default
    matches = [call for call in calls if _dest(call) == member.name]
    if len(matches) != 1:
        return default
    found = _argument_type(ctx, module, matches[0], function_node)
    return found if found is not None else default


def _argument_type(
    ctx: AttributeContext, module: ast.Module, call: ast.Call, function_node: Node
) -> Type | None:
    keywords = {keyword.arg: keyword.value for keyword in call.keywords}
    if None in keywords or any(name in keywords for name in ("nargs", "dest", "const")):
        return None
    first = call.args[0]
    optional = (
        isinstance(first, ast.Constant) and isinstance(first.value, str)
        and first.value.startswith("-")
    )
    action = keywords.get("action")
    if action is not None:
        if isinstance(action, ast.Constant) and action.value in ("store_true", "store_false"):
            if "type" in keywords or "default" in keywords:
                return None
            return ctx.api.named_generic_type("builtins.bool", [])
        return None
    converter = keywords.get("type")
    if converter is None:
        base = "builtins.str"
    elif isinstance(converter, ast.Name) and converter.id in _BUILTIN_TYPES:
        if _bindings(module, converter.id):
            return None
        base = _BUILTIN_TYPES[converter.id]
    elif isinstance(converter, ast.Name) and converter.id == "Path":
        if not _module_binds_only_import(module, "Path", "pathlib", "Path"):
            return None
        base = "pathlib.Path"
    else:
        return None
    value_type = ctx.api.named_generic_type(base, [])
    required = keywords.get("required")
    if not optional or (isinstance(required, ast.Constant) and required.value is True):
        return value_type
    default = keywords.get("default")
    if default is None or (isinstance(default, ast.Constant) and default.value is None):
        return UnionType.make_union([value_type, NoneType()])
    if isinstance(default, ast.Constant) and isinstance(default.value, str):
        return value_type  # argparse converts a string default with type=
    expression = _mypy_expression_at(function_node, default)
    if expression is None:
        return None
    default_type = ctx.api.get_expression_type(expression)
    if isinstance(get_proper_type(default_type), AnyType):
        return None
    return make_simplified_union([value_type, default_type])


def _mypy_expression_at(function_node: Node, target: ast.expr) -> Expression | None:
    """The mypy expression for the stdlib *target* (same start and end), if found."""
    stack: list[object] = [function_node]
    while stack:
        item = stack.pop()
        if isinstance(item, (list, tuple)):
            stack.extend(item)
            continue
        if not isinstance(item, Node):
            continue
        if (
            isinstance(item, Expression)
            and item.line == target.lineno
            and item.column == target.col_offset
            and item.end_line == target.end_lineno
            and item.end_column == target.end_col_offset
        ):
            return item
        for attr in ("body", "expr", "rvalue", "args", "callee", "items", "left", "right",
                     "else_body", "operands", "base", "index"):
            value = getattr(item, attr, None)
            if isinstance(value, (Node, list, tuple)) and value is not item:
                stack.append(value)
        if isinstance(item, CallExpr):
            stack.extend(item.args)
    return None


class ArgparsePlugin(Plugin):
    def get_attribute_hook(self, fullname: str) -> Callable[[AttributeContext], Type] | None:
        if fullname.startswith("argparse.Namespace."):
            return _attribute_type
        return None
