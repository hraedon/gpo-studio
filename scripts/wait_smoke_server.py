"""Read the ephemeral port announced by the smoke test's own CLI server.

The pinned uvicorn default log format is exercised by the real CLI startup
regression; a format change must fail closed.
The private log is written only by that child. Never discover a server by
probing a shared port: it could be an unrelated application with writable APIs.
"""

from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

_READY = re.compile(
    r"INFO:\s+Uvicorn running on http://127\.0\.0\.1:([0-9]+) "
    r"\(Press CTRL\+C to quit\)",
)


def announced_port(text: str, pid: int) -> int | None:
    lines = text.splitlines()
    if not any(re.fullmatch(rf"INFO:\s+Started server process \[{pid}\]", line) for line in lines):
        return None
    ports = [match[1] for line in lines if (match := _READY.fullmatch(line)) is not None]
    if not ports:
        return None
    if len(ports) != 1 or not 1 <= int(ports[0]) <= 65535:
        raise RuntimeError("invalid server port announcement")
    return int(ports[0])


def wait_for_port(pid: int, log: Path, *, timeout: float = 30) -> int:
    if pid <= 0:
        raise ValueError("server PID must be positive")
    deadline = time.monotonic() + timeout
    while True:
        text = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
        try:
            os.kill(pid, 0)
        except ProcessLookupError as error:
            raise RuntimeError(
                "smoke server exited before becoming ready\n" + text[-2000:]
            ) from error
        if re.search(r"^ERROR:", text, re.MULTILINE):
            raise RuntimeError("smoke server failed during startup\n" + text[-2000:])
        port = announced_port(text, pid)
        if port is not None:
            return port
        if time.monotonic() >= deadline:
            raise RuntimeError("timed out waiting for the smoke server\n" + text[-2000:])
        time.sleep(0.05)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {sys.argv[0]} PID LOG", file=sys.stderr)
        return 2
    try:
        print(wait_for_port(int(argv[0]), Path(argv[1])))
    except (OSError, ValueError, RuntimeError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
