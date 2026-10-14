"""The release-only closure gate (Plan 036 WP-0, WI-083).

Every live lane verdict binds the files its finalizer's hand-written tables
name, and `test_a_live_verdict_still_binds_the_harness_that_ships` proves only
those are unchanged; nine live verdicts import changed code those tables do
not name. This gate closes that for the release: from the freeze commit `F`
to the 1.1.0 final tag it re-derives every live lane's static import closure,
re-runs the fail-closed scan of every resolved call target, re-enumerates
every declared directory, and fails on any added or removed edge, on any
changed byte in a pinned file, on a changed toolchain identity, and on a pin
that was not derived from the committed successor record
(`plans/036-change-cost.md`, "The release-only closure gate").

**Before the pin exists** the gate skips, and the skip names WI-083. The pin
is written at the freeze (the plan's step 7), so on `main` today it is absent.
The skip is refused, and the gate fails, once the package version (read by the
release gate's own strict reader) is a final release: a final release cannot
ship without it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from plan036_support import REPO_ROOT, load

record = load("record")


def test_the_release_only_closure_gate_holds() -> None:
    decision, reason = record.gate_decision(REPO_ROOT)
    if decision == "skip":
        pytest.skip(reason)
    assert decision == "run", reason
    pin = json.loads((REPO_ROOT / record.PIN_RECORD).read_text(encoding="utf-8"))
    problems = record.verify_pin(REPO_ROOT, pin)
    assert not problems, "the release-only closure gate is red:\n" + "\n".join(problems)


def _tree(tmp_path: Path, version: str, *, pin: bool = False) -> Path:
    package = tmp_path / "src" / "gpo_studio"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(
        f'"""Fixture."""\n\n__version__ = "{version}"\n', encoding="utf-8"
    )
    if pin:
        target = tmp_path / record.PIN_RECORD
        target.parent.mkdir(parents=True)
        target.write_text("{}\n", encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize("version", ["1.1.0rc1", "1.1.0rc2", "1.2.0.dev0", "1.1.0b1"])
def test_a_pre_release_without_a_pin_skips_naming_wi083(tmp_path: Path, version: str) -> None:
    decision, reason = record.gate_decision(_tree(tmp_path, version))
    assert decision == "skip"
    assert "WI-083" in reason and version in reason


@pytest.mark.parametrize("version", ["1.1.0", "2.0.0", "1.1.0+local", "1.1.0.post1"])
def test_a_final_version_without_a_pin_fails_instead_of_skipping(
    tmp_path: Path, version: str
) -> None:
    """The skip is refused once the version is final (or of a form not known to be a
    pre-release: strict, so an unusual version string cannot buy a skip)."""
    decision, reason = record.gate_decision(_tree(tmp_path, version))
    assert decision == "fail"
    assert "WI-083" in reason and record.PIN_RECORD in reason


def test_a_present_pin_runs_the_gate_whatever_the_version(tmp_path: Path) -> None:
    assert record.gate_decision(_tree(tmp_path, "1.1.0rc1", pin=True)) == ("run", "")
    assert record.gate_decision(_tree(tmp_path / "f", "1.1.0", pin=True)) == ("run", "")


def test_the_version_is_read_by_the_release_gates_strict_reader(tmp_path: Path) -> None:
    """Two bindings of __version__ are refused by `package_version`, so they are refused
    here too, rather than this gate reading a different version than the release gate."""
    root = _tree(tmp_path, "1.1.0rc1")
    init = root / "src" / "gpo_studio" / "__init__.py"
    init.write_text(init.read_text(encoding="utf-8") + '__version__ = "1.1.0"\n', "utf-8")
    with pytest.raises(Exception, match="__version__|version"):
        record.gate_decision(root)
