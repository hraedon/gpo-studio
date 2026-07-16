"""Test the identifier gate's fail-closed behavior.

The gate has two complementary checks:
1. Always-on: no tracked file under samples/ (a gitignored data dir).
2. Secret-driven: scan tracked text files for forbidden identifiers.

This test exercises the pure-Python scanning logic (no git dependency)
to verify that configured identifiers are caught and clean files pass.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT_PATH = _PROJECT_ROOT / "scripts" / "check_committed_identifiers.py"

_spec = importlib.util.spec_from_file_location("check_committed_identifiers", _SCRIPT_PATH)
assert _spec is not None
assert _spec.loader is not None
_mod = importlib.util.module_from_spec(_spec)
sys.modules["check_committed_identifiers"] = _mod
_spec.loader.exec_module(_mod)

Violation = _mod.Violation
leaked_tracked_files = _mod.leaked_tracked_files
parse_identifier_set = _mod.parse_identifier_set
scan_text = _mod.scan_text

SCRIPTS_DIR = _PROJECT_ROOT / "scripts"


def test_parse_identifier_set_strips_and_lowercases():
    raw = "WORK-DOMAIN  Another-Host\n# comment\nabc"
    result = parse_identifier_set(raw)
    assert "work-domain" in result
    assert "another-host" in result
    assert "abc" not in result  # below MIN_IDENTIFIER_LENGTH (4)
    assert "# comment" not in result


def test_parse_identifier_set_handles_comments():
    raw = "real-host # this is a comment\nanother-host #inline"
    result = parse_identifier_set(raw)
    assert "real-host" in result
    assert "another-host" in result
    assert "comment" not in result
    assert "inline" not in result


def test_scan_text_finds_substring_match():
    identifiers = frozenset({"forbidden-host"})
    text = "Connect to forbidden-host.example.com for details"
    violations = list(scan_text(text, identifiers))
    assert len(violations) == 1
    assert violations[0].identifier == "forbidden-host"
    assert violations[0].line_number == 1


def test_scan_text_case_insensitive():
    identifiers = frozenset({"work-domain"})
    text = "Server WORK-DOMAIN is up"
    violations = list(scan_text(text, identifiers))
    assert len(violations) == 1


def test_scan_text_multiple_occurrences():
    identifiers = frozenset({"secret-host"})
    text = "secret-host\nmore text\nsecret-host again"
    violations = list(scan_text(text, identifiers))
    assert len(violations) == 2


def test_scan_text_empty_identifiers_no_violations():
    violations = list(scan_text("anything", frozenset()))
    assert violations == []


def test_leaked_tracked_files_detects_samples_root():
    paths = [
        Path("samples/real-data.json"),
        Path("tests/samples/test_fixture.py"),  # nested — should NOT be flagged
        Path("src/gpo_studio/api.py"),
    ]
    leaked = leaked_tracked_files(paths, frozenset({"samples"}))
    assert len(leaked) == 1
    assert leaked[0] == Path("samples/real-data.json")


def test_leaked_tracked_files_allows_nested_samples():
    paths = [
        Path("tests/samples/legit.py"),
        Path("docs/guide.md"),
    ]
    leaked = leaked_tracked_files(paths, frozenset({"samples"}))
    assert leaked == []


def test_clean_source_has_no_violations():
    source_files = [
        SCRIPTS_DIR / "check_committed_identifiers.py",
        SCRIPTS_DIR.parent / "src" / "gpo_studio" / "__init__.py",
        SCRIPTS_DIR.parent / "README.md",
    ]
    identifiers = frozenset({
        "real-domain-controller",
        "production-ad-server",
        "actual-company-name",
    })
    for path in source_files:
        text = path.read_text()
        violations = list(scan_text(text, identifiers))
        assert violations == [], f"Unexpected violation in {path}: {violations}"


def test_gate_is_noop_without_secret():
    result = parse_identifier_set("")
    assert len(result) == 0


def test_violation_dataclass_fields():
    v = Violation(
        identifier="test-host",
        path=Path("test.txt"),
        line_number=42,
        line="connect to test-host",
    )
    assert v.identifier == "test-host"
    assert v.line_number == 42
