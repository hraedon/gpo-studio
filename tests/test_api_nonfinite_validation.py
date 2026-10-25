"""Invalid numeric JSON values remain field errors without workspace writes."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gpo_studio.api import JSONResponse, _json_safe, _sanitize_validation_issues, app
from gpo_studio.store import WorkspaceStore


@pytest.fixture
def workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[TestClient, WorkspaceStore]]:
    with closing(WorkspaceStore(tmp_path / "workspace.db")) as store:
        monkeypatch.setattr(app.state, "store", store, raising=False)
        monkeypatch.setattr(app.state, "owns_store", False, raising=False)
        client = TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=False)
        yield client, store


def _forbid_nonfinite_token(token: str) -> None:
    raise AssertionError(f"Error response contains a non-JSON numeric token: {token}")


@pytest.mark.parametrize("field", ["name", "description", "actor"])
@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("NaN", "NaN"),
        ("Infinity", "Infinity"),
        ("-Infinity", "-Infinity"),
        ("1e400", "Infinity"),
        ("-1e400", "-Infinity"),
    ],
)
def test_nonfinite_field_error_is_renderable_and_retry_works(
    workspace: tuple[TestClient, WorkspaceStore], field: str, token: str, expected: str
) -> None:
    client, store = workspace
    before = tuple(store._connection.iterdump())
    prefix = "" if field == "name" else '"name":"Synthetic rejected draft",'
    raw = "{" + prefix + json.dumps(field) + ":" + token + "}"

    response = client.post(
        "/api/gpos", content=raw, headers={"Content-Type": "application/json"}
    )

    assert tuple(store._connection.iterdump()) == before
    assert not store._connection.in_transaction
    assert response.status_code == 422, response.text
    body = json.loads(response.content, parse_constant=_forbid_nonfinite_token)
    issues = body["error"]["issues"]
    assert any(
        issue["loc"] == ["body", field] and issue["input"] == expected for issue in issues
    )

    retry = client.post("/api/gpos", json={"name": "Synthetic valid retry"})
    assert retry.status_code == 201, retry.text
    assert retry.json()["gpo"]["revision"] == 1


@pytest.mark.parametrize(
    ("value", "expected"),
    [(float("nan"), "NaN"), (float("inf"), "Infinity"), (-float("inf"), "-Infinity")],
)
def test_json_safe_preserves_nested_issue_shape_and_finite_values(
    value: float, expected: str
) -> None:
    original = {
        "loc": ("body", "name"),
        "input": {"nested": [value, 1.25, -7, "NaN", None]},
        "ctx": {"limit": value},
    }

    safe = _json_safe(original)

    assert safe == {
        "loc": ["body", "name"],
        "input": {"nested": [expected, 1.25, -7, "NaN", None]},
        "ctx": {"limit": expected},
    }
    assert json.loads(JSONResponse(safe).body, parse_constant=_forbid_nonfinite_token) == safe


def test_sensitive_inputs_are_removed_before_numeric_normalization() -> None:
    marker = "SYNTHETIC-UNECHOED-INPUT"
    issues = [
        {
            "type": "extra_forbidden",
            "loc": ("body", "extra"),
            "input": {"secret": marker, "number": float("nan")},
        },
        {
            "type": "model_type",
            "loc": ("body", "registry"),
            "input": {"raw_xml": marker, "number": float("inf")},
        },
    ]

    safe = _sanitize_validation_issues(issues)

    assert all("input" not in issue for issue in safe)
    assert marker not in JSONResponse(safe).body.decode()
