"""Resultant Set of Policy (RSOP) modeling engine.

Computes the effective policy settings for a target computer/user combination
from a SOM tree and a set of GPOs, following Windows Group Policy precedence
rules with security filtering, WMI filtering, block inheritance, and
loopback processing.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

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


@dataclass(frozen=True, slots=True)
class RsopTarget:
    """The target computer/user for RSOP computation."""

    computer_name: str = ""
    computer_dn: str = ""
    user_name: str = ""
    user_dn: str = ""
    site_name: str = ""
    domain: str = ""
    group_memberships: tuple[str, ...] = ()
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


@dataclass(frozen=True, slots=True)
class RsopGpoResult:
    """Result for a single GPO in the RSOP computation."""

    gpo_guid: str
    gpo_name: str
    is_applied: bool
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
        return tuple(g for g in self.gpo_results if g.is_applied)

    def gpos_filtered(self) -> tuple[RsopGpoResult, ...]:
        """Get only GPOs that were filtered out."""
        return tuple(g for g in self.gpo_results if not g.is_applied)


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
    change_type: Literal["added", "removed", "modified", "gpo_changed"] = "modified"


def _setting_identity(setting: RegistrySetting | RsopSettingResult) -> tuple[str, str, str]:
    return (setting.side, setting.key.casefold(), setting.value_name.casefold())


def _gpo_by_guid(gpos: Sequence[GPO]) -> dict[str, GPO]:
    return {gpo.guid: gpo for gpo in gpos}


def _target_identities(target: RsopTarget) -> set[str]:
    """Return the principal identifiers that can match security filters."""
    ids: set[str] = set()
    if target.computer_name:
        ids.add(target.computer_name)
    if target.user_name:
        ids.add(target.user_name)
    if target.computer_dn:
        ids.add(target.computer_dn)
    if target.user_dn:
        ids.add(target.user_dn)
    ids.update(target.group_memberships)
    return ids


def _filter_matches(filter_: SecurityFilter, target: RsopTarget) -> bool:
    """Return True when a security filter principal matches the target.

    SIDs and principals are compared case-insensitively, matching Windows
    semantics where SIDs are case-insensitive.
    """
    target_ids = {t.casefold() for t in _target_identities(target)}
    sid_match = bool(filter_.sid) and filter_.sid.casefold() in target_ids
    principal_match = bool(filter_.principal) and filter_.principal.casefold() in target_ids
    return sid_match or principal_match


def _gpo_filter_status(
    gpo: GPO,
    entry: PrecedenceEntry,
    target: RsopTarget,
    wmi_results: dict[str, WmiEvaluation] | None = None,
) -> tuple[bool, tuple[str, ...], tuple[str, ...]]:
    """Return (is_applied, blocking_reasons, warning_reasons) for a GPO."""
    blocking: list[str] = []
    warnings: list[str] = []

    if entry.blocked:
        blocking.append("blocked_by_inheritance")
        return False, tuple(blocking), tuple(warnings)

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
        denied = any(
            filter_.deny
            and filter_.permission == "apply"
            and _filter_matches(filter_, target)
            for filter_ in filters
        )
        if denied:
            blocking.append("security_filter_denied")
        else:
            has_apply = any(
                filter_.permission == "apply"
                and not filter_.deny
                and _filter_matches(filter_, target)
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

    return (not blocking), tuple(blocking), tuple(warnings)


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
    gpo_states: dict[str, tuple[bool, tuple[str, ...], tuple[str, ...]]]
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
    gpo_states: dict[str, tuple[bool, tuple[str, ...], tuple[str, ...]]] = {}

    # Process precedence entries from lowest to highest precedence so that
    # the highest-precedence GPO's settings win conflicts. precedence_order
    # still reflects the original highest-first position (1 = highest).
    entries_with_order = list(enumerate(precedence.entries, start=1))
    for precedence_order, entry in reversed(entries_with_order):
        gpo = gpo_map.get(entry.gpo_guid)
        if gpo is None:
            continue

        applies, blocking, warnings = _gpo_filter_status(
            gpo, entry, query.target, dict(query.wmi_filter_results)
        )
        side_enabled = _side_enabled(gpo, side)
        if side_enabled and applies:
            effective_applies = True
        else:
            effective_applies = False
            if applies and not side_enabled:
                blocking = blocking + (f"{side}_side_disabled",)
        gpo_states[gpo.guid] = (effective_applies, blocking, warnings)

        if not effective_applies:
            continue

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

    return _SideResolution(
        settings=tuple(settings_by_identity.values()),
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

            comp_state = computer_resolution.gpo_states.get(gpo.guid, (False, (), ()))
            user_state = user_resolution.gpo_states.get(gpo.guid, (False, (), ()))

            # The GPO is applied if it is applied to at least one side.
            is_applied = comp_state[0] or user_state[0]
            all_reasons = set(comp_state[1]) | set(user_state[1])
            all_warnings = set(comp_state[2]) | set(user_state[2])
            all_reasons.update(all_warnings)

            settings_applied = len(settings_by_gpo.get(gpo.guid, ()))

            # Count settings this GPO contributed that were overridden by later GPOs.
            settings_overridden = 0
            if is_applied:
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
                is_applied=is_applied,
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
            if value_changed:
                change_type: Literal["added", "removed", "modified", "gpo_changed"] = "modified"
            elif gpo_changed:
                change_type = "gpo_changed"
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
    "RsopDiff",
    "RsopGpoResult",
    "RsopMode",
    "RsopQuery",
    "RsopResult",
    "RsopSettingResult",
    "RsopTarget",
    "compare_rsop_results",
    "compute_rsop",
]
