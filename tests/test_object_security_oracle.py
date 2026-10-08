"""Regression tests for the Plan 034 object-security evidence lane."""

from __future__ import annotations

import json
import runpy
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from gpo_studio.security_template import encode_security_template

_SCRIPT = (
    Path(__file__).parents[1] / "scripts" / "windows-oracle" / "finalize_object_security_run.py"
)
_FINALIZER = runpy.run_path(str(_SCRIPT))
_template_rows = cast(
    Callable[[Path], dict[tuple[str, str], tuple[int, str]]],
    _FINALIZER["_template_rows"],
)
_expected_rows = cast(
    Callable[[object], dict[tuple[str, str], tuple[int, str]]],
    _FINALIZER["_expected_rows"],
)
_operations_match = cast(Callable[[Mapping[str, Any]], bool], _FINALIZER["_operations_match"])
_member_server_environment = cast(
    Callable[[object], bool], _FINALIZER["_member_server_environment"]
)
_template_group_membership = cast(
    Callable[..., dict[tuple[str, str], tuple[str, ...]]],
    _FINALIZER["_template_group_membership"],
)
_expected_group_membership = cast(
    Callable[[object], dict[tuple[str, str], tuple[str, ...]]],
    _FINALIZER["_expected_group_membership"],
)

_SDDL = "D:PAR(A;OICI;FA;;;BA)"


def _write_template(path: Path, body: str) -> None:
    path.write_bytes(
        encode_security_template(
            '[Unicode]\nUnicode=yes\n\n[Version]\nsignature="$CHICAGO$"\nRevision=1\n\n' + body
        )
    )


def test_candidate_native_rows_are_parsed_with_exact_codes(tmp_path: Path) -> None:
    path = tmp_path / "candidate.inf"
    _write_template(
        path,
        '[Registry Keys]\n"MACHINE\\SOFTWARE\\StudioLab\\A",0,"'
        + _SDDL
        + '"\n\n[File Security]\n"C:\\StudioLab\\A",1,"'
        + _SDDL
        + '"\n\n[Service General Setting]\n"StudioLabSvc",2,"'
        + _SDDL
        + '"',
    )

    rows = _FINALIZER["_template_rows"](path, exported=False)
    assert rows == {
        ("registry keys", r"machine\software\studiolab\a"): (0, _SDDL),
        ("file security", r"c:\studiolab\a"): (1, _SDDL),
        ("service general setting", "studiolabsvc"): (2, _SDDL),
    }


def test_windows_ordinal_export_is_parsed_and_paths_are_case_folded(
    tmp_path: Path,
) -> None:
    path = tmp_path / "exported.inf"
    _write_template(
        path,
        '[Registry Keys]\n1="machine\\software\\studiolab\\a", 0, "'
        + _SDDL
        + '"\n\n[File Security]\n1="c:\\studiolab\\a", 1, "'
        + _SDDL
        + '"\n\n[Service General Setting]\n1="studiolabsvc", 2, "'
        + _SDDL
        + '"',
    )

    rows = _FINALIZER["_template_rows"](path, exported=True)
    assert rows[("registry keys", r"machine\software\studiolab\a")] == (0, _SDDL)
    assert rows[("file security", r"c:\studiolab\a")] == (1, _SDDL)
    assert rows[("service general setting", "studiolabsvc")] == (2, _SDDL)


@pytest.mark.parametrize(
    "body",
    [
        '[Registry Keys]\n1="a",0,"D:"\n1="b",0,"D:"',
        '[Registry Keys]\nnot-an-ordinal="a",0,"D:"',
    ],
)
def test_export_rejects_duplicate_or_non_numeric_ordinals(tmp_path: Path, body: str) -> None:
    path = tmp_path / "exported.inf"
    _write_template(
        path,
        body
        + '\n\n[File Security]\n1="c:\\a",0,"D:"'
        + '\n\n[Service General Setting]\n1="svc",2,"D:"',
    )
    with pytest.raises(ValueError, match="ordinal"):
        _FINALIZER["_template_rows"](path, exported=True)


def test_expected_schema_rejects_duplicate_targets() -> None:
    setting = {
        "section": "Registry Keys",
        "target": r"MACHINE\SOFTWARE\StudioLab\A",
        "code": 0,
        "sddl": _SDDL,
    }
    with pytest.raises(ValueError, match="repeat"):
        _expected_rows({"schema_version": 1, "settings": [setting, setting]})


@pytest.mark.parametrize(
    "raw",
    [
        {"schema_version": True, "settings": []},
        {"schema_version": 1, "settings": [], "extra": "field"},
    ],
)
def test_expected_schema_requires_integer_version_and_exact_top_keys(raw: object) -> None:
    with pytest.raises(ValueError, match="schema_version"):
        _expected_rows(raw)


def _operation(name: str, *arguments: str) -> dict[str, object]:
    return {"name": name, "arguments": [f"/{name}", *arguments]}


def test_operations_require_exact_non_applying_areas() -> None:
    database = r"C:\runs\object-security-1\temporary-security-database.sdb"
    areas = ("/areas", "regkeys", "filestore", "services", "group_mgmt")
    result = {
        "invoked_operations": [
            _operation("validate", r"C:\runs\object-security-1\candidate.inf"),
            _operation(
                "import",
                "/db",
                database,
                "/cfg",
                r"C:\runs\object-security-1\candidate.inf",
                "/overwrite",
                *areas,
                "/log",
                r"C:\runs\import.log",
                "/quiet",
            ),
            _operation(
                "export",
                "/db",
                database,
                "/cfg",
                r"C:\runs\object-security-1\exported.inf",
                *areas,
                "/log",
                r"C:\runs\export.log",
                "/quiet",
            ),
        ]
    }
    assert _operations_match(result)

    cast(list[dict[str, object]], result["invoked_operations"])[1] = _operation(
        "import", "/areas", "regkeys", "filestore", "/configure"
    )
    assert not _operations_match(result)


def test_operation_validation_rejects_truncated_validate_argv() -> None:
    assert not _operations_match({"invoked_operations": [_operation("validate"), {}, {}]})


@pytest.mark.parametrize("role", [None, True, 2, 4, "3"])
def test_lane_rejects_non_member_server_roles(role: object) -> None:
    assert not _member_server_environment({"computer_system_domain_role": role})
    assert _member_server_environment({"computer_system_domain_role": 3})


def test_lane_binds_every_executed_and_semantic_source_file() -> None:
    assert set(_FINALIZER["DEPLOYED_FILES"]) == {"run-object-security-template.ps1"}
    assert set(_FINALIZER["LOCAL_FILES"]) == {
        "run-object-security-oracle.sh",
        "build-object-security-candidate.py",
        "finalize_object_security_run.py",
        "psdirect.ps1",
        "object_security.py",
        "security_template.py",
        "sddl.py",
        "oracle_evidence.py",
    }


def test_guest_harness_never_configures_security_policy() -> None:
    harness = (
        Path(__file__).parents[1]
        / "scripts"
        / "windows-oracle"
        / "run-object-security-template.ps1"
    ).read_text(encoding="ascii")
    assert "/configure" not in harness.casefold()
    assert "'regkeys', 'filestore', 'services', 'group_mgmt'" in harness
    assert "-ErrorAction Stop" in harness
    assert "<enumeration-failed>" in harness
    assert "cleanup enumeration:" in harness


def _evidence_pack(tmp_path: Path) -> tuple[Path, Path]:
    root = Path(__file__).parents[1]
    candidate = tmp_path / "candidate"
    builder = runpy.run_path(
        str(root / "scripts" / "plan-033" / "build-object-security-candidate.py")
    )
    previous = sys.argv
    try:
        sys.argv = ["build-object-security-candidate.py", str(candidate)]
        assert builder["main"]() == 0
    finally:
        sys.argv = previous

    run_dir = tmp_path / "run"
    (run_dir / "deployed").mkdir(parents=True)
    (run_dir / "commands").mkdir()
    for name in (
        "validate.stdout.txt",
        "validate.stderr.txt",
        "import.stdout.txt",
        "import.stderr.txt",
        "export.stdout.txt",
        "export.stderr.txt",
        "import.log",
        "export.log",
    ):
        (run_dir / "commands" / name).write_text("", encoding="utf-8")
    expected = json.loads((candidate / "expected.json").read_text(encoding="utf-8"))
    by_section: dict[str, list[dict[str, object]]] = {}
    for setting in expected["settings"]:
        by_section.setdefault(setting["section"], []).append(setting)
    lines = [
        "[Unicode]",
        "Unicode=yes",
        "",
        "[Version]",
        'signature="$CHICAGO$"',
        "Revision=1",
    ]
    for section, settings in by_section.items():
        lines.extend(("", f"[{section}]"))
        for ordinal, setting in enumerate(settings, start=1):
            lines.append(f'{ordinal}="{setting["target"]}", {setting["code"]}, "{setting["sddl"]}"')
    # Windows' shape for these rows (WP-3 export): `key = value`, starred on
    # both sides. Members are written in reverse here because the export is
    # free to reorder a principal list, and the comparison must not care.
    lines.extend(("", "[Group Membership]"))
    for group in expected["group_membership"]:
        suffix = "Members" if group["relation"] == "members" else "Memberof"
        members = ",".join(f"*{sid}" for sid in reversed(group["member_sids"]))
        lines.append(f"*{group['group_sid']}__{suffix} = {members}")
    (run_dir / "exported.inf").write_bytes(encode_security_template("\n".join(lines) + "\n"))
    shutil.copy2(candidate / "candidate.inf", run_dir / "candidate.inf")
    shutil.copy2(candidate / "expected.json", run_dir / "expected.json")
    for name, relative in _FINALIZER["DEPLOYED_FILES"].items():
        shutil.copy2(root / relative, run_dir / "deployed" / name)
    for name, relative in _FINALIZER["LOCAL_FILES"].items():
        shutil.copy2(root / relative, run_dir / name)
    run_root = r"C:\runs\object-security-20260907000000-1000"
    database = run_root + r"\temporary-security-database.sdb"
    result = {
        "run_id": "object-security-20260907000000-1000",
        "validate_exit_code": 0,
        "import_exit_code": 0,
        "export_exit_code": 0,
        "export_created": True,
        "cleanup_succeeded": True,
        "database_absent_after_cleanup": True,
        "database_residual_files": [],
        "invoked_operations": [
            _operation("validate", run_root + r"\candidate.inf"),
            _operation(
                "import",
                "/db",
                database,
                "/cfg",
                run_root + r"\candidate.inf",
                "/overwrite",
                "/areas",
                "regkeys",
                "filestore",
                "services",
                "group_mgmt",
                "/log",
                r"C:\runs\import.log",
                "/quiet",
            ),
            _operation(
                "export",
                "/db",
                database,
                "/cfg",
                run_root + r"\exported.inf",
                "/areas",
                "regkeys",
                "filestore",
                "services",
                "group_mgmt",
                "/log",
                r"C:\runs\export.log",
                "/quiet",
            ),
        ],
        "environment": {
            "server_build": "26100.1",
            "powershell_edition": "Desktop",
            "powershell_version": "5.1.26100.1",
            "group_policy_module_version": "1.0.0.0",
            "gpmc_version": "built-in",
            "locale": "en-US",
            "computer_system_domain_role": 3,
        },
        "error": None,
    }
    (run_dir / "result.json").write_text(json.dumps(result), encoding="utf-8")
    return candidate, run_dir


def _run_main(
    monkeypatch: pytest.MonkeyPatch,
    candidate: Path,
    run_dir: Path,
    *,
    dirty: bool = False,
) -> dict[str, Any]:
    # Synthetic Git isolates grading; all lanes have real-Git refusal subprocess tests.
    monkeypatch.setitem(
        _FINALIZER["main"].__globals__, "assert_bound_source_bytes", lambda *_: None
    )
    root = Path(__file__).parents[1]

    def fake_run(arguments: list[str], **_kwargs: object) -> SimpleNamespace:
        if arguments[1] == "rev-parse":
            return SimpleNamespace(stdout="a" * 40 + "\n")
        if arguments[1] == "status":
            return SimpleNamespace(stdout=" M dirty\n" if dirty else "")
        raise AssertionError(arguments)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(_SCRIPT),
            str(run_dir),
            "--candidate-root",
            str(candidate),
            "--repo-root",
            str(root),
            "--no-tag",
        ],
    )
    _FINALIZER["main"]()
    return json.loads((run_dir / "verification.json").read_text(encoding="utf-8"))


def test_complete_synthetic_evidence_pack_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate, run_dir = _evidence_pack(tmp_path)
    verdict = _run_main(monkeypatch, candidate, run_dir)
    assert verdict["passed"] is True
    assert all(verdict["checks"].values())


@pytest.mark.parametrize(
    "corruption",
    [
        "deployed",
        "candidate",
        "cleanup",
        "harness_error",
        "operation",
        "command_artifact",
        "dirty",
        "areas_without_group_mgmt",
    ],
)
def test_evidence_pack_corruption_cannot_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    corruption: str,
) -> None:
    candidate, run_dir = _evidence_pack(tmp_path)
    result_path = run_dir / "result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    if corruption == "deployed":
        (run_dir / "deployed" / "run-object-security-template.ps1").write_text(
            "corrupt", encoding="utf-8"
        )
    elif corruption == "candidate":
        (run_dir / "candidate.inf").write_bytes(b"corrupt")
    elif corruption == "cleanup":
        result["cleanup_succeeded"] = False
    elif corruption == "harness_error":
        result["error"] = "synthetic harness failure"
    elif corruption == "operation":
        result["invoked_operations"][1]["arguments"].append("/configure")
    elif corruption == "command_artifact":
        (run_dir / "commands" / "export.stderr.txt").unlink()
    elif corruption == "areas_without_group_mgmt":
        for operation in result["invoked_operations"][1:]:
            operation["arguments"].remove("group_mgmt")
    result_path.write_text(json.dumps(result), encoding="utf-8")

    verdict = _run_main(monkeypatch, candidate, run_dir, dirty=corruption == "dirty")
    assert verdict["passed"] is False


# ---------------------------------------------------------------------------
# WI-064: [Group Membership] rows built by RestrictedGroupsFamily
# ---------------------------------------------------------------------------


def _replace_group_section(path: Path, section: str) -> None:
    """Rewrite `path` with its `[Group Membership]` section replaced (or dropped)."""
    from gpo_studio.security_template import decode_security_template

    text = decode_security_template(path.read_bytes()).replace("\r\n", "\n")
    head, _, _tail = text.partition("\n[Group Membership]\n")
    path.write_bytes(encode_security_template(head.rstrip("\n") + "\n" + section))


def test_candidate_group_key_must_be_star_sid(tmp_path: Path) -> None:
    """The bare-SID key WI-064 fixed is unparseable on Studio's side, not tolerated.

    `RestrictedGroupsFamily.from_template` strips a leading star, which is how
    the defect hid behind a clean round trip; the finalizer must not.
    """
    path = tmp_path / "candidate.inf"
    _write_template(path, "[Group Membership]\nS-1-5-32-544__Members = *S-1-5-32-551\n")
    with pytest.raises(ValueError, match="starred SID"):
        _template_group_membership(path, exported=False)


@pytest.mark.parametrize(
    "line",
    [
        "*S-1-5-32-544__Members = S-1-5-32-551",
        "*S-1-5-32-544__Members = Administrator",
        "*S-1-5-32-544__Members =",
        "*S-1-5-32-544__Members = *S-1-5-32-551,*S-1-5-32-551",
        "*S-1-5-32-544__Owners = *S-1-5-32-551",
    ],
)
def test_export_group_rows_are_read_strictly(tmp_path: Path, line: str) -> None:
    path = tmp_path / "exported.inf"
    _write_template(path, f"[Group Membership]\n{line}\n")
    with pytest.raises(ValueError):
        _template_group_membership(path, exported=True)


def test_export_group_rows_compare_members_as_a_set_and_fold_case(tmp_path: Path) -> None:
    path = tmp_path / "exported.inf"
    _write_template(
        path,
        "[Group Membership]\n"
        "*S-1-5-32-555__members = *S-1-5-32-551, *S-1-5-32-544\n"
        "*S-1-5-32-555__MemberOf = *S-1-5-32-545\n",
    )
    assert _template_group_membership(path, exported=True) == {
        ("s-1-5-32-555", "members"): ("s-1-5-32-544", "s-1-5-32-551"),
        ("s-1-5-32-555", "memberof"): ("s-1-5-32-545",),
    }


def test_a_template_without_group_membership_yields_no_rows(tmp_path: Path) -> None:
    path = tmp_path / "exported.inf"
    _write_template(path, '[Registry Keys]\n1="a", 0, "D:"\n')
    assert _template_group_membership(path, exported=True) == {}


def test_expected_v1_still_reads_object_rows_but_cannot_certify_groups() -> None:
    """Banked v1 packs stay re-gradable; a new run must carry Group Membership."""
    setting = {
        "section": "Registry Keys",
        "target": r"MACHINE\SOFTWARE\StudioLab\A",
        "code": 0,
        "sddl": _SDDL,
    }
    v1 = {"schema_version": 1, "settings": [setting]}
    assert len(_expected_rows(v1)) == 1
    with pytest.raises(ValueError, match="schema_version 2"):
        _expected_group_membership(v1)
    with pytest.raises(ValueError, match="schema_version"):
        _expected_rows({"schema_version": 2, "settings": [setting]})


@pytest.mark.parametrize(
    "group",
    [
        {"group_sid": "Administrators", "relation": "members", "member_sids": ["S-1-5-32-544"]},
        {"group_sid": "S-1-5-32-544", "relation": "owners", "member_sids": ["S-1-5-32-544"]},
        {"group_sid": "S-1-5-32-544", "relation": "members", "member_sids": []},
        {"group_sid": "S-1-5-32-544", "relation": "members", "member_sids": ["*S-1-5-32-1"]},
        {"group_sid": "S-1-5-32-544", "relation": "members"},
    ],
)
def test_expected_group_membership_schema_is_strict(group: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        _expected_group_membership(
            {"schema_version": 2, "settings": [], "group_membership": [group]}
        )


@pytest.mark.parametrize(
    "export_section",
    [
        # Windows dropped a relation.
        "[Group Membership]\n*S-1-5-32-551__Members = *S-1-5-32-544\n"
        "*S-1-5-32-555__Members = *S-1-5-32-544,*S-1-5-32-551\n",
        # Windows added a member.
        "[Group Membership]\n*S-1-5-32-551__Members = *S-1-5-32-544,*S-1-5-32-545\n"
        "*S-1-5-32-555__Members = *S-1-5-32-544,*S-1-5-32-551\n"
        "*S-1-5-32-555__Memberof = *S-1-5-32-545\n",
        # Windows exported nothing for the area.
        "",
    ],
    ids=["dropped-relation", "added-member", "absent-section"],
)
def test_a_group_membership_export_difference_fails_the_lane(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, export_section: str
) -> None:
    candidate, run_dir = _evidence_pack(tmp_path)
    _replace_group_section(run_dir / "exported.inf", export_section)
    verdict = _run_main(monkeypatch, candidate, run_dir)
    assert verdict["passed"] is False
    assert verdict["checks"]["windows_export_group_membership_exact"] is False
    assert verdict["checks"]["windows_export_exact"] is True
    assert verdict["group_membership_export_differences"]


def test_a_bare_sid_candidate_fails_the_lane(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The WI-064 defect, replayed through the whole finalizer."""
    candidate, run_dir = _evidence_pack(tmp_path)
    for root in (candidate, run_dir):
        _replace_group_section(
            root / "candidate.inf",
            "[Group Membership]\nS-1-5-32-551__Members = *S-1-5-32-544\n"
            "S-1-5-32-555__Members = *S-1-5-32-544,*S-1-5-32-551\n"
            "S-1-5-32-555__Memberof = *S-1-5-32-545\n",
        )
    verdict = _run_main(monkeypatch, candidate, run_dir)
    assert verdict["passed"] is False
    assert verdict["checks"]["candidate_group_membership_exact"] is False
    assert "starred SID" in verdict["comparison_error"]


def test_a_v1_expectation_cannot_pass_a_new_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate, run_dir = _evidence_pack(tmp_path)
    for root in (candidate, run_dir):
        expected = json.loads((root / "expected.json").read_text(encoding="utf-8"))
        del expected["group_membership"]
        expected["schema_version"] = 1
        (root / "expected.json").write_text(json.dumps(expected), encoding="utf-8")
    verdict = _run_main(monkeypatch, candidate, run_dir)
    assert verdict["passed"] is False
    assert verdict["checks"]["expected_schema_supported"] is False
