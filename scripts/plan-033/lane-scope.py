#!/usr/bin/env python3
"""The second containment layer for run-requal-batch.sh: one cgroup per lane.

lane-supervisor.py contains a lane by being its subreaper. If the supervisor
itself dies, that layer is gone; this one is not. Each lane runs in its own
systemd user scope -- a cgroup nothing the lane starts can leave -- and
everything after creation is done against the cgroup directly, in
/sys/fs/cgroup, without asking the user manager anything:

  start <unit> <nonce> <proof> -- cmd...
      Create the scope with systemd-run (described with the run's nonce) and,
      INSIDE it, run `enter`, which records the scope's cgroup path in the
      ownership proof <proof> and then execs cmd. If systemd-run cannot create
      the scope (a name collision, an unreachable manager), cmd never runs and
      no proof is written -- and the driver then touches nothing, since it
      cannot prove the unit is its own.
  enter <unit> <proof> -- cmd...
      Internal. Refuses unless /proc/self/cgroup puts this process in
      <unit>.scope (cgroup v2) and <proof>'s directory is private; writes the
      proof exclusively at 0600, fsyncs, renames it into place, execs cmd.
  procs <unit> <proof>
      Exit 0: EMPTY, certified by two consecutive reads in which the scope's
      own cgroup.events says `populated 0` (which covers the whole subtree)
      and a walk of the subtree finds no pid. Exit 1: POPULATED (any pids found
      on stdout -- possibly none, if only cgroup.events saw them). Exit 3:
      GONE -- the scope's directory is absent (ENOENT), confirmed by a second
      look. Exit 2: UNKNOWN -- anything else: an invalid proof, a permission
      or I/O error, a subtree that would not hold still.
  kill <unit> <proof>
      SIGKILL everything in the scope via cgroup.kill, falling back to each pid
      of a subtree walk. Exit 0 if that was done (or the scope is GONE), 1 if
      it could not be, 2 if the proof is invalid or the scope's state unknown.
  probe <nonce>
      Exit 0 if a user scope can be created at all.

The rule throughout: only a positive, specific signal means "gone" or
"empty". An error is never quietly turned into either -- that is what lets the
driver tell "could not tell" from "nothing left".

Only the driver calls this. Tests substitute a stand-in through
GPO_STUDIO_REQUAL_TEST_SCOPE_TOOL, which the driver honours only with
GPO_STUDIO_REQUAL_ALLOW_TEST_SCOPE=1.
"""

from __future__ import annotations

import os
import re
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

CGROUP_ROOT = Path("/sys/fs/cgroup")
UNIT = re.compile(r"^gpo-studio-lane-[0-9a-f]{16}-[0-9]+$")
EMPTY, POPULATED, UNKNOWN, GONE = 0, 1, 2, 3
#: How often a walk is restarted because a descendant cgroup vanished under it.
WALKS = 5
#: Pause between the two reads that certify EMPTY, and between the two looks
#: that confirm GONE.
SETTLE = 0.1


class Unknown(Exception):
    """The scope's state cannot be established."""


class _Vanished(Exception):
    """A descendant cgroup disappeared mid-walk: walk again."""


# --- the ownership proof -----------------------------------------------------


def _private_dir(directory: Path) -> None:
    """Raise Unknown unless `directory` is ours and nobody else can write it."""
    try:
        st = os.stat(directory, follow_symlinks=False)
    except OSError as error:
        raise Unknown(f"proof directory {directory}: {error}") from error
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
        raise Unknown(f"proof directory {directory} is not a directory owned by this user")
    if st.st_mode & 0o022:
        raise Unknown(f"proof directory {directory} is group- or world-writable")


def write_proof(proof: Path, cgroup_path: str) -> None:
    """Publish the proof: exclusive 0600 temp file, fsync, rename, fsync dir."""
    _private_dir(proof.parent)
    temp = proof.with_name(f".{proof.name}.{os.getpid()}.tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(fd, 0o600)  # whatever the umask said
        os.write(fd, (cgroup_path + "\n").encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(temp, proof)
    dir_fd = os.open(proof.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def read_proof(unit: str, proof: Path) -> Path:
    """The scope's cgroup directory, from a proof this user wrote for `unit`.

    Raises Unknown for anything else: a bad unit name, a proof that is not a
    regular file owned by this user, writable by group or world, in a
    directory others can write, or naming a cgroup other than <unit>.scope.
    """
    if not UNIT.match(unit):
        raise Unknown(f"not a lane unit name: {unit!r}")
    _private_dir(proof.parent)
    try:
        fd = os.open(proof, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as error:
        raise Unknown(f"proof {proof}: {error}") from error
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o022:
            raise Unknown(f"proof {proof} is not a private regular file of this user")
        text = os.read(fd, 4096).decode("utf-8", "replace").strip()
    finally:
        os.close(fd)
    parts = text.split("/")
    if not text.startswith("/") or ".." in parts or parts[-1] != f"{unit}.scope":
        raise Unknown(f"proof {proof} does not name {unit}.scope")
    return CGROUP_ROOT / text.lstrip("/")


# --- reading the scope -------------------------------------------------------


def _read(path: Path) -> str:
    """Read a cgroup file. Errors propagate; nothing is created."""
    fd = os.open(path, os.O_RDONLY)
    try:
        chunks = []
        while chunk := os.read(fd, 65536):
            chunks.append(chunk)
        return b"".join(chunks).decode("ascii")
    finally:
        os.close(fd)


def _scan(directory: Path) -> list[Path]:
    """Child cgroups of `directory`. Errors propagate (no silent rglob)."""
    with os.scandir(directory) as entries:
        return [Path(e.path) for e in entries if e.is_dir(follow_symlinks=False)]


def absent(directory: Path) -> bool:
    """True only if `directory` is absent (ENOENT) on two looks; any other
    error raises Unknown."""
    for look in range(2):
        try:
            os.stat(directory, follow_symlinks=False)
            return False
        except FileNotFoundError:
            if look == 0:
                time.sleep(SETTLE)
        except OSError as error:
            raise Unknown(f"stat {directory}: {error}") from error
    return True


def _populated(directory: Path) -> bool:
    """The scope's own `populated` flag, which covers its whole subtree."""
    for line in _read(directory / "cgroup.events").splitlines():
        key, _, value = line.partition(" ")
        if key == "populated":
            if value.strip() not in ("0", "1"):
                raise Unknown(f"unreadable populated value {value!r}")
            return value.strip() == "1"
    raise Unknown(f"{directory}/cgroup.events has no populated field")


def _walk_once(top: Path) -> list[int]:
    pids: list[int] = []
    stack = [top]
    while stack:
        directory = stack.pop()
        try:
            text = _read(directory / "cgroup.procs")
            children = _scan(directory)
        except FileNotFoundError:
            if directory == top:
                raise
            raise _Vanished() from None
        try:
            pids.extend(int(token) for token in text.split())
        except ValueError as error:
            raise Unknown(f"{directory}/cgroup.procs: {error}") from error
        stack.extend(children)
    return pids


def walk(top: Path) -> list[int]:
    """Every pid in `top` and below. A descendant vanishing means walk again;
    `top` vanishing raises FileNotFoundError; anything else raises."""
    for _ in range(WALKS):
        try:
            return _walk_once(top)
        except _Vanished:
            continue
    raise Unknown(f"the subtree of {top} kept changing")


def observe(directory: Path) -> tuple[int, list[int]]:
    """One read: (EMPTY|POPULATED|GONE, pids). Raises Unknown otherwise."""
    try:
        populated = _populated(directory)
        pids = walk(directory)
    except FileNotFoundError:
        if absent(directory):
            return GONE, []
        raise Unknown(f"{directory}: its files vanished but the directory remains") from None
    except OSError as error:
        raise Unknown(f"{directory}: {error}") from error
    return (POPULATED if populated or pids else EMPTY), pids


def certify(directory: Path) -> tuple[int, list[int]]:
    """EMPTY only on two consecutive EMPTY reads; otherwise the second answer
    that is not EMPTY (or the first, if it was not)."""
    state, pids = observe(directory)
    if state != EMPTY:
        return state, pids
    time.sleep(SETTLE)
    return observe(directory)


# --- actions -----------------------------------------------------------------


def _split(argv: list[str]) -> tuple[list[str], list[str]]:
    if "--" not in argv:
        raise SystemExit("lane-scope: missing -- before the command")
    i = argv.index("--")
    return argv[:i], argv[i + 1 :]


def start(argv: list[str]) -> int:
    head, command = _split(argv)
    unit, nonce, proof = head
    if not UNIT.match(unit) or not command:
        print(f"lane-scope: refusing unit name {unit!r}", file=sys.stderr)
        return UNKNOWN
    os.execvp(
        "systemd-run",
        [
            "systemd-run",
            "--user",
            "--scope",
            "--quiet",
            "--collect",
            f"--unit={unit}",
            f"--description=gpo-studio lane {nonce}",
            "--",
            sys.executable,
            os.path.abspath(__file__),
            "enter",
            unit,
            proof,
            "--",
            *command,
        ],
    )


def enter(argv: list[str]) -> int:
    head, command = _split(argv)
    unit, proof = head
    lines = Path("/proc/self/cgroup").read_text(encoding="utf-8").splitlines()
    path = next((line[3:] for line in lines if line.startswith("0::")), "")
    if Path(path).name != f"{unit}.scope":
        print(f"lane-scope: not inside {unit}.scope ({path!r}); refusing", file=sys.stderr)
        return 125
    try:
        write_proof(Path(proof), path)
    except (OSError, Unknown) as error:
        print(f"lane-scope: cannot publish the ownership proof: {error}", file=sys.stderr)
        return 125
    os.execvp(command[0], command)


def procs(argv: list[str]) -> int:
    unit, proof = argv
    try:
        state, pids = certify(read_proof(unit, Path(proof)))
    except Unknown as error:
        print(f"lane-scope: unknown: {error}", file=sys.stderr)
        return UNKNOWN
    if pids:
        print("\n".join(str(p) for p in pids))
    return state


def kill(argv: list[str]) -> int:
    unit, proof = argv
    try:
        directory = read_proof(unit, Path(proof))
        if absent(directory):
            return 0
    except Unknown as error:
        print(f"lane-scope: unknown: {error}", file=sys.stderr)
        return UNKNOWN
    try:
        fd = os.open(directory / "cgroup.kill", os.O_WRONLY)  # never O_CREAT
        try:
            os.write(fd, b"1")
        finally:
            os.close(fd)
        return 0
    except FileNotFoundError:
        try:
            if absent(directory):
                return 0
        except Unknown:
            return UNKNOWN
        # The directory is there but cgroup.kill is not: a kernel before 5.14.
    except OSError as error:
        print(f"lane-scope: cgroup.kill: {error}; signalling each pid", file=sys.stderr)
    try:
        pids = walk(directory)
    except (OSError, Unknown) as error:
        print(f"lane-scope: cannot list the scope to kill it: {error}", file=sys.stderr)
        return 1
    failed = False
    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError:
            failed = True
    return 1 if failed else 0


def probe(argv: list[str]) -> int:
    unit = f"gpo-studio-lane-{argv[0]}-0"
    result = subprocess.run(
        ["systemd-run", "--user", "--scope", "--quiet", "--collect", f"--unit={unit}", "--"]
        + ["true"],
        capture_output=True,
    )
    return result.returncode


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        return UNKNOWN
    action, rest = sys.argv[1], sys.argv[2:]
    handlers = {"start": start, "enter": enter, "procs": procs, "kill": kill, "probe": probe}
    if action not in handlers:
        print(f"lane-scope: unknown action {action!r}", file=sys.stderr)
        return UNKNOWN
    return handlers[action](rest)


if __name__ == "__main__":
    sys.exit(main())
