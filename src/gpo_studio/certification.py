"""GPMC parity certification framework.

Plan 031: defines conformance test cases, certification suites, and parity
evidence portfolios so that GPO Studio can certify parity with the Group
Policy Management Console (GPMC) for registry policy, GPP XML, security
descriptors, WMI filters, links, backup/restore, RSOP, and publication.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from .model import ValidationIssue

ConformanceCategory = Literal[
    "registry_pol",
    "gpp_xml",
    "security_descriptor",
    "wmi_filter",
    "gpo_links",
    "backup_restore",
    "rsop_computation",
    "publication",
]

ConformanceLevel = Literal["required", "recommended", "optional"]


@dataclass(frozen=True, slots=True)
class ConformanceTestCase:
    case_id: str
    category: ConformanceCategory
    name: str
    description: str
    level: ConformanceLevel = "required"
    input_fixture: str = ""
    expected_output: str = ""
    oracle: str = ""

    def validate(self) -> tuple[ValidationIssue, ...]:
        """Validate test case metadata.

        Rules:
        - case_id empty -> error
        - name empty -> error
        - category not in ConformanceCategory -> error (handled by Literal)
        - oracle empty -> warning
        """
        issues: list[ValidationIssue] = []
        if not self.case_id:
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="empty_case_id",
                    message="case_id must not be empty.",
                    path="case_id",
                )
            )
        if not self.name:
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="empty_name",
                    message="name must not be empty.",
                    path="name",
                )
            )
        if not self.oracle:
            issues.append(
                ValidationIssue(
                    severity="warning",
                    code="empty_oracle",
                    message="oracle should be provided for reproducible verification.",
                    path="oracle",
                )
            )
        return tuple(issues)


@dataclass(frozen=True, slots=True)
class ConformanceResult:
    case_id: str
    passed: bool
    actual_output: str = ""
    detail: str = ""
    duration_ms: int = 0
    skipped: bool = False
    skip_reason: str = ""


@dataclass(frozen=True, slots=True)
class CertificationSuite:
    suite_id: str
    name: str
    version: str = "1.0"
    cases: tuple[ConformanceTestCase, ...] = field(default_factory=tuple)

    def cases_for_category(
        self,
        category: ConformanceCategory,
    ) -> tuple[ConformanceTestCase, ...]:
        """Get all cases in a given category."""
        return tuple(c for c in self.cases if c.category == category)

    def required_cases(self) -> tuple[ConformanceTestCase, ...]:
        """Get only required-level cases."""
        return tuple(c for c in self.cases if c.level == "required")

    def validate(self) -> tuple[ValidationIssue, ...]:
        """Validate suite metadata.

        Rules:
        - suite_id empty -> error
        - cases empty -> error
        - Duplicate case_id -> error
        """
        issues: list[ValidationIssue] = []
        if not self.suite_id:
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="empty_suite_id",
                    message="suite_id must not be empty.",
                    path="suite_id",
                )
            )
        if not self.cases:
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="empty_cases",
                    message="Certification suite must contain at least one case.",
                    path="cases",
                )
            )
        seen: set[str] = set()
        for case in self.cases:
            if case.case_id in seen:
                issues.append(
                    ValidationIssue(
                        severity="error",
                        code="duplicate_case_id",
                        message=f"Duplicate case_id {case.case_id!r} in suite.",
                        path=f"cases/{case.case_id}",
                    )
                )
            else:
                seen.add(case.case_id)
        return tuple(issues)


CertificationLevel = Literal["none", "basic", "standard", "full"]


@dataclass(frozen=True, slots=True)
class CertificationResult:
    suite_id: str
    suite_name: str
    results: tuple[ConformanceResult, ...]
    total: int
    passed: int
    failed: int
    skipped: int
    certified: bool
    certification_level: CertificationLevel = "none"
    computed_at: str = ""

    def failed_cases(self) -> tuple[ConformanceResult, ...]:
        """Get results that did not pass, including skipped cases."""
        return tuple(r for r in self.results if not r.passed or r.skipped)

    def passed_cases(self) -> tuple[ConformanceResult, ...]:
        """Get results that passed and were not skipped."""
        return tuple(r for r in self.results if r.passed and not r.skipped)

    def summary(self) -> str:
        """Human-readable summary."""
        status = "CERTIFIED" if self.certified else "NOT CERTIFIED"
        lines = [
            f"Certification result for {self.suite_name!r} ({self.suite_id})",
            f"  Level: {self.certification_level}",
            f"  Status: {status}",
            f"  Total: {self.total}, Passed: {self.passed}, "
            f"Failed: {self.failed}, Skipped: {self.skipped}",
        ]
        if self.computed_at:
            lines.append(f"  Computed at: {self.computed_at}")
        return "\n".join(lines)


def evaluate_certification(
    suite: CertificationSuite,
    results: tuple[ConformanceResult, ...],
) -> CertificationResult:
    """Evaluate certification results.

    Certification levels:
    - none: any required case failed
    - basic: all registry_pol and gpp_xml required cases pass
    - standard: basic + all security_descriptor and wmi_filter required cases pass
    - full: all required cases pass
    """
    result_by_case: dict[str, ConformanceResult] = {}
    for result in results:
        # Last write wins for duplicate case_ids.
        result_by_case[result.case_id] = result

    total = len(suite.cases)
    passed_count = 0
    failed_count = 0
    skipped_count = 0

    evaluated: list[ConformanceResult] = []
    for case in suite.cases:
        if case.case_id not in result_by_case:
            failed_count += 1
            evaluated.append(
                ConformanceResult(
                    case_id=case.case_id,
                    passed=False,
                    detail="No result provided for case.",
                )
            )
            continue

        result = result_by_case[case.case_id]
        if result.skipped:
            skipped_count += 1
            # Skipped cases do not count as passed.
            effective_passed = False
        else:
            effective_passed = result.passed

        if effective_passed:
            passed_count += 1
        else:
            failed_count += 1

        evaluated.append(result)

    required_cases = suite.required_cases()
    required_results = [
        r for r in evaluated if any(r.case_id == c.case_id for c in required_cases)
    ]
    all_required_pass = all(r.passed and not r.skipped for r in required_results)

    def _required_passes(category: ConformanceCategory) -> bool:
        ids = {c.case_id for c in suite.cases_for_category(category) if c.level == "required"}
        if not ids:
            return True
        return all(
            r.passed and not r.skipped
            for r in required_results
            if r.case_id in ids
        )

    def _has_cases(category: ConformanceCategory) -> bool:
        return bool(suite.cases_for_category(category))

    if all_required_pass:
        level: CertificationLevel
        if (
            _has_cases("gpo_links")
            and _has_cases("backup_restore")
            and _has_cases("rsop_computation")
            and _has_cases("publication")
            and _required_passes("gpo_links")
            and _required_passes("backup_restore")
            and _required_passes("rsop_computation")
            and _required_passes("publication")
        ):
            level = "full"
        elif (
            _has_cases("security_descriptor")
            and _has_cases("wmi_filter")
            and _required_passes("security_descriptor")
            and _required_passes("wmi_filter")
        ):
            level = "standard"
        elif _has_cases("registry_pol") and _has_cases("gpp_xml") \
                and _required_passes("registry_pol") and _required_passes("gpp_xml"):
            level = "basic"
        else:
            level = "none"
    else:
        level = "none"

    return CertificationResult(
        suite_id=suite.suite_id,
        suite_name=suite.name,
        results=tuple(evaluated),
        total=total,
        passed=passed_count,
        failed=failed_count,
        skipped=skipped_count,
        certified=all_required_pass,
        certification_level=level,
        computed_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )


EvidenceType = Literal[
    "round_trip",
    "windows_oracle",
    "gpmc_edit",
    "client_apply",
    "crash_recovery",
]


@dataclass(frozen=True, slots=True)
class ParityEvidence:
    evidence_id: str
    case_id: str
    evidence_type: EvidenceType
    description: str
    artifact_hash: str = ""
    collected_at: str = ""
    collected_by: str = ""
    environment: str = ""

    def validate(self) -> tuple[ValidationIssue, ...]:
        """Validate evidence metadata.

        Rules:
        - evidence_id empty -> error
        - case_id empty -> error
        - description empty -> error
        - artifact_hash empty -> warning (no evidence artifact)
        """
        issues: list[ValidationIssue] = []
        if not self.evidence_id:
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="empty_evidence_id",
                    message="evidence_id must not be empty.",
                    path="evidence_id",
                )
            )
        if not self.case_id:
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="empty_case_id",
                    message="case_id must not be empty.",
                    path="case_id",
                )
            )
        if not self.description:
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="empty_description",
                    message="description must not be empty.",
                    path="description",
                )
            )
        if not self.artifact_hash:
            issues.append(
                ValidationIssue(
                    severity="warning",
                    code="empty_artifact_hash",
                    message="artifact_hash should be provided to link evidence artifacts.",
                    path="artifact_hash",
                )
            )
        return tuple(issues)


@dataclass(frozen=True, slots=True)
class EvidencePortfolio:
    evidence: tuple[ParityEvidence, ...] = field(default_factory=tuple)

    def for_case(self, case_id: str) -> tuple[ParityEvidence, ...]:
        """Get all evidence for a given case."""
        return tuple(e for e in self.evidence if e.case_id == case_id)

    def by_type(self, evidence_type: EvidenceType) -> tuple[ParityEvidence, ...]:
        """Get all evidence of a given type."""
        return tuple(e for e in self.evidence if e.evidence_type == evidence_type)

    def coverage(self, suite: CertificationSuite) -> dict[str, bool]:
        """Return {case_id: has_evidence} for all cases in suite."""
        covered = {e.case_id for e in self.evidence}
        return {case.case_id: case.case_id in covered for case in suite.cases}


def _case(
    case_id: str,
    category: ConformanceCategory,
    name: str,
    description: str,
    level: ConformanceLevel = "required",
    input_fixture: str = "",
    expected_output: str = "",
    oracle: str = "round_trip",
) -> ConformanceTestCase:
    return ConformanceTestCase(
        case_id=case_id,
        category=category,
        name=name,
        description=description,
        level=level,
        input_fixture=input_fixture,
        expected_output=expected_output,
        oracle=oracle,
    )


def default_certification_suite() -> CertificationSuite:
    """Return the default GPO Studio certification suite.

    Includes required cases for:
    - registry_pol: round-trip, deterministic serialization, max settings
    - gpp_xml: groups round-trip, registry round-trip, all adapters round-trip,
      unknown preservation
    - security_descriptor: SDDL round-trip, object ACE, inherited ACE, deny precedence
    - wmi_filter: association, WQL lint, multi-query
    - gpo_links: link CRUD, precedence computation, block inheritance
    - backup_restore: manifest round-trip, restore plan generation
    - rsop_computation: simple precedence, security filtering, loopback
    - publication: plan generation, script generation, gate evaluation
    """
    cases: tuple[ConformanceTestCase, ...] = (
        # registry_pol
        _case(
            "reg-roundtrip",
            "registry_pol",
            "Registry.pol round-trip",
            "All registry settings survive serialize->parse->serialize.",
            input_fixture="all_registry_types",
            expected_output="settings_equal",
            oracle="round_trip",
        ),
        _case(
            "reg-deterministic",
            "registry_pol",
            "Registry.pol deterministic serialization",
            "Serializing the same GPO twice yields identical bytes.",
            input_fixture="all_registry_types",
            expected_output="byte_equal",
            oracle="round_trip",
        ),
        _case(
            "reg-max-settings",
            "registry_pol",
            "Registry.pol max settings",
            "Very large registry.pol files are handled within GPMC limits.",
            input_fixture="large_registry_policy",
            expected_output="parses_successfully",
            oracle="structural",
        ),
        # gpp_xml
        _case(
            "gpp-groups-roundtrip",
            "gpp_xml",
            "GPP Groups round-trip",
            "GPP group preferences survive XML serialize->parse->serialize.",
            input_fixture="gpp_groups_all_actions",
            expected_output="groups_equal",
            oracle="round_trip",
        ),
        _case(
            "gpp-registry-roundtrip",
            "gpp_xml",
            "GPP Registry round-trip",
            "GPP registry preferences survive XML serialize->parse->serialize.",
            input_fixture="gpp_registry_all_actions",
            expected_output="registry_equal",
            oracle="round_trip",
        ),
        _case(
            "gpp-adapters-roundtrip",
            "gpp_xml",
            "GPP all adapters round-trip",
            "Every supported GPP adapter survives XML round-trip.",
            input_fixture="comprehensive",
            expected_output="adapters_equal",
            oracle="round_trip",
        ),
        _case(
            "gpp-unknown-preservation",
            "gpp_xml",
            "GPP unknown content preservation",
            "Unsupported GPP XML is preserved verbatim by Studio.",
            input_fixture="unsupported_ilt_nested_collection_xml",
            expected_output="unknown_preserved",
            oracle="matches_windows",
        ),
        # security_descriptor
        _case(
            "sd-sddl-roundtrip",
            "security_descriptor",
            "SDDL round-trip",
            "SDDL strings parse and format back to an equivalent descriptor.",
            input_fixture="security_filter_types",
            expected_output="sddl_equal",
            oracle="round_trip",
        ),
        _case(
            "sd-object-ace",
            "security_descriptor",
            "SDDL object ACE",
            "Object-specific ACEs (OA/OD) round-trip correctly.",
            input_fixture="object_ace_sddl",
            expected_output="ace_equal",
            oracle="round_trip",
        ),
        _case(
            "sd-inherited-ace",
            "security_descriptor",
            "SDDL inherited ACE",
            "Inherited ACEs (ID flag) round-trip correctly.",
            input_fixture="inherited_ace_sddl",
            expected_output="ace_equal",
            oracle="round_trip",
        ),
        _case(
            "sd-deny-precedence",
            "security_descriptor",
            "SDDL deny precedence",
            "Deny ACEs take precedence over allow ACEs in effective access.",
            input_fixture="deny_precedence_sddl",
            expected_output="deny_wins",
            oracle="structural",
        ),
        # wmi_filter
        _case(
            "wmi-association",
            "wmi_filter",
            "WMI filter association",
            "WMI filter is associated with a GPO and survives round-trip.",
            input_fixture="wmi_filter",
            expected_output="association_equal",
            oracle="round_trip",
        ),
        _case(
            "wmi-wql-lint",
            "wmi_filter",
            "WQL lint",
            "WQL queries are syntactically valid and lint cleanly.",
            input_fixture="wmi_filter",
            expected_output="no_lint_issues",
            oracle="structural",
        ),
        _case(
            "wmi-multi-query",
            "wmi_filter",
            "WMI multi-query filter",
            "Filters with multiple WQL queries round-trip correctly.",
            input_fixture="multi_query_wmi_filter",
            expected_output="queries_equal",
            oracle="round_trip",
        ),
        # gpo_links
        _case(
            "link-crud",
            "gpo_links",
            "GPLink CRUD",
            "gPLink values can be created, read, updated, and deleted.",
            input_fixture="link_shapes",
            expected_output="links_equal",
            oracle="round_trip",
        ),
        _case(
            "link-precedence",
            "gpo_links",
            "GPLink precedence computation",
            "Link order and enforced links resolve precedence correctly.",
            input_fixture="link_shapes",
            expected_output="precedence_correct",
            oracle="structural",
        ),
        _case(
            "link-block-inheritance",
            "gpo_links",
            "Block inheritance",
            "Blocked inheritance excludes non-enforced GPOs.",
            input_fixture="link_shapes",
            expected_output="blocked_excluded",
            oracle="structural",
        ),
        # backup_restore
        _case(
            "backup-manifest-roundtrip",
            "backup_restore",
            "Backup manifest round-trip",
            "GPMC backup manifest survives export/import/export.",
            input_fixture="comprehensive",
            expected_output="manifest_equal",
            oracle="round_trip",
        ),
        _case(
            "backup-restore-plan",
            "backup_restore",
            "Restore plan generation",
            "A restore plan can be generated from a backup manifest.",
            input_fixture="comprehensive",
            expected_output="plan_generated",
            oracle="structural",
        ),
        # rsop_computation
        _case(
            "rsop-simple-precedence",
            "rsop_computation",
            "RSOP simple precedence",
            "Later GPOs override earlier GPOs for the same setting.",
            input_fixture="link_shapes",
            expected_output="later_wins",
            oracle="matches_windows",
        ),
        _case(
            "rsop-security-filtering",
            "rsop_computation",
            "RSOP security filtering",
            "GPOs are filtered out when the target lacks apply permission.",
            input_fixture="security_filter_types",
            expected_output="filtered_out",
            oracle="matches_windows",
        ),
        _case(
            "rsop-loopback",
            "rsop_computation",
            "RSOP loopback processing",
            "Loopback merge/replace applies user settings via computer precedence.",
            input_fixture="link_shapes",
            expected_output="loopback_applied",
            oracle="matches_windows",
        ),
        # publication
        _case(
            "pub-plan-generation",
            "publication",
            "Publication plan generation",
            "A complete publication plan is generated for a GPO.",
            input_fixture="comprehensive",
            expected_output="plan_valid",
            oracle="structural",
        ),
        _case(
            "pub-script-generation",
            "publication",
            "PowerShell script generation",
            "A PowerShell publication script is generated and references the plan.",
            input_fixture="comprehensive",
            expected_output="script_valid",
            oracle="structural",
        ),
        _case(
            "pub-gate-evaluation",
            "publication",
            "Identifier gate evaluation",
            "Publication artifacts pass the identifier gate before leaving Studio.",
            input_fixture="comprehensive",
            expected_output="gate_passed",
            oracle="structural",
        ),
    )

    return CertificationSuite(
        suite_id="gpo-studio-default-1",
        name="GPO Studio Default GPMC Parity Certification Suite",
        version="1.0",
        cases=cases,
    )


__all__ = [
    "CertificationLevel",
    "CertificationResult",
    "CertificationSuite",
    "ConformanceCategory",
    "ConformanceLevel",
    "ConformanceResult",
    "ConformanceTestCase",
    "EvidencePortfolio",
    "EvidenceType",
    "ParityEvidence",
    "default_certification_suite",
    "evaluate_certification",
]
