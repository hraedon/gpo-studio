"""Candidate export is the bound export path; captured readback is independent."""

from __future__ import annotations

import hashlib
import io
import json
import runpy
import struct
import subprocess
import sys
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

from gpo_studio.firewall_policy import FirewallProfileSettings
from gpo_studio.registry_pol import parse

ROOT = Path(__file__).resolve().parents[1]
BUILDER_PATH = ROOT / "scripts/plan-033/build-firewall-candidate.py"
BUILDER = runpy.run_path(str(BUILDER_PATH))


def record_size_offset(raw: bytes, start: int) -> int:
    offset = start + 2  # '['
    for _ in range(2):  # key/name UTF-16 NUL and ';'
        while raw[offset : offset + 2] != b"\0\0":
            assert offset < len(raw)
            offset += 2
        offset += 4
    return offset + 6  # registry type DWORD and ';'


def raw_record_slices(raw: bytes) -> list[bytes]:
    """Delimit records using native size DWORDs; never serialize expectations."""
    assert raw[:8] == b"PReg\x01\0\0\0"
    chunks = []
    offset = 8
    while offset < len(raw):
        size_offset = record_size_offset(raw, offset)
        end = size_offset + 6 + struct.unpack_from("<I", raw, size_offset)[0] + 2
        assert raw[offset : offset + 2] == b"[\0"
        assert raw[end - 2 : end] == b"]\0"
        chunks.append(raw[offset:end])
        offset = end
    assert offset == len(raw)
    return chunks


def test_candidate_is_deterministic_and_every_output_is_hash_bound(tmp_path: Path) -> None:
    payloads = []
    for name in ("one", "two"):
        out = tmp_path / name
        completed = subprocess.run(
            [sys.executable, str(BUILDER_PATH), str(out)],
            check=True,
            capture_output=True,
            text=True,
        )
        files = {p.name: p.read_bytes() for p in out.iterdir()}
        assert set(files) == set(BUILDER["REQUIRED_CANDIDATE_FILES"])
        for filename, data in files.items():
            assert f"{filename} sha256={hashlib.sha256(data).hexdigest()}" in completed.stdout
        payloads.append(files)
    assert payloads[0] == payloads[1]


def test_backup_carries_only_measured_machine_records(tmp_path: Path) -> None:
    policy = BUILDER["candidate_policy"]()
    assert len(policy.rules) == 13
    assert all(r.rule_id.startswith("StudioFwLane") for r in policy.rules)
    assert all(r.name.startswith("StudioFwLane") for r in policy.rules)
    assert policy.public == FirewallProfileSettings()
    BUILDER["build"](tmp_path, policy)
    with zipfile.ZipFile(io.BytesIO((tmp_path / BUILDER["ARCHIVE_NAME"]).read_bytes())) as bundle:
        paths = [p for p in bundle.namelist() if p.lower().endswith("registry.pol")]
        assert len(paths) == 1 and "/Machine/" in paths[0]
        raw = bundle.read(paths[0])
    # Compare archive bytes with native slices, independently of the codec and
    # expected.json. Only deliberate candidate identity/payload changes apply.
    native = (ROOT / "tests/fixtures/native-firewall-gpmc/Registry.pol").read_bytes()
    native_chunks = raw_record_slices(native)
    expected_chunks = set()
    for chunk in native_chunks:
        for old, new in (
            ("StudioFwProbe", "StudioFwLane"),
            ("GPOStudioProbe", "StudioFwLaneSvc"),
            ("StudioProbe", "StudioFwLane"),
            ("Studio Probe Group", "StudioFwLane Group"),
            ("studio-domain", "StudioFwLane-domain"),
        ):
            chunk = chunk.replace(old.encode("utf-16le"), new.encode("utf-16le"))
        if "StudioFwLane-06".encode("utf-16le") in chunk:
            chunk = chunk.replace(
                "RPort=443|".encode("utf-16le"), "RPort=65011|".encode("utf-16le")
            )
        # Substitutions change data lengths: update only the size DWORD, leaving
        # native delimiters, type, string terminators and token order intact.
        size_offset = record_size_offset(chunk, 0)
        size = len(chunk) - size_offset - 6 - 2
        chunk = chunk[:size_offset] + struct.pack("<I", size) + chunk[size_offset + 4 :]
        expected_chunks.add(chunk)
    assert len(expected_chunks) == 25
    assert set(raw_record_slices(raw)) == expected_chunks
    records = parse(raw)
    assert len(records) == 25
    assert not any("PublicProfile" in r.key for r in records)
    assert not any(
        "PrivateProfile" in r.key and r.value_name == "DefaultOutboundAction" for r in records
    )


def test_expected_fields_match_independent_native_capture(tmp_path: Path) -> None:
    BUILDER["build"](tmp_path, BUILDER["candidate_policy"]())
    expected = json.loads((tmp_path / "expected.json").read_text())
    capture = json.loads(
        (ROOT / "tests/fixtures/native-firewall-gpmc/capture.json").read_text(encoding="utf-8-sig")
    )
    rules = capture["rules_readback"]
    replacements = [
        ("StudioFwProbe", "StudioFwLane"),
        ("GPOStudioProbe", "StudioFwLaneSvc"),
        ("StudioProbe", "StudioFwLane"),
        ("Studio Probe Group", "StudioFwLane Group"),
    ]
    for r in rules:
        for field, value in r.items():
            for old, new in replacements:
                value = value.replace(old, new)
            r[field] = value
        r["interface_alias"] = "Any"  # additional readback control; unconfigured interface alias
        if r["name"] == "StudioFwLane-06":
            r["remote_port"] = "65011"
    profiles = capture["profiles_readback"]
    profiles[0]["log_file"] = profiles[0]["log_file"].replace(
        "studio-domain", "StudioFwLane-domain"
    )
    assert expected["rules_readback"] == rules
    assert expected["profiles_readback"] == profiles
    provenance = json.loads(
        (ROOT / "tests/fixtures/native-firewall-gpmc/provenance.json").read_text()
    )
    assert [n["name"] for n in expected["normalizations"]] == [
        n["name"] for n in provenance["normalizations"]
    ]
    authored = json.loads((tmp_path / "authoring.json").read_text())
    assert authored["rules"][4]["RemoteAddress"] == ["192.0.2.0/24", "2001:db8::/32"]
    assert authored["rules"][10]["InterfaceType"] == "Wired"
    assert authored["rules"][12]["OverrideBlockRules"] is True
    assert [p["Name"] for p in authored["profiles"]] == ["Domain", "Private"]


def test_validation_refuses_before_writing_any_candidate(tmp_path: Path, monkeypatch) -> None:
    policy = BUILDER["candidate_policy"]()
    policy = replace(policy, rules=(replace(policy.rules[0], protocol=99), *policy.rules[1:]))
    with pytest.raises(ValueError, match="unmeasured_protocol"):
        BUILDER["build"](tmp_path / "refused", policy)
    assert not (tmp_path / "refused").exists()
    from gpo_studio.model import ValidationIssue

    monkeypatch.setitem(
        BUILDER["build"].__globals__,
        "validate_gpo",
        lambda _: [ValidationIssue("warning", "synthetic", "issue", "GPO")],
    )
    with pytest.raises(ValueError, match="synthetic"):
        BUILDER["build"](tmp_path / "warning", BUILDER["candidate_policy"]())
    assert not (tmp_path / "warning").exists()


@pytest.mark.parametrize(
    "change",
    [{"rule_id": 7}, {"name": 7}, {"local_port": 65001}, {"remote_port_range": [1, 2, 3]}],
)
def test_builder_runtime_type_probes_use_candidate_refused_path(
    tmp_path: Path, monkeypatch, capsys, change
) -> None:
    policy = BUILDER["candidate_policy"]()
    policy = replace(policy, rules=(replace(policy.rules[0], **change), *policy.rules[1:]))
    monkeypatch.setitem(BUILDER["main"].__globals__, "candidate_policy", lambda: policy)
    out = tmp_path / "refused"
    monkeypatch.setattr(sys, "argv", [str(BUILDER_PATH), str(out)])
    assert BUILDER["main"]() == 2
    assert "candidate refused: firewall_invalid_field_type" in capsys.readouterr().err
    assert not out.exists()


def test_native_archive_assertion_detects_shared_codec_corruption(
    tmp_path: Path, monkeypatch
) -> None:
    import gpo_studio.firewall_policy as codec

    original = codec._rule_tokens

    def corrupt(rule):
        return [token.replace("LPort=65001", "LPort=65099") for token in original(rule)]

    # Both builder expectations and archive output consume this same codec.
    # Native slices still disagree, so the formerly tautological test fails.
    monkeypatch.setattr(codec, "_rule_tokens", corrupt)
    with pytest.raises(AssertionError):
        test_backup_carries_only_measured_machine_records(tmp_path)
