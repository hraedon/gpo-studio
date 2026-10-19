"""A pattern ending in `$` and called with match() or search() accepts a trailing newline.

`$` also matches just before one final "\\n", so `re.match(r"^x$", "x\\n")`
succeeds. The final-r4 to r6 reviews found this in the release-identity writer,
the release gate, the workflow grammar's action inputs, and authoring paths
where it let an LF reach Groups.xml and the PowerShell plan. Whole-value checks
use `fullmatch` (or end in `\\Z`).

This test finds every such site across `src/`, `scripts/` and the grammar and
test helper modules, and fails on any not in :data:`LEFT` (each of which says
why it stays). It follows the forms the final-r6 and r7 reviews named, each
planted below: a plain, annotated or walrus binding of a compiled pattern; the
pattern positional or as `pattern=`; built with `+` or an f-string; a
module-level string constant; a pattern imported from another scanned module
(absolute or relative, aliased or not); `re` imported under another name, or
`compile`/`match`/`search` imported from it; a compiled pattern passed back
to `re.match`; and a chained `re.compile(...).match(...)`. An inline site is
keyed by its pattern, so a second inline site in the same file is a new key.

It is a static scan, not a proof: a pattern that only exists at run time
(built in a function, held on an attribute, passed in as an argument) is not
followed. Those sites are reviewed by hand, as before.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCOPE = (
    "src/gpo_studio/*.py",
    "scripts/*.py",
    "scripts/plan-033/*.py",
    "scripts/windows-oracle/*.py",
    "tests/workflow_grammar.py",
    "tests/batch_provenance.py",
    "tests/current_qualification.py",
    "tests/manifest_comparator.py",
)
#: Left on purpose: (file, pattern name, or "re.<call>:<pattern>") -> why.
LEFT: dict[tuple[str, str], str] = {
    ("scripts/check_release_manifest.py", "_SETEXT_UNDERLINE"): (
        "line-oriented: applied to elements of text.split('\\n'), which hold no newline"
    ),
    ("tests/workflow_grammar.py", "CONFIG_PATTERNS"): (
        "over-matching is the safe direction: a name it matches must be a reviewed "
        "config, so a trailing newline makes a name more suspect, never less"
    ),
}


def _ends_in_dollar(pattern: str | None) -> bool:
    return pattern is not None and pattern.endswith("$") and not pattern.endswith("\\$")


@dataclass
class _Module:
    path: str
    tree: ast.Module
    strings: dict[str, str]  # module-level string constants
    anchored: dict[str, str]  # compiled-pattern name -> pattern tail
    re_names: frozenset[str]  # names the re module is bound to
    re_funcs: dict[str, str]  # local name -> re function it is (compile/match/search)


def _tail(node: ast.expr | None, strings: dict[str, str]) -> str | None:
    """The end of a pattern expression, as far as it can be read statically."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr) and node.values:
        last = node.values[-1]
        if isinstance(last, ast.Constant) and isinstance(last.value, str):
            return last.value
        return None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _tail(node.right, strings)
    if isinstance(node, ast.Name):
        return strings.get(node.id)
    return None


def _re_aliases(tree: ast.Module) -> tuple[frozenset[str], dict[str, str]]:
    names = {"re"}
    funcs: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.asname or a.name for a in node.names if a.name == "re"}
        elif isinstance(node, ast.ImportFrom) and node.module == "re" and not node.level:
            for alias in node.names:
                if alias.name in ("compile", "match", "search"):
                    funcs[alias.asname or alias.name] = alias.name
    return frozenset(names), funcs


def _re_call(
    node: ast.expr | None, re_names: frozenset[str], re_funcs: dict[str, str]
) -> str | None:
    """Which re function a call is (``re.x(...)``, ``rx.x(...)`` or an imported ``x``)."""
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id in re_names
    ):
        return func.attr
    if isinstance(func, ast.Name):
        return re_funcs.get(func.id)
    return None


def _pattern_arg(node: ast.Call) -> ast.expr | None:
    if node.args:
        return node.args[0]
    return next((k.value for k in node.keywords if k.arg == "pattern"), None)


def _compile_tail(
    node: ast.expr | None,
    strings: dict[str, str],
    re_names: frozenset[str] = frozenset({"re"}),
    re_funcs: dict[str, str] | None = None,
) -> str | None:
    """The pattern tail of ``re.compile(<pattern>, ...)`` in any spelling, or None."""
    if _re_call(node, re_names, re_funcs or {}) == "compile":
        assert isinstance(node, ast.Call)
        return _tail(_pattern_arg(node), strings)
    return None


def _targets(node: ast.AST) -> tuple[list[ast.expr], ast.expr | None]:
    if isinstance(node, ast.Assign):
        return list(node.targets), node.value
    if isinstance(node, ast.AnnAssign):
        return [node.target], node.value
    if isinstance(node, ast.NamedExpr):
        return [node.target], node.value
    return [], None


def _read(root: Path, relative: str) -> _Module:
    tree = ast.parse((root / relative).read_text(encoding="utf-8"), filename=relative)
    re_names, re_funcs = _re_aliases(tree)
    strings: dict[str, str] = {}
    anchored: dict[str, str] = {}
    for _ in range(2):  # constants that build on constants
        for node in ast.walk(tree):
            targets, value = _targets(node)
            for target in targets:
                if not isinstance(target, ast.Name):
                    continue
                text = _tail(value, strings)
                if text is not None and not isinstance(value, ast.Call):
                    strings[target.id] = text
                compiled = _compile_tail(value, strings, re_names, re_funcs)
                if compiled is not None:
                    anchored[target.id] = compiled
    return _Module(
        relative,
        tree,
        strings,
        {k: v for k, v in anchored.items() if _ends_in_dollar(v)},
        re_names,
        re_funcs,
    )


def _module_file(importer: str, node: ast.ImportFrom, files: set[str]) -> str | None:
    """The scanned file a ``from X import ...`` names, if any."""
    parts = importer.split("/")[:-1]
    if node.level:
        base = parts[: len(parts) - (node.level - 1)] if node.level > 1 else parts
        dotted = [*base, *((node.module or "").split(".") if node.module else [])]
    else:
        dotted = ["src", *node.module.split(".")] if node.module else []
    candidate = "/".join(dotted) + ".py"
    return candidate if candidate in files else None


def scan(root: Path, scope: tuple[str, ...] = SCOPE) -> set[tuple[str, str]]:
    """Every (file, key) where `$` with match()/search() accepts a trailing newline."""
    files = sorted({p.relative_to(root).as_posix() for g in scope for p in root.glob(g)})
    modules = {relative: _read(root, relative) for relative in files}
    found: set[tuple[str, str]] = set()
    for relative, module in modules.items():
        anchored = dict(module.anchored)
        for node in ast.walk(module.tree):
            if isinstance(node, ast.ImportFrom):
                source = _module_file(relative, node, set(modules))
                if source is None:
                    continue
                for alias in node.names:
                    if alias.name in modules[source].anchored:
                        anchored[alias.asname or alias.name] = modules[source].anchored[alias.name]
        for node in ast.walk(module.tree):
            if not isinstance(node, ast.Call):
                continue
            kind = _re_call(node, module.re_names, module.re_funcs)
            if kind in ("match", "search"):
                first = _pattern_arg(node)
                if isinstance(first, ast.Name) and first.id in anchored:
                    found.add((relative, first.id))
                    continue
                compiled = _compile_tail(first, module.strings, module.re_names, module.re_funcs)
                pattern = compiled if compiled is not None else _tail(first, module.strings)
                if first is not None and _ends_in_dollar(pattern):
                    found.add((relative, f"re.{kind}:{ast.unparse(first)}"))
                continue
            if not (isinstance(node.func, ast.Attribute) and node.func.attr in ("match", "search")):
                continue
            owner = node.func.value
            if isinstance(owner, ast.Name) and owner.id in anchored:
                found.add((relative, owner.id))
                continue
            chained = _compile_tail(owner, module.strings, module.re_names, module.re_funcs)
            if _ends_in_dollar(chained):
                found.add((relative, f"re.compile.{node.func.attr}:{ast.unparse(owner)}"))
    return found


def test_no_whole_value_validator_accepts_a_trailing_newline() -> None:
    unexplained = sorted(scan(ROOT) - set(LEFT))
    assert not unexplained, (
        f"`$` with match()/search() accepts a trailing newline: {unexplained}. Use "
        "fullmatch() (or end the pattern in \\Z); if the site is a prefix or line match, "
        "add it to LEFT with the reason"
    )


def test_every_site_left_is_still_there() -> None:
    """A stale entry would silently cover a new site of the same key."""
    assert not sorted(set(LEFT) - scan(ROOT))


# --- planted forms: each must be found ---------------------------------------------

#: form -> (file, planted text, key found, the same text fixed).
_PLANTED: dict[str, tuple[str, str, str, str | None]] = {
    "assign": (
        "src/gpo_studio/p_assign.py",
        'import re\nA = re.compile(r"^a$")\nA.match("a")\n',
        "A",
        None,
    ),
    "annotated": (
        "src/gpo_studio/p_ann.py",
        'import re\nA: re.Pattern[str] = re.compile(r"^a$")\nA.match("a")\n',
        "A",
        None,
    ),
    "concatenated": (
        "src/gpo_studio/p_concat.py",
        'import re\nA = re.compile(r"^a" + r"b$")\nA.search("ab")\n',
        "A",
        None,
    ),
    "fstring": (
        "src/gpo_studio/p_fstr.py",
        'import re\nX = "a"\nA = re.compile(rf"^{X}$")\nA.match("a")\n',
        "A",
        None,
    ),
    "constant-name": (
        "src/gpo_studio/p_const.py",
        'import re\nP = r"^a$"\nre.search(P, "a")\n',
        "re.search:P",
        'import re\nP = r"^a$"\nre.fullmatch(P, "a")\n',
    ),
    "constant-built": (
        "src/gpo_studio/p_built.py",
        'import re\nP = r"^a" + "$"\nre.match(P, "a")\n',
        "re.match:P",
        'import re\nP = r"^a" + "$"\nre.fullmatch(P, "a")\n',
    ),
    "chained": (
        "src/gpo_studio/p_chain.py",
        'import re\nre.compile(r"^a$").match("a")\n',
        "re.compile.match:re.compile('^a$')",
        None,
    ),
    "inline": (
        "src/gpo_studio/p_inline.py",
        'import re\nre.match(r"^a$", "a")\n',
        "re.match:'^a$'",
        'import re\nre.fullmatch(r"^a$", "a")\n',
    ),
    "pattern-keyword": (
        "src/gpo_studio/p_kw.py",
        'import re\nA = re.compile(pattern=r"^a$")\nA.match("a")\n',
        "A",
        None,
    ),
    "inline-keyword": (
        "src/gpo_studio/p_ikw.py",
        'import re\nre.search(pattern=r"^a$", string="a")\n',
        "re.search:'^a$'",
        'import re\nre.fullmatch(pattern=r"^a$", string="a")\n',
    ),
    "re-aliased": (
        "src/gpo_studio/p_rx.py",
        'import re as rx\nA = rx.compile(r"^a$")\nA.match("a")\nrx.search(r"^b$", "b")\n',
        "A",
        'import re as rx\nA = rx.compile(r"^a$")\nA.fullmatch("a")\nrx.fullmatch(r"^b$", "b")\n',
    ),
    "re-aliased-inline": (
        "src/gpo_studio/p_rx2.py",
        'import re as rx\nrx.search(r"^b$", "b")\n',
        "re.search:'^b$'",
        'import re as rx\nrx.fullmatch(r"^b$", "b")\n',
    ),
    "from-re-compile": (
        "src/gpo_studio/p_frc.py",
        'from re import compile as C\nA = C(r"^a$")\nA.match("a")\n',
        "A",
        None,
    ),
    "from-re-match": (
        "src/gpo_studio/p_frm.py",
        'from re import match as M\nM(r"^a$", "a")\n',
        "re.match:'^a$'",
        'from re import fullmatch as M\nM(r"^a$", "a")\n',
    ),
    "from-re-search": (
        "src/gpo_studio/p_frs.py",
        'from re import search\nsearch(r"^a$", "a")\n',
        "re.search:'^a$'",
        'from re import fullmatch\nfullmatch(r"^a$", "a")\n',
    ),
    "walrus": (
        "src/gpo_studio/p_walrus.py",
        'import re\nif (A := re.compile(r"^a$")):\n    A.match("a")\n',
        "A",
        None,
    ),
    "compiled-passed-to-re": (
        "src/gpo_studio/p_cpr.py",
        'import re\nre.match(re.compile(r"^a$"), "a")\n',
        "re.match:re.compile('^a$')",
        'import re\nre.fullmatch(re.compile(r"^a$"), "a")\n',
    ),
}


def _plant(tmp_path: Path, files: dict[str, str]) -> set[tuple[str, str]]:
    for relative, text in files.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return scan(tmp_path, ("src/gpo_studio/*.py",))


@pytest.mark.parametrize("form", sorted(_PLANTED))
def test_every_form_of_the_site_is_found(tmp_path: Path, form: str) -> None:
    relative, text, key, fixed = _PLANTED[form]
    found = _plant(tmp_path, {relative: text})
    assert (relative, key) in found, found
    if fixed is None:
        fixed = text.replace(".match(", ".fullmatch(").replace(".search(", ".fullmatch(")
    assert not _plant(tmp_path / "fixed", {relative: fixed})


@pytest.mark.parametrize(
    "importer",
    [
        "from .p_defs import DEF_RE\nDEF_RE.match('a')\n",
        "from gpo_studio.p_defs import DEF_RE as R\nR.search('a')\n",
        "import re\nfrom .p_defs import DEF_RE\nre.match(DEF_RE, 'a')\n",
    ],
    ids=["relative", "absolute-aliased", "passed-to-re"],
)
def test_an_imported_pattern_is_followed(tmp_path: Path, importer: str) -> None:
    found = _plant(tmp_path, {
        "src/gpo_studio/p_defs.py": 'import re\nDEF_RE = re.compile(r"^a$")\n',
        "src/gpo_studio/p_uses.py": importer,
    })
    assert any(path == "src/gpo_studio/p_uses.py" for path, _ in found), found


def test_a_second_inline_site_in_the_same_file_is_a_new_key(tmp_path: Path) -> None:
    one = _plant(tmp_path / "a", {"src/gpo_studio/p.py": 'import re\nre.match(r"^a$", "a")\n'})
    two = _plant(tmp_path / "b", {
        "src/gpo_studio/p.py": 'import re\nre.match(r"^a$", "a")\nre.match(r"^b$", "b")\n'
    })
    assert len(two) == len(one) + 1


def test_the_reason_this_exists() -> None:
    import re

    assert re.compile(r"^v\d+$").match("v1\n") is not None
    assert re.compile(r"^v\d+$").fullmatch("v1\n") is None
