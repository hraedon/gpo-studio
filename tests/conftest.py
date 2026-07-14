from __future__ import annotations

import os
from collections.abc import Iterator

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
