"""Drive ``safe_io``'s Windows code paths on POSIX through a stubbed Win32 layer.

``safe_io`` walks a Windows path with ``NtOpenFile`` relative to retained
handles, re-opens the final component with ``CreateFileW``, compares
``FILE_ID_INFO`` identities and enumerates with ``NtQueryDirectoryFile``. This
module installs stand-ins for ``ctypes.WinDLL``, ``ctypes.get_last_error`` and
``msvcrt`` that perform those operations on the real POSIX filesystem, with a
handle being an ordinary file descriptor. The Windows code itself runs
unmodified, so a test can push real directory trees through it and inject a
failure status for any component or entry it opens.

It models the calls ``safe_io`` makes, not Windows: reparse attributes come from
``lstat`` (a symlink reads as a reparse point) and nothing else about NTFS is
emulated. Native behaviour is covered by the ``test-windows`` CI job.
"""

from __future__ import annotations

import ctypes
import os
import stat
import sys
import types
from collections.abc import Callable
from typing import Any

import pytest

_STATUS_SUCCESS = 0
_STATUS_NO_MORE_FILES = 0x80000006
_STATUS_OBJECT_NAME_NOT_FOUND = 0xC0000034
_STATUS_ACCESS_DENIED = 0xC0000022
_FILE_DIRECTORY_FILE = 0x00000001
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_FILE_ATTRIBUTE_DIRECTORY = 0x10
_FILE_ATTRIBUTE_NORMAL = 0x80
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


class _DirectoryInformation(ctypes.Structure):
    """safe_io's FILE_DIRECTORY_INFORMATION header, field for field.

    Declared with the same ctypes types as safe_io, so the layout matches what
    it parses on this platform (a ``c_ulong`` is 8 bytes on LP64 POSIX).
    """

    _fields_ = [
        ("NextEntryOffset", ctypes.c_ulong),
        ("FileIndex", ctypes.c_ulong),
        ("CreationTime", ctypes.c_longlong),
        ("LastAccessTime", ctypes.c_longlong),
        ("LastWriteTime", ctypes.c_longlong),
        ("ChangeTime", ctypes.c_longlong),
        ("EndOfFile", ctypes.c_longlong),
        ("AllocationSize", ctypes.c_longlong),
        ("FileAttributes", ctypes.c_ulong),
        ("FileNameLength", ctypes.c_ulong),
    ]


class _Function:
    """A callable that tolerates ``restype``/``argtypes`` assignment."""

    def __init__(self, body: Callable[..., Any]) -> None:
        self._body = body
        self.restype: Any = None
        self.argtypes: Any = None

    def __call__(self, *args: Any) -> Any:
        return self._body(*args)


class FakeWindows:
    """Stubbed Win32 layer over the POSIX filesystem.

    ``nt_open_failures`` maps a name passed to ``NtOpenFile`` (a path component
    or a directory entry) to the NTSTATUS to fail it with.
    ``create_file_failures`` maps a predicate over the ``CreateFileW`` path to
    the Win32 error to fail it with.
    """

    def __init__(self) -> None:
        self.nt_open_failures: dict[str, int] = {}
        self.create_file_failures: list[tuple[Callable[[str], bool], int]] = []
        self.last_error = 0
        self._listings: dict[int, list[str]] = {}
        self.ntdll = types.SimpleNamespace(
            NtOpenFile=_Function(self._nt_open_file),
            NtQueryDirectoryFile=_Function(self._nt_query_directory_file),
            NtCreateFile=_Function(self._unsupported),
        )
        self.kernel32 = types.SimpleNamespace(
            CreateFileW=_Function(self._create_file),
            CloseHandle=_Function(self._close_handle),
            GetFileInformationByHandleEx=_Function(self._file_information),
        )
        self.msvcrt = types.ModuleType("msvcrt")
        self.msvcrt.get_osfhandle = lambda fd: fd  # type: ignore[attr-defined]
        self.msvcrt.open_osfhandle = lambda handle, flags: handle  # type: ignore[attr-defined]

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from gpo_studio import safe_io

        monkeypatch.setattr(safe_io, "sys", types.SimpleNamespace(platform="win32"))
        monkeypatch.setitem(sys.modules, "msvcrt", self.msvcrt)
        monkeypatch.setattr(ctypes, "WinDLL", self._win_dll, raising=False)
        monkeypatch.setattr(ctypes, "get_last_error", lambda: self.last_error, raising=False)

    def _win_dll(self, name: str, use_last_error: bool = False) -> Any:
        return {"ntdll": self.ntdll, "kernel32": self.kernel32}[name]

    @staticmethod
    def _unsupported(*args: Any) -> int:
        raise AssertionError("NtCreateFile is not stubbed")

    def _nt_open_file(
        self, handle_ref: Any, access: int, attributes_ref: Any, iosb_ref: Any,
        share: int, options: int,
    ) -> int:
        attributes = attributes_ref._obj
        object_name = attributes.ObjectName.contents
        name = object_name.Buffer[: object_name.Length // 2]
        if name in self.nt_open_failures:
            return self.nt_open_failures[name]
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        if options & _FILE_DIRECTORY_FILE:
            flags |= os.O_DIRECTORY
        try:
            fd = os.open(name, flags, dir_fd=int(attributes.RootDirectory))
        except FileNotFoundError:
            return _STATUS_OBJECT_NAME_NOT_FOUND
        except OSError:
            return _STATUS_ACCESS_DENIED
        handle_ref._obj.value = fd
        return _STATUS_SUCCESS

    def _nt_query_directory_file(
        self, handle: int, event: Any, apc: Any, context: Any, iosb_ref: Any,
        buffer: Any, length: int, information_class: int, single: Any,
        file_name: Any, restart: Any,
    ) -> int:
        if restart or handle not in self._listings:
            self._listings[handle] = [".", "..", *sorted(os.listdir(handle))]
        pending = self._listings[handle]
        header = ctypes.sizeof(_DirectoryInformation)
        entries: list[tuple[_DirectoryInformation, bytes]] = []
        used = 0
        while pending:
            name = pending[0]
            encoded = name.encode("utf-16-le")
            size = header + len(encoded)
            aligned = (size + 7) & ~7
            if used + size > length:
                break
            pending.pop(0)
            if name in (".", ".."):
                attributes = _FILE_ATTRIBUTE_DIRECTORY
            else:
                mode = os.stat(name, dir_fd=handle, follow_symlinks=False).st_mode
                attributes = (
                    _FILE_ATTRIBUTE_REPARSE_POINT if stat.S_ISLNK(mode)
                    else _FILE_ATTRIBUTE_DIRECTORY if stat.S_ISDIR(mode)
                    else _FILE_ATTRIBUTE_NORMAL
                )
            info = _DirectoryInformation(FileAttributes=attributes, FileNameLength=len(encoded))
            entries.append((info, encoded + b"\0" * (aligned - size)))
            used += aligned
        if not entries:
            return _STATUS_NO_MORE_FILES
        packed = bytearray()
        for index, (info, tail) in enumerate(entries):
            if index < len(entries) - 1:
                info.NextEntryOffset = header + len(tail)
            packed += bytes(info) + tail
        ctypes.memmove(buffer, bytes(packed), len(packed))
        iosb_ref._obj.Information = len(packed)
        return _STATUS_SUCCESS

    def _create_file(
        self, path: str, access: int, share: int, security: Any, disposition: int,
        flags: int, template: Any,
    ) -> int:
        invalid = ctypes.c_void_p(-1).value or 0
        for matches, error in self.create_file_failures:
            if matches(path):
                self.last_error = error
                return invalid
        open_flags = os.O_RDONLY | os.O_NONBLOCK
        if flags & _FILE_FLAG_OPEN_REPARSE_POINT:
            open_flags |= os.O_NOFOLLOW
        try:
            return os.open(path, open_flags)
        except OSError as error:
            self.last_error = 5 if error.errno == 13 else 2
            return invalid

    def _close_handle(self, handle: int) -> int:
        self._listings.pop(handle, None)
        os.close(handle)
        return 1

    @staticmethod
    def _file_information(handle: int, information_class: int, info_ref: Any, size: int) -> int:
        info = info_ref._obj
        status = os.fstat(handle)
        info.VolumeSerialNumber = status.st_dev or 1
        identifier = status.st_ino.to_bytes(16, "little")
        ctypes.memmove(ctypes.addressof(info.FileId.Identifier), identifier, 16)
        return 1
