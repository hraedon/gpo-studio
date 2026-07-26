from __future__ import annotations

from gpo_studio.certification import (
    CertificationResult,
    CertificationSuite,
    ConformanceCategory,
    ConformanceResult,
    ConformanceTestCase,
    EvidencePortfolio,
    ParityEvidence,
    default_certification_suite,
    evaluate_certification,
)


def _make_case(
    case_id: str = "c1",
    category: ConformanceCategory = "registry_pol",
) -> ConformanceTestCase:
    return ConformanceTestCase(
        case_id=case_id,
        category=category,
        name="Test Case",
        description="A test case.",
        oracle="round_trip",
    )


def _make_result(
    case_id: str = "c1",
    passed: bool = True,
    skipped: bool = False,
) -> ConformanceResult:
    return ConformanceResult(
        case_id=case_id,
        passed=passed,
        skipped=skipped,
    )


# ConformanceTestCase


def test_conformance_test_case_valid() -> None:
    case = _make_case()
    issues = case.validate()
    assert not any(i.severity == "error" for i in issues)


def test_conformance_test_case_empty_id_error() -> None:
    case = ConformanceTestCase(
        case_id="",
        category="registry_pol",
        name="Test",
        description="A test case.",
    )
    issues = case.validate()
    assert any(i.code == "empty_case_id" and i.severity == "error" for i in issues)


def test_conformance_test_case_empty_name_error() -> None:
    case = ConformanceTestCase(
        case_id="c1",
        category="registry_pol",
        name="",
        description="A test case.",
    )
    issues = case.validate()
    assert any(i.code == "empty_name" and i.severity == "error" for i in issues)


def test_conformance_test_case_empty_oracle_warning() -> None:
    case = ConformanceTestCase(
        case_id="c1",
        category="registry_pol",
        name="Test",
        description="A test case.",
        oracle="",
    )
    issues = case.validate()
    assert any(i.code == "empty_oracle" and i.severity == "warning" for i in issues)
    assert not any(i.severity == "error" for i in issues)


# CertificationSuite


def test_certification_suite_valid() -> None:
    suite = CertificationSuite(
        suite_id="s1",
        name="Suite",
        cases=(_make_case(),),
    )
    issues = suite.validate()
    assert not any(i.severity == "error" for i in issues)


def test_certification_suite_empty_cases_error() -> None:
    suite = CertificationSuite(suite_id="s1", name="Suite")
    issues = suite.validate()
    assert any(i.code == "empty_cases" and i.severity == "error" for i in issues)


def test_certification_suite_empty_suite_id_error() -> None:
    suite = CertificationSuite(
        suite_id="",
        name="Suite",
        cases=(_make_case(),),
    )
    issues = suite.validate()
    assert any(i.code == "empty_suite_id" and i.severity == "error" for i in issues)


def test_certification_suite_duplicate_case_id_error() -> None:
    suite = CertificationSuite(
        suite_id="s1",
        name="Suite",
        cases=(_make_case("c1"), _make_case("c1")),
    )
    issues = suite.validate()
    assert any(i.code == "duplicate_case_id" and i.severity == "error" for i in issues)


def test_certification_suite_cases_for_category() -> None:
    suite = CertificationSuite(
        suite_id="s1",
        name="Suite",
        cases=(
            _make_case("c1", "registry_pol"),
            _make_case("c2", "gpp_xml"),
            _make_case("c3", "registry_pol"),
        ),
    )
    assert len(suite.cases_for_category("registry_pol")) == 2
    assert len(suite.cases_for_category("gpp_xml")) == 1
    assert len(suite.cases_for_category("wmi_filter")) == 0


def test_certification_suite_required_cases() -> None:
    suite = CertificationSuite(
        suite_id="s1",
        name="Suite",
        cases=(
            ConformanceTestCase(
                case_id="c1",
                category="registry_pol",
                name="Required",
                description="Required case.",
                level="required",
            ),
            ConformanceTestCase(
                case_id="c2",
                category="gpp_xml",
                name="Recommended",
                description="Recommended case.",
                level="recommended",
            ),
        ),
    )
    required = suite.required_cases()
    assert len(required) == 1
    assert required[0].case_id == "c1"


# evaluate_certification


def test_evaluate_certification_all_pass_full() -> None:
    suite = default_certification_suite()
    results = tuple(
        ConformanceResult(case_id=c.case_id, passed=True)
        for c in suite.cases
    )
    cert = evaluate_certification(suite, results)
    assert cert.certified is True
    assert cert.certification_level == "full"
    assert cert.total == len(suite.cases)
    assert cert.passed == len(suite.cases)
    assert cert.failed == 0


def test_evaluate_certification_some_required_fail_none() -> None:
    suite = default_certification_suite()
    results = []
    for case in suite.cases:
        passed = case.category != "registry_pol"
        results.append(ConformanceResult(case_id=case.case_id, passed=passed))
    cert = evaluate_certification(suite, tuple(results))
    assert cert.certified is False
    assert cert.certification_level == "none"
    assert cert.failed > 0


def test_evaluate_certification_basic_level() -> None:
    suite = CertificationSuite(
        suite_id="basic-suite",
        name="Basic Suite",
        cases=(
            ConformanceTestCase(
                case_id="c1",
                category="registry_pol",
                name="Registry Round-trip",
                description="Round-trip.",
                level="required",
            ),
            ConformanceTestCase(
                case_id="c2",
                category="gpp_xml",
                name="GPP Round-trip",
                description="Round-trip.",
                level="required",
            ),
        ),
    )
    results = (
        ConformanceResult(case_id="c1", passed=True),
        ConformanceResult(case_id="c2", passed=True),
    )
    cert = evaluate_certification(suite, results)
    assert cert.certified is True
    assert cert.certification_level == "basic"


def test_evaluate_certification_standard_level() -> None:
    suite = CertificationSuite(
        suite_id="standard-suite",
        name="Standard Suite",
        cases=(
            ConformanceTestCase(
                case_id="c1",
                category="registry_pol",
                name="Registry Round-trip",
                description="Round-trip.",
                level="required",
            ),
            ConformanceTestCase(
                case_id="c2",
                category="gpp_xml",
                name="GPP Round-trip",
                description="Round-trip.",
                level="required",
            ),
            ConformanceTestCase(
                case_id="c3",
                category="security_descriptor",
                name="SDDL Round-trip",
                description="Round-trip.",
                level="required",
            ),
            ConformanceTestCase(
                case_id="c4",
                category="wmi_filter",
                name="WMI Association",
                description="Association.",
                level="required",
            ),
        ),
    )
    results = tuple(
        ConformanceResult(case_id=c.case_id, passed=True)
        for c in suite.cases
    )
    cert = evaluate_certification(suite, results)
    assert cert.certified is True
    assert cert.certification_level == "standard"


# CertificationResult


def test_certification_result_failed_cases() -> None:
    result = CertificationResult(
        suite_id="s1",
        suite_name="Suite",
        results=(
            ConformanceResult(case_id="c1", passed=True),
            ConformanceResult(case_id="c2", passed=False),
            ConformanceResult(case_id="c3", passed=False, skipped=True),
        ),
        total=3,
        passed=1,
        failed=2,
        skipped=1,
        certified=False,
    )
    failed = result.failed_cases()
    assert len(failed) == 2
    assert {r.case_id for r in failed} == {"c2", "c3"}


def test_certification_result_passed_cases() -> None:
    result = CertificationResult(
        suite_id="s1",
        suite_name="Suite",
        results=(
            ConformanceResult(case_id="c1", passed=True),
            ConformanceResult(case_id="c2", passed=False),
            ConformanceResult(case_id="c3", passed=True),
        ),
        total=3,
        passed=2,
        failed=1,
        skipped=0,
        certified=False,
    )
    passed = result.passed_cases()
    assert len(passed) == 2
    assert {r.case_id for r in passed} == {"c1", "c3"}


def test_certification_result_summary() -> None:
    result = CertificationResult(
        suite_id="s1",
        suite_name="Suite",
        results=(
            ConformanceResult(case_id="c1", passed=True),
            ConformanceResult(case_id="c2", passed=False),
        ),
        total=2,
        passed=1,
        failed=1,
        skipped=0,
        certified=False,
        certification_level="none",
        computed_at="2026-01-01T00:00:00Z",
    )
    summary = result.summary()
    assert "NOT CERTIFIED" in summary
    assert "Level: none" in summary
    assert "Total: 2" in summary
    assert "Computed at" in summary


# ParityEvidence


def test_parity_evidence_valid() -> None:
    evidence = ParityEvidence(
        evidence_id="e1",
        case_id="c1",
        evidence_type="round_trip",
        description="Evidence.",
        artifact_hash="abc123",
    )
    issues = evidence.validate()
    assert not any(i.severity == "error" for i in issues)


def test_parity_evidence_empty_description_error() -> None:
    evidence = ParityEvidence(
        evidence_id="e1",
        case_id="c1",
        evidence_type="round_trip",
        description="",
    )
    issues = evidence.validate()
    assert any(i.code == "empty_description" and i.severity == "error" for i in issues)


def test_parity_evidence_empty_artifact_warning() -> None:
    evidence = ParityEvidence(
        evidence_id="e1",
        case_id="c1",
        evidence_type="round_trip",
        description="Evidence.",
    )
    issues = evidence.validate()
    assert any(i.code == "empty_artifact_hash" and i.severity == "warning" for i in issues)
    assert not any(i.severity == "error" for i in issues)


def test_parity_evidence_empty_evidence_id_error() -> None:
    evidence = ParityEvidence(
        evidence_id="",
        case_id="c1",
        evidence_type="round_trip",
        description="Evidence.",
    )
    issues = evidence.validate()
    assert any(i.code == "empty_evidence_id" and i.severity == "error" for i in issues)


def test_parity_evidence_empty_case_id_error() -> None:
    evidence = ParityEvidence(
        evidence_id="e1",
        case_id="",
        evidence_type="round_trip",
        description="Evidence.",
    )
    issues = evidence.validate()
    assert any(i.code == "empty_case_id" and i.severity == "error" for i in issues)


# EvidencePortfolio


def test_evidence_portfolio_for_case() -> None:
    portfolio = EvidencePortfolio(
        evidence=(
            ParityEvidence("e1", "c1", "round_trip", "Evidence 1."),
            ParityEvidence("e2", "c2", "windows_oracle", "Evidence 2."),
            ParityEvidence("e3", "c1", "gpmc_edit", "Evidence 3."),
        )
    )
    assert len(portfolio.for_case("c1")) == 2
    assert len(portfolio.for_case("c2")) == 1
    assert len(portfolio.for_case("c3")) == 0


def test_evidence_portfolio_by_type() -> None:
    portfolio = EvidencePortfolio(
        evidence=(
            ParityEvidence("e1", "c1", "round_trip", "Evidence 1."),
            ParityEvidence("e2", "c2", "round_trip", "Evidence 2."),
            ParityEvidence("e3", "c3", "windows_oracle", "Evidence 3."),
        )
    )
    assert len(portfolio.by_type("round_trip")) == 2
    assert len(portfolio.by_type("windows_oracle")) == 1
    assert len(portfolio.by_type("client_apply")) == 0


def test_evidence_portfolio_coverage() -> None:
    suite = CertificationSuite(
        suite_id="s1",
        name="Suite",
        cases=(_make_case("c1"), _make_case("c2")),
    )
    portfolio = EvidencePortfolio(
        evidence=(
            ParityEvidence("e1", "c1", "round_trip", "Evidence 1."),
        )
    )
    coverage = portfolio.coverage(suite)
    assert coverage == {"c1": True, "c2": False}


# default_certification_suite


def test_default_certification_suite_has_cases_in_all_categories() -> None:
    suite = default_certification_suite()
    categories = {c.category for c in suite.cases}
    expected: set[ConformanceCategory] = {
        "registry_pol",
        "gpp_xml",
        "security_descriptor",
        "wmi_filter",
        "gpo_links",
        "backup_restore",
        "rsop_computation",
        "publication",
    }
    assert categories == expected


def test_default_certification_suite_all_required_cases_have_oracle() -> None:
    suite = default_certification_suite()
    for case in suite.required_cases():
        assert case.oracle, f"Required case {case.case_id!r} has no oracle"


# Integration


def test_integration_default_suite_evaluates_to_full_when_all_pass() -> None:
    suite = default_certification_suite()
    results = tuple(
        ConformanceResult(case_id=c.case_id, passed=True)
        for c in suite.cases
    )
    cert = evaluate_certification(suite, results)
    assert cert.certified is True
    assert cert.certification_level == "full"
    assert cert.failed == 0
    assert cert.passed == len(suite.cases)
    assert "CERTIFIED" in cert.summary()


def test_integration_missing_result_counts_as_failed() -> None:
    suite = default_certification_suite()
    # Drop one result.
    results = tuple(
        ConformanceResult(case_id=c.case_id, passed=True)
        for c in suite.cases[:-1]
    )
    cert = evaluate_certification(suite, results)
    assert cert.certified is False
    assert cert.certification_level == "none"
    assert cert.failed >= 1
