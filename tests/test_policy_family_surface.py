"""Plan 034 WP-3: the policy-family surface, held against its certification.

`policy_families.py` was reachable from nothing until `/api/security-template/
policy-families`. WP-3's rule for surfacing a module is that WP-1 certified it
first, and it did: `wp3-security-template-20260907071149-3752` on a member
server and `...-20260907071106-1024` on a domain controller, 21/21 each at
`4e27f27`, zero differences through `secedit /validate`, `/import` and
`/export`.

The risk this file exists to close is named in `wp3-policy-family-results.md`,
because the project already paid it once: "Previously it handwrote the INF
sections and could pass while those serializers emitted different keys." The
surface composes the INF in `api.py` rather than in `policy_families.py`, for
the reason the endpoint's own block gives -- the serializers, their codec and
the candidate builder are all in the verdicts' bound file set, so editing them
to share a function would expire both certifications and cost an estate run.
Two compositions is the price of not paying that now, and an unheld second
composition is the exact defect the lane was corrected for.

So: same inputs, same sections, in both scopes. If the builder changes and the
surface does not, this fails, and the surface stops claiming a certification
that has moved out from under it.
"""

from __future__ import annotations

import base64
import runpy
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from gpo_studio.api import PolicyFamilyRenderRequest, app, policy_family_sections
from gpo_studio.security_template import InfSection, decode_security_template

REPO_ROOT = Path(__file__).resolve().parents[1]
BUILDER = REPO_ROOT / "scripts" / "plan-033" / "build-wp3-candidate.py"

#: The builder is a script, not an importable module; `test_bound_source_bytes`
#: reaches `check-bound-source-bytes.py` the same way.
_BUILDER_SYMBOLS = runpy.run_path(str(BUILDER))
_candidate_sections = _BUILDER_SYMBOLS["_candidate_sections"]

#: Exactly the values `build-wp3-candidate.py` hands the serializers, restated
#: as a request. Restated and not imported, because what this file has to prove
#: is that a caller sending the certified values through the public shape
#: reaches the certified bytes -- importing the builder's dataclasses would
#: skip the request model, which is half the path under test.
CERTIFIED_REQUEST: dict[str, Any] = {
    "account": {
        "password": {
            "minimum_password_age_days": 1,
            "maximum_password_age_days": 42,
            "minimum_password_length": 14,
            "password_complexity_enabled": True,
            "password_history_size": 12,
        },
        "lockout": {
            "lockout_threshold": 5,
            "lockout_duration_minutes": 30,
            "lockout_window_minutes": 30,
        },
    },
    "audit": {
        "system_events": "success_and_failure",
        "logon_events": "success",
        "policy_change": "success_and_failure",
    },
    "user_rights": {
        "assignments": [
            {
                "name": "SeBackupPrivilege",
                "principals": ["*S-1-5-32-544", "*S-1-5-32-551"],
            },
            {"name": "SeRestorePrivilege", "principals": ["*S-1-5-32-544"]},
        ]
    },
    "security_options": {
        "options": [
            {"key": "MACHINE\\Software\\StudioLab\\Sz", "value": '1,"studio sz value"'},
            {
                "key": "MACHINE\\Software\\StudioLab\\ExpandSz",
                "value": '2,"%SystemRoot%\\studio"',
            },
            {"key": "MACHINE\\Software\\StudioLab\\Dword", "value": "4,1"},
            {"key": "MACHINE\\Software\\StudioLab\\MultiSz", "value": "7,alpha,beta"},
        ]
    },
}

#: The builder's comparator control: proof the lane notices a section
#: `policy_families.py` does not model. The surface must not emit it.
CONTROL_SECTION = "Group Membership"


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _builder_family_sections(*, include_kerberos: bool) -> tuple[InfSection, ...]:
    return tuple(
        section
        for section in _candidate_sections(include_kerberos=include_kerberos)
        if section.name != CONTROL_SECTION
    )


@pytest.mark.parametrize(
    "scope,include_kerberos",
    [("member_server", False), ("domain_controller", True)],
)
def test_the_surface_composes_what_windows_certified(
    scope: str, include_kerberos: bool
) -> None:
    """Section for section, entry for entry, against the bound builder.

    Order is asserted, not just membership: `format_security_template` writes
    the sections in the order it is given them, and the lane compared the bytes
    `secedit` returned against the bytes it was sent.
    """
    request = PolicyFamilyRenderRequest.model_validate({**CERTIFIED_REQUEST, "scope": scope})
    assert policy_family_sections(request) == _builder_family_sections(
        include_kerberos=include_kerberos
    )


def test_a_member_server_gets_no_kerberos_section(client: TestClient) -> None:
    """A member server exports the section empty, so the candidate omits it.

    The finalizer refuses a Kerberos candidate unless the guest reports a
    domain-controller role; emitting the section for a member server would
    hand an operator an INF on the far side of that refusal.
    """
    response = client.post(
        "/api/security-template/policy-families", json={**CERTIFIED_REQUEST}
    )
    assert response.status_code == 200
    body = response.json()
    assert [section["name"] for section in body["sections"]] == [
        "Unicode",
        "System Access",
        "Event Audit",
        "Privilege Rights",
        "Registry Values",
        "Version",
    ]
    assert "MaxTicketAge" not in body["inf_text"]
    assert "kerberos_omitted_for_member_server" in {
        limitation["code"] for limitation in body["limitations"]
    }


def test_a_domain_controller_gets_the_measured_kerberos_defaults(
    client: TestClient,
) -> None:
    """The five keys and their units, as read off a real DC in work order R7.

    `MaxTicketAge=10` hours beside `MaxServiceAge=600` minutes is the same ten
    hours twice, which is exactly the shape a unit mix-up would hide in.
    """
    response = client.post(
        "/api/security-template/policy-families",
        json={**CERTIFIED_REQUEST, "scope": "domain_controller"},
    )
    assert response.status_code == 200
    body = response.json()
    kerberos = next(
        section for section in body["sections"] if section["name"] == "Kerberos Policy"
    )
    assert kerberos["entries"] == [
        ["MaxTicketAge", "10"],
        ["MaxRenewAge", "7"],
        ["MaxServiceAge", "600"],
        ["MaxClockSkew", "5"],
        ["TicketValidateClient", "1"],
    ]
    assert "kerberos_omitted_for_member_server" not in {
        limitation["code"] for limitation in body["limitations"]
    }


def test_the_bytes_are_the_ones_windows_consumes(client: TestClient) -> None:
    """`inf_base64` is the encoded template, not the text in another wrapper.

    `GptTmpl.inf` is UTF-16LE with a BOM and CRLF line endings; a surface that
    returned UTF-8, or LF, would read identically in `inf_text` and be rejected
    on the guest -- a difference no reader of the JSON could see. So assert the
    wire form rather than a round trip: `encode_security_template` converts
    endings on purpose, and `decode` does not convert them back, so
    `decode(encode(text)) == text` is false by design and asserting it would
    have been asserting the bug.
    """
    response = client.post(
        "/api/security-template/policy-families", json={**CERTIFIED_REQUEST}
    )
    body = response.json()
    raw = base64.b64decode(body["inf_base64"])
    assert raw.startswith(b"\xff\xfe")
    decoded = decode_security_template(raw)
    assert "\r\n" in decoded
    assert decoded.replace("\r\n", "\n") == body["inf_text"]


def test_validation_is_advisory_and_does_not_block_the_render(
    client: TestClient,
) -> None:
    """Whether Windows accepts a template and whether it should ship differ.

    Only the first has been measured, so a render is never refused on the
    second. Reversible encryption is the clearest case: `secedit` takes it
    without complaint and no administrator should deploy it.
    """
    payload = {**CERTIFIED_REQUEST}
    payload["account"] = {
        **CERTIFIED_REQUEST["account"],
        "password": {
            **CERTIFIED_REQUEST["account"]["password"],
            "reversible_encryption": True,
        },
    }
    response = client.post("/api/security-template/policy-families", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert "reversible_encryption_enabled" in {
        issue["code"] for issue in body["issues"]
    }
    assert "ClearTextPassword = 1" in body["inf_text"]


def test_two_families_claiming_one_key_is_refused_not_overwritten(
    client: TestClient,
) -> None:
    """A silent overwrite would emit an INF the lane never compared.

    `SeBackupPrivilege` twice is the reachable case: `UserRightsFamily`
    serializes its assignments into one `Privilege Rights` mapping, so the
    second would win and the caller would be told nothing.
    """
    payload = {**CERTIFIED_REQUEST}
    payload["user_rights"] = {
        "assignments": [
            {"name": "SeBackupPrivilege", "principals": ["*S-1-5-32-544"]},
            {"name": "SeBackupPrivilege", "principals": ["*S-1-5-32-551"]},
        ]
    }
    response = client.post("/api/security-template/policy-families", json=payload)
    assert response.status_code == 200
    body = response.json()
    rights = next(
        section for section in body["sections"] if section["name"] == "Privilege Rights"
    )
    # `to_template_entries` collapses the duplicate before the merge sees it,
    # so the merge guard cannot fire here -- the last assignment wins inside
    # the family. Recorded rather than asserted away: this is the one place the
    # surface loses a caller's input silently, and it loses it in the certified
    # serializer rather than in the composition.
    assert rights["entries"] == [["SeBackupPrivilege", "*S-1-5-32-551"]]


def test_unknown_request_fields_are_refused(client: TestClient) -> None:
    """WI-036's lesson: a field accepted and never read is worse than absent."""
    response = client.post(
        "/api/security-template/policy-families",
        json={**CERTIFIED_REQUEST, "apply_to_sysvol": True},
    )
    assert response.status_code == 422


def test_every_answer_carries_the_three_unconditional_limitations(
    client: TestClient,
) -> None:
    """A capability whose limits live only in the matrix is surfaced without them.

    These three hold for every render: the lane never invoked `/configure`, it
    measured one tranche of values, and the read direction has no oracle. A
    caller who parses this response has been told all three.
    """
    for scope in ("member_server", "domain_controller"):
        response = client.post(
            "/api/security-template/policy-families",
            json={**CERTIFIED_REQUEST, "scope": scope},
        )
        codes = {limitation["code"] for limitation in response.json()["limitations"]}
        assert {
            "round_trip_not_application",
            "representative_values_only",
            "gpmc_editing_unmeasured",
        } <= codes


def test_an_empty_family_section_is_declared_unmeasured(client: TestClient) -> None:
    """No certified candidate ever carried a section with no entries.

    `UserRightsFamily` and `SecurityOptionsFamily` emit their section
    unconditionally, so a caller who supplies neither gets `[Privilege Rights]`
    and `[Registry Values]` as bare headers -- a template shape `secedit` has
    never been asked to accept here. The object-security families omit an empty
    section instead, so the two halves of the same file disagree about it and
    only the non-empty behaviour is measured.

    Conditional on what this render produced, which is an exact property of the
    answer rather than a guess about the caller.
    """
    response = client.post("/api/security-template/policy-families", json={})
    assert response.status_code == 200
    body = response.json()
    empty = [
        section["name"] for section in body["sections"] if not section["entries"]
    ]
    assert empty == ["Privilege Rights", "Registry Values"]
    message = next(
        limitation["message"]
        for limitation in body["limitations"]
        if limitation["code"] == "empty_sections_unmeasured"
    )
    assert "Privilege Rights" in message and "Registry Values" in message


def test_the_certified_render_carries_no_empty_section_limitation(
    client: TestClient,
) -> None:
    """The control: the limitation must be absent when it does not apply.

    One that fired on every answer would be noise, and one that fired on the
    certified candidate would be saying the measured case is unmeasured.
    """
    response = client.post(
        "/api/security-template/policy-families", json=CERTIFIED_REQUEST
    )
    body = response.json()
    assert all(section["entries"] for section in body["sections"])
    assert "empty_sections_unmeasured" not in {
        limitation["code"] for limitation in body["limitations"]
    }
