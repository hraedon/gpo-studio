"""Regression tests for the Plan 033 WP-3 verdict logic."""

from __future__ import annotations

import runpy
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from gpo_studio.security_template import InfSection, SecurityTemplate

_FINALIZER = runpy.run_path(
    str(
        Path(__file__).parents[1]
        / "scripts"
        / "windows-oracle"
        / "finalize_wp3_run.py"
    )
)
_candidate_key_set_matches = cast(
    Callable[[SecurityTemplate, list[dict[str, Any]]], bool],
    _FINALIZER["_candidate_key_set_matches"],
)
_observed_operations_match = cast(
    Callable[[dict[str, Any]], bool],
    _FINALIZER["_observed_operations_match"],
)


def test_candidate_key_set_rejects_unexpected_security_setting() -> None:
    expected = [
        {
            "section": "Privilege Rights",
            "key": "SeBackupPrivilege",
            "value": "*S-1-5-32-544",
        }
    ]
    exact = SecurityTemplate(
        sections=(
            InfSection(
                name="Privilege Rights",
                entries=(("SeBackupPrivilege", "*S-1-5-32-544"),),
            ),
        )
    )
    extra = SecurityTemplate(
        sections=(
            InfSection(
                name="Privilege Rights",
                entries=(
                    ("SeBackupPrivilege", "*S-1-5-32-544"),
                    ("SeDebugPrivilege", "*S-1-5-32-544"),
                ),
            ),
        )
    )
    assert _candidate_key_set_matches(exact, expected)
    assert not _candidate_key_set_matches(extra, expected)


def test_candidate_key_set_rejects_unexpected_authored_section() -> None:
    expected = [
        {
            "section": "System Access",
            "key": "MinimumPasswordLength",
            "value": "14",
        }
    ]
    template = SecurityTemplate(
        sections=(
            InfSection(
                name="System Access",
                entries=(("MinimumPasswordLength", "14"),),
            ),
            InfSection(
                name="Registry Values",
                entries=((r"MACHINE\Software\Synthetic", "4,1"),),
            ),
        )
    )
    assert not _candidate_key_set_matches(template, expected)


def test_observed_operations_require_exact_non_applying_sequence() -> None:
    result = {
        "invoked_operations": [
            {"name": "validate", "arguments": ["/validate", "candidate.inf"]},
            {"name": "import", "arguments": ["/import", "/db", "temporary.sdb"]},
            {"name": "export", "arguments": ["/export", "/db", "temporary.sdb"]},
        ]
    }
    assert _observed_operations_match(result)

    result["invoked_operations"].append(
        {"name": "configure", "arguments": ["/configure", "/db", "temporary.sdb"]}
    )
    assert not _observed_operations_match(result)


def test_observed_operations_reject_name_argument_mismatch() -> None:
    result = {
        "invoked_operations": [
            {"name": "validate", "arguments": ["/validate", "candidate.inf"]},
            {"name": "import", "arguments": ["/configure", "/db", "temporary.sdb"]},
            {"name": "export", "arguments": ["/export", "/db", "temporary.sdb"]},
        ]
    }
    assert not _observed_operations_match(result)
