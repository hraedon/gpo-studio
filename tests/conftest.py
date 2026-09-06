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
