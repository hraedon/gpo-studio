"""Print the version of the gpo-studio installed in the running interpreter.

    <venv>/bin/python scripts/print_installed_version.py             # __version__
    <venv>/bin/python scripts/print_installed_version.py --metadata  # dist metadata

The installed-package checks compare the two (and the API's) to catch a wheel
whose code and metadata disagree. A checked-in script rather than ``python -c``
because ``tests/workflow_grammar.py`` refuses inline interpreter code: a body it
cannot read is a body that could install anything.
"""

from __future__ import annotations

import sys


def main(argv: list[str]) -> int:
    if argv == ["--metadata"]:
        import importlib.metadata as m

        print(m.version("gpo-studio"))
        return 0
    if argv:
        print(f"usage: {sys.argv[0]} [--metadata]", file=sys.stderr)
        return 2
    from gpo_studio import __version__

    print(__version__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
