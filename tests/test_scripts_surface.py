"""Plan 034: the Scripts export surface, held against the lane that measured it.

`/api/gpos/{guid}/gpmc-backup-with-scripts` exists because `script_policy.py`
had a certified writer -- `export.gpmc_backup_bundle(gpo, scripts=...)`, read by
the R10 scripts-metadata lane -- and nothing an operator could reach passed
scripts to it. The surface calls that function and `native_backup_refusal`
unchanged, so the first test here is the one that matters: for the request
that restates the certified candidate, the endpoint returns the lane builder's
bytes exactly. If the builder changes and the surface does not (or the other
way round), this fails and the surface stops claiming a certification that has
moved out from under it.

The rest pins the refusals. The lane measured machine-side startup entries on a
GPO with nothing else in it, PowerShell ordered first; the surface refuses
everything outside that rather than warning, and each refusal is a 422 with its
own code. A limitation that lives only in the docs is not delivered, so every
response's limitations are checked too.
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
import re
import runpy
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from gpo_studio.api import app
from gpo_studio.gpp import GppCollection, GppService
from gpo_studio.model import GPO, CseMetadataEntry, RegistrySetting
from gpo_studio.store import WorkspaceStore

REPO_ROOT = Path(__file__).resolve().parents[1]
BUILDER = REPO_ROOT / "scripts" / "plan-033" / "build-scripts-backup-candidate.py"
#: The native GPMC capture (Windows Server 2025) the R10 lane's builder is
#: asserted against byte for byte. Read here independently of the builder.
NATIVE_CAPTURES = REPO_ROOT / "tests" / "fixtures" / "native-scripts-gpmc"

#: The builder is a script, not an importable module; the policy-family surface
#: test reaches its builder the same way.
_BUILDER_SYMBOLS = runpy.run_path(str(BUILDER))
_CANDIDATE_GPO: GPO = _BUILDER_SYMBOLS["_GPO"]

#: Exactly the scripts `build-scripts-backup-candidate.py` hands the writer,
#: restated as a request. Restated and not imported, because what this file has
#: to prove is that a caller sending the certified values through the public
#: shape reaches the certified bytes -- importing the builder's dataclasses
#: would skip the request model, which is half the path under test.
CERTIFIED_REQUEST: dict[str, Any] = {
    "computer": {
        "startup": [
            {"command": "zz-studio-marker.cmd", "parameters": "/c alpha beta"},
            {"command": "zz-studio-second.cmd"},
        ],
        "powershell_startup": [
            {"command": "zz-studio-marker.ps1", "parameters": "-Mode Alpha"},
        ],
        "powershell_order": "run_windows_powershell_scripts_first",
    }
}

EXPECTED_LIMITATIONS = {
    "payload_not_carried",
    "execution_unmeasured",
    "gpme_editing_unmeasured",
    "one_entry_shape_measured",
}

ROUTE = "/api/gpos/{guid}/gpmc-backup-with-scripts"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[WorkspaceStore]:
    workspace = WorkspaceStore(tmp_path / "scripts.db")
    app.state.store = workspace
    app.state.owns_store = False
    yield workspace


@pytest.fixture
def client(store: WorkspaceStore) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def _candidate_gpo(store: WorkspaceStore, **overrides: Any) -> GPO:
    """The lane candidate's identity, created in the workspace."""
    return store.create_gpo(
        _CANDIDATE_GPO.name,
        identity="tester",
        reason="scripts surface test",
        guid=_CANDIDATE_GPO.guid,
        domain=_CANDIDATE_GPO.domain,
        **overrides,
    )


def _request(**computer: Any) -> dict[str, Any]:
    body = copy.deepcopy(CERTIFIED_REQUEST)
    body["computer"].update(computer)
    return body


def _codes(response: Any) -> list[str]:
    assert response.status_code == 422, response.text
    return [issue["code"] for issue in response.json()["error"]["issues"]]


# --------------------------------------------------------------------------
# The certified path
# --------------------------------------------------------------------------


def test_the_certified_request_returns_the_lane_builders_bytes(
    store: WorkspaceStore, client: TestClient
) -> None:
    gpo = _candidate_gpo(store)
    response = client.post(ROUTE.format(guid=gpo.guid), json=CERTIFIED_REQUEST)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    assert response.content == _BUILDER_SYMBOLS["build_bundle"]()


def _banked_native_bytes(transcript_name: str) -> bytes:
    """The bytes Windows wrote, rebuilt from the banked capture transcript.

    The reconstruction is the one `provenance.json` documents (BOM, then the
    content with LF transcribed back to CRLF, as UTF-16LE), and its length is
    checked against both the transcript header and the provenance record's
    measured byte count, so neither the transcript nor this helper can drift
    alone.
    """
    provenance = json.loads((NATIVE_CAPTURES / "provenance.json").read_text(encoding="utf-8"))
    measured = provenance["files"][transcript_name]["measured_total_bytes"]
    transcript = (NATIVE_CAPTURES / transcript_name).read_text(encoding="ascii")
    header, counters, content = transcript.split("\n", 2)
    assert header == "first 4 bytes: FF FE 0D 00"
    size = re.search(r"size: (\d+) bytes", counters)
    assert size is not None and int(size.group(1)) == measured
    native = b"\xff\xfe" + content.replace("\n", "\r\n").encode("utf-16-le")
    assert len(native) == measured
    return native


#: Backup-relative path of each Scripts file -> its banked capture transcript.
_CAPTURED_FILES = {
    "Machine/Scripts/scripts.ini": "scripts.ini.txt",
    "Machine/Scripts/psscripts.ini": "psscripts.ini.txt",
}


def test_the_certified_request_returns_the_bytes_windows_wrote(
    store: WorkspaceStore, client: TestClient
) -> None:
    """Bind the endpoint to the measurement, not only to the shared serializer.

    The builder-equality test above proves the request maps onto the certified
    call, but both sides run the same `export.py` serializer, so a drift in it
    (an extra leading blank line, say) moves both and passes. These bytes come
    from the native GPMC capture instead: the download's INI files and the
    preview's must equal what Windows wrote for this entry shape.
    """
    gpo = _candidate_gpo(store)
    download = client.post(ROUTE.format(guid=gpo.guid), json=CERTIFIED_REQUEST)
    assert download.status_code == 200, download.text
    with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
        members = {
            name.split("/DomainSysvol/GPO/", 1)[1]: archive.read(name)
            for name in archive.namelist()
            if "/Scripts/" in name and not name.endswith("/")
        }
    preview = client.post(ROUTE.format(guid=gpo.guid) + "/preview", json=CERTIFIED_REQUEST)
    assert preview.status_code == 200, preview.text
    previewed = {item["path"]: item for item in preview.json()["files"]}

    assert set(members) == set(_CAPTURED_FILES)
    assert set(previewed) == set(_CAPTURED_FILES)
    for path, transcript_name in _CAPTURED_FILES.items():
        native = _banked_native_bytes(transcript_name)
        assert members[path] == native, path
        assert previewed[path]["sha256"] == hashlib.sha256(native).hexdigest(), path
        assert previewed[path]["size"] == len(native), path
        assert b"\xff\xfe" + previewed[path]["text"].encode("utf-16-le") == native, path


def test_the_preview_reads_the_same_bytes_the_download_returns(
    store: WorkspaceStore, client: TestClient
) -> None:
    gpo = _candidate_gpo(store)
    download = client.post(ROUTE.format(guid=gpo.guid), json=CERTIFIED_REQUEST)
    preview = client.post(
        ROUTE.format(guid=gpo.guid) + "/preview", json=CERTIFIED_REQUEST
    ).json()
    assert preview["bundle_sha256"] == hashlib.sha256(download.content).hexdigest()
    assert preview["bundle_size"] == len(download.content)
    assert preview["backup_id"] == download.headers["x-gpo-backup-id"]

    with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
        members = {
            name.split("/DomainSysvol/GPO/", 1)[1]: archive.read(name)
            for name in archive.namelist()
            if "/Scripts/" in name
        }
    assert {item["path"] for item in preview["files"]} == set(members)
    for item in preview["files"]:
        raw = members[item["path"]]
        assert item["sha256"] == hashlib.sha256(raw).hexdigest()
        # The text is the native bytes decoded, not a re-rendering.
        assert raw == b"\xff\xfe" + item["text"].encode("utf-16-le")
    texts = {item["path"]: item["text"] for item in preview["files"]}
    assert "0CmdLine=zz-studio-marker.cmd" in texts["Machine/Scripts/scripts.ini"]
    assert "1Parameters=\r\n" in texts["Machine/Scripts/scripts.ini"]
    assert "StartExecutePSFirst=true" in texts["Machine/Scripts/psscripts.ini"]
    assert preview["machine_extension_names"] == (
        "[{42B5FAAE-6536-11D2-AE5A-0000F87571E3}{40B6664F-4972-11D1-A7CA-0000F87571E3}]"
    )
    assert preview["user_extension_names"] == ""


def test_every_response_carries_the_limitations(
    store: WorkspaceStore, client: TestClient
) -> None:
    gpo = _candidate_gpo(store)
    download = client.post(ROUTE.format(guid=gpo.guid), json=CERTIFIED_REQUEST)
    header = {code.strip() for code in download.headers["x-gpo-studio-limitations"].split(",")}
    assert header == EXPECTED_LIMITATIONS
    preview = client.post(ROUTE.format(guid=gpo.guid) + "/preview", json=CERTIFIED_REQUEST)
    body = preview.json()
    assert {item["code"] for item in body["limitations"]} == EXPECTED_LIMITATIONS
    assert all(item["message"] for item in body["limitations"])


def test_a_legacy_only_policy_needs_no_powershell_order(
    store: WorkspaceStore, client: TestClient
) -> None:
    gpo = _candidate_gpo(store)
    body = _request(powershell_startup=[], powershell_order="not_configured")
    preview = client.post(ROUTE.format(guid=gpo.guid) + "/preview", json=body)
    assert preview.status_code == 200, preview.text
    assert [item["path"] for item in preview.json()["files"]] == ["Machine/Scripts/scripts.ini"]


def test_position_is_the_order(store: WorkspaceStore, client: TestClient) -> None:
    gpo = _candidate_gpo(store)
    body = _request(
        startup=[{"command": "second.cmd"}, {"command": "first.cmd"}],
        powershell_startup=[],
    )
    text = client.post(ROUTE.format(guid=gpo.guid) + "/preview", json=body).json()["files"][0][
        "text"
    ]
    assert text.index("0CmdLine=second.cmd") < text.index("1CmdLine=first.cmd")


def test_parameter_warnings_ride_with_the_answer(
    store: WorkspaceStore, client: TestClient
) -> None:
    gpo = _candidate_gpo(store)
    body = _request(
        startup=[{"command": "a.cmd", "parameters": "%TEMP%\\x"}], powershell_startup=[]
    )
    preview = client.post(ROUTE.format(guid=gpo.guid) + "/preview", json=body).json()
    assert [issue["code"] for issue in preview["issues"]] == ["environment_variable_path"]
    download = client.post(ROUTE.format(guid=gpo.guid), json=body)
    assert download.headers["x-gpo-studio-warnings"] == "environment_variable_path"


# --------------------------------------------------------------------------
# Refusals outside the lane's measurement
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "code"),
    [
        pytest.param(
            {**CERTIFIED_REQUEST, "user": {"logon": [{"command": "u.cmd"}]}},
            "scripts_user_side_unmeasured",
            id="user-side",
        ),
        pytest.param({"user": {}}, "scripts_user_side_unmeasured", id="empty-user-side"),
        pytest.param({}, "scripts_policy_empty", id="no-sides"),
        pytest.param({"computer": {}}, "scripts_policy_empty", id="empty-computer"),
        pytest.param(
            _request(shutdown=[{"command": "s.cmd"}]),
            "scripts_trigger_unmeasured",
            id="shutdown",
        ),
        pytest.param(
            _request(powershell_shutdown=[{"command": "s.ps1"}]),
            "scripts_trigger_unmeasured",
            id="powershell-shutdown",
        ),
        pytest.param(
            _request(logon=[{"command": "l.cmd"}]),
            "scripts_trigger_unmeasured",
            id="computer-logon",
        ),
        pytest.param(
            _request(powershell_order="run_windows_powershell_scripts_last"),
            "scripts_powershell_order_unmeasured",
            id="powershell-last",
        ),
        pytest.param(
            _request(powershell_order="not_configured"),
            "scripts_powershell_order_unmeasured",
            id="powershell-not-configured",
        ),
        pytest.param(
            _request(startup=[{"command": "a.cmd", "parameters": "x\r\n0CmdLine=evil.cmd"}]),
            "script_control_character",
            id="ini-line-injection",
        ),
        pytest.param(
            _request(startup=[{"command": "a\x00.cmd"}]),
            "script_control_character",
            id="nul-in-command",
        ),
        pytest.param(
            _request(startup=[{"command": "   "}]),
            "script_command_empty",
            id="blank-command",
        ),
        pytest.param(
            _request(startup=[{"command": "a.cmd", "parameters": "x & y"}]),
            "unquoted_metacharacter",
            id="script-policy-validation",
        ),
    ],
)
def test_shapes_outside_the_lane_are_refused(
    store: WorkspaceStore, client: TestClient, body: dict[str, Any], code: str
) -> None:
    gpo = _candidate_gpo(store)
    for suffix in ("", "/preview"):
        response = client.post(ROUTE.format(guid=gpo.guid) + suffix, json=body)
        assert code in _codes(response)


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(
            _request(startup=[{"command": "a.cmd", "execution": "asynchronous"}]),
            id="asynchronous",
        ),
        pytest.param(
            _request(powershell_startup=[{"command": "a.ps1", "no_profile": True}]),
            id="no-profile",
        ),
        pytest.param(
            _request(powershell_startup=[{"command": "a.ps1", "non_interactive": False}]),
            id="interactive",
        ),
        pytest.param(_request(run_logon_scripts_sync=True), id="logon-sync"),
    ],
)
def test_export_refusals_reach_the_caller_with_their_own_code(
    store: WorkspaceStore, client: TestClient, body: dict[str, Any]
) -> None:
    """`_native_scripts_refusal` runs unchanged and its code is not rewritten."""
    gpo = _candidate_gpo(store)
    response = client.post(ROUTE.format(guid=gpo.guid), json=body)
    assert _codes(response) == ["inexpressible_native_script_state"]


@pytest.mark.parametrize(
    "field",
    [
        pytest.param({"timeout_seconds": 30}, id="timeout"),
        pytest.param({"order": 3}, id="order"),
    ],
)
def test_values_the_ini_cannot_carry_are_not_accepted_silently(
    store: WorkspaceStore, client: TestClient, field: dict[str, Any]
) -> None:
    """A field `export.py` would drop is a schema error, never a vanished value."""
    gpo = _candidate_gpo(store)
    body = _request(startup=[{"command": "a.cmd", **field}])
    response = client.post(ROUTE.format(guid=gpo.guid), json=body)
    assert response.status_code == 422
    assert response.json()["error"]["message"] == "Invalid request"


def test_legacy_scripts_first_is_not_accepted_silently(
    store: WorkspaceStore, client: TestClient
) -> None:
    gpo = _candidate_gpo(store)
    response = client.post(
        ROUTE.format(guid=gpo.guid), json=_request(legacy_scripts_first=False)
    )
    assert response.status_code == 422
    assert response.json()["error"]["message"] == "Invalid request"


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        pytest.param(
            {
                "settings": (
                    RegistrySetting(
                        id="m1",
                        side="computer",
                        hive="HKLM",
                        key=r"SOFTWARE\Policies\SyntheticApp",
                        value_name="Flag",
                        registry_type="REG_DWORD",
                        value=1,
                    ),
                )
            },
            "scripts_with_other_content_unmeasured",
            id="registry-settings",
        ),
        pytest.param(
            {
                "gpp_collections": (
                    GppCollection(
                        scope="computer",
                        services=(
                            GppService(service_name="SyntheticSvc", startup_type="automatic"),
                        ),
                    ),
                )
            },
            "scripts_with_other_content_unmeasured",
            id="preferences",
        ),
        pytest.param(
            {"user_enabled": False}, "scripts_disabled_side_unmeasured", id="user-disabled"
        ),
        pytest.param(
            {"computer_enabled": False}, "scripts_disabled_side_unmeasured", id="computer-disabled"
        ),
        pytest.param(
            {"cse_metadata": (CseMetadataEntry(guid="{unknown-guid}", side="machine"),)},
            "unknown_cse_content",
            id="preserved-content",
        ),
    ],
)
def test_gpos_outside_the_lanes_shape_are_refused_and_advertised(
    store: WorkspaceStore, client: TestClient, overrides: dict[str, Any], code: str
) -> None:
    gpo = _candidate_gpo(store, **overrides)
    response = client.post(ROUTE.format(guid=gpo.guid), json=CERTIFIED_REQUEST)
    assert code in _codes(response)
    capability = client.get(f"/api/gpos/{gpo.guid}").json()["artifact_capabilities"][
        "scripts_export"
    ]
    assert capability["enabled"] is False
    assert capability["reason"]


def test_the_capability_is_enabled_for_a_gpo_inside_the_shape(
    store: WorkspaceStore, client: TestClient
) -> None:
    gpo = _candidate_gpo(store)
    capability = client.get(f"/api/gpos/{gpo.guid}").json()["artifact_capabilities"][
        "scripts_export"
    ]
    assert capability == {
        "enabled": True,
        "format": "zip",
        "reason": "",
        "measured_shape": "computer startup scripts on a GPO with no other content",
    }


def test_links_and_filters_do_not_change_the_bundle(
    store: WorkspaceStore, client: TestClient
) -> None:
    """A backup carries neither links nor filtering, so neither is refused."""
    from gpo_studio.model import GPOLink, SecurityFilter

    gpo = _candidate_gpo(
        store,
        links=(GPOLink(id="l1", target="OU=Servers,DC=synthetic,DC=test"),),
        security_filters=(
            SecurityFilter(id="sf1", principal="SYNTHETIC\\Servers", permission="apply"),
        ),
    )
    response = client.post(ROUTE.format(guid=gpo.guid), json=CERTIFIED_REQUEST)
    assert response.status_code == 200
    assert response.content == _BUILDER_SYMBOLS["build_bundle"]()


def test_an_unknown_gpo_is_a_404(client: TestClient) -> None:
    response = client.post(
        ROUTE.format(guid="00000000-0000-0000-0000-000000000000"), json=CERTIFIED_REQUEST
    )
    assert response.status_code == 404
