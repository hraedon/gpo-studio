"""Plan 033 remediation scenario corpus tests.

The corpus under tests/fixtures/scenarios/ is the durable record the
Plans 025-032 remediation program proves repaired behavior against. These
tests keep the corpus honest: schema validity, loader acceptance, anchor
integrity (a changed native capture breaks the corpus loudly), readiness
honesty (no scenario claims ready on an unqualified platform), and executable
WI-022 reader/writer checks pinned to the genuine Services capture.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import jsonschema
import pytest

from gpo_studio.gpp_adapters import GppService, parse_gpp_services, serialize_gpp_services
from gpo_studio.remediation_corpus import (
    FAMILIES,
    RemediationCorpusError,
    anchor_violations,
    load_corpus,
    load_platform_registry,
    load_scenario,
)
from gpo_studio.xml_safety import parse_xml_bounded

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
NATIVE_SERVICES_RECOVERY_XML = (
    REPO_ROOT
    / "tests/fixtures/native-gpp-gpmc/WI01A-ServicesRecovery-GPMC"
    / "{8A722525-0F66-42EB-9BCE-2EEBC38BDEA8}"
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
            "writer-parity-target": "ready",
            "edition-union-expansion": "ready",
            "server-10x-collision": "ready",
            "lsdou-precedence": "ready",
            "codec-edge-cases": "ready",
            "group-membership": "ready",
            "regkeys-filesecurity": "ready",
            "services-area": "ready",
            "disabled-block-enforced": "blocked",
            "security-filtering": "blocked",
            "wmi-loopback-slowlink": "blocked",
        }

    def test_rsop_corpus_is_mostly_user_scope(self, registry) -> None:  # type: ignore[no-untyped-def]
        """The cost of the computer-scope-only ruling, made visible.

        Three of the four authored rsop-topology scenarios need a user-scope
        capture, so WP-6B inherits a one-scenario corpus. That is a real
        consequence of a real decision, and it should break this test if
        someone quietly re-marks those scenarios ready to make the lane look
        better fed than it is.
        """
        scenarios = {s.scenario_id: s for s in load_corpus(SCENARIO_DIR, registry)}
        rsop = [s for s in scenarios.values() if s.family == "rsop-topology"]
        computer_scope = [s for s in rsop if s.readiness == "ready"]
        assert len(rsop) == 4
        assert [s.scenario_id for s in computer_scope] == ["lsdou-precedence"]
        for scenario in rsop:
            if scenario.readiness == "blocked":
                assert scenario.blocked_reason is not None
                assert "WP-9" in scenario.blocked_reason, scenario.scenario_id

    def test_frozen_host_matches_environment_spec(self, registry) -> None:  # type: ignore[no-untyped-def]
        """Doc-truth coupling: a host row claiming frozen status must match
        the frozen environment specification it derives from."""
        spec = (SCHEMA_DIR / "environment-spec.md").read_text(encoding="utf-8")
        for host in registry.hosts:
            if host.status == "frozen":
                assert host.build in spec, host.host_id
                assert host.os.split(" Standard")[0] in spec, host.host_id
                assert host.qualifying_run is not None
                assert host.qualifying_run in spec, (
                    f"{host.host_id}: cites qualifying run {host.qualifying_run!r}, "
                    "which environment-spec.md does not record"
                )

    def test_every_qualified_environment_is_acknowledged_by_the_registry(self) -> None:
        """The registry may not lag a qualification the spec already records.

        This is the guard for a failure mode that has now recurred four times
        in this project: plan status lines said `proposed` while implemented,
        the capability matrix said `failed` while supported,
        environment-spec.md cited an orphaned commit, and platforms.json said
        `pending-qualification` for two hosts that had been qualified the same
        session. Each time the document that *gates work* disagreed with the
        document that *records reality*, and each time a human found it.

        A qualification is not real until the registry that gates work on it
        says so. So: every run id the spec's Qualified environments table
        cites -- except rows explicitly marked retired -- must appear
        somewhere in platforms.json. The check is deliberately textual rather
        than structural, because the point is that nobody can land a
        qualification in the spec while leaving the registry silent about it.
        """
        spec = (SCHEMA_DIR / "environment-spec.md").read_text(encoding="utf-8")
        registry_text = PLATFORM_REGISTRY_PATH.read_text(encoding="utf-8")

        table = [
            line
            for line in spec.splitlines()
            if line.startswith("|") and "`" in line and "retired" not in line
        ]
        run_ids = {
            run_id
            for line in table
            for run_id in re.findall(r"[a-z0-9][a-z0-9-]*-\d{14}-\d+", line)
        }
        assert run_ids, "no run ids parsed from the Qualified environments table"

        missing = sorted(run_id for run_id in run_ids if run_id not in registry_text)
        assert missing == [], (
            "environment-spec.md records these qualifying runs but platforms.json "
            f"never mentions them: {missing}. A qualification is not real until the "
            "registry that gates work on it says so."
        )


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

    def _base_rsop_scenario(self) -> dict:
        """An rsop-topology scenario, which is blocked because its lane is."""
        return {
            "schema_version": "1",
            "scenario_id": "neg-case",
            "family": "rsop-topology",
            "title": "negative case",
            "readiness": "blocked",
            "blocked_reason": "client-win11 qualification",
            "provenance": {"tier": "hypothesis", "note": "negative case"},
            "platform": {
                "lane": "rsop-endpoint",
                "boundaries": ["endpoint-resultant-state"],
            },
            "authored_intent": {"topology": {"som": "ou"}},
            "expected_native": {"winners": [{"key": "a", "winner": "GPO-1"}]},
        }

    def test_rsop_empty_winners_rejected(self, tmp_path: Path, registry) -> None:  # type: ignore[no-untyped-def]
        """A conflict-resolution scenario that names no winner asserts nothing.

        The other three families reject empty payload collections via
        _require_key; rsop-topology must not be laxer just because it accepts
        either of two shapes.
        """
        data = self._base_rsop_scenario()
        data["expected_native"] = {"winners": []}
        path = self._write_scenario(tmp_path, "rsop-topology", "neg-case", data)
        with pytest.raises(RemediationCorpusError, match="non-empty 'winners' or 'per_mode'"):
            load_scenario(path, registry)

    def test_rsop_empty_per_mode_rejected(self, tmp_path: Path, registry) -> None:  # type: ignore[no-untyped-def]
        data = self._base_rsop_scenario()
        data["expected_native"] = {"per_mode": []}
        path = self._write_scenario(tmp_path, "rsop-topology", "neg-case", data)
        with pytest.raises(RemediationCorpusError, match="non-empty 'winners' or 'per_mode'"):
            load_scenario(path, registry)

    def test_rsop_accepts_either_populated_shape(self, tmp_path: Path, registry) -> None:  # type: ignore[no-untyped-def]
        """The tightening must not reject the two shapes the corpus uses."""
        for payload in (
            {"winners": [{"key": "a", "winner": "GPO-1"}]},
            {"per_mode": [{"mode": "merge", "winners": []}]},
        ):
            data = self._base_rsop_scenario()
            data["expected_native"] = payload
            path = self._write_scenario(tmp_path, "rsop-topology", "neg-case", data)
            assert load_scenario(path, registry).family == "rsop-topology"

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
        """Uses rsop-user-loopback, whose `whoami` row is pending because the
        estate has no interactive logon. This test previously pointed at
        rsop-endpoint, and the 2026-08-03 estate qualification silently turned
        it vacuous -- a negative test is only a check while its example is
        still an example."""
        data = self._base_scenario()
        data["family"] = "rsop-topology"
        data["platform"] = {
            "lane": "rsop-user-loopback",
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


class TestServicesConformance:
    """Pin the WI-022/WI-024 corrections to genuine GPMC Services captures."""

    def test_recovery_semantics_are_preserved(self) -> None:
        items = {
            item.service_name: item
            for item in parse_gpp_services(NATIVE_SERVICES_XML.read_bytes())
        }
        assert set(items) == {"WinRM", "Spooler", "W32Time"}

        winrm = items["WinRM"]
        spooler = items["Spooler"]

        assert winrm.startup_type == "no_change"
        assert winrm.service_action == "start"
        assert winrm.first_failure == "restart"
        assert winrm.second_failure == "restart"
        assert winrm.third_failure == "restart"
        assert winrm.reset_fail_count_delay_seconds == 0
        assert winrm.restart_service_delay_milliseconds == 60000000
        assert winrm.timeout_seconds == 30

        assert spooler.third_failure is None
        assert spooler.reset_fail_count_delay_seconds == 86400
        assert spooler.restart_service_delay_milliseconds == 30000000
        assert spooler.unknown_props_children == ()
        assert ("image", "2") in spooler.unknown_attrs

    def test_rebuilt_capture_uses_native_names_and_omission_rules(self) -> None:
        items = parse_gpp_services(NATIVE_SERVICES_XML.read_bytes())
        emitted = serialize_gpp_services(items, "computer").decode("utf-8")
        assert 'resetFailCountDelay="86400"' in emitted
        assert 'restartServiceDelay="30000000"' in emitted
        assert emitted.count('thirdFailure="RESTART"') == 1
        assert "resetPeriod" not in emitted
        assert "restartDelay" not in emitted

    def test_manual_recovery_capture_settles_actions_units_and_fields(self) -> None:
        items = {
            item.service_name: item
            for item in parse_gpp_services(NATIVE_SERVICES_RECOVERY_XML.read_bytes())
        }
        spooler = items["Spooler"]
        assert spooler.service_action == "no_change"
        assert spooler.first_failure == "run_command"
        assert spooler.second_failure == "restart"
        assert spooler.third_failure == "reboot"
        assert spooler.reset_fail_count_delay_seconds == 172800
        assert spooler.restart_service_delay_milliseconds == 420000
        assert spooler.restart_computer_delay_milliseconds == 180000
        assert spooler.restart_message == "Synthetic GPO Studio recovery evidence"
        assert spooler.program == r"C:\Windows\System32\cmd.exe"
        assert spooler.arguments == "/c exit 0"
        assert spooler.append_failure_count is True

        w32time = items["W32Time"]
        assert w32time.service_action == "no_change"
        assert w32time.account_name == "LocalSystem"
        assert w32time.interact_with_desktop is True

        emitted = serialize_gpp_services(tuple(items.values()), "computer")
        assert b'firstFailure="RUNCMD"' in emitted
        assert b'thirdFailure="REBOOT"' in emitted
        assert b'restartServiceDelay="420000"' in emitted
        assert b'restartComputerDelay="180000"' in emitted
        assert b'append="1"' in emitted
        assert b"serviceAction" not in emitted

    def test_writer_parity_target_is_executable(self) -> None:
        scenario_path = SCENARIO_DIR / "gpp-services" / "writer-parity-target.json"
        scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
        services: list[GppService] = []
        for authored in scenario["authored_intent"]["items"]:
            recovery = authored["recovery"]
            services.append(
                GppService(
                    service_name=authored["service_name"],
                    startup_type=authored["startup_type"],
                    service_action=authored["service_action"],
                    timeout_seconds=authored["timeout_seconds"],
                    first_failure=(
                        recovery["first_failure"] if recovery is not None else None
                    ),
                    second_failure=(
                        recovery["second_failure"] if recovery is not None else None
                    ),
                    third_failure=(
                        recovery["third_failure"]
                        if recovery is not None and recovery["third_failure"] != "none"
                        else None
                    ),
                    reset_fail_count_delay_seconds=(
                        recovery["reset_fail_count_after_seconds"]
                        if recovery is not None
                        else None
                    ),
                    restart_service_delay_milliseconds=(
                        recovery["restart_service_after_milliseconds"]
                        if recovery is not None
                        else None
                    ),
                )
            )

        root = parse_xml_bounded(
            serialize_gpp_services(tuple(services), "computer"),
            max_size=1024 * 1024,
        )
        properties = {
            props.get("serviceName", ""): dict(props.attrib)
            for item in root
            for props in item
            if props.tag.split("}", 1)[-1] == "Properties"
        }
        for expected in scenario["expected_native"]["items"]:
            attrs = properties[expected["service_name"]]
            assert attrs == expected["properties_attrs"]
            assert not set(expected["omitted_attrs"]) & attrs.keys()
            assert not set(expected["must_not_contain_attrs"]) & attrs.keys()
