"""Candidate export is the bound export path; captured readback is independent."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import runpy
import subprocess
import sys
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

from gpo_studio.firewall_policy import FirewallProfileSettings, to_registry_settings
from gpo_studio.registry_pol import parse, serialize

ROOT = Path(__file__).resolve().parents[1]
BUILDER_PATH = ROOT / "scripts/plan-033/build-firewall-candidate.py"
BUILDER = runpy.run_path(str(BUILDER_PATH))


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
    expected = json.loads((tmp_path / "expected.json").read_text())
    with zipfile.ZipFile(io.BytesIO((tmp_path / BUILDER["ARCHIVE_NAME"]).read_bytes())) as bundle:
        paths = [p for p in bundle.namelist() if p.lower().endswith("registry.pol")]
        assert len(paths) == 1 and "/Machine/" in paths[0]
        raw = bundle.read(paths[0])
    settings = to_registry_settings(policy)
    assert raw == serialize(settings)
    records = parse(raw)
    assert len(records) == 25
    assert not any("PublicProfile" in r.key for r in records)
    assert not any(
        "PrivateProfile" in r.key and r.value_name == "DefaultOutboundAction" for r in records
    )
    chunks = {
        (r["key"], r["value_name"]): base64.b64decode(r["bytes_base64"])
        for r in expected["registry_records"]
    }
    assert chunks == {(r.key, r.value_name): serialize([r])[8:] for r in settings}


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
