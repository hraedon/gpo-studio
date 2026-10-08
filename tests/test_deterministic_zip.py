"""`deterministic_zip`: the same members give the same bytes on every platform."""

from __future__ import annotations

import io
import zipfile
from typing import Any

import pytest

from gpo_studio.deterministic_zip import (
    DeterministicZipError,
    deterministic_zip,
)
from gpo_studio.export import export_bundle, gpmc_backup_bundle
from gpo_studio.model import GPO, RegistrySetting

ENTRIES = {
    "b/second.xml": b"<b/>",
    "A/first.xml": b"<a/>",
    "a-sibling.txt": "Ünïcode name\r\n".encode(),
    "Ünïcode/datei.txt": b"x" * 1000,
}


def _gpo() -> GPO:
    return GPO(
        guid="9b1de5c0-0000-4000-8000-0000000000d1",
        name="Deterministic zip",
        domain="synthetic.test",
        settings=(
            RegistrySetting(
                id="s1",
                side="computer",
                hive="HKLM",
                key=r"Software\Policies\GPOStudio\Zip",
                value_name="V",
                registry_type="REG_DWORD",
                value=1,
            ),
        ),
    )


def test_members_are_sorted_stored_and_carry_fixed_metadata() -> None:
    with zipfile.ZipFile(io.BytesIO(deterministic_zip(ENTRIES))) as archive:
        infos = archive.infolist()
        assert [i.filename for i in infos] == sorted(ENTRIES)
        for info in infos:
            assert info.date_time == (1980, 1, 1, 0, 0, 0)
            assert info.compress_type == zipfile.ZIP_STORED
            assert info.create_system == 3
            assert info.external_attr == 0o100600 << 16
            assert archive.read(info) == ENTRIES[info.filename]


def test_insertion_order_does_not_matter() -> None:
    reversed_entries = dict(reversed(list(ENTRIES.items())))
    assert deterministic_zip(reversed_entries) == deterministic_zip(ENTRIES)


def _windows_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """What a Windows CPython's `zipfile.ZipInfo` would default to."""
    original = zipfile.ZipInfo.__init__

    def windows_default(self: zipfile.ZipInfo, *args: Any, **kwargs: Any) -> None:
        original(self, *args, **kwargs)
        self.create_system = 0

    monkeypatch.setattr(zipfile.ZipInfo, "__init__", windows_default)


def test_a_windows_host_writes_the_same_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    native = deterministic_zip(ENTRIES)
    native_backup = gpmc_backup_bundle(_gpo())
    native_bundle = export_bundle(_gpo())
    _windows_defaults(monkeypatch)
    assert deterministic_zip(ENTRIES) == native
    assert gpmc_backup_bundle(_gpo()) == native_backup
    assert export_bundle(_gpo()) == native_bundle


def test_no_deflate_means_no_zlib_dependence(monkeypatch: pytest.MonkeyPatch) -> None:
    """STORED never calls the compressor, so a different zlib cannot matter."""
    import zlib

    def refuse(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("deflate was used")

    monkeypatch.setattr(zlib, "compressobj", refuse)
    with zipfile.ZipFile(io.BytesIO(gpmc_backup_bundle(_gpo()))) as archive:
        assert {i.compress_type for i in archive.infolist()} == {zipfile.ZIP_STORED}
    deterministic_zip(ENTRIES)


@pytest.mark.parametrize(
    "name",
    ["", "/abs.txt", "a\\b.txt", "a/../b.txt", "a//b.txt", "./a.txt", "a/."],
)
def test_unsafe_names_are_refused(name: str) -> None:
    with pytest.raises(DeterministicZipError):
        deterministic_zip({name: b""})


def test_names_differing_only_by_case_are_refused() -> None:
    """They would extract onto each other on Windows."""
    with pytest.raises(DeterministicZipError, match="only by case"):
        deterministic_zip({"Machine/registry.pol": b"", "Machine/Registry.pol": b""})
