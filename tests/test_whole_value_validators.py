"""A pattern ending in `$` and called with match() or search() accepts a trailing newline.

`$` also matches just before one final "\\n", so `re.match(r"^x$", "x\\n")`
succeeds. Every final-r4/r5 review finding of this class was a whole-value
validator written that way (the release-identity writer, the release gate,
the workflow grammar's action inputs). They now use `fullmatch` (or `\\Z`).

This test finds, across `src/`, `scripts/` and the workflow grammar, every
compiled pattern ending in `$` that is called with `match` or `search`, and
every inline `re.match`/`re.search` with such a pattern, and fails on any site
not in :data:`LEFT` -- each of which says why it stays.
"""

from __future__ import annotations

import ast
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
#: Left on purpose: (file, pattern name or "re.<call>") -> why.
_BOUND = (
    "bound by live lane verdicts: an edit expires them and turns the freshness "
    "test red until the estate re-runs the lanes (the batch at the freeze commit "
    "requalifies every lane, so a freeze PR may change it with the batch)"
)
LEFT: dict[tuple[str, str], str] = {
    ("scripts/check_release_manifest.py", "_SETEXT_UNDERLINE"): (
        "line-oriented: applied to elements of text.split('\\n'), which hold no newline"
    ),
    ("tests/workflow_grammar.py", "CONFIG_PATTERNS"): (
        "over-matching is the safe direction: a name it matches must be a reviewed "
        "config, so a trailing newline makes a name more suspect, never less"
    ),
    **{
        (path, name): _BOUND
        for path, names in {
            "scripts/windows-oracle/finalize_lifecycle_run.py": (
                "_BARE_GUID", "_PERMISSION", "_WQL", "_SID", "_BRACED_GUID", "_STAMP",
                "_GROUP_NAME", "_RUN_ID",
            ),
            "src/gpo_studio/backup.py": ("_GUID_RE",),
            "src/gpo_studio/fdeploy.py": (
                "_REDIRECTION_SECTION", "_FLAGS_VALUE", "_SECTION_HEADER",
            ),
            "src/gpo_studio/gpp_adapters.py": ("_BARE_TIME_RE",),
            "src/gpo_studio/lifecycle.py": ("_SOM_FILTER_PATH",),
            "src/gpo_studio/object_security.py": ("_OBJECT_ROW_RE",),
            "src/gpo_studio/oracle_evidence.py": ("_RUN_ID", "_BUILD_FAMILY"),
            "src/gpo_studio/publication.py": ("re.match",),
            "src/gpo_studio/validation.py": (
                "_PRINCIPAL_DOMAIN_USER", "_PRINCIPAL_UPN", "_PRINCIPAL_SID", "_DOMAIN_FORMAT",
            ),
        }.items()
        for name in names
    },
}


def _ends_in_dollar(pattern: str) -> bool:
    return pattern.endswith("$") and not pattern.endswith("\\$")


def _pattern(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr) and node.values:
        last = node.values[-1]
        if isinstance(last, ast.Constant) and isinstance(last.value, str):
            return last.value
    return None


def _sites() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for glob in SCOPE:
        for path in sorted(ROOT.glob(glob)):
            relative = path.relative_to(ROOT).as_posix()
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
            anchored: set[str] = set()
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Assign)
                    and isinstance(node.value, ast.Call)
                    and isinstance(node.value.func, ast.Attribute)
                    and node.value.func.attr == "compile"
                    and node.value.args
                ):
                    pattern = _pattern(node.value.args[0])
                    if pattern is not None and _ends_in_dollar(pattern):
                        anchored |= {t.id for t in node.targets if isinstance(t, ast.Name)}
            for node in ast.walk(tree):
                if not (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in ("match", "search")
                    and isinstance(node.func.value, ast.Name)
                ):
                    continue
                owner = node.func.value.id
                if owner in anchored:
                    found.add((relative, owner))
                elif owner == "re" and node.args:
                    pattern = _pattern(node.args[0])
                    if pattern is not None and _ends_in_dollar(pattern):
                        found.add((relative, f"re.{node.func.attr}"))
    return found


def test_no_whole_value_validator_accepts_a_trailing_newline() -> None:
    unexplained = sorted(_sites() - set(LEFT))
    assert not unexplained, (
        f"`$` with match()/search() accepts a trailing newline: {unexplained}. Use "
        "fullmatch() (or end the pattern in \\Z); if the site is a prefix or line match, "
        "add it to LEFT with the reason"
    )


def test_every_site_left_is_still_there() -> None:
    """A stale entry would silently cover a new site of the same name."""
    assert not sorted(set(LEFT) - _sites())


@pytest.mark.parametrize(
    "pattern",
    [r"^v\d+$", r"^[0-9a-f]{40}$"],
)
def test_the_reason_this_exists(pattern: str) -> None:
    import re

    compiled = re.compile(pattern)
    value = "v1" if pattern.startswith("^v") else "a" * 40
    assert compiled.match(value + "\n") is not None
    assert compiled.fullmatch(value + "\n") is None
