"""The 58b80aa legacy-reading loader, vendored as a test oracle (final-r20, r21).

``tests/test_legacy_differential.py`` holds the current reading of every
legacy stored shape to the loader that read them before the typed load took
them over: the GPP module of commit 58b80aa. It is vendored here VERBATIM,
as data (``legacy_58b80aa/*.py.txt``: neither linted, type-checked, collected
nor measured), together with EVERY module it imports, directly or through
another -- its whole import closure at that commit, fifteen modules -- so the
oracle runs none of the current code (final-r21: its siblings used to resolve
to the current modules, and a change to one would have moved both sides of
the differential together).

The vendored modules run as the package ``_oracle_58b80aa``, through an
import hook that serves only the files listed in `SOURCES`, each checked
against its SHA-256 as it loads: a relative import of anything not vendored
fails, and `load` asserts that no oracle module holds anything of the current
``gpo_studio`` package. Nothing here needs git; where commit 58b80aa is in the
checkout, ``test_the_vendored_oracle_is_the_commit`` checks every file
against its blob.

Provenance: each ``legacy_58b80aa/<module>.py.txt`` is the whole blob of
``src/gpo_studio/<module>.py`` at commit
58b80aa2ced37b9002b0124f851a11791a0818c2, byte for byte; `SOURCES` holds the
SHA-256 of each blob.
"""

from __future__ import annotations

import hashlib
import importlib.abc
import importlib.machinery
import importlib.util
import sys
import types
from collections.abc import Sequence
from functools import cache
from pathlib import Path
from typing import Any

COMMIT = "58b80aa2ced37b9002b0124f851a11791a0818c2"
HERE = Path(__file__).resolve().parent / "legacy_58b80aa"
PACKAGE = "_oracle_58b80aa"

#: module -> SHA-256 of the blob ``src/gpo_studio/<module>.py`` at `COMMIT`.
SOURCES: dict[str, str] = {
    "backup_inventory": "3939c0eb724728203c0172d9c14f06836c6be16b8f2786ef35cca8af2b1fb2af",
    "closed_sets": "f506f690d2d2388ec24634ffa80b504530c6fb28a778c3f02b629cfd0ead0bb6",
    "emit_integrity": "ea8f6e4c25e0414a54c53236550de6657168d1295ce06f9b49993053c091f6d5",
    "fdeploy": "b43b8254f8d3be390a6586cbf08265e48280e90f0ed567d785956fe3a2461bfa",
    "gpp_adapters": "ed9436fd498c581f8689f8263706bbcf26e69c20824f573be94642a765a8367c",
    "gpp_native": "1499edc9cc0363a14cc5c6eae6d62dbdc9ae8b6535342e74b908e2dd83c19cfc",
    "gpp_provenance": "21c363a550af8c6a82c1d70a775b956366db2c2ed4e601ccd410726d21a1c57b",
    "gpp": "435d066069d414b0a59f2755a9c8bb34053a7a69be9d27e7e078ad1155327f66",
    "identity_values": "437033764c09945ca851e4bf8d115c667789080335f61fcdda15355bf8e04cda",
    "ilt": "8b3ac22204cfad3237c64bfc384c7fb419cd20019e04c53715ef2c39225f51b9",
    "model": "67f7d58b90fd1e28eb457bee5c56196f0eb899b26ea285ad41a65f7457a1c344",
    "numeric": "d379e83ac2e518e9263bec2d8a7e15ec3d3f8f415c3e81b59354758dfc38b6cc",
    "registry_pol": "bb1dd52ad0090566179b3af348502a73c71c9a7cf85a7c54b65ccf82032e959c",
    "stored_types": "5cf322be2e66ef80e2804fcb7ac18de91d316278e464f5c090e1b12a4f4f9426",
    "xml_safety": "c0d196739b4accd494049b9d9959a78078e4934258734c00c0cd3e38df030a11",
}


def source_path(module: str) -> str:
    return f"src/gpo_studio/{module}.py"


def vendored_bytes(module: str) -> bytes:
    data = (HERE / f"{module}.py.txt").read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != SOURCES[module]:
        raise RuntimeError(f"vendored oracle {module} is {digest}, not {SOURCES[module]}")
    return data


class _Vendored(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Serves ``_oracle_58b80aa.<module>`` from the vendored files only."""

    def find_spec(
        self, fullname: str, path: Sequence[str] | None, target: types.ModuleType | None = None
    ) -> importlib.machinery.ModuleSpec | None:
        if fullname == PACKAGE:
            return importlib.util.spec_from_loader(fullname, self, is_package=True)
        prefix = f"{PACKAGE}."
        if not fullname.startswith(prefix):
            return None
        module = fullname[len(prefix) :]
        if module not in SOURCES:
            raise ImportError(f"the oracle imports {module}, which is not vendored")
        return importlib.util.spec_from_loader(fullname, self)

    def create_module(self, spec: importlib.machinery.ModuleSpec) -> None:
        return None

    def exec_module(self, module: types.ModuleType) -> None:
        if module.__name__ == PACKAGE:
            module.__path__ = []
            return
        name = module.__name__[len(PACKAGE) + 1 :]
        source = vendored_bytes(name).decode("utf-8")
        code = compile(source, f"{COMMIT[:7]}:{source_path(name)}", "exec")
        exec(code, module.__dict__)  # noqa: S102 - the vendored oracle


def _foreign(module: types.ModuleType) -> list[str]:
    """What *module* holds of the current ``gpo_studio`` package."""
    found = []
    for name, value in vars(module).items():
        owner = value.__name__ if isinstance(value, types.ModuleType) else getattr(
            value, "__module__", None
        )
        if isinstance(owner, str) and (owner == "gpo_studio" or owner.startswith("gpo_studio.")):
            found.append(f"{module.__name__}.{name} -> {owner}")
    return found


@cache
def gpp() -> Any:
    """The 58b80aa ``gpp`` module, with the 58b80aa versions of everything it
    imports -- and nothing of the current package."""
    if not any(isinstance(finder, _Vendored) for finder in sys.meta_path):
        sys.meta_path.insert(0, _Vendored())
    module = importlib.import_module(f"{PACKAGE}.gpp")
    # Every module the gpp module could reach, loaded and checked.
    for name in SOURCES:
        importlib.import_module(f"{PACKAGE}.{name}")
    loaded = [m for n, m in sys.modules.items() if n.startswith(f"{PACKAGE}.")]
    assert {m.__name__[len(PACKAGE) + 1 :] for m in loaded} == set(SOURCES)
    foreign = [entry for m in loaded for entry in _foreign(m)]
    assert not foreign, f"the oracle holds current code: {foreign}"
    return module
