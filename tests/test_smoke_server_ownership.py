"""Installed smoke requests must reach the CLI server this run started."""

from __future__ import annotations

import os
import runpy
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_HELPER = runpy.run_path(str(ROOT / "scripts/wait_smoke_server.py"))
announced_port = _HELPER["announced_port"]
wait_for_port = _HELPER["wait_for_port"]


def announcement(pid: int, port: int) -> str:
    return (
        f"INFO:     Started server process [{pid}]\n"
        f"INFO:     Uvicorn running on http://127.0.0.1:{port} (Press CTRL+C to quit)\n"
    )


def test_readiness_requires_own_pid_and_loopback_endpoint() -> None:
    assert announced_port(announcement(123, 45123), 123) == 45123
    assert announced_port(announcement(124, 45123), 123) is None
    assert announced_port(announcement(123, 45123).replace("127.0.0.1", "0.0.0.0"), 123) is None
    assert announced_port("INFO:     Application startup complete.\n", 123) is None


@pytest.mark.parametrize("port", [0, 65536])
def test_invalid_announced_port_is_refused(port: int) -> None:
    with pytest.raises(RuntimeError, match="invalid server port"):
        announced_port(announcement(123, port), 123)


def test_multiple_announcements_are_refused() -> None:
    with pytest.raises(RuntimeError, match="invalid server port"):
        announced_port(announcement(123, 45123) * 2, 123)


def test_dead_server_is_refused_even_with_a_ready_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    log = tmp_path / "server.log"
    log.write_text(announcement(123, 45123))

    def gone(pid: int, sig: int) -> None:
        raise ProcessLookupError

    monkeypatch.setattr(os, "kill", gone)
    with pytest.raises(RuntimeError, match="exited before becoming ready"):
        wait_for_port(123, log)


@pytest.mark.parametrize("text", ["", "INFO:     Application startup complete.\n"])
def test_incomplete_startup_times_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str):
    log = tmp_path / "server.log"
    log.write_text(text)
    monkeypatch.setattr(os, "kill", lambda pid, sig: None)
    with pytest.raises(RuntimeError, match="timed out"):
        wait_for_port(123, log, timeout=0)


def test_startup_error_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    log = tmp_path / "server.log"
    log.write_text("ERROR:    startup failed\n")
    monkeypatch.setattr(os, "kill", lambda pid, sig: None)
    with pytest.raises(RuntimeError, match="startup failed"):
        wait_for_port(123, log)


@pytest.mark.skipif(os.name != "posix" or not shutil.which("curl"), reason="POSIX smoke with curl")
def test_smoke_startup_does_not_contact_a_foreign_app(tmp_path: Path) -> None:
    requests: list[tuple[str, str]] = []

    class ForeignApp(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            pass

        def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
            requests.append((self.command, self.path))

        def do_POST(self) -> None:
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"gpo":{"guid":"foreign"}}')

    foreign = ThreadingHTTPServer(("127.0.0.1", 0), ForeignApp)
    worker = threading.Thread(target=foreign.serve_forever, daemon=True)
    worker.start()
    venv_bin = tmp_path / "venv/bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").symlink_to(sys.executable)
    (venv_bin / "gpo-studio").symlink_to(Path(sys.executable).parent / "gpo-studio")
    source = (ROOT / "scripts/installed_package_smoke.sh").read_text()
    startup = source.split('echo "=== Starting server ==="\n', 1)[1].split(
        'echo "=== Checking installed API version ==="', 1
    )[0]
    # The exact startup section is enough to exercise endpoint ownership without
    # rebuilding/installing packages. A foreign app occupies the old target;
    # only our newly started server may receive the following synthetic write.
    script = (
        'set -euo pipefail\nSERVER_PID=""\n'
        'trap \'kill "$SERVER_PID" 2>/dev/null || true; '
        'wait "$SERVER_PID" 2>/dev/null || true\' EXIT\n'
        + startup
        + 'curl -sf -X POST "http://127.0.0.1:$PORT/api/gpos" '
        '-H "Content-Type: application/json" '
        '-d \'{"name":"Synthetic ownership check","actor":"test","reason":"Own server only"}\'\n'
    )
    try:
        result = subprocess.run(
            ["bash", "-c", script],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=45,
            env={
                **os.environ,
                "TMPDIR": str(tmp_path),
                "DB": str(tmp_path / "smoke.db"),
                "PORT": str(foreign.server_port),
            },
        )
        assert requests == [], "smoke test sent a write to the foreign application"
        assert result.returncode == 0, result.stderr
        assert (tmp_path / "smoke.db").exists()
        assert '"Synthetic ownership check"' in result.stdout
    finally:
        foreign.shutdown()
        foreign.server_close()
        worker.join(timeout=5)
