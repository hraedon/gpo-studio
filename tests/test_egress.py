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
from hypothesis import given, settings
from hypothesis import strategies as st
from starlette.routing import BaseRoute, Mount

from gpo_studio import api as api_module
from gpo_studio import egress
from gpo_studio.api import NO_ARTIFACT_ROUTES, ROUTE_ARTIFACTS, app
from gpo_studio.emit_integrity import EmitError
from gpo_studio.store import WorkspaceStore


def _walk(routes: list[BaseRoute], prefix: str = "") -> Iterator[tuple[str, BaseRoute]]:
    """Every route, mounted ones included, with its full path (Sol final-r11 B2):
    a mounted application's routes are not in ``app.routes``."""
    for route in routes:
        path = prefix + getattr(route, "path", "")
        if isinstance(route, Mount):
            if _is_shipped_static(route):
                yield path, route  # a terminal, after its app and directory are checked
                continue
            yield from _walk(list(route.routes), path)
            if not route.routes:
                yield path, route  # an opaque ASGI app: must be declared itself
        else:
            yield path, route


def _is_shipped_static(route: Mount) -> bool:
    """The `/assets` mount: Studio's own static-files app over its package directory."""
    from gpo_studio.api import STATIC, StudioStaticFiles

    app_under = route.app
    return (
        type(app_under) is StudioStaticFiles
        and Path(str(app_under.directory)).resolve() == Path(STATIC).resolve()
    )


def route_problems(
    routes: list[BaseRoute],
    artifacts: Mapping[tuple[str, str], str],
    no_artifact: Mapping[tuple[str, str], str],
) -> list[str]:
    """Every route not declared exactly once, or declared but not wrapped."""
    problems: list[str] = []
    seen: set[tuple[str, str]] = set()
    for path, route in _walk(routes):
        if isinstance(route, APIRoute):
            for method in sorted(route.methods):
                key = (method, path)
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
            key = ("*", path)
            seen.add(key)
            if key not in no_artifact:
                problems.append(f"{key} is not declared")
            elif isinstance(route, Mount) and not _is_shipped_static(route):
                problems.append(f"{key} is a mount that is not Studio's static files")
    stale = (set(artifacts) | set(no_artifact)) - seen
    problems += [f"{key} is declared but no such route exists" for key in sorted(stale)]
    return problems


def test_every_route_is_declared() -> None:
    assert route_problems(list(app.routes), ROUTE_ARTIFACTS, NO_ARTIFACT_ROUTES) == []
    assert len(ROUTE_ARTIFACTS) == 15 and len(NO_ARTIFACT_ROUTES) == 103


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
        "export.export_bundle",
        lambda o: lambda *a, **k: _with_duplicate_plan(o(*a, **k)),
    ),
    ("GET", "/api/gpos/{guid}/plan.ps1"): (
        lambda c: (f"/api/gpos/{_gpo(c)}/plan.ps1", None, None),
        "export.powershell_plan",
        _plan_plus,
    ),
    ("GET", "/api/gpos/{guid}/report.txt"): (
        lambda c: (f"/api/gpos/{_gpo(c)}/report.txt", None, None),
        "policy_report",
        lambda _o: lambda *a, **k: "Name: x\u2028Status: ready\n",
    ),
    ("GET", "/api/gpos/{guid}/gpmc-backup"): (
        lambda c: (f"/api/gpos/{_gpo(c)}/gpmc-backup", None, None),
        "export.gpmc_backup_bundle",
        lambda o: lambda *a, **k: _with_separator_in_manifest(o(*a, **k)),
    ),
    ("POST", "/api/gpos/{guid}/gpmc-backup-with-scripts"): (
        lambda c: (f"/api/gpos/{_empty_gpo(c)}/gpmc-backup-with-scripts", _scripts_request(), None),
        "export.gpmc_backup_bundle",
        lambda o: lambda *a, **k: _with_member(o(*a, **k), "evil.exe", b"MZ"),
    ),
    ("POST", "/api/gpos/{guid}/gpmc-backup-with-scripts/preview"): (
        lambda c: (
            f"/api/gpos/{_empty_gpo(c)}/gpmc-backup-with-scripts/preview",
            _scripts_request(),
            None,
        ),
        "export.gpmc_backup_bundle",
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


def _entries(bundle: bytes) -> list[tuple[str, bytes]]:
    with zipfile.ZipFile(io.BytesIO(bundle)) as source:
        return [(info.filename, source.read(info)) for info in source.infolist()]


def _rezip(entries: list[tuple[str, bytes]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in entries:
            archive.writestr(name, data)
    return buffer.getvalue()


def _with_duplicate_plan(bundle: bytes) -> bytes:
    """Sol final-r11 B3: a hostile apply.ps1 first, the real one last --
    ``read(name)`` sees only the last."""
    import warnings

    entries = _entries(bundle)
    real = next(data for name, data in entries if name == "apply.ps1")
    hostile = real.decode("utf-8-sig") + _BROKEN_OUT
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)  # zipfile: duplicate name
        return _rezip([("apply.ps1", hostile.encode("utf-8-sig")), *entries])


def _with_separator_in_manifest(bundle: bytes) -> bytes:
    """DeepSeek final-r11 N1: a real U+2028, not the bytes ``\\u2028``."""
    return _rezip(
        [
            (name, data.replace(b"<GPODisplayName>", "<GPODisplayName>\u2028".encode()))
            if name == "manifest.xml"
            else (name, data)
            for name, data in _entries(bundle)
        ]
    )


def _with_member(bundle: bytes, name: str, data: bytes) -> bytes:
    return _rezip([*_entries(bundle), (name, data)])


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
    from gpo_studio import export as export_module

    owner: Any = api_module
    if attribute.startswith("export."):
        owner, attribute = export_module, attribute.split(".", 1)[1]
    path, body, params = build(client)
    # The route answers before the writer is replaced: the probe is live.
    method = route[0].lower()
    honest = getattr(client, method)(
        path, **({"json": body} if body is not None else {}), params=params
    )
    assert honest.status_code == 200, honest.text
    monkeypatch.setattr(owner, attribute, hostile(getattr(owner, attribute)))
    refused = getattr(client, method)(
        path, **({"json": body} if body is not None else {}), params=params
    )
    assert refused.status_code == 422, refused.text[:500]
    error = refused.json()["error"]
    # The scripts routes ask the capability's own refusal first, which reports
    # the same egress refusal as an issue.
    codes = {error.get("code"), *(issue["code"] for issue in error.get("issues", []))}
    assert "unwritable_artifact_value" in codes, error


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
    for name in ("plan_refusal", "studio_export_refusal", "gpmc_backup_refusal"):
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


# ===========================================================================
# Final-r11
# ===========================================================================


def test_a_mounted_application_route_is_enumerated() -> None:
    """Sol final-r11 B2: a sub-application mounted on the app has routes that
    ``app.routes`` does not list; they are walked and must be declared."""
    planted = FastAPI()
    for route in app.routes:
        planted.router.routes.append(route)
    sub = FastAPI()

    @sub.get("/plan.ps1")
    def hostile() -> str:  # pragma: no cover - never called
        return "Remove-Item -Recurse C:\\"

    planted.mount("/api/mounted", sub)
    problems = route_problems(list(planted.routes), ROUTE_ARTIFACTS, NO_ARTIFACT_ROUTES)
    assert any("/api/mounted/plan.ps1" in p for p in problems), problems


def test_a_static_mount_must_be_studios_own() -> None:
    from fastapi.staticfiles import StaticFiles

    planted = FastAPI()
    for route in app.routes:
        if getattr(route, "path", None) != "/assets":
            planted.router.routes.append(route)
    planted.mount("/assets", StaticFiles(directory=str(Path(__file__).parent)), name="assets")
    problems = route_problems(list(planted.routes), ROUTE_ARTIFACTS, NO_ARTIFACT_ROUTES)
    assert any("not Studio's static files" in p for p in problems), problems


def _real(kind: str) -> bytes:
    from gpo_studio.export import export_bundle, gpmc_backup_bundle
    from gpo_studio.model import GPO, RegistrySetting

    gpo = GPO(
        guid="{0B0D0080-0000-4000-8000-000000000000}",
        name="Zip",
        settings=(RegistrySetting("s", "computer", "HKLM", "Software\\X", "V", "REG_SZ", "x"),),
    )
    return export_bundle(gpo) if kind == "studio" else gpmc_backup_bundle(gpo)


@pytest.mark.parametrize("kind", ["studio", "gpmc"])
def test_a_real_export_passes_and_an_empty_one_does_not(kind: str) -> None:
    assert egress.zip_problems(_real(kind), kind) == []  # type: ignore[arg-type]
    assert egress.zip_problems(_rezip([]), kind)  # type: ignore[arg-type]


@pytest.mark.parametrize("kind", ["studio", "gpmc"])
def test_every_required_member_is_required(kind: str) -> None:
    entries = _entries(_real(kind))
    required = egress._STUDIO_REQUIRED if kind == "studio" else egress._GPMC_REQUIRED
    for pattern in required:
        partial = [(n, d) for n, d in entries if not pattern.fullmatch(n)]
        assert len(partial) < len(entries)
        assert egress.zip_problems(_rezip(partial), kind), pattern.pattern  # type: ignore[arg-type]


def test_a_duplicate_member_hiding_a_hostile_one_is_refused() -> None:
    """Sol final-r11 B3."""
    hostile = _with_duplicate_plan(_real("studio"))
    with zipfile.ZipFile(io.BytesIO(hostile)) as archive:
        first, *_rest = [i for i in archive.infolist() if i.filename == "apply.ps1"]
        assert _BROKEN_OUT.strip() in archive.read(first).decode("utf-8-sig")
    problems = egress.zip_problems(hostile, "studio")
    assert any("more than once" in p.problem for p in problems), problems
    assert any("not a statement" in p.problem for p in problems), problems


# --- the plan grammar is complete for the writer, by test ----------------------------


def _writer_shapes(gpos: Iterator[Any]) -> set[str]:
    from gpo_studio.export import powershell_plan

    shapes: set[str] = set()
    for gpo in gpos:
        skeleton, problems = egress.ps_skeleton(powershell_plan(gpo))
        assert not problems, problems
        shapes.update(skeleton)
    return shapes


def test_the_plan_grammar_is_exactly_the_writers_statements() -> None:
    """DeepSeek final-r11 B1: every line the writer produces over the exhaustive
    generator is in the grammar, and every grammar line is produced."""
    from plan_corpus import gpos

    shapes = _writer_shapes(gpos())
    assert shapes - egress.PLAN_GRAMMAR == set()
    assert egress.PLAN_GRAMMAR - shapes == set()


def test_every_generated_plan_passes_egress() -> None:
    from plan_corpus import gpos

    from gpo_studio.export import powershell_plan

    for gpo in gpos():
        assert egress.powershell_plan_problems(powershell_plan(gpo)) == [], gpo.name


def test_the_two_shapes_the_reference_missed_pass() -> None:
    """``$applySids = @()`` (once ``$desiredApply = @()``: read-only filtering)
    and ``-Value @()`` (an empty REG_MULTI_SZ): valid output the round-10
    reference refused."""
    from gpo_studio.export import powershell_plan
    from gpo_studio.model import GPO, RegistrySetting, SecurityFilter

    gpo = GPO(
        guid="{0B0D0080-0000-4000-8000-000000000000}",
        name="Shapes",
        settings=(
            RegistrySetting("s", "computer", "HKLM", "Software\\X", "M", "REG_MULTI_SZ", []),
        ),
        security_filters=(SecurityFilter("f", "LAB\\Readers", permission="read"),),
    )
    plan = powershell_plan(gpo)
    assert "$applySids = @()" in plan and "-Value @()" in plan
    assert egress.powershell_plan_problems(plan) == []


_TEXT = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters=("\x00", "\r")),
    max_size=12,
)
_NAME = st.text(
    alphabet=st.characters(blacklist_categories=("Cc", "Cf", "Zl", "Zp", "Cs")),
    min_size=1,
    max_size=12,
).map(lambda v: v.strip(" ") or "n")


@st.composite
def _random_gpo(draw: st.DrawFn) -> Any:
    from gpo_studio.model import GPO, GPOLink, RegistrySetting, SecurityFilter, WmiFilter

    settings = []
    for index in range(draw(st.integers(0, 6))):
        kind = draw(
            st.sampled_from(
                ["REG_SZ", "REG_EXPAND_SZ", "REG_MULTI_SZ", "REG_DWORD", "REG_QWORD", "REG_BINARY"]
            )
        )
        value: Any = {
            "REG_SZ": _TEXT,
            "REG_EXPAND_SZ": _TEXT,
            "REG_MULTI_SZ": st.lists(_TEXT, max_size=3),
            "REG_DWORD": st.integers(0, 0xFFFFFFFF),
            "REG_QWORD": st.integers(0, 0xFFFFFFFFFFFFFFFF),
            "REG_BINARY": st.binary(max_size=4).map(lambda b: b.hex().upper()),
        }[kind]
        side = draw(st.sampled_from(["computer", "user"]))
        action = draw(st.sampled_from(["set", "set", "delete", "delete_all_values"]))
        settings.append(
            RegistrySetting(
                f"s{index}",
                side,
                "HKLM" if side == "computer" else "HKCU",
                f"Software\\{draw(_NAME)}",
                draw(_NAME),
                kind,
                draw(value),
                action,
            )
        )
    filters = tuple(
        SecurityFilter(
            f"f{i}",
            f"LAB\\{draw(_NAME).replace(' ', '')}x",
            permission=draw(st.sampled_from(["apply", "read"])),
            target_type=draw(st.sampled_from(["user", "group", "computer"])),
        )
        for i in range(draw(st.integers(0, 3)))
    )
    links = tuple(
        GPOLink(
            f"l{i}",
            "OU=Lab,DC=studio,DC=local",
            enabled=draw(st.booleans()),
            enforced=draw(st.booleans()),
            order=draw(st.integers(1, 99)),
        )
        for i in range(draw(st.integers(0, 2)))
    )
    return GPO(
        guid="{0B0D0080-0000-4000-8000-000000000000}",
        name=draw(_NAME),
        description=draw(_TEXT),
        settings=tuple(settings),
        links=links,
        security_filters=filters,
        wmi_filter=draw(st.none() | st.just(WmiFilter("w", "W", query="SELECT * FROM Win32_Bios"))),
        computer_enabled=draw(st.booleans()),
        user_enabled=draw(st.booleans()),
    )


@settings(max_examples=150, deadline=None, derandomize=True, database=None)
@given(_random_gpo())
def test_any_plan_the_writer_produces_passes_egress(gpo: Any) -> None:
    """The property: whatever valid GPO, the writer's plan is in the grammar."""
    from gpo_studio.export import powershell_plan
    from gpo_studio.model import ValidationError
    from gpo_studio.validation import validate_gpo

    if any(issue.severity == "error" for issue in validate_gpo(gpo)):
        return
    try:
        plan = powershell_plan(gpo)
    except ValidationError:
        return
    assert egress.powershell_plan_problems(plan) == []


# --- a capability and its download never disagree -----------------------------------


def _matrix() -> list[Any]:
    from dataclasses import replace

    from plan_corpus import gpos
    from writer_corpus import synthetic_collections

    from gpo_studio.model import GPO, RegistrySetting, SecurityFilter

    picked = [gpo for index, gpo in enumerate(gpos()) if index % 16 == 0]
    shapes = GPO(
        guid="{0B0D0080-0000-4000-8000-000000000000}",
        name="Shapes",
        settings=(
            RegistrySetting("s", "computer", "HKLM", "Software\\X", "M", "REG_MULTI_SZ", []),
        ),
        security_filters=(SecurityFilter("f", "LAB\\Readers", permission="read"),),
    )
    maximal = replace(picked[0], gpp_collections=synthetic_collections())
    return [*picked, shapes, maximal]


@pytest.mark.parametrize("index", range(len(_matrix())))
def test_a_capability_is_its_download(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, index: int
) -> None:
    """DeepSeek final-r11 B1 and N8: through the real routes, for GPOs with
    links, filters (read-only included), every registry type (empty lists
    included), a description, a WMI filter, a disabled side and every GPP family
    -- each artifact is advertised enabled exactly when its route answers 200."""
    from dataclasses import replace

    guid = _empty_gpo(client)
    stored = app.state.store.get_gpo(guid)
    gpo = replace(_matrix()[index], guid=guid, revision=stored.revision)
    monkeypatch.setattr(app.state.store, "get_gpo", lambda requested: gpo)
    monkeypatch.setattr(api_module, "_REFUSAL_CACHE", api_module.OrderedDict())
    payload = client.get(f"/api/gpos/{guid}")
    assert payload.status_code == 200, payload.text
    capabilities = payload.json()["artifact_capabilities"]
    for name, route in (
        ("powershell_plan", "plan.ps1"),
        ("studio_export", "export.zip"),
        ("gpmc_export", "gpmc-backup"),
    ):
        status = client.get(f"/api/gpos/{guid}/{route}").status_code
        assert capabilities[name]["enabled"] == (status == 200), (name, status, capabilities[name])
    assert client.get(f"/api/gpos/{guid}/report.txt").status_code == 200


def test_the_release_fixture_gpos_export(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The released 1.0.0 workspace's GPOs, through every artifact route."""
    import shutil

    fixture = Path(__file__).parent / "fixtures" / "release-1.0.0-workspace" / "workspace.db"
    target = tmp_path / "release.db"
    shutil.copyfile(fixture, target)
    with closing(WorkspaceStore(target)) as store:
        monkeypatch.setattr(app.state, "store", store)
        rows = client.get("/api/gpos").json()["items"]
        assert rows
        for row in rows:
            for route in ("plan.ps1", "export.zip", "gpmc-backup", "report.txt"):
                response = client.get(f"/api/gpos/{row['guid']}/{route}")
                assert response.status_code == 200, (row["name"], route, response.text[:300])


# --- retained content cannot override the typed model (Sol final-r11 B1) -------------

_RUN_ONCE = (
    '<FilterRunOnce hidden="1" not="0" bool="AND" id="{11111111-2222-3333-4444-555555555555}"/>'
)


def test_the_api_refuses_a_retained_attribute_overriding_a_typed_one(client: TestClient) -> None:
    gpo = client.post("/api/gpos", json={"name": "B1", "actor": "p", "reason": "r11"}).json()["gpo"]
    response = client.post(
        f"/api/gpos/{gpo['guid']}/preferences/groups",
        json={
            "scope": "computer",
            "group": {
                "name": "Administrators",
                "sid": "S-1-5-32-544",
                "common": {"disabled": True},
                "unknown_attrs": [["disabled", "0"]],
                "ilt_filter": {"items": [_RUN_ONCE]},
            },
            "actor": "p",
            "reason": "r11",
            "expected_revision": gpo["revision"],
        },
    )
    assert response.status_code == 422, response.text
    # No request carries retained content (final-r13): the field is refused.
    assert {issue["type"] for issue in response.json()["error"]["issues"]} >= {"extra_forbidden"}


def test_a_stored_override_is_refused_by_the_writer_and_at_egress() -> None:
    """Content that reached the store some other way (a legacy record) still
    cannot ship: the writer refuses the override, and egress refuses a raw
    predicate that changes what the typed options mean."""
    from dataclasses import replace

    from gpo_studio.export import export_bundle
    from gpo_studio.gpp import GppCollection, GppCommonOptions, GppError, GppGroup, serialize_gpp
    from gpo_studio.ilt import IltFilter
    from gpo_studio.model import GPO

    group = GppGroup(
        name="Administrators", sid="S-1-5-32-544", common=GppCommonOptions(disabled=True)
    )
    with pytest.raises(GppError):
        serialize_gpp(
            GppCollection(
                scope="computer", groups=(replace(group, unknown_attrs=(("disabled", "0"),)),)
            )
        )
    run_once = _RUN_ONCE
    smuggled = replace(group, ilt_filter=IltFilter(items=(run_once,)))
    gpo = GPO(
        guid="{0B0D0080-0000-4000-8000-000000000000}",
        name="B1",
        gpp_collections=(GppCollection(scope="computer", groups=(smuggled,)),),
    )
    problems = egress.zip_problems(export_bundle(gpo), "studio", gpo)
    assert problems, "a raw FilterRunOnce the typed options did not ask for shipped"


def _typed_attribute_names() -> Iterator[Any]:
    """(family, index, element, attribute) for every attribute the typed writer
    sets on an item element or its Properties, for every GPP type."""
    import dataclasses

    from writer_corpus import synthetic_collections

    from gpo_studio.gpp import GppCollection, serialize_gpp
    from gpo_studio.xml_safety import parse_xml_bounded

    (collection, _user) = synthetic_collections()
    for field in dataclasses.fields(collection):
        items = getattr(collection, field.name)
        if not (isinstance(items, tuple) and items and dataclasses.is_dataclass(items[0])):
            continue
        item = items[0]
        if not hasattr(item, "unknown_attrs"):
            continue
        alone = GppCollection(scope="computer", **{field.name: (item,)})
        for data in serialize_gpp(alone).values():
            root = parse_xml_bounded(data, max_size=len(data), max_attr_length=len(data))
            element = next(iter(root))
            for name in element.attrib:
                yield pytest.param(field.name, "item", name, id=f"{field.name}-item-{name}")
            props = next((c for c in element if c.tag.endswith("Properties")), None)
            if props is not None and hasattr(item, "unknown_props_attrs"):
                for name in props.attrib:
                    yield pytest.param(field.name, "props", name, id=f"{field.name}-props-{name}")


@pytest.mark.parametrize(("family", "element", "name"), list(_typed_attribute_names()))
def test_no_retained_attribute_can_collide_with_a_typed_one(
    family: str, element: str, name: str
) -> None:
    """Derived from the serializer: for every GPP type, every attribute the
    typed writer sets is reserved against a retained attribute of that name."""
    from dataclasses import replace

    from writer_corpus import synthetic_collections

    from gpo_studio.gpp import GppCollection, GppError, serialize_gpp

    (collection, _user) = synthetic_collections()
    item = getattr(collection, family)[0]
    field = "unknown_attrs" if element == "item" else "unknown_props_attrs"
    colliding = replace(item, **{field: ((name, "\u2603planted"),)})
    try:
        files = serialize_gpp(GppCollection(scope="computer", **{family: (colliding,)}))
    except GppError:
        return  # refused: the typed attribute is reserved
    # Or dropped: a registry item's derived attributes are regenerated, and a
    # retained copy is discarded rather than written over them.
    assert all("\u2603planted".encode() not in data for data in files.values())


def test_an_element_outside_the_vocabulary_is_refused() -> None:
    from gpo_studio.emit_integrity import gpp_element_problems

    data = (
        b'<Groups clsid="{3125E937-EB16-4b4c-9934-544FC6D24D26}"><Group clsid="x" name="G">'
        b'<Properties action="U" groupName="G"/><Payload/></Group></Groups>'
    )
    assert any(p.name == "Payload" for p in gpp_element_problems("Groups.xml", data))


def test_the_element_vocabulary_covers_the_corpus() -> None:
    from writer_corpus import gpp_corpus

    from gpo_studio.emit_integrity import gpp_element_problems
    from gpo_studio.gpp import serialize_gpp

    for source, collection in gpp_corpus():
        for path, data in serialize_gpp(collection).items():
            assert gpp_element_problems(path, data) == [], (source, path)


def test_the_vocabulary_holds_the_sourced_microsoft_names() -> None:
    """The vocabulary's Microsoft names are sourced, not invented (DeepSeek
    final-r12 N4): every targeting element in the Plan 021 capability
    inventory, and the GPP item tags `oracle_evidence` lists."""
    import re

    from gpo_studio.emit_integrity import GPP_ELEMENTS, MICROSOFT_FILTER_ELEMENTS
    from gpo_studio.oracle_evidence import _GPP_ITEM_TAGS

    inventory = Path(__file__).resolve().parents[1] / "docs/plan-021/capability-inventory.md"
    named = set(re.findall(r"\bFilter[A-Z][A-Za-z]+", inventory.read_text(encoding="utf-8")))
    assert len(named) >= 28
    assert named == MICROSOFT_FILTER_ELEMENTS
    every = set().union(*GPP_ELEMENTS.values())
    assert named <= every
    for tag in ("LocalPrinter", "PortPrinter", "ImmediateTask"):
        assert tag in _GPP_ITEM_TAGS and tag in every, tag


def test_a_retained_element_passes_egress_only_with_its_gpo() -> None:
    """Retained structure is judged by provenance (final-r12): an element the
    GPO's imported content holds is exported as imported; the vocabulary
    judges everything else."""
    from gpo_studio.emit_integrity import gpp_element_problems

    data = (
        b'<Groups clsid="{3125E937-EB16-4b4c-9934-544FC6D24D26}"><Group clsid="x" name="G">'
        b'<Properties action="U" groupName="G"/><Payload/></Group></Groups>'
    )
    assert gpp_element_problems("Groups.xml", data, frozenset({"Payload"})) == []
    assert gpp_element_problems("Groups.xml", data, frozenset({"Other"})) != []


def test_a_continuation_line_is_never_returned_raw() -> None:
    """DeepSeek final-r11 N4: a comment ending in a backslash swallowed the next
    header, and every physical line passed."""
    from gpo_studio.security_template import format_security_template, parse_security_template

    raw = "; a preamble comment \\\n[Unicode]\nUnicode=yes\n"
    assert format_security_template(parse_security_template(raw)) != raw


def test_a_row_key_may_hold_an_equals_sign(client: TestClient) -> None:
    """DeepSeek final-r11 N5: '=' is legal in a registry key name."""
    response = client.post(
        "/api/security-template/object-security",
        json={
            "registry_keys": [{"key_path": "MACHINE\\Software\\A=B", "raw_sddl": "D:(A;;GA;;;BA)"}]
        },
    )
    assert response.status_code == 200, response.text
    assert '"MACHINE\\Software\\A=B",' in response.json()["inf_text"]
