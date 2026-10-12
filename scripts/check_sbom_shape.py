"""Check that a file is a CycloneDX SBOM carrying a URN serial number.

python scripts/check_sbom_shape.py sbom.cdx.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(f"usage: {sys.argv[0]} SBOM_JSON", file=sys.stderr)
        return 2
    data = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    if data["bomFormat"] != "CycloneDX":
        print(f"FAIL: bomFormat is {data['bomFormat']!r}, not 'CycloneDX'")
        return 1
    if not data["serialNumber"].startswith("urn:uuid:"):
        print(f"FAIL: serialNumber {data['serialNumber']!r} is not a urn:uuid")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
