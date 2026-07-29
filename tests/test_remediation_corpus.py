"""Plan 033 remediation scenario corpus tests.

The corpus under tests/fixtures/scenarios/ is the durable record the
Plans 025-032 remediation program proves repaired behavior against. These
tests keep the corpus honest: schema validity, loader acceptance, anchor
integrity (a changed native capture breaks the corpus loudly), readiness
honesty (no scenario claims ready on an unqualified platform), and one
characterization probe that pins today's known WI-022 parse-side divergence
so the corpus flips loudly when the fix lands.
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from gpo_studio.gpp_adapters import parse_gpp_services
from gpo_studio.remediation_corpus import (
    FAMILIES,
    RemediationCorpusError,
    anchor_violations,
    load_corpus,
    load_platform_registry,
    load_scenario,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SCENARIO_DIR = REPO_ROOT / "tests" / "fixtures" / "scenarios"
SCHEMA_DIR = REPO_ROOT / "docs" / "plan-033"
SCENARIO_SCHEMA_PATH = SCHEMA_DIR / "remediation-scenario-v1.schema.json"
PLATFORM_SCHEMA_PATH = SCHEMA_DIR / "test-platform-registry-v1.schema.json"
PLATFORM_REGISTRY_PATH = SCENARIO_DIR / "platforms.json"

NATIVE_SERVICES_XML = (
    REPO_ROOT
    / "tests/fixtures/native-gpp-gpmc/WI01A-Services-GPMC"
    / "{BFC38120-130B-43A1-AB13-80D3A4D0E6C1}"
    / "DomainSysvol/GPO/Machine/Preferences/Services/Services.xml"
)

SCENARIO_FILES = sorted(SCENARIO_DIR.glob("*/*.json"))


@pytest.fixture(scope="module")
def registry():  # type: ignore[no-untyped-def]
    return load_platform_registry(PLATFORM_REGISTRY_PATH)


class TestSchemas:
    def test_scenario_schema_is_valid_draft_2020_12(self) -> None:
        schema = json.loads(SCENARIO_SCHEMA_PATH.read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator.check_schema(schema)

    def test_platform_schema_is_valid_draft_2020_12(self) -> None:
        schema = json.loads(PLATFORM_SCHEMA_PATH.read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator.check_schema(schema)

    def test_platform_registry_validates(self) -> None:
        schema = json.loads(PLATFORM_SCHEMA_PATH.read_text(encoding="utf-8"))
        data = json.loads(PLATFORM_REGISTRY_PATH.read_text(encoding="utf-8"))
        jsonschema.validate(instance=data, schema=schema)

    @pytest.mark.parametrize("path", SCENARIO_FILES, ids=lambda p: f"{p.parent.name}/{p.stem}")
    def test_scenario_validates(self, path: Path) -> None:
        schema = json.loads(SCENARIO_SCHEMA_PATH.read_text(encoding="utf-8"))
        jsonschema.validate(instance=json.loads(path.read_text(encoding="utf-8")), schema=schema)


class TestCorpus:
    def test_corpus_loads_all_files(self, registry) -> None:  # type: ignore[no-untyped-def]
        scenarios = load_corpus(SCENARIO_DIR, registry)
        assert len(scenarios) == len(SCENARIO_FILES) == 13
        assert {scenario.family for scenario in scenarios} == set(FAMILIES)

    def test_every_anchor_hash_verifies(self, registry) -> None:  # type: ignore[no-untyped-def]
        scenarios = load_corpus(SCENARIO_DIR, registry)
        violations = [
            violation
            for scenario in scenarios
            for violation in anchor_violations(scenario, REPO_ROOT)
        ]
        assert violations == []

    def test_ready_scenarios_run_on_qualified_platforms(self, registry) -> None:  # type: ignore[no-untyped-def]
        """Readiness honesty: ready means every required platform is frozen.

        load_scenario enforces this per file; this asserts the corpus-level
        invariant so a registry change (for example a tool losing its frozen
        status) is caught here too.
        """
        for scenario in load_corpus(SCENARIO_DIR, registry):
            lane = registry.lane(scenario.platform.lane)
            assert lane is not None
            pending = registry.pending_platforms(lane)
            if scenario.readiness == "ready":
                assert pending == (), f"{scenario.scenario_id}: ready on {pending}"
            else:
                assert scenario.blocked_reason, scenario.scenario_id

    def test_known_readiness_map(self, registry) -> None:  # type: ignore[no-untyped-def]
        """The readiness map is itself a deliverable: it records which lanes
        are executable today (capture analysis) and which wait on platform
        qualification (client, disposable member) or lane extension."""
        scenarios = load_corpus(SCENARIO_DIR, registry)
        readiness = {scenario.scenario_id: scenario.readiness for scenario in scenarios}
        assert readiness == {
            "native-recovery-units": "ready",
            "reader-no-silent-drop": "ready",
            "writer-parity-target": "blocked",
            "edition-union-expansion": "ready",
            "server-10x-collision": "ready",
            "disabled-block-enforced": "blocked",
            "lsdou-precedence": "blocked",
            "security-filtering": "blocked",
            "wmi-loopback-slowlink": "blocked",
            "codec-edge-cases": "blocked",
            "group-membership": "blocked",
            "regkeys-filesecurity": "blocked",
            "services-area": "blocked",
        }

    def test_frozen_host_matches_environment_spec(self, registry) -> None:  # type: ignore[no-untyped-def]
        """Doc-truth coupling: a host row claiming frozen status must match
        the frozen environment specification it derives from."""
        spec = (SCHEMA_DIR / "environment-spec.md").read_text(encoding="utf-8")
        for host in registry.hosts:
            if host.status == "frozen":
                assert host.build in spec, host.host_id
                assert host.os.split(" Standard")[0] in spec, host.host_id


class TestLoaderNegatives:
    """Each invariant has a negative test: an invariant without a failing
    case is a hope, not a check."""

    def _write_scenario(self, tmp_path: Path, family: str, scenario_id: str, data: dict) -> Path:
        directory = tmp_path / family
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{scenario_id}.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def _base_scenario(self) -> dict:
        return {
            "schema_version": "1",
            "scenario_id": "neg-case",
            "family": "ilt-os",
            "title": "negative case",
            "readiness": "ready",
            "provenance": {"tier": "hypothesis", "note": "negative case"},
            "platform": {"lane": "gpp-reader-native", "boundaries": ["gpo-backup-content"]},
            "authored_intent": {"predicate": {"type": "os"}},
            "expected_native": {"match_semantics": {"distinguishable": True}},
        }

    def test_unknown_family_rejected(self, tmp_path: Path, registry) -> None:  # type: ignore[no-untyped-def]
        data = self._base_scenario()
        data["family"] = "gpp-printers"
        path = self._write_scenario(tmp_path, "gpp-printers", "neg-case", data)
        with pytest.raises(RemediationCorpusError, match="unknown family"):
            load_scenario(path, registry)

    def test_unknown_lane_rejected(self, tmp_path: Path, registry) -> None:  # type: ignore[no-untyped-def]
        data = self._base_scenario()
        data["platform"]["lane"] = "no-such-lane"
        path = self._write_scenario(tmp_path, "ilt-os", "neg-case", data)
        with pytest.raises(RemediationCorpusError, match="unknown lane"):
            load_scenario(path, registry)

    def test_boundary_outside_lane_rejected(self, tmp_path: Path, registry) -> None:  # type: ignore[no-untyped-def]
        data = self._base_scenario()
        data["platform"]["boundaries"] = ["gpo-backup-content", "endpoint-resultant-state"]
        path = self._write_scenario(tmp_path, "ilt-os", "neg-case", data)
        with pytest.raises(RemediationCorpusError, match="not a subset"):
            load_scenario(path, registry)

    def test_ready_on_unqualified_platform_rejected(self, tmp_path: Path, registry) -> None:  # type: ignore[no-untyped-def]
        data = self._base_scenario()
        data["family"] = "rsop-topology"
        data["platform"] = {
            "lane": "rsop-endpoint",
            "boundaries": ["endpoint-resultant-state"],
        }
        data["authored_intent"] = {"topology": {"som": []}}
        data["expected_native"] = {"winners": [{"key": "k"}]}
        path = self._write_scenario(tmp_path, "rsop-topology", "neg-case", data)
        with pytest.raises(RemediationCorpusError, match="unqualified platforms"):
            load_scenario(path, registry)

    def test_ready_with_blocked_reason_rejected(self, tmp_path: Path, registry) -> None:  # type: ignore[no-untyped-def]
        data = self._base_scenario()
        data["blocked_reason"] = "contradiction"
        path = self._write_scenario(tmp_path, "ilt-os", "neg-case", data)
        with pytest.raises(RemediationCorpusError, match="must not carry blocked_reason"):
            load_scenario(path, registry)

    def test_file_stem_must_match_scenario_id(self, tmp_path: Path, registry) -> None:  # type: ignore[no-untyped-def]
        data = self._base_scenario()
        path = self._write_scenario(tmp_path, "ilt-os", "other-name", data)
        with pytest.raises(RemediationCorpusError, match="file stem"):
            load_scenario(path, registry)

    def test_family_directory_enforced(self, tmp_path: Path, registry) -> None:  # type: ignore[no-untyped-def]
        data = self._base_scenario()
        path = self._write_scenario(tmp_path, "gpp-services", "neg-case", data)
        with pytest.raises(RemediationCorpusError, match="directory"):
            load_scenario(path, registry)

    def test_native_capture_anchor_requires_sha256(self, tmp_path: Path, registry) -> None:  # type: ignore[no-untyped-def]
        data = self._base_scenario()
        data["provenance"]["anchors"] = [
            {"kind": "native-capture", "path": "tests/fixtures/scenarios/platforms.json"}
        ]
        path = self._write_scenario(tmp_path, "ilt-os", "neg-case", data)
        with pytest.raises(RemediationCorpusError, match="sha256"):
            load_scenario(path, registry)

    def test_family_payload_shape_enforced(self, tmp_path: Path, registry) -> None:  # type: ignore[no-untyped-def]
        data = self._base_scenario()
        data["expected_native"] = {"wrong_key": {}}
        path = self._write_scenario(tmp_path, "ilt-os", "neg-case", data)
        with pytest.raises(RemediationCorpusError, match="match_semantics"):
            load_scenario(path, registry)

    def test_registry_rejects_unknown_lane_host(self, tmp_path: Path) -> None:
        data = json.loads(PLATFORM_REGISTRY_PATH.read_text(encoding="utf-8"))
        data["lanes"][0]["required_hosts"] = ["no-such-host"]
        path = tmp_path / "platforms.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(RemediationCorpusError, match="unknown host"):
            load_platform_registry(path)

    def test_duplicate_scenario_ids_rejected(self, tmp_path: Path, registry) -> None:  # type: ignore[no-untyped-def]
        data = self._base_scenario()
        self._write_scenario(tmp_path, "ilt-os", "neg-case", data)
        second = dict(data)
        second["title"] = "duplicate id, different title"
        (tmp_path / "ilt-os" / "neg-case-copy.json").write_text(
            json.dumps(second), encoding="utf-8"
        )
        with pytest.raises(RemediationCorpusError, match="duplicate"):
            load_corpus(tmp_path, registry)


class TestWi022ParseCharacterization:
    """Pin today's WI-022 parse-side divergence against the genuine capture.

    These assertions describe CURRENT behavior and are expected to FAIL once
    WI-022 is fixed; that failure is the signal to flip them and remove the
    scenario's current_behavior block. Until then they are executable
    documentation of exactly what the reader loses.
    """

    def test_recovery_semantics_currently_lost(self) -> None:
        items = {
            item.service_name: item
            for item in parse_gpp_services(NATIVE_SERVICES_XML.read_bytes())
        }
        assert set(items) == {"WinRM", "Spooler", "W32Time"}

        winrm = items["WinRM"]
        spooler = items["Spooler"]

        # Parsed correctly today: startup, action, first/second failure, timeout.
        assert winrm.startup_type == "no_change"
        assert winrm.service_action == "start"
        assert winrm.first_failure == "restart"
        assert winrm.timeout_seconds == 30

        # Lost today, part one: the model has no third-failure field at all,
        # so the capture's thirdFailure="RESTART" vanishes.
        assert not hasattr(winrm, "third_failure")

        # Lost today, part two: resetFailCountDelay/restartServiceDelay are
        # read from Studio's synthetic attribute names, which the capture
        # does not contain, so both fall back to zero.
        assert spooler.reset_period_days == 0  # capture carries resetFailCountDelay="86400"
        assert spooler.restart_delay_minutes == 0  # capture carries restartServiceDelay="30000000"

        # Lost today, part three: unknown Properties attributes have no
        # preservation path; only item-level unknowns are captured.
        assert spooler.unknown_props_children == ()
        assert ("image", "2") in spooler.unknown_attrs  # item-level unknowns DO survive

    def test_emitted_attribute_names_are_synthetic(self) -> None:
        """The writer half of the same divergence: serializing the parsed
        capture emits Studio's synthetic attribute names, which appear
        nowhere in the native corpus."""
        from gpo_studio.gpp_adapters import serialize_gpp_services

        items = parse_gpp_services(NATIVE_SERVICES_XML.read_bytes())
        emitted = serialize_gpp_services(items, "computer").decode("utf-8")
        assert 'resetPeriod="' in emitted  # synthetic name
        assert 'restartDelay="' in emitted  # synthetic name
        assert "resetFailCountDelay" not in emitted  # native name never emitted
        assert "restartServiceDelay" not in emitted
        assert "thirdFailure" not in emitted  # native attribute not even modeled
