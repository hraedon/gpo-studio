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
from collections.abc import Callable, Iterator
from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

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
