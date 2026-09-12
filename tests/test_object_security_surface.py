"""Plan 034 WP-3: the object-security surface, held against its certification.

The second module this plan surfaces, by the route the first one established
and under the same constraint: `object_security.py`, `sddl.py`,
`security_template.py` and `build-object-security-candidate.py` are all in the
verdict's bound file set, so the composition lives in `api.py` and this file is
what stops the two drifting. Same shape as
`test_policy_family_surface.py`, same reason.

Certified by `object-security-20260905191252-4253` at `f5cad577`, 18/18 checks,
succeeding `object-security-20260907075319-7408` (19/19) on the same lane.
Three Registry Keys rows, three File Security rows and three Service General
Setting rows, exercising propagation codes 0/1/2 and startup codes 2/3/4.

**Restricted groups are not part of the surface**, and one test here exists to
keep it that way. The candidate has no `[Group Membership]` rows, so the
serializer for that family has never been read by an oracle -- and when it was
looked at, it emitted a bare SID where Windows emits a star-SID (WI-064).
"""

from __future__ import annotations

import base64
import runpy
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from gpo_studio.api import (
    ObjectSecurityRenderRequest,
    app,
    object_security_sections,
)
from gpo_studio.object_security import (
    RestrictedGroup,
    RestrictedGroupMember,
    RestrictedGroupsFamily,
)
from gpo_studio.security_template import decode_security_template

REPO_ROOT = Path(__file__).resolve().parents[1]
BUILDER = REPO_ROOT / "scripts" / "plan-033" / "build-object-security-candidate.py"

_BUILDER_SYMBOLS = runpy.run_path(str(BUILDER))
_candidate_sections = _BUILDER_SYMBOLS["candidate_sections"]

_REGISTRY_SDDL = "D:PAR(A;CI;KA;;;BA)(A;CI;KR;;;BU)"
_FILE_SDDL = "D:PAR(A;OICI;FA;;;BA)"
_SERVICE_SDDL = "D:PAR(A;;CCDCLCSWRPWPDTLOCRSDRCWDWO;;;BA)"

#: Exactly what `build-object-security-candidate.py` hands the families,
#: restated through the public request shape for the reason the policy-family
#: file gives: the request model is half the path under test.
CERTIFIED_REQUEST: dict[str, Any] = {
    "registry_keys": [
        {
            "key_path": rf"MACHINE\Software\GPOStudio\ObjectSecurity\Registry{code}",
            "raw_sddl": _REGISTRY_SDDL,
            "propagation": mode,
        }
        for code, mode in ((0, "propagate"), (1, "do_not_allow_replace"), (2, "replace"))
    ],
    "files": [
        {
            "file_path": rf"C:\GPOStudio\ObjectSecurity\File{code}",
            "raw_sddl": _FILE_SDDL,
            "propagation": mode,
        }
        for code, mode in ((0, "propagate"), (1, "do_not_allow_replace"), (2, "replace"))
    ],
    "services": [
        {
            "service_name": f"GPOStudioObject{mode.title()}",
            "startup_mode": mode,
            "raw_sddl": _SERVICE_SDDL,
        }
        for mode in ("automatic", "manual", "disabled")
    ],
}


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_the_surface_composes_what_windows_certified() -> None:
    """Section for section, entry for entry, against the bound builder."""
    request = ObjectSecurityRenderRequest.model_validate(CERTIFIED_REQUEST)
    assert object_security_sections(request) == _candidate_sections()


def test_the_section_order_is_this_lanes_order_not_the_other_ones() -> None:
    """`Version` comes second here and last in the policy-family surface.

    Both orders were accepted by `secedit`, each on its own run, and neither
    has been measured against the other. So each surface emits the order its
    own lane certified. Asserting it here is what stops someone tidying the two
    into agreement and silently moving one off its evidence.
    """
    request = ObjectSecurityRenderRequest.model_validate(CERTIFIED_REQUEST)
    assert [section.name for section in object_security_sections(request)] == [
        "Unicode",
        "Version",
        "Registry Keys",
        "File Security",
        "Service General Setting",
    ]


def test_all_three_propagation_codes_and_startup_codes_survive(
    client: TestClient,
) -> None:
    """0/1/2 and 2/3/4 are the whole measured span; a dropped code is silent."""
    response = client.post(
        "/api/security-template/object-security", json=CERTIFIED_REQUEST
    )
    assert response.status_code == 200
    sections = {s["name"]: s["entries"] for s in response.json()["sections"]}
    assert [entry[1].split(",", 1)[0] for entry in sections["Registry Keys"]] == [
        "0",
        "1",
        "2",
    ]
    assert [entry[1].split(",", 1)[0] for entry in sections["File Security"]] == [
        "0",
        "1",
        "2",
    ]
    assert [
        entry[1].split(",", 1)[0] for entry in sections["Service General Setting"]
    ] == ["2", "3", "4"]


def test_the_bytes_are_the_ones_windows_consumes(client: TestClient) -> None:
    """UTF-16LE with a BOM and CRLF endings, same contract as the other surface."""
    response = client.post(
        "/api/security-template/object-security", json=CERTIFIED_REQUEST
    )
    body = response.json()
    decoded = decode_security_template(base64.b64decode(body["inf_base64"]))
    assert decoded.startswith("[Unicode]")
    assert "\r\n" in decoded
    assert decoded.replace("\r\n", "\n") == body["inf_text"]


def test_a_permissive_acl_renders_clean_and_the_response_says_why(
    client: TestClient,
) -> None:
    """WI-055's ruling, surfaced as a limitation rather than as silence.

    Everyone (`WD`) full control (`KA`) produces no issue -- deliberately, not
    by oversight. A caller who reads an empty `issues` list as approval has to
    have been told, and `acl_content_is_not_judged` is where they are told.
    """
    payload = {
        **CERTIFIED_REQUEST,
        "registry_keys": [
            {
                "key_path": r"MACHINE\Software\Contoso",
                "raw_sddl": "D:PAR(A;CI;KA;;;WD)",
                "propagation": "propagate",
            }
        ],
    }
    response = client.post("/api/security-template/object-security", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["issues"] == []
    assert "acl_content_is_not_judged" in {
        limitation["code"] for limitation in body["limitations"]
    }


def test_replace_propagation_on_a_system_hive_is_still_flagged(
    client: TestClient,
) -> None:
    """The one hazard `validate` does judge, so the silence above is not total.

    Without this the permissive-ACL test above could pass against a validator
    that had stopped checking anything at all, which would look identical from
    the outside.
    """
    payload = {
        "registry_keys": [
            {
                "key_path": r"MACHINE\SYSTEM\CurrentControlSet",
                "raw_sddl": "D:PAR(A;CI;KA;;;BA)",
                "propagation": "replace",
            }
        ]
    }
    response = client.post("/api/security-template/object-security", json=payload)
    assert response.status_code == 200
    assert response.json()["issues"] != []


def test_restricted_groups_cannot_be_sent_and_the_response_says_so(
    client: TestClient,
) -> None:
    """WI-064: the family is omitted, not offered with a warning.

    `extra="forbid"` turns a caller's attempt into a 422 naming the field
    rather than a render that quietly dropped it -- WI-036's lesson applied
    before the same mistake could be made twice.
    """
    response = client.post(
        "/api/security-template/object-security",
        json={**CERTIFIED_REQUEST, "restricted_groups": []},
    )
    assert response.status_code == 422
    limitations = client.post(
        "/api/security-template/object-security", json=CERTIFIED_REQUEST
    ).json()["limitations"]
    assert "restricted_groups_not_surfaced" in {
        limitation["code"] for limitation in limitations
    }


def test_the_restricted_groups_writer_still_emits_the_unmeasured_key_form() -> None:
    """WI-064, pinned: the defect that keeps the family off the surface.

    Windows exports `*S-1-5-32-544__Members` (R4, `wp3-expansion-design.md`);
    the serializer writes `S-1-5-32-544__Members`, while starring every SID in
    the *value* of the same entry. The parser strips a leading star, so Studio
    reads its own output back into the model that produced it and the round
    trip is clean -- which is exactly why nothing noticed.

    Asserting the wrong form on purpose. `object_security.py` is bound by the
    live verdict, so the one-line fix expires it and costs an estate run; this
    fails the moment someone fixes the writer without re-running the lane, and
    that is the reminder the two have to move together.
    """
    family = RestrictedGroupsFamily(
        groups=(
            RestrictedGroup(
                group_sid="S-1-5-32-544",
                members=(RestrictedGroupMember(sid="S-1-5-32-551"),),
            ),
        )
    )
    entries = family.to_template_entries()["Group Membership"]
    assert list(entries) == ["S-1-5-32-544__Members"], (
        "the restricted-groups key form changed. If it is now the star form "
        "Windows exports, WI-064's code half is done -- the lane must re-run "
        "with Group Membership rows in its candidate before the family is "
        "surfaced, and this test comes out with that batch."
    )
    assert entries["S-1-5-32-544__Members"] == "*S-1-5-32-551", (
        "the value is starred and the key is not, in the same call; that "
        "self-disagreement is what makes WI-064 unambiguous"
    )


def test_every_answer_carries_all_four_limitations(client: TestClient) -> None:
    """None of these is conditional: the lane's scope is the same every time."""
    response = client.post(
        "/api/security-template/object-security", json=CERTIFIED_REQUEST
    )
    assert {
        limitation["code"] for limitation in response.json()["limitations"]
    } == {
        "round_trip_not_application",
        "acl_content_is_not_judged",
        "restricted_groups_not_surfaced",
        "first_tranche_only",
    }


def test_the_certified_service_descriptor_is_not_called_unparseable(
    client: TestClient,
) -> None:
    """WI-065: the surface parses the SDDL, so `validate` means what it says.

    The lane's candidate uses `D:PAR(A;;CCDCLCSWRPWPDTLOCRSDRCWDWO;;;BA)` for
    all three services. Windows accepted it and re-exported it byte for byte,
    and `parse_sddl` reads it without complaint -- so an operator sending the
    certified candidate through this endpoint must not be told it is malformed.
    """
    response = client.post(
        "/api/security-template/object-security", json=CERTIFIED_REQUEST
    )
    assert response.json()["issues"] == []


def test_the_family_still_misdiagnoses_an_unparsed_descriptor() -> None:
    """WI-065, pinned at its source, so the mitigation above stays honest.

    `SystemServicesFamily.validate` reads `raw_sddl and security_descriptor is
    None` as "could not be parsed". That field is only populated by
    `from_template`; a directly constructed model carries `None` because
    nothing tried, not because something failed. The candidate builder
    constructs services exactly that way and never calls `validate`, which is
    how a valid descriptor came to be reported as malformed with nobody seeing
    it.

    `object_security.py` is bound by the live verdict, so the fix costs an
    estate run and this asserts the defect rather than its absence. When it
    fails, the check has been corrected -- and the entry comes out with the
    batch that re-runs the lane.
    """
    from gpo_studio.object_security import ServiceSecurity, SystemServicesFamily

    family = SystemServicesFamily(
        services=(
            ServiceSecurity(
                service_name="Spooler",
                startup_mode="automatic",
                raw_sddl="D:PAR(A;;CCDCLCSWRPWPDTLOCRSDRCWDWO;;;BA)",
            ),
        )
    )
    assert [issue.code for issue in family.validate()] == ["unparseable_service_sddl"], (
        "the unparsed/unparseable conflation is gone. If the check now "
        "distinguishes them, WI-065's code half is done: the lane must re-run "
        "before the verdict binding this module is honest again, and this test "
        "comes out with that batch."
    )


def test_genuinely_unparseable_sddl_is_still_reported(client: TestClient) -> None:
    """The control: parsing at the surface must not silence the real case.

    Without this, `_parsed_sddl` could return a descriptor for anything and the
    two tests above would still pass, having turned a misdiagnosis into a
    blanket exemption.
    """
    response = client.post(
        "/api/security-template/object-security",
        json={
            "services": [
                {
                    "service_name": "Spooler",
                    "startup_mode": "automatic",
                    "raw_sddl": "not-a-descriptor",
                }
            ]
        },
    )
    assert response.status_code == 200
    assert [issue["code"] for issue in response.json()["issues"]] == [
        "unparseable_service_sddl"
    ]
