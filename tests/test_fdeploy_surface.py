"""The fdeploy operator surface, held against the banked capture and the module contract.

Plan 034 WP-4 ruled Folder Redirection a read target and `fdeploy.py` is the
read half; this is the first route that reaches it at all (see that module's
docstring). Same posture as `test_object_security_surface.py` and
`test_policy_family_surface.py`: the request/response shape is exercised
through the FastAPI app, not by calling the module functions directly, so a
mismatch between `api.py`'s composition and the module's own contract shows up
here.

The positive assertions are pinned to the R3 capture the same way
`test_fdeploy.py` pins them -- reconstructed from the banked ASCII transcript,
hash-checked against what Windows wrote, never against a fixture this
repository invented. The negative and structural tests (bad base64, no BOM,
an unknown folder GUID, the four limitations) use synthetic documents built
with `encode_fdeploy`, because they are about the surface's error handling and
response shape rather than about what the one capture says.
"""

from __future__ import annotations

import base64
import hashlib
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gpo_studio.api import app
from gpo_studio.fdeploy import encode_fdeploy

REPO_ROOT = Path(__file__).resolve().parents[1]
_FIXTURES = REPO_ROOT / "tests" / "fixtures" / "native-folder-redirection-gpmc"

#: Banked in the R3 provenance record; the same constants `test_fdeploy.py`
#: and `test_native_folder_redirection_capture.py` bind.
_POLICY_SHA = "71f1026180c4a92ed5bcca3366e5d22b80a64931f663fa079ef5d49cd2450800"

#: The GUID keying R3's one redirection (FOLDERID_Documents), not the Folder
#: Redirection CSE GUID -- WI-067.
_DOCUMENTS = "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}"
_EVERYONE = "s-1-1-0"

_UNKNOWN_FOLDER = "{11111111-1111-1111-1111-111111111111}"
_FOLDER_B = "{22222222-2222-2222-2222-222222222222}"
_FOLDER_C = "{33333333-3333-3333-3333-333333333333}"


def _native_capture_bytes(name: str) -> bytes:
    """Rebuild the native UTF-16LE bytes from a banked ASCII transcript.

    Copied from `test_fdeploy.py::_native_capture_bytes` -- duplicated
    between capture tests already, which is existing practice in this repo
    (see that function's neighbours).
    """
    transcript = (_FIXTURES / name).read_text(encoding="ascii")
    header, counters, content = transcript.split("\n", 2)
    assert header == "first 4 bytes: FF FE 0D 00"
    size_match = re.search(r"size: (\d+) bytes", counters)
    lf_match = re.search(r"LF count: (\d+)", counters)
    assert size_match is not None and lf_match is not None
    assert content.count("\n") == int(lf_match.group(1))
    native = b"\xff\xfe" + content.replace("\n", "\r\n").encode("utf-16-le")
    assert len(native) == int(size_match.group(1))
    return native


def _policy_bytes() -> bytes:
    data = _native_capture_bytes("fdeploy1.ini.txt")
    assert hashlib.sha256(data).hexdigest() == _POLICY_SHA
    return data


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _synthetic_bytes(*, folder: str, path: str, principal: str = _EVERYONE) -> bytes:
    """Build a minimal well-formed fdeploy document with one redirection."""
    text = (
        "[version]\r\n"
        "version=100\r\n"
        "[Folder_Redirection]\r\n"
        f"{folder}={principal};\r\n"
        f"[{folder}_{principal}]\r\n"
        f"FullPath={path}\r\n"
        "Flags=1021\r\n"
    )
    return encode_fdeploy(text)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


# ---------------------------------------------------------------------------
# /api/folder-redirection/fdeploy -- the capture, through the endpoint
# ---------------------------------------------------------------------------


def test_the_native_capture_parses_through_the_endpoint_to_its_banked_shape(
    client: TestClient,
) -> None:
    """Same values `test_fdeploy.py` binds against the module, now via HTTP."""
    response = client.post(
        "/api/folder-redirection/fdeploy",
        json={"content_base64": _b64(_policy_bytes())},
    )
    assert response.status_code == 200
    body = response.json()

    assert body["version"] == "100"
    assert body["is_marker"] is False
    assert body["parse_warnings"] == []
    assert body["folders"] == [{"guid": _DOCUMENTS, "principals": [_EVERYONE]}]

    (redirection,) = body["redirections"]
    assert redirection["folder_guid"] == _DOCUMENTS
    assert redirection["folder_name"] == "Documents"
    assert redirection["principal"] == _EVERYONE
    assert redirection["full_path"] == (
        r"\\zz-studio-fileserver\zzredir\%USERNAME%\Documents"
    )
    assert redirection["flags"] == 1021
    assert redirection["flags_text"] == "1021"
    assert redirection["describe_flags"] == "1021 (binary 1111111101)"

    assert body["report_lines"][0] == "Version: 100"
    # Not `any("Documents" in line)`: the FullPath line ends in \Documents, so
    # that assertion passes unchanged when the name lookup returns None. Pin
    # the line that actually renders the name.
    assert body["report_lines"][2] == f"Documents {_DOCUMENTS} for {_EVERYONE}:"


def test_the_capture_validates_clean_through_the_endpoint(client: TestClient) -> None:
    response = client.post(
        "/api/folder-redirection/fdeploy",
        json={"content_base64": _b64(_policy_bytes())},
    )
    assert response.json()["validation"] == []


# ---------------------------------------------------------------------------
# The four limitations, carried in every parse response
# ---------------------------------------------------------------------------


def test_every_parse_response_carries_all_four_limitations(client: TestClient) -> None:
    """The house rule: limits travel in the response body, not only in docs.

    None of the four depends on what was sent -- the Flags decode, the
    single-capture scope, the folder-name table's evidence base and the
    read-only ruling hold for every call -- so all four must be present
    regardless of which document was parsed.

    `folder_names_documented_not_measured` is here because the module
    docstring saying it is not enough: a caller reading JSON is not reading
    the docstring, which is this block's whole stated rationale. Twelve of the
    thirteen names in that table are documented Windows constants no lane has
    measured.
    """
    response = client.post(
        "/api/folder-redirection/fdeploy",
        json={"content_base64": _b64(_policy_bytes())},
    )
    codes = {item["code"] for item in response.json()["limitations"]}
    assert codes == {
        "flags_not_decoded",
        "single_capture_only",
        "folder_names_documented_not_measured",
        "read_only_no_writer",
    }


def test_the_diff_response_also_carries_the_four_limitations(
    client: TestClient,
) -> None:
    old = _synthetic_bytes(folder=_UNKNOWN_FOLDER, path=r"\\server\A")
    response = client.post(
        "/api/folder-redirection/fdeploy/diff",
        json={"old_base64": _b64(old), "new_base64": _b64(old)},
    )
    codes = {item["code"] for item in response.json()["limitations"]}
    assert codes == {
        "flags_not_decoded",
        "single_capture_only",
        "folder_names_documented_not_measured",
        "read_only_no_writer",
    }


# ---------------------------------------------------------------------------
# Error handling: 400, never 500
# ---------------------------------------------------------------------------


def test_invalid_base64_is_a_400_not_a_500(client: TestClient) -> None:
    response = client.post(
        "/api/folder-redirection/fdeploy",
        json={"content_base64": "not@@valid$$base64!!"},
    )
    assert response.status_code == 400
    assert "base64" in response.json()["error"]["message"].lower()


def test_a_body_with_no_bom_is_a_400_not_a_500(client: TestClient) -> None:
    """Valid base64, but the decoded bytes fail `decode_fdeploy`'s BOM sniff."""
    response = client.post(
        "/api/folder-redirection/fdeploy",
        json={"content_base64": _b64(b"hello world, no BOM here")},
    )
    assert response.status_code == 400
    assert "byte-order mark" in response.json()["error"]["message"]


def test_diff_endpoint_also_refuses_bad_input_with_a_400(client: TestClient) -> None:
    good = _b64(_synthetic_bytes(folder=_UNKNOWN_FOLDER, path=r"\\server\A"))
    response = client.post(
        "/api/folder-redirection/fdeploy/diff",
        json={"old_base64": good, "new_base64": "!!not base64!!"},
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Never invent a folder name
# ---------------------------------------------------------------------------


def test_an_unrecognised_folder_guid_gets_a_null_name_never_a_guess(
    client: TestClient,
) -> None:
    data = _synthetic_bytes(folder=_UNKNOWN_FOLDER, path=r"\\server\share\Unknown")
    response = client.post(
        "/api/folder-redirection/fdeploy",
        json={"content_base64": _b64(data)},
    )
    (redirection,) = response.json()["redirections"]
    assert redirection["folder_guid"] == _UNKNOWN_FOLDER
    assert redirection["folder_name"] is None


# ---------------------------------------------------------------------------
# /api/folder-redirection/fdeploy/diff
# ---------------------------------------------------------------------------


def test_the_diff_endpoint_reports_added_modified_and_removed(
    client: TestClient,
) -> None:
    old = encode_fdeploy(
        "[version]\r\n"
        "version=100\r\n"
        "[Folder_Redirection]\r\n"
        f"{_UNKNOWN_FOLDER}={_EVERYONE};\r\n"
        f"{_FOLDER_B}={_EVERYONE};\r\n"
        f"[{_UNKNOWN_FOLDER}_{_EVERYONE}]\r\n"
        r"FullPath=\\server\A" "\r\n"
        "Flags=1021\r\n"
        f"[{_FOLDER_B}_{_EVERYONE}]\r\n"
        r"FullPath=\\server\B" "\r\n"
        "Flags=1021\r\n"
    )
    new = encode_fdeploy(
        "[version]\r\n"
        "version=100\r\n"
        "[Folder_Redirection]\r\n"
        f"{_UNKNOWN_FOLDER}={_EVERYONE};\r\n"
        f"{_FOLDER_C}={_EVERYONE};\r\n"
        f"[{_UNKNOWN_FOLDER}_{_EVERYONE}]\r\n"
        r"FullPath=\\server\A-changed" "\r\n"
        "Flags=1021\r\n"
        f"[{_FOLDER_C}_{_EVERYONE}]\r\n"
        r"FullPath=\\server\C" "\r\n"
        "Flags=1021\r\n"
    )

    response = client.post(
        "/api/folder-redirection/fdeploy/diff",
        json={"old_base64": _b64(old), "new_base64": _b64(new)},
    )
    assert response.status_code == 200
    changes = {(c["kind"], c["folder_guid"]): c for c in response.json()["changes"]}

    assert ("modified", _UNKNOWN_FOLDER) in changes
    modified = changes[("modified", _UNKNOWN_FOLDER)]
    assert modified["old_full_path"] == r"\\server\A"
    assert modified["new_full_path"] == r"\\server\A-changed"

    assert ("removed", _FOLDER_B) in changes
    removed = changes[("removed", _FOLDER_B)]
    assert removed["old_full_path"] == r"\\server\B"
    assert removed["new_full_path"] is None

    assert ("added", _FOLDER_C) in changes
    added = changes[("added", _FOLDER_C)]
    assert added["old_full_path"] is None
    assert added["new_full_path"] == r"\\server\C"

    assert all(c["folder_name"] is None for c in changes.values())
