"""Contract tests for the Plan 034 object-security candidate builder."""

from __future__ import annotations

import json
import runpy
import sys
from pathlib import Path

import pytest

from gpo_studio.object_security import RestrictedGroupsFamily, SystemServicesFamily
from gpo_studio.security_template import decode_security_template, parse_security_template

_SCRIPT = Path(__file__).parents[1] / "scripts" / "plan-033" / "build-object-security-candidate.py"


def _build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, dict[str, object]]:
    out = tmp_path / "candidate"
    namespace = runpy.run_path(str(_SCRIPT))
    monkeypatch.setattr(sys, "argv", [str(_SCRIPT), str(out)])
    assert namespace["main"]() == 0
    return out, namespace


def test_builder_emits_serializer_backed_mixed_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out, _namespace = _build(tmp_path, monkeypatch)

    expected = json.loads((out / "expected.json").read_text(encoding="utf-8"))
    template = parse_security_template(
        decode_security_template((out / "candidate.inf").read_bytes())
    )
    assert expected["schema_version"] == 2
    assert set(expected) == {"schema_version", "settings", "group_membership"}
    # The nine quoted-CSV object rows have no `=`; Group Membership rows do.
    assert len(template.parse_warnings) == 9
    settings = expected["settings"]
    assert len(settings) == 9
    expected_codes = {
        ("Registry Keys", rf"MACHINE\Software\GPOStudio\ObjectSecurity\Registry{code}"): code
        for code in (0, 1, 2)
    }
    expected_codes.update(
        {("File Security", rf"C:\GPOStudio\ObjectSecurity\File{code}"): code for code in (0, 1, 2)}
    )
    expected_codes.update(
        {
            ("Service General Setting", f"GPOStudioObject{name}"): code
            for name, code in (("Automatic", 2), ("Manual", 3), ("Disabled", 4))
        }
    )
    assert {(item["section"], item["target"]): item["code"] for item in settings} == expected_codes
    assert {item["sddl"] for item in settings} == {
        "D:PAR(A;CI;KA;;;BA)(A;CI;KR;;;BU)",
        "D:PAR(A;OICI;FA;;;BA)",
        "D:PAR(A;;CCDCLCSWRPWPDTLOCRSDRCWDWO;;;BA)",
    }
    for setting in expected["settings"]:
        section = template.get_section(setting["section"])
        assert section is not None
        assert any(setting["target"] in line for line in section.unknown_lines)


def test_group_membership_rows_are_the_familys_output_in_star_sid_form(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """WI-064: the rows are built by `RestrictedGroupsFamily`, not written by hand.

    The candidate's `[Group Membership]` section must be exactly what the
    family emits, so the lane measures the serializer that ships. Both
    relations are covered, and one `__Members` value carries two principals.
    """
    out, namespace = _build(tmp_path, monkeypatch)
    families = namespace["_families"]()
    restricted_groups = families[3]
    assert isinstance(restricted_groups, RestrictedGroupsFamily)

    template = parse_security_template(
        decode_security_template((out / "candidate.inf").read_bytes())
    )
    section = template.get_section("Group Membership")
    assert section is not None
    assert section.unknown_lines == ()
    assert dict(section.entries) == restricted_groups.to_template_entries()["Group Membership"]
    assert section.entries == (
        ("*S-1-5-32-551__Members", "*S-1-5-32-544"),
        ("*S-1-5-32-555__Members", "*S-1-5-32-544,*S-1-5-32-551"),
        ("*S-1-5-32-555__Memberof", "*S-1-5-32-545"),
    )

    expected = json.loads((out / "expected.json").read_text(encoding="utf-8"))
    assert expected["group_membership"] == [
        {"group_sid": "S-1-5-32-551", "relation": "members", "member_sids": ["S-1-5-32-544"]},
        {
            "group_sid": "S-1-5-32-555",
            "relation": "members",
            "member_sids": ["S-1-5-32-544", "S-1-5-32-551"],
        },
        {"group_sid": "S-1-5-32-555", "relation": "memberof", "member_sids": ["S-1-5-32-545"]},
    ]


def test_the_candidate_validates_clean_and_services_carry_descriptors() -> None:
    """WI-065: the families the lane certifies are ones the product calls valid."""
    namespace = runpy.run_path(str(_SCRIPT))
    assert namespace["candidate_issues"]() == ()
    services = namespace["_families"]()[2]
    assert isinstance(services, SystemServicesFamily)
    assert services.services
    assert all(svc.security_descriptor is not None for svc in services.services)


def test_the_builder_refuses_a_candidate_the_product_reports_issues_for(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    namespace = runpy.run_path(str(_SCRIPT))
    monkeypatch.setitem(
        namespace["main"].__globals__,
        "candidate_issues",
        lambda: ("error:unparseable_service_sddl:SystemServicesFamily/x",),
    )
    out = tmp_path / "candidate"
    monkeypatch.setattr(sys, "argv", [str(_SCRIPT), str(out)])
    with pytest.raises(SystemExit) as excinfo:
        namespace["main"]()
    assert excinfo.value.code != 0
    assert not out.exists()
