"""A superseded module must not acquire a production consumer.

`docs/scope-decision-2026-09-06-software-installation-and-certification.md`
rules that `oracle_evidence.py` is this project's parity framework and that
`certification.py` is superseded. The ruling is enforced here rather than
announced, because the hazard is specific and asymmetric: `certification.py`
sitting unused is harmless, but `certification.py` wired into a real lane would
issue verdicts from a two-state vocabulary whose default oracle is the literal
string ``"round_trip"`` -- the one kind of evidence Plan 033 exists to reject.

If `certification.py` is ever deleted, delete this test with it. Until then it
is what makes "superseded" mean something.
"""

from __future__ import annotations

import ast
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SUPERSEDED = "certification"


def _modules_importing(target: str, roots: tuple[str, ...]) -> set[str]:
    """Every file under *roots* whose AST imports ``gpo_studio.<target>``."""
    found: set[str] = set()
    for root in roots:
        base = _ROOT / root
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):  # pragma: no cover
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    if module.endswith(target) and (
                        module.startswith("gpo_studio") or node.level > 0
                    ):
                        found.add(str(path.relative_to(_ROOT)))
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.endswith(f"gpo_studio.{target}"):
                            found.add(str(path.relative_to(_ROOT)))
    return found


def test_certification_module_has_no_production_consumer() -> None:
    """Nothing outside ``tests/`` may import the superseded module.

    Named in the scope decision as the thing that enforces it. Loosening this
    assertion falsifies that document and must be done in the same change.
    """
    consumers = _modules_importing(_SUPERSEDED, ("src", "scripts"))
    assert not consumers, (
        f"certification.py is superseded by oracle_evidence.py but is imported by "
        f"{sorted(consumers)}. Its ConformanceResult is a two-state model with no "
        "'inconclusive' or 'unsupported', and its default oracle is 'round_trip'. "
        "Use gpo_studio.oracle_evidence instead, or revise the scope decision in "
        "docs/scope-decision-2026-09-06-software-installation-and-certification.md."
    )


def test_the_parity_framework_still_has_four_evidence_states() -> None:
    """The vocabulary the ruling picked is the one with the states it picked it for.

    Guards the *reason* for the ruling, not just its conclusion: if
    ``oracle_evidence`` ever loses ``unsupported`` or ``inconclusive``, the
    argument in the scope decision no longer holds and the decision needs
    retaking rather than inheriting.
    """
    from gpo_studio.oracle_evidence import EvidenceState

    states = set(EvidenceState.__args__)  # type: ignore[attr-defined]
    assert states == {"pass", "fail", "unsupported", "inconclusive"}, (
        f"EvidenceState is {sorted(states)}; the scope decision rests on "
        "'unsupported' being a capability downgrade and 'inconclusive' meaning "
        "the run cannot support a claim"
    )
