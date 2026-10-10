"""Every plan's Status line uses the corpus's vocabulary and cites its evidence.

Plans 002-012 read "executable plan" (009 "proposed") months after they had
landed, and 013-015 said "implemented"/"completed" with nothing a reader could
check. AGENTS.md asks for the Status line to be updated when a plan lands; this
is the mechanical half of that rule.

The vocabulary is the one plans 013-035 already used when this test was written,
not an invented one. A Status section is the text from ``Status:`` to the
``Scope:`` line (or the first ``##`` heading). It must open with a term from
``VOCABULARY`` (optionally after the module it is about). If any delivery word
appears anywhere in it -- after an open opening too -- it must cite evidence: a
commit SHA in backticks that exists, a relative Markdown link whose file and
anchor exist, or a backticked repository path that exists. External links are
never evidence, and a link that does not resolve fails the section outright. The
loopholes two reviewers found are pinned as negative cases.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PLANS = sorted((REPO_ROOT / "plans").glob("[0-9][0-9][0-9]-*.md"))
#: Where the synthetic statuses below pretend to live (it does not exist).
PROBE = REPO_ROOT / "plans" / "000-probe.md"

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
#: Words that claim something was delivered without being a disposition. Any of
#: these, or any DELIVERED term, anywhere in the section requires evidence --
#: an OPEN opening ("active -- fully implemented") does not excuse it.
CLAIMS = (*DELIVERED, "delivered", "shipped", "landed", "done", "released", "verified")
_CLAIM = re.compile(r"\b(" + "|".join(re.escape(term) for term in CLAIMS) + r")\b")
#: The disposition opens the line, optionally after the module it is about
#: ("`rsop.py` is certified ...", "`publication.py` surfaced ...").
_OPENING = re.compile(
    r"^(?:[a-z0-9_]+\.py (?:is )?)?("
    + "|".join(re.escape(term) for term in sorted(VOCABULARY, key=len, reverse=True))
    + r")\b"
)
_SHA = re.compile(r"`([0-9a-f]{7,40})`")
_LINK = re.compile(r"\]\(([^)\s]+)\)")
_PATH = re.compile(r"`((?:docs|plans|tests|scripts|src|\.github)/[^`*{}\s]+)`")
_HEADING = re.compile(r"^#{1,6} (.+?)\s*#*$", re.M)


def _status_of_text(text: str, name: str) -> str:
    lines = text.splitlines()
    start = next((index for index, line in enumerate(lines) if line.startswith("Status:")), None)
    assert start is not None, f"{name} has no Status: line"
    assert start <= 4, f"{name}: the Status line must sit under the title"
    section = [lines[start].removeprefix("Status:")]
    for line in lines[start + 1 :]:
        if line.startswith(("Scope:", "## ")):
            break
        section.append(line)
    else:
        raise AssertionError(f"{name}: no Scope: line or heading ends the Status section")
    return "\n".join(section)


def _status(path: Path) -> str:
    return _status_of_text(path.read_text(encoding="utf-8"), path.name)


def _plain(text: str) -> str:
    return " ".join(text.replace("**", "").replace("`", "").lower().split())


def _slug(heading: str) -> str:
    """GitHub's anchor for a heading: lowercase, punctuation dropped, spaces to hyphens."""
    text = re.sub(r"`|\*\*|\[([^\]]*)\]\([^)]*\)", r"\1", heading).strip().lower()
    return re.sub(r"[^\w\- ]", "", text).replace(" ", "-")


def _anchors(path: Path) -> set[str]:
    return {_slug(heading) for heading in _HEADING.findall(path.read_text(encoding="utf-8"))}


def status_problems(status: str, plan: Path) -> list[str]:
    """Why the Status section of *plan* fails (links resolve from the plan's directory)."""
    problems = []
    plain = _plain(status)
    if "executable plan" in plain:
        problems.append("'executable plan' is not a disposition")
    opening = _OPENING.match(plain)
    if opening is None:
        problems.append(f"does not open with a status term from {VOCABULARY}: {plain[:80]!r}")
    evidence = [f"commit {sha}" for sha in _SHA.findall(status)]
    for target in _LINK.findall(status):
        if "://" in target or target.startswith("mailto:"):
            continue  # an external page is not evidence a reader can check here
        file_part, _, fragment = target.partition("#")
        document = (plan.parent / file_part).resolve() if file_part else plan
        if not document.exists():
            problems.append(f"link {target} does not resolve")
            continue
        if fragment and (document.suffix != ".md" or fragment not in _anchors(document)):
            problems.append(f"link {target}: no heading has the anchor #{fragment}")
            continue
        evidence.append(f"link {target}")
    evidence.extend(
        f"path {repo_path}"
        for repo_path in _PATH.findall(status)
        if (REPO_ROOT / repo_path).exists()
    )
    claims = sorted(set(_CLAIM.findall(plain)))
    if claims and not evidence:
        problems.append(f"claims {claims} but cites no commit, resolving link or repository path")
    return problems


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


def test_the_plans_are_found() -> None:
    assert len(PLANS) >= 35
    assert PLANS[0].name.startswith("001-")


@pytest.mark.parametrize("path", PLANS, ids=lambda path: path.name[:3])
def test_the_status_line_is_well_formed(path: Path) -> None:
    assert status_problems(_status(path), path) == []


#: Each must fail. The first five are Sol N1's executed probes, the next two
#: DeepSeek N2's; the rest are their neighbours.
LOOPHOLES = {
    "no_opening_term": "uncertain; awaiting discussion. This paragraph mentions an active project.",
    "negated_claim": "not implemented; placeholder `src/gpo_studio/__init__.py`.",
    "missing_anchor": (
        "implemented — [proof](../docs/capability-matrix.md#this-anchor-does-not-exist)"
    ),
    "open_term_then_claims": "active — fully implemented and certified; no evidence exists.",
    "external_link_only": (
        "active; delivered and certified [evidence](https://example.invalid/missing)"
    ),
    "active_then_shipped": "active — implemented in 1.1.0 and shipped, no evidence cited",
    "proposed_then_complete": "proposed. Implemented and complete.",
    "missing_file": "implemented — [proof](../docs/no-such-file.md)",
    "missing_same_file_anchor": "implemented — see [below](#no-such-heading)",
    "nonexistent_path_only": "implemented in `src/gpo_studio/no_such_module.py`",
    "executable_plan": "executable plan",
    "claim_word_only": "proposed — the parser landed last week",
}


@pytest.mark.parametrize("name", sorted(LOOPHOLES))
def test_a_loophole_status_is_refused(name: str) -> None:
    assert status_problems(LOOPHOLES[name], PROBE), f"{name} was accepted"


def test_well_formed_examples_pass() -> None:
    """The control for the loopholes: the same checker accepts real evidence."""
    for status in (
        "implemented — `e3aa321` (2026-07-12).",
        "implemented — see [what 1.1.0 is](../docs/release-evidence-1.1.0.md#what-110-is)",
        "proposed (2026-10-09). Nothing here blocks 1.1.0.",
        "`rsop.py` is **certified** in [the matrix](../docs/capability-matrix.md)",
    ):
        assert status_problems(status, PROBE) == [], status


@pytest.mark.parametrize("path", PLANS, ids=lambda path: path.name[:3])
def test_every_cited_commit_exists(path: Path) -> None:
    if not _full_clone():
        pytest.skip("shallow clone: older commits are not fetched here")
    for sha in _SHA.findall(_status(path)):
        assert _commit_exists(sha), f"{path.name} cites {sha}, which is not a commit here"
