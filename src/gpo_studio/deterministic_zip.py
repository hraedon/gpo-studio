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
``..`` segment, a NUL or other control character, a character or device name
Windows forbids (``\\``, ``:``, ``CON``, ``NUL.txt`` ...), or a trailing dot or
space, or that collides with another member case-insensitively (including a
file that is also another member's directory), is refused: it would extract
differently, or onto another member, on Windows.
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


#: Device names Windows reserves in every directory, with or without an
#: extension ("NUL.txt" is the NUL device too).
_WINDOWS_RESERVED = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"{port}{n}" for port in ("COM", "LPT") for n in "123456789\u00b9\u00b2\u00b3"}
)
#: Microsoft's naming rules reserve COM and LPT with the superscript digits
#: 1, 2 and 3 (U+00B9, U+00B2, U+00B3) too, with or without an extension:
#: https://learn.microsoft.com/en-us/windows/win32/fileio/naming-a-file
#: Characters Windows forbids in a path component (``\\`` and ``:`` included:
#: a backslash is a separator there, a colon a drive or alternate stream).
_WINDOWS_FORBIDDEN = frozenset('<>:"|?*\\')


def _component_problem(part: str) -> str | None:
    if part in ("", ".", ".."):
        return "an empty, '.' or '..' segment"
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in part):
        # NUL in particular: zipfile truncates the name at it, so "a\x00x"
        # would be stored as "a" beside a real "a".
        return "a control character"
    if any(ch in _WINDOWS_FORBIDDEN for ch in part):
        return "a character Windows forbids in a path"
    if part.endswith((".", " ")):
        return "a trailing dot or space, which Windows strips"
    if part.split(".", 1)[0].rstrip(" ").upper() in _WINDOWS_RESERVED:
        return "a Windows reserved device name"
    return None


def _check_names(names: list[str]) -> None:
    files: dict[str, str] = {}
    directories: dict[str, str] = {}
    for name in names:
        if not name or name.startswith("/"):
            raise DeterministicZipError(f"unsafe archive member name {name!r}: absolute or empty")
        parts = name.split("/")
        for part in parts:
            problem = _component_problem(part)
            if problem is not None:
                raise DeterministicZipError(f"unsafe archive member name {name!r}: {problem}")
        key = name.casefold()
        if key in files:
            raise DeterministicZipError(
                f"archive members {files[key]!r} and {name!r} differ only by case"
            )
        files[key] = name
        for depth in range(1, len(parts)):
            directories.setdefault("/".join(parts[:depth]).casefold(), name)
    for key, name in files.items():
        if key in directories:
            raise DeterministicZipError(
                f"archive member {name!r} is also a directory of {directories[key]!r}"
            )


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
