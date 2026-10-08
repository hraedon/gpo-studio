"""Text XML cannot carry never reaches storage, and stored text never breaks a read.

Batch-2 review: a REG_MULTI_SZ element holding an escaped lone surrogate
committed a revision and then made that GPO, its backup and the WHOLE workspace
list return 500; NUL, VT, FF and U+FFFF were accepted and exported as XML no
parser accepts. The fix is general, not per field:

* one predicate (`xml_safety.xml_text_problem`) and one walk over every string
  in the model (`xml_safety.unwritable_text`);
* the store refuses to write any revision that fails it, whatever endpoint the
  text arrived through, and the API refuses such a JSON body up front;
* reads render any stored legacy text (JSON escapes), report it, and every
  exporter refuses it with a code.

The property test throws arbitrary Unicode -- surrogates and controls
included -- at string fields across the API's GPP families and the GPO's other
text, and holds: every mutation is 201/200 or 422, the workspace list, the GPO
and its artifacts never 500, and an accepted value comes back byte-exact
through export and re-import.
"""

from __future__ import annotations

import io
import json
import zipfile
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from gpo_studio.api import app
from gpo_studio.backup import read_backup
from gpo_studio.import_export import collect_gpp_collections, extract_side_settings
from gpo_studio.store import WorkspaceStore
from gpo_studio.xml_safety import unwritable_text, xml_text_problem

HOSTILE = ["\ud800", "\udfff", "\x00", "\x0b", "\x0c", "\r", "\t", "\n", "￾", "￿"]
TEXT = st.text(
    alphabet=st.one_of(st.characters(), st.sampled_from(HOSTILE)), min_size=1, max_size=12
)


# ---------------------------------------------------------------------------
# The predicate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["\ud800", "\udfff", "\x00", "\x0b", "\x0c", "￾", "￿"])
def test_the_predicate_refuses_what_xml_1_0_forbids(bad: str) -> None:
    assert xml_text_problem(f"one{bad}two") is not None


@pytest.mark.parametrize("good", ["\t", "\n", "é", "\U0001f600", ""])
def test_the_predicate_allows_xml_characters(good: str) -> None:
    assert xml_text_problem(f"one{good}two") is None


def test_carriage_return_is_allowed_only_on_request() -> None:
    assert xml_text_problem("one\rtwo") is not None
    assert xml_text_problem("one\rtwo", allow_cr=True) is None


def test_carriage_return_survives_only_where_text_never_becomes_xml() -> None:
    """XML reads CR back as LF, in element text and in attribute values alike."""
    tree = {
        "name": "a\rb",
        "description": "a\rb",
        "gpp_collections": [{"x": "a\rb"}],
        "settings": [{"value": "a\rb", "comment": "a\rb", "value_list": ["a\rb"]},
                     {"value": ["a\rb"]}],
        "fdeploy": {"text": "a\r\nb"},
    }
    assert sorted(path for path, _ in unwritable_text(tree)) == [
        "description",
        "gpp_collections/0/x",
        "name",
        "settings/0/comment",
        "settings/0/value_list/0",
    ]


@pytest.mark.parametrize("field", ["name", "description"])
def test_a_carriage_return_in_gpo_metadata_is_refused(client: TestClient, field: str) -> None:
    """It would come back as LF from Backup.xml/bkupInfo.xml/manifest.xml (review)."""
    body = {"name": "metadata", "description": ""}
    body[field] = "line one\rline two"
    created = _send(client, "POST", "/api/gpos", body)
    assert created.status_code == 422, created.text
    assert "text_not_xml_writable" in created.text
    gpo = _new_gpo(client, f"cr {field}")
    patched = _send(client, "PATCH", f"/api/gpos/{gpo['guid']}", {
        **_audit(gpo), "name": gpo["name"], "description": "",
        field: "line one\r\nline two",
    })
    assert patched.status_code == 422, patched.text
    assert client.get(f"/api/gpos/{gpo['guid']}").json()["gpo"]["revision"] == gpo["revision"]


def test_a_line_feed_in_the_description_round_trips(client: TestClient) -> None:
    created = _send(client, "POST", "/api/gpos", {"name": "lf", "description": "one\ntwo"})
    assert created.status_code == 201, created.text
    assert created.json()["gpo"]["description"] == "one\ntwo"


# ---------------------------------------------------------------------------
# The API and the store
# ---------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    store = WorkspaceStore(tmp_path / "text.db")
    app.state.store = store
    app.state.owns_store = False
    with TestClient(app) as test_client:
        yield test_client
    store.close()


def _send(client: TestClient, method: str, url: str, body: dict[str, Any]) -> Any:
    """JSON with ASCII escapes, so a lone surrogate reaches the server as \\udXXX."""
    return client.request(
        method, url, content=json.dumps(body).encode("ascii"),
        headers={"content-type": "application/json"},
    )


def _new_gpo(client: TestClient, name: str) -> dict[str, Any]:
    response = _send(client, "POST", "/api/gpos", {"name": name})
    assert response.status_code == 201, response.text
    return response.json()["gpo"]  # type: ignore[no-any-return]


def _audit(gpo: dict[str, Any]) -> dict[str, Any]:
    return {"expected_revision": gpo["revision"], "actor": "tester", "reason": "text"}


def test_the_reviewers_surrogate_is_refused_before_commit(client: TestClient) -> None:
    gpo = _new_gpo(client, "surrogate")
    response = _send(client, "POST", f"/api/gpos/{gpo['guid']}/preferences/registry", {
        **_audit(gpo),
        "scope": "computer",
        "registry": {"key": "Software\\S", "value": {
            "name": "M", "value": ["one\ud800two"], "registry_type": "REG_MULTI_SZ",
        }},
    })
    assert response.status_code == 422
    assert "text_not_xml_writable" in response.text
    after = client.get(f"/api/gpos/{gpo['guid']}")
    assert after.status_code == 200
    assert after.json()["gpo"]["revision"] == gpo["revision"]
    assert client.get("/api/gpos").status_code == 200


@pytest.mark.parametrize("bad", ["\x00", "\x0b", "\x0c", "￿", "\r"])
def test_controls_in_a_multi_string_are_refused(client: TestClient, bad: str) -> None:
    gpo = _new_gpo(client, f"control {ord(bad)}")
    response = _send(client, "POST", f"/api/gpos/{gpo['guid']}/preferences/registry", {
        **_audit(gpo),
        "scope": "computer",
        "registry": {"key": "Software\\S", "value": {
            "name": "M", "value": [f"one{bad}two"], "registry_type": "REG_MULTI_SZ",
        }},
    })
    assert response.status_code == 422, response.text


def test_the_legitimate_multi_string_control_still_works(client: TestClient) -> None:
    gpo = _new_gpo(client, "control")
    response = _send(client, "POST", f"/api/gpos/{gpo['guid']}/preferences/registry", {
        **_audit(gpo),
        "scope": "computer",
        "registry": {"key": "Software\\S", "value": {
            "name": "M", "value": ["one", "two"], "registry_type": "REG_MULTI_SZ",
        }},
    })
    assert response.status_code == 201, response.text
    assert client.get(f"/api/gpos/{gpo['guid']}/gpmc-backup").status_code == 200


def test_a_legacy_stored_surrogate_is_readable_reported_and_refused(
    client: TestClient,
) -> None:
    """A revision written before the gate: read and list work, exports refuse."""
    gpo = _new_gpo(client, "legacy")
    store: WorkspaceStore = app.state.store
    row = store._connection.execute(
        "SELECT snapshot_json FROM gpos WHERE guid = ?", (gpo["guid"],)
    ).fetchone()
    snapshot = json.loads(row["snapshot_json"])
    snapshot["description"] = "legacy\ud800text"
    store._connection.execute(
        "UPDATE gpos SET snapshot_json = ? WHERE guid = ?", (json.dumps(snapshot), gpo["guid"])
    )
    store._connection.commit()
    listing = client.get("/api/gpos")
    assert listing.status_code == 200
    read = client.get(f"/api/gpos/{gpo['guid']}")
    assert read.status_code == 200
    body = read.json()
    assert body["gpo"]["description"] == "legacy\ud800text"
    assert any(v["code"] == "text_not_xml_writable" for v in body["validation"])
    assert body["artifact_capabilities"]["gpmc_export"]["enabled"] is False
    for artifact in ("gpmc-backup", "export.zip", "plan.ps1", "report.txt"):
        produced = client.get(f"/api/gpos/{gpo['guid']}/{artifact}")
        assert produced.status_code in (200, 422), (artifact, produced.text)
    backup = client.get(f"/api/gpos/{gpo['guid']}/gpmc-backup")
    assert backup.status_code == 422
    assert "text_not_xml_writable" in backup.text
    # And the fix is an ordinary edit: clearing the field writes a clean revision.
    fixed = _send(client, "PATCH", f"/api/gpos/{gpo['guid']}", {
        "expected_revision": gpo["revision"], "actor": "tester", "reason": "fix",
        "name": "legacy", "description": "clean",
    })
    assert fixed.status_code == 200, fixed.text


# ---------------------------------------------------------------------------
# The property
# ---------------------------------------------------------------------------


def _backup_content(client: TestClient, guid: str, root: Path) -> Path | None:
    response = client.get(f"/api/gpos/{guid}/gpmc-backup")
    assert response.status_code in (200, 422), response.text
    if response.status_code != 200:
        return None
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        archive.extractall(root)
    backup = read_backup(root)
    # A GPO with no policy content has no DomainSysvol tree to read back.
    return backup.gpos[0].content_root


Target = Callable[[str], tuple[str, str, dict[str, Any], Callable[[Path], object]]]


def _group_target(field: str) -> Target:
    def build(text: str) -> tuple[str, str, dict[str, Any], Callable[[Path], object]]:
        group: dict[str, Any] = {"name": "G", "description": ""}
        member: dict[str, Any] = {"sid": "S-1-5-32-544", "name": "M"}
        if field == "member_name":
            member["name"] = text
        else:
            group[field] = text
        group["members"] = [member]

        def read(content: Path) -> object:
            found = collect_gpp_collections(content)[0].groups[0]
            if field == "member_name":
                return found.members[0].name
            return getattr(found, field)

        return "groups", "group", group, read

    return build


def _registry_target(field: str) -> Target:
    def build(text: str) -> tuple[str, str, dict[str, Any], Callable[[Path], object]]:
        value: dict[str, Any] = {"name": "V", "value": "v", "registry_type": "REG_SZ"}
        if field == "multi":
            value.update(registry_type="REG_MULTI_SZ", value=[text, "second"])
        else:
            value[field] = text

        def read(content: Path) -> object:
            found = collect_gpp_collections(content)[0].registry[0].value
            if field == "multi":
                return found.value[0] if isinstance(found.value, list) else found.value
            return getattr(found, field)

        return "registry", "registry", {"key": "Software\\P", "value": value}, read

    return build


TARGETS: dict[str, Target] = {
    "group name": _group_target("name"),
    "group description": _group_target("description"),
    "group member name": _group_target("member_name"),
    "registry value name": _registry_target("name"),
    "registry value": _registry_target("value"),
    "registry multi-string": _registry_target("multi"),
}


@settings(
    max_examples=60,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)
@given(target=st.sampled_from(sorted(TARGETS)), text=TEXT, extra=TEXT)
def test_no_text_makes_the_workspace_unreadable_and_accepted_text_round_trips(
    client: TestClient, tmp_path_factory: pytest.TempPathFactory, target: str,
    text: str, extra: str,
) -> None:
    # What the server receives: JSON joins an escaped surrogate PAIR into one
    # astral character, so compare against the decoded form.
    text = json.loads(json.dumps(text))
    extra = json.loads(json.dumps(extra))
    family, key, item, read = TARGETS[target](text)
    gpo = _new_gpo(client, f"p-{tmp_path_factory.mktemp('p').name}")
    response = _send(client, "POST", f"/api/gpos/{gpo['guid']}/preferences/{family}", {
        **_audit(gpo), "scope": "computer", key: item,
    })
    assert response.status_code in (201, 422), response.text
    # The GPO's other text, through the metadata route, with the second string.
    patched = _send(client, "PATCH", f"/api/gpos/{gpo['guid']}", {
        "expected_revision": client.get(f"/api/gpos/{gpo['guid']}").json()["gpo"]["revision"],
        "actor": "tester", "reason": "text", "name": gpo["name"], "description": extra,
    })
    assert patched.status_code in (200, 422), patched.text

    assert client.get("/api/gpos").status_code == 200
    assert client.get(f"/api/gpos/{gpo['guid']}").status_code == 200
    for artifact in ("export.zip", "plan.ps1", "report.txt", "publication-plan"):
        produced = client.get(f"/api/gpos/{gpo['guid']}/{artifact}")
        assert produced.status_code in (200, 422), (artifact, produced.text)

    content = _backup_content(client, gpo["guid"], tmp_path_factory.mktemp("rt"))
    if response.status_code == 201 and content is not None:
        assert read(content) == text, (target, text)
    if patched.status_code == 200:
        assert xml_text_problem(extra) is None  # CR included: the description is XML
        stored = client.get(f"/api/gpos/{gpo['guid']}").json()["gpo"]["description"]
        assert stored in (extra, extra.strip())
    if response.status_code == 201:
        assert xml_text_problem(text) is None


def test_registry_policy_text_round_trips_through_registry_pol(
    client: TestClient, tmp_path: Path
) -> None:
    """Non-GPP text (Registry.pol data) keeps CR; it is not XML element text."""
    gpo = _new_gpo(client, "pol")
    ok = _send(client, "POST", f"/api/gpos/{gpo['guid']}/settings", {
        **_audit(gpo),
        "setting": {"side": "computer", "hive": "HKLM", "key": "Software\\Policies\\T",
                    "value_name": "V", "registry_type": "REG_SZ", "value": "a\r\nb\tc"},
    })
    assert ok.status_code == 201, ok.text
    content = _backup_content(client, gpo["guid"], tmp_path / "pol")
    assert content is not None
    assert [s.value for s in extract_side_settings(content, "computer")] == ["a\r\nb\tc"]


def test_a_surrogate_in_registry_policy_text_is_refused(client: TestClient) -> None:
    gpo = _new_gpo(client, "pol surrogate")
    bad = _send(client, "POST", f"/api/gpos/{gpo['guid']}/settings", {
        **_audit(gpo),
        "setting": {"side": "computer", "hive": "HKLM", "key": "Software\\Policies\\T",
                    "value_name": "V", "registry_type": "REG_SZ", "value": "a\ud800"},
    })
    assert bad.status_code == 422
    assert client.get("/api/gpos").status_code == 200
