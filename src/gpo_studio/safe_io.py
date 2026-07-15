"""Race-resistant local file opening helpers.

POSIX paths are resolved one component at a time relative to retained
directory descriptors, with symlinks rejected at every level.  Windows
walks each component via ``NtOpenFile`` with ``RootDirectory`` (the NT
equivalent of ``openat``), so every ancestor is pinned by its handle.
``FILE_OPEN_REPARSE_POINT`` on each open prevents following reparse
points, and a post-open handle check rejects any reparse point present
at open time.
"""

from __future__ import annotations

import contextlib
import os
import stat
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


class SafeOpenError(OSError):
    """A path could not be opened without following links."""


@dataclass(frozen=True, slots=True)
class SafeDirectoryEntry:
    """An entry opened relative to a retained parent directory descriptor.

    ``fd`` remains valid only until the iterator advances or is closed.  A
    directory entry's descriptor may be passed recursively to
    :func:`iter_directory`; a regular-file entry's descriptor is readable.
    """

    name: str
    is_directory: bool
    fd: int


_IS_WINDOWS = sys.platform == "win32"
_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)

_windows_test_hook_phase2: Callable[[], None] | None = None


def _parts(path: Path) -> tuple[str, tuple[str, ...]]:
    absolute = path.absolute()
    anchor = absolute.anchor
    parts = absolute.parts[1:] if anchor else absolute.parts
    if any(part in ("", ".", "..") for part in parts):
        raise SafeOpenError("Unsafe path component")
    return anchor or ".", parts


def _open_directory_posix(path: Path) -> int:
    anchor, parts = _parts(path)
    flags = os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW | _O_CLOEXEC
    try:
        current_fd = os.open(anchor, flags)
    except OSError as error:
        raise SafeOpenError("Cannot open path root safely") from error
    try:
        for part in parts:
            next_fd = os.open(part, flags, dir_fd=current_fd)
            os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except OSError as error:
        os.close(current_fd)
        raise SafeOpenError("Cannot open directory without following links") from error


def _open_regular_posix(path: Path) -> int:
    anchor, parts = _parts(path)
    if not parts:
        raise SafeOpenError("Path does not name a regular file")
    parent = Path(anchor, *parts[:-1])
    parent_fd = _open_directory_posix(parent)
    try:
        fd = os.open(
            parts[-1],
            os.O_RDONLY | _O_NOFOLLOW | _O_CLOEXEC,
            dir_fd=parent_fd,
        )
    except OSError as error:
        raise SafeOpenError("Cannot open file without following links") from error
    finally:
        os.close(parent_fd)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise SafeOpenError("Path does not name a regular file")
        return fd
    except BaseException:
        os.close(fd)
        raise


def _open_or_create_regular_posix(
    path: Path, *, exclusive: bool, mode: int
) -> int:
    anchor, parts = _parts(path)
    if not parts:
        raise SafeOpenError("Path does not name a regular file")
    parent_fd = _open_directory_posix(Path(anchor, *parts[:-1]))
    flags = os.O_RDWR | os.O_CREAT | _O_NOFOLLOW | _O_CLOEXEC
    if exclusive:
        flags |= os.O_EXCL
    try:
        fd = os.open(parts[-1], flags, mode, dir_fd=parent_fd)
    except FileExistsError:
        raise
    except OSError as error:
        raise SafeOpenError("Cannot safely open or create regular file") from error
    finally:
        os.close(parent_fd)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise SafeOpenError("Path does not name a regular file")
        return fd
    except BaseException:
        os.close(fd)
        raise


def _windows_components(path: Path) -> Iterator[Path]:
    absolute = path.absolute()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        if part in ("", ".", ".."):
            raise SafeOpenError("Unsafe path component")
        current /= part
        yield current


def is_link_or_junction(path: str | Path) -> bool:
    """Return whether *path* is a symlink or Windows directory junction."""
    component = Path(path)
    if component.is_symlink():
        return True
    is_junction = getattr(component, "is_junction", None)
    return bool(is_junction and is_junction())


def _validate_entry_name(name: str) -> None:
    if name in ("", ".", "..") or "\x00" in name:
        raise SafeOpenError("Unsafe directory entry name")
    if os.sep in name or (os.altsep is not None and os.altsep in name):
        raise SafeOpenError("Unsafe directory entry name")


def _iter_directory_posix(dir_fd: int) -> Iterator[SafeDirectoryEntry]:
    try:
        scanner = os.scandir(dir_fd)
    except OSError as error:
        raise SafeOpenError("Cannot enumerate directory safely") from error

    with scanner:
        for directory_entry in scanner:
            name = directory_entry.name
            _validate_entry_name(name)
            try:
                before = directory_entry.stat(follow_symlinks=False)
            except OSError as error:
                raise SafeOpenError(
                    f"Cannot inspect directory entry {name!r}"
                ) from error
            if stat.S_ISLNK(before.st_mode):
                raise SafeOpenError(f"Link found in directory entry {name!r}")
            if stat.S_ISDIR(before.st_mode):
                flags = os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW | _O_CLOEXEC
                is_directory = True
            elif stat.S_ISREG(before.st_mode):
                flags = os.O_RDONLY | _O_NOFOLLOW | _O_CLOEXEC
                is_directory = False
            else:
                raise SafeOpenError(
                    f"Unsupported file type in directory entry {name!r}"
                )

            try:
                child_fd = os.open(name, flags, dir_fd=dir_fd)
            except OSError as error:
                raise SafeOpenError(
                    f"Cannot open directory entry {name!r} safely"
                ) from error
            try:
                after = os.fstat(child_fd)
                if is_directory and not stat.S_ISDIR(after.st_mode):
                    raise SafeOpenError(
                        f"Directory entry {name!r} changed while opening"
                    )
                if not is_directory and not stat.S_ISREG(after.st_mode):
                    raise SafeOpenError(f"File entry {name!r} changed while opening")
                yield SafeDirectoryEntry(
                    name=name,
                    is_directory=is_directory,
                    fd=child_fd,
                )
            finally:
                with contextlib.suppress(OSError):
                    os.close(child_fd)


def _iter_directory_windows(dir_fd: int) -> Iterator[SafeDirectoryEntry]:
    import ctypes
    import msvcrt

    _GENERIC_READ = 0x80000000
    _FILE_SHARE_READ = 0x00000001
    _FILE_ATTRIBUTE_DIRECTORY = 0x00000010
    _FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
    _FILE_DIRECTORY_INFORMATION_CLASS = 1
    _FILE_DIRECTORY_FILE = 0x00000001
    _FILE_SYNCHRONOUS_IO_NONALERT = 0x00000020
    _FILE_NON_DIRECTORY_FILE = 0x00000040
    _FILE_OPEN_FOR_BACKUP_INTENT = 0x00004000
    _FILE_OPEN_REPARSE_POINT = 0x00200000
    _OBJ_CASE_INSENSITIVE = 0x00000040
    _STATUS_SUCCESS = 0x00000000
    _STATUS_NO_MORE_FILES = 0x80000006
    _SYNCHRONIZE = 0x00100000
    _O_BINARY = 0x8000
    _BUFFER_SIZE = 64 * 1024

    class IO_STATUS_BLOCK(ctypes.Structure):
        _fields_ = [
            ("Status", ctypes.c_long),
            ("Information", ctypes.c_void_p),
        ]

    class UNICODE_STRING(ctypes.Structure):
        _fields_ = [
            ("Length", ctypes.c_ushort),
            ("MaximumLength", ctypes.c_ushort),
            ("Buffer", ctypes.c_wchar_p),
        ]

    class OBJECT_ATTRIBUTES(ctypes.Structure):
        _fields_ = [
            ("Length", ctypes.c_ulong),
            ("RootDirectory", ctypes.c_void_p),
            ("ObjectName", ctypes.POINTER(UNICODE_STRING)),
            ("Attributes", ctypes.c_ulong),
            ("SecurityDescriptor", ctypes.c_void_p),
            ("SecurityQualityOfService", ctypes.c_void_p),
        ]

    class FILE_DIRECTORY_INFORMATION_HEADER(ctypes.Structure):
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

    ntdll = ctypes.WinDLL("ntdll", use_last_error=True)  # type: ignore[attr-defined]
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    ntdll.NtQueryDirectoryFile.restype = ctypes.c_long
    ntdll.NtQueryDirectoryFile.argtypes = (
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(IO_STATUS_BLOCK),
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_int,
        ctypes.c_ubyte,
        ctypes.c_void_p,
        ctypes.c_ubyte,
    )
    ntdll.NtOpenFile.restype = ctypes.c_long
    ntdll.NtOpenFile.argtypes = (
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_uint32,
        ctypes.POINTER(OBJECT_ATTRIBUTES),
        ctypes.POINTER(IO_STATUS_BLOCK),
        ctypes.c_uint32,
        ctypes.c_uint32,
    )
    kernel32.CloseHandle.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)

    parent_handle = msvcrt.get_osfhandle(dir_fd)  # type: ignore[attr-defined]
    restart_scan = True
    while True:
        buffer = ctypes.create_string_buffer(_BUFFER_SIZE)
        iosb = IO_STATUS_BLOCK()
        status = ntdll.NtQueryDirectoryFile(
            parent_handle,
            None,
            None,
            None,
            ctypes.byref(iosb),
            buffer,
            len(buffer),
            _FILE_DIRECTORY_INFORMATION_CLASS,
            False,
            None,
            restart_scan,
        )
        restart_scan = False
        unsigned_status = status & 0xFFFFFFFF
        if unsigned_status == _STATUS_NO_MORE_FILES:
            return
        if unsigned_status != _STATUS_SUCCESS:
            raise SafeOpenError(
                "Cannot enumerate directory safely: "
                f"NT status 0x{unsigned_status:08X}"
            )

        returned = int(iosb.Information or 0)
        fixed_size = ctypes.sizeof(FILE_DIRECTORY_INFORMATION_HEADER)
        if returned < fixed_size or returned > len(buffer):
            raise SafeOpenError("Invalid directory enumeration result")

        offset = 0
        while offset < returned:
            if returned - offset < fixed_size:
                raise SafeOpenError("Truncated directory enumeration result")
            info = FILE_DIRECTORY_INFORMATION_HEADER.from_buffer_copy(
                buffer.raw[offset : offset + fixed_size]
            )
            name_length = int(info.FileNameLength)
            if name_length % 2 or name_length > returned - offset - fixed_size:
                raise SafeOpenError("Invalid directory entry name length")
            raw_name = buffer.raw[
                offset + fixed_size : offset + fixed_size + name_length
            ]
            try:
                name = raw_name.decode("utf-16-le")
            except UnicodeDecodeError as error:
                raise SafeOpenError("Invalid directory entry name encoding") from error

            if name not in (".", ".."):
                _validate_entry_name(name)
                attributes = int(info.FileAttributes)
                if attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
                    raise SafeOpenError(f"Reparse point found in directory entry {name!r}")
                is_directory = bool(attributes & _FILE_ATTRIBUTE_DIRECTORY)

                name_us = UNICODE_STRING(
                    Length=name_length,
                    MaximumLength=name_length + 2,
                    Buffer=name,
                )
                obj_attrs = OBJECT_ATTRIBUTES(
                    Length=ctypes.sizeof(OBJECT_ATTRIBUTES),
                    RootDirectory=parent_handle,
                    ObjectName=ctypes.pointer(name_us),
                    Attributes=_OBJ_CASE_INSENSITIVE,
                    SecurityDescriptor=None,
                    SecurityQualityOfService=None,
                )
                open_options = (
                    _FILE_OPEN_REPARSE_POINT
                    | _FILE_OPEN_FOR_BACKUP_INTENT
                    | _FILE_SYNCHRONOUS_IO_NONALERT
                    | (_FILE_DIRECTORY_FILE if is_directory else _FILE_NON_DIRECTORY_FILE)
                )
                child_handle = ctypes.c_void_p()
                child_iosb = IO_STATUS_BLOCK()
                child_status = ntdll.NtOpenFile(
                    ctypes.byref(child_handle),
                    _GENERIC_READ | _SYNCHRONIZE,
                    ctypes.byref(obj_attrs),
                    ctypes.byref(child_iosb),
                    _FILE_SHARE_READ,
                    open_options,
                )
                if child_status != _STATUS_SUCCESS:
                    raise SafeOpenError(
                        f"Cannot open directory entry {name!r} safely: "
                        f"NT status 0x{child_status & 0xFFFFFFFF:08X}"
                    )
                child_handle_value = child_handle.value
                if child_handle_value is None:
                    raise SafeOpenError(
                        f"Cannot open directory entry {name!r} safely: null handle"
                    )
                try:
                    child_fd: int = msvcrt.open_osfhandle(child_handle_value, _O_BINARY)  # type: ignore[attr-defined]
                except Exception:
                    kernel32.CloseHandle(child_handle_value)
                    raise
                if child_fd == -1:
                    kernel32.CloseHandle(child_handle_value)
                    raise SafeOpenError(f"Cannot adopt directory entry {name!r} handle")
                try:
                    child_stat = os.fstat(child_fd)
                    child_attrs = getattr(child_stat, "st_file_attributes", 0)
                    if child_attrs & _FILE_ATTRIBUTE_REPARSE_POINT:
                        raise SafeOpenError(
                            f"Reparse point found in directory entry {name!r}"
                        )
                    if is_directory and not stat.S_ISDIR(child_stat.st_mode):
                        raise SafeOpenError(f"Directory entry {name!r} changed while opening")
                    if not is_directory and not stat.S_ISREG(child_stat.st_mode):
                        raise SafeOpenError(f"File entry {name!r} changed while opening")
                    yield SafeDirectoryEntry(
                        name=name,
                        is_directory=is_directory,
                        fd=child_fd,
                    )
                finally:
                    with contextlib.suppress(OSError):
                        os.close(child_fd)

            next_offset = int(info.NextEntryOffset)
            if next_offset == 0:
                break
            if next_offset < fixed_size or next_offset > returned - offset:
                raise SafeOpenError("Invalid directory entry offset")
            offset += next_offset


def iter_directory(dir_fd: int) -> Iterator[SafeDirectoryEntry]:
    """Yield safely opened entries relative to a retained directory descriptor.

    Entries that are links or reparse points fail the complete enumeration.
    The caller owns *dir_fd*.  Each yielded entry descriptor is owned by the
    iterator and is closed when the iterator advances or closes.
    """
    if _IS_WINDOWS:
        yield from _iter_directory_windows(dir_fd)
    else:
        yield from _iter_directory_posix(dir_fd)


def _windows_open_relative(path: Path, *, directory: bool) -> int:
    """Open a file or directory using retained ancestor handles.

    Walks the path one component at a time via ``NtOpenFile`` with
    ``RootDirectory``, so each ancestor is pinned by its handle.  This
    prevents a concurrent parent-swap (replacing a checked directory with a
    junction) from diverting the final open.  ``FILE_OPEN_REPARSE_POINT``
    on every component ensures the open itself does not follow reparse
    points.

    Because ``NtOpenFile`` handles are not CRT-compatible for data reads,
    the final component is re-opened via ``CreateFileW`` and verified to be
    the same file by its Windows ``FILE_ID_INFO`` identity.  The
    ``NtOpenFile`` handle is kept open until the identity check completes,
    preventing file deletion during the brief window between the two opens.
    """
    import ctypes
    import msvcrt

    _GENERIC_READ = 0x80000000
    _FILE_SHARE_READ = 0x00000001
    _OPEN_EXISTING = 3
    _FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    _FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    _FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
    _O_BINARY = 0x8000

    _FILE_READ_ATTRIBUTES = 0x00000080
    _SYNCHRONIZE = 0x00100000
    _FILE_OPEN_REPARSE_POINT_NT = 0x00200000
    _FILE_DIRECTORY_FILE = 0x00000001
    _FILE_NON_DIRECTORY_FILE = 0x00000040
    _FILE_OPEN_FOR_BACKUP_INTENT = 0x00004000
    _OBJ_CASE_INSENSITIVE = 0x00000040
    _STATUS_SUCCESS = 0

    class IO_STATUS_BLOCK(ctypes.Structure):
        _fields_ = [
            ("Status", ctypes.c_long),
            ("Information", ctypes.c_void_p),
        ]

    class UNICODE_STRING(ctypes.Structure):
        _fields_ = [
            ("Length", ctypes.c_ushort),
            ("MaximumLength", ctypes.c_ushort),
            ("Buffer", ctypes.c_wchar_p),
        ]

    class OBJECT_ATTRIBUTES(ctypes.Structure):
        _fields_ = [
            ("Length", ctypes.c_ulong),
            ("RootDirectory", ctypes.c_void_p),
            ("ObjectName", ctypes.POINTER(UNICODE_STRING)),
            ("Attributes", ctypes.c_ulong),
            ("SecurityDescriptor", ctypes.c_void_p),
            ("SecurityQualityOfService", ctypes.c_void_p),
        ]

    class FILE_ID_128(ctypes.Structure):
        _fields_ = [
            ("Identifier", ctypes.c_ubyte * 16),
        ]

    class FILE_ID_INFO(ctypes.Structure):
        _fields_ = [
            ("VolumeSerialNumber", ctypes.c_uint64),
            ("FileId", FILE_ID_128),
        ]

    _FILE_ID_INFO_CLASS = 18

    ntdll = ctypes.WinDLL("ntdll", use_last_error=True)  # type: ignore[attr-defined]
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]

    ntdll.NtOpenFile.restype = ctypes.c_long
    ntdll.NtOpenFile.argtypes = (
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_uint32,
        ctypes.POINTER(OBJECT_ATTRIBUTES),
        ctypes.POINTER(IO_STATUS_BLOCK),
        ctypes.c_uint32,
        ctypes.c_uint32,
    )
    kernel32.CreateFileW.restype = ctypes.c_void_p
    kernel32.CreateFileW.argtypes = (
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    )
    kernel32.CloseHandle.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
    kernel32.GetFileInformationByHandleEx.restype = ctypes.c_int
    kernel32.GetFileInformationByHandleEx.argtypes = (
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_ulong,
    )

    def _file_identity(fd: int) -> tuple[int, bytes]:
        handle = msvcrt.get_osfhandle(fd)  # type: ignore[attr-defined]
        info = FILE_ID_INFO()
        if not kernel32.GetFileInformationByHandleEx(
            handle,
            _FILE_ID_INFO_CLASS,
            ctypes.byref(info),
            ctypes.sizeof(info),
        ):
            last_error = ctypes.get_last_error()  # type: ignore[attr-defined]
            raise SafeOpenError(
                "Cannot get stable file identity: "
                f"Win32 error {last_error}"
            )
        volume_serial = int(info.VolumeSerialNumber)
        file_id = bytes(info.FileId.Identifier)
        if volume_serial == 0 or not any(file_id):
            raise SafeOpenError("Stable file identity is unavailable")
        return volume_serial, file_id

    absolute = path.absolute()
    anchor = absolute.anchor
    raw_parts = absolute.parts[1:] if anchor else absolute.parts
    if any(p in ("", ".", "..") for p in raw_parts):
        raise SafeOpenError("Unsafe path component")
    if not raw_parts and not directory:
        raise SafeOpenError("Path does not name a regular file")
    parts = raw_parts

    # --- Phase 1: Walk path with NtOpenFile + RootDirectory ---
    root_handle = kernel32.CreateFileW(
        str(Path(anchor)),
        _GENERIC_READ,
        _FILE_SHARE_READ,
        None,
        _OPEN_EXISTING,
        _FILE_FLAG_BACKUP_SEMANTICS | _FILE_FLAG_OPEN_REPARSE_POINT,
        None,
    )
    _INVALID_HANDLE = ctypes.c_void_p(-1).value
    if not root_handle or root_handle == _INVALID_HANDLE:
        last_error = ctypes.get_last_error()  # type: ignore[attr-defined]
        raise SafeOpenError(
            f"Cannot open path root {anchor!s}: Win32 error {last_error}"
        )

    try:
        nt_fd: int = msvcrt.open_osfhandle(root_handle, _O_BINARY)  # type: ignore[attr-defined]
    except Exception:
        kernel32.CloseHandle(root_handle)
        raise
    if nt_fd == -1:
        kernel32.CloseHandle(root_handle)
        raise SafeOpenError(f"open_osfhandle failed for root {anchor!s}")

    try:
        def _check_reparse(label: str) -> None:
            st = os.fstat(nt_fd)
            attrs = getattr(st, "st_file_attributes", 0)
            if attrs & _FILE_ATTRIBUTE_REPARSE_POINT:
                raise SafeOpenError(f"Reparse point detected at {label}")

        _check_reparse(f"root {anchor!s}")

        for i, component in enumerate(parts):
            is_last = i == len(parts) - 1
            parent_handle = msvcrt.get_osfhandle(nt_fd)  # type: ignore[attr-defined]

            name_us = UNICODE_STRING(
                Length=len(component.encode("utf-16-le")),
                MaximumLength=len(component.encode("utf-16-le")) + 2,
                Buffer=component,
            )
            obj_attrs = OBJECT_ATTRIBUTES(
                Length=ctypes.sizeof(OBJECT_ATTRIBUTES),
                RootDirectory=parent_handle,
                ObjectName=ctypes.pointer(name_us),
                Attributes=_OBJ_CASE_INSENSITIVE,
                SecurityDescriptor=None,
                SecurityQualityOfService=None,
            )

            open_options = (
                _FILE_OPEN_REPARSE_POINT_NT
                | _FILE_OPEN_FOR_BACKUP_INTENT
            )
            if is_last and not directory:
                open_options |= _FILE_NON_DIRECTORY_FILE
            else:
                open_options |= _FILE_DIRECTORY_FILE

            child_handle = ctypes.c_void_p()
            iosb = IO_STATUS_BLOCK()
            status = ntdll.NtOpenFile(
                ctypes.byref(child_handle),
                _FILE_READ_ATTRIBUTES | _SYNCHRONIZE,
                ctypes.byref(obj_attrs),
                ctypes.byref(iosb),
                _FILE_SHARE_READ,
                open_options,
            )
            if status != _STATUS_SUCCESS:
                raise SafeOpenError(
                    f"NtOpenFile failed for {component!r}: "
                    f"NT status 0x{status & 0xFFFFFFFF:08X}"
                )

            # Convert child handle to fd BEFORE closing parent, so that
            # if open_osfhandle raises, nt_fd is still valid for cleanup.
            child_handle_value = child_handle.value
            if child_handle_value is None:
                raise SafeOpenError(f"NtOpenFile returned a null handle for {component!r}")
            try:
                new_fd: int = msvcrt.open_osfhandle(child_handle_value, _O_BINARY)  # type: ignore[attr-defined]
            except Exception:
                kernel32.CloseHandle(child_handle_value)
                raise
            if new_fd == -1:
                kernel32.CloseHandle(child_handle_value)
                raise SafeOpenError(
                    f"open_osfhandle failed for {component!r}"
                )
            os.close(nt_fd)
            nt_fd = new_fd

            _check_reparse(component)

        nt_identity = _file_identity(nt_fd)

        if _windows_test_hook_phase2 is not None:
            _windows_test_hook_phase2()

        # --- Phase 2: Re-open with CreateFileW for CRT compatibility ---
        cw_flags = _FILE_FLAG_OPEN_REPARSE_POINT
        if directory:
            cw_flags |= _FILE_FLAG_BACKUP_SEMANTICS

        cw_handle = kernel32.CreateFileW(
            str(absolute),
            _GENERIC_READ,
            _FILE_SHARE_READ,
            None,
            _OPEN_EXISTING,
            cw_flags,
            None,
        )
        if not cw_handle or cw_handle == _INVALID_HANDLE:
            last_error = ctypes.get_last_error()  # type: ignore[attr-defined]
            raise SafeOpenError(
                f"CreateFileW failed for {path!s}: Win32 error {last_error}"
            )

        try:
            cw_fd: int = msvcrt.open_osfhandle(cw_handle, _O_BINARY)  # type: ignore[attr-defined]
        except Exception:
            kernel32.CloseHandle(cw_handle)
            raise
        if cw_fd == -1:
            kernel32.CloseHandle(cw_handle)
            raise SafeOpenError(f"open_osfhandle failed for {path!s}")

        try:
            cw_stat = os.fstat(cw_fd)
            cw_identity = _file_identity(cw_fd)
            if cw_identity != nt_identity:
                raise SafeOpenError("File changed between validation and open")
            cw_attrs = getattr(cw_stat, "st_file_attributes", 0)
            if cw_attrs & _FILE_ATTRIBUTE_REPARSE_POINT:
                raise SafeOpenError("Links and reparse points are not allowed")
            if directory and not stat.S_ISDIR(cw_stat.st_mode):
                raise SafeOpenError("Path does not name a directory")
            if not directory and not stat.S_ISREG(cw_stat.st_mode):
                raise SafeOpenError("Path does not name a regular file")
            os.close(nt_fd)
            nt_fd = -1
            return cw_fd
        except BaseException:
            os.close(cw_fd)
            raise
    except BaseException:
        with contextlib.suppress(OSError):
            os.close(nt_fd)
        raise


def _open_or_create_regular_windows(path: Path, *, exclusive: bool) -> int:
    import ctypes
    import msvcrt

    _GENERIC_READ = 0x80000000
    _GENERIC_WRITE = 0x40000000
    _FILE_SHARE_READ = 0x00000001
    _FILE_SHARE_WRITE = 0x00000002
    _FILE_ATTRIBUTE_NORMAL = 0x00000080
    _FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
    _FILE_CREATE = 0x00000002
    _FILE_OPEN_IF = 0x00000003
    _FILE_SYNCHRONOUS_IO_NONALERT = 0x00000020
    _FILE_NON_DIRECTORY_FILE = 0x00000040
    _FILE_OPEN_REPARSE_POINT = 0x00200000
    _OBJ_CASE_INSENSITIVE = 0x00000040
    _STATUS_SUCCESS = 0x00000000
    _STATUS_OBJECT_NAME_COLLISION = 0xC0000035
    _SYNCHRONIZE = 0x00100000
    _O_BINARY = 0x8000
    _O_RDWR = 0x0002

    class IO_STATUS_BLOCK(ctypes.Structure):
        _fields_ = [
            ("Status", ctypes.c_long),
            ("Information", ctypes.c_void_p),
        ]

    class UNICODE_STRING(ctypes.Structure):
        _fields_ = [
            ("Length", ctypes.c_ushort),
            ("MaximumLength", ctypes.c_ushort),
            ("Buffer", ctypes.c_wchar_p),
        ]

    class OBJECT_ATTRIBUTES(ctypes.Structure):
        _fields_ = [
            ("Length", ctypes.c_ulong),
            ("RootDirectory", ctypes.c_void_p),
            ("ObjectName", ctypes.POINTER(UNICODE_STRING)),
            ("Attributes", ctypes.c_ulong),
            ("SecurityDescriptor", ctypes.c_void_p),
            ("SecurityQualityOfService", ctypes.c_void_p),
        ]

    ntdll = ctypes.WinDLL("ntdll", use_last_error=True)  # type: ignore[attr-defined]
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    ntdll.NtCreateFile.restype = ctypes.c_long
    ntdll.NtCreateFile.argtypes = (
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_uint32,
        ctypes.POINTER(OBJECT_ATTRIBUTES),
        ctypes.POINTER(IO_STATUS_BLOCK),
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
    )
    kernel32.CloseHandle.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)

    absolute = path.absolute()
    anchor = absolute.anchor
    parts = absolute.parts[1:] if anchor else absolute.parts
    if not parts or any(part in ("", ".", "..") for part in parts):
        raise SafeOpenError("Unsafe file path")
    name = parts[-1]
    _validate_entry_name(name)
    parent = Path(anchor, *parts[:-1])
    parent_fd = open_directory(parent)
    try:
        parent_handle = msvcrt.get_osfhandle(parent_fd)  # type: ignore[attr-defined]
        name_bytes = name.encode("utf-16-le")
        name_us = UNICODE_STRING(
            Length=len(name_bytes),
            MaximumLength=len(name_bytes) + 2,
            Buffer=name,
        )
        obj_attrs = OBJECT_ATTRIBUTES(
            Length=ctypes.sizeof(OBJECT_ATTRIBUTES),
            RootDirectory=parent_handle,
            ObjectName=ctypes.pointer(name_us),
            Attributes=_OBJ_CASE_INSENSITIVE,
            SecurityDescriptor=None,
            SecurityQualityOfService=None,
        )
        handle = ctypes.c_void_p()
        iosb = IO_STATUS_BLOCK()
        status = ntdll.NtCreateFile(
            ctypes.byref(handle),
            _GENERIC_READ | _GENERIC_WRITE | _SYNCHRONIZE,
            ctypes.byref(obj_attrs),
            ctypes.byref(iosb),
            None,
            _FILE_ATTRIBUTE_NORMAL,
            _FILE_SHARE_READ | _FILE_SHARE_WRITE,
            _FILE_CREATE if exclusive else _FILE_OPEN_IF,
            (
                _FILE_NON_DIRECTORY_FILE
                | _FILE_OPEN_REPARSE_POINT
                | _FILE_SYNCHRONOUS_IO_NONALERT
            ),
            None,
            0,
        )
        unsigned_status = status & 0xFFFFFFFF
        if unsigned_status == _STATUS_OBJECT_NAME_COLLISION and exclusive:
            raise FileExistsError(str(path))
        if unsigned_status != _STATUS_SUCCESS:
            raise SafeOpenError(
                "Cannot safely open or create regular file: "
                f"NT status 0x{unsigned_status:08X}"
            )
        handle_value = handle.value
        if handle_value is None:
            raise SafeOpenError("Cannot safely open or create regular file: null handle")
        try:
            fd: int = msvcrt.open_osfhandle(handle_value, _O_RDWR | _O_BINARY)  # type: ignore[attr-defined]
        except Exception:
            kernel32.CloseHandle(handle_value)
            raise
        if fd == -1:
            kernel32.CloseHandle(handle_value)
            raise SafeOpenError("Cannot adopt created file handle")
        try:
            info = os.fstat(fd)
            attributes = getattr(info, "st_file_attributes", 0)
            if attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
                raise SafeOpenError("Links and reparse points are not allowed")
            if not stat.S_ISREG(info.st_mode):
                raise SafeOpenError("Path does not name a regular file")
            return fd
        except BaseException:
            os.close(fd)
            raise
    finally:
        os.close(parent_fd)


def _open_regular_windows(path: Path) -> int:
    # Pre-check each component as defense-in-depth; the real protection is
    # the NtOpenFile + RootDirectory walk in _windows_open_relative.
    components = tuple(_windows_components(path))
    if not components:
        raise SafeOpenError("Path does not name a regular file")
    for component in components:
        try:
            if is_link_or_junction(component):
                raise SafeOpenError("Links and reparse points are not allowed")
        except OSError as error:
            raise SafeOpenError("Cannot inspect path safely") from error
    return _windows_open_relative(path, directory=False)


def open_regular_file(path: str | Path) -> int:
    """Open a regular file while rejecting links in every path component."""
    candidate = Path(path)
    if _IS_WINDOWS:
        return _open_regular_windows(candidate)
    return _open_regular_posix(candidate)


def open_or_create_regular_file(
    path: str | Path, *, exclusive: bool = False, mode: int = 0o600
) -> int:
    """Open or create a read-write regular file without following links.

    Parent components are resolved relative to retained directory handles and
    the final component is opened atomically with no-follow semantics.  When
    *exclusive* is true, an existing final component raises
    :class:`FileExistsError`.
    """
    candidate = Path(path)
    if _IS_WINDOWS:
        return _open_or_create_regular_windows(candidate, exclusive=exclusive)
    return _open_or_create_regular_posix(candidate, exclusive=exclusive, mode=mode)


def open_directory(path: str | Path) -> int:
    """Open a directory without following links in its path."""
    candidate = Path(path)
    if not _IS_WINDOWS:
        return _open_directory_posix(candidate)
    for component in _windows_components(candidate):
        if is_link_or_junction(component):
            raise SafeOpenError("Links and reparse points are not allowed")
    return _windows_open_relative(candidate, directory=True)


@contextmanager
def regular_file_descriptor(path: str | Path) -> Iterator[int]:
    """Yield a safely opened regular-file descriptor and always close it."""
    fd = open_regular_file(path)
    try:
        yield fd
    finally:
        with contextlib.suppress(OSError):
            os.close(fd)
