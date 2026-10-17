"""The capability matrix's header states the contract version, not the package's.

It read "Version: 1.0.0" while the package was 1.1.0rc1. That was the contract's
version under a label that reads as the package's: 1.1.0 keeps the 1.0 contract
unchanged (``release-evidence-1.1.0.md``) and lists what it adds as post-1.0
layers. The matrix is hand-written -- ``scripts/generate_public_matrix.py`` emits
a different, evidence-pack-derived matrix -- so the header is pinned here.
"""

from __future__ import annotations

import re
from pathlib import Path

from gpo_studio import __version__

DOCS = Path(__file__).resolve().parents[1] / "docs"
MATRIX = DOCS / "capability-matrix.md"


def _header() -> str:
    text = MATRIX.read_text(encoding="utf-8")
    return text.split("\n\n", 2)[1]


def _slug(heading: str) -> str:
    """GitHub's heading anchor: lowercase, punctuation dropped, spaces to hyphens."""
    return re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")


def _anchors(path: Path) -> set[str]:
    headings = re.findall(r"^#{1,6} (.+)$", path.read_text(encoding="utf-8"), re.M)
    return {_slug(heading) for heading in headings}


def test_the_header_names_the_contract_version_and_no_package_version() -> None:
    header = _header()
    match = re.search(r"^> \*\*Contract version:\*\* (\d+)\.(\d+),", header, re.M)
    assert match is not None, header
    contract = (int(match[1]), int(match[2]))
    assert f"GPO Studio {contract[0]}.{contract[1]} capability contract" in header
    # Link destinations are paths, not statements: the release evidence document's
    # file name carries X.Y.Z, which is the package version on a final release.
    prose = re.sub(r"\]\([^)]*\)", "]", header)
    assert __version__ not in prose, (
        "the header states the package version, which lives in src/gpo_studio/__init__.py"
    )
    package = tuple(int(part) for part in re.findall(r"\d+", __version__)[:2])
    assert contract <= package, (contract, __version__)
    assert "**Version:**" not in header


def test_the_header_links_resolve() -> None:
    header = _header()
    assert "release-evidence-1.1.0.md#what-110-is" in header
    assert "what-110-is" in _anchors(DOCS / "release-evidence-1.1.0.md")
    assert "#reconciled-post-10-layers--certified-and-surfaced" in header
    assert "reconciled-post-10-layers--certified-and-surfaced" in _anchors(MATRIX)
