"""Tests for the evidence-pack schema and public matrix derivation (Plan 021 WP-4)."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from gpo_studio.evidence import (
    PackError,
    canonical_pack_bytes,
    canonical_pack_hash,
    derive_claims,
    load_pack,
    parse_pack,
    render_matrix_markdown,
    signability_report,
)

RECORD_REGISTRY = {
    "capability": "registry-policy",
    "cse_guid": "{35378EAC-683F-11D2-A89A-00C04FBBCFA2}",
    "side": "both",
    "action": "set",
    "outcome": "pass",
    "classification": "verified-rw",
    "evidence_kind": "windows-side",
    "tool": "Set-GPRegistryValue",
    "ms_doc": "https://learn.microsoft.com/windows/win32/api/_grouppolicy/",
    "evidence_hash": "sha256:abc",
    "notes": "",
}

RECORD_CPASSWORD = {
    "capability": "cpassword",
    "cse_guid": None,
    "side": "both",
    "action": "import",
    "outcome": "pass",
    "classification": "intentional-deny",
    "evidence_kind": "windows-side",
    "tool": "structural-detection",
    "ms_doc": "https://learn.microsoft.com/openspecs/windows_protocols/ms-gppref",
    "evidence_hash": "sha256:denied",
    "notes": "cpassword is permanently refused",
}


def _pack(records: list[dict], *, redaction: bool = True, licensing: bool = True) -> dict:
    return {
        "schema_version": 1,
        "pack_id": "test-pack",
        "generated_at": "2026-07-18T00:00:00Z",
        "source_commit": "e5e4c90",
        "operator": "gpstudio-lab",
        "redaction_verified": redaction,
        "licensing_complete": licensing,
        "estate": {
            "os": "Windows Server 2025",
            "build": "26100",
            "role": "DC",
            "forest": "ad.hraedon.com",
            "domain": "ad.hraedon.com",
            "dc": "mvmdc03",
            "gpmc_version": "10.0.26100",
        },
        "records": [copy.deepcopy(r) for r in records],
        "content": [
            {
                "content_id": "admx-set",
                "classification": "hash-reference",
                "sha256": "sha256:deadbeef",
                "source_build": "26100",
                "regeneration_path": "%SystemRoot%\\PolicyDefinitions",
                "license_note": "Microsoft-copyrighted; referenced, not redistributed",
            }
        ],
    }


def _record(
    capability: str,
    *,
    outcome: str = "pass",
    kind: str = "windows-side",
    classification: str = "verified-rw",
) -> dict:
    base = copy.deepcopy(RECORD_REGISTRY)
    base["capability"] = capability
    base["outcome"] = outcome
    base["evidence_kind"] = kind
    base["classification"] = classification
    base["evidence_hash"] = f"sha256:{capability}-{kind}-{outcome}"
    return base


# --- parsing / validation -------------------------------------------------


def test_parse_pack_valid() -> None:
    pack = parse_pack(_pack([RECORD_REGISTRY]))
    assert pack.pack_id == "test-pack"
    assert pack.signable is True
    assert len(pack.records) == 1
    assert pack.records[0].capability == "registry-policy"


def test_parse_pack_rejects_wrong_schema_version() -> None:
    raw = _pack([RECORD_REGISTRY])
    raw["schema_version"] = 0
    with pytest.raises(PackError, match="schema_version"):
        parse_pack(raw)


def test_parse_pack_rejects_invalid_outcome() -> None:
    raw = _pack([_record("x", outcome="bogus")])
    with pytest.raises(PackError, match="outcome"):
        parse_pack(raw)


def test_parse_pack_rejects_invalid_classification() -> None:
    raw = _pack([_record("x", classification="super-verified")])
    with pytest.raises(PackError, match="classification"):
        parse_pack(raw)


def test_parse_pack_rejects_invalid_evidence_kind() -> None:
    raw = _pack([_record("x", kind="linux-side")])
    with pytest.raises(PackError, match="evidence_kind"):
        parse_pack(raw)


def test_parse_pack_rejects_missing_evidence_hash() -> None:
    raw = _pack([RECORD_REGISTRY])
    raw["records"][0]["evidence_hash"] = ""
    with pytest.raises(PackError, match="evidence_hash"):
        parse_pack(raw)


def test_parse_pack_rejects_hash_reference_without_sha256() -> None:
    raw = _pack([RECORD_REGISTRY])
    raw["content"][0]["sha256"] = None
    with pytest.raises(PackError, match="sha256"):
        parse_pack(raw)


def test_parse_pack_rejects_invalid_content_classification() -> None:
    raw = _pack([RECORD_REGISTRY])
    raw["content"][0]["classification"] = "redistribute-freely"
    with pytest.raises(PackError, match="invalid classification"):
        parse_pack(raw)


def test_parse_pack_rejects_invalid_estate_role() -> None:
    raw = _pack([RECORD_REGISTRY])
    raw["estate"]["role"] = "workstation"
    with pytest.raises(PackError, match="estate role"):
        parse_pack(raw)


def test_load_pack_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "pack.json"
    path.write_text(json.dumps(_pack([RECORD_REGISTRY])), encoding="utf-8")
    pack = load_pack(path)
    assert pack.pack_id == "test-pack"


def test_load_pack_rejects_bad_json(tmp_path: Path) -> None:
    path = tmp_path / "pack.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(PackError, match="invalid JSON"):
        load_pack(path)


# --- signability ----------------------------------------------------------


def test_signable_pack_has_empty_report() -> None:
    pack = parse_pack(_pack([RECORD_REGISTRY]))
    assert signability_report(pack) == []
    assert pack.signable is True


def test_unsigned_pack_reports_reasons() -> None:
    pack = parse_pack(_pack([RECORD_REGISTRY], redaction=False, licensing=False))
    reasons = signability_report(pack)
    assert "redaction_verified is false" in reasons
    assert "licensing_complete is false" in reasons
    assert pack.signable is False


# --- canonical hashing ----------------------------------------------------


def test_canonical_hash_is_deterministic() -> None:
    # Two JSON inputs with identical content parse to equal EvidencePacks and
    # thus identical canonical hashes, regardless of textual key order.
    pack_a = parse_pack(_pack([RECORD_REGISTRY]))
    pack_b = parse_pack(_pack([RECORD_REGISTRY]))
    assert canonical_pack_hash(pack_a) == canonical_pack_hash(pack_b)


def test_canonical_bytes_use_sorted_keys() -> None:
    pack = parse_pack(_pack([RECORD_REGISTRY, RECORD_CPASSWORD]))
    text = canonical_pack_bytes(pack).decode("utf-8")
    top_keys = list(json.loads(text).keys())
    assert top_keys == sorted(top_keys)


def test_canonical_hash_changes_on_field_change() -> None:
    pack_a = parse_pack(_pack([RECORD_REGISTRY]))
    raw = _pack([RECORD_REGISTRY])
    raw["pack_id"] = "different-pack"
    pack_b = parse_pack(raw)
    assert canonical_pack_hash(pack_a) != canonical_pack_hash(pack_b)


def test_canonical_bytes_are_compact_and_stable() -> None:
    pack = parse_pack(_pack([RECORD_REGISTRY]))
    text = canonical_pack_bytes(pack).decode("utf-8")
    # Re-serializing the parsed structure with the same canonical settings must
    # be byte-identical: no structural whitespace, sorted keys, stable order.
    reserialized = json.dumps(
        json.loads(text), sort_keys=True, separators=(",", ":")
    )
    assert text == reserialized


# --- claim derivation -----------------------------------------------------


def test_windows_only_passing_yields_verified_ro() -> None:
    pack = parse_pack(_pack([_record("registry-policy", kind="windows-side")]))
    result = derive_claims([pack])
    assert result.derived_from_signable_packs
    assert len(result.claims) == 1
    assert result.claims[0].classification == "verified-ro"
    assert result.claims[0].has_windows is True
    assert result.claims[0].has_endpoint is False


def test_windows_and_endpoint_yield_verified_rw() -> None:
    pack = parse_pack(
        _pack(
            [
                _record("registry-policy", kind="windows-side"),
                _record("registry-policy", kind="endpoint"),
            ]
        )
    )
    result = derive_claims([pack])
    assert result.claims[0].classification == "verified-rw"
    assert result.claims[0].has_windows is True
    assert result.claims[0].has_endpoint is True


def test_endpoint_only_yields_unknown() -> None:
    pack = parse_pack(_pack([_record("registry-policy", kind="endpoint")]))
    result = derive_claims([pack])
    assert result.claims[0].classification == "unknown"


def test_non_passing_outcome_produces_no_claim() -> None:
    pack = parse_pack(_pack([_record("registry-policy", outcome="fail")]))
    result = derive_claims([pack])
    assert result.claims == ()


def test_policy_classification_taken_as_is() -> None:
    pack = parse_pack(_pack([RECORD_CPASSWORD]))
    result = derive_claims([pack])
    assert result.claims[0].classification == "intentional-deny"


def test_policy_classification_wins_over_evidence() -> None:
    # cpassword is intentional-deny even if both evidence kinds "pass".
    rec = copy.deepcopy(RECORD_CPASSWORD)
    rec_windows = {**rec, "evidence_kind": "windows-side", "evidence_hash": "sha256:1"}
    rec_endpoint = {**rec, "evidence_kind": "endpoint", "evidence_hash": "sha256:2"}
    pack = parse_pack(_pack([rec_windows, rec_endpoint]))
    result = derive_claims([pack])
    assert result.claims[0].classification == "intentional-deny"


def test_expected_failure_collected_separately() -> None:
    pack = parse_pack(
        _pack(
            [
                _record("gpo-links", outcome="expected_failure", classification="unknown"),
                _record("registry-policy"),
            ]
        )
    )
    result = derive_claims([pack])
    capabilities = {c.capability for c in result.claims}
    assert "gpo-links" not in capabilities
    assert "registry-policy" in capabilities
    assert len(result.expected_failures) == 1
    assert result.expected_failures[0].capability == "gpo-links"


def test_unsigned_pack_excluded_by_default() -> None:
    pack = parse_pack(_pack([_record("registry-policy")], redaction=False))
    result = derive_claims([pack])
    assert result.claims == ()
    assert result.unsigned_pack_ids == ("test-pack",)
    assert result.derived_from_signable_packs is False


def test_allow_unsigned_admits_unsigned_pack() -> None:
    pack = parse_pack(_pack([_record("registry-policy")], redaction=False))
    result = derive_claims([pack], allow_unsigned=True)
    assert len(result.claims) == 1
    assert result.unsigned_pack_ids == ()


def test_cross_pack_evidence_aggregates() -> None:
    # Windows-side evidence in one pack, endpoint evidence in another, same estate.
    pack_windows = parse_pack(
        _pack([_record("registry-policy", kind="windows-side")])
    )
    endpoint_pack = _pack([_record("registry-policy", kind="endpoint")])
    endpoint_pack["pack_id"] = "endpoint-pack"
    pack_endpoint = parse_pack(endpoint_pack)
    result = derive_claims([pack_windows, pack_endpoint])
    assert result.claims[0].classification == "verified-rw"
    assert len(result.claims[0].source_packs) == 2


def test_distinct_estates_do_not_aggregate() -> None:
    pack_windows = parse_pack(
        _pack([_record("registry-policy", kind="windows-side")])
    )
    endpoint_raw = _pack([_record("registry-policy", kind="endpoint")])
    endpoint_raw["estate"]["os"] = "Windows Server 2022"
    endpoint_raw["estate"]["build"] = "20348"
    pack_endpoint = parse_pack(endpoint_raw)
    result = derive_claims([pack_windows, pack_endpoint])
    # Two separate claims, each verified-ro / unknown — not a cross-estate verified-rw.
    assert len(result.claims) == 2
    assert all(c.classification != "verified-rw" for c in result.claims)


# --- rendering ------------------------------------------------------------


def test_render_includes_draft_stamp_for_unsigned() -> None:
    pack = parse_pack(_pack([_record("registry-policy")], redaction=False))
    result = derive_claims([pack], allow_unsigned=True)
    out = render_matrix_markdown(result)
    assert "DRAFT" in out
    assert "UNSIGNED PACKS" in out


def test_render_no_draft_stamp_when_all_signable() -> None:
    pack = parse_pack(_pack([_record("registry-policy")]))
    result = derive_claims([pack])
    out = render_matrix_markdown(result)
    assert "DRAFT" not in out


def test_render_verified_rw_row_shows_both_evidence_flags() -> None:
    pack = parse_pack(
        _pack(
            [
                _record("registry-policy", kind="windows-side"),
                _record("registry-policy", kind="endpoint"),
            ]
        )
    )
    out = render_matrix_markdown(derive_claims([pack]))
    assert "verified-rw" in out
    assert "yes | yes" in out


def test_render_empty_matrix_when_no_passing_evidence() -> None:
    pack = parse_pack(_pack([_record("x", outcome="fail")]))
    out = render_matrix_markdown(derive_claims([pack]))
    assert "no passing evidence" in out


# --- per-classification content enforcement --------------------------------


def _content_pack(content: list[dict]) -> dict:
    raw = _pack([RECORD_REGISTRY])
    raw["content"] = content
    return raw


def test_hash_reference_requires_source_build() -> None:
    raw = _content_pack(
        [
            {
                "content_id": "admx",
                "classification": "hash-reference",
                "sha256": "sha256:deadbeef",
                "regeneration_path": "%SystemRoot%\\PolicyDefinitions",
                "license_note": "Microsoft-copyrighted",
            }
        ]
    )
    with pytest.raises(PackError, match="source_build"):
        parse_pack(raw)


def test_in_repo_requires_sha256_and_license_note() -> None:
    raw = _content_pack(
        [{"content_id": "synthetic", "classification": "in-repo", "license_note": "authored"}]
    )
    with pytest.raises(PackError, match="sha256"):
        parse_pack(raw)


def test_excluded_requires_license_note() -> None:
    raw = _content_pack(
        [{"content_id": "vendor", "classification": "excluded", "sha256": "sha256:x"}]
    )
    with pytest.raises(PackError, match="license_note"):
        parse_pack(raw)


def test_in_repo_with_sha256_and_license_note_accepted() -> None:
    raw = _content_pack(
        [
            {
                "content_id": "synthetic",
                "classification": "in-repo",
                "sha256": "sha256:syn",
                "license_note": "authored in-repo; MIT-licensed",
            }
        ]
    )
    assert parse_pack(raw).signable is True


# --- additional derivation edge cases --------------------------------------


def test_skip_outcome_produces_no_claim() -> None:
    pack = parse_pack(_pack([_record("registry-policy", outcome="skip")]))
    assert derive_claims([pack]).claims == ()


def test_not_present_on_target_policy_taken_as_is() -> None:
    pack = parse_pack(
        _pack(
            [
                _record(
                    "ie-branding",
                    outcome="pass",
                    classification="not-present-on-target",
                )
            ]
        )
    )
    result = derive_claims([pack])
    assert result.claims[0].classification == "not-present-on-target"


def test_repeated_records_deduplicate_tools_and_docs() -> None:
    pack = parse_pack(
        _pack(
            [
                _record("registry-policy", kind="windows-side"),
                _record("registry-policy", kind="windows-side"),
                _record("registry-policy", kind="endpoint"),
            ]
        )
    )
    claim = derive_claims([pack]).claims[0]
    assert claim.classification == "verified-rw"
    # Same tool/doc repeated three times collapses to one entry each.
    assert claim.tools == ("Set-GPRegistryValue",)
    assert claim.ms_docs == (
        "https://learn.microsoft.com/windows/win32/api/_grouppolicy/",
    )


def test_invalid_side_type_raises_packerror() -> None:
    raw = _pack([RECORD_REGISTRY])
    raw["records"][0]["side"] = 5
    with pytest.raises(PackError, match="side"):
        parse_pack(raw)


def test_licensing_complete_with_empty_content_accepted() -> None:
    raw = _pack([RECORD_REGISTRY])
    raw["content"] = []
    assert parse_pack(raw).signable is True


# --- CLI smoke -------------------------------------------------------------


def _load_cli():
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(
        "generate_public_matrix",
        _PROJECT_ROOT / "scripts" / "generate_public_matrix.py",
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["generate_public_matrix"] = mod
    spec.loader.exec_module(mod)
    return mod


_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_cli_hash_and_verify(tmp_path, capsys) -> None:
    cli = _load_cli()
    path = tmp_path / "pack.json"
    path.write_text(json.dumps(_pack([RECORD_REGISTRY])), encoding="utf-8")
    assert cli.main(["--pack", str(path), "--hash"]) == 0
    captured = capsys.readouterr()
    digest = captured.out.strip()
    assert len(digest) == 64
    # Verify against the correct hash.
    assert cli.main(["--pack", str(path), "--verify", digest]) == 0
    # Mismatch exits non-zero.
    assert cli.main(["--pack", str(path), "--verify", "0" * 64]) == 1


def test_cli_matrix_refuses_unsigned_pack(tmp_path, capsys) -> None:
    cli = _load_cli()
    path = tmp_path / "pack.json"
    path.write_text(
        json.dumps(_pack([_record("registry-policy")], redaction=False)),
        encoding="utf-8",
    )
    rc = cli.main(["--pack", str(path), "--matrix"])
    assert rc == 1
    assert "refusing" in capsys.readouterr().err.lower()


def test_cli_matrix_allow_unsigned_emits_draft(tmp_path, capsys) -> None:
    cli = _load_cli()
    path = tmp_path / "pack.json"
    path.write_text(
        json.dumps(_pack([_record("registry-policy")], redaction=False)),
        encoding="utf-8",
    )
    assert cli.main(["--pack", str(path), "--matrix", "--allow-unsigned"]) == 0
    assert "DRAFT" in capsys.readouterr().out


def test_cli_check_reports_unsigned(tmp_path, capsys) -> None:
    cli = _load_cli()
    path = tmp_path / "pack.json"
    path.write_text(
        json.dumps(_pack([_record("registry-policy")], licensing=False)),
        encoding="utf-8",
    )
    assert cli.main(["--pack", str(path), "--check"]) == 1
    err = capsys.readouterr().out
    assert "UNSIGNED" in err
    assert "licensing_complete is false" in err


# --- additional error-path and render coverage ----------------------------


def test_parse_pack_rejects_non_string_cse_guid() -> None:
    raw = _pack([RECORD_REGISTRY])
    raw["records"][0]["cse_guid"] = 5
    with pytest.raises(PackError, match="cse_guid"):
        parse_pack(raw)


def test_parse_pack_rejects_content_not_a_list() -> None:
    raw = _pack([RECORD_REGISTRY])
    raw["content"] = "not-a-list"
    with pytest.raises(PackError, match="content must be a list"):
        parse_pack(raw)


def test_parse_pack_rejects_estate_not_an_object() -> None:
    raw = _pack([RECORD_REGISTRY])
    raw["estate"] = "not-an-object"
    with pytest.raises(PackError, match="estate must be a JSON object"):
        parse_pack(raw)


def test_parse_pack_rejects_records_not_a_list() -> None:
    raw = _pack([RECORD_REGISTRY])
    raw["records"] = "not-a-list"
    with pytest.raises(PackError, match="records must be a list"):
        parse_pack(raw)


def test_render_includes_expected_failures_section() -> None:
    pack = parse_pack(
        _pack(
            [
                _record("registry-policy", kind="windows-side"),
                _record("registry-policy", kind="endpoint"),
                _record("gpo-links", outcome="expected_failure", classification="unknown"),
            ]
        )
    )
    out = render_matrix_markdown(derive_claims([pack]))
    assert "Expected failures" in out
    assert "gpo-links" in out
    assert "synthetic" in out.lower() or "gpo-links" in out


def test_render_writes_to_stream() -> None:
    import io

    pack = parse_pack(_pack([_record("registry-policy")]))
    buf = io.StringIO()
    render_matrix_markdown(derive_claims([pack]), stream=buf)
    assert "public capability matrix" in buf.getvalue()


def test_render_escapes_pipe_in_capability() -> None:
    rec = _record("weird|capability", kind="windows-side")
    pack = parse_pack(_pack([rec]))
    out = render_matrix_markdown(derive_claims([pack]))
    assert "weird\\|capability" in out


# --- doc/code contract ----------------------------------------------------
#
# The evidence-pack schema is documented in
# docs/plan-021/reference-estates-and-evidence.md, and the doc carries a
# fenced ``json`` example pack. The doc and the loader were authored
# separately, so they can silently drift (the 2026-07-18 review found the
# doc's example did not parse against the loader). These tests pin the doc's
# examples to the loader mechanically, so any future drift fails CI.

_WP4_DOC = (
    Path(__file__).resolve().parent.parent
    / "docs"
    / "plan-021"
    / "reference-estates-and-evidence.md"
)


def _fenced_json_blocks(markdown: str) -> list[str]:
    """Return the bodies of every ```json fenced block in *markdown*."""
    blocks: list[str] = []
    lines = markdown.splitlines()
    inside = False
    current: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not inside and stripped == "```json":
            inside = True
            current = []
            continue
        if inside and stripped == "```":
            inside = False
            blocks.append("\n".join(current))
            continue
        if inside:
            current.append(line)
    return blocks


def test_wp4_doc_has_a_json_example() -> None:
    assert _WP4_DOC.is_file(), f"WP-4 reference doc missing: {_WP4_DOC}"
    blocks = _fenced_json_blocks(_WP4_DOC.read_text(encoding="utf-8"))
    assert blocks, "WP-4 doc must carry at least one ```json example pack"


def test_wp4_doc_example_packs_parse_against_loader() -> None:
    """Every ```json example in the WP-4 doc must parse via the loader.

    This is the doc/code contract: the schema the doc teaches operators to
    author must be exactly the schema the loader accepts.
    """
    blocks = _fenced_json_blocks(_WP4_DOC.read_text(encoding="utf-8"))
    for i, block in enumerate(blocks):
        try:
            raw = json.loads(block)
        except json.JSONDecodeError as exc:  # pragma: no cover - failure detail
            raise AssertionError(f"WP-4 doc json block #{i} is not valid JSON: {exc}") from exc
        pack = parse_pack(raw, source=f"{_WP4_DOC.name}#json[{i}]")
        # The documented example must be signable and derive without raising —
        # a reviewer following the doc should get a usable pack, not a subset
        # the loader rejects.
        assert pack.signable, f"WP-4 doc json block #{i} example is not signable"
        derive_claims([pack])
