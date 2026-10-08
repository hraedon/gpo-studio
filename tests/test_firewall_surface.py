"""Plan 034: the firewall surface, held against the lane that certified it.

`POST /api/network-security/firewall/render` and
`GET /api/gpos/{guid}/firewall-policy` compose `firewall_policy.py` in
`api.py`, which no lane binds. They are honest only while they agree with the
lane, so the anchors here are the lane's own artifacts rather than restated
expectations:

- the render output for the certified request (the builder's
  `candidate_policy()`) equals the builder's emission record for record, and
  equals the banked `expected.json` byte for byte;
- those settings, posted to a GPO through the ordinary settings endpoint and
  exported, give the Registry.pol Windows returned unchanged in the certifying
  run's write leg;
- the decode of the banked native fixture, imported as a GPMC backup, equals
  what `finalize_firewall_run.py` parses from the same bytes.
"""

from __future__ import annotations

import base64
import io
import json
import runpy
import shutil
from collections.abc import Iterator
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient

from gpo_studio import api
from gpo_studio.api import app
from gpo_studio.firewall_policy import (
    FIREWALL_KEY,
    FirewallPolicy,
    from_registry_records,
    to_registry_settings,
)
from gpo_studio.model import RegistrySetting
from gpo_studio.registry_pol import parse, serialize
from gpo_studio.store import WorkspaceStore

ROOT = Path(__file__).resolve().parents[1]
BUILDER = runpy.run_path(str(ROOT / "scripts/plan-033/build-firewall-candidate.py"))
FINALIZER = runpy.run_path(str(ROOT / "scripts/windows-oracle/finalize_firewall_run.py"))
PACK = ROOT / "docs/plan-033/wp3-evidence/firewall-20261008/firewall"
NATIVE = ROOT / "tests/fixtures/native-firewall-gpmc"
CERTIFIED: FirewallPolicy = BUILDER["candidate_policy"]()

RENDER = "/api/network-security/firewall/render"
DECODE = "/api/gpos/{guid}/firewall-policy"

EVERY_RESPONSE_LIMITATIONS = {
    "policy_store_readback_not_application",
    "representative_tranche_only",
    "ipsec_pki_wired_wireless_out_of_scope",
    "gpme_display_unmeasured",
    "single_build_measured",
}


def _request_for(policy: FirewallPolicy) -> dict[str, Any]:
    """The JSON body an operator would send for this typed policy."""
    data = json.loads(json.dumps(asdict(policy)))
    for rule in data["rules"]:
        assert rule.pop("unknown_tokens") == []
    return dict(data)


CERTIFIED_REQUEST = _request_for(CERTIFIED)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[WorkspaceStore]:
    workspace = WorkspaceStore(tmp_path / "firewall.db")
    app.state.store = workspace
    app.state.owns_store = False
    yield workspace


@pytest.fixture
def client(store: WorkspaceStore) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def _render(client: TestClient, body: dict[str, Any]) -> dict[str, Any]:
    response = client.post(RENDER, json=body)
    assert response.status_code == 200, response.text
    return dict(response.json())


def _decode(client: TestClient, guid: str) -> dict[str, Any]:
    response = client.get(DECODE.format(guid=guid))
    assert response.status_code == 200, response.text
    return dict(response.json())


def _as_setting(body: dict[str, str], index: int) -> RegistrySetting:
    value: str | int = body["value"]
    if body["registry_type"] == "REG_DWORD":
        value = int(body["value"])
    return RegistrySetting(
        id=f"rendered-{index}",
        side=body["side"],  # type: ignore[arg-type]
        hive=body["hive"],  # type: ignore[arg-type]
        key=body["key"],
        value_name=body["value_name"],
        registry_type=body["registry_type"],  # type: ignore[arg-type]
        value=value,
        action=body["action"],  # type: ignore[arg-type]
    )


def _candidate_registry_pol() -> bytes:
    with ZipFile(PACK / "controller-candidate/studio-firewall-backup.zip") as archive:
        return _machine_pol(archive)


def _machine_pol(archive: ZipFile) -> bytes:
    (name,) = [
        n
        for n in archive.namelist()
        if n.casefold().endswith("/domainsysvol/gpo/machine/registry.pol")
    ]
    return archive.read(name)


# --------------------------------------------------------------------------
# Render: equal to the lane builder's emission
# --------------------------------------------------------------------------


def test_the_certified_request_is_the_builders_policy() -> None:
    request = api.FirewallRenderRequest.model_validate(CERTIFIED_REQUEST)
    assert api.firewall_policy_from_request(request) == CERTIFIED


def test_render_of_the_certified_request_equals_the_builders_emission(
    client: TestClient,
) -> None:
    body = _render(client, CERTIFIED_REQUEST)
    emitted = to_registry_settings(CERTIFIED)
    assert len(body["registry_settings"]) == len(emitted)
    for rendered, setting in zip(body["registry_settings"], emitted, strict=True):
        assert rendered == {
            "side": setting.side,
            "hive": setting.hive,
            "key": setting.key,
            "value_name": setting.value_name,
            "registry_type": setting.registry_type,
            "value": str(setting.value),
            "action": "set",
            "comment": "",
        }
    assert body["rule_strings"] == [
        {"rule_id": s.value_name, "value": s.value}
        for s in emitted
        if s.key == FIREWALL_KEY + "\\FirewallRules"
    ]
    assert len(body["rule_strings"]) == 13
    assert body["issues"] == []
    assert {item["code"] for item in body["limitations"]} == EVERY_RESPONSE_LIMITATIONS


def test_render_equals_the_banked_expectation_byte_for_byte(client: TestClient) -> None:
    """Independent of the codec as it ships today: the controller's banked bytes."""
    body = _render(client, CERTIFIED_REQUEST)
    expected = json.loads((PACK / "controller-candidate/expected.json").read_text(encoding="utf-8"))
    rendered = [
        {
            "key": s["key"],
            "value_name": s["value_name"],
            "bytes_base64": base64.b64encode(
                serialize([_as_setting(s, i)])[8:]
            ).decode("ascii"),
        }
        for i, s in enumerate(body["registry_settings"])
    ]
    assert rendered == expected["registry_records"]


def test_posted_and_exported_the_render_is_the_registry_pol_windows_returned(
    client: TestClient,
) -> None:
    """Through the ordinary settings endpoint, to the bytes the write leg proved.

    The certifying run imported the candidate with `Import-GPO` and got this
    Registry.pol back byte for byte. A GPO built from the render through
    `POST /api/gpos/{guid}/settings` must export the same bytes, or the
    surface hands operators something the lane never saw.
    """
    body = _render(client, CERTIFIED_REQUEST)
    gpo = client.post("/api/gpos", json={"name": "Firewall surface"}).json()["gpo"]
    revision = gpo["revision"]
    for setting in body["registry_settings"]:
        response = client.post(
            f"/api/gpos/{gpo['guid']}/settings",
            json={"expected_revision": revision, "reason": "firewall", "setting": setting},
        )
        assert response.status_code == 201, response.text
        revision = response.json()["gpo"]["revision"]
    exported = client.get(f"/api/gpos/{gpo['guid']}/gpmc-backup")
    assert exported.status_code == 200, exported.text
    with ZipFile(io.BytesIO(exported.content)) as archive:
        machine = _machine_pol(archive)
    assert machine == _candidate_registry_pol()
    windows = base64.b64decode(
        json.loads((PACK / "result.json").read_text(encoding="utf-8-sig"))["write_leg"][
            "registry_pol_base64"
        ]
    )
    assert machine == windows

    # And the decode endpoint reads it back as the certified policy.
    decoded = _decode(client, gpo["guid"])
    assert decoded["status"] == "decoded"
    assert decoded["unrecognised_records"] == []
    assert decoded["settings_outside_firewall_key"] == 0
    assert _policy_from_decode(decoded) == FINALIZER["_policy_dict"](CERTIFIED)


# --------------------------------------------------------------------------
# Render: refusals arrive as refusals
# --------------------------------------------------------------------------


def _with_rule(**changes: Any) -> dict[str, Any]:
    body = json.loads(json.dumps(CERTIFIED_REQUEST))
    body["rules"][0].update(changes)
    return dict(body)


@pytest.mark.parametrize(
    ("body", "code"),
    [
        (_with_rule(protocol=99), "firewall_unmeasured_protocol"),
        (_with_rule(profiles=["public"]), "firewall_unmeasured_profiles"),
        (_with_rule(local_port="0080"), "firewall_unmeasured_local_port"),
        (_with_rule(interface_type="Wireless"), "firewall_unmeasured_interface"),
        (_with_rule(name="bad|name"), "firewall_invalid_text"),
        (
            {**CERTIFIED_REQUEST, "public": {"enabled": True}},
            "firewall_unmeasured_public_settings",
        ),
        ({**CERTIFIED_REQUEST, "policy_version": None}, "firewall_missing_policy_version"),
        ({**CERTIFIED_REQUEST, "policy_version": 546}, "firewall_unmeasured_policy_version"),
    ],
)
def test_outside_the_tranche_is_a_422_with_the_codecs_code(
    client: TestClient, body: dict[str, Any], code: str
) -> None:
    response = client.post(RENDER, json=body)
    assert response.status_code == 422, response.text
    issues = response.json()["error"]["issues"]
    assert code in {issue["code"] for issue in issues}


def test_duplicate_rule_ids_are_refused(client: TestClient) -> None:
    body = json.loads(json.dumps(CERTIFIED_REQUEST))
    body["rules"][1]["rule_id"] = body["rules"][0]["rule_id"].lower()
    response = client.post(RENDER, json=body)
    assert response.status_code == 422
    assert "firewall_duplicate_rule" in {
        issue["code"] for issue in response.json()["error"]["issues"]
    }


@pytest.mark.parametrize(
    "body",
    [
        _with_rule(protocol=True),
        _with_rule(protocol="6"),
        _with_rule(enabled="true"),
        _with_rule(remote_port_range=[1, 2, 3]),
        _with_rule(unknown_tokens=[]),
        {**CERTIFIED_REQUEST, "ipsec": {}},
    ],
)
def test_loose_types_and_unknown_fields_never_reach_the_codec(
    client: TestClient, body: dict[str, Any]
) -> None:
    response = client.post(RENDER, json=body)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["message"] == "Invalid request"


def test_an_empty_policy_renders_only_the_version(client: TestClient) -> None:
    body = _render(client, {})
    assert [(s["value_name"], s["value"]) for s in body["registry_settings"]] == [
        ("PolicyVersion", "545")
    ]
    assert body["rule_strings"] == []


# --------------------------------------------------------------------------
# Decode: equal to what the finalizer parses
# --------------------------------------------------------------------------


def _policy_from_decode(body: dict[str, Any]) -> dict[str, Any]:
    """The decode response in `finalize_firewall_run._policy_dict`'s shape."""
    rules = []
    for rule in body["rules"]:
        rule = dict(rule)
        rule.pop("rule_string")
        rules.append(rule)
    rules.sort(key=lambda r: r["rule_id"])
    return {
        "policy_version": body["policy_version"],
        "domain": body["profiles"]["domain"],
        "private": body["profiles"]["private"],
        "public": body["profiles"]["public"],
        "rules": rules,
    }


def _import(client: TestClient, backup: Path) -> dict[str, Any]:
    response = client.post(
        "/api/backups/import",
        json={"path": str(backup), "actor": "tester", "reason": "firewall fixture"},
    )
    assert response.status_code == 201, response.text
    return dict(response.json()["gpo"])


def test_decode_of_the_native_fixture_equals_what_the_finalizer_parses(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GPO_STUDIO_INBOX_DIR", str(tmp_path))
    backup = tmp_path / "backup"
    shutil.copytree(NATIVE / "backup", backup)
    gpo = _import(client, backup)
    body = _decode(client, gpo["guid"])
    raw = (NATIVE / "Registry.pol").read_bytes()
    (pol,) = backup.rglob("registry.pol")
    assert pol.read_bytes() == raw
    parsed = from_registry_records(parse(raw))
    assert body["status"] == "decoded"
    assert body["issues"] == []
    assert body["unrecognised_records"] == []
    assert _policy_from_decode(body) == FINALIZER["_policy_dict"](parsed.policy)
    assert len(body["rules"]) == 13
    records = {(r.key, r.value_name): r.value for r in parse(raw)}
    for rule in body["rules"]:
        assert rule["rule_string"] == records[(FIREWALL_KEY + "\\FirewallRules", rule["rule_id"])]
    assert {item["code"] for item in body["limitations"]} == EVERY_RESPONSE_LIMITATIONS


def test_decode_of_the_certifying_runs_windows_authored_policy(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The read leg's Registry.pol (authored by New-NetFirewallRule), imported."""
    monkeypatch.setenv("GPO_STUDIO_INBOX_DIR", str(tmp_path))
    backup = tmp_path / "backup"
    shutil.copytree(NATIVE / "backup", backup)
    raw = base64.b64decode(
        json.loads((PACK / "result.json").read_text(encoding="utf-8-sig"))["read_leg"][
            "registry_pol_base64"
        ]
    )
    (pol,) = backup.rglob("registry.pol")
    pol.write_bytes(raw)
    gpo = _import(client, backup)
    body = _decode(client, gpo["guid"])
    expected = json.loads((PACK / "controller-candidate/expected.json").read_text(encoding="utf-8"))
    assert body["status"] == "decoded"
    assert body["unrecognised_records"] == []
    assert _policy_from_decode(body) == expected["policy"]
    assert _policy_from_decode(body) == FINALIZER["_policy_dict"](
        from_registry_records(parse(raw)).policy
    )


def _gpo_with(
    store: WorkspaceStore, settings: list[RegistrySetting], name: str = "Firewall decode"
) -> str:
    gpo = store.create_gpo(
        name, "", identity="tester", reason="decode", settings=tuple(settings)
    )
    return gpo.guid


def _setting(key: str, name: str, kind: str, value: str | int, side: str = "computer") -> (
    RegistrySetting
):
    return RegistrySetting(
        id=f"{key}:{name}",
        side=side,  # type: ignore[arg-type]
        hive="HKLM" if side == "computer" else "HKCU",
        key=key,
        value_name=name,
        registry_type=kind,  # type: ignore[arg-type]
        value=value,
    )


_VERSION = _setting(FIREWALL_KEY, "PolicyVersion", "REG_DWORD", 545)
_RULE_01 = next(
    s
    for s in to_registry_settings(CERTIFIED)
    if s.key == FIREWALL_KEY + "\\FirewallRules" and s.value_name == "StudioFwLane-01"
)


def test_a_gpo_without_firewall_records_is_empty(
    store: WorkspaceStore, client: TestClient
) -> None:
    guid = _gpo_with(store, [_setting(r"Software\Policies\Other", "X", "REG_DWORD", 1)])
    body = _decode(client, guid)
    assert body["status"] == "empty"
    assert body["rules"] == [] and body["unrecognised_records"] == []
    assert body["settings_outside_firewall_key"] == 1
    assert {item["code"] for item in body["limitations"]} == EVERY_RESPONSE_LIMITATIONS


def test_unknown_tokens_are_preserved_and_flagged(
    store: WorkspaceStore, client: TestClient
) -> None:
    rule = _RULE_01
    assert isinstance(rule.value, str)
    text = rule.value + "Future=Token|"
    guid = _gpo_with(store, [_VERSION, replace(rule, value=text)])
    body = _decode(client, guid)
    assert body["status"] == "decoded"
    (decoded,) = body["rules"]
    assert decoded["rule_string"] == text
    assert decoded["unknown_tokens"] == [{"position": 7, "text": "Future=Token"}]
    codes = {item["code"] for item in body["limitations"]}
    assert codes == EVERY_RESPONSE_LIMITATIONS | {"unmodeled_tokens_preserved_not_editable"}


def test_a_record_outside_the_tranche_refuses_the_whole_decode(
    store: WorkspaceStore, client: TestClient
) -> None:
    assert isinstance(_RULE_01.value, str)
    bad = replace(_RULE_01, value=_RULE_01.value.replace("Protocol=6", "Protocol=99"))
    guid = _gpo_with(store, [_VERSION, bad])
    body = _decode(client, guid)
    assert body["status"] == "refused"
    assert body["rules"] == []
    assert len(body["unrecognised_records"]) == 2
    assert "firewall_unmeasured_protocol" in {issue["code"] for issue in body["issues"]}


def test_records_without_policy_version_are_legacy(
    store: WorkspaceStore, client: TestClient
) -> None:
    legacy = _setting(FIREWALL_KEY + r"\DomainProfile", "EnableFirewall", "REG_DWORD", 1)
    guid = _gpo_with(store, [legacy])
    body = _decode(client, guid)
    assert body["status"] == "legacy"
    assert body["rules"] == []
    assert len(body["unrecognised_records"]) == 1
    assert [issue["code"] for issue in body["issues"]] == [
        "firewall_legacy_without_policy_version"
    ]


def test_a_user_side_firewall_record_comes_back_unrecognised(
    store: WorkspaceStore, client: TestClient
) -> None:
    user = _setting(FIREWALL_KEY, "Something", "REG_DWORD", 1, side="user")
    guid = _gpo_with(store, [_VERSION, user])
    body = _decode(client, guid)
    assert body["status"] == "decoded"
    assert [(r["side"], r["value_name"]) for r in body["unrecognised_records"]] == [
        ("user", "Something")
    ]


def test_decode_of_an_unknown_gpo_is_404(client: TestClient) -> None:
    response = client.get(DECODE.format(guid="00000000-0000-4000-8000-000000000000"))
    assert response.status_code == 404


def test_a_same_named_user_record_cannot_supply_a_machine_rules_string(
    store: WorkspaceStore, client: TestClient
) -> None:
    """Review finding (Sol, PR 96): the raw-string lookup ignored side and hive."""
    assert isinstance(_RULE_01.value, str)
    user = replace(
        _RULE_01,
        id="user-shadow",
        side="user",
        hive="HKCU",
        value="USER SIDE UNINTERPRETED",
    )
    for index, order in enumerate(([_VERSION, _RULE_01, user], [_VERSION, user, _RULE_01])):
        guid = _gpo_with(store, order, name=f"Firewall shadow {index}")
        body = _decode(client, guid)
        assert body["status"] == "decoded"
        (decoded,) = body["rules"]
        assert decoded["rule_string"] == _RULE_01.value
        assert [(r["side"], r["value"]) for r in body["unrecognised_records"]] == [
            ("user", "USER SIDE UNINTERPRETED")
        ]


# --------------------------------------------------------------------------
# Every response carries the limitations, refusals included
# --------------------------------------------------------------------------


def _limitation_codes(body: dict[str, Any]) -> set[str]:
    return {item["code"] for item in body["limitations"]}


def test_a_codec_refusal_carries_the_limitations(client: TestClient) -> None:
    response = client.post(RENDER, json=_with_rule(protocol=99))
    assert response.status_code == 422
    body = response.json()
    assert "firewall_unmeasured_protocol" in {i["code"] for i in body["error"]["issues"]}
    assert _limitation_codes(body) == EVERY_RESPONSE_LIMITATIONS


def test_a_request_validation_refusal_carries_the_limitations(client: TestClient) -> None:
    response = client.post(RENDER, json=_with_rule(protocol=True))
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["message"] == "Invalid request"
    assert _limitation_codes(body) == EVERY_RESPONSE_LIMITATIONS


def test_an_unknown_gpo_carries_the_limitations(client: TestClient) -> None:
    response = client.get(DECODE.format(guid="00000000-0000-4000-8000-000000000000"))
    assert response.status_code == 404
    assert _limitation_codes(response.json()) == EVERY_RESPONSE_LIMITATIONS


def test_other_routes_errors_are_unchanged(client: TestClient) -> None:
    """The control: only the registered firewall routes gain the key."""
    response = client.get("/api/gpos/00000000-0000-4000-8000-000000000000")
    assert response.status_code == 404
    assert set(response.json()) == {"error"}
    response = client.post("/api/security-template/object-security", json={"bogus": 1})
    assert response.status_code == 422
    assert set(response.json()) == {"error"}


# --------------------------------------------------------------------------
# ... including refusals made before the handler runs (Sol, second pass)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("shape", "status"),
    [
        ("invalid-utf8-json", 400),
        ("oversized", 413),
        ("chunked", 400),
        ("origin", 403),
        ("host", 421),
    ],
)
def test_refusals_before_the_handler_carry_the_limitations(
    store: WorkspaceStore, monkeypatch: pytest.MonkeyPatch, shape: str, status: int
) -> None:
    """Middleware and body-parsing refusals know the route by its path."""
    monkeypatch.setenv("GPO_STUDIO_UNSAFE_BIND", "0")
    kwargs: dict[str, Any] = {
        "invalid-utf8-json": {
            "content": b"\xff",
            "headers": {"content-type": "application/json"},
        },
        "oversized": {
            "content": b"{}",
            "headers": {"content-length": str(api.MAX_REQUEST_BODY_BYTES + 1)},
        },
        "chunked": {"content": b"{}", "headers": {"transfer-encoding": "chunked"}},
        "origin": {"json": {}, "headers": {"origin": "null"}},
        "host": {"json": {}, "headers": {"host": "evil.example"}},
    }[shape]
    with TestClient(app, base_url="http://127.0.0.1") as safe_client:
        response = safe_client.post(RENDER, **kwargs)
        assert response.status_code == status, response.text
        assert _limitation_codes(response.json()) == EVERY_RESPONSE_LIMITATIONS
        # The decode route too, for the GET-reachable refusals.
        if shape == "host":
            response = safe_client.get(
                DECODE.format(guid="00000000-0000-4000-8000-000000000000"),
                headers={"host": "evil.example"},
            )
            assert response.status_code == 421
            assert _limitation_codes(response.json()) == EVERY_RESPONSE_LIMITATIONS
        # The control: the same refusal elsewhere keeps its old body.
        other = safe_client.post("/api/gpos", **kwargs)
        assert other.status_code == status, other.text
        assert "limitations" not in other.json()


def test_a_wrong_method_on_a_firewall_route_carries_the_limitations(
    client: TestClient,
) -> None:
    response = client.get(RENDER)
    assert response.status_code == 405
    body = response.json()
    assert body["detail"] == "Method Not Allowed"
    assert _limitation_codes(body) == EVERY_RESPONSE_LIMITATIONS
    assert set(client.get("/api/no-such-route").json()) == {"detail"}


@pytest.mark.parametrize(
    ("route", "limited"),
    [(RENDER, True), ("/api/gpos", False), ("/api/security-template/object-security", False)],
)
def test_a_non_json_body_is_a_422_not_a_500(
    client: TestClient, route: str, limited: bool
) -> None:
    """Pre-existing (found in review): bytes in a validation issue crashed the handler."""
    response = client.post(route, content=b"x", headers={"content-type": "text/plain"})
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["error"]["message"] == "Invalid request"
    inputs = [issue.get("input") for issue in body["error"]["issues"]]
    assert "<1 bytes, not JSON>" in inputs
    assert ("limitations" in body) is limited


# --------------------------------------------------------------------------
# Limitations follow the router's own path, not a re-parsed URL (Sol, third pass)
# --------------------------------------------------------------------------


@pytest.fixture
def safe_client(store: WorkspaceStore, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("GPO_STUDIO_UNSAFE_BIND", "0")
    with TestClient(app, base_url="http://127.0.0.1") as test_client:
        yield test_client


@pytest.mark.parametrize("encoded", ["%3F", "%23", "%09"])
def test_an_unmatched_encoded_suffix_gains_no_limitations(
    safe_client: TestClient, encoded: str
) -> None:
    """`render%3Fextra` routes nowhere; it must not borrow the firewall's body."""
    path = f"/api/network-security/firewall/render{encoded}extra"
    response = safe_client.get(path)
    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}
    refused = safe_client.post(path, json={}, headers={"origin": "null"})
    assert refused.status_code == 403
    assert refused.json() == {"error": {"message": "Origin not allowed"}}


@pytest.mark.parametrize("encoded", ["%3F", "%23"])
def test_a_matching_encoded_guid_keeps_the_limitations(
    safe_client: TestClient, encoded: str
) -> None:
    """`/api/gpos/missing%3Fx/firewall-policy` routes to the decode endpoint."""
    path = f"/api/gpos/missing{encoded}x/firewall-policy"
    response = safe_client.get(path)
    assert response.status_code == 404
    assert _limitation_codes(response.json()) == EVERY_RESPONSE_LIMITATIONS
    # Refused by the origin middleware before routing; the router would still
    # pick the decode route (method mismatch is a partial match).
    refused = safe_client.post(path, json={}, headers={"origin": "null"})
    assert refused.status_code == 403
    assert _limitation_codes(refused.json()) == EVERY_RESPONSE_LIMITATIONS


def test_a_letter_encoded_in_a_firewall_segment_still_routes_there(
    safe_client: TestClient,
) -> None:
    response = safe_client.post(
        "/api/network-security/firewall/rend%65r", json={}, headers={"origin": "null"}
    )
    assert response.status_code == 403
    assert _limitation_codes(response.json()) == EVERY_RESPONSE_LIMITATIONS
