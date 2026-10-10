"""Check that a saved ``/api/health`` response reports the installed version.

    <venv>/bin/python scripts/check_health_version.py /tmp/health.json

Run with the interpreter of the environment the server was installed into.
"""

from __future__ import annotations

import json
import sys


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(f"usage: {sys.argv[0]} HEALTH_JSON", file=sys.stderr)
        return 2
    from gpo_studio import __version__

    with open(argv[0], encoding="utf-8") as stream:
        reported = json.load(stream)["version"]
    if reported != __version__:
        print(f"FAIL: API version ({reported}) != installed version ({__version__})")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
