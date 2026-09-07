"""Contract tests for the Plan 034 object-security candidate builder."""

from __future__ import annotations

import json
import runpy
import sys
from pathlib import Path

from gpo_studio.security_template import decode_security_template, parse_security_template

_SCRIPT = Path(__file__).parents[1] / "scripts" / "plan-033" / "build-object-security-candidate.py"


def test_builder_emits_serializer_backed_mixed_candidate(tmp_path: Path, monkeypatch) -> None:
    out = tmp_path / "candidate"
    namespace = runpy.run_path(str(_SCRIPT))
    monkeypatch.setattr(sys, "argv", [str(_SCRIPT), str(out)])
    assert namespace["main"]() == 0

    expected = json.loads((out / "expected.json").read_text(encoding="utf-8"))
    template = parse_security_template(
        decode_security_template((out / "candidate.inf").read_bytes())
    )
    assert expected["schema_version"] == 1
    assert set(expected) == {"schema_version", "settings"}
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
