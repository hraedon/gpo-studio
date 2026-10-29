"""Every loader of untrusted JSON refuses malformed input with its own error (#145).

``json.loads`` raises a plain ``ValueError`` (not ``JSONDecodeError``) for an
integer literal longer than Python's int-string limit of 4,300 digits, and
``RecursionError`` for deep nesting (#140). Loaders that caught only
``JSONDecodeError`` let these escape as uncaught exceptions; request bodies
became a generic 400, or a 500 once nesting was deep enough to break the
request text check but not the parser.
"""

from __future__ import annotations

import json
import re
import tracemalloc
from collections.abc import Callable, Iterator
from contextlib import closing
from pathlib import Path
from typing import get_args

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import BaseModel

from gpo_studio import api as api_module
from gpo_studio import json_input
from gpo_studio.api import app
from gpo_studio.evidence import PackError, load_pack
from gpo_studio.json_input import JsonInputError, loads_untrusted
from gpo_studio.oracle_harness import RecipeError, load_recipe
from gpo_studio.provenance import ProvenanceError, load_trust_anchor, parse_signature
from gpo_studio.remediation_corpus import RemediationCorpusError, load_platform_registry
from gpo_studio.store import WorkspaceStore
from gpo_studio.wmi_catalogue import WmiCatalogueError, load_wmi_catalogue
from gpo_studio.workspace_ops import WorkspaceError, backup_workspace, restore_workspace

LONG_INT = "1" * 4301
MALFORMED = {
    "long-integer": '{"schema_version": ' + LONG_INT + "}",
    "long-negative-integer-in-a-list": '{"filters": [-' + LONG_INT + "]}",
    "deep-nesting": "[" * 200_000 + "]" * 200_000,
    "nan": '{"schema_version": NaN}',
    "overflowing-float": '{"schema_version": 1e400}',
}


@pytest.mark.parametrize("payload", MALFORMED.values(), ids=MALFORMED.keys())
def test_loads_untrusted_raises_one_error_type(payload: str) -> None:
    with pytest.raises(JsonInputError):
        loads_untrusted(payload)


def test_loads_untrusted_messages_name_the_problem() -> None:
    with pytest.raises(JsonInputError, match="an integer of 4301 digits exceeds the 4300-digit"):
        loads_untrusted(LONG_INT)
    with pytest.raises(JsonInputError, match="nested too deeply"):
        loads_untrusted("[" * 200_000 + "]" * 200_000)
    with pytest.raises(JsonInputError, match="not valid Unicode text"):
        loads_untrusted(b'{"a": "\xff"}')
    with pytest.raises(JsonInputError, match="Expecting value") as error:
        loads_untrusted('{"a": }')
    assert (error.value.reason, error.value.pos) == ("Expecting value", 6)


def test_loads_untrusted_keeps_valid_json_and_optional_extensions() -> None:
    assert loads_untrusted('{"a": [1, -2.5, "x", null]}') == {"a": [1, -2.5, "x", None]}
    assert loads_untrusted("9" * 4300) == int("9" * 4300)
    assert loads_untrusted('[NaN, 1e400]', allow_non_finite=True) == [
        pytest.approx(float("nan"), nan_ok=True), float("inf")
    ]


def test_max_depth_counts_nested_arrays_and_objects() -> None:
    assert loads_untrusted("[" * 3 + "]" * 3, max_depth=3) == [[[]]]
    assert loads_untrusted('{"a": {"b": 1}}', max_depth=2) == {"a": {"b": 1}}
    with pytest.raises(JsonInputError, match="nested more than 3 levels deep"):
        loads_untrusted("[" * 4 + "]" * 4, max_depth=3)
    with pytest.raises(JsonInputError, match="nested more than 2 levels deep"):
        loads_untrusted('{"a": [{"b": 1}]}', max_depth=2)


def _write(tmp_path: Path, payload: str) -> Path:
    path = tmp_path / "input.json"
    path.write_text(payload, encoding="utf-8")
    return path


FILE_LOADERS: dict[str, tuple[Callable[[Path], object], type[Exception], str]] = {
    "evidence-pack": (load_pack, PackError, "invalid JSON in pack"),
    "oracle-recipe": (load_recipe, RecipeError, "invalid JSON in recipe"),
    "trust-anchor": (load_trust_anchor, ProvenanceError, "cannot load trust anchor"),
    "signature-sidecar": (
        lambda path: parse_signature(path.read_bytes()),
        ProvenanceError,
        "invalid signature sidecar",
    ),
    "platform-registry": (
        load_platform_registry, RemediationCorpusError, "unreadable or invalid JSON"
    ),
    "wmi-catalogue": (load_wmi_catalogue, WmiCatalogueError, "Invalid JSON in WMI catalogue"),
}


@pytest.mark.parametrize("loader", FILE_LOADERS)
@pytest.mark.parametrize("payload", MALFORMED.values(), ids=MALFORMED.keys())
def test_file_loaders_refuse_malformed_json_with_their_own_error(
    tmp_path: Path, loader: str, payload: str
) -> None:
    load, error, message = FILE_LOADERS[loader]
    with pytest.raises(error, match=message):
        load(_write(tmp_path, payload))


def test_backup_metadata_refuses_a_long_integer(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    with closing(WorkspaceStore(source)):
        pass
    backup = tmp_path / "backup.db"
    backup_workspace(source, backup)
    meta = Path(str(backup) + ".meta.json")
    text = json.dumps({**json.loads(meta.read_text(encoding="utf-8")), "schema_version": 0})
    meta.write_text(
        text.replace('"schema_version": 0', f'"schema_version": {LONG_INT}'), encoding="utf-8"
    )
    assert LONG_INT in meta.read_text(encoding="utf-8")
    with pytest.raises(WorkspaceError, match="Cannot read backup metadata"):
        restore_workspace(backup, tmp_path / "target.db")


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    with closing(WorkspaceStore(tmp_path / "workspace.db")) as store:
        monkeypatch.setattr(app.state, "store", store, raising=False)
        monkeypatch.setattr(app.state, "owns_store", False, raising=False)
        yield TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=False)


REQUEST_BODIES = {
    # Before: a generic 400 "There was an error parsing the body".
    "long-integer": ('{"name": ' + LONG_INT + "}", "an integer of 4301 digits"),
    "long-integer-in-a-list": (
        '{"name": "x", "description": [' + LONG_INT + "]}", "an integer of 4301 digits"
    ),
    "deep-nesting": ("[" * 200_000 + "]" * 200_000, "nested too deeply"),
    "not-text": (b'{"name": "\xff"}', "not valid Unicode text"),
    # Before: a 500, from the request text check's recursion.
    "nesting-the-text-check-cannot-walk": (
        '{"name": "x", "description": ' + "[" * 2000 + "]" * 2000 + "}",
        "nested more than 100 levels deep",
    ),
}


@pytest.mark.parametrize("body", REQUEST_BODIES.values(), ids=REQUEST_BODIES.keys())
def test_request_bodies_refuse_malformed_json_as_json_invalid(
    client: TestClient, body: tuple[str | bytes, str]
) -> None:
    content, error = body
    store: WorkspaceStore = app.state.store
    before = tuple(store._connection.iterdump())
    response = client.post(
        "/api/gpos", content=content, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 422, response.text
    (issue,) = response.json()["error"]["issues"]
    assert issue["type"] == "json_invalid"
    assert error in issue["ctx"]["error"]
    assert tuple(store._connection.iterdump()) == before


def test_request_bodies_nested_within_the_limit_still_reach_validation(
    client: TestClient,
) -> None:
    content = '{"name": "x", "description": ' + "[" * 99 + "]" * 99 + "}"
    response = client.post(
        "/api/gpos", content=content, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 422, response.text
    (issue,) = response.json()["error"]["issues"]
    assert (issue["type"], issue["loc"]) == ("string_type", ["body", "description"])
    created = client.post("/api/gpos", json={"name": "Synthetic valid"})
    assert created.status_code == 201, created.text


# --- non-finite numbers cannot reach a handler (#148 review) ----------------

NON_FINITE = ["NaN", "Infinity", "-Infinity", "1e400", "-1e400"]
DISCOVERY_OU = (
    '{"kind":"gpo-studio-discovery-ous","ous":[{"name":"Synthetic",'
    '"distinguished_name":"OU=Synthetic,DC=example,DC=test",'
    '"gpo_links":[{"id":"x","target":"x","order":%s}]}]}'
)


def _mutation_routes() -> list[tuple[str, APIRoute]]:
    return [
        (method, route)
        for route in app.routes
        if isinstance(route, APIRoute) and route.dependant.body_params
        for method in sorted(route.methods & {"POST", "PUT", "PATCH", "DELETE"})
    ]


def _admits_float(annotation: object, seen: set[type] | None = None) -> bool:
    if annotation is float:
        return True
    seen = set() if seen is None else seen
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        if annotation in seen:
            return False
        seen.add(annotation)
        return any(_admits_float(f.annotation, seen) for f in annotation.model_fields.values())
    return any(_admits_float(argument, seen) for argument in get_args(annotation))


def test_every_body_either_refuses_non_finite_numbers_or_cannot_hold_them() -> None:
    """A typed body has no float field, so validation refuses NaN and Infinity.

    Any other body admits ``Any`` and is parsed with non-finite numbers refused.
    """
    untyped = set()
    for _method, route in _mutation_routes():
        annotations = [field.field_info.annotation for field in route.dependant.body_params]
        if any(api_module._admits_any(annotation) for annotation in annotations):
            untyped.add(route.path)
        else:
            assert not any(_admits_float(annotation) for annotation in annotations), route.path
    assert {path for path in untyped if path.startswith("/api/discovery/")} == {
        f"/api/discovery/{kind}"
        for kind in ("forest", "domain", "sites", "ous", "gpos", "principals")
    }


@pytest.mark.parametrize("token", NON_FINITE)
def test_discovery_import_refuses_non_finite_numbers(client: TestClient, token: str) -> None:
    # Before: int(inf) raised OverflowError (int(nan) ValueError) -> 500.
    response = client.post(
        "/api/discovery/ous",
        content=DISCOVERY_OU % token,
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422, response.text
    (issue,) = response.json()["error"]["issues"]
    assert issue["type"] == "json_invalid"
    ok = client.post(
        "/api/discovery/ous",
        content=DISCOVERY_OU % "1",
        headers={"Content-Type": "application/json"},
    )
    assert ok.status_code == 200, ok.text


@pytest.mark.parametrize("token", NON_FINITE)
def test_no_route_answers_non_finite_numbers_with_a_server_error(
    client: TestClient, token: str
) -> None:
    guid = client.post("/api/gpos", json={"name": "Synthetic sweep"}).json()["gpo"]["guid"]
    failures = []
    for method, route in _mutation_routes():
        url = re.sub(r"\{[^}]+\}", "x", route.path.replace("{guid}", guid))
        names = [
            name
            for field in route.dependant.body_params
            for name in (
                field.field_info.annotation.model_fields
                if isinstance(field.field_info.annotation, type)
                and issubclass(field.field_info.annotation, BaseModel)
                else [field.name]
            )
        ]
        bodies = [
            "{" + ",".join(f'"{name}":{token}' for name in names) + "}",
            "{" + ",".join(f'"{name}":[{token}]' for name in names) + "}",
            "{" + ",".join(f'"{name}":{{"k":{token}}}' for name in names) + "}",
            token,
            DISCOVERY_OU % token,
        ]
        for body in bodies:
            response = client.request(
                method, url, content=body, headers={"Content-Type": "application/json"}
            )
            if response.status_code >= 500:
                failures.append((method, route.path, body[:60], response.status_code))
    assert failures == []


def test_measuring_depth_does_not_copy_a_wide_array_of_scalars() -> None:
    # #148 review: every scalar was pushed as a (child, depth) tuple, so a
    # 10 MB array of zeros cost hundreds of megabytes to measure.
    wide = [0] * 1_000_000
    tracemalloc.start()
    try:
        assert not json_input._deeper_than(wide, 100)
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 1_000_000
    assert json_input._deeper_than([[0]] * 3 + [[[[0]]]], 3)


# --- wrong-typed fields in untyped bodies (#148 review round 2) -------------

_GUID = "11111111-2222-3333-4444-555555555555"
UNTYPED_BODIES: dict[str, dict[str, object]] = {
    "/api/discovery/forest": {
        "kind": "gpo-studio-discovery-forest", "name": "example.test",
        "schema_master": "dc01.example.test", "domain_naming_master": "dc01.example.test",
        "domains": ["example.test"], "global_catalogs": ["dc01.example.test"],
        "trusts": [{
            "source": "example.test", "target": "child.example.test",
            "direction": "bidirectional", "trust_type": "parent-child", "transitive": True,
        }],
    },
    "/api/discovery/domain": {
        "kind": "gpo-studio-discovery-domain", "dns_name": "example.test",
        "netbios_name": "EXAMPLE", "domain_controllers": ["dc01.example.test"],
        "pdc_emulator": "dc01.example.test", "rid_master": "dc01.example.test",
        "infrastructure_master": "dc01.example.test", "functional_level": "Windows2016Domain",
    },
    "/api/discovery/sites": {
        "kind": "gpo-studio-discovery-sites",
        "sites": [{
            "name": "Site", "description": "", "subnets": ["192.0.2.0/24"],
            "site_links": ["LINK"],
        }],
        "subnets": [{"cidr": "192.0.2.0/24", "site_name": "Site", "description": ""}],
    },
    "/api/discovery/ous": {
        "kind": "gpo-studio-discovery-ous",
        "ous": [{
            "name": "Synthetic", "distinguished_name": "OU=Synthetic,DC=example,DC=test",
            "parent_dn": "DC=example,DC=test", "description": "",
            "gpo_links": [{
                "id": _GUID, "target": "OU=Synthetic,DC=example,DC=test",
                "enabled": True, "enforced": False, "order": 1,
            }],
        }],
    },
    "/api/discovery/gpos": {
        "kind": "gpo-studio-discovery-gpos", "domain": "example.test",
        "gpos": [{"guid": _GUID, "name": "Synthetic"}],
    },
    "/api/discovery/principals": {
        "kind": "gpo-studio-discovery-principals",
        "principals": [{
            "object_guid": _GUID, "object_sid": "S-1-5-21-1-2-3-1001",
            "sid_history": ["S-1-5-21-1-2-3-2001"], "object_class": "user",
            "sam_account_name": "synthetic", "display_name": "Synthetic",
            "canonical_name": "example.test/Users/synthetic",
            "distinguished_name": "CN=synthetic,CN=Users,DC=example,DC=test",
            "domain": "example.test", "source_dc": "dc01.example.test",
            "collected_at": "2026-07-25T10:00:00Z", "resolution_state": "resolved",
        }],
    },
    "/api/estate/import": {
        "kind": "gpo-lens-estate",
        "gpos": [{
            "guid": _GUID, "display_name": "Synthetic baseline", "description": "",
            "domain": "example.test", "computer_enabled": True, "user_enabled": True,
            "settings": [{
                "id": "s1", "side": "computer", "hive": "HKLM",
                "key": r"Software\Policies\Synthetic", "value_name": "Enabled",
                "registry_type": "REG_DWORD", "value": 1, "action": "set", "comment": "",
            }],
            "links": [{
                "id": "l1", "target": "OU=Synthetic,DC=example,DC=test",
                "enabled": True, "enforced": False, "order": 1,
            }],
            "security_filters": [],
            "wmi_filter": None,
        }],
    },
}
WRONG_TYPES = ["x", None, [], {}, True, 10**30, -1, 0, 1.5]


def _paths(value: object, path: tuple[object, ...] = ()) -> Iterator[tuple[object, ...]]:
    yield path
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _paths(child, (*path, key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _paths(child, (*path, index))


def _replaced(value: object, path: tuple[object, ...], new: object) -> object:
    if not path:
        return new
    head, *rest = path
    copy = json.loads(json.dumps(value))
    copy[head] = _replaced(copy[head], tuple(rest), new)
    return copy


def _gpo_dict(client: TestClient) -> dict[str, object]:
    guid = client.post("/api/gpos", json={"name": "Synthetic diff"}).json()["gpo"]["guid"]
    return client.get(f"/api/gpos/{guid}").json()["gpo"]


def test_untyped_bodies_answer_every_wrong_typed_field_without_a_server_error(
    client: TestClient,
) -> None:
    gpo = _gpo_dict(client)
    bodies = {**UNTYPED_BODIES, "/api/diff": {"baseline": gpo, "draft": gpo, "observed": gpo}}
    assert set(bodies) == {
        route.path for _method, route in _mutation_routes()
        if any(
            api_module._admits_any(field.field_info.annotation)
            for field in route.dependant.body_params
        )
    }
    failures = []
    for path, body in bodies.items():
        valid = client.post(path, json=body)
        assert valid.status_code < 300, (path, valid.text)
        for where in _paths(body):
            if where == ("kind",):
                continue
            for wrong in WRONG_TYPES:
                response = client.post(path, json=_replaced(body, where, wrong))
                if response.status_code >= 500:
                    failures.append((path, where, wrong, response.status_code))
    assert failures == []


@pytest.mark.parametrize("order", ["Infinity", "NaN", "7", None, [], {}, True, 0, -1, 1.5, 2**31])
def test_discovery_link_order_must_be_a_positive_integer(client: TestClient, order: object) -> None:
    # Sol r2: int() on these raised inside the handler (500) or coerced them.
    where = ("ous", 0, "gpo_links", 0, "order")
    body = _replaced(UNTYPED_BODIES["/api/discovery/ous"], where, order)
    response = client.post("/api/discovery/ous", json=body)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["issues"][0]["code"] == "invalid_gpo_link_order"


@pytest.mark.parametrize("value", ["false", 0, 1, None, []])
def test_discovery_booleans_must_be_json_booleans(client: TestClient, value: object) -> None:
    # bool("false") is True: a string read as a boolean flipped its meaning.
    where = ("ous", 0, "gpo_links", 0, "enabled")
    body = _replaced(UNTYPED_BODIES["/api/discovery/ous"], where, value)
    response = client.post("/api/discovery/ous", json=body)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["issues"][0]["code"] == "invalid_boolean_field"
