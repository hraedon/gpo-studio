"""Template ingest reads each file once, without following links, within limits.

Three properties of ``ingest_source``:

* the lock attests exactly the bytes the catalogue was built from (each file is
  read once, through one handle, and that read feeds both the hash and the
  parser);
* a template folder is read through the ``safe_io`` boundary backup imports
  use, so a symbolic link, junction or other reparse point anywhere in the
  folder refuses the whole source instead of being followed;
* a file over the per-file limit is refused after reading at most one byte
  past the limit, and a source over the aggregate byte or entry limit is
  refused outright rather than activated as a partial catalogue.

For a folder without links, ingest must be unchanged: the reference below is the
pre-change two-pass inventory, kept verbatim so the lock (which live lanes and
saved workspaces may bind) is compared byte for byte on every CI platform.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gpo_studio import template_store
from gpo_studio.admx import AdmxError, build_catalogue, find_adml
from gpo_studio.api import app
from gpo_studio.safe_io import SafeDirectoryEntry
from gpo_studio.store import WorkspaceStore
from gpo_studio.template_store import (
    TemplateError,
    TemplateFile,
    build_lock,
    classify_template_file,
    ingest_source,
)

_ADMX = """\
<?xml version="1.0" encoding="utf-8"?>
<policyDefinitions revision="1.0" schemaVersion="1.0"
    xmlns="http://schemas.microsoft.com/GroupPolicy/2006/07/PolicyDefinitions">
  <policyNamespaces>
    <target prefix="p" namespace="{namespace}" />
  </policyNamespaces>
  <resources minRequiredRevision="1.0" />
  <categories>
    <category name="Cat" displayName="$(string.Cat)" />
  </categories>
  <policies>
    <policy name="{policy}" class="Machine" displayName="$(string.{policy})"
            explainText="$(string.{policy}_help)" key="Software\\Policies\\Synthetic"
            valueName="{policy}">
      <parentCategory ref="Cat" />
      <enabledValue><decimal value="1" /></enabledValue>
      <disabledValue><decimal value="0" /></disabledValue>
    </policy>
  </policies>
</policyDefinitions>
"""

_ADML = """\
<?xml version="1.0" encoding="utf-8"?>
<policyDefinitionResources revision="1.0" schemaVersion="1.0"
    xmlns="http://schemas.microsoft.com/GroupPolicy/2006/07/PolicyDefinitions">
  <displayName>Synthetic</displayName>
  <description>Synthetic</description>
  <resources>
    <stringTable>
      <string id="Cat">Category</string>
      <string id="{policy}">{policy} {locale}</string>
      <string id="{policy}_help">Help text</string>
    </stringTable>
  </resources>
</policyDefinitionResources>
"""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_admx(folder: Path, name: str, namespace: str, policy: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(
        _ADMX.format(namespace=namespace, policy=policy), encoding="utf-8"
    )


def _write_adml(folder: Path, name: str, policy: str, locale: str = "en-US") -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(_ADML.format(policy=policy, locale=locale), encoding="utf-8")


def _write_pair(folder: Path, stem: str, namespace: str, policy: str) -> None:
    _write_admx(folder, f"{stem}.admx", namespace, policy)
    _write_adml(folder / "en-US", f"{stem}.adml", policy)


# --- The pre-change inventory and catalogue passes, kept as the reference ------


def _reference_files(directory: Path) -> tuple[TemplateFile, ...]:
    """Verbatim pre-change ``_scan_directory`` (path-based, link-following)."""
    limit = 10 * 1024 * 1024
    microsoft_names = template_store._MICROSOFT_FILE_NAMES
    has_copyright = template_store._has_microsoft_copyright
    files: list[TemplateFile] = []
    seen_adml: set[Path] = set()
    for path in sorted(directory.glob("*.admx")):
        try:
            if path.stat().st_size > limit:
                continue
            admx_data = path.read_bytes()
        except OSError:
            continue
        relative = str(path.relative_to(directory))
        adml_path = find_adml(path)
        adml_data: bytes | None = None
        if adml_path is not None:
            try:
                if adml_path.stat().st_size <= limit:
                    adml_data = adml_path.read_bytes()
                    seen_adml.add(adml_path)
            except OSError:
                pass
        classification, note = classify_template_file(relative, admx_data, adml_data)
        files.append(TemplateFile(relative, _sha(admx_data), len(admx_data), classification, note))
        if adml_path is not None and adml_data is not None:
            adml_relative = str(adml_path.relative_to(directory))
            adml_classification = classification
            adml_note = note
            if adml_path.name.lower() in microsoft_names or has_copyright(adml_data):
                adml_classification = "hash-reference"
                adml_note = f"Microsoft indicator in paired ADML {adml_relative}"
            files.append(TemplateFile(
                adml_relative, _sha(adml_data), len(adml_data), adml_classification, adml_note
            ))
    adml_paths = sorted(directory.glob("*.adml"))
    for child in sorted(directory.iterdir()):
        if child.is_dir():
            adml_paths.extend(sorted(child.glob("*.adml")))
    for path in adml_paths:
        if path in seen_adml:
            continue
        try:
            if path.stat().st_size > limit:
                continue
            data = path.read_bytes()
        except OSError:
            continue
        relative = str(path.relative_to(directory))
        classification, note = classify_template_file(relative, None, data)
        files.append(TemplateFile(relative, _sha(data), len(data), classification, note))
    return tuple(files)


def _reference_catalogue(directory: Path) -> tuple[tuple[str, ...], tuple[tuple[str, str], ...]]:
    """Pre-change catalogue pass: (qualified policy IDs, (path, error kind))."""
    policies: list[str] = []
    errors: list[tuple[str, str]] = []
    for admx_path in sorted(directory.glob("*.admx")):
        rel = str(admx_path.relative_to(directory))
        adml_path = find_adml(admx_path)
        if adml_path is None:
            errors.append((rel, "missing_adml"))
            continue
        try:
            catalogue = build_catalogue(admx_path.read_bytes(), adml_path.read_bytes())
        except (AdmxError, ValueError):
            errors.append((rel, "parse_error"))
            continue
        policies.extend(p.qualified_id for p in catalogue.policies)
    return tuple(policies), tuple(errors)


def _realistic_store(root: Path, locale_dir: str = "en-US") -> Path:
    """A synthetic store exercising every pairing and inventory rule."""
    store = root / "PolicyDefinitions"
    _write_admx(store, "alpha.admx", "Synthetic.Test.Alpha", "AlphaPolicy")
    _write_admx(store, "vendor.admx", "Synthetic.Policies.Vendor", "VendorPolicy")
    # The ADML's case differs from the ADMX stem, as in a real Windows store.
    _write_admx(store, "casefold.admx", "Synthetic.Test.Casefold", "CasefoldPolicy")
    # Paired from a sibling in the folder itself.
    _write_admx(store, "sibling.admx", "Synthetic.Test.Sibling", "SiblingPolicy")
    _write_adml(store, "sibling.adml", "SiblingPolicy")
    # Paired only from a non-default locale (the sorted-locale fallback).
    _write_admx(store, "german.admx", "Synthetic.Test.German", "GermanPolicy")
    _write_admx(store, "orphan.admx", "Synthetic.Test.Orphan", "OrphanPolicy")
    (store / "broken.admx").write_text("not xml", encoding="utf-8")
    _write_adml(store, "loose.adml", "LoosePolicy")
    (store / "readme.txt").write_text("synthetic", encoding="utf-8")
    locale = store / locale_dir
    _write_adml(locale, "alpha.adml", "AlphaPolicy")
    _write_adml(locale, "vendor.adml", "VendorPolicy")
    _write_adml(locale, "CaseFold.adml", "CasefoldPolicy")
    (locale / "broken.adml").write_text("not xml either", encoding="utf-8")
    (locale / "unpaired.adml").write_text(
        "<!-- Copyright (c) Microsoft Corporation -->\n<r/>", encoding="utf-8"
    )
    _write_adml(store / "fr-FR", "alpha.adml", "AlphaPolicy", "fr-FR")
    _write_adml(store / "fr-FR", "vendor.adml", "VendorPolicy", "fr-FR")
    _write_adml(store / "de-DE", "german.adml", "GermanPolicy", "de-DE")
    (store / "de-DE" / "nested").mkdir()
    return store


@pytest.mark.parametrize("locale_dir", ["en-US", "EN-us"])
def test_ingest_of_a_link_free_store_matches_the_previous_reader(
    tmp_path: Path, locale_dir: str
) -> None:
    store = _realistic_store(tmp_path, locale_dir)
    result = ingest_source("store", "central-store", store)

    reference = _reference_files(store)
    assert result.source.files == reference
    assert build_lock((result,)) == template_store.TemplateLock(
        source_hashes=(("store", template_store._hash_files(reference)),)
    )
    policies, errors = _reference_catalogue(store)
    assert tuple(p.qualified_id for p in result.catalogue.policies) == policies
    assert tuple((e.relative_path, e.kind) for e in result.errors) == errors
    # The fixture really exercises the rules it is here for.
    assert "Synthetic.Test.German:GermanPolicy" in policies
    assert "Synthetic.Test.Casefold:CasefoldPolicy" in policies
    assert ("orphan.admx", "missing_adml") in errors
    assert ("broken.admx", "parse_error") in errors
    assert any(f.relative_path.startswith("fr-FR") for f in reference)


def test_ingest_of_the_shipped_fixture_store_matches_the_previous_reader() -> None:
    store = Path(__file__).parent / "fixtures" / "admx"
    result = ingest_source("default", "local", store)
    assert result.source.files == _reference_files(store)


# --- Finding: the lock hashed different bytes from those loaded ----------------


def test_lock_attests_the_bytes_the_catalogue_was_built_from(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deterministic swap probe: replace the pair after the first read.

    The previous reader hashed one read and parsed a second, so a pair swapped
    in between was loaded while the lock still attested the reviewed bytes.
    """
    store = tmp_path / "store"
    _write_pair(store, "vendor", "Synthetic.Policies.Reviewed", "ReviewedPolicy")
    swap = tmp_path / "swap"
    _write_pair(swap, "vendor", "Synthetic.Policies.Swapped", "SwappedPolicy")
    reviewed = ingest_source("vendor", "vendor-pack", store)
    relatives = ("vendor.admx", str(Path("en-US", "vendor.adml")))

    original_classify = template_store.classify_template_file
    swapped: list[bool] = []

    def classify_then_swap(*args: object, **kwargs: object) -> object:
        if not swapped:
            try:
                # ADML first: on Windows the handle enumeration holds shares
                # read only, so the write is refused while that file is open
                # (itself the property under test); swap at the next call.
                for relative in reversed(relatives):
                    (store / relative).write_bytes((swap / relative).read_bytes())
            except PermissionError:
                assert sys.platform == "win32"
            else:
                swapped.append(True)
        return original_classify(*args, **kwargs)  # type: ignore[arg-type]

    original_build = template_store.build_catalogue
    parsed: list[tuple[str, str]] = []

    def recording_build(admx_data: bytes, adml_data: bytes) -> object:
        parsed.append((_sha(admx_data), _sha(adml_data)))
        return original_build(admx_data, adml_data)

    monkeypatch.setattr(template_store, "classify_template_file", classify_then_swap)
    monkeypatch.setattr(template_store, "build_catalogue", recording_build)
    result = ingest_source("vendor", "vendor-pack", store)

    assert swapped, "the probe never ran"
    hashes = {f.relative_path: f.sha256 for f in result.source.files}
    assert parsed == [(hashes[relatives[0]], hashes[relatives[1]])]
    assert [p.qualified_id for p in result.catalogue.policies] == [
        "Synthetic.Policies.Reviewed:ReviewedPolicy"
    ]
    assert build_lock((result,)) == build_lock((reviewed,))


def test_each_template_file_is_opened_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _realistic_store(tmp_path)
    opened: list[str] = []
    original = template_store.iter_directory

    def recording_iter(dir_fd: int) -> Iterator[SafeDirectoryEntry]:
        for entry in original(dir_fd):
            opened.append(entry.name)
            yield entry

    monkeypatch.setattr(template_store, "iter_directory", recording_iter)
    monkeypatch.setattr(Path, "read_bytes", _forbidden_read_bytes)
    ingest_source("store", "central-store", store)
    templates = sorted(name for name in opened if name.lower().endswith((".admx", ".adml")))
    assert templates == sorted(
        p.name for p in store.rglob("*") if p.suffix.lower() in (".admx", ".adml")
    )


def _forbidden_read_bytes(self: Path) -> bytes:
    raise AssertionError(f"template ingest reopened {self} by path")


# --- Finding: links and reparse points inside the template folder --------------


def _outside_pair(tmp_path: Path) -> Path:
    outside = tmp_path / "outside"
    _write_pair(outside, "escape", "Synthetic.Test.Escape", "EscapedPolicy")
    return outside


@pytest.mark.usefixtures("symlink_privilege")
def test_symlinked_admx_outside_the_folder_is_refused(tmp_path: Path) -> None:
    outside = _outside_pair(tmp_path)
    store = tmp_path / "store"
    _write_pair(store, "good", "Synthetic.Test.Good", "GoodPolicy")
    (store / "escape.admx").symlink_to(outside / "escape.admx")
    (store / "en-US" / "escape.adml").symlink_to(outside / "en-US" / "escape.adml")

    with pytest.raises(TemplateError, match="Refused template folder") as refusal:
        ingest_source("store", "local", store)
    assert "link" in str(refusal.value).lower()


@pytest.mark.usefixtures("symlink_privilege")
def test_symlinked_adml_is_refused(tmp_path: Path) -> None:
    outside = _outside_pair(tmp_path)
    store = tmp_path / "store"
    _write_admx(store, "escape.admx", "Synthetic.Test.Escape", "EscapedPolicy")
    (store / "en-US").mkdir()
    (store / "en-US" / "escape.adml").symlink_to(outside / "en-US" / "escape.adml")

    with pytest.raises(TemplateError, match="Refused template folder"):
        ingest_source("store", "local", store)


@pytest.mark.usefixtures("symlink_privilege")
def test_symlinked_locale_folder_is_refused(tmp_path: Path) -> None:
    outside = _outside_pair(tmp_path)
    store = tmp_path / "store"
    _write_admx(store, "escape.admx", "Synthetic.Test.Escape", "EscapedPolicy")
    (store / "en-US").symlink_to(outside / "en-US", target_is_directory=True)

    with pytest.raises(TemplateError, match="Refused template folder"):
        ingest_source("store", "local", store)


@pytest.mark.usefixtures("symlink_privilege")
def test_any_link_in_the_folder_refuses_the_source(tmp_path: Path) -> None:
    """Strict, like backup imports: an unrelated link is not skipped either."""
    store = tmp_path / "store"
    _write_pair(store, "good", "Synthetic.Test.Good", "GoodPolicy")
    (tmp_path / "notes.txt").write_text("synthetic", encoding="utf-8")
    (store / "notes.txt").symlink_to(tmp_path / "notes.txt")

    with pytest.raises(TemplateError, match="Refused template folder"):
        ingest_source("store", "local", store)


@pytest.mark.usefixtures("symlink_privilege")
def test_template_folder_reached_through_a_link_is_refused(tmp_path: Path) -> None:
    """``ingest_source`` itself never follows a link; callers resolve first."""
    store = tmp_path / "store"
    _write_pair(store, "good", "Synthetic.Test.Good", "GoodPolicy")
    alias = tmp_path / "alias"
    alias.symlink_to(store, target_is_directory=True)

    with pytest.raises(TemplateError, match="Refused template folder"):
        ingest_source("store", "local", alias)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows junction test")
def test_junction_locale_folder_is_refused(tmp_path: Path) -> None:
    outside = _outside_pair(tmp_path)
    store = tmp_path / "store"
    _write_admx(store, "escape.admx", "Synthetic.Test.Escape", "EscapedPolicy")
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(store / "en-US"), str(outside / "en-US")],
        check=True,
        capture_output=True,
    )

    with pytest.raises(TemplateError, match="Refused template folder"):
        ingest_source("store", "local", store)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows junction test")
def test_template_folder_reached_through_a_junction_is_refused(tmp_path: Path) -> None:
    store = tmp_path / "store"
    _write_pair(store, "good", "Synthetic.Test.Good", "GoodPolicy")
    junction = tmp_path / "junction"
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(store)],
        check=True,
        capture_output=True,
    )

    with pytest.raises(TemplateError, match="Refused template folder"):
        ingest_source("store", "local", junction)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX special-file test")
def test_special_file_named_like_a_template_is_refused_without_blocking(
    tmp_path: Path,
) -> None:
    store = tmp_path / "store"
    _write_pair(store, "good", "Synthetic.Test.Good", "GoodPolicy")
    os.mkfifo(store / "pipe.admx")

    with pytest.raises(TemplateError, match="Refused template folder"):
        ingest_source("store", "local", store)


# --- Finding: size limits ------------------------------------------------------


def _count_os_reads(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    sizes: list[int] = []
    original = os.read

    def counting_read(fd: int, length: int) -> bytes:
        chunk = original(fd, length)
        sizes.append(len(chunk))
        return chunk

    monkeypatch.setattr(os, "read", counting_read)
    return sizes


@pytest.mark.parametrize("oversized", ["big.admx", "en-US/big.adml", "fr-FR/other.adml"])
def test_file_over_the_size_limit_refuses_the_source_after_a_bounded_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, oversized: str
) -> None:
    store = tmp_path / "store"
    _write_pair(store, "big", "Synthetic.Test.Big", "BigPolicy")
    limit = template_store._MAX_TEMPLATE_FILE_SIZE
    target = store / oversized
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("wb") as handle:
        handle.write(b"<r>")
        handle.write(b" " * (3 * limit))
    small = sum(
        p.stat().st_size for p in store.rglob("*") if p.is_file() and p != target
    )
    reads = _count_os_reads(monkeypatch)

    with pytest.raises(TemplateError, match="larger than the 10485760-byte limit"):
        ingest_source("store", "local", store)
    assert sum(reads) <= small + limit + 1


def test_file_at_the_size_limit_is_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = tmp_path / "store"
    _write_pair(store, "edge", "Synthetic.Test.Edge", "EdgePolicy")
    admx = store / "edge.admx"
    size = admx.stat().st_size
    assert (store / "en-US" / "edge.adml").stat().st_size <= size
    monkeypatch.setattr(template_store, "_MAX_TEMPLATE_FILE_SIZE", size)

    result = ingest_source("store", "local", store)
    assert result.source.files[0].size == size
    assert result.catalogue.policies

    admx.write_bytes(admx.read_bytes() + b" ")
    with pytest.raises(TemplateError, match=f"larger than the {size}-byte limit"):
        ingest_source("store", "local", store)


def test_source_over_the_aggregate_byte_limit_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _realistic_store(tmp_path)
    total = sum(
        p.stat().st_size
        for p in store.rglob("*")
        if p.is_file() and p.suffix in (".admx", ".adml")
    )
    monkeypatch.setattr(template_store, "_MAX_TEMPLATE_SOURCE_BYTES", total, raising=False)
    assert ingest_source("store", "local", store).source.files

    monkeypatch.setattr(template_store, "_MAX_TEMPLATE_SOURCE_BYTES", total - 1, raising=False)
    with pytest.raises(TemplateError, match=f"more than {total - 1} bytes"):
        ingest_source("store", "local", store)


def test_source_over_the_entry_limit_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _realistic_store(tmp_path)
    # The folder and its immediate subfolders are enumerated; de-DE/nested is
    # counted as an entry of de-DE but never entered (it is empty here anyway).
    entries = sum(1 for _ in store.rglob("*"))
    monkeypatch.setattr(template_store, "_MAX_TEMPLATE_SOURCE_ENTRIES", entries, raising=False)
    assert ingest_source("store", "local", store).source.files

    monkeypatch.setattr(
        template_store, "_MAX_TEMPLATE_SOURCE_ENTRIES", entries - 1, raising=False
    )
    with pytest.raises(TemplateError, match=f"more than {entries - 1} files and folders"):
        ingest_source("store", "local", store)


def test_aggregate_limits_leave_headroom_over_a_measured_windows_store() -> None:
    # C:\Windows\PolicyDefinitions on Windows Server 2025 (10.0.26100), measured
    # 2026-10-29: 223 ADMX + 223 en-US ADML = 446 files, 7,382,895 bytes, and
    # 224 + 223 = 447 directory entries. The limits must not be the reason a
    # real store (or one with several locales and vendor packs) is refused.
    assert template_store._MAX_TEMPLATE_SOURCE_BYTES >= 50 * 7_382_895
    assert template_store._MAX_TEMPLATE_SOURCE_ENTRIES >= 40 * 447
    assert template_store._MAX_TEMPLATE_FILE_SIZE == 10 * 1024 * 1024


# --- API: refusals are clear and leave the active catalogue alone --------------


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    store = WorkspaceStore(tmp_path / "workspace.db")
    monkeypatch.setattr(app.state, "store", store, raising=False)
    monkeypatch.setattr(app.state, "owns_store", False, raising=False)
    monkeypatch.setenv("GPO_STUDIO_ADMX_DIR", str(Path(__file__).parent / "fixtures" / "admx"))
    monkeypatch.setenv("GPO_STUDIO_TEMPLATE_ROOT", str(tmp_path / "root"))
    names = ("admx_catalogue", "template_sources", "catalogue_snapshot")
    for name in names:
        monkeypatch.delattr(app.state, name, raising=False)
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        store.close()
        for name in names:
            if hasattr(app.state, name):
                delattr(app.state, name)


def _refused_ingest_keeps_catalogue(client: TestClient, folder: Path, expected: str) -> None:
    before = client.get("/api/templates").json()
    sources = app.state.template_sources
    response = client.post(
        "/api/templates/ingest",
        json={"name": "default", "kind": "local", "path": str(folder)},
    )
    assert response.status_code == 422, response.text
    assert expected in response.json()["error"]["message"]
    assert app.state.template_sources is sources
    assert client.get("/api/templates").json() == before


@pytest.mark.usefixtures("symlink_privilege")
def test_api_refuses_a_linked_template_inside_the_template_root(
    client: TestClient, tmp_path: Path
) -> None:
    outside = _outside_pair(tmp_path)
    folder = tmp_path / "root" / "vendor"
    _write_pair(folder, "good", "Synthetic.Test.Good", "GoodPolicy")
    (folder / "escape.admx").symlink_to(outside / "escape.admx")
    (folder / "en-US" / "escape.adml").symlink_to(outside / "en-US" / "escape.adml")
    _refused_ingest_keeps_catalogue(client, folder, "Refused template folder")


def test_api_refuses_a_source_over_a_limit_without_a_partial_catalogue(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = tmp_path / "root" / "vendor"
    _write_pair(folder, "first", "Synthetic.Test.First", "FirstPolicy")
    _write_pair(folder, "second", "Synthetic.Test.Second", "SecondPolicy")
    monkeypatch.setattr(template_store, "_MAX_TEMPLATE_SOURCE_ENTRIES", 3, raising=False)
    _refused_ingest_keeps_catalogue(client, folder, "more than 3 files and folders")
