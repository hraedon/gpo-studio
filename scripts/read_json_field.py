"""Print one field of the JSON document on stdin, by a dotted key path.

    echo "$RESPONSE" | python scripts/read_json_field.py gpo.guid

Used by the installed-package smoke test in place of ``python -c``, which
``tests/workflow_grammar.py`` refuses.
"""

from __future__ import annotations

import json
import sys
from typing import Any


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(f"usage: {sys.argv[0]} KEY[.KEY...] < document.json", file=sys.stderr)
        return 2
    value: Any = json.load(sys.stdin)
    for key in argv[0].split("."):
        value = value[key]
    print(value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
