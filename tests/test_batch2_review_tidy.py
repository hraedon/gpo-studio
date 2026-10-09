"""Deferred review items folded into batch 2 (N6, N7).

N5 (a comment) and N8 (`run-requal-batch.sh`, tested in
`test_requal_batch_driver.py`) have no behaviour here.
"""

from __future__ import annotations

import runpy
from pathlib import Path
from typing import Any, cast

import pytest

from gpo_studio.export import gpmc_backup_bundle, native_backup_refusal
from gpo_studio.fdeploy import encode_fdeploy, read_fdeploy
from gpo_studio.gpp import GppCollection, GppGroup
from gpo_studio.model import GPO, ValidationError
from gpo_studio.object_security import _principal_wire

ROOT = Path(__file__).resolve().parents[1]

#: "S-1-5-32-544" with the last group in ARABIC-INDIC digits: `\d` without
#: re.ASCII matches them, Windows has no such principal.
LOOK_ALIKE = "S-1-5-32-٥٤٤"


def _fdeploy() -> Any:
    return read_fdeploy(
        encode_fdeploy(
            "[version]\r\nversion=100\r\n[Folder_Redirection]\r\n"
            "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}=s-1-1-0;\r\n"
            "[{FDD39AD0-238F-46AF-ADB4-6C85480369C7}_s-1-1-0]\r\n"
            "FullPath=\\\\synthetic\\share\r\nFlags=1021\r\n"
        )
    )


# ---------------------------------------------------------------------------
# N6: every refusal is reported, the most relevant first
# ---------------------------------------------------------------------------


def test_cpassword_outranks_the_folder_redirection_refusal() -> None:
    group = GppGroup(name="G", unknown_attrs=(("cpassword", "x"),))
    gpo = GPO(
        guid="9b1de5c0-0000-4000-8000-0000000000e1",
        name="N6",
        domain="synthetic.test",
        fdeploy=_fdeploy(),
        gpp_collections=(GppCollection(scope="computer", groups=(group,)),),
    )
    refusal = native_backup_refusal(gpo)
    assert refusal is not None and refusal.code == "cpassword_detected"
    with pytest.raises(ValidationError) as caught:
        gpmc_backup_bundle(gpo)
    codes = [issue.code for issue in caught.value.issues]
    assert codes == ["cpassword_detected", "folder_redirection_not_exportable"]


def test_fdeploy_alone_is_still_refused() -> None:
    gpo = GPO(
        guid="9b1de5c0-0000-4000-8000-0000000000e2",
        name="N6",
        domain="synthetic.test",
        fdeploy=_fdeploy(),
    )
    refusal = native_backup_refusal(gpo)
    assert refusal is not None and refusal.code == "folder_redirection_not_exportable"


# ---------------------------------------------------------------------------
# N7: SID patterns are ASCII-only
# ---------------------------------------------------------------------------


def test_look_alike_is_a_unicode_digit_trap() -> None:
    """The control: without re.ASCII the pattern really would accept it."""
    import re

    assert re.fullmatch(r"S-1-\d+(?:-\d+)+", LOOK_ALIKE) is not None


def test_object_security_does_not_star_a_look_alike_sid() -> None:
    assert _principal_wire("S-1-5-32-544") == "*S-1-5-32-544"
    assert _principal_wire(LOOK_ALIKE) == LOOK_ALIKE


def _finalizer() -> dict[str, object]:
    return runpy.run_path(
        str(ROOT / "scripts" / "windows-oracle" / "finalize_object_security_run.py")
    )


@pytest.mark.parametrize(
    "name",
    ["_SID_RE", "_NATIVE_GROUP_MEMBER", "_EXPORTED_GROUP_MEMBER"],
)
def test_object_security_finalizer_patterns_reject_a_look_alike(name: str) -> None:
    pattern = cast(Any, _finalizer()[name])
    star = "" if name == "_SID_RE" else "*"
    assert pattern.fullmatch(f"{star}S-1-5-32-544") is not None
    assert pattern.fullmatch(f"{star}{LOOK_ALIKE}") is None


def test_object_security_finalizer_group_keys_reject_a_look_alike() -> None:
    symbols = _finalizer()
    native = cast(Any, symbols["_NATIVE_GROUP_KEY"])
    exported = cast(Any, symbols["_EXPORTED_GROUP_KEY"])
    assert native.fullmatch("*S-1-5-32-544__Members") is not None
    assert native.fullmatch(f"*{LOOK_ALIKE}__Members") is None
    assert exported.fullmatch(f"*{LOOK_ALIKE}__members") is None


def test_object_security_finalizer_refuses_a_look_alike_expectation() -> None:
    expected_group_membership = cast(Any, _finalizer()["_expected_group_membership"])
    raw = {
        "schema_version": 2,
        "settings": [],
        "group_membership": [
            {"group_sid": LOOK_ALIKE, "relation": "Members", "member_sids": []},
        ],
    }
    with pytest.raises(ValueError, match="SID"):
        expected_group_membership(raw)
