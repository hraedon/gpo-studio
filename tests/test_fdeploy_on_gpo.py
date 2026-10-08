"""WI-068: an imported ``fdeploy1.ini`` reaches the GPO, its report and its diff.

The backup these tests import is a **composite**: the native Scripts rebackup
the inventory tests already use, with R3's two native Folder Redirection files
laid into ``User/Documents & Settings/``. Windows never wrote that GPO -- R3
captured the two files from a different one -- so nothing here claims Windows
would produce or accept the combination. What the composite does carry is
R3's bytes, rebuilt from the banked transcript and hash-checked against what
GPMC wrote, so every positive assertion about the parsed document is still an
assertion about a native file.

The split this item exists to hold: the parsed document is **import
provenance**, like ``backup_inventory``. It is in the review digest and not in
the policy-semantic digest, it is never emitted by an export, and the GPMC
backup path refuses a GPO carrying it rather than silently dropping it
(there is no writer; WI-066).
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import shutil
import zipfile
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gpo_studio import backup as backup_module
from gpo_studio.api import app
from gpo_studio.backup import BackupError, read_backup
from gpo_studio.canonical import (
    policy_semantic_dict,
    policy_semantic_sha256,
    review_model_dict,
    review_model_sha256,
)
from gpo_studio.diff import diff_gpos, three_way_diff
from gpo_studio.export import export_bundle, gpmc_backup_bundle, native_backup_refusal
from gpo_studio.fdeploy import (
    FDEPLOY_POLICY_PATH,
    diff_fdeploy,
    encode_fdeploy,
    fdeploy_report_lines,
    native_digest,
    read_fdeploy,
)
from gpo_studio.import_export import collect_cse_metadata
from gpo_studio.model import GPO, StudioError, ValidationError
from gpo_studio.report import policy_report
from gpo_studio.store import WorkspaceStore, gpo_from_dict

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "docs/plan-033/wp1b-evidence/wi059-20260908/scripts-metadata/rebackup"
_FIXTURES = ROOT / "tests" / "fixtures" / "native-folder-redirection-gpmc"

#: Banked in the R3 provenance record; the same constants `test_fdeploy.py` binds.
_POLICY_SHA = "71f1026180c4a92ed5bcca3366e5d22b80a64931f663fa079ef5d49cd2450800"
_MARKER_SHA = "5ad8f52071d25165e7e68064ab194ec27a074a3846149ed0689af23e7f7f2d00"
_DOCUMENTS = "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}"
_EVERYONE = "s-1-1-0"
_MARKER_PATH = "User/Documents & Settings/fdeploy.ini"
_OTHER_FOLDER = "{22222222-2222-2222-2222-222222222222}"


def _native_capture_bytes(name: str) -> bytes:
    """Rebuild native UTF-16LE bytes from a banked transcript (as `test_fdeploy.py`)."""
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


def _marker_bytes() -> bytes:
    data = _native_capture_bytes("fdeploy.ini.txt")
    assert hashlib.sha256(data).hexdigest() == _MARKER_SHA
    return data


def _composite_backup(tmp_path: Path, *, policy: bytes | None = None) -> Path:
    """The Scripts rebackup with R3's two Folder Redirection files added."""
    target = tmp_path / "inbox"
    shutil.copytree(SCRIPTS, target)
    (gpo_root,) = target.glob("*/DomainSysvol/GPO")
    folder = gpo_root / "User" / "Documents & Settings"
    folder.mkdir(parents=True)
    (folder / "fdeploy1.ini").write_bytes(_policy_bytes() if policy is None else policy)
    (folder / "fdeploy.ini").write_bytes(_marker_bytes())
    return target


def _synthetic(path: str, folder: str = _DOCUMENTS) -> bytes:
    return encode_fdeploy(
        "[version]\r\n"
        "version=100\r\n"
        "[Folder_Redirection]\r\n"
        f"{folder}={_EVERYONE};\r\n"
        f"[{folder}_{_EVERYONE}]\r\n"
        f"FullPath={path}\r\n"
        "Flags=1021\r\n"
    )


# ---------------------------------------------------------------------------
# read_backup
# ---------------------------------------------------------------------------


def test_read_backup_parses_the_native_policy_file_onto_the_backup_gpo(
    tmp_path: Path,
) -> None:
    (gpo,) = read_backup(_composite_backup(tmp_path)).gpos
    assert gpo.fdeploy is not None
    assert gpo.fdeploy == read_fdeploy(_policy_bytes())
    (rule,) = gpo.fdeploy.redirections()
    assert (rule.folder_guid, rule.principal, rule.flags) == (_DOCUMENTS, _EVERYONE, 1021)
    assert rule.full_path == r"\\zz-studio-fileserver\zzredir\%USERNAME%\Documents"


def test_the_parsed_document_hashes_to_the_inventory_row_for_the_same_file(
    tmp_path: Path,
) -> None:
    """The join a reviewer makes between the report section and the appendix."""
    (gpo,) = read_backup(_composite_backup(tmp_path)).gpos
    assert gpo.fdeploy is not None and gpo.backup_inventory is not None
    rows = {f.relative_path: f for f in gpo.backup_inventory.files}
    assert rows[FDEPLOY_POLICY_PATH].content_hash == _POLICY_SHA
    assert native_digest(gpo.fdeploy) == (_POLICY_SHA, rows[FDEPLOY_POLICY_PATH].size)


def test_a_backup_without_the_file_carries_no_document() -> None:
    (gpo,) = read_backup(SCRIPTS).gpos
    assert gpo.fdeploy is None


def test_the_marker_alone_is_not_read_onto_the_model(tmp_path: Path) -> None:
    backup = _composite_backup(tmp_path)
    next(backup.rglob("fdeploy1.ini")).unlink()
    (gpo,) = read_backup(backup).gpos
    assert gpo.fdeploy is None


def test_a_policy_file_without_the_measured_bom_refuses_the_import(tmp_path: Path) -> None:
    backup = _composite_backup(tmp_path, policy=b"[version]\r\nversion=100\r\n")
    with pytest.raises(BackupError, match="fdeploy1.ini"):
        read_backup(backup)


def test_case_variant_policy_files_are_refused_as_ambiguous(tmp_path: Path) -> None:
    backup = _composite_backup(tmp_path)
    folder = next(backup.rglob("fdeploy1.ini")).parent
    variant = folder / "FDEPLOY1.INI"
    variant.write_bytes(_policy_bytes())
    if len({p.name for p in folder.iterdir()}) < 3:
        pytest.skip("case-insensitive filesystem cannot hold both spellings")
    with pytest.raises(BackupError, match="Ambiguous"):
        read_backup(backup)


def test_bytes_that_differ_from_the_scanned_file_refuse_the_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The parse must describe the file the inventory hashed, not a later one."""
    backup = _composite_backup(tmp_path)
    real = backup_module.read_file_bytes

    def swapped(path: Path) -> bytes:
        if path.name == "fdeploy1.ini":
            return _synthetic(r"\\elsewhere\share")
        return real(path)

    monkeypatch.setattr(backup_module, "read_file_bytes", swapped)
    with pytest.raises(BackupError, match="changed while"):
        read_backup(backup)


def test_the_parsed_file_leaves_the_unmodeled_list_and_the_marker_stays(
    tmp_path: Path,
) -> None:
    (gpo,) = read_backup(_composite_backup(tmp_path)).gpos
    listed = {
        f.relative_path.replace("\\", "/")
        for entry in collect_cse_metadata(gpo)
        for f in entry.files
    }
    assert "Documents & Settings/fdeploy.ini" in listed
    assert "Documents & Settings/fdeploy1.ini" not in listed
    # Without a parse, the file is unmodeled again and is listed as such.
    unparsed = {
        f.relative_path.replace("\\", "/")
        for entry in collect_cse_metadata(replace(gpo, fdeploy=None))
        for f in entry.files
    }
    assert "Documents & Settings/fdeploy1.ini" in unparsed


# ---------------------------------------------------------------------------
# The public import path, report, persistence, fork and list row
# ---------------------------------------------------------------------------


def test_import_report_persistence_and_fork_carry_the_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    inbox = _composite_backup(tmp_path)
    monkeypatch.setenv("GPO_STUDIO_INBOX_DIR", str(inbox))
    database = tmp_path / "fdeploy.db"
    expected = read_fdeploy(_policy_bytes())
    with closing(WorkspaceStore(database)) as store:
        monkeypatch.setattr(app.state, "store", store, raising=False)
        monkeypatch.setattr(app.state, "owns_store", False, raising=False)
        with TestClient(app) as client:
            imported = client.post("/api/backups/import", json={
                "path": str(inbox), "actor": "fdeploy-test", "reason": "WI-068",
            })
            assert imported.status_code == 201, imported.text
            guid = imported.json()["gpo"]["guid"]
            assert gpo_from_dict(imported.json()["gpo"]).fdeploy == expected
            capabilities = imported.json()["artifact_capabilities"]
            assert capabilities["gpmc_export"]["enabled"] is False

            rows = {item["guid"]: item for item in client.get("/api/gpos").json()["items"]}
            assert rows[guid]["has_fdeploy"] is True
            assert "fdeploy" not in rows[guid]

            fork = client.post(f"/api/gpos/{guid}/fork", json={
                "name": "Redirection fork", "actor": "fdeploy-test", "reason": "provenance",
            })
            assert fork.status_code == 201, fork.text
            assert gpo_from_dict(fork.json()["gpo"]).fdeploy == expected

            assert client.get(f"/api/gpos/{guid}/gpmc-backup").status_code == 422

    with closing(WorkspaceStore(database)) as reopened:
        assert reopened.get_gpo(guid).fdeploy == expected
        monkeypatch.setattr(app.state, "store", reopened, raising=False)
        with TestClient(app) as client:
            report = client.get(f"/api/gpos/{guid}/report.txt")
            assert report.status_code == 200
            text = report.text

    section = text.split("Folder Redirection (fdeploy1.ini)\n", 1)[1].split("\n\n", 1)[0]
    body = section.splitlines()[1:]  # drop the underline
    assert body[0] == f"Source: {FDEPLOY_POLICY_PATH}: 458 bytes; SHA-256 {_POLICY_SHA}"
    assert "not published or exported" in body[1]
    # Rendered through the module's renderer, line for line, with nothing
    # structural to report for the native capture.
    assert body[2:] == list(fdeploy_report_lines(expected))
    assert f"Documents {_DOCUMENTS} for {_EVERYONE}:" in body

    unmodeled = text.split("Unmodeled extension files (metadata only)\n", 1)[1].split(
        "\n\n", 1
    )[0]
    assert "fdeploy.ini" in unmodeled
    assert "fdeploy1.ini" not in unmodeled
    # Provenance stays honest: the source inventory appendix still lists the
    # file by path, size and hash, exactly as it was observed in the backup.
    appendix = text.split("Imported Windows inventory (source snapshot)", 1)[1]
    assert FDEPLOY_POLICY_PATH in appendix and _POLICY_SHA in appendix
    assert _MARKER_PATH in appendix and _MARKER_SHA in appendix


def test_the_report_section_matches_what_the_endpoint_renders(tmp_path: Path) -> None:
    """The two compositions of `fdeploy_report_lines` must not drift apart.

    The endpoint is `test_fdeploy_surface.py`'s subject; this holds the GPO
    report's rendering of the same bytes equal to it.
    """
    (backup_gpo,) = read_backup(_composite_backup(tmp_path)).gpos
    gpo = GPO(guid=backup_gpo.guid, name="Composite", fdeploy=backup_gpo.fdeploy)
    with TestClient(app) as client:
        response = client.post(
            "/api/folder-redirection/fdeploy",
            json={"content_base64": base64.b64encode(_policy_bytes()).decode("ascii")},
        )
    assert response.status_code == 200
    endpoint_lines = response.json()["report_lines"]
    report = policy_report(gpo)
    rendered = report.split("Folder Redirection (fdeploy1.ini)\n", 1)[1].split("\n\n", 1)[0]
    assert rendered.splitlines()[3:] == endpoint_lines


def test_structural_findings_are_reported_with_the_document() -> None:
    document = read_fdeploy(encode_fdeploy(
        "[version]\r\nversion=100\r\n[Folder_Redirection]\r\n"
        f"{_DOCUMENTS}={_EVERYONE};\r\n"
    ))
    report = policy_report(GPO(guid="g", name="Structural", fdeploy=document))
    assert "Structural [ERROR] missing_redirection_section" in report


def test_a_gpo_without_a_document_gets_no_section() -> None:
    assert "Folder Redirection (fdeploy1.ini)" not in policy_report(GPO(guid="g", name="n"))


# ---------------------------------------------------------------------------
# Canonical: review digest yes, policy-semantic digest no
# ---------------------------------------------------------------------------


def test_the_document_is_review_provenance_and_not_policy_semantics(
    tmp_path: Path,
) -> None:
    """The same split `test_backup_report_inventory.py` pins for backup_inventory."""
    (backup_gpo,) = read_backup(_composite_backup(tmp_path)).gpos
    with_doc = GPO(guid=backup_gpo.guid, name="Composite", fdeploy=backup_gpo.fdeploy)
    without = replace(with_doc, fdeploy=None)
    changed = replace(with_doc, fdeploy=read_fdeploy(_synthetic(r"\\elsewhere\share")))

    assert "fdeploy" not in policy_semantic_dict(with_doc)
    assert policy_semantic_sha256(with_doc) == policy_semantic_sha256(without)
    assert policy_semantic_sha256(changed) == policy_semantic_sha256(with_doc)

    assert review_model_sha256(with_doc) != review_model_sha256(without)
    assert review_model_sha256(changed) != review_model_sha256(with_doc)
    # A GPO with no document keeps the digest it always had: the key is absent,
    # not null.
    assert "fdeploy" not in review_model_dict(without)


# ---------------------------------------------------------------------------
# diff_gpos
# ---------------------------------------------------------------------------


def test_diff_gpos_compares_the_documents_through_diff_fdeploy() -> None:
    old_doc = read_fdeploy(_policy_bytes())
    new_doc = read_fdeploy(_synthetic(r"\\elsewhere\share"))
    old = GPO(guid="g", name="n", fdeploy=old_doc)
    new = replace(old, fdeploy=new_doc)

    changes = diff_gpos(old, new).fdeploy
    assert changes == diff_fdeploy(old_doc, new_doc)
    (change,) = changes
    assert change.kind == "modified"
    assert change.new is not None and change.new.full_path == r"\\elsewhere\share"

    assert diff_gpos(old, old).fdeploy == ()
    assert diff_gpos(replace(old, fdeploy=None), replace(old, fdeploy=None)).fdeploy == ()


def test_a_document_appearing_or_disappearing_diffs_as_added_or_removed_rows() -> None:
    doc = read_fdeploy(_policy_bytes())
    present = GPO(guid="g", name="n", fdeploy=doc)
    absent = replace(present, fdeploy=None)
    assert [(c.kind, c.folder_guid) for c in diff_gpos(absent, present).fdeploy] == [
        ("added", _DOCUMENTS)
    ]
    assert [(c.kind, c.folder_guid) for c in diff_gpos(present, absent).fdeploy] == [
        ("removed", _DOCUMENTS)
    ]


def test_the_three_way_diff_the_workbench_calls_shows_rows_and_conflicts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`/api/estate/diff` is the GPO-to-GPO comparison the browser offers.

    Revisions of one GPO never differ in this field -- it is import provenance
    and no mutation touches it -- so comparing two imports is where a
    redirection change is actually seen.
    """
    draft_doc = read_fdeploy(_synthetic(r"\\server\a", folder=_OTHER_FOLDER))
    observed_doc = read_fdeploy(_synthetic(r"\\server\b", folder=_OTHER_FOLDER))
    with closing(WorkspaceStore(tmp_path / "diff.db")) as store:
        monkeypatch.setattr(app.state, "store", store, raising=False)
        monkeypatch.setattr(app.state, "owns_store", False, raising=False)
        baseline = store.create_gpo("Baseline", identity="t", reason="r")
        draft = store.create_gpo("Draft", identity="t", reason="r", fdeploy=draft_doc)
        observed = store.create_gpo("Observed", identity="t", reason="r", fdeploy=observed_doc)
        with TestClient(app) as client:
            response = client.post("/api/estate/diff", json={
                "baseline_guid": baseline.guid,
                "draft_guid": draft.guid,
                "observed_guid": observed.guid,
            })
    assert response.status_code == 200, response.text
    body = response.json()
    assert [(r["kind"], r["folder_guid"]) for r in body["fdeploy"]] == [
        ("added", _OTHER_FOLDER)
    ]
    (conflict,) = body["fdeploy_conflicts"]
    assert conflict["baseline"] is None
    assert conflict["draft"]["full_path"] == r"\\server\a"
    assert conflict["observed"]["full_path"] == r"\\server\b"


def test_the_same_change_on_both_sides_is_not_a_conflict() -> None:
    doc = read_fdeploy(_synthetic(r"\\server\a"))
    baseline = GPO(guid="b", name="b")
    same = GPO(guid="d", name="d", fdeploy=doc)
    result = three_way_diff(baseline, same, replace(same, guid="o"))
    assert len(result.fdeploy) == 1
    assert result.fdeploy_conflicts == ()


# ---------------------------------------------------------------------------
# Persistence of the stored form
# ---------------------------------------------------------------------------


def test_the_stored_form_is_rebuilt_from_its_text_not_from_its_views() -> None:
    doc = read_fdeploy(_policy_bytes())
    data = GPO(guid="g", name="n", fdeploy=doc).to_dict()
    data = json.loads(json.dumps(data))
    assert gpo_from_dict(data).fdeploy == doc
    # A view that disagrees with the text is recomputed, never believed.
    data["fdeploy"]["sections"] = []
    assert gpo_from_dict(data).fdeploy == doc


@pytest.mark.parametrize("stored", [[], {"sections": []}, {"raw_text": 5}])
def test_a_malformed_stored_document_is_refused(stored: object) -> None:
    with pytest.raises(StudioError):
        gpo_from_dict({"guid": "g", "name": "n", "fdeploy": stored})


def test_a_snapshot_written_before_the_field_existed_reads_back_without_it() -> None:
    assert gpo_from_dict({"guid": "g", "name": "n"}).fdeploy is None


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------


def test_gpmc_backup_refuses_a_gpo_carrying_the_document() -> None:
    """Refused, not dropped -- even with no unmodeled files to trip on first."""
    gpo = GPO(guid="11111111-2222-3333-4444-555555555555", name="Redirected",
              fdeploy=read_fdeploy(_policy_bytes()))
    assert gpo.cse_metadata == ()
    refusal = native_backup_refusal(gpo)
    assert refusal is not None and refusal.code == "folder_redirection_not_exportable"
    with pytest.raises(ValidationError) as caught:
        gpmc_backup_bundle(gpo)
    assert caught.value.issues[0].code == "folder_redirection_not_exportable"
    # The same GPO without it exports.
    assert gpmc_backup_bundle(replace(gpo, fdeploy=None))


def test_the_capability_advertises_the_gpmc_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    with closing(WorkspaceStore(tmp_path / "cap.db")) as store:
        monkeypatch.setattr(app.state, "store", store, raising=False)
        monkeypatch.setattr(app.state, "owns_store", False, raising=False)
        gpo = store.create_gpo(
            "Redirected", identity="t", reason="r", fdeploy=read_fdeploy(_policy_bytes()),
        )
        with TestClient(app) as client:
            payload = client.get(f"/api/gpos/{gpo.guid}").json()
            download = client.get(f"/api/gpos/{gpo.guid}/gpmc-backup")
    gpmc = payload["artifact_capabilities"]["gpmc_export"]
    assert gpmc["enabled"] is False
    assert "fdeploy1.ini" in gpmc["reason"]
    assert download.status_code == 422
    assert "folder_redirection_not_exportable" in download.text


def test_the_studio_bundle_retains_the_document_and_emits_no_fdeploy_file() -> None:
    """Same rule as backup_inventory: carried in the manifest, never emitted."""
    doc = read_fdeploy(_policy_bytes())
    gpo = GPO(guid="g", name="Redirected", fdeploy=doc)
    with zipfile.ZipFile(io.BytesIO(export_bundle(gpo))) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        names = archive.namelist()
    assert gpo_from_dict(manifest["gpo"]).fdeploy == doc
    assert "fdeploy" not in manifest["canonical_model"]
    assert not any("fdeploy" in name.casefold() for name in names)
