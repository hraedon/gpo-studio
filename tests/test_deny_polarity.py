"""WI-041: a deny must survive every boundary it crosses, or be refused.

`SecurityFilter.deny` arrived with WI-033 and was wired into `rsop.py` only.
Everything else kept treating a filter as `(principal, permission)`, so the
polarity was erased on persistence, invisible to the review hash and the diff,
unauthorable through the API -- and, worst, INVERTED on export: a GPO authored
as "keep this off these machines" emitted `Set-GPPermission -PermissionLevel
GpoApply`, byte-identical to the allow plan.

These tests are deliberately grouped by boundary rather than by module. The
defect was not in any one file; it was in the assumption, held in five places
independently, that a filter's meaning is its permission. A per-module test
would have passed in every module.
"""

from __future__ import annotations

import pytest

from gpo_studio import canonical, diff, export, store
from gpo_studio.model import GPO, SecurityFilter, ValidationError

_GUID = "11111111-2222-3333-4444-555555555555"


def _filter(*, deny: bool, permission: str = "apply") -> SecurityFilter:
    return SecurityFilter(
        id="sf-1",
        principal="LabCL01$",
        permission=permission,  # type: ignore[arg-type]
        target_type="computer",
        deny=deny,
    )


def _gpo(*, deny: bool, permission: str = "apply") -> GPO:
    return GPO(
        guid=_GUID,
        name="Studio-Deny-Probe",
        security_filters=(_filter(deny=deny, permission=permission),),
    )


class TestDenySurvivesPersistence:
    def test_round_trip_preserves_a_deny(self) -> None:
        """Reload used to drop the field, so a deny came back an ALLOW."""
        original = _filter(deny=True)
        restored = store._security_filter(
            {
                "id": original.id,
                "principal": original.principal,
                "permission": original.permission,
                "inheritable": original.inheritable,
                "target_type": original.target_type,
                "sid": original.sid,
                "deny": original.deny,
            }
        )
        assert restored.deny is True

    def test_a_row_with_no_polarity_is_an_allow(self) -> None:
        """Every row written before WI-033 has no `deny` key and meant allow."""
        restored = store._security_filter(
            {"id": "sf-1", "principal": "LabCL01$", "permission": "apply"}
        )
        assert restored.deny is False


class TestDenyIsVisibleToReview:
    def test_allow_and_deny_do_not_share_a_semantic_hash(self) -> None:
        """The review hash exists to make a semantic change visible.

        Allow and deny for the same principal and right are opposite policy,
        and they hashed identically -- so the one change most worth catching
        was the one the hash could not see.
        """
        assert canonical.semantic_hash(_gpo(deny=True)) != canonical.semantic_hash(
            _gpo(deny=False)
        )

    def test_the_diff_sees_a_polarity_flip(self) -> None:
        assert not diff._security_filters_equal(_filter(deny=True), _filter(deny=False))

    def test_the_diff_still_matches_identical_filters(self) -> None:
        """The control: without it, the test above passes on a broken comparator."""
        assert diff._security_filters_equal(_filter(deny=True), _filter(deny=True))

    def test_a_gpo_with_no_deny_hashes_exactly_as_before(self) -> None:
        """The compatibility property, pinned so nobody tidies it away.

        `deny` is serialized ONLY when true. Emitting it unconditionally would
        move `policy_semantic_sha256` and `review_model_sha256` for every GPO
        ever hashed, including those with no deny -- invalidating the
        hash-pinned release evidence and every detached cairn signature over a
        `canonical_pack_hash`.

        The golden-vector tests in `test_canonical.py` are the other half of
        this guard; they failed loudly when the field was emitted always, which
        is how the consequence was noticed at all.
        """
        allow_only = canonical.canonical_json(canonical.semantic_dict(_gpo(deny=False)))
        assert '"deny"' not in allow_only

        with_deny = canonical.canonical_json(canonical.semantic_dict(_gpo(deny=True)))
        assert '"deny":true' in with_deny.replace(" ", "")


class TestExportRefusesADeny:
    """`Set-GPPermission` cannot express a deny, so no plan is generated.

    Ruled 2026-08-05. The alternatives both fail in the dangerous direction:
    emitting `GpoApply` inverts the meaning, and dropping the row publishes a
    plan that looks complete while leaving the deny unapplied.
    """

    def test_a_deny_on_apply_is_refused(self) -> None:
        with pytest.raises(ValidationError) as exc:
            export.powershell_plan(_gpo(deny=True))
        assert any(i.code == "deny_filter_not_expressible" for i in exc.value.issues)

    def test_a_deny_on_read_is_refused_too(self) -> None:
        """WI-040's shape. It used to export as a `GpoRead` GRANT."""
        with pytest.raises(ValidationError) as exc:
            export.powershell_plan(_gpo(deny=True, permission="read"))
        assert any(i.code == "deny_filter_not_expressible" for i in exc.value.issues)

    def test_the_refusal_names_the_principal(self) -> None:
        """An operator has to be able to find the row that blocked the plan."""
        with pytest.raises(ValidationError) as exc:
            export.powershell_plan(_gpo(deny=True))
        assert "LabCL01$" in exc.value.issues[0].message

    def test_an_ordinary_allow_still_exports(self) -> None:
        """The control. A refusal test proves nothing if nothing ever exports."""
        plan = export.powershell_plan(_gpo(deny=False))
        assert "Set-GPPermission" in plan
        assert "GpoApply" in plan

    def test_the_refused_plan_is_never_partially_emitted(self) -> None:
        """No half-written plan escapes: the refusal precedes every append."""
        with pytest.raises(ValidationError):
            export.powershell_plan(_gpo(deny=True))
