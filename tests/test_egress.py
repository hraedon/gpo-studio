"""Egress is the boundary: every route is declared, every artifact is checked
in its final form (final-r10).

Round 8 kept the writers in a hand list and round 9 found them with a static
scan of the source; reviewers bypassed both with ordinary Python. This module
works at run time instead:

* it enumerates ``app.routes`` and requires every route to be declared, in
  `api.ROUTE_ARTIFACTS` with the format its result is validated against (and
  its endpoint wrapped by `api.egress` for that format), or in
  `api.NO_ARTIFACT_ROUTES` with the reason it returns no artifact -- a route
  added without a declaration fails, as the planted routes below show;
* for every artifact route it makes the writer behind the route emit a hostile
  artifact -- a value out of its literal, a smuggled line, an unsafe member
  name -- and requires the route to answer 422 instead of shipping it;
* and it holds the format validators to the reviewers' payloads directly.
"""

from __future__ import annotations

import base64
import io
import zipfile
from collections.abc import Callable, Iterator, Mapping
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from starlette.routing import BaseRoute

from gpo_studio import api as api_module
from gpo_studio import egress
from gpo_studio.api import NO_ARTIFACT_ROUTES, ROUTE_ARTIFACTS, app
from gpo_studio.emit_integrity import EmitError
from gpo_studio.store import WorkspaceStore


def route_problems(
    routes: list[BaseRoute],
    artifacts: Mapping[tuple[str, str], str],
    no_artifact: Mapping[tuple[str, str], str],
) -> list[str]:
    """Every route not declared exactly once, or declared but not wrapped."""
    problems: list[str] = []
    seen: set[tuple[str, str]] = set()
    for route in routes:
        if isinstance(route, APIRoute):
            for method in sorted(route.methods):
                key = (method, route.path)
                seen.add(key)
                if (key in artifacts) == (key in no_artifact):
                    problems.append(
                        f"{key} is declared in {'both' if key in artifacts else 'neither'} table"
                    )
                elif key in artifacts:
                    wrapped = getattr(route.endpoint, "__egress__", None)
                    if wrapped != artifacts[key]:
                        problems.append(
                            f"{key} is an artifact route its endpoint does not check ({wrapped})"
                        )
        else:
            key = ("*", getattr(route, "path", repr(route)))
            seen.add(key)
            if key not in no_artifact:
                problems.append(f"{key} is not declared")
    stale = (set(artifacts) | set(no_artifact)) - seen
    problems += [f"{key} is declared but no such route exists" for key in sorted(stale)]
    return problems


def test_every_route_is_declared() -> None:
    assert route_problems(list(app.routes), ROUTE_ARTIFACTS, NO_ARTIFACT_ROUTES) == []
    assert len(ROUTE_ARTIFACTS) == 15 and len(NO_ARTIFACT_ROUTES) == 94


def test_every_artifact_format_has_a_validator() -> None:
    from typing import get_args

    assert set(ROUTE_ARTIFACTS.values()) == set(get_args(egress.Format))


def _planted(app_under_test: FastAPI, *, declared_artifact: bool, wrapped: bool) -> list[str]:
    def script() -> dict[str, str]:
        return {"script": "param([string]$Domain = 'x')"}

    endpoint: Callable[..., Any] = script
    if wrapped:
        endpoint = api_module.egress("powershell-discovery-domain")(script)
    app_under_test.get("/api/planted/script")(endpoint)
    artifacts = dict(ROUTE_ARTIFACTS)
    if declared_artifact:
        artifacts[("GET", "/api/planted/script")] = "powershell-discovery-domain"
    return route_problems(list(app_under_test.routes), artifacts, NO_ARTIFACT_ROUTES)


def test_a_planted_undeclared_route_fails() -> None:
    planted = FastAPI()
    for route in app.routes:
        planted.router.routes.append(route)
    problems = _planted(planted, declared_artifact=False, wrapped=False)
    assert any("/api/planted/script" in p and "neither" in p for p in problems), problems


def test_a_planted_artifact_route_without_egress_fails() -> None:
    planted = FastAPI()
    for route in app.routes:
        planted.router.routes.append(route)
    problems = _planted(planted, declared_artifact=True, wrapped=False)
    assert any("/api/planted/script" in p and "does not check" in p for p in problems), problems
    wrapped = FastAPI()
    for route in app.routes:
        wrapped.router.routes.append(route)
    assert _planted(wrapped, declared_artifact=True, wrapped=True) == []


# --- every artifact route refuses a hostile artifact from its writer --------------------


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    with closing(WorkspaceStore(tmp_path / "api.db")) as store:
        monkeypatch.setattr(app.state, "store", store, raising=False)
        monkeypatch.setattr(app.state, "owns_store", False, raising=False)
        with TestClient(app, base_url="http://127.0.0.1") as test_client:
            yield test_client


def _empty_gpo(client: TestClient) -> str:
    """A GPO for the scripts routes: the Scripts lane measured scripts alone."""
    return str(
        client.post("/api/gpos", json={"name": "Egress", "actor": "probe", "reason": "r10"}).json()[
            "gpo"
        ]["guid"]
    )


def _gpo(client: TestClient) -> str:
    gpo = client.post(
        "/api/gpos", json={"name": "Egress", "actor": "probe", "reason": "r10"}
    ).json()["gpo"]
    client.post(
        f"/api/gpos/{gpo['guid']}/settings",
        json={
            "setting": {
                "side": "computer",
                "hive": "HKLM",
                "key": "Software\\Egress",
                "value_name": "V",
                "registry_type": "REG_SZ",
                "value": "x",
            },
            "actor": "probe",
            "reason": "r10",
            "expected_revision": gpo["revision"],
        },
    )
    return str(gpo["guid"])


def _zip(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return buffer.getvalue()


#: An injected statement: what a value that left its literal looks like.
_BROKEN_OUT = "\nRemove-Item -Recurse -Force C:\\\n"
_OLD_QUOTE = "lab\u2019 ) ; Write-Output PWNED ; #"


def _plan_plus(original: Callable[..., str]) -> Callable[..., str]:
    return lambda *a, **k: original(*a, **k) + _BROKEN_OUT


def _old_domain_script(domain: str = "") -> str:
    """The pre-r9 discovery writer: only ASCII ' doubled."""
    from gpo_studio import ad_discovery

    neutral = ad_discovery.discovery_script_domain("neutral.test")
    return neutral.replace("'neutral.test'", "'" + domain.replace("'", "''") + "'")


_FIREWALL: dict[str, Any] = {}
_SCRIPTS: dict[str, Any] = {}


def _firewall_request() -> dict[str, Any]:
    if not _FIREWALL:
        from test_firewall_surface import CERTIFIED_REQUEST

        _FIREWALL.update(CERTIFIED_REQUEST)
    return _FIREWALL


def _scripts_request() -> dict[str, Any]:
    if not _SCRIPTS:
        from test_scripts_surface import CERTIFIED_REQUEST

        _SCRIPTS.update(CERTIFIED_REQUEST)
    return _SCRIPTS


def _hostile_settings(original: Callable[..., Any]) -> Callable[..., Any]:
    """A token smuggled into a rule string: well-formed registry data that
    validation passes, and that only the firewall codec can see is not what the
    request asked for."""
    from dataclasses import replace

    def write(*args: Any, **kwargs: Any) -> Any:
        settings = list(original(*args, **kwargs))
        for index, setting in enumerate(settings):
            if setting.registry_type == "REG_SZ" and str(setting.value).startswith("v2."):
                settings[index] = replace(setting, value=str(setting.value) + "LPort=4444|")
                break
        else:  # pragma: no cover - the certified request has rules
            raise AssertionError("no rule string to tamper with")
        return settings

    return write


#: (method, path) -> (request, attribute of api to replace, the replacement factory).
_HOSTILE: dict[
    tuple[str, str],
    tuple[
        Callable[[TestClient], tuple[str, dict[str, Any] | None, dict[str, str] | None]],
        str,
        Callable[[Any], Any],
    ],
] = {
    ("GET", "/api/gpos/{guid}/export.zip"): (
        lambda c: (f"/api/gpos/{_gpo(c)}/export.zip", None, None),
        "export_bundle",
        lambda _o: lambda *a, **k: _zip({"../escape.ps1": b"x"}),
    ),
    ("GET", "/api/gpos/{guid}/plan.ps1"): (
        lambda c: (f"/api/gpos/{_gpo(c)}/plan.ps1", None, None),
        "powershell_plan",
        _plan_plus,
    ),
    ("GET", "/api/gpos/{guid}/report.txt"): (
        lambda c: (f"/api/gpos/{_gpo(c)}/report.txt", None, None),
        "policy_report",
        lambda _o: lambda *a, **k: "Name: x\u2028Status: ready\n",
    ),
    ("GET", "/api/gpos/{guid}/gpmc-backup"): (
        lambda c: (f"/api/gpos/{_gpo(c)}/gpmc-backup", None, None),
        "gpmc_backup_bundle",
        lambda _o: lambda *a, **k: _zip({"manifest.xml": b"<a\u2028/>".decode().encode()}),
    ),
    ("POST", "/api/gpos/{guid}/gpmc-backup-with-scripts"): (
        lambda c: (f"/api/gpos/{_empty_gpo(c)}/gpmc-backup-with-scripts", _scripts_request(), None),
        "gpmc_backup_bundle",
        lambda _o: lambda *a, **k: _zip({"evil.exe": b"MZ"}),
    ),
    ("POST", "/api/gpos/{guid}/gpmc-backup-with-scripts/preview"): (
        lambda c: (
            f"/api/gpos/{_empty_gpo(c)}/gpmc-backup-with-scripts/preview",
            _scripts_request(),
            None,
        ),
        "gpmc_backup_bundle",
        lambda o: lambda *a, **k: _with_script_line(o(*a, **k)),
    ),
    ("GET", "/api/discovery/forest/script"): (
        lambda c: ("/api/discovery/forest/script", None, None),
        "discovery_script_forest",
        _plan_plus,
    ),
    ("GET", "/api/discovery/domain/script"): (
        lambda c: ("/api/discovery/domain/script", None, {"domain": _OLD_QUOTE}),
        "discovery_script_domain",
        lambda _o: _old_domain_script,
    ),
    ("GET", "/api/discovery/sites/script"): (
        lambda c: ("/api/discovery/sites/script", None, None),
        "discovery_script_sites",
        _plan_plus,
    ),
    ("GET", "/api/discovery/ous/script"): (
        lambda c: ("/api/discovery/ous/script", None, {"domain": "lab.test"}),
        "discovery_script_ous",
        _plan_plus,
    ),
    ("GET", "/api/discovery/gpos/script"): (
        lambda c: ("/api/discovery/gpos/script", None, None),
        "discovery_script_gpos",
        _plan_plus,
    ),
    ("GET", "/api/discovery/principals/script"): (
        lambda c: ("/api/discovery/principals/script", None, {"q": "x"}),
        "discovery_script_principal_search",
        _plan_plus,
    ),
    ("POST", "/api/security-template/policy-families"): (
        lambda c: ("/api/security-template/policy-families", {}, None),
        "format_security_template",
        lambda o: lambda *a, **k: o(*a, **k) + "\n[Privilege Rights]\nSeDebugPrivilege = *S-1-5-18",
    ),
    ("POST", "/api/security-template/object-security"): (
        lambda c: (
            "/api/security-template/object-security",
            {
                "services": [
                    {
                        "service_name": "Spooler",
                        "startup_mode": "automatic",
                        "raw_sddl": "D:(A;;CC;;;SY)",
                    }
                ]
            },
            None,
        ),
        "format_security_template",
        lambda o: lambda *a, **k: o(*a, **k) + '\n"Injected",2,"D:(A;;GA;;;WD)"',
    ),
    ("POST", "/api/network-security/firewall/render"): (
        lambda c: ("/api/network-security/firewall/render", _firewall_request(), None),
        "to_registry_settings",
        _hostile_settings,
    ),
}


def _with_script_line(bundle: bytes) -> bytes:
    """The real bundle, with a second command smuggled into scripts.ini."""
    source = zipfile.ZipFile(io.BytesIO(bundle))
    entries = {name: source.read(name) for name in source.namelist()}
    for name in entries:
        if name.endswith("scripts.ini"):
            text = entries[name][2:].decode("utf-16-le") + "1CmdLine=evil.cmd\u2028\r\n"
            entries[name] = b"\xff\xfe" + text.encode("utf-16-le")
    return _zip(entries)


def test_every_artifact_route_has_a_hostile_probe() -> None:
    assert set(_HOSTILE) == set(ROUTE_ARTIFACTS)


@pytest.mark.parametrize("route", sorted(_HOSTILE), ids=lambda r: f"{r[0]} {r[1]}")
def test_an_artifact_route_refuses_what_its_writer_should_not_have_written(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, route: tuple[str, str]
) -> None:
    build, attribute, hostile = _HOSTILE[route]
    path, body, params = build(client)
    # The route answers before the writer is replaced: the probe is live.
    method = route[0].lower()
    honest = getattr(client, method)(
        path, **({"json": body} if body is not None else {}), params=params
    )
    assert honest.status_code == 200, honest.text
    monkeypatch.setattr(api_module, attribute, hostile(getattr(api_module, attribute)))
    refused = getattr(client, method)(
        path, **({"json": body} if body is not None else {}), params=params
    )
    assert refused.status_code == 422, refused.text[:500]
    assert refused.json()["error"]["code"] == "unwritable_artifact_value"


# --- the validators themselves -------------------------------------------------------


@pytest.mark.parametrize("quote", ["'", "\u2018", "\u2019", "\u201a", "\u201b"])
def test_a_value_out_of_its_literal_changes_the_plan_shape(quote: str) -> None:
    from gpo_studio.export import powershell_plan
    from gpo_studio.model import GPO

    plan = powershell_plan(GPO(guid="{0B0D0080-0000-4000-8000-000000000000}", name="P"))
    assert egress.powershell_plan_problems(plan) == []
    broken = plan.replace("-Name 'P'", f"-Name 'P{quote}; Remove-GPO -All; {quote}'", 1)
    assert broken != plan
    doubled = plan.replace(
        "-Name 'P'", f"-Name 'P{quote}{quote}; Remove-GPO -All; {quote}{quote}'", 1
    )
    assert egress.powershell_plan_problems(broken), broken
    assert egress.powershell_plan_problems(doubled) == []


def test_an_unclosed_literal_is_refused() -> None:
    skeleton, problems = egress.ps_skeleton("$a = 'open\n'")
    assert problems and len(skeleton) == 2


def test_the_old_discovery_quoting_is_refused_at_egress() -> None:
    assert egress.discovery_script_problems(_old_domain_script(_OLD_QUOTE), "domain")
    from gpo_studio.ad_discovery import discovery_script_domain

    assert egress.discovery_script_problems(discovery_script_domain(_OLD_QUOTE), "domain") == []


@pytest.mark.parametrize(
    "name",
    ["../x.xml", "/abs.xml", "a/./b.xml", "Machine/Registry.pol\u2028", "Machine\\Registry.pol"],
)
def test_an_unsafe_member_name_is_refused(name: str) -> None:
    assert egress.zip_problems(_zip({name: b""}), "studio")


def test_a_registry_pol_that_does_not_round_trip_is_refused() -> None:
    from gpo_studio.model import RegistrySetting
    from gpo_studio.registry_pol import serialize

    data = serialize([RegistrySetting("s", "computer", "HKLM", "Software\\X", "V", "REG_SZ", "x")])
    assert egress.registry_pol_egress_problems("x", data) == []
    assert egress.registry_pol_egress_problems("x", data + b"\x00\x00")


def test_inf_base64_must_encode_inf_text() -> None:
    text = '[Unicode]\nUnicode = yes\n\n[Version]\nsignature = "$CHICAGO$"\nRevision = 1\n'
    other = base64.b64encode(b"\xff\xfe").decode()
    assert egress.inf_text_egress_problems(text, other)


def test_a_check_failure_is_an_emit_error() -> None:
    with pytest.raises(EmitError):
        egress.check("report-text", type("R", (), {"body": "a\u2028b".encode()})())


# --- final-r10 probes ------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "ignored‮outside\n[Unicode]\nUnicode=yes\n",
        "ignored\x00outside\n[Unicode]\nUnicode=yes\n",
        "; outside\x0bcomment\n[Unicode]\nUnicode=yes\n",
        "[Unicode]\nUnicode=yes\x0b\n",
        "\x0c[Unicode]\x0c\nUnicode=yes\n",
    ],
    ids=["preamble-rlo", "preamble-nul", "outside-comment-vt", "stripped-vt", "ff-header"],
)
def test_the_lossless_inf_path_never_returns_what_it_did_not_check(text: str) -> None:
    """Sol final-r10 B3: the parser drops preamble lines and strips margins, so
    the section check never saw them, and the original text was returned."""
    from gpo_studio.emit_integrity import line_problem
    from gpo_studio.security_template import format_security_template, parse_security_template

    written = format_security_template(parse_security_template(text))
    assert written != text
    assert all(line_problem(line) is None for line in written.split("\n")), written


def test_an_inf_text_must_hold_exactly_the_declared_sections() -> None:
    text = '[Unicode]\nUnicode = yes\n\n[Version]\nsignature = "$CHICAGO$"\nRevision = 1\n'
    declared = [
        {"name": "Unicode", "entries": [["Unicode", "yes"]]},
        {"name": "Version", "entries": [["signature", '"$CHICAGO$"'], ["Revision", "1"]]},
    ]
    assert egress.inf_text_egress_problems(text, None, declared) == []
    smuggled = text + "\n[Privilege Rights]\nSeDebugPrivilege = *S-1-5-18\n"
    assert egress.inf_text_egress_problems(smuggled, None, declared)


def test_a_plan_with_astral_format_characters_passes_egress() -> None:
    from gpo_studio.export import powershell_plan
    from gpo_studio.model import GPO, RegistrySetting

    gpo = GPO(
        guid="{0B0D0080-0000-4000-8000-000000000000}",
        name="Astral",
        description="a\U000110bdb",
        settings=(
            RegistrySetting(
                "s1", "computer", "HKLM", "Software\\X", "V", "REG_SZ", "x\U000e0001\U0001f600y"
            ),
        ),
    )
    plan = powershell_plan(gpo)
    assert "[char]::ConvertFromUtf32(0x110BD)" in plan
    assert egress.powershell_plan_problems(plan) == []


@pytest.mark.parametrize("field", ["key", "value_name", "value"])
def test_registry_pol_refuses_a_lone_surrogate_cleanly(field: str) -> None:
    """DeepSeek final-r10 N3: an EmitError, not a UnicodeEncodeError."""
    from dataclasses import replace

    from gpo_studio.model import RegistrySetting
    from gpo_studio.registry_pol import serialize

    setting = RegistrySetting("s", "computer", "HKLM", "Software\\X", "V", "REG_SZ", "x")
    with pytest.raises(EmitError):
        serialize([replace(setting, **{field: "a\ud800b"})])


def test_escape_line_writes_an_astral_point_as_one_escape() -> None:
    """DeepSeek final-r10 N4: \\u110bd read as \\u110b then d."""
    from gpo_studio.emit_integrity import escape_line

    assert escape_line("a\U000110bdb") == "a\\U000110bdb"
    assert escape_line("a​b") == "a\\u200bb"


def test_report_txt_renders_a_legacy_lone_surrogate(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DeepSeek final-r10 N5: hashing it raised; the report now says why there
    is no digest and still renders, through its egress check."""
    from dataclasses import replace

    from gpo_studio.model import RegistrySetting

    guid = _empty_gpo(client)
    store = app.state.store
    original = store.get_gpo

    def legacy(requested: str) -> Any:
        gpo = original(requested)
        bad = RegistrySetting("s", "computer", "HKLM", "Software\\X", "V", "REG_SZ", "a\ud800b")
        return replace(gpo, settings=(bad,))

    monkeypatch.setattr(store, "get_gpo", legacy)
    response = client.get(f"/api/gpos/{guid}/report.txt")
    assert response.status_code == 200, response.text
    assert "stored text cannot be encoded" in response.text
    assert "\\ud800" in response.text


@pytest.mark.parametrize(
    ("registry_type", "data"),
    [
        ("REG_SZ", "x".encode("utf-16le")),
        ("REG_EXPAND_SZ", "%x%".encode("utf-16le")),
        ("REG_MULTI_SZ", "a\0b\0".encode("utf-16le")),
    ],
)
def test_string_data_without_its_terminator_is_refused(registry_type: str, data: bytes) -> None:
    """Sol final-r10 N1."""
    import struct

    from gpo_studio.registry_pol import RegistryPolError, parse

    code = {"REG_SZ": 1, "REG_EXPAND_SZ": 2, "REG_MULTI_SZ": 7}[registry_type]
    sep = ";".encode("utf-16le")
    record = (
        "[".encode("utf-16le")
        + "K\0".encode("utf-16le")
        + sep
        + "V\0".encode("utf-16le")
        + sep
        + struct.pack("<I", code)
        + sep
        + struct.pack("<I", len(data))
        + sep
        + data
        + "]".encode("utf-16le")
    )
    with pytest.raises(RegistryPolError):
        parse(b"PReg" + struct.pack("<I", 1) + record)


def test_capabilities_are_cached_by_content(monkeypatch: pytest.MonkeyPatch) -> None:
    """DeepSeek final-r10 N6: the three artifacts are built once per content."""
    from dataclasses import replace

    from gpo_studio.model import GPO

    calls: list[str] = []
    for name in ("plan_refusal", "studio_export_refusal", "native_backup_refusal"):
        original = getattr(api_module, name)

        def counted(gpo: Any, *, _name: str = name, _original: Any = original) -> Any:
            calls.append(_name)
            return _original(gpo)

        monkeypatch.setattr(api_module, name, counted)
    monkeypatch.setattr(api_module, "_REFUSAL_CACHE", api_module.OrderedDict())
    gpo = GPO(guid="{0B0D0080-0000-4000-8000-000000000000}", name="Cached", revision=3)
    api_module._gpo_payload(gpo)
    api_module._gpo_payload(gpo)
    assert len(calls) == 3
    api_module._gpo_payload(replace(gpo, description="changed"))  # same revision, new content
    assert len(calls) == 6
