"""Drop environment markers from a hash-pinned requirements file.

`pip-audit --disable-pip` evaluates each requirement's marker against the
machine it runs on, so on the Linux CI runner it audits the Linux subset of
`requirements-lock.txt`: `colorama ; sys_platform == 'win32'` -- which every
Windows install gets -- was never audited. With the markers removed, every
package the lock pins for any supported platform is audited, hashes intact.

    python scripts/requirements_all_platforms.py IN OUT
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# `name==version ; marker \` -> `name==version \` (the marker never contains a backslash).
_MARKER = re.compile(r"^(?P<requirement>[A-Za-z0-9][A-Za-z0-9._-]*==[^\s;]+)\s*;[^\\\n]*")


def strip_markers(text: str) -> str:
    lines = []
    for line in text.splitlines(keepends=True):
        match = _MARKER.match(line)
        lines.append(match["requirement"] + " " + line[match.end() :] if match else line)
    return "".join(lines)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    source, target = (Path(arg) for arg in argv)
    out = strip_markers(source.read_text(encoding="utf-8"))
    if ";" in "".join(line for line in out.splitlines() if not line.lstrip().startswith("#")):
        print("ERROR: a marker survived", file=sys.stderr)
        return 1
    target.write_text(out, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
