"""Build the workspace fixture written by a released GPO Studio, and rehearse rollback.

The synthetic ``workspace_v0.db``/``workspace_v1.db`` fixtures are written by
``generate_legacy_fixture.py`` with hand-written SQL, so they prove that the
migrations accept the shape this repository *believes* an old release wrote.
This script removes the belief: it drives the **released** application through
its own HTTP API, so every byte of the fixture was written by that release.

It needs two installed command-line entry points:

* ``--legacy-cli``: ``gpo-studio`` from the release being upgraded *from*, for
  example a venv built from ``git worktree add <dir> v1.0.0`` with
  ``uv sync --frozen --no-editable``;
* ``--current-cli``: ``gpo-studio`` from the tree being released;
* ``--legacy-source``: the clean checkout the legacy CLI was installed from,
  which must be exactly ``--legacy-tag`` (default ``v1.0.0``).

It then records, in ``<output-dir>/provenance.json``:

0. the writer: the tag's commit, the ``uv.lock`` digest, and a digest of the
   package files the legacy CLI actually imports (refused if editable);
1. what the legacy release served for every GPO and revision (the lossless
   comparison baseline) and the bytes of each Registry.pol it exported;
2. a legacy-written verified backup of the same workspace, with its sidecar;
3. what the legacy release does when pointed at the workspace after the current
   release has upgraded it, and when asked to restore a backup of it;
4. that restoring the pre-upgrade backup with the legacy release recovers a
   workspace the legacy release can serve again;
5. WI-072: the legacy ``workspace check --full`` changing a copy of the backup,
   and the legacy restore then refusing it.

Steps 3 and 4 are the documented rollback procedure, observed rather than
asserted. Everything is synthetic: the policy names, domain, principals and
SIDs are invented and use the ``studio.local`` placeholder domain.

Usage::

    python scripts/generate_release_workspace_fixture.py \\
        --legacy-source <v1.0.0-worktree> \\
        --legacy-cli <v1.0.0-worktree>/.venv/bin/gpo-studio \\
        --current-cli .venv/bin/gpo-studio \\
        --output-dir tests/fixtures/release-1.0.0-workspace
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

ACTOR = "release-fixture"

WORKSPACE_NAME = "workspace.db"
BACKUP_NAME = "pre-upgrade-backup.db"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _Server:
    """A ``gpo-studio run`` child process bound to a free loopback port."""

    def __init__(self, cli: Path, database: Path) -> None:
        self.cli = cli
        self.database = database
        self.port = _free_port()
        self.process: subprocess.Popen[str] | None = None
        self.output = ""
        self.returncode: int | None = None

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        self.process = subprocess.Popen(
            [
                str(self.cli),
                "run",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
                "--database",
                str(self.database),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                break
            try:
                with urllib.request.urlopen(f"{self.base}/api/health", timeout=1):
                    return
            except (urllib.error.URLError, ConnectionError):
                time.sleep(0.2)
        self.stop()
        raise RuntimeError(f"server did not become healthy:\n{self.output}")

    def stop(self) -> int | None:
        if self.process is None:
            return None
        if self.process.poll() is None:
            self.process.terminate()
        try:
            out, _ = self.process.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            self.process.kill()
            out, _ = self.process.communicate()
        self.output = out or ""
        self.returncode = self.process.returncode
        self.process = None
        return self.returncode

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base}{path}",
            data=data,
            method=method,
            headers={"Content-Type": "application/json"} if data is not None else {},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read()
                if response.headers.get_content_type() == "application/json":
                    return json.loads(raw)
                return raw
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", "replace")
            raise RuntimeError(f"{method} {path} -> {error.code}: {detail}") from error


def _audit(revision: int, reason: str) -> dict[str, Any]:
    return {"actor": ACTOR, "reason": reason, "expected_revision": revision}


def _setting(
    side: str, key: str, name: str, registry_type: str, value: Any, action: str = "set"
) -> dict[str, Any]:
    return {
        "side": side,
        "hive": "HKLM" if side == "computer" else "HKCU",
        "key": key,
        "value_name": name,
        "registry_type": registry_type,
        "value": value,
        "action": action,
    }


def _author(server: _Server) -> list[str]:
    """Author two synthetic GPOs through the legacy API; return their GUIDs."""
    base_key = r"SOFTWARE\Policies\StudioFixture\Release"
    user_key = r"Software\Policies\StudioFixture\Release"

    first = server.request(
        "POST",
        "/api/gpos",
        {
            "name": "Release Fixture Registry Baseline",
            "description": "Synthetic workspace written by the released application",
            "actor": ACTOR,
            "reason": "Create the release fixture baseline",
        },
    )["gpo"]
    guid = first["guid"]
    revision = first["revision"]

    for setting, reason in (
        (_setting("computer", base_key, "Banner", "REG_SZ", "Synthetic banner"), "Add REG_SZ"),
        (_setting("computer", base_key, "Timeout", "REG_DWORD", "900"), "Add REG_DWORD"),
        (
            _setting("computer", base_key, "Quota", "REG_QWORD", "18446744073709551615"),
            "Add REG_QWORD at its maximum",
        ),
        (
            _setting("computer", base_key, "Servers", "REG_MULTI_SZ", ["alpha", "beta"]),
            "Add REG_MULTI_SZ",
        ),
        (_setting("computer", base_key, "Blob", "REG_BINARY", "DEADBEEF"), "Add REG_BINARY"),
        (
            _setting("computer", base_key, "Path", "REG_EXPAND_SZ", r"%SystemRoot%\Studio"),
            "Add REG_EXPAND_SZ",
        ),
        (_setting("user", user_key, "Greeting", "REG_SZ", "Grüße, 世界"), "Add Unicode value"),
        (
            _setting("computer", base_key, "Retired", "REG_SZ", "", action="delete"),
            "Add a delete action",
        ),
    ):
        payload = server.request(
            "POST", f"/api/gpos/{guid}/settings", {**_audit(revision, reason), "setting": setting}
        )
        revision = payload["gpo"]["revision"]

    settings = {item["value_name"]: item for item in payload["gpo"]["settings"]}
    timeout = settings["Timeout"]
    payload = server.request(
        "PUT",
        f"/api/gpos/{guid}/settings/{timeout['id']}",
        {
            **_audit(revision, "Edit the DWORD"),
            "setting": _setting("computer", base_key, "Timeout", "REG_DWORD", "1800"),
        },
    )
    revision = payload["gpo"]["revision"]
    payload = server.request(
        "DELETE",
        f"/api/gpos/{guid}/settings/{settings['Blob']['id']}",
        _audit(revision, "Remove the binary value"),
    )
    revision = payload["gpo"]["revision"]
    payload = server.request(
        "POST",
        f"/api/gpos/{guid}/links",
        {
            **_audit(revision, "Link to a synthetic OU"),
            "link": {"target": "OU=Workstations,DC=studio,DC=local", "enforced": True, "order": 1},
        },
    )
    revision = payload["gpo"]["revision"]
    payload = server.request(
        "POST",
        f"/api/gpos/{guid}/security-filters",
        {
            **_audit(revision, "Filter to a synthetic group"),
            "filter": {
                "principal": r"STUDIO\FixtureWorkstations",
                "permission": "apply",
                "target_type": "group",
                "sid": "S-1-5-21-1000000001-1000000002-1000000003-1104",
            },
        },
    )
    revision = payload["gpo"]["revision"]
    payload = server.request(
        "PUT",
        f"/api/gpos/{guid}/wmi-filter",
        {
            **_audit(revision, "Attach a synthetic WMI filter"),
            "wmi_filter": {
                "name": "Fixture Windows 11",
                "query": "SELECT * FROM Win32_OperatingSystem WHERE BuildNumber >= 22000",
            },
        },
    )
    revision = payload["gpo"]["revision"]
    payload = server.request(
        "PATCH",
        f"/api/gpos/{guid}",
        {
            **_audit(revision, "Mark ready"),
            "name": "Release Fixture Registry Baseline",
            "description": "Synthetic workspace written by the released application",
            "computer_enabled": True,
            "user_enabled": True,
            "status": "ready",
            "domain": "studio.local",
        },
    )

    second = server.request(
        "POST",
        "/api/gpos",
        {
            "name": "Release Fixture Preferences",
            "actor": ACTOR,
            "reason": "Create the preferences fixture",
        },
    )["gpo"]
    guid2 = second["guid"]
    revision2 = second["revision"]
    payload = server.request(
        "POST",
        f"/api/gpos/{guid2}/preferences/groups",
        {
            **_audit(revision2, "Add a GPP local group"),
            "scope": "computer",
            "group": {
                "name": "Administrators (built-in)",
                "sid": "S-1-5-32-544",
                "action": "update",
                "members": [
                    {
                        "sid": "S-1-5-21-1000000001-1000000002-1000000003-1105",
                        "name": r"STUDIO\FixtureAdmins",
                        "action": "add",
                    }
                ],
            },
        },
    )
    revision2 = payload["gpo"]["revision"]
    payload = server.request(
        "POST",
        f"/api/gpos/{guid2}/preferences/registry",
        {
            **_audit(revision2, "Add a GPP registry value"),
            "scope": "computer",
            "registry": {
                "key": r"SOFTWARE\StudioFixture\Preferences",
                "hive": "HKEY_LOCAL_MACHINE",
                "action": "update",
                "value": {
                    "name": "Mode",
                    "value": "2",
                    "registry_type": "REG_DWORD",
                    "action": "update",
                },
            },
        },
    )
    revision2 = payload["gpo"]["revision"]
    payload = server.request(
        "PATCH",
        f"/api/gpos/{guid2}",
        {
            **_audit(revision2, "Disable the user side"),
            "name": "Release Fixture Preferences",
            "description": "",
            "computer_enabled": True,
            "user_enabled": False,
            "status": "draft",
            "domain": "studio.local",
        },
    )
    return [guid, guid2]


def _capture(server: _Server, guids: list[str]) -> dict[str, Any]:
    """Record what the running application serves for each GPO."""
    captured: dict[str, Any] = {"list": server.request("GET", "/api/gpos"), "gpos": {}}
    for guid in guids:
        detail = server.request("GET", f"/api/gpos/{guid}")
        revisions = server.request("GET", f"/api/gpos/{guid}/revisions")
        bundle = zipfile.ZipFile(io.BytesIO(server.request("GET", f"/api/gpos/{guid}/export.zip")))
        # The bytes, not only their digest: a later serializer may reorder
        # records on purpose, and only the bytes let a test tell that from loss.
        registry_pol = {
            name: base64.b64encode(bundle.read(name)).decode("ascii")
            for name in sorted(bundle.namelist())
            if name.lower().endswith("registry.pol")
        }
        captured["gpos"][guid] = {
            "detail": detail,
            "revisions": revisions,
            "export_registry_pol_base64": registry_pol,
        }
    return captured


def _run(cli: Path, *args: str) -> dict[str, Any]:
    result = subprocess.run(
        [str(cli), *args], capture_output=True, text=True, timeout=120, check=False
    )
    return {
        "argv": ["gpo-studio", *args],
        "exit_code": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def _version(cli: Path) -> str:
    python = cli.parent / ("python.exe" if cli.suffix == ".exe" else "python")
    return subprocess.run(
        [str(python), "-c", "import gpo_studio; print(gpo_studio.__version__)"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _refusal_line(observation: dict[str, Any], tmp: Path) -> dict[str, Any]:
    """Keep the observation, minus temporary paths and the server's access log."""
    text = observation.get("stderr", "") + observation.get("stdout", "")
    lines = [line for line in text.splitlines() if "chema version" in line]
    return {
        "argv": [part.replace(str(tmp), "<tmp>") for part in observation["argv"]],
        "exit_code": observation["exit_code"],
        "schema_lines": [line.replace(str(tmp), "<tmp>") for line in lines],
    }


def installed_source_digest(files: dict[str, bytes]) -> str:
    """One SHA-256 over ``relative/path NUL sha256 LF`` lines, sorted by path.

    ``tests/test_release_upgrade_from_1_0_0.py`` computes the same digest from
    the tag's ``src/gpo_studio`` tree, so the recorded value ties the installed
    package that wrote the fixture to the tagged source.
    """
    lines = sorted(
        f"{name}\0{hashlib.sha256(data).hexdigest()}\n" for name, data in files.items()
    )
    return hashlib.sha256("".join(lines).encode("utf-8")).hexdigest()


def _git_out(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


def _writer_provenance(cli: Path, source: Path, tag: str) -> dict[str, Any]:
    """Pin the writer: its commit, its lockfile, and the bytes actually installed."""
    head = _git_out(source, "rev-parse", "HEAD")
    tagged = _git_out(source, "rev-parse", f"{tag}^{{commit}}")
    if head != tagged:
        raise RuntimeError(f"--legacy-source is at {head}, not {tag} ({tagged})")
    if _git_out(source, "status", "--porcelain", "--untracked-files=no"):
        raise RuntimeError("--legacy-source has local modifications")
    python = cli.parent / ("python.exe" if cli.suffix == ".exe" else "python")
    package_dir = Path(
        subprocess.run(
            [
                str(python),
                "-c",
                "import gpo_studio, pathlib; print(pathlib.Path(gpo_studio.__file__).parent)",
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    )
    if package_dir.resolve() == (source / "src" / "gpo_studio").resolve():
        raise RuntimeError("the legacy CLI runs an editable install; install --no-editable")
    files = {
        path.relative_to(package_dir).as_posix(): path.read_bytes()
        for path in package_dir.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }
    versions = subprocess.run(
        [
            str(python),
            "-c",
            "import sys, fastapi, uvicorn; "
            "print(sys.version.split()[0], fastapi.__version__, uvicorn.__version__)",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    return {
        "tag": tag,
        "commit": head,
        "uv_lock_sha256": _sha256(source / "uv.lock"),
        "installed_package_files": len(files),
        "installed_source_digest": installed_source_digest(files),
        "install": "uv sync --frozen --no-editable (the tag's own lockfile)",
        "python": versions[0],
        "fastapi": versions[1],
        "uvicorn": versions[2],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--legacy-cli", type=Path, required=True)
    parser.add_argument("--current-cli", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--legacy-source",
        type=Path,
        required=True,
        help="the checkout the legacy CLI was installed from (e.g. a v1.0.0 worktree)",
    )
    parser.add_argument("--legacy-tag", default="v1.0.0")
    args = parser.parse_args()

    legacy_version = _version(args.legacy_cli)
    writer = _writer_provenance(args.legacy_cli, args.legacy_source, args.legacy_tag)
    current_version = _version(args.current_cli)

    with tempfile.TemporaryDirectory(prefix="gpo-studio-release-fixture-") as raw_tmp:
        tmp = Path(raw_tmp)
        workspace = tmp / WORKSPACE_NAME

        legacy = _Server(args.legacy_cli, workspace)
        legacy.start()
        try:
            guids = _author(legacy)
            baseline = _capture(legacy, guids)
        finally:
            legacy.stop()
        wal = Path(f"{workspace}-wal")
        if wal.exists() and wal.stat().st_size:
            raise RuntimeError("legacy server left an uncheckpointed WAL")
        backup = tmp / BACKUP_NAME
        backup_run = _run(
            args.legacy_cli, "workspace", "backup", "--database", str(workspace),
            "--output", str(backup),
        )
        if backup_run["exit_code"] != 0:
            raise RuntimeError(f"legacy backup failed: {backup_run}")

        # The fixture itself is frozen here, before anything else opens it.
        fixture_dir = tmp / "fixture"
        fixture_dir.mkdir()
        shutil.copyfile(workspace, fixture_dir / WORKSPACE_NAME)
        shutil.copyfile(backup, fixture_dir / BACKUP_NAME)
        shutil.copyfile(
            Path(f"{backup}.meta.json"), fixture_dir / f"{BACKUP_NAME}.meta.json"
        )

        # Upgrade a copy with the current release: starting the server migrates.
        upgraded = tmp / "upgraded.db"
        shutil.copyfile(workspace, upgraded)
        current = _Server(args.current_cli, upgraded)
        current.start()
        try:
            after_upgrade = _capture(current, guids)
        finally:
            current.stop()
        if sorted(after_upgrade["gpos"]) != sorted(baseline["gpos"]):
            raise RuntimeError("the upgraded workspace does not serve the same GPOs")
        with sqlite3.connect(upgraded) as conn:
            upgraded_schema = conn.execute(
                "SELECT value FROM workspace_meta WHERE key='schema_version'"
            ).fetchone()[0]
        upgraded_backup = tmp / "upgraded-backup.db"
        upgraded_backup_run = _run(
            args.current_cli, "workspace", "backup", "--database", str(upgraded),
            "--output", str(upgraded_backup),
        )
        if upgraded_backup_run["exit_code"] != 0:
            raise RuntimeError(f"current backup failed: {upgraded_backup_run}")

        # Roll back: the legacy release against the upgraded workspace...
        refused = _Server(args.legacy_cli, upgraded)
        try:
            refused.start()
        except RuntimeError:
            pass
        else:
            refused.stop()
            raise RuntimeError("legacy release served an upgraded workspace")
        legacy_run_upgraded = {
            "argv": ["gpo-studio", "run", "--database", "<tmp>/upgraded.db"],
            "exit_code": refused.returncode,
            "stdout": refused.output,
        }
        legacy_restore_upgraded = _run(
            args.legacy_cli, "workspace", "restore", str(upgraded_backup),
            str(tmp / "restored-from-upgraded.db"),
        )
        # Known issue (WI-072): 1.0.0's `workspace check` writes into the file it
        # checks. Observe it on a disposable copy of the backup and sidecar.
        probe = tmp / "check-probe" / BACKUP_NAME
        probe.parent.mkdir()
        shutil.copyfile(backup, probe)
        shutil.copyfile(Path(f"{backup}.meta.json"), Path(f"{probe}.meta.json"))
        probe_before = _sha256(probe)
        legacy_check = _run(
            args.legacy_cli, "workspace", "check", "--database", str(probe), "--full"
        )
        probe_after = _sha256(probe)
        legacy_restore_checked = _run(
            args.legacy_cli, "workspace", "restore", str(probe), str(tmp / "after-check.db")
        )
        restore_text = legacy_restore_checked["stderr"] + legacy_restore_checked["stdout"]

        # ...and the documented recovery: restore the pre-upgrade backup.
        rolled_back = tmp / "rolled-back.db"
        legacy_restore_preupgrade = _run(
            args.legacy_cli, "workspace", "restore", str(backup), str(rolled_back)
        )
        if legacy_restore_preupgrade["exit_code"] != 0:
            raise RuntimeError(f"legacy restore failed: {legacy_restore_preupgrade}")
        recovered = _Server(args.legacy_cli, rolled_back)
        recovered.start()
        try:
            after_rollback = _capture(recovered, guids)
        finally:
            recovered.stop()
        if after_rollback["gpos"] != baseline["gpos"]:
            raise RuntimeError("restoring the pre-upgrade backup did not recover the baseline")

        args.output_dir.mkdir(parents=True, exist_ok=True)
        for item in fixture_dir.iterdir():
            shutil.copyfile(item, args.output_dir / item.name)

        provenance = {
            "description": (
                "Synthetic workspace written by the released application through its own "
                "HTTP API. Generated by scripts/generate_release_workspace_fixture.py."
            ),
            "written_by_version": legacy_version,
            "writer": writer,
            # The tree that performed the upgrade in the rollback rehearsal. Before
            # the release cut bumps __version__, it still reports the old number.
            "upgraded_by_tree_version": current_version,
            "upgraded_schema_version": int(upgraded_schema),
            "files": {
                item.name: _sha256(args.output_dir / item.name)
                for item in sorted(fixture_dir.iterdir())
            },
            "baseline": baseline,
            "rollback_observations": {
                "legacy_run_on_upgraded_workspace": _refusal_line(legacy_run_upgraded, tmp),
                "legacy_restore_of_upgraded_backup": _refusal_line(
                    legacy_restore_upgraded, tmp
                ),
                "legacy_restore_of_pre_upgrade_backup": {
                    "exit_code": legacy_restore_preupgrade["exit_code"],
                    "served_baseline_again": True,
                },
                "legacy_check_full_on_a_backup_copy": {
                    "check_exit_code": legacy_check["exit_code"],
                    "backup_sha256_changed": probe_before != probe_after,
                    "restore_exit_code": legacy_restore_checked["exit_code"],
                    "restore_lines": [
                        line.replace(str(tmp), "<tmp>")
                        for line in restore_text.splitlines()
                        if "checksum" in line.lower()
                    ],
                },
            },
        }
        (args.output_dir / "provenance.json").write_text(
            json.dumps(provenance, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print(f"wrote {args.output_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
