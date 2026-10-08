"""`gpmc_interop.py` is one type, and its shape is pinned here.

Reduced on 2026-10-07 by operator ruling
(`docs/direction-2026-10-07-plan-034-completion.md`). The interop checks and
their tests are gone: the importable predicate equated *Studio cannot emit
this* with *GPMC cannot import this*, and `is_gpmc_editable` had no oracle.

What is left is `InteropIssue`, which `publication.py` constructs. The
publication lane's verdict binds `publication.py` but not this file, so an edit
here expires nothing, and nothing in the lane would notice one either. These
tests are what notice: a renamed field, a changed default or a widened level
set changes what the planner emits, and has to be a deliberate change.
"""

from __future__ import annotations

import ast
import dataclasses
import typing
from pathlib import Path

import pytest

from gpo_studio import gpmc_interop, publication
from gpo_studio.gpmc_interop import InteropCheckLevel, InteropIssue


def test_interop_issue_has_exactly_its_four_fields_in_order() -> None:
    fields = dataclasses.fields(InteropIssue)
    assert [(f.name, f.type) for f in fields] == [
        ("check", "str"),
        ("level", "InteropCheckLevel"),
        ("message", "str"),
        ("component", "str"),
    ]
    defaults = {
        f.name: f.default
        for f in fields
        if f.default is not dataclasses.MISSING
    }
    assert defaults == {"component": ""}


def test_interop_issue_is_frozen_and_slotted() -> None:
    issue = InteropIssue(check="test", level="pass", message="ok")
    assert issue.component == ""
    assert not hasattr(issue, "__dict__")
    with pytest.raises(dataclasses.FrozenInstanceError):
        issue.message = "changed"  # type: ignore[misc]


def test_the_level_vocabulary_is_closed_at_three() -> None:
    assert typing.get_args(InteropCheckLevel) == ("pass", "warning", "error")


def test_publication_uses_this_type_and_not_a_copy() -> None:
    """The reason the type survived: the bound planner imports it from here."""
    assert publication.InteropIssue is InteropIssue


def test_the_module_holds_nothing_else() -> None:
    """The checks were deleted by ruling; this keeps them from growing back.

    Re-adding an interop predicate means re-opening the 2026-10-07 ruling,
    which says what an honest one would need: an oracle, not Studio's own
    emission vocabulary standing in for GPMC's import behaviour.
    """
    tree = ast.parse(Path(gpmc_interop.__file__).read_text(encoding="utf-8"))
    defined: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.append(node.name)
        elif isinstance(node, ast.Assign):
            defined.extend(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            defined.append(node.target.id)
        elif isinstance(node, ast.TypeAlias):
            defined.append(node.name.id)
    assert sorted(defined) == ["InteropCheckLevel", "InteropIssue"]
