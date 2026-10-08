#!/usr/bin/env python3
"""The second containment layer for run-requal-batch.sh: one cgroup per lane.

lane-supervisor.py contains a lane by being its subreaper. If the supervisor
itself dies, that layer is gone; this one is not. Each lane runs in its own
systemd user scope -- a cgroup nothing the lane starts can leave -- and
everything after creation is done against the cgroup directly, in
/sys/fs/cgroup, without asking the user manager anything:

  start <unit> <nonce> <cgroup-file> -- cmd...
      Create the scope with systemd-run (described with the run's nonce) and,
      INSIDE it, run `enter`, which records the scope's cgroup path to
      <cgroup-file> and then execs cmd. If systemd-run cannot create the scope
      (a name collision, an unreachable manager), cmd never runs and
      <cgroup-file> stays empty -- and the driver then touches nothing, since
      it cannot prove the unit is its own.
  enter <unit> <cgroup-file> -- cmd...
      Internal. Reads /proc/self/cgroup, refuses unless this process is in
      <unit>.scope, writes the path atomically, execs cmd.
  procs <cgroup-file>
      Print every pid in the recorded cgroup (and any cgroup below it).
      Exit 0: the answer is known (possibly empty). Exit 3: the cgroup is gone
      (empty). Exit 2: UNKNOWN -- unreadable or no recorded cgroup.
  kill <cgroup-file>
      SIGKILL everything in the recorded cgroup via cgroup.kill, falling back
      to signalling each pid. Exit 0 only if that was done (or it was gone).
  probe <nonce>
      Exit 0 if a user scope can be created at all.

Only the driver calls this. Tests substitute a stand-in through
GPO_STUDIO_REQUAL_TEST_SCOPE_TOOL, which the driver honours only with
GPO_STUDIO_REQUAL_ALLOW_TEST_SCOPE=1.
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
from pathlib import Path

CGROUP_ROOT = Path("/sys/fs/cgroup")
UNIT = re.compile(r"^gpo-studio-lane-[0-9a-f]{16}-[0-9]+$")
UNKNOWN, GONE = 2, 3


def _split(argv: list[str]) -> tuple[list[str], list[str]]:
    if "--" not in argv:
        raise SystemExit("lane-scope: missing -- before the command")
    i = argv.index("--")
    return argv[:i], argv[i + 1 :]


def _write_atomic(path: Path, text: str) -> None:
    partial = path.with_name(path.name + ".partial")
    partial.write_text(text, encoding="utf-8")
    os.replace(partial, path)


def _recorded(cgroup_file: str) -> Path | None:
    try:
        text = Path(cgroup_file).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text.startswith("/") or ".." in text.split("/"):
        return None
    return CGROUP_ROOT / text.lstrip("/")


def _pids(directory: Path) -> list[int]:
    pids: list[int] = []
    for procs in [directory / "cgroup.procs", *directory.rglob("*/cgroup.procs")]:
        pids.extend(int(line) for line in procs.read_text(encoding="utf-8").split())
    return pids


def start(argv: list[str]) -> int:
    head, command = _split(argv)
    unit, nonce, cgroup_file = head
    if not UNIT.match(unit) or not command:
        print(f"lane-scope: refusing unit name {unit!r}", file=sys.stderr)
        return 2
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
            cgroup_file,
            "--",
            *command,
        ],
    )


def enter(argv: list[str]) -> int:
    head, command = _split(argv)
    unit, cgroup_file = head
    lines = Path("/proc/self/cgroup").read_text(encoding="utf-8").splitlines()
    path = next((line[3:] for line in lines if line.startswith("0::")), "")
    if Path(path).name != f"{unit}.scope":
        print(f"lane-scope: not inside {unit}.scope ({path!r}); refusing", file=sys.stderr)
        return 125
    _write_atomic(Path(cgroup_file), path + "\n")
    os.execvp(command[0], command)


def procs(argv: list[str]) -> int:
    directory = _recorded(argv[0])
    if directory is None:
        return UNKNOWN
    if not directory.exists():
        return GONE
    try:
        pids = _pids(directory)
    except FileNotFoundError:
        return GONE  # it went while being read
    except (OSError, ValueError):
        return UNKNOWN
    print("\n".join(str(p) for p in pids))
    return 0


def kill(argv: list[str]) -> int:
    directory = _recorded(argv[0])
    if directory is None:
        return UNKNOWN
    if not directory.exists():
        return 0
    try:
        (directory / "cgroup.kill").write_text("1", encoding="utf-8")
        return 0
    except FileNotFoundError:
        return 0
    except OSError:
        pass
    try:
        pids = _pids(directory)
    except FileNotFoundError:
        return 0
    except (OSError, ValueError):
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
        [
            "systemd-run",
            "--user",
            "--scope",
            "--quiet",
            "--collect",
            f"--unit={unit}",
            "--",
            "true",
        ],
        capture_output=True,
    )
    return result.returncode


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    action, rest = sys.argv[1], sys.argv[2:]
    handlers = {"start": start, "enter": enter, "procs": procs, "kill": kill, "probe": probe}
    if action not in handlers:
        print(f"lane-scope: unknown action {action!r}", file=sys.stderr)
        return 2
    return handlers[action](rest)


if __name__ == "__main__":
    sys.exit(main())
