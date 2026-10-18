"""The 58b80aa legacy-reading loader, vendored as a test oracle (final-r20).

``tests/test_legacy_differential.py`` holds the current reading of every
legacy stored shape to the loader that read them before the typed load took
them over. That loader is the GPP module of commit 58b80aa together with the
``stored_types`` module it imported. Both are vendored here VERBATIM, as data
(``legacy_58b80aa/*.py.txt``, so they are neither linted, type-checked,
collected nor measured), and executed as modules of the ``gpo_studio``
package beside the current ones -- so the differential test needs no git
history, and survives a squash merge that drops the commit.

Provenance (each vendored file is the whole blob, byte for byte; the SHA-256
is of the blob's bytes, checked on every load, and against git itself by
``test_the_vendored_oracle_is_the_commit``, which skips when the commit is
not in the checkout):

=================================  ===================================  ==================
vendored file                      source (commit 58b80aa2ced37b90...)  SHA-256
=================================  ===================================  ==================
legacy_58b80aa/gpp.py.txt          src/gpo_studio/gpp.py                435d066069d414b0...
legacy_58b80aa/stored_types.py.txt src/gpo_studio/stored_types.py       5cf322be2e66ef80...
=================================  ===================================  ==================

The full digests are `SOURCES` below. The oracle's own imports of the GPP
module's siblings (``.gpp_adapters``, ``.ilt``, ``.gpp_native`` ...) resolve
to the current modules, as they did at 58b80aa for the files vendored here.
"""

from __future__ import annotations

import hashlib
import sys
import types
from functools import cache
from pathlib import Path
from typing import Any

COMMIT = "58b80aa2ced37b9002b0124f851a11791a0818c2"
HERE = Path(__file__).resolve().parent / "legacy_58b80aa"

#: vendored file -> (source path at COMMIT, SHA-256 of the blob).
SOURCES: dict[str, tuple[str, str]] = {
    "stored_types.py.txt": (
        "src/gpo_studio/stored_types.py",
        "5cf322be2e66ef80e2804fcb7ac18de91d316278e464f5c090e1b12a4f4f9426",
    ),
    "gpp.py.txt": (
        "src/gpo_studio/gpp.py",
        "435d066069d414b0a59f2755a9c8bb34053a7a69be9d27e7e078ad1155327f66",
    ),
}

#: The module names the vendored files run as, inside ``gpo_studio``.
_MODULES = {
    "stored_types.py.txt": "gpo_studio._oracle_58b80aa_stored_types",
    "gpp.py.txt": "gpo_studio._oracle_58b80aa_gpp",
}


def vendored_bytes(name: str) -> bytes:
    data = (HERE / name).read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    expected = SOURCES[name][1]
    if digest != expected:
        raise RuntimeError(f"vendored oracle {name} is {digest}, not {expected}")
    return data


@cache
def gpp() -> Any:
    """The 58b80aa ``gpp`` module, importing the 58b80aa ``stored_types``."""
    module: Any = None
    for name, module_name in _MODULES.items():
        source = vendored_bytes(name).decode("utf-8")
        if name == "gpp.py.txt":
            source = source.replace(
                "from .stored_types import", "from ._oracle_58b80aa_stored_types import"
            )
        module = types.ModuleType(module_name)
        module.__package__ = "gpo_studio"
        sys.modules[module_name] = module
        exec(compile(source, f"{COMMIT[:7]}:{SOURCES[name][0]}", "exec"), module.__dict__)  # noqa: S102
    return module
