"""Every plan's Status line uses the corpus's vocabulary and cites its evidence.

Plans 002-012 read "executable plan" (009 "proposed") months after they had
landed, and 013-015 said "implemented"/"completed" with nothing a reader could
check. AGENTS.md asks for the Status line to be updated when a plan lands; this
is the mechanical half of that rule.

The vocabulary is the one plans 013-035 already used when this test was written,
not an invented one. A Status section is the text from ``Status:`` to the
``Scope:`` line (or the first ``##`` heading). Its first disposition term must
be in ``VOCABULARY``, and a term that claims delivery must be backed, in the
same section, by a commit SHA in backticks that exists, a relative Markdown link
that resolves, or a backticked repository path that exists.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PLANS = sorted((REPO_ROOT / "plans").glob("[0-9][0-9][0-9]-*.md"))

#: Claims nothing is delivered.
OPEN = ("north-star program charter", "proposed", "active")
#: Claims delivery, so it must cite evidence. Longest first: "implemented in
#: part" must win over "implemented", "completed" over "complete".
DELIVERED = (
    "implemented in part",
    "implemented",
    "completed",
    "complete",
    "signed off",
    "reconciled",
    "surfaced",
    "certified",
)
VOCABULARY = OPEN + DELIVERED
_TERM = re.compile(
    r"\b("
    + "|".join(re.escape(term) for term in sorted(VOCABULARY, key=len, reverse=True))
    + r")\b"
)
_SHA = re.compile(r"`([0-9a-f]{7,40})`")
_LINK = re.compile(r"\]\(([^)#\s]+)(?:#[^)]*)?\)")
_PATH = re.compile(r"`((?:docs|plans|tests|scripts|src|\.github)/[^`*{}\s]+)`")


def _status(path: Path) -> str:
    lines = path.read_text(encoding="utf-8").splitlines()
    start = next((index for index, line in enumerate(lines) if line.startswith("Status:")), None)
    assert start is not None, f"{path.name} has no Status: line"
    assert start <= 4, f"{path.name}: the Status line must sit under the title"
    section = [lines[start].removeprefix("Status:")]
    for line in lines[start + 1 :]:
        if line.startswith(("Scope:", "## ")):
            break
        section.append(line)
    else:
        raise AssertionError(f"{path.name}: no Scope: line or heading ends the Status section")
    return "\n".join(section)


def _plain(text: str) -> str:
    return " ".join(text.replace("**", "").replace("`", "").lower().split())


def _full_clone() -> bool:
    result = subprocess.run(
        ["git", "rev-parse", "--is-shallow-repository"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0 and result.stdout.strip() == "false"


def _commit_exists(sha: str) -> bool:
    result = subprocess.run(
        ["git", "cat-file", "-e", f"{sha}^{{commit}}"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def _evidence(path: Path, status: str) -> list[str]:
    found = [f"commit {sha}" for sha in _SHA.findall(status)]
    for target in _LINK.findall(status):
        if "://" in target:
            continue
        assert (path.parent / target).exists(), f"{path.name}: link {target} does not resolve"
        found.append(f"link {target}")
    for repo_path in _PATH.findall(status):
        if (REPO_ROOT / repo_path).exists():
            found.append(f"path {repo_path}")
    return found


def test_the_plans_are_found() -> None:
    assert len(PLANS) >= 35
    assert PLANS[0].name.startswith("001-")


@pytest.mark.parametrize("path", PLANS, ids=lambda path: path.name[:3])
def test_the_status_uses_the_vocabulary(path: Path) -> None:
    status = _plain(_status(path))
    assert "executable plan" not in status, f"{path.name}: 'executable plan' is not a disposition"
    match = _TERM.search(status)
    assert match is not None, f"{path.name}: no status term from {VOCABULARY}: {status[:120]!r}"


@pytest.mark.parametrize("path", PLANS, ids=lambda path: path.name[:3])
def test_a_delivered_status_cites_evidence(path: Path) -> None:
    raw = _status(path)
    match = _TERM.search(_plain(raw))
    assert match is not None
    if match.group(1) in OPEN:
        return
    evidence = _evidence(path, raw)
    assert evidence, (
        f"{path.name} claims {match.group(1)!r} but its Status section cites no commit, "
        "link or repository path"
    )


@pytest.mark.parametrize("path", PLANS, ids=lambda path: path.name[:3])
def test_every_cited_commit_exists(path: Path) -> None:
    if not _full_clone():
        pytest.skip("shallow clone: older commits are not fetched here")
    for sha in _SHA.findall(_status(path)):
        assert _commit_exists(sha), f"{path.name} cites {sha}, which is not a commit here"
