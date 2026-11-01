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
from typing import Any

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


# --- Review round 1 ------------------------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX FIFO test")
def test_ingest_does_not_hang_on_a_fifo_swapped_in_before_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A template replaced by a FIFO between inspection and open is refused.

    Startup ingests the configured folder, so a blocked open kept the server
    from coming up at all.
    """
    import threading
    from types import ModuleType

    from gpo_studio import safe_io

    store = tmp_path / "store"
    _write_pair(store, "victim", "Synthetic.Test.Victim", "VictimPolicy")
    victim = store / "victim.admx"
    real_os = os

    class SwappingOs(ModuleType):
        def __getattr__(self, name: str) -> Any:
            return getattr(real_os, name)

    swapping = SwappingOs("os")

    def swap_then_open(path: Any, flags: int, *args: Any, dir_fd: Any = None, **kw: Any) -> int:
        if path == victim.name and dir_fd is not None and not victim.is_fifo():
            real_os.unlink(path, dir_fd=dir_fd)
            real_os.mkfifo(path, dir_fd=dir_fd)
        return real_os.open(path, flags, *args, dir_fd=dir_fd, **kw)

    swapping.open = swap_then_open  # type: ignore[attr-defined]
    monkeypatch.setattr(safe_io, "os", swapping)
    outcome: dict[str, object] = {}

    def run() -> None:
        try:
            outcome["result"] = ingest_source("store", "local", store)
        except BaseException as error:  # noqa: BLE001 - reported to the test
            outcome["error"] = error

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(5)
    if worker.is_alive():
        writer = real_os.open(victim, os.O_WRONLY | os.O_NONBLOCK)
        real_os.close(writer)
        worker.join(5)
        pytest.fail("ingest blocked opening a FIFO")
    assert isinstance(outcome.get("error"), TemplateError), outcome
    assert "Refused template folder" in str(outcome["error"])


@pytest.mark.parametrize("swapped", ["root", "locale"])
def test_locale_folders_are_read_from_the_tree_that_was_enumerated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, swapped: str
) -> None:
    """Swap the root or its locale folder after the root is enumerated.

    Locale folders are opened through the handles root enumeration produced,
    so an ADMX from one tree is never paired with an ADML from another.
    """
    store = tmp_path / "store"
    _write_pair(store, "vendor", "Synthetic.Policies.TreeA", "TreeAPolicy")
    other = tmp_path / "other"
    _write_pair(other, "vendor", "Synthetic.Policies.TreeB", "TreeBPolicy")
    tree_a_adml = _sha((store / "en-US" / "vendor.adml").read_bytes())
    assert tree_a_adml != _sha((other / "en-US" / "vendor.adml").read_bytes())

    original = template_store.iter_directory
    calls: list[int] = []

    def swap_after_root(dir_fd: int) -> Iterator[SafeDirectoryEntry]:
        calls.append(dir_fd)
        first = len(calls) == 1
        yield from original(dir_fd)
        if not first:
            return
        try:
            if swapped == "root":
                store.rename(tmp_path / "moved")
                other.rename(store)
            else:
                (store / "en-US").rename(tmp_path / "moved-locale")
                (other / "en-US").rename(store / "en-US")
        except PermissionError:
            # Windows: the retained handles deny the rename, the same property.
            assert sys.platform == "win32"

    monkeypatch.setattr(template_store, "iter_directory", swap_after_root)
    result = ingest_source("store", "local", tmp_path / "store")
    hashes = {f.relative_path: f.sha256 for f in result.source.files}
    assert hashes[str(Path("en-US", "vendor.adml"))] == tree_a_adml
    assert [p.qualified_id for p in result.catalogue.policies] == [
        "Synthetic.Policies.TreeA:TreeAPolicy"
    ]


def test_source_over_the_subfolder_limit_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = tmp_path / "store"
    _write_pair(store, "good", "Synthetic.Test.Good", "GoodPolicy")
    (store / "fr-FR").mkdir()
    monkeypatch.setattr(template_store, "_MAX_TEMPLATE_SUBFOLDERS", 2, raising=False)
    assert ingest_source("store", "local", store).source.files

    (store / "de-DE").mkdir()
    with pytest.raises(TemplateError, match="more than 2 subfolders"):
        ingest_source("store", "local", store)


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("0xC0000043", "Cannot read template folder"),  # sharing violation
        ("0xC0000022", "Cannot read template folder"),  # access denied
        ("0x8000002D", "Refused template folder"),  # stopped on symlink
        ("0xC000050B", "Refused template folder"),  # reparse point encountered
    ],
)
def test_windows_open_failures_are_reported_by_cause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: str, expected: str
) -> None:
    store = tmp_path / "store"
    _write_pair(store, "good", "Synthetic.Test.Good", "GoodPolicy")

    def failing(dir_fd: int) -> Iterator[SafeDirectoryEntry]:
        raise template_store.SafeOpenError(
            f"Cannot open directory entry 'good.admx' safely: NT status {status}",
            nt_status=int(status, 16),
        )
        yield  # pragma: no cover - makes this a generator

    monkeypatch.setattr(template_store, "iter_directory", failing)
    with pytest.raises(TemplateError) as refusal:
        ingest_source("store", "local", store)
    message = str(refusal.value)
    assert message.startswith(expected), message
    assert status in message
    if expected.startswith("Cannot read"):
        assert "link" not in message.lower()


@pytest.mark.usefixtures("symlink_privilege")
def test_api_does_not_disclose_where_an_escaping_path_resolves(
    client: TestClient, tmp_path: Path
) -> None:
    secret = tmp_path / "outside-secret-folder"
    _write_pair(secret, "good", "Synthetic.Test.Good", "GoodPolicy")
    (tmp_path / "root").mkdir()
    alias = tmp_path / "root" / "alias"
    alias.symlink_to(secret, target_is_directory=True)
    response = client.post(
        "/api/templates/ingest",
        json={"name": "default", "kind": "local", "path": str(alias)},
    )
    assert response.status_code == 422, response.text
    message = response.json()["error"]["message"]
    assert "escapes template root" in message
    assert "outside-secret-folder" not in message


@pytest.mark.parametrize(
    "name",
    ["NT status 0xC0000022.admx", "Win32 error 5.admx", "NT status 0xC0000043"],
)
def test_refusal_classification_ignores_entry_names_that_look_like_statuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    """The cause is the status the error carries, never text in its message.

    A link refusal names the entry, so an entry named like a status code
    must not turn the refusal into a read failure.
    """
    store = tmp_path / "store"
    _write_pair(store, "good", "Synthetic.Test.Good", "GoodPolicy")

    def failing(dir_fd: int) -> Iterator[SafeDirectoryEntry]:
        raise template_store.SafeOpenError(f"Link found in directory entry {name!r}")
        yield  # pragma: no cover - makes this a generator

    monkeypatch.setattr(template_store, "iter_directory", failing)
    with pytest.raises(TemplateError, match="^Refused template folder"):
        ingest_source("store", "local", store)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows sharing-violation test")
def test_a_template_held_open_without_read_sharing_is_reported_as_unreadable(
    tmp_path: Path,
) -> None:
    import ctypes

    store = tmp_path / "store"
    _write_pair(store, "held", "Synthetic.Test.Held", "HeldPolicy")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    kernel32.CreateFileW.restype = ctypes.c_void_p
    kernel32.CreateFileW.argtypes = (
        ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
        ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
    )
    kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
    # GENERIC_READ, share mode 0, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL.
    handle = kernel32.CreateFileW(str(store / "held.admx"), 0x80000000, 0, None, 3, 0x80, None)
    assert handle not in (None, ctypes.c_void_p(-1).value)
    try:
        with pytest.raises(TemplateError) as refusal:
            ingest_source("store", "local", store)
    finally:
        kernel32.CloseHandle(handle)
    assert str(refusal.value).startswith("Cannot read template folder")
    cause = refusal.value.__cause__
    assert isinstance(cause, template_store.SafeOpenError)
    assert cause.nt_status == 0xC0000043  # STATUS_SHARING_VIOLATION


# --- Review round 2 ------------------------------------------------------------


def _post_ingest(client: TestClient, path: Path | str, name: str = "default") -> Any:
    return client.post(
        "/api/templates/ingest", json={"name": name, "kind": "local", "path": str(path)}
    )


@pytest.mark.usefixtures("symlink_privilege")
@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "linked-entry",
        "oversized-file",
        "byte-limit",
        "entry-limit",
        "subfolder-limit",
        "windows-open-failure",
        "windows-link-status",
    ],
)
def test_no_refusal_discloses_where_the_submitted_path_resolves(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    """Every 422 an ingest can return names the submitted path, never its target.

    Swept with no template root configured, so nothing refuses the path
    before ingest reads it, through an alias that resolves somewhere else.
    """
    monkeypatch.delenv("GPO_STUDIO_TEMPLATE_ROOT")
    secret = tmp_path / "resolved-secret-target"
    alias = tmp_path / "submitted-alias"
    if case != "missing":
        _write_pair(secret, "good", "Synthetic.Test.Good", "GoodPolicy")
    alias.symlink_to(secret, target_is_directory=True)
    if case == "linked-entry":
        (tmp_path / "elsewhere.admx").write_text("synthetic", encoding="utf-8")
        (secret / "linked.admx").symlink_to(tmp_path / "elsewhere.admx")
    elif case == "oversized-file":
        monkeypatch.setattr(template_store, "_MAX_TEMPLATE_FILE_SIZE", 16)
    elif case == "byte-limit":
        monkeypatch.setattr(template_store, "_MAX_TEMPLATE_SOURCE_BYTES", 16)
    elif case == "entry-limit":
        monkeypatch.setattr(template_store, "_MAX_TEMPLATE_SOURCE_ENTRIES", 1)
    elif case == "subfolder-limit":
        monkeypatch.setattr(template_store, "_MAX_TEMPLATE_SUBFOLDERS", 0)
    elif case.startswith("windows-"):
        status = 0xC0000022 if case == "windows-open-failure" else 0x8000002D
        resolved = str(secret.resolve())

        def failing(dir_fd: int) -> Iterator[SafeDirectoryEntry]:
            # safe_io's Windows walk names the full path it failed to open.
            raise template_store.SafeOpenError(
                f"CreateFileW failed for {resolved}: NT status 0x{status:08X}",
                nt_status=status,
            )
            yield  # pragma: no cover - makes this a generator

        monkeypatch.setattr(template_store, "iter_directory", failing)

    response = _post_ingest(client, alias)
    assert response.status_code == 422, response.text
    message = response.json()["error"]["message"]
    assert "resolved-secret-target" not in message, message
    # The only absolute path a message may name is the one submitted.
    assert str(tmp_path) not in message.replace(str(alias), ""), message


@pytest.fixture
def blocking_ingest(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """Make the next ingest wait until the test releases it."""
    import threading

    started = threading.Event()
    release = threading.Event()
    original = template_store.ingest_source
    threads: list[str] = []

    def held(*args: Any, **kwargs: Any) -> template_store.IngestResult:
        threads.append(threading.current_thread().name)
        started.set()
        assert release.wait(timeout=30)
        return original(*args, **kwargs)

    monkeypatch.setattr(template_store, "ingest_source", held)
    gate = type("Gate", (), {"started": started, "release": release, "threads": threads})
    try:
        yield gate
    finally:
        release.set()


def test_a_second_ingest_while_one_runs_is_refused_with_409(
    client: TestClient, tmp_path: Path, blocking_ingest: Any
) -> None:
    """Never queued: a running ingest cannot be cancelled and has no time bound."""
    import threading

    folder = tmp_path / "root" / "vendor"
    _write_pair(folder, "good", "Synthetic.Test.Good", "GoodPolicy")
    first: dict[str, Any] = {}
    worker = threading.Thread(
        target=lambda: first.setdefault("response", _post_ingest(client, folder, "one"))
    )
    worker.start()
    assert blocking_ingest.started.wait(timeout=10)

    second: dict[str, Any] = {}
    racer = threading.Thread(
        target=lambda: second.setdefault("response", _post_ingest(client, folder, "two"))
    )
    racer.start()
    racer.join(timeout=10)
    queued = racer.is_alive()
    blocking_ingest.release.set()
    worker.join(timeout=30)
    racer.join(timeout=30)
    assert not queued, "the second ingest waited instead of being refused"
    assert second["response"].status_code == 409, second["response"].text
    error = second["response"].json()["error"]
    assert error["code"] == "template_ingest_in_progress"
    assert first["response"].status_code == 201, first["response"].text
    names = {item["name"] for item in client.get("/api/templates").json()["items"]}
    assert "one" in names and "two" not in names
    assert _post_ingest(client, folder, "two").status_code == 201


def test_every_ingest_runs_on_the_one_dedicated_ingest_thread(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One thread for all ingests, so they share one allocator arena."""
    import threading

    folder = tmp_path / "root" / "vendor"
    _write_pair(folder, "good", "Synthetic.Test.Good", "GoodPolicy")
    original = template_store.ingest_source
    seen: list[tuple[str, int]] = []

    def recording(*args: Any, **kwargs: Any) -> template_store.IngestResult:
        thread = threading.current_thread()
        seen.append((thread.name, thread.ident or 0))
        return original(*args, **kwargs)

    monkeypatch.setattr(template_store, "ingest_source", recording)
    for index in range(4):
        assert _post_ingest(client, folder, f"src{index}").status_code == 201
    for name in ("admx_catalogue", "template_sources", "catalogue_snapshot"):
        monkeypatch.delattr(app.state, name, raising=False)
    with TestClient(app):  # the startup ingest runs there too
        pass
    assert len(seen) == 5
    assert {ident for _, ident in seen} == {seen[0][1]}
    assert all(name.startswith("gpo-studio-template-ingest") for name, _ in seen), seen


# --- Review round 3 ------------------------------------------------------------


def _assert_names_only(body: str, alias: Path, tmp_path: Path) -> None:
    """No part of the resolved target, and no absolute path but the alias."""
    assert "resolved-secret-target" not in body, body
    assert str(tmp_path) not in body.replace(str(alias), ""), body


@pytest.mark.usefixtures("symlink_privilege")
@pytest.mark.parametrize("root", ["unset", "set"])
def test_a_successful_ingest_reports_the_submitted_path_not_its_target(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, root: str
) -> None:
    """The 201 body and the source list name the submitted path, never its target.

    With a template root set, both the alias and its target lie inside it.
    """
    base = tmp_path / "root"
    if root == "unset":
        monkeypatch.delenv("GPO_STUDIO_TEMPLATE_ROOT")
        base = tmp_path
    secret = base / "resolved-secret-target"
    _write_pair(secret, "good", "Synthetic.Test.Good", "GoodPolicy")
    alias = base / "submitted-alias"
    alias.symlink_to(secret, target_is_directory=True)

    response = _post_ingest(client, alias, "vendor")
    assert response.status_code == 201, response.text
    assert response.json()["path"] == str(alias)
    _assert_names_only(response.text, alias, tmp_path)
    listing = client.get("/api/templates")
    vendor = next(item for item in listing.json()["items"] if item["name"] == "vendor")
    assert vendor["path"] == str(alias)
    _assert_names_only(listing.text, alias, tmp_path)


@pytest.mark.usefixtures("symlink_privilege")
def test_the_startup_source_reports_the_configured_path_not_its_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = tmp_path / "resolved-secret-target"
    _write_pair(secret, "good", "Synthetic.Test.Good", "GoodPolicy")
    alias = tmp_path / "submitted-alias"
    alias.symlink_to(secret, target_is_directory=True)
    store = WorkspaceStore(tmp_path / "workspace.db")
    monkeypatch.setattr(app.state, "store", store, raising=False)
    monkeypatch.setattr(app.state, "owns_store", False, raising=False)
    monkeypatch.setenv("GPO_STUDIO_ADMX_DIR", str(alias))
    names = ("admx_catalogue", "template_sources", "catalogue_snapshot")
    for name in names:
        monkeypatch.delattr(app.state, name, raising=False)
    try:
        with TestClient(app) as started:
            listing = started.get("/api/templates")
    finally:
        store.close()
        for name in names:
            if hasattr(app.state, name):
                delattr(app.state, name)
    assert [item["path"] for item in listing.json()["items"]] == [str(alias)]
    _assert_names_only(listing.text, alias, tmp_path)


@pytest.mark.skipif(sys.platform == "win32", reason="stubs the Win32 layer on POSIX")
@pytest.mark.usefixtures("symlink_privilege")
@pytest.mark.parametrize(
    "failure",
    ["none", "component-denied", "component-link", "reopen-denied", "entry-denied"],
)
def test_the_windows_walk_never_names_a_resolved_component(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """Drive safe_io's real Windows walk (stubbed OS calls) through an alias.

    The route resolves the alias; the Windows walk then opens every component
    of the resolved path by name. A failure there used to put that component
    in the 422 ("NtOpenFile failed for 'resolved-secret-target'"). Public text
    is now built from the status code and the submitted path only.
    """
    from fake_windows_io import FakeWindows

    monkeypatch.delenv("GPO_STUDIO_TEMPLATE_ROOT")
    secret = tmp_path / "resolved-secret-target"
    _write_pair(secret, "good", "Synthetic.Test.Good", "GoodPolicy")
    alias = tmp_path / "submitted-alias"
    alias.symlink_to(secret, target_is_directory=True)
    fake = FakeWindows()
    if failure == "component-denied":
        fake.nt_open_failures["resolved-secret-target"] = 0xC0000022
    elif failure == "component-link":
        fake.nt_open_failures["resolved-secret-target"] = 0x8000002D
    elif failure == "reopen-denied":
        fake.create_file_failures.append(
            (lambda path: path.endswith("resolved-secret-target"), 5)
        )
    elif failure == "entry-denied":
        fake.nt_open_failures["good.admx"] = 0xC0000022
    fake.install(monkeypatch)

    response = _post_ingest(client, alias, "vendor")
    _assert_names_only(response.text, alias, tmp_path)
    if failure == "none":
        assert response.status_code == 201, response.text
        assert response.json()["policy_count"] == 1
        listing = client.get("/api/templates")
        _assert_names_only(listing.text, alias, tmp_path)
        return
    assert response.status_code == 422, response.text
    message = response.json()["error"]["message"]
    expected = "Refused" if failure == "component-link" else "Cannot read"
    assert message.startswith(f"{expected} template folder {alias}"), message
    if failure == "entry-denied":
        assert "'good.admx'" in message


# --- Review round 4 ------------------------------------------------------------

_permission_test = pytest.mark.skipif(
    sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="needs POSIX permission bits enforced (not Windows, not root)",
)


@pytest.fixture
def unsearchable_alias(tmp_path: Path) -> Iterator[Path]:
    """An alias to a store whose parent folder its reader may not search."""
    parent = tmp_path / "resolved-secret-parent"
    _write_pair(parent / "store", "good", "Synthetic.Test.Good", "GoodPolicy")
    alias = tmp_path / "submitted-alias"
    alias.symlink_to(parent / "store", target_is_directory=True)
    parent.chmod(0)
    try:
        yield alias
    finally:
        parent.chmod(0o755)


def _is_dir_as_on_python_314(self: Path) -> bool:
    """``Path.is_dir`` as Python 3.14 implements it: any OSError reads as False.

    Python 3.13's version raises PermissionError instead, so emulating 3.14
    lets one interpreter check both behaviours.
    """
    return os.path.isdir(self)


@_permission_test
@pytest.mark.usefixtures("symlink_privilege")
@pytest.mark.parametrize("is_dir", ["native", "python-3.14"])
def test_an_unreadable_folder_is_not_reported_as_missing(
    unsearchable_alias: Path, monkeypatch: pytest.MonkeyPatch, is_dir: str
) -> None:
    if is_dir == "python-3.14":
        monkeypatch.setattr(Path, "is_dir", _is_dir_as_on_python_314)
    resolved = unsearchable_alias.resolve()
    with pytest.raises(template_store.TemplateReadError) as failure:
        ingest_source("store", "local", resolved, display_path=str(unsearchable_alias))
    message = str(failure.value)
    assert message == (
        f"Cannot read template folder {unsearchable_alias}: "
        "opening the folder failed with EACCES"
    )
    assert "resolved-secret-parent" not in message
    assert isinstance(failure.value, OSError)


@pytest.mark.parametrize("is_dir", ["native", "python-3.14"])
def test_a_missing_folder_is_still_reported_as_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, is_dir: str
) -> None:
    if is_dir == "python-3.14":
        monkeypatch.setattr(Path, "is_dir", _is_dir_as_on_python_314)
    missing = tmp_path / "not-created"
    with pytest.raises(TemplateError, match="^Template directory does not exist: "):
        ingest_source("store", "local", missing)
    (tmp_path / "file.admx").write_text("synthetic", encoding="utf-8")
    with pytest.raises(TemplateError, match="^Template path is not a directory: "):
        ingest_source("store", "local", tmp_path / "file.admx")


@_permission_test
@pytest.mark.usefixtures("symlink_privilege")
@pytest.mark.parametrize("is_dir", ["native", "python-3.14"])
def test_the_api_reports_an_unreadable_folder_as_unreadable(
    client: TestClient,
    unsearchable_alias: Path,
    monkeypatch: pytest.MonkeyPatch,
    is_dir: str,
) -> None:
    monkeypatch.delenv("GPO_STUDIO_TEMPLATE_ROOT")
    if is_dir == "python-3.14":
        monkeypatch.setattr(Path, "is_dir", _is_dir_as_on_python_314)
    response = _post_ingest(client, unsearchable_alias)
    assert response.status_code == 422, response.text
    message = response.json()["error"]["message"]
    assert message.startswith(f"Cannot read ADMX folder: {unsearchable_alias}."), message
    assert "resolved-secret-parent" not in response.text


@_permission_test
@pytest.mark.usefixtures("symlink_privilege")
@pytest.mark.parametrize("is_dir", ["native", "python-3.14"])
def test_a_startup_load_failure_is_logged_without_os_text(
    unsearchable_alias: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    is_dir: str,
) -> None:
    """The startup warning is built like the client message: no OS text.

    The OS error names the resolved path ("Permission denied: '.../resolved-
    secret-parent/store'"); the log names the configured path, the phase and
    the errno code only.
    """
    import logging

    if is_dir == "python-3.14":
        monkeypatch.setattr(Path, "is_dir", _is_dir_as_on_python_314)
    store = WorkspaceStore(tmp_path / "workspace.db")
    monkeypatch.setattr(app.state, "store", store, raising=False)
    monkeypatch.setattr(app.state, "owns_store", False, raising=False)
    monkeypatch.setenv("GPO_STUDIO_ADMX_DIR", str(unsearchable_alias))
    names = ("admx_catalogue", "template_sources", "catalogue_snapshot")
    for name in names:
        monkeypatch.delattr(app.state, name, raising=False)
    caplog.set_level(logging.WARNING, logger="gpo_studio.api")
    try:
        with TestClient(app) as started:
            listing = started.get("/api/templates").json()
    finally:
        store.close()
        for name in names:
            if hasattr(app.state, name):
                delattr(app.state, name)
    assert listing["items"] == []
    warnings = [r.getMessage() for r in caplog.records if r.name == "gpo_studio.api"]
    loads = [m for m in warnings if m.startswith("Failed to load ADMX catalogue")]
    assert loads == [
        "Failed to load ADMX catalogue: Cannot read template folder "
        f"{unsearchable_alias}: opening the folder failed with EACCES"
    ], warnings
    assert not any("resolved-secret-parent" in m for m in warnings), warnings
