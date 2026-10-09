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
own status (128 + N for a signal; 127 or 126, with a watchdog line in the log,
when its command could not be started at all -- not found, or not
executable), 124 when the deadline killed it, or 128 + N
when the supervisor itself was stopped by signal N -- TERM, INT or HUP; the
driver's SIGUSR1 and cancel file count as TERM -- (a cancellation, whatever
the leader then exited with, and whether or not the lane had started); the
report file says which, because a lane can exit 124 or 143 by itself. When
it can no longer vouch for the lane's containment it exits 125 and writes NO
report, which the driver records as lost containment.

run-requal-batch.sh also runs the supervisor inside a systemd user scope, so
that if the supervisor itself dies (SIGKILL), the driver can still find and
kill everything the lane started.

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
#: Exit status when the supervisor can no longer vouch for the lane's
#: containment; it then writes NO report, which the driver records as lost
#: containment (the driver's own CONTAINMENT_LOST_STATUS is the same 125).
CONTAINMENT_LOST_STATUS = 125
#: How long a closed launch gate's wrapper may take to exit (it exits at once
#: on EOF; this only covers its interpreter starting up). Past it, the
#: wrapper is killed and containment is reported lost. With the kill grace,
#: it stays inside the driver's stop grace (kill grace + 15 s).
GATE_CLOSE_TIMEOUT = 10.0


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


def cancellation_signal(pending: set[signal.Signals]) -> int:
    """The signal a cancellation found PENDING at the final check is reported
    as. Several may be pending and their order is lost, so take the one the
    kernel would have delivered first had they been unblocked (the lowest
    number), counting the driver's SIGUSR1 as the TERM it stands for."""
    return min(signal.SIGTERM if sig == signal.SIGUSR1 else sig for sig in pending)


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


#: The launch gate's exec wrapper. It inherits the supervisor's blocked
#: cancellation signals; it waits for the gate byte (EOF -- the supervisor
#: cancelled -- means exit 125 having run nothing), then gives the lane a clean
#: signal state -- every catchable signal at its default disposition and an
#: empty signal mask -- and execs the lane's command in its own place. Not
#: just the supervisor's own signals: a blocked mask and an ignored
#: disposition both survive exec, so whatever the supervisor's parent blocked
#: or ignored (bash ignores SIGINT and SIGQUIT for a background job; Python
#: ignores SIGPIPE and SIGXFSZ) would otherwise reach the lane. A command that cannot be
#: exec'd at all exits as a shell would -- 127 when it is not found, 126 when
#: it is found but cannot be executed -- with a watchdog line in the log, so
#: an unstartable lane is never mistaken for one that ran and failed with 1.
GATE = """\
import os, signal, sys
fd = int(sys.argv[1])
opened = os.read(fd, 1) == b"1"
os.close(fd)
if not opened:
    os._exit(125)
for sig in signal.valid_signals():
    if sig not in (signal.SIGKILL, signal.SIGSTOP):
        try:
            signal.signal(sig, signal.SIG_DFL)
        except (OSError, ValueError):
            pass
signal.pthread_sigmask(signal.SIG_SETMASK, ())
try:
    os.execvp(sys.argv[2], sys.argv[2:])
except OSError as exc:
    missing = isinstance(exc, FileNotFoundError)
    why = "not found" if missing else "not executable"
    line = f"=== watchdog: cannot start the lane command {sys.argv[2]}: {why} ({exc.strerror})"
    os.write(2, (line + "; it never ran\\n").encode())
    os._exit(127 if missing else 126)
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--deadline", type=int, required=True, help="epoch seconds")
    parser.add_argument("--grace", type=float, default=15.0)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    # The driver's cancellation channel: a file it creates when it is stopped.
    # Unlike a signal it cannot arrive before this process is ready for it --
    # it is checked before the lane is started and on every poll.
    parser.add_argument("--cancel-file", type=Path)
    # Created once this process is ready for SIGUSR1 (handlers installed and
    # the cancellation signals blocked); the driver signals only after that.
    parser.add_argument("--ready-file", type=Path)
    # TEST ONLY (the driver passes them only on its test scope stand-in):
    # pause before the final cancellation check, or between it and the gate.
    parser.add_argument("--test-pause-before-check", type=float, default=0.0)
    parser.add_argument("--test-pause-before-release", type=float, default=0.0)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("no command")

    def report(status: int, timed_out: bool, strays: int, cancelled: bool = False) -> int:
        record = {
            "status": status, "timed_out": timed_out, "cancelled": cancelled, "killed": strays
        }
        # Atomically: a supervisor killed mid-write leaves no report at all,
        # never a partial one.
        partial = args.report.with_name(args.report.name + ".partial")
        partial.write_text(json.dumps(record) + "\n", encoding="utf-8")
        os.replace(partial, args.report)
        return status

    if not sys.platform.startswith("linux"):
        print("lane-supervisor: refusing: lane containment needs Linux", file=sys.stderr)
        return 2
    become_subreaper()
    if time.time() >= args.deadline:
        log_line(args.log, f"no budget left to start: {' '.join(command)}")
        return report(TIMED_OUT_STATUS, True, 0)

    # SIGTERM/SIGINT/SIGHUP to the supervisor (an operator stopping the batch,
    # a hangup) ends the lane too, and is reported as a CANCELLATION with
    # 128 + that signal: whatever the leader then exits with (a lane that traps
    # TERM may well exit 0) is not its verdict.
    stopping = 0

    def on_term(signum: int, frame: object) -> None:
        nonlocal stopping
        stopping = stopping or signum

    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGINT, on_term)
    signal.signal(signal.SIGHUP, on_term)
    # SIGUSR1 is the driver's "cancel now", sent in addition to the cancel file.
    signal.signal(signal.SIGUSR1, lambda signum, frame: on_term(signal.SIGTERM, frame))

    def cancel_requested() -> bool:
        return args.cancel_file is not None and args.cancel_file.exists()

    # THE LAUNCH GATE. The final cancellation check (step 3) is the lane's
    # ADMISSION BOUNDARY. The guarantee: a cancellation observed at or before
    # that check -- before the gate opens -- prevents every lane command; one
    # that arrives after it is a mid-lane cancellation (the lane is killed,
    # contained and recorded as cancelled by the loop below). There is no
    # third case:
    #   1. The cancellation signals are BLOCKED before the final check, so one
    #      arriving from here on stays pending instead of being missed.
    #   2. The lane is forked held behind a gate: a minimal exec wrapper
    #      (GATE, below) whose first act is to read one byte from a pipe, and
    #      only then exec the lane's command. Nothing of the lane has run.
    #   3. Final check: the cancel file, and any cancellation signal pending.
    #      If either is present the gate is closed without a byte -- the child
    #      reads EOF and exits 125 without exec -- and the lane is recorded
    #      cancelled having run nothing, with 128 + the cancelling signal (the
    #      cancel file reads as TERM).
    #   4. Otherwise the gate opens (one byte) and the signals are unblocked:
    #      a signal that arrived after the check is delivered now, and is the
    #      mid-lane cancellation of case "after".
    cancel_signals = {signal.SIGTERM, signal.SIGINT, signal.SIGHUP, signal.SIGUSR1}
    previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, cancel_signals)
    if args.ready_file is not None:
        args.ready_file.touch()

    gate_read, gate_write = os.pipe()
    try:
        with args.log.open("ab") as out:
            proc = subprocess.Popen(
                [sys.executable, "-I", "-c", GATE, str(gate_read), *command],
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                pass_fds=(gate_read,),
            )
    except BaseException as exc:
        # Neither end of the gate pipe may outlive a launch that failed.
        os.close(gate_read)
        os.close(gate_write)
        if not isinstance(exc, OSError):
            raise
        # Nothing of the lane ran (Popen reaps a child whose exec failed), so
        # containment holds: the lane is reported unstartable, as the gate
        # reports a command it cannot exec -- 127 not found, 126 otherwise.
        message = f"could not start the lane's launch gate ({exc}); the lane never ran"
        print(f"lane-supervisor: {message}", file=sys.stderr)
        with contextlib.suppress(OSError):
            log_line(args.log, message)
        return report(127 if isinstance(exc, FileNotFoundError) else 126, False, 0)
    os.close(gate_read)
    if args.test_pause_before_check:
        time.sleep(args.test_pause_before_check)
    pending = signal.sigpending() & cancel_signals
    if stopping or cancel_requested() or pending:
        os.close(gate_write)  # EOF: the child exits 125 without exec
        try:
            proc.wait(timeout=GATE_CLOSE_TIMEOUT)
        except subprocess.TimeoutExpired:
            # A wrapper that ignores a closed gate is a regression no report
            # should paper over: kill it, and leave the report unwritten so
            # the driver takes its lost-containment path (scope cleared and
            # verified, batch stopped).
            log_line(
                args.log,
                f"CONTAINMENT LOST: the launch gate did not exit within "
                f"{GATE_CLOSE_TIMEOUT:g}s of a cancel; killing it, and writing no report",
            )
            gate_reaper = Reaper(proc.pid)
            contain(gate_reaper, proc.pid, args.grace)
            proc.returncode = -1 if gate_reaper.leader_status is None else gate_reaper.leader_status
            return CONTAINMENT_LOST_STATUS
        cancelled_by = stopping or (cancellation_signal(pending) if pending else signal.SIGTERM)
        log_line(
            args.log, f"lane cancelled before it started (signal {cancelled_by}); not a verdict"
        )
        return report(128 + cancelled_by, False, 0, cancelled=True)
    if args.test_pause_before_release:
        time.sleep(args.test_pause_before_release)
    os.write(gate_write, b"1")
    os.close(gate_write)
    signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
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
        if not stopping and cancel_requested():
            stopping = signal.SIGTERM
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
    if stopping:
        log_line(args.log, f"lane cancelled by signal {stopping}; not a verdict")
        return report(128 + stopping, False, strays, cancelled=True)
    if timed_out:
        return report(TIMED_OUT_STATUS, True, strays)
    assert reaper.leader_status is not None
    return report(reaper.leader_status, False, strays)


if __name__ == "__main__":
    sys.exit(main())
