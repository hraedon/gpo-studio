"""Test the identifier gate's fail-closed behavior.

The gate has two complementary checks:
1. Always-on: no tracked file under samples/ (a gitignored data dir).
2. Secret-driven: scan tracked text files for forbidden identifiers.

This test exercises the pure-Python scanning logic (no git dependency)
to verify that configured identifiers are caught and clean files pass, and
the --strict mode that makes CI fail closed when the denylist is missing.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

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


def test_resolve_identifiers_strict_returns_empty_frozenset_when_unset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """--strict fails closed: a missing denylist is an empty frozenset, not None.

    CI passes --strict so a misconfigured secret cannot silently disable the
    hard gate. The caller treats an empty frozenset as "refuse", None as "no-op".
    """
    monkeypatch.delenv("GPO_STUDIO_FORBIDDEN_IDENTIFIERS", raising=False)
    monkeypatch.setattr(_mod, "_DENYLIST_FILE_CANDIDATES", ())
    result = _mod._resolve_identifiers(strict=True)
    assert result is not None
    assert result == frozenset()


def test_resolve_identifiers_non_strict_returns_none_when_unset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Default mode fail-opens: a missing denylist is None, so the caller no-ops.

    A fresh clone or fork without the secret must not be bricked.
    """
    monkeypatch.delenv("GPO_STUDIO_FORBIDDEN_IDENTIFIERS", raising=False)
    monkeypatch.setattr(_mod, "_DENYLIST_FILE_CANDIDATES", ())
    result = _mod._resolve_identifiers(strict=False)
    assert result is None


def test_resolve_identifiers_reads_denylist_file_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The script resolves the denylist from a file when the env var is unset.

    Mirrors githooks/pre-commit so direct invocation (CI, ad-hoc) finds the
    same denylist the hook does.
    """
    denylist = tmp_path / "denylist"
    denylist.write_text("forbidden-host another-host\n# comment\n")
    monkeypatch.delenv("GPO_STUDIO_FORBIDDEN_IDENTIFIERS", raising=False)
    monkeypatch.setattr(_mod, "_DENYLIST_FILE_CANDIDATES", (denylist,))
    result = _mod._resolve_identifiers(strict=False)
    assert result is not None
    assert "forbidden-host" in result
    assert "another-host" in result


def test_resolve_identifiers_env_var_wins_over_file_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The env var takes precedence over the file fallback."""
    denylist = tmp_path / "denylist"
    denylist.write_text("file-only-host")
    monkeypatch.setenv("GPO_STUDIO_FORBIDDEN_IDENTIFIERS", "env-host")
    monkeypatch.setattr(_mod, "_DENYLIST_FILE_CANDIDATES", (denylist,))
    result = _mod._resolve_identifiers(strict=False)
    assert result is not None
    assert "env-host" in result
    assert "file-only-host" not in result


def test_main_strict_exits_nonzero_without_denylist(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """CI mode: --strict with no denylist fails the run rather than no-op'ing."""
    monkeypatch.delenv("GPO_STUDIO_FORBIDDEN_IDENTIFIERS", raising=False)
    monkeypatch.setattr(_mod, "_DENYLIST_FILE_CANDIDATES", ())
    monkeypatch.setattr(_mod, "collect_tracked_paths", lambda: [])
    rc = _mod.main(["--strict"])
    assert rc == 1


def test_main_non_strict_exits_zero_without_denylist(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Default mode: no denylist no-ops (exit 0), so a fresh clone is not bricked."""
    monkeypatch.delenv("GPO_STUDIO_FORBIDDEN_IDENTIFIERS", raising=False)
    monkeypatch.setattr(_mod, "_DENYLIST_FILE_CANDIDATES", ())
    monkeypatch.setattr(_mod, "collect_tracked_paths", lambda: [])
    rc = _mod.main([])
    assert rc == 0
