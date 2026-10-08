"""lane-scope.py, the driver's second containment layer, read in isolation.

Its one rule: only a positive, specific signal means "gone" or "empty". Most
of these run the module's own functions against a synthetic cgroup tree under
a temporary root (no user manager needed), injecting the errors and races
review found: permission and I/O errors, a descendant vanishing or being born
mid-walk, a `populated` flag the walk cannot explain, and an ownership proof
someone else could write. The last test runs against a real user scope when a
manager is available.
"""

from __future__ import annotations

import errno
import importlib.util
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCOPE_TOOL = REPO_ROOT / "scripts" / "plan-033" / "lane-scope.py"

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="cgroups are Linux")

UNIT = "gpo-studio-lane-0123456789abcdef-1"
CGROUP = f"/user.slice/app.slice/{UNIT}.scope"


@pytest.fixture
def ls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ModuleType]:
    spec = importlib.util.spec_from_file_location("lane_scope", SCOPE_TOOL)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "CGROUP_ROOT", tmp_path / "cgroup")
    monkeypatch.setattr(module, "SETTLE", 0.0)
    yield module


def _make(root: Path, rel: str, procs: list[int] | None = None, populated: int = 0) -> Path:
    directory = root / rel.lstrip("/")
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "cgroup.procs").write_text("".join(f"{p}\n" for p in procs or []))
    (directory / "cgroup.events").write_text(f"populated {populated}\nfrozen 0\n")
    return directory


def _proof(tmp_path: Path, content: str = CGROUP, mode: int = 0o600) -> Path:
    private = tmp_path / "private"
    private.mkdir(mode=0o700, exist_ok=True)
    proof = private / "scope-proof"
    proof.write_text(content + "\n")
    proof.chmod(mode)
    return proof


# --- the proof ---------------------------------------------------------------


def test_the_proof_is_published_private_whatever_the_umask(
    ls: ModuleType, tmp_path: Path
) -> None:
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    old = os.umask(0o002)
    try:
        ls.write_proof(private / "scope-proof", CGROUP)
    finally:
        os.umask(old)
    assert (private / "scope-proof").stat().st_mode & 0o777 == 0o600
    assert ls.read_proof(UNIT, private / "scope-proof") == ls.CGROUP_ROOT / CGROUP.lstrip("/")
    assert [p.name for p in private.iterdir()] == ["scope-proof"]


@pytest.mark.parametrize(
    "case",
    ["group-writable-proof", "shared-dir", "other-unit", "dotdot", "symlink", "bad-unit-name"],
)
def test_an_untrustworthy_proof_is_unknown(ls: ModuleType, tmp_path: Path, case: str) -> None:
    unit = UNIT
    if case == "group-writable-proof":
        proof = _proof(tmp_path, mode=0o620)
    elif case == "shared-dir":
        proof = _proof(tmp_path)
        proof.parent.chmod(0o770)
    elif case == "other-unit":
        proof = _proof(tmp_path, "/user.slice/app.slice/someone-else.scope")
    elif case == "dotdot":
        proof = _proof(tmp_path, f"/user.slice/../../etc/{UNIT}.scope")
    elif case == "symlink":
        real = _proof(tmp_path)
        proof = real.with_name("link")
        proof.symlink_to(real)
    else:
        proof = _proof(tmp_path)
        unit = "some-other.unit"
    with pytest.raises(ls.Unknown):
        ls.read_proof(unit, proof)
    if case == "shared-dir":
        proof.parent.chmod(0o700)


def test_the_proof_cannot_be_published_into_a_shared_directory(
    ls: ModuleType, tmp_path: Path
) -> None:
    shared = tmp_path / "shared"
    shared.mkdir(mode=0o770)
    shared.chmod(0o770)
    with pytest.raises(ls.Unknown):
        ls.write_proof(shared / "scope-proof", CGROUP)


# --- gone, empty, unknown ----------------------------------------------------


def test_only_enoent_on_the_scope_itself_is_gone(ls: ModuleType, tmp_path: Path) -> None:
    directory = ls.CGROUP_ROOT / CGROUP.lstrip("/")
    assert ls.certify(directory) == (ls.GONE, [])


def test_a_scope_whose_files_vanished_but_whose_directory_remains_is_unknown(
    ls: ModuleType, tmp_path: Path
) -> None:
    directory = _make(ls.CGROUP_ROOT, CGROUP)
    (directory / "cgroup.events").unlink()
    with pytest.raises(ls.Unknown):
        ls.certify(directory)


@pytest.mark.parametrize("code", [errno.EACCES, errno.EIO])
def test_a_stat_error_is_unknown_not_gone(
    ls: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    """Path.exists() turns EACCES/EIO into False; the scope must not."""
    directory = ls.CGROUP_ROOT / CGROUP.lstrip("/")
    real_stat = os.stat

    def failing_stat(path: object, *args: object, **kwargs: object) -> os.stat_result:
        if Path(str(path)) == directory:
            raise OSError(code, os.strerror(code), str(path))
        return real_stat(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(ls.os, "stat", failing_stat)
    with pytest.raises(ls.Unknown):
        ls.certify(directory)


@pytest.mark.parametrize("code", [errno.EACCES, errno.EIO])
def test_a_traversal_error_is_unknown_not_empty(
    ls: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    """rglob suppresses scan errors; the walk must propagate them."""
    directory = _make(ls.CGROUP_ROOT, CGROUP)
    _make(directory, "child", [4242], populated=1)
    real_scandir = os.scandir

    def failing_scandir(path: object) -> object:
        if Path(str(path)) == directory:
            raise OSError(code, os.strerror(code), str(path))
        return real_scandir(path)  # type: ignore[call-overload]

    monkeypatch.setattr(ls.os, "scandir", failing_scandir)
    with pytest.raises(ls.Unknown):
        ls.certify(directory)


def test_an_unreadable_descendant_is_unknown(ls: ModuleType, tmp_path: Path) -> None:
    directory = _make(ls.CGROUP_ROOT, CGROUP)
    child = _make(directory, "child")
    child.chmod(0o000)
    try:
        with pytest.raises(ls.Unknown):
            ls.certify(directory)
    finally:
        child.chmod(0o755)


def test_a_vanishing_descendant_means_walk_again_not_gone(
    ls: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review round 3: one descendant's ENOENT was taken for the whole scope's."""
    directory = _make(ls.CGROUP_ROOT, CGROUP, [1111], populated=1)
    child = _make(directory, "child")
    real_read: Callable[[Path], str] = ls._read
    vanished: list[bool] = []

    def racing_read(path: Path) -> str:
        if path == child / "cgroup.procs" and not vanished:
            shutil.rmtree(child)
            vanished.append(True)
        return real_read(path)

    monkeypatch.setattr(ls, "_read", racing_read)
    assert ls.certify(directory) == (ls.POPULATED, [1111])
    assert vanished


def test_a_birth_into_a_new_child_cgroup_mid_walk_is_populated(
    ls: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The walk sees no pid -- the process moved into a child created after the
    walk listed the tree -- but the scope's own populated flag covers it."""
    directory = _make(ls.CGROUP_ROOT, CGROUP, [], populated=1)
    state, pids = ls.certify(directory)
    assert (state, pids) == (ls.POPULATED, [])


def test_empty_needs_two_consecutive_empty_reads(
    ls: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = _make(ls.CGROUP_ROOT, CGROUP)
    real_observe: Callable[[Path], tuple[int, list[int]]] = ls.observe
    calls = []

    def observe_then_fork(path: Path) -> tuple[int, list[int]]:
        calls.append(path)
        if len(calls) == 2:  # a process arrives between the two reads
            _make(directory, "late-child", [2222], populated=1)
            (directory / "cgroup.events").write_text("populated 1\n")
        return real_observe(path)

    monkeypatch.setattr(ls, "observe", observe_then_fork)
    assert ls.certify(directory) == (ls.POPULATED, [2222])
    assert len(calls) == 2


def test_a_quiet_scope_is_certified_empty(ls: ModuleType, tmp_path: Path) -> None:
    directory = _make(ls.CGROUP_ROOT, CGROUP)
    _make(directory, "child")
    assert ls.certify(directory) == (ls.EMPTY, [])


def test_kill_never_creates_cgroup_kill_and_falls_back_to_each_pid(
    ls: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On a kernel without cgroup.kill the file is absent; opening it must not
    create it (O_CREAT), and every pid in the subtree is signalled instead."""
    directory = _make(ls.CGROUP_ROOT, CGROUP, [3333])
    _make(directory, "child", [4444])
    signalled: list[int] = []
    monkeypatch.setattr(ls.os, "kill", lambda pid, sig: signalled.append(pid))
    proof = _proof(tmp_path)
    assert ls.kill([UNIT, str(proof)]) == 0
    assert not (directory / "cgroup.kill").exists()
    assert sorted(signalled) == [3333, 4444]


def test_procs_exit_codes_through_the_command_line(tmp_path: Path) -> None:
    """No proof is UNKNOWN (2) -- never GONE or EMPTY."""
    missing = subprocess.run(
        ["python3", str(SCOPE_TOOL), "procs", UNIT, str(tmp_path / "never-written")],
        capture_output=True,
    )
    assert missing.returncode == 2
    bad_unit = subprocess.run(
        ["python3", str(SCOPE_TOOL), "start", "some-other.unit", "n", str(tmp_path / "c"), "--"]
        + ["true"],
        capture_output=True,
    )
    assert bad_unit.returncode == 2


# --- against a real user scope ------------------------------------------------


def _user_scopes() -> bool:
    if shutil.which("systemd-run") is None:
        return False
    probe = subprocess.run(
        ["systemd-run", "--user", "--scope", "--quiet", "--collect", "--", "true"],
        capture_output=True,
        timeout=30,
    )
    return probe.returncode == 0


def _tool(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["python3", str(SCOPE_TOOL), *args], capture_output=True, text=True, timeout=60
    )


def test_against_a_real_user_scope(tmp_path: Path) -> None:
    """Integration: a collision runs nothing and leaves the existing unit
    alone; a process that moves into a CHILD cgroup is still seen (populated)
    and killed; the scope is then certified empty or gone."""
    if not _user_scopes():
        pytest.skip("no systemd user manager")
    nonce = os.urandom(8).hex()
    taken = f"gpo-studio-lane-{nonce}-1"
    holder = subprocess.Popen(
        ["systemd-run", "--user", "--scope", "--quiet", "--collect", f"--unit={taken}", "--"]
        + ["sleep", "300"]
    )
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    try:
        time.sleep(1)
        marker = tmp_path / "ran"
        collided = _tool("start", taken, nonce, str(private / "proof1"), "--", "touch", str(marker))
        assert collided.returncode != 0
        assert not marker.exists(), "the command ran without its own scope"
        assert not (private / "proof1").exists()
        assert holder.poll() is None, "the existing unit was disturbed"

        own = f"gpo-studio-lane-{nonce}-2"
        proof = private / "proof2"
        # The lane moves a child process into a child cgroup of its scope.
        script = (
            'cg=/sys/fs/cgroup$(sed -n "s/^0:://p" /proc/self/cgroup); '
            'mkdir "$cg/child" || exit 9; sleep 300 & echo $! > "$cg/child/cgroup.procs"; wait'
        )
        runner = subprocess.Popen(
            ["python3", str(SCOPE_TOOL), "start", own, nonce, str(proof), "--"]
            + ["bash", "-c", script]
        )
        deadline = time.monotonic() + 20
        while not proof.exists():
            assert time.monotonic() < deadline, "the scope never published its proof"
            time.sleep(0.1)
        assert proof.stat().st_mode & 0o777 == 0o600
        time.sleep(1)
        listed = _tool("procs", own, str(proof))
        assert listed.returncode == 1, listed  # POPULATED
        in_child = [
            pid
            for pid in listed.stdout.split()
            if Path(f"/proc/{pid}/cgroup").read_text().strip().endswith(f"{own}.scope/child")
        ]
        assert in_child, listed.stdout  # the walk reached the child cgroup
        assert _tool("kill", own, str(proof)).returncode == 0
        runner.wait(timeout=20)
        deadline = time.monotonic() + 20
        while (after := _tool("procs", own, str(proof))).returncode not in (0, 3):
            assert time.monotonic() < deadline, after
            time.sleep(0.2)
    finally:
        holder.terminate()
        holder.wait()
