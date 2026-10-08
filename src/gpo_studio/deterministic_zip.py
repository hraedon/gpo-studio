"""Byte-identical ZIP archives on every platform.

Every archive Studio produces -- the GPMC backup, the Studio bundle and every
lane candidate -- goes through `deterministic_zip`, so the same members yield
the same bytes on Linux and on Windows, under any Python.

What used to vary, and what pins it here:

* **Member order.** Names are sorted by code point, which no filesystem or
  locale influences (a directory walk on a case-insensitive filesystem does).
* **Timestamps.** Every member is dated 1980-01-01 00:00:00, the ZIP epoch.
* **Host byte.** `zipfile.ZipInfo` defaults ``create_system`` to 0 on Windows
  and 3 elsewhere; it is always 3 here.
* **Attributes.** ``external_attr`` is always a regular file, mode 0600.
* **Compression.** Members are STORED, not deflated. CPython's Windows builds
  link a different deflate implementation (zlib-ng from 3.14) than Linux
  distributions' zlib, and the two produce different -- equally valid --
  compressed bytes for the same input, so no deflate setting can make the
  archives identical. The cost is size; these archives are small, and nothing
  that consumes them cares: Windows never reads them (``Import-GPO`` reads the
  extracted folder), and ``Expand-Archive`` / ``System.IO.Compression`` and
  Python's ``zipfile`` all extract STORED members.

Names are validated rather than normalised: a name that is absolute, holds a
backslash or a ``..`` segment, or differs from another only by case is
refused, because it would extract differently (or onto another member) on
Windows.
"""

from __future__ import annotations

import io
import stat
import zipfile
from collections.abc import Mapping

ZIP_DATE_TIME = (1980, 1, 1, 0, 0, 0)
ZIP_CREATE_SYSTEM = 3
ZIP_EXTERNAL_ATTR = (stat.S_IFREG | 0o600) << 16
ZIP_COMPRESSION = zipfile.ZIP_STORED


class DeterministicZipError(ValueError):
    """A member name that would not extract to the same place everywhere."""


def _check_names(names: list[str]) -> None:
    folded: dict[str, str] = {}
    for name in names:
        parts = name.split("/")
        if (
            not name
            or name.startswith("/")
            or "\\" in name
            or any(part in ("", ".", "..") for part in parts)
        ):
            raise DeterministicZipError(f"unsafe archive member name: {name!r}")
        key = name.casefold()
        if key in folded:
            raise DeterministicZipError(
                f"archive members {folded[key]!r} and {name!r} differ only by case"
            )
        folded[key] = name


def deterministic_zip(entries: Mapping[str, bytes]) -> bytes:
    """Return a ZIP of *entries* (member name -> bytes), identical on every platform."""
    names = sorted(entries)
    _check_names(names)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=ZIP_COMPRESSION) as archive:
        for name in names:
            info = zipfile.ZipInfo(name, date_time=ZIP_DATE_TIME)
            info.compress_type = ZIP_COMPRESSION
            info.create_system = ZIP_CREATE_SYSTEM
            info.external_attr = ZIP_EXTERNAL_ATTR
            archive.writestr(info, entries[name])
    return buffer.getvalue()
