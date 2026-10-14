"""Part 1: the static import closure of a lane's processes.

Re-derived from the tree, never traced. A lane runs two Python processes on the
controller, the builder and the finalizer (WP-0 has only the finalizer), each
launched by its runner as `uv run python <script> ...`. For each process this
walks every `import` and `from ... import` statement in every module it can
reach, at any depth (inside functions, under `if TYPE_CHECKING:`, in `try:`
blocks: a superset, never a guess about which branch runs), and resolves each
name the way the interpreter would for that process:

1. built-in modules (`sys.builtin_module_names`) first, as CPython's
   BuiltinImporter does;
2. then the process's `sys.path`, in order: any entry the scan's declarations
   say the process inserts at the front (the RSoP builder's `src/`), the
   script's own directory (not under `-I`/`-P`, which no runner uses), the
   standard library, and the environment's site-packages, where the editable
   install puts `src/` last.

A name found as a file in a repository directory on that path is a repository
module and joins the closure, with every enclosing package's `__init__.py`
(importing `gpo_studio.model` executes `gpo_studio/__init__.py` first, which is
the line D-9's exemption exists for). A name the standard library provides is
recorded as external. A name found nowhere in the repository and not in the
standard library is recorded as external too, by name: the closure gate pins
the external set, so a new third-party import is an added edge.

**An interpretation for the reviewers to check.** An import under
`if TYPE_CHECKING:` never executes (`TYPE_CHECKING` is False at run time), so
it is left out of the run-time closure. The plan does not say this; it says
"imports at any depth". Without it, `model.py`'s type-only imports of `gpp`
and `fdeploy` would put those modules in every lane's closure, and D-9's
option 3 would select all 26 lanes. The guard is honoured only when it
resolves to `typing.TYPE_CHECKING` or `typing_extensions.TYPE_CHECKING`:

- a bare name bound in the module only by `from typing import TYPE_CHECKING`
  or `from typing_extensions import TYPE_CHECKING` (aliased with `as` or not);
- or `<m>.TYPE_CHECKING`, where `<m>` is bound in the module only by
  `import typing` or `import typing_extensions` (aliased or not).

Any other name, including a local `TYPE_CHECKING = ...`, a `TYPE_CHECKING`
imported from anywhere else, or one also bound a second way, makes the
guarded imports run-time imports. The `else:` branch of a real guard does run
and is walked. Type-only imports are recorded beside the closure
(`ProcessClosure.type_only`), so the record shows what was left out and why.

A module loaded dynamically (`runpy.run_path`, `importlib.util.
spec_from_file_location`) is not an import statement; the scan finds the call
and the reviewed declarations say which repository file it loads. Such a file
joins the closure of the process that loads it, and its own imports resolve
against that process's path, not the file's directory (`runpy.run_path` of a
`.py` file does not touch `sys.path`).

A namespace package (a directory without `__init__.py`) that would satisfy an
import is refused: none exists in the repository, and the rule for which
portion wins is not one this derivation should be trusted to reproduce.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .repo import Wp0Error

#: Marker entries in a process's path list.
STDLIB = "<stdlib>"
SITE = "<site-packages>"
#: Where the editable install puts the package source, relative to the root.
SRC = "src"


@dataclass(frozen=True)
class ImportEdge:
    importer: str  # repository-relative path of the importing module
    line: int
    name: str  # the dotted module name as written (resolved for relative imports)
    resolved: str | None  # repository-relative path, or None for an external module


@dataclass
class ProcessClosure:
    """The closure of one Python process (a builder or a finalizer run)."""

    entry: str
    path_entries: tuple[str, ...]
    modules: dict[str, str] = field(default_factory=dict)  # path -> module name
    external: set[str] = field(default_factory=set)  # top-level external names
    edges: list[ImportEdge] = field(default_factory=list)
    #: (importer, line, module) imported only under `if TYPE_CHECKING:`
    type_only: list[tuple[str, int, str]] = field(default_factory=list)

    def paths(self) -> list[str]:
        return sorted(self.modules)


def process_path(entry: str, front: tuple[str, ...] = ()) -> tuple[str, ...]:
    """The path list a `python <entry>` process searches, as repository-relative dirs.

    *front* are directories the process's own code inserts at `sys.path[0]`;
    they are taken as present for every import (a superset: an import that
    ran before the insertion would resolve without it, and both resolutions
    are taken, see `_resolve_top`).
    """
    script_dir = Path(entry).parent.as_posix()
    return (*front, script_dir, STDLIB, SITE)


class _Resolver:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _module_file(self, directory: str, dotted: list[str]) -> str | None:
        """`directory/a/b.py` or `directory/a/b/__init__.py` for dotted [a, b]."""
        base = self.root / directory if directory not in ("", ".") else self.root
        target = base.joinpath(*dotted)
        package = target / "__init__.py"
        module = target.with_name(target.name + ".py")
        if package.is_file():
            return package.relative_to(self.root).as_posix()
        if module.is_file():
            return module.relative_to(self.root).as_posix()
        if target.is_dir() and any(target.glob("*.py")):
            raise Wp0Error(
                f"{target.relative_to(self.root).as_posix()} would be imported as a "
                "namespace package; the closure refuses to model one"
            )
        return None

    def resolve_top(self, name: str, path_entries: tuple[str, ...]) -> tuple[str, str] | None:
        """(directory, file) for top-level *name*, or None when it is external."""
        if name in sys.builtin_module_names:
            return None
        for entry in path_entries:
            if entry == STDLIB:
                if name in sys.stdlib_module_names:
                    return None
                continue
            if entry == SITE:
                found = self._module_file(SRC, [name])
                return (SRC, found) if found else None
            found = self._module_file(entry, [name])
            if found:
                return (entry, found)
        return None


def _package_of(module_name: str, path: str) -> str:
    if path.endswith("/__init__.py"):
        return module_name
    return module_name.rpartition(".")[0]


_TYPING_MODULES = frozenset({"typing", "typing_extensions"})


def _type_checking_guards(tree: ast.Module) -> tuple[set[str], set[str]]:
    """Names meaning TYPE_CHECKING, and names meaning typing or typing_extensions.

    A name counts only if every binding of it in the module is that import.
    """
    bindings: dict[str, list[ast.AST]] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                bindings.setdefault((alias.asname or alias.name).split(".")[0], []).append(node)
        elif isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load):
            bindings.setdefault(node.id, []).append(node)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bindings.setdefault(node.name, []).append(node)
        elif isinstance(node, ast.arg):
            bindings.setdefault(node.arg, []).append(node)
    constants: set[str] = set()
    modules: set[str] = set()
    for name, binders in bindings.items():
        if len(binders) != 1:
            continue
        binder = binders[0]
        if (
            isinstance(binder, ast.ImportFrom)
            and binder.module in _TYPING_MODULES
            and not binder.level
        ):
            if any(a.name == "TYPE_CHECKING" and (a.asname or a.name) == name
                   for a in binder.names):
                constants.add(name)
        elif isinstance(binder, ast.Import) and any(
            a.name in _TYPING_MODULES and (a.asname or a.name) == name for a in binder.names
        ):
            modules.add(name)
    return constants, modules


def _is_type_checking(test: ast.expr, constants: set[str], modules: set[str]) -> bool:
    if isinstance(test, ast.Name):
        return test.id in constants
    return (
        isinstance(test, ast.Attribute)
        and test.attr == "TYPE_CHECKING"
        and isinstance(test.value, ast.Name)
        and test.value.id in modules
    )


def _imports(
    tree: ast.AST,
) -> tuple[list[tuple[int, int, str | None, list[str]]], list[tuple[int, str]]]:
    """(line, level, module, names) for every run-time import at any depth, and
    (line, module) for the imports only under `if TYPE_CHECKING:`."""
    constants, modules = (
        _type_checking_guards(tree) if isinstance(tree, ast.Module) else (set(), set())
    )
    found: list[tuple[int, int, str | None, list[str]]] = []
    type_only: list[tuple[int, str]] = []
    stack: list[tuple[ast.AST, bool]] = [(tree, False)]
    while stack:
        node, guarded = stack.pop()
        if isinstance(node, ast.If) and _is_type_checking(node.test, constants, modules):
            stack.extend((child, True) for child in node.body)
            stack.extend((child, guarded) for child in node.orelse)
            stack.append((node.test, guarded))
            continue
        if isinstance(node, ast.Import):
            for alias in node.names:
                if guarded:
                    type_only.append((node.lineno, alias.name))
                else:
                    found.append((node.lineno, 0, alias.name, []))
        elif isinstance(node, ast.ImportFrom):
            if guarded:
                names = ", ".join(alias.name for alias in node.names)
                type_only.append(
                    (node.lineno, f"{'.' * node.level}{node.module or ''} import {names}")
                )
            else:
                found.append(
                    (node.lineno, node.level, node.module, [alias.name for alias in node.names])
                )
        stack.extend((child, guarded) for child in ast.iter_child_nodes(node))
    return found, type_only


def derive_process(
    root: Path,
    entry: str,
    *,
    front: tuple[str, ...] = (),
    loaded: tuple[str, ...] = (),
) -> ProcessClosure:
    """The closure of the process that runs *entry* (and loads *loaded* files)."""
    if not (root / entry).is_file():
        raise Wp0Error(f"{entry}: no such file under {root}")
    variants = [process_path(entry, front)]
    if front:
        variants.append(process_path(entry))
    resolver = _Resolver(root)
    closure = ProcessClosure(entry=entry, path_entries=variants[0])
    pending: list[tuple[str, str]] = [(entry, "__main__")]
    for extra in loaded:
        if not (root / extra).is_file():
            raise Wp0Error(f"declared module load {extra}: no such file")
        pending.append((extra, f"<loaded:{extra}>"))
    while pending:
        path, name = pending.pop()
        if path in closure.modules:
            continue
        closure.modules[path] = name
        source = (root / path).read_bytes()
        try:
            tree = ast.parse(source, filename=path)
        except SyntaxError as exc:
            raise Wp0Error(f"{path}: does not parse: {exc}") from exc
        runtime, type_only = _imports(tree)
        closure.type_only.extend((path, line, module) for line, module in type_only)
        for line, level, module, names in runtime:
            if level:
                if not path.startswith(SRC + "/") or name.startswith(("__main__", "<loaded")):
                    raise Wp0Error(f"{path}:{line}: a relative import outside a package")
                package = _package_of(name, path).split(".")
                if level - 1 > len(package) - 1:
                    raise Wp0Error(f"{path}:{line}: a relative import beyond the top package")
                base = package[: len(package) - (level - 1)]
                dotted = [*base, *(module.split(".") if module else [])]
            else:
                assert module is not None
                dotted = module.split(".")
            from_names = names if names != ["*"] else []
            for variant in variants:
                _resolve_chain(resolver, closure, pending, path, line, dotted, from_names, variant)
    return closure


def _resolve_chain(
    resolver: _Resolver,
    closure: ProcessClosure,
    pending: list[tuple[str, str]],
    importer: str,
    line: int,
    dotted: list[str],
    from_names: list[str],
    path_entries: tuple[str, ...],
) -> None:
    top = resolver.resolve_top(dotted[0], path_entries)
    if top is None:
        closure.external.add(dotted[0])
        closure.edges.append(ImportEdge(importer, line, ".".join(dotted), None))
        return
    directory, found = top
    chain = [(dotted[0], found)]
    for depth in range(2, len(dotted) + 1):
        sub = resolver._module_file(directory, dotted[:depth])
        if sub is None:
            # An attribute of the package rather than a submodule file: a
            # missing submodule is an ImportError at run time, which no lane
            # would have passed with; recorded so the record shows it.
            closure.edges.append(ImportEdge(importer, line, ".".join(dotted[:depth]), None))
            break
        chain.append((".".join(dotted[:depth]), sub))
    for module_name, module_path in chain:
        pending.append((module_path, module_name))
        closure.edges.append(ImportEdge(importer, line, module_name, module_path))
    if len(chain) == len(dotted):
        for name in from_names:
            sub = resolver._module_file(directory, [*dotted, name])
            if sub is not None:
                full = ".".join([*dotted, name])
                pending.append((sub, full))
                closure.edges.append(ImportEdge(importer, line, full, sub))
