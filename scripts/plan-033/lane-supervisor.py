#!/usr/bin/env python3
"""Run one lane command under a wall-clock deadline and leave nothing behind.

run-requal-batch.sh starts every lane (and WP-0's printed finalizer) through
this. Two things a shell watchdog could not guarantee are the reason it exists:

* **Containment.** The supervisor makes itself a child SUBREAPER
  (``PR_SET_CHILD_SUBREAPER``), so every process the lane starts stays its
  descendant even after its own parent dies -- including one that detached
  with ``setsid`` and a double fork before anything was killed. A walk of the
  supervisor's process tree therefore finds the whole lane, always.
* **Cleanup on every exit.** When the lane's leader exits -- by itself, with
  any status -- or the deadline passes, everything still running under the
  supervisor is sent SIGTERM, then SIGKILL after the grace, and reaped. The
  batch never moves to the next lane with part of this one still running.

The leader runs as the leader of a new session, with stdin from /dev/null and
stdout/stderr appended to the lane log. The supervisor exits with the leader's
own status (128 + N for a signal), or 124 when the deadline killed it; the
report file says which, because a lane can exit 124 by itself.

Linux only: it refuses to run a lane it cannot contain.
"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

PR_SET_CHILD_SUBREAPER = 36
TIMED_OUT_STATUS = 124


def become_subreaper() -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) != 0:
        err = ctypes.get_errno()
        raise OSError(err, f"prctl(PR_SET_CHILD_SUBREAPER) failed: {os.strerror(err)}")


def descendants(root: int) -> list[int]:
    """Every live process under `root`, from /proc (zombies excluded)."""
    children: dict[int, list[int]] = {}
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            stat = Path(f"/proc/{entry}/stat").read_text(encoding="utf-8")
        except OSError:
            continue
        fields = stat.rsplit(")", 1)[1].split()
        if fields[0] == "Z":
            continue
        children.setdefault(int(fields[1]), []).append(int(entry))
    found: list[int] = []
    stack = [root]
    while stack:
        for child in children.get(stack.pop(), []):
            found.append(child)
            stack.append(child)
    return found


def log_line(log: Path, message: str) -> None:
    with log.open("a", encoding="utf-8") as fh:
        fh.write(f"=== watchdog: {message}\n")


class Reaper:
    """Reaps every child of the supervisor, remembering the leader's status."""

    def __init__(self, leader: int) -> None:
        self.leader = leader
        self.leader_status: int | None = None

    def reap(self) -> bool:
        """Reap what has exited. False once the supervisor has no children."""
        while True:
            try:
                pid, status = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                return False
            if pid == 0:
                return True
            if pid == self.leader:
                code = os.waitstatus_to_exitcode(status)
                # A signal shows as -N; report it the way a shell would.
                self.leader_status = 128 - code if code < 0 else code


def signal_all(pids: list[int], sig: int) -> None:
    for pid in pids:
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, sig)


def contain(reaper: Reaper, leader: int, grace: float) -> int:
    """TERM, then KILL, everything under the supervisor; reap it. Returns how
    many processes were still running when cleanup began."""
    me = os.getpid()
    initial = descendants(me)
    if not initial:
        while reaper.reap():
            time.sleep(0.05)
        return 0
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(leader, signal.SIGTERM)
    signal_all(initial, signal.SIGTERM)
    end = time.monotonic() + grace
    while time.monotonic() < end:
        reaper.reap()
        if not descendants(me):
            break
        time.sleep(0.1)
    # Killing a parent re-parents its children to us, so repeat until empty.
    for _ in range(50):
        left = descendants(me)
        if not left:
            break
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(leader, signal.SIGKILL)
        signal_all(left, signal.SIGKILL)
        time.sleep(0.1)
        reaper.reap()
    while reaper.reap():
        time.sleep(0.05)
    return len(initial)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--deadline", type=int, required=True, help="epoch seconds")
    parser.add_argument("--grace", type=float, default=15.0)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("no command")

    def report(status: int, timed_out: bool, strays: int) -> int:
        args.report.write_text(
            json.dumps({"status": status, "timed_out": timed_out, "killed": strays}) + "\n",
            encoding="utf-8",
        )
        return status

    if not sys.platform.startswith("linux"):
        print("lane-supervisor: refusing: lane containment needs Linux", file=sys.stderr)
        return 2
    become_subreaper()
    if time.time() >= args.deadline:
        log_line(args.log, f"no budget left to start: {' '.join(command)}")
        return report(TIMED_OUT_STATUS, True, 0)

    # SIGTERM to the supervisor (an operator stopping the batch) ends the lane too.
    stopping = False

    def on_term(signum: int, frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGINT, on_term)

    with args.log.open("ab") as out:
        proc = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    leader = proc.pid
    reaper = Reaper(leader)
    timed_out = False
    while reaper.leader_status is None:
        if time.time() >= args.deadline:
            timed_out = True
            log_line(
                args.log,
                f"lane budget exhausted; killing process group {leader} and every descendant",
            )
            break
        if stopping:
            log_line(args.log, "supervisor stopped; killing the lane")
            break
        reaper.reap()
        time.sleep(0.2)
    strays = contain(reaper, leader, args.grace)
    # The reaper collected the leader's status; tell Popen so it never waits.
    proc.returncode = reaper.leader_status if reaper.leader_status is not None else -1
    if not timed_out and not stopping and strays:
        log_line(args.log, f"{strays} process(es) outlived the lane's leader; killed")
    if timed_out:
        return report(TIMED_OUT_STATUS, True, strays)
    status = reaper.leader_status if reaper.leader_status is not None else 128 + signal.SIGTERM
    return report(status, False, strays)


if __name__ == "__main__":
    sys.exit(main())
