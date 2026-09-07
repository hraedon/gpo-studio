"""The parity framework's vocabulary is the reason a ruling picked it.

`docs/scope-decision-2026-09-06-software-installation-and-certification.md`
ruled that `oracle_evidence.py` is this project's parity framework and that
`certification.py` was superseded. `certification.py` was **deleted** on
2026-09-07 under WI-056, and the enforcement test that kept anything from
importing it went with it -- a module that does not exist cannot acquire a
consumer, and a test guarding against that would be theatre.

What survives is the half that was never about `certification.py`: the ruling
rests on `oracle_evidence` having four evidence states rather than two, and
that argument can still be falsified by a later edit.
"""

from __future__ import annotations


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
