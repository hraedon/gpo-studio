"""Policy-family coverage in the WP-3 security-template candidate."""

from __future__ import annotations

import json
import runpy
import sys
from pathlib import Path
from typing import Any

import pytest


def _build_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    include_kerberos: bool,
) -> dict[tuple[str, str], str]:
    script = (
        Path(__file__).parents[1]
        / "scripts"
        / "plan-033"
        / "build-wp3-candidate.py"
    )
    output_dir = tmp_path / ("dc-candidate" if include_kerberos else "member-candidate")
    namespace: dict[str, Any] = runpy.run_path(str(script))
    argv = [str(script), str(output_dir)]
    if include_kerberos:
        argv.append("--include-kerberos")
    monkeypatch.setattr(sys, "argv", argv)
    assert namespace["main"]() == 0

    expected = json.loads(
        (output_dir / "expected.json").read_text(encoding="utf-8")
    )
    return {
        (setting["section"], setting["key"]): setting["value"]
        for setting in expected["settings"]
    }


def test_member_candidate_covers_policy_families_without_dc_only_kerberos(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _build_candidate(
        tmp_path, monkeypatch, include_kerberos=False
    )

    assert not any(section == "Kerberos Policy" for section, _ in settings)
    assert settings[("System Access", "MinimumPasswordAge")] == "1"
    assert settings[("System Access", "MinimumPasswordLength")] == "14"
    assert settings[("System Access", "ClearTextPassword")] == "0"
    assert settings[("System Access", "LockoutBadCount")] == "5"
    assert settings[("Event Audit", "AuditSystemEvents")] == "3"
    assert settings[("Event Audit", "AuditLogonEvents")] == "1"
    assert settings[("Event Audit", "AuditObjectAccess")] == "0"
    assert settings[("Event Audit", "AuditPolicyChange")] == "3"
    assert settings[("Event Audit", "AuditDSAccess")] == "0"
    assert ("Event Audit", "AuditDirectoryServiceAccess") not in settings
    assert settings[("Privilege Rights", "SeBackupPrivilege")] == (
        "*S-1-5-32-544,*S-1-5-32-551"
    )
    assert settings[("Registry Values", "MACHINE\\Software\\StudioLab\\Sz")] == (
        '1,"studio sz value"'
    )
    assert settings[
        ("Registry Values", "MACHINE\\Software\\StudioLab\\ExpandSz")
    ] == '2,"%SystemRoot%\\studio"'
    assert settings[
        ("Group Membership", "Power Users__Members")
    ] == "Administrator"


def test_dc_candidate_adds_all_kerberos_family_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _build_candidate(tmp_path, monkeypatch, include_kerberos=True)

    kerberos = {
        key: value
        for (section, key), value in settings.items()
        if section == "Kerberos Policy"
    }
    assert kerberos == {
        "MaxTicketAge": "10",
        "MaxRenewAge": "7",
        "MaxServiceAge": "600",
        "MaxClockSkew": "5",
        "TicketValidateClient": "1",
        "EnforceLogonRestrictions": "1",
        "EnforceUserLogonRestrictions": "0",
    }
