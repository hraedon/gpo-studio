"""Resultant Set of Policy (RSOP) modeling engine.

Computes the effective policy settings for a target computer/user combination
from a SOM tree and a set of GPOs, following Windows Group Policy precedence
rules with security filtering, WMI filtering, block inheritance, and
loopback processing.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Literal, assert_never

from .model import GPO, RegistrySetting, SecurityFilter, ValidationError, ValidationIssue
from .som import PrecedenceEntry, SomNode, SomPrecedence, compute_precedence

RsopMode = Literal["planning", "logging"]
LoopbackMode = Literal["disabled", "merge", "replace"]
#: How a WMI filter turned out on a target.
#:
#: ``"unevaluatable"`` is not a third flavour of false, and the distinction was
#: measured rather than reasoned: a filter naming a class the target does not
#: have cannot be true, and **Windows fails closed on it** (WI-039, run
#: ``rsop-observe-20260804153726-7284``). A filter simply ABSENT from the
#: mapping still means "nobody looked", which is a different fact and keeps its
#: old behaviour -- the GPO applies and the result warns.
WmiEvaluation = bool | Literal["unevaluatable"]

#: Whether a GPO reached a target, as a CLOSED set rather than a bool.
#:
#: ``"unevaluable"`` is the one this type exists for. A bool has exactly two
#: answers and the model has three situations: it applies, it is kept off, and
#: -- for regions no oracle has measured -- nobody knows. WI-043: a deny on Read
#: is certified for the COMPUTER (WI-040, run
#: ``rsop-observe-20260805221707-4871``) and is unmeasured for the USER, because
#: MS16-072 has a user's GPOs retrieved in the computer's security context, so
#: the denied principal may never be the reading one. Answering that with
#: ``False`` would be as unfounded as answering it with ``True``; both convert an
#: open question into policy behaviour, which is what WI-039 and WI-041 each
#: ruled against in their own way.
#:
#: There is deliberately no ``is_applied`` bool anywhere in this module. It was
#: removed rather than kept as a convenience property: every caller that wrote
#: ``if result.is_applied`` would have silently read ``unevaluable`` as "not
#: applied", which is precisely the failure this type prevents. Dispatch on this
#: with ``assert_never`` and the type checker will find the callers that have not
#: considered the third case.
RsopGpoStatus = Literal["applied", "blocked", "unevaluable"]

#: How one setting differs between two RSOP results.
#:
#: ``"uncertainty_changed"`` carries no value change at all: the winner and its
#: value are identical and what moved is whether an unevaluable GPO could have
#: overridden it. That is still a difference between two estates, and omitting
#: it made `compare_rsop_results` the one place uncertainty could leave the
#: module silently.
RsopChangeType = Literal[
    "added", "removed", "modified", "gpo_changed", "uncertainty_changed"
]


@dataclass(frozen=True, slots=True)
class RsopTarget:
    """The target computer/user for RSOP computation."""

    computer_name: str = ""
    computer_dn: str = ""
    user_name: str = ""
    user_dn: str = ""
    site_name: str = ""
    domain: str = ""
    #: WI-047. PER SIDE, and the flat `group_memberships` it replaced is gone
    #: rather than kept as a convenience.
    #:
    #: A security filter is evaluated against ONE principal's token, and which
    #: principal that is depends on the right. The old single tuple had nowhere
    #: to record whether a group belonged to the computer or to the user, so a
    #: filter naming a computer group could decide the user side and vice versa.
    #: That was not a matcher bug that could be fixed in the matcher: the type
    #: had no answer to give it.
    #:
    #: Keeping a merged alias would let every existing caller read "both sides"
    #: while looking correct, which is the same reason WI-043 deleted
    #: `is_applied` outright instead of leaving a property behind.
    computer_group_memberships: tuple[str, ...] = ()
    user_group_memberships: tuple[str, ...] = ()
    loopback_mode: LoopbackMode = "disabled"
    slow_link: bool = False
    safe_mode: bool = False

    def validate(self) -> tuple[ValidationIssue, ...]:
        """Validate target fields."""
        issues: list[ValidationIssue] = []
        if not self.computer_name and not self.user_name:
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="empty_target",
                    message="At least one of computer_name or user_name must be provided.",
                    path="target",
                )
            )
        if not self.domain:
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="empty_domain",
                    message="domain is required.",
                    path="target.domain",
                )
            )
        if self.loopback_mode == "replace" and not self.computer_name:
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="loopback_replace_without_computer",
                    message="loopback_mode=replace requires a computer target.",
                    path="target.loopback_mode",
                )
            )
        return tuple(issues)


@dataclass(frozen=True, slots=True)
class RsopQuery:
    """A complete RSOP query specification."""

    query_id: str
    mode: RsopMode = "planning"
    target: RsopTarget = field(default_factory=RsopTarget)
    som_nodes: tuple[SomNode, ...] = field(default_factory=tuple)
    gpos: tuple[GPO, ...] = field(default_factory=tuple)
    #: How each WMI filter evaluated ON THIS TARGET, keyed by ``WmiFilter.id``.
    #:
    #: Studio does not evaluate WQL and should not: that is the CSE's job
    #: against the live machine. What it can do is honour an answer a caller
    #: already has -- from a lab observation, an inventory, or an operator who
    #: knows the machine. Before this existed, a WMI-filtered GPO was predicted
    #: to apply whatever its filter would evaluate to, which is the failure
    #: direction that tells an operator settings will arrive when they will not
    #: (WI-035, demonstrated against a real client).
    #:
    #: A filter absent from this mapping stays UNKNOWN and keeps the old
    #: behaviour -- the GPO applies and the result carries
    #: ``wmi_filter_unknown``. Guessing "unknown means false" would trade a
    #: false promise for a false absence, and an absence is the harder error to
    #: notice.
    wmi_filter_results: tuple[tuple[str, WmiEvaluation], ...] = ()
    simulate_no_loopback: bool = False
    simulate_slow_link: bool | None = None
    simulate_safe_mode: bool | None = None
    created_at: str = ""
    created_by: str = ""

    def validate(self) -> tuple[ValidationIssue, ...]:
        """Validate query fields."""
        issues: list[ValidationIssue] = []
        if not self.query_id:
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="empty_query_id",
                    message="query_id is required.",
                    path="query_id",
                )
            )
        if not self.som_nodes:
            issues.append(
                ValidationIssue(
                    severity="warning",
                    code="empty_som_nodes",
                    message="No SOM tree provided; results will be limited.",
                    path="som_nodes",
                )
            )
        if not self.gpos:
            issues.append(
                ValidationIssue(
                    severity="warning",
                    code="empty_gpos",
                    message="No GPOs provided to evaluate.",
                    path="gpos",
                )
            )
        issues.extend(self.target.validate())
        return tuple(issues)


@dataclass(frozen=True, slots=True)
class RsopSettingResult:
    """A single setting's effective value after precedence resolution."""

    setting_id: str
    side: Literal["computer", "user"]
    hive: str = ""
    key: str = ""
    value_name: str = ""
    effective_value: str | int | list[str] = ""
    winning_gpo_guid: str = ""
    winning_gpo_name: str = ""
    overridden_by: tuple[str, ...] = ()
    is_enforced: bool = False
    precedence_order: int = 0
    #: GUIDs of GPOs that write this same value and could not be evaluated.
    #:
    #: Uncertainty about a GPO is uncertainty about every value it writes. If an
    #: unevaluable GPO sits above this winner, the winner named here is the
    #: answer *if* that GPO does not apply, and the caller has to be told so
    #: rather than handed a value that looks settled.
    unevaluable_gpos: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RsopGpoResult:
    """Result for a single GPO in the RSOP computation."""

    gpo_guid: str
    gpo_name: str
    status: RsopGpoStatus
    filtering_reasons: tuple[str, ...] = ()
    precedence: int = 0
    link_scope: str = ""
    is_enforced: bool = False
    is_blocked: bool = False
    settings_applied: int = 0
    settings_overridden: int = 0


@dataclass(frozen=True, slots=True)
class RsopResult:
    """Complete RSOP computation result."""

    query_id: str
    mode: RsopMode
    target: RsopTarget
    computer_settings: tuple[RsopSettingResult, ...] = field(default_factory=tuple)
    user_settings: tuple[RsopSettingResult, ...] = field(default_factory=tuple)
    gpo_results: tuple[RsopGpoResult, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)
    computed_at: str = ""

    def get_effective_value(
        self,
        side: Literal["computer", "user"],
        key: str,
        value_name: str,
    ) -> RsopSettingResult | None:
        """Look up the effective value for a specific setting."""
        settings = self.computer_settings if side == "computer" else self.user_settings
        key_fold = key.casefold()
        value_fold = value_name.casefold()
        for setting in settings:
            if setting.key.casefold() == key_fold and setting.value_name.casefold() == value_fold:
                return setting
        return None

    def gpos_applied(self) -> tuple[RsopGpoResult, ...]:
        """Get only GPOs that were applied."""
        return tuple(g for g in self.gpo_results if g.status == "applied")

    def gpos_filtered(self) -> tuple[RsopGpoResult, ...]:
        """Get only GPOs Windows is known to keep off the target.

        This is NOT the complement of :meth:`gpos_applied`. A GPO whose status
        is ``unevaluable`` is in neither set, because calling it filtered would
        assert the very thing that is unknown. Use :meth:`gpos_unevaluable`.
        """
        return tuple(g for g in self.gpo_results if g.status == "blocked")

    def gpos_unevaluable(self) -> tuple[RsopGpoResult, ...]:
        """GPOs whose outcome this model cannot honestly predict (WI-043)."""
        return tuple(g for g in self.gpo_results if g.status == "unevaluable")

    def is_conclusive(self) -> bool:
        """True when every GPO resolved to a definite answer."""
        return not self.gpos_unevaluable()


@dataclass(frozen=True, slots=True)
class RsopDiff:
    """A single difference between two RSOP results."""

    setting_id: str
    side: Literal["computer", "user"]
    key: str
    value_name: str
    baseline_value: str | int | list[str] = ""
    current_value: str | int | list[str] = ""
    baseline_gpo: str = ""
    current_gpo: str = ""
    change_type: RsopChangeType = "modified"


def _setting_identity(setting: RegistrySetting | RsopSettingResult) -> tuple[str, str, str]:
    return (setting.side, setting.key.casefold(), setting.value_name.casefold())


def _gpo_by_guid(gpos: Sequence[GPO]) -> dict[str, GPO]:
    return {gpo.guid: gpo for gpo in gpos}


def _principal_identities(target: RsopTarget, principal: Literal["computer", "user"]) -> set[str]:
    """Return the identifiers in ONE principal's token.

    WI-047. This used to return the union of both principals' identifiers, so
    every filter was tested against a set that answered for whichever of the two
    happened to match. Each principal now answers only for itself, and the
    caller states which one the right in question is evaluated against.
    """
    ids: set[str] = set()
    if principal == "computer":
        if target.computer_name:
            ids.add(target.computer_name)
        if target.computer_dn:
            ids.add(target.computer_dn)
        ids.update(target.computer_group_memberships)
    else:
        if target.user_name:
            ids.add(target.user_name)
        if target.user_dn:
            ids.add(target.user_dn)
        ids.update(target.user_group_memberships)
    return ids


def _filter_matches(filter_: SecurityFilter, identities: set[str]) -> bool:
    """Return True when a security filter names one of ``identities``.

    SIDs and principals are compared case-insensitively, matching Windows
    semantics where SIDs are case-insensitive.

    Takes the resolved identity set rather than the target, so that a caller
    cannot forget to say which principal it is asking about -- the omission the
    previous signature made not merely possible but the default.
    """
    folded = {t.casefold() for t in identities}
    sid_match = bool(filter_.sid) and filter_.sid.casefold() in folded
    principal_match = bool(filter_.principal) and filter_.principal.casefold() in folded
    return sid_match or principal_match


def _gpo_filter_status(
    gpo: GPO,
    entry: PrecedenceEntry,
    target: RsopTarget,
    side: Literal["computer", "user"],
    wmi_results: dict[str, WmiEvaluation] | None = None,
) -> tuple[RsopGpoStatus, tuple[str, ...], tuple[str, ...]]:
    """Return (status, blocking_reasons, warning_reasons) for a GPO on one side.

    ``side`` is not decoration. Filtering used to be resolved without it, so
    every rule here answered for both sides from one evaluation -- which is how
    the read-deny rule certified on the computer came to be asserted for the
    user as well, on nothing (WI-043).
    """
    blocking: list[str] = []
    warnings: list[str] = []
    unevaluable: list[str] = []

    if entry.blocked:
        blocking.append("blocked_by_inheritance")
        return "blocked", tuple(blocking), tuple(warnings)

    filters = gpo.security_filters
    if filters:
        # DENY WINS, which is how token evaluation works and is why this is
        # checked before the allow.
        #
        # Without it, a GPO whose DACL holds both an allow and a deny for the
        # same principal was reported as applying -- the model saying a machine
        # would receive settings that Windows keeps off it. That failure
        # direction is the dangerous one for an operator asking "what will this
        # machine get?", and it was demonstrated against a real client before
        # being fixed here (WI-033).
        # APPLYING A GPO TAKES BOTH RIGHTS, so there are two independent denies
        # and this used to see only one. Measured, not reasoned: run
        # `rsop-observe-20260805045139-3731` authored a deny on GenericRead
        # beside an INTACT Read + Apply allow, and Windows did not apply the GPO
        # (WI-040). The control row in the same run carried the identical grant
        # without the read deny and applied, so the absence was the deny working
        # rather than a DACL write that failed.
        #
        # The two are reported separately. An operator reading
        # `security_filter_denied` would go looking at Apply Group Policy and
        # find it granted; the right actually denied is the one worth naming.
        # Same argument as WI-039's `wmi_filter_unevaluatable` versus
        # `wmi_filter_false`.
        #
        # WI-043/WI-047: THE TWO RIGHTS ARE EVALUATED AGAINST DIFFERENT
        # PRINCIPALS. THREE OF THE FOUR READ CELLS ARE MEASURED AND THE FOURTH
        # IS REASONED; THIS COMMENT SAYS WHICH IS WHICH, BECAUSE THE ORIGINAL
        # VERSION OF IT DID NOT AND THAT IS WHAT WI-043 WAS OPENED ABOUT.
        #
        # Read deny, by resolving side and by the principal the deny NAMES:
        #
        #   side=computer, names the COMPUTER -> BLOCKS   MEASURED
        #                  (rsop-observe-20260805045139-3731, WI-040)
        #   side=user,     names the COMPUTER -> BLOCKS   MEASURED
        #                  (rsop-user-observe-20260806165543-8004, row B)
        #   side=user,     names the USER     -> APPLIES  MEASURED
        #                  (rsop-user-observe-20260806165543-8004, row A)
        #   side=computer, names the USER     -> APPLIES  REASONED ONLY
        #                  (no estate row exists; WI-049)
        #
        # The three measured cells collapse to one sentence: A READ DENY GATES
        # POLICY WHEN IT NAMES THE COMPUTER, ON EITHER SIDE, because MS16-072
        # has the computer perform the retrieval for both sides. The side being
        # resolved does not decide it; the principal named by the deny does.
        #
        # The fourth cell follows from the same mechanism -- a user-named ACE
        # cannot affect a retrieval the computer performs with its own token --
        # but NOTHING MEASURED IT. Under the pre-WI-047 union it BLOCKED; this
        # tranche flips it to APPLIES, which is the over-promising direction,
        # the one that tells an operator about settings that never arrive.
        # `TestTheUnmeasuredCellsArePinned` holds the chosen answer so the flip
        # cannot drift unnoticed, and WI-049 carries the measurement.
        #
        # GROUP MEMBERSHIP IS UNIT-TESTED ONLY, in both directions. The
        # candidate builder always passes `computer_group_memberships=()`, so no
        # estate run has ever exercised a deny that matches through a group.
        #
        # Apply Group Policy is the other way round -- it is evaluated against
        # the principal the policy applies TO. THE DIAGONAL IS CERTIFIED on both
        # sides: the user-scope deny (WI-033,
        # rsop-user-observe-20260804150527-3868) and the computer-scope deny row
        # of `computer-security-filtering`. THE OFF-DIAGONAL IS NOT. Matching
        # `denied_apply` against `side_identities` alone means a computer-named
        # Apply deny on the USER side no longer blocks, where the pre-WI-047
        # union blocked it. That is the same reasoned flip as the fourth read
        # cell, in the same direction, and it is recorded here rather than
        # absorbed silently. WI-049 carries it too.
        #
        # Row A is the reason this cannot be written with a single identity set.
        # Before WI-047 the model matched every filter against the union of both
        # principals, so row A's user-named deny and row B's computer-named deny
        # were indistinguishable and both had to abstain. The abstention is gone
        # because the region was measured -- for the three cells above. For the
        # fourth it is gone because a rule was reasoned, and saying so is the
        # difference between this comment and the one it replaced.
        read_identities = _principal_identities(target, "computer")
        side_identities = _principal_identities(target, side)

        denied_apply = any(
            filter_.deny
            and filter_.permission == "apply"
            and _filter_matches(filter_, side_identities)
            for filter_ in filters
        )
        denied_read = any(
            filter_.deny
            and filter_.permission == "read"
            and _filter_matches(filter_, read_identities)
            for filter_ in filters
        )
        if denied_apply:
            blocking.append("security_filter_denied")
        elif denied_read:
            blocking.append("security_filter_read_denied")
        else:
            has_apply = any(
                filter_.permission == "apply"
                and not filter_.deny
                and _filter_matches(filter_, side_identities)
                for filter_ in filters
            )
            if not has_apply:
                blocking.append("security_filter_mismatch")

    if gpo.wmi_filter is not None and gpo.wmi_filter.query:
        # Three states, and the third is why the warning survives.
        #
        # A filter the caller has evaluated to FALSE blocks the GPO, which is
        # what Windows does and what this could not previously express. One
        # evaluated TRUE applies silently. One nobody has evaluated is still
        # unknown: the GPO applies and the warning says the prediction rests on
        # an unevaluated filter, because inventing an answer here would replace
        # a visible gap with an invisible one.
        evaluated = (wmi_results or {}).get(gpo.wmi_filter.id)
        if evaluated is False:
            blocking.append("wmi_filter_false")
        elif evaluated == "unevaluatable":
            # Its own reason, not `wmi_filter_false`: the filter did not
            # evaluate to false, it could not be evaluated at all, and an
            # operator reading the reason should be able to tell those apart.
            blocking.append("wmi_filter_unevaluatable")
        elif evaluated is None:
            warnings.append("wmi_filter_unknown")

    # A definite block outranks an open question: if Windows keeps the GPO off
    # for a reason that IS measured, the unmeasured one changes nothing.
    if blocking:
        return "blocked", tuple(blocking), tuple(warnings)
    if unevaluable:
        return "unevaluable", tuple(unevaluable), tuple(warnings)
    return "applied", (), tuple(warnings)


def query_reaches_a_reasoned_cell(query: RsopQuery) -> bool:
    """Does this topology's answer rest on WI-049's unmeasured cells?

    Two off-diagonal cells were flipped from BLOCKS to APPLIES by reasoning
    rather than by measurement, both in the over-promising direction:

      * a READ deny naming the USER, resolved on the computer side;
      * an APPLY deny naming the COMPUTER, resolved on the user side.

    A caller reaches them only by supplying a deny that names the principal
    which is *not* the one being resolved, so this is an exact test rather than
    a heuristic -- which matters, because a limitation emitted on a guess would
    be absent precisely when the guess was wrong.

    Public because the API layer must disclose this at the point the answer is
    read. `docs/capability-matrix.md` is not the payload, and a caller reading
    JSON is not reading the matrix.
    """
    computer = _principal_identities(query.target, "computer")
    user = _principal_identities(query.target, "user")
    if not user:
        # No user in the topology: neither cell is reachable, because both need
        # a principal on the side that is not being resolved.
        return False
    for gpo in query.gpos:
        for filter_ in gpo.security_filters:
            if not filter_.deny:
                continue
            names_user = _filter_matches(filter_, user)
            names_computer = _filter_matches(filter_, computer)
            if filter_.permission == "read" and names_user and not names_computer:
                return True
            if filter_.permission == "apply" and names_computer and not names_user:
                return True
    return False


def _status_certainty(status: RsopGpoStatus) -> int:
    """Rank a status so two sides can be merged by a total order.

    `applied` > `unevaluable` > `blocked`, and both comparisons are deliberate.
    Applied on either side keeps the existing meaning of "applied to at least
    one side" (WI-032 tracks the missing per-side sets). Unevaluable beats
    blocked because an open question on one side is not settled by a definite
    answer on the other -- reporting `blocked` there would settle it by
    omission, which is the whole failure WI-043 exists to prevent.

    Written as a dispatch rather than a dict so `assert_never` can see it: a
    fourth status has to be given a rank here, and the type checker says so.
    """
    if status == "applied":
        return 2
    if status == "unevaluable":
        return 1
    if status == "blocked":
        return 0
    assert_never(status)


def _merge_side_status(computer: RsopGpoStatus, user: RsopGpoStatus) -> RsopGpoStatus:
    return max(computer, user, key=_status_certainty)


def _side_enabled(gpo: GPO, side: Literal["computer", "user"]) -> bool:
    if side == "computer":
        return gpo.computer_enabled
    return gpo.user_enabled


def _precedence_for_side(
    query: RsopQuery,
    side: Literal["computer", "user"],
) -> SomPrecedence:
    """Compute the SOM precedence list for the given side."""
    target = query.target
    use_computer_dn = side == "computer" or (
        target.loopback_mode == "replace" and not query.simulate_no_loopback
    )
    dn = target.computer_dn if use_computer_dn else target.user_dn

    if not dn:
        return SomPrecedence(target_dn="", entries=(), warnings=())
    return compute_precedence(query.som_nodes, dn)


def _merge_user_precedence(
    computer_precedence: SomPrecedence,
    user_precedence: SomPrecedence,
) -> SomPrecedence:
    """Combine computer and user precedence lists for loopback merge.

    In merge mode, user GPOs are applied first (lower precedence) and computer
    GPOs are applied second (higher precedence), so computer GPOs win
    conflicts. The returned list is ordered highest-precedence first: computer
    entries, then user entries.
    """
    combined = computer_precedence.entries + user_precedence.entries
    warnings = user_precedence.warnings + computer_precedence.warnings
    return SomPrecedence(
        target_dn=user_precedence.target_dn or computer_precedence.target_dn,
        entries=combined,
        warnings=warnings,
    )


@dataclass(frozen=True, slots=True)
class _SideResolution:
    settings: tuple[RsopSettingResult, ...]
    gpo_states: dict[str, tuple[RsopGpoStatus, tuple[str, ...], tuple[str, ...]]]
    warnings: tuple[str, ...]


def _resolve_side(
    query: RsopQuery,
    side: Literal["computer", "user"],
    gpo_map: dict[str, GPO],
    precedence: SomPrecedence,
) -> _SideResolution:
    """Resolve settings for one side and collect per-GPO filter state."""
    settings_by_identity: dict[tuple[str, str, str], RsopSettingResult] = {}
    overridden_guids: dict[tuple[str, str, str], list[str]] = {}
    gpo_states: dict[str, tuple[RsopGpoStatus, tuple[str, ...], tuple[str, ...]]] = {}
    #: setting identity -> GUIDs of unevaluable GPOs that write it.
    unevaluable_writers: dict[tuple[str, str, str], list[str]] = {}

    # Process precedence entries from lowest to highest precedence so that
    # the highest-precedence GPO's settings win conflicts. precedence_order
    # still reflects the original highest-first position (1 = highest).
    entries_with_order = list(enumerate(precedence.entries, start=1))
    for precedence_order, entry in reversed(entries_with_order):
        gpo = gpo_map.get(entry.gpo_guid)
        if gpo is None:
            continue

        status, reasons, warnings = _gpo_filter_status(
            gpo, entry, query.target, side, dict(query.wmi_filter_results)
        )
        # A disabled side is a definite answer and outranks an open question:
        # Windows does not process a side it was told not to process, whatever
        # the filters would have decided.
        if not _side_enabled(gpo, side):
            if status != "blocked":
                reasons = reasons + (f"{side}_side_disabled",)
            status = "blocked"
        gpo_states[gpo.guid] = (status, reasons, warnings)

        if status == "unevaluable":
            # Its settings are not applied -- that would assert the GPO reached
            # the target -- but they are not silently discarded either. Every
            # value it writes is recorded against the winner below, so a caller
            # reading that winner is told the answer is conditional.
            for setting in gpo.settings:
                if setting.side == side:
                    unevaluable_writers.setdefault(_setting_identity(setting), []).append(
                        gpo.guid
                    )
            continue

        if status == "blocked":
            continue

        # Exhaustive by construction: a fourth status must not reach the
        # settings loop by falling through, which is exactly how it would
        # silently start APPLYING settings. `assert_never` makes that a
        # type error at the point the status is added, not a behaviour change
        # discovered later.
        if status != "applied":
            assert_never(status)

        for setting in gpo.settings:
            if setting.side != side:
                continue
            identity = _setting_identity(setting)
            previous = settings_by_identity.get(identity)
            if previous is not None:
                overridden_guids.setdefault(identity, []).append(previous.winning_gpo_guid)
            current_overridden = tuple(overridden_guids.get(identity, ()))
            settings_by_identity[identity] = RsopSettingResult(
                setting_id=setting.id,
                side=setting.side,
                hive=setting.hive,
                key=setting.key,
                value_name=setting.value_name,
                effective_value=setting.value,
                winning_gpo_guid=gpo.guid,
                winning_gpo_name=gpo.name,
                overridden_by=current_overridden,
                is_enforced=entry.enforced,
                precedence_order=precedence_order,
            )

    # Stamp the conditional winners last: a GPO's unevaluability is only known
    # once every entry has been walked, and it can sit either side of the winner
    # in precedence order.
    settled = tuple(
        replace(setting, unevaluable_gpos=tuple(unevaluable_writers.get(identity, ())))
        for identity, setting in settings_by_identity.items()
    )

    return _SideResolution(
        settings=settled,
        gpo_states=gpo_states,
        warnings=precedence.warnings,
    )


def compute_rsop(query: RsopQuery) -> RsopResult:
    """Compute the Resultant Set of Policy for a query.

    Raises :class:`ValidationError` if the query has any validation errors.
    """
    validation_issues = query.validate()
    errors = [i for i in validation_issues if i.severity == "error"]
    if errors:
        raise ValidationError(errors)
    warnings: list[str] = [i.message for i in validation_issues if i.severity == "warning"]

    gpo_map = _gpo_by_guid(query.gpos)

    loopback_mode = query.target.loopback_mode
    if query.simulate_no_loopback:
        loopback_mode = "disabled"

    computer_precedence = _precedence_for_side(query, "computer")
    computer_resolution = _resolve_side(query, "computer", gpo_map, computer_precedence)

    if loopback_mode == "merge":
        user_prec = _precedence_for_side(query, "user")
        user_precedence = _merge_user_precedence(computer_precedence, user_prec)
    elif loopback_mode == "replace":
        user_precedence = computer_precedence
    else:
        user_precedence = _precedence_for_side(query, "user")

    user_resolution = _resolve_side(query, "user", gpo_map, user_precedence)

    all_settings = (*computer_resolution.settings, *user_resolution.settings)
    settings_by_gpo: dict[str, list[RsopSettingResult]] = {}
    for setting in all_settings:
        settings_by_gpo.setdefault(setting.winning_gpo_guid, []).append(setting)

    # Build per-GPO results. Deduplicate by GPO GUID, preferring the earliest
    # precedence order across both precedence lists.
    gpo_results: dict[str, RsopGpoResult] = {}
    seen_order: dict[str, int] = {}

    def _process_precedence(precedence: SomPrecedence) -> None:
        for order, entry in enumerate(precedence.entries, start=1):
            gpo = gpo_map.get(entry.gpo_guid)
            if gpo is None:
                continue
            if gpo.guid in seen_order and seen_order[gpo.guid] <= order:
                continue
            seen_order[gpo.guid] = order

            blocked: RsopGpoStatus = "blocked"
            comp_state = computer_resolution.gpo_states.get(gpo.guid, (blocked, (), ()))
            user_state = user_resolution.gpo_states.get(gpo.guid, (blocked, (), ()))

            # Applied on either side wins, preserving the existing meaning of
            # "applied to at least one side" (WI-032 tracks the missing per-side
            # sets). Otherwise an open question outranks a definite block: if
            # one side is unevaluable and the other blocked, the GPO's fate is
            # not settled, and saying "blocked" would settle it by omission.
            status = _merge_side_status(comp_state[0], user_state[0])
            all_reasons = set(comp_state[1]) | set(user_state[1])
            all_warnings = set(comp_state[2]) | set(user_state[2])
            all_reasons.update(all_warnings)

            settings_applied = len(settings_by_gpo.get(gpo.guid, ()))

            # Count settings this GPO contributed that were overridden by later GPOs.
            settings_overridden = 0
            if status == "applied":
                winning_identities = {_setting_identity(s) for s in all_settings}
                for setting in gpo.settings:
                    if setting.side not in ("computer", "user"):
                        continue
                    identity = _setting_identity(setting)
                    if identity in winning_identities:
                        winner = next(
                            (s for s in all_settings if _setting_identity(s) == identity),
                            None,
                        )
                        if winner is not None and winner.winning_gpo_guid != gpo.guid:
                            settings_overridden += 1

            gpo_results[gpo.guid] = RsopGpoResult(
                gpo_guid=gpo.guid,
                gpo_name=gpo.name,
                status=status,
                filtering_reasons=tuple(sorted(all_reasons)),
                precedence=order,
                link_scope=entry.scope_dn,
                is_enforced=entry.enforced,
                is_blocked=entry.blocked,
                settings_applied=settings_applied,
                settings_overridden=settings_overridden,
            )

    _process_precedence(computer_precedence)
    _process_precedence(user_precedence)

    warnings.extend(computer_resolution.warnings)
    warnings.extend(user_resolution.warnings)

    # Surface per-GPO warning reasons (e.g. WMI filters) in the result warnings.
    gpo_warnings: set[str] = set()
    for state in (*computer_resolution.gpo_states.values(), *user_resolution.gpo_states.values()):
        gpo_warnings.update(state[2])
    warnings.extend(sorted(gpo_warnings))

    # An unevaluable GPO is a property of the whole answer, not just its own
    # row. A caller that reads only `computer_settings`/`user_settings` would
    # otherwise see a clean result with no sign that part of it is conditional.
    unevaluable = sorted(g.gpo_guid for g in gpo_results.values() if g.status == "unevaluable")
    if unevaluable:
        warnings.append(
            "rsop_result_is_not_conclusive: no measurement covers the outcome of "
            f"{', '.join(unevaluable)} (WI-043)"
        )

    return RsopResult(
        query_id=query.query_id,
        mode=query.mode,
        target=query.target,
        computer_settings=computer_resolution.settings,
        user_settings=user_resolution.settings,
        gpo_results=tuple(gpo_results.values()),
        warnings=tuple(warnings),
        computed_at="",
    )


def compare_rsop_results(
    baseline: RsopResult,
    current: RsopResult,
) -> tuple[RsopDiff, ...]:
    """Compare two RSOP results and return differences."""
    baseline_map: dict[tuple[str, str, str], RsopSettingResult] = {}
    for setting in (*baseline.computer_settings, *baseline.user_settings):
        baseline_map[_setting_identity(setting)] = setting

    current_map: dict[tuple[str, str, str], RsopSettingResult] = {}
    for setting in (*current.computer_settings, *current.user_settings):
        current_map[_setting_identity(setting)] = setting

    all_keys = set(baseline_map) | set(current_map)
    diffs: list[RsopDiff] = []

    for key in all_keys:
        baseline_setting = baseline_map.get(key)
        current_setting = current_map.get(key)

        if baseline_setting is None and current_setting is not None:
            diffs.append(
                RsopDiff(
                    setting_id=current_setting.setting_id,
                    side=current_setting.side,
                    key=current_setting.key,
                    value_name=current_setting.value_name,
                    baseline_value="",
                    current_value=current_setting.effective_value,
                    baseline_gpo="",
                    current_gpo=current_setting.winning_gpo_guid,
                    change_type="added",
                )
            )
        elif baseline_setting is not None and current_setting is None:
            diffs.append(
                RsopDiff(
                    setting_id=baseline_setting.setting_id,
                    side=baseline_setting.side,
                    key=baseline_setting.key,
                    value_name=baseline_setting.value_name,
                    baseline_value=baseline_setting.effective_value,
                    current_value="",
                    baseline_gpo=baseline_setting.winning_gpo_guid,
                    current_gpo="",
                    change_type="removed",
                )
            )
        elif baseline_setting is not None and current_setting is not None:
            value_changed = baseline_setting.effective_value != current_setting.effective_value
            gpo_changed = baseline_setting.winning_gpo_guid != current_setting.winning_gpo_guid
            # WI-043. How CERTAIN a value is, is part of the answer. Two results
            # can name the same winner with the same value and differ in whether
            # some GPO above it could have overridden it -- and the earlier
            # version of this function returned an EMPTY diff for that pair,
            # quietly deleting the uncertainty on the way out of the module.
            # A diff is what a caller compares two estates with; a change in
            # what is knowable is exactly the kind of change it must not hide.
            uncertainty_changed = set(baseline_setting.unevaluable_gpos) != set(
                current_setting.unevaluable_gpos
            )
            if value_changed:
                change_type: RsopChangeType = "modified"
            elif gpo_changed:
                change_type = "gpo_changed"
            elif uncertainty_changed:
                change_type = "uncertainty_changed"
            else:
                continue
            diffs.append(
                RsopDiff(
                    setting_id=current_setting.setting_id,
                    side=current_setting.side,
                    key=current_setting.key,
                    value_name=current_setting.value_name,
                    baseline_value=baseline_setting.effective_value,
                    current_value=current_setting.effective_value,
                    baseline_gpo=baseline_setting.winning_gpo_guid,
                    current_gpo=current_setting.winning_gpo_guid,
                    change_type=change_type,
                )
            )

    return tuple(diffs)


__all__ = [
    "RsopChangeType",
    "RsopDiff",
    "RsopGpoResult",
    "RsopGpoStatus",
    "RsopMode",
    "RsopQuery",
    "RsopResult",
    "RsopSettingResult",
    "RsopTarget",
    "compare_rsop_results",
    "compute_rsop",
    "query_reaches_a_reasoned_cell",
]
