from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _unsafe_bind_for_tests() -> Iterator[None]:
    old = os.environ.get("GPO_STUDIO_UNSAFE_BIND")
    os.environ["GPO_STUDIO_UNSAFE_BIND"] = "1"
    yield
    if old is not None:
        os.environ["GPO_STUDIO_UNSAFE_BIND"] = old
    else:
        os.environ.pop("GPO_STUDIO_UNSAFE_BIND", None)


def _filesystem_is_case_insensitive() -> bool:
    """True when the filesystem under the test tree folds case.

    Windows and macOS resolve `Policy.adml` for a request spelled
    `policy.adml`, so the library's own case-insensitive fallback -- the thing
    the tests using this guard exist to exercise -- never gets a chance to run,
    and a test that deliberately creates two names differing only in case
    cannot create two entries at all. Detect the filesystem rather than the
    platform, because a case-sensitive volume mounted on Windows should still
    run these.
    """
    with tempfile.TemporaryDirectory() as raw:
        probe = Path(raw) / "wcd-case-probe"
        probe.write_bytes(b"")
        return (Path(raw) / "WCD-CASE-PROBE").exists()


@pytest.fixture
def case_sensitive_fs() -> None:
    """Skip a test that cannot mean anything on a case-folding filesystem."""
    if _filesystem_is_case_insensitive():
        pytest.skip(
            "needs a case-sensitive filesystem: this checkout is on a "
            "case-folding volume, where the OS resolves the name before the "
            "code under test can"
        )


_SYMLINK_PRIVILEGE: bool | None = None


def _symlink_privilege_available() -> bool:
    """True when this process can create a symlink at all.

    Windows gates ``os.symlink`` behind SeCreateSymbolicLinkPrivilege (or
    Developer Mode); an account without it raises
    ``OSError: [WinError 1314] A required privilege is not held by the
    client`` on every attempt, and the symlink-rejection tests cannot
    plant the link they exist to refuse. Detect the capability rather
    than the platform, because a Windows runner that holds the privilege
    should still run them.
    """
    global _SYMLINK_PRIVILEGE
    if _SYMLINK_PRIVILEGE is not None:
        return _SYMLINK_PRIVILEGE
    with tempfile.TemporaryDirectory() as raw:
        target = Path(raw) / "wcd-symlink-probe-target"
        target.write_bytes(b"")
        link = Path(raw) / "wcd-symlink-probe-link"
        try:
            link.symlink_to(target)
            _SYMLINK_PRIVILEGE = True
        except OSError as exc:
            if getattr(exc, "winerror", None) != 1314:
                raise
            _SYMLINK_PRIVILEGE = False
    return _SYMLINK_PRIVILEGE


@pytest.fixture
def symlink_privilege() -> None:
    """Skip a test that must plant a symlink to do its job.

    Probes once per session; the probe result is cached so 30-odd tests
    do not each pay for a temp directory.
    """
    if not _symlink_privilege_available():
        pytest.skip(
            "needs SeCreateSymbolicLinkPrivilege: os.symlink raises "
            "[WinError 1314] A required privilege is not held by the "
            "client on this account, so the symlink these tests plant "
            "cannot be created"
        )
