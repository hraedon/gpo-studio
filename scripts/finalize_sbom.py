"""Add the deterministic release identity required for SBOM attestation."""

from __future__ import annotations

import argparse
import json
import uuid
from pathlib import Path
from typing import Any


def deterministic_serial(repository: str, source_commit: str) -> str:
    """Return a stable CycloneDX serial number for one repository commit."""
    identity = f"https://github.com/{repository}@{source_commit}"
    return f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, identity)}"


def finalize_sbom(path: Path, repository: str, source_commit: str) -> str:
    """Validate a CycloneDX JSON document and add its deterministic serial."""
    data: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("bomFormat") != "CycloneDX":
        raise ValueError("SBOM must be a CycloneDX JSON object")
    if not isinstance(data.get("specVersion"), str):
        raise ValueError("CycloneDX SBOM must declare specVersion")

    serial = deterministic_serial(repository, source_commit)
    data["serialNumber"] = serial
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return serial


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Add a deterministic serial number to a CycloneDX SBOM.",
    )
    parser.add_argument("path", type=Path)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--source-commit", required=True)
    args = parser.parse_args()
    serial = finalize_sbom(args.path, args.repository, args.source_commit)
    print(f"Finalized CycloneDX SBOM {args.path} with {serial}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
