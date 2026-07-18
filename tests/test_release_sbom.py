from __future__ import annotations

import json
import runpy
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

_SCRIPT = runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "scripts" / "finalize_sbom.py")
)
deterministic_serial = cast(Callable[[str, str], str], _SCRIPT["deterministic_serial"])
finalize_sbom = cast(
    Callable[[Path, str, str], str],
    _SCRIPT["finalize_sbom"],
)


def test_finalize_sbom_adds_stable_cyclonedx_serial(tmp_path: Path) -> None:
    sbom = tmp_path / "sbom.cdx.json"
    sbom.write_text(
        json.dumps(
            {
                "bomFormat": "CycloneDX",
                "specVersion": "1.6",
                "version": 1,
                "components": [],
            }
        ),
        encoding="utf-8",
    )

    serial = finalize_sbom(sbom, "example/gpo-studio", "a" * 40)
    first_bytes = sbom.read_bytes()
    assert serial == deterministic_serial("example/gpo-studio", "a" * 40)
    assert serial.startswith("urn:uuid:")
    uuid.UUID(serial.removeprefix("urn:uuid:"), version=5)
    assert json.loads(first_bytes)["serialNumber"] == serial

    assert finalize_sbom(sbom, "example/gpo-studio", "a" * 40) == serial
    assert sbom.read_bytes() == first_bytes


def test_finalize_sbom_rejects_unsupported_document(tmp_path: Path) -> None:
    sbom = tmp_path / "sbom.json"
    sbom.write_text('{"spdxVersion": "SPDX-2.3"}', encoding="utf-8")

    with pytest.raises(ValueError, match="CycloneDX"):
        finalize_sbom(sbom, "example/gpo-studio", "b" * 40)
