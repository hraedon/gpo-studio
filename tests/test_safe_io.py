from __future__ import annotations

import os
import stat
import sys
import threading
from pathlib import Path

import pytest

from gpo_studio.safe_io import (
    SafeOpenError,
    is_link_or_junction,
    iter_directory,
    open_directory,
    open_or_create_regular_file,
    open_regular_file,
    regular_file_descriptor,
)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX special-file test")
def test_iter_directory_rejects_special_file(tmp_path: Path) -> None:
    special = tmp_path / "named-pipe"
    os.mkfifo(special)

    dir_fd = open_directory(tmp_path)
    try:
        with pytest.raises(SafeOpenError, match="Unsupported file type"):
            list(iter_directory(dir_fd))
    finally:
        os.close(dir_fd)

_IS_WINDOWS = sys.platform == "win32"
windows_only = pytest.mark.skipif(
    not _IS_WINDOWS, reason="Windows-specific reparse-point test"
)


def test_open_regular_file_basic(tmp_path: Path) -> None:
    f = tmp_path / "file.txt"
    f.write_text("hello")
    fd = open_regular_file(f)
    try:
        assert os.read(fd, 5) == b"hello"
    finally:
        os.close(fd)


def test_regular_file_descriptor_closes(tmp_path: Path) -> None:
    f = tmp_path / "file.txt"
    f.write_text("world")
    with regular_file_descriptor(f) as fd:
        assert os.read(fd, 5) == b"world"
    with pytest.raises(OSError):
        os.fstat(fd)


def test_open_regular_file_rejects_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    target.write_text("data")
    link = tmp_path / "link.txt"
    os.symlink(target, link)
    with pytest.raises(SafeOpenError):
        open_regular_file(link)


def test_open_regular_file_rejects_symlink_in_path(tmp_path: Path) -> None:
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    (target_dir / "file.txt").write_text("data")
    link_dir = tmp_path / "link"
    os.symlink(target_dir, link_dir)
    with pytest.raises(SafeOpenError):
        open_regular_file(link_dir / "file.txt")


def test_open_regular_file_rejects_dotdot(tmp_path: Path) -> None:
    f = tmp_path / "file.txt"
    f.write_text("x")
    tricky = tmp_path / ".." / f.name
    with pytest.raises(SafeOpenError):
        open_regular_file(tricky)


def test_open_regular_file_rejects_nonexistent(tmp_path: Path) -> None:
    with pytest.raises(SafeOpenError):
        open_regular_file(tmp_path / "nonexistent.txt")


def test_open_directory_basic(tmp_path: Path) -> None:
    d = tmp_path / "subdir"
    d.mkdir()
    fd = open_directory(d)
    try:
        assert stat.S_ISDIR(os.fstat(fd).st_mode)
    finally:
        os.close(fd)


def test_open_directory_rejects_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    os.symlink(target, link)
    with pytest.raises(SafeOpenError):
        open_directory(link)


def test_open_directory_rejects_dotdot(tmp_path: Path) -> None:
    d = tmp_path / "subdir"
    d.mkdir()
    tricky = tmp_path / ".." / d.name
    with pytest.raises(SafeOpenError):
        open_directory(tricky)


def test_iter_directory_opens_children_relative_to_parent(tmp_path: Path) -> None:
    root = tmp_path / "root"
    nested = root / "nested"
    nested.mkdir(parents=True)
    (root / "root.txt").write_bytes(b"root")
    (nested / "child.txt").write_bytes(b"child")

    root_fd = open_directory(root)
    yielded_fds: list[int] = []
    seen: dict[str, bytes | set[str]] = {}
    try:
        for entry in iter_directory(root_fd):
            yielded_fds.append(entry.fd)
            if entry.is_directory:
                child_names: set[str] = set()
                for child in iter_directory(entry.fd):
                    yielded_fds.append(child.fd)
                    assert not child.is_directory
                    child_names.add(child.name)
                    assert os.read(child.fd, 5) == b"child"
                seen[entry.name] = child_names
            else:
                seen[entry.name] = os.read(entry.fd, 4)
    finally:
        os.close(root_fd)

    assert seen == {"nested": {"child.txt"}, "root.txt": b"root"}
    for yielded_fd in yielded_fds:
        with pytest.raises(OSError):
            os.fstat(yielded_fd)


def test_iter_directory_rejects_symlink_entry(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    target = tmp_path / "target.txt"
    target.write_text("outside")
    os.symlink(target, root / "link.txt")

    root_fd = open_directory(root)
    try:
        with pytest.raises(SafeOpenError):
            tuple(iter_directory(root_fd))
    finally:
        os.close(root_fd)


def test_is_link_or_junction_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    os.symlink(target, link)
    assert is_link_or_junction(link)


def test_is_link_or_junction_regular(tmp_path: Path) -> None:
    d = tmp_path / "dir"
    d.mkdir()
    assert not is_link_or_junction(d)
    f = tmp_path / "file.txt"
    f.write_text("x")
    assert not is_link_or_junction(f)


def test_open_regular_file_nested_dirs(tmp_path: Path) -> None:
    deep = tmp_path / "a" / "b" / "c"
    deep.mkdir(parents=True)
    f = deep / "file.txt"
    f.write_text("nested")
    fd = open_regular_file(f)
    try:
        assert os.read(fd, 6) == b"nested"
    finally:
        os.close(fd)


def test_open_regular_file_rejects_directory(tmp_path: Path) -> None:
    d = tmp_path / "dir"
    d.mkdir()
    with pytest.raises(SafeOpenError):
        open_regular_file(d)


def test_open_or_create_regular_file_creates_and_reopens(tmp_path: Path) -> None:
    path = tmp_path / "created.lock"
    fd = open_or_create_regular_file(path)
    try:
        os.write(fd, b"lock")
    finally:
        os.close(fd)

    reopened_fd = open_or_create_regular_file(path)
    try:
        assert os.read(reopened_fd, 4) == b"lock"
    finally:
        os.close(reopened_fd)


def test_open_or_create_regular_file_exclusive_rejects_existing(tmp_path: Path) -> None:
    path = tmp_path / "existing.tmp"
    path.write_text("existing")
    with pytest.raises(FileExistsError):
        open_or_create_regular_file(path, exclusive=True)
    assert path.read_text() == "existing"


def test_open_or_create_regular_file_rejects_symlink(tmp_path: Path) -> None:
    referent = tmp_path / "referent"
    referent.write_bytes(b"")
    link = tmp_path / "created.lock"
    os.symlink(referent, link)

    with pytest.raises(SafeOpenError):
        open_or_create_regular_file(link)
    assert referent.read_bytes() == b""


def test_open_or_create_regular_file_rejects_symlink_parent(tmp_path: Path) -> None:
    referent = tmp_path / "referent"
    referent.mkdir()
    link = tmp_path / "linked-parent"
    os.symlink(referent, link)

    with pytest.raises(SafeOpenError):
        open_or_create_regular_file(link / "created.lock")
    assert not (referent / "created.lock").exists()


def test_open_or_create_regular_file_rejects_directory(tmp_path: Path) -> None:
    directory = tmp_path / "created.lock"
    directory.mkdir()
    with pytest.raises(SafeOpenError):
        open_or_create_regular_file(directory)


@windows_only
def test_junction_rejected_in_file_path(tmp_path: Path) -> None:
    import subprocess

    target = tmp_path / "target"
    target.mkdir()
    (target / "file.txt").write_text("data")
    junction = tmp_path / "junction"
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(target)],
        check=True,
    )
    assert is_link_or_junction(junction)
    with pytest.raises(SafeOpenError):
        open_regular_file(junction / "file.txt")


@windows_only
def test_junction_rejected_in_directory(tmp_path: Path) -> None:
    import subprocess

    target = tmp_path / "target"
    target.mkdir()
    junction = tmp_path / "junction"
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(target)],
        check=True,
    )
    with pytest.raises(SafeOpenError):
        open_directory(junction)


@windows_only
def test_iter_directory_rejects_junction_entry(tmp_path: Path) -> None:
    import subprocess

    root = tmp_path / "root"
    root.mkdir()
    target = tmp_path / "target"
    target.mkdir()
    junction = root / "junction"
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(target)],
        check=True,
    )

    root_fd = open_directory(root)
    try:
        with pytest.raises(SafeOpenError):
            tuple(iter_directory(root_fd))
    finally:
        os.close(root_fd)


@windows_only
def test_open_or_create_regular_file_rejects_junction_parent(tmp_path: Path) -> None:
    import subprocess

    target = tmp_path / "target"
    target.mkdir()
    junction = tmp_path / "junction"
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(target)],
        check=True,
    )

    with pytest.raises(SafeOpenError):
        open_or_create_regular_file(junction / "created.lock")
    assert not (target / "created.lock").exists()


@windows_only
def test_parent_swap_race_regular_file(tmp_path: Path) -> None:
    """Concurrent parent-swap must never result in reading the wrong file."""
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    (real_dir / "file.txt").write_text("real")

    evil_dir = tmp_path / "evil"
    evil_dir.mkdir()
    (evil_dir / "file.txt").write_text("evil")

    target = tmp_path / "real" / "file.txt"
    backup = tmp_path / "real_bak"
    stop = threading.Event()
    swap_count = 0
    swap_lock = threading.Lock()

    def swap_loop() -> None:
        nonlocal swap_count
        while not stop.is_set():
            try:
                os.rename(real_dir, backup)
                try:
                    os.symlink(evil_dir, real_dir)
                    with swap_lock:
                        swap_count += 1
                except OSError:
                    os.rename(backup, real_dir)
                    continue
            except OSError:
                continue
            try:
                os.unlink(real_dir)
                os.rename(backup, real_dir)
            except OSError:
                pass

    t = threading.Thread(target=swap_loop, daemon=True)
    t.start()
    try:
        evil_reads = 0
        successful_opens = 0
        for _ in range(200):
            try:
                fd = open_regular_file(target)
                content = os.read(fd, 4)
                os.close(fd)
                successful_opens += 1
                if content == b"evil":
                    evil_reads += 1
            except (SafeOpenError, OSError):
                pass
    finally:
        stop.set()
        t.join(timeout=5)

    assert swap_count > 0, "Swap thread never successfully swapped parent"
    assert successful_opens > 0, "No successful opens (test did not exercise the race)"
    assert evil_reads == 0, f"Read evil file {evil_reads} times"


@windows_only
def test_parent_swap_race_directory(tmp_path: Path) -> None:
    """Concurrent parent-swap must never result in opening the wrong dir."""
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    (real_dir / "marker_real").write_text("")

    evil_dir = tmp_path / "evil"
    evil_dir.mkdir()
    (evil_dir / "marker_evil").write_text("")

    target = tmp_path / "real"
    backup = tmp_path / "real_bak"
    stop = threading.Event()
    swap_count = 0
    swap_lock = threading.Lock()

    real_stat = os.stat(real_dir)
    real_identity = (real_stat.st_dev, real_stat.st_ino)

    def swap_loop() -> None:
        nonlocal swap_count
        while not stop.is_set():
            try:
                os.rename(real_dir, backup)
                try:
                    os.symlink(evil_dir, real_dir)
                    with swap_lock:
                        swap_count += 1
                except OSError:
                    os.rename(backup, real_dir)
                    continue
            except OSError:
                continue
            try:
                os.unlink(real_dir)
                os.rename(backup, real_dir)
            except OSError:
                pass
            import time
            time.sleep(0.002)

    t = threading.Thread(target=swap_loop, daemon=True)
    t.start()
    try:
        evil_opens = 0
        successful_opens = 0
        for _ in range(200):
            try:
                fd = open_directory(target)
                st = os.fstat(fd)
                os.close(fd)
                successful_opens += 1
                if (st.st_dev, st.st_ino) != real_identity:
                    evil_opens += 1
            except (SafeOpenError, OSError):
                pass
    finally:
        stop.set()
        t.join(timeout=5)

    assert swap_count > 0, "Swap thread never successfully swapped parent"
    assert successful_opens > 0, "No successful opens (test did not exercise the race)"
    assert evil_opens == 0, f"Opened evil directory {evil_opens} times"


def test_no_handle_leak_on_success(tmp_path: Path) -> None:
    """Repeated open/close must not leak file descriptors."""
    f = tmp_path / "file.txt"
    f.write_text("data")
    for _ in range(100):
        fd = open_regular_file(f)
        os.close(fd)


def test_file_renameable_after_close(tmp_path: Path) -> None:
    """Closing the fd must release all locks so the file can be renamed."""
    f = tmp_path / "original.txt"
    f.write_text("data")
    fd = open_regular_file(f)
    os.close(fd)
    new_path = tmp_path / "renamed.txt"
    os.rename(f, new_path)
    assert new_path.exists()
    assert not f.exists()


def test_directory_renameable_after_close(tmp_path: Path) -> None:
    """Closing a directory fd must release all locks so it can be renamed."""
    d = tmp_path / "original_dir"
    d.mkdir()
    fd = open_directory(d)
    os.close(fd)
    new_path = tmp_path / "renamed_dir"
    os.rename(d, new_path)
    assert new_path.exists()
    assert not d.exists()


def test_directory_removable_after_close(tmp_path: Path) -> None:
    """Closing a directory fd must allow the directory to be removed."""
    d = tmp_path / "removable"
    d.mkdir()
    fd = open_directory(d)
    os.close(fd)
    os.rmdir(d)
    assert not d.exists()


@windows_only
def test_no_handle_leak_counted(tmp_path: Path) -> None:
    """Verify via GetProcessHandleCount that handles don't grow."""
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32")  # type: ignore[attr-defined]
    kernel32.GetProcessHandleCount.restype = ctypes.c_int  # type: ignore[attr-defined]
    kernel32.GetProcessHandleCount.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32))  # type: ignore[attr-defined]
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p  # type: ignore[attr-defined]

    proc = kernel32.GetCurrentProcess()
    before = ctypes.c_uint32(0)
    ok1 = kernel32.GetProcessHandleCount(proc, ctypes.byref(before))
    assert ok1, "GetProcessHandleCount failed (before)"

    f = tmp_path / "file.txt"
    f.write_text("data")
    for _ in range(200):
        fd = open_regular_file(f)
        os.close(fd)

    after = ctypes.c_uint32(0)
    ok2 = kernel32.GetProcessHandleCount(proc, ctypes.byref(after))
    assert ok2, "GetProcessHandleCount failed (after)"
    delta = after.value - before.value
    assert delta <= 2, f"Handle count grew by {delta} (before={before.value}, after={after.value})"


@windows_only
def test_phase2_regular_file_denies_ancestor_rename(tmp_path: Path) -> None:
    """The retained file handle must pin its ancestor path through phase 2."""
    import gpo_studio.safe_io as safe_io_mod

    real_a = tmp_path / "a"
    real_b = real_a / "b"
    real_b.mkdir(parents=True)
    (real_b / "file.txt").write_text("real")

    target = tmp_path / "a" / "b" / "file.txt"
    backup = tmp_path / "a_bak"
    rename_denied = threading.Event()

    def phase2_hook() -> None:
        with pytest.raises(OSError):
            os.rename(real_a, backup)
        rename_denied.set()

    safe_io_mod._windows_test_hook_phase2 = phase2_hook
    try:
        fd = open_regular_file(target)
        try:
            assert os.read(fd, 4) == b"real"
        finally:
            os.close(fd)
    finally:
        safe_io_mod._windows_test_hook_phase2 = None

    assert rename_denied.is_set(), "Ancestor rename was not denied during phase 2"
    assert real_a.is_dir()
    assert not backup.exists()


@windows_only
def test_phase2_directory_denies_ancestor_rename(tmp_path: Path) -> None:
    """The retained directory handle must pin its ancestor path through phase 2."""
    import gpo_studio.safe_io as safe_io_mod

    real_a = tmp_path / "a"
    real_b = real_a / "b"
    real_b.mkdir(parents=True)
    (real_b / "marker").write_text("")

    target = tmp_path / "a" / "b"
    backup = tmp_path / "a_bak"
    rename_denied = threading.Event()

    def phase2_hook() -> None:
        with pytest.raises(OSError):
            os.rename(real_a, backup)
        rename_denied.set()

    safe_io_mod._windows_test_hook_phase2 = phase2_hook
    try:
        fd = open_directory(target)
        try:
            assert stat.S_ISDIR(os.fstat(fd).st_mode)
        finally:
            os.close(fd)
    finally:
        safe_io_mod._windows_test_hook_phase2 = None

    assert rename_denied.is_set(), "Ancestor rename was not denied during phase 2"
    assert real_a.is_dir()
    assert not backup.exists()


@windows_only
def test_non_bmp_path_component(tmp_path: Path) -> None:
    """Path components with non-BMP characters must open correctly."""
    emoji_dir = tmp_path / "emoji_\U0001F389"
    emoji_dir.mkdir()
    f = emoji_dir / "file.txt"
    f.write_text("party")
    fd = open_regular_file(f)
    try:
        assert os.read(fd, 5) == b"party"
    finally:
        os.close(fd)
