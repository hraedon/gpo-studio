"""Exercise the real firewall finalizer, with a negative control for every gate."""

from __future__ import annotations

import base64
import copy
import json
import os
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

from gpo_studio.oracle_evidence import FROZEN_ENVIRONMENT
from gpo_studio.registry_pol import PolRecord, parse, serialize

ROOT = Path(__file__).resolve().parents[1]
FINALIZER_PATH = Path(
    os.environ.get(
        "FIREWALL_FINALIZER_UNDER_TEST", ROOT / "scripts/windows-oracle/finalize_firewall_run.py"
    )
)
FINALIZER = runpy.run_path(str(FINALIZER_PATH))
BUILDER = runpy.run_path(str(ROOT / "scripts/plan-033/build-firewall-candidate.py"))


@pytest.fixture
def evidence(tmp_path: Path):
    candidate, run = tmp_path / "candidate", tmp_path / "run"
    BUILDER["build"](candidate, BUILDER["candidate_policy"]())
    expected = json.loads((candidate / "expected.json").read_text())
    run.mkdir()
    (run / "commands").mkdir()
    (run / "deployed").mkdir()
    (run / "builder.stdout.txt").write_text("synthetic builder log")
    (run / "candidate.zip").write_bytes((candidate / BUILDER["ARCHIVE_NAME"]).read_bytes())
    (run / "authoring.json").write_bytes((candidate / "authoring.json").read_bytes())
    for name, relative in FINALIZER["DEPLOYED_FILES"].items():
        (run / "deployed" / name).write_bytes((ROOT / relative).read_bytes())
    frozen = FROZEN_ENVIRONMENT
    env = {
        field: getattr(frozen, field)
        for field in (
            "powershell_edition",
            "group_policy_module_version",
            "gpmc_version",
            "locale",
        )
    }
    env.update(
        server_build="26100",
        powershell_version="5.1.26100.33438",
        computer_system_domain_role=3,
        computer_system_name="LabMS01",
    )
    result = dict(
        schema_version=1,
        run_id="firewall-synthetic",
        domain="synthetic.test",
        persistent_before=[],
        persistent_after=[],
        cleanup_verified=True,
        cleanup_remaining=[],
        environment=env,
        error=None,
        operations=[],
    )
    # Deliberately use native record order (reverse here), not serializer order.
    raw = b"PReg\x01\0\0\0" + b"".join(
        base64.b64decode(r["bytes_base64"]) for r in reversed(expected["registry_records"])
    )
    for leg, guid in [
        ("read", "11111111-1111-4111-8111-111111111111"),
        ("write", "22222222-2222-4222-8222-222222222222"),
    ]:
        name = f"StudioFwLane-{leg}-{result['run_id']}"
        result[leg + "_leg"] = dict(
            target_name=name,
            owned_gpo_id=guid,
            policy_store="synthetic.test\\" + name,
            authoring_succeeded=leg == "read",
            import_succeeded=leg == "write",
            rules_readback=copy.deepcopy(expected["rules_readback"]),
            profiles_readback=copy.deepcopy(expected["profiles_readback"]),
            registry_pol_base64=base64.b64encode(raw).decode(),
            report_links_to_count=0,
            report_xml='<GPO><Computer><ExtensionData><Extension xmlns:f="urn:WindowsFirewall">'
            "<f:WindowsFirewallSettings/></Extension></ExtensionData></Computer></GPO>",
            ad_attributes=dict(
                gPCMachineExtensionNames="synthetic-extension-metadata",
                gPCUserExtensionNames="",
                gPCFileSysPath="synthetic-path",
            ),
        )
    sequence = FINALIZER["_required_operations"](result, expected)
    for cmd, leg, store, subject in sequence:
        rows = [(cmd, leg, store, subject)]
        if cmd == "Get-NetFirewallRule" and leg in ("read", "write"):
            rows.extend(
                ("Get-NetFirewall" + kind + "Filter", leg, store, r["name"])
                for r in expected["rules_readback"]
                for kind in FINALIZER["FILTERS"]
            )
        for name, tag, policy_store, item in rows:
            index = len(result["operations"])
            op = dict(name=name, leg=tag, policy_store=policy_store, subject=item, ok=True)
            for stream in ("stdout", "stderr"):
                op[stream] = f"{index:04d}-{name}.{stream}.txt"
                (run / "commands" / op[stream]).write_text("")
            result["operations"].append(op)
    return result, expected, run, candidate


def grade(evidence):
    result, expected, run, candidate = evidence
    return FINALIZER["grade"](result, expected, run, ROOT, candidate)[0]


def test_complete_synthetic_evidence_passes(evidence) -> None:
    checks = grade(evidence)
    assert checks and all(checks.values()), checks


FAILURES = [
    ("result_schema_exact", "extra"),
    ("authoring_succeeded", "author"),
    ("import_succeeded", "import"),
    ("windows_registry_pol_parses_with_zero_unrecognised", "unknown"),
    ("read_parsed_policy_equals_authored_policy", "parsed"),
    ("read_registry_records_equal_codec_emission", "bytes"),
    ("read_cmdlet_readback_matches_expected", "read_rule"),
    ("write_cmdlet_readback_matches_expected", "write_rule"),
    ("write_every_rule_id_present", "missing_rule"),
    ("write_no_extra_rules", "extra_rule"),
    ("gpos_never_linked", "linked"),
    ("persistent_store_untouched", "persistent"),
    ("cleanup_verified", "cleanup"),
    ("member_server_host_role", "role"),
    ("environment_matches_frozen_spec", "environment"),
    ("deployed_harness_matches_source", "harness"),
    ("candidate_delivered_intact", "delivery"),
    ("harness_reported_no_error", "error"),
    ("operations_match_scoped_contract", "scope"),
    ("builder_stdout_present", "builder"),
]


@pytest.mark.parametrize("check,mutation", FAILURES)
def test_each_named_check_can_fail(evidence, check, mutation) -> None:
    result, expected, run, _ = evidence
    if mutation == "extra":
        result["unexpected"] = True
    elif mutation in ("author", "import"):
        result["read_leg" if mutation == "author" else "write_leg"][
            "authoring_succeeded" if mutation == "author" else "import_succeeded"
        ] = False
    elif mutation in ("unknown", "parsed", "bytes"):
        raw = base64.b64decode(result["read_leg"]["registry_pol_base64"])
        records = parse(raw)
        if mutation == "unknown":
            records.append(PolRecord("SOFTWARE\\Policies\\Synthetic", "Unknown", "REG_DWORD", 1))
            raw = serialize(records)
        elif mutation == "parsed":
            records = [r for r in records if r.value_name != "StudioFwLane-01"]
            raw = serialize(records)
        else:
            # Case is retained in raw records; the codec still recognises it.
            raw = raw.replace(
                "DomainProfile".encode("utf-16le"), "domainProfile".encode("utf-16le")
            )
        result["read_leg"]["registry_pol_base64"] = base64.b64encode(raw).decode()
    elif mutation in ("read_rule", "write_rule"):
        result[mutation.split("_")[0] + "_leg"]["rules_readback"][0]["local_port"] = "65099"
    elif mutation == "missing_rule":
        result["write_leg"]["rules_readback"].pop()
    elif mutation == "extra_rule":
        row = dict(result["write_leg"]["rules_readback"][0], name="StudioFwLane-extra")
        result["write_leg"]["rules_readback"].append(row)
    elif mutation == "linked":
        result["write_leg"]["report_xml"] = (
            "<GPO><LinksTo><SOMName>synthetic</SOMName></LinksTo></GPO>"
        )
    elif mutation == "persistent":
        result["persistent_after"] = ["StudioFwLane-01"]
    elif mutation == "cleanup":
        result["cleanup_remaining"] = ["synthetic-residual"]
    elif mutation == "role":
        result["environment"]["computer_system_domain_role"] = 5
    elif mutation == "environment":
        result["environment"]["server_build"] = "99999"
    elif mutation == "harness":
        (run / "deployed" / "run-firewall-policy.ps1").write_text("modified")
    elif mutation == "delivery":
        (run / "authoring.json").write_text("{}")
    elif mutation == "error":
        result["error"] = "synthetic failure"
    elif mutation == "scope":
        result["operations"][3]["policy_store"] = "PersistentStore"
    elif mutation == "builder":
        (run / "builder.stdout.txt").unlink()
    else:
        raise AssertionError(mutation)
    assert grade(evidence)[check] is False, check


@pytest.mark.parametrize("leg", ["read", "write"])
@pytest.mark.parametrize("normalization", FINALIZER["NORMALIZATIONS"])
def test_every_named_normalization_fails_independently(evidence, leg, normalization) -> None:
    result, expected, _, _ = evidence
    norm = next(n for n in expected["normalizations"] if n["name"] == normalization)
    row = next(r for r in result[leg + "_leg"]["rules_readback"] if r["name"] == norm["rule_id"])
    row[next(iter(norm["readback"]))] = "synthetic-wrong-readback"
    assert grade(evidence)[leg + "_normalization_" + normalization] is False


@pytest.mark.parametrize("leg", ["read", "write"])
def test_profile_collapse_is_detected(evidence, leg) -> None:
    evidence[0][leg + "_leg"]["profiles_readback"][1]["default_outbound"] = "Allow"
    assert grade(evidence)[leg + "_cmdlet_readback_matches_expected"] is False


@pytest.mark.parametrize("key", sorted(FINALIZER["RESULT_KEYS"]))
def test_missing_top_level_data_never_passes(evidence, key) -> None:
    del evidence[0][key]
    assert not all(grade(evidence).values())


@pytest.mark.parametrize("leg", ["read", "write"])
@pytest.mark.parametrize("key", sorted(FINALIZER["LEG_KEYS"]))
def test_missing_leg_data_never_passes(evidence, leg, key) -> None:
    del evidence[0][leg + "_leg"][key]
    assert not all(grade(evidence).values())


def test_raw_bytes_comparison_does_not_hide_a_second_string_terminator(evidence) -> None:
    result, _, _, _ = evidence
    raw = base64.b64decode(result["read_leg"]["registry_pol_base64"])
    # A semantically identical REG_SZ with another terminator is byte-different.
    import struct

    chunks = FINALIZER["_record_bytes"](raw)
    key = next(k for k in chunks if k[1] == "StudioFwLane-01")
    chunk = chunks[key]
    size_offset = 2 + (len(key[0]) + 1) * 2 + 2 + (len(key[1]) + 1) * 2 + 2 + 6
    old_size = struct.unpack_from("<I", chunk, size_offset)[0]
    changed = (
        chunk[:size_offset]
        + struct.pack("<I", old_size + 2)
        + chunk[size_offset + 4 : -2]
        + b"\0\0"
        + chunk[-2:]
    )
    raw = raw.replace(chunk, changed)
    result["read_leg"]["registry_pol_base64"] = base64.b64encode(raw).decode()
    checks = grade(evidence)
    assert checks["read_parsed_policy_equals_authored_policy"] is True
    assert checks["read_registry_records_equal_codec_emission"] is False


def test_finalizer_refuses_each_missing_candidate(tmp_path: Path) -> None:
    # The source-byte guard runs first, so supply genuinely committed bound bytes.
    source = tmp_path / "source"
    source.mkdir()
    for path in {**FINALIZER["LOCAL_FILES"], **FINALIZER["DEPLOYED_FILES"]}.values():
        target = source / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / path).read_bytes())
    for args in (
        ["init"],
        ["config", "user.name", "Synthetic Test"],
        ["config", "user.email", "test@example.invalid"],
        ["config", "core.autocrlf", "false"],
        ["config", "commit.gpgsign", "false"],
        ["add", "."],
        ["commit", "-m", "synthetic bound source"],
    ):
        subprocess.run(["git", *args], cwd=source, check=True, capture_output=True)
    for omitted in FINALIZER["REQUIRED_CANDIDATE_FILES"]:
        root = tmp_path / omitted
        root.mkdir()
        for name in FINALIZER["REQUIRED_CANDIDATE_FILES"]:
            if name != omitted:
                (root / name).write_text("{}")
        completed = subprocess.run(
            [
                sys.executable,
                str(FINALIZER_PATH),
                str(tmp_path),
                "--candidate-root",
                str(root),
                "--repo-root",
                str(source),
                "--no-tag",
            ],
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 1 and omitted in completed.stderr


def test_rule_blind_finalizer_mutation_fails_the_comparison_test(tmp_path: Path) -> None:
    if "FIREWALL_FINALIZER_UNDER_TEST" in os.environ:
        pytest.skip("avoid recursive mutation")
    source = FINALIZER_PATH.read_text()
    assert "rules == wanted" in source
    mutant = tmp_path / "mutant.py"
    mutant.write_text(source.replace("rules == wanted", "True"))
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            str(Path(__file__)),
            "-k",
            "each_named_check_can_fail and write_rule",
        ],
        env={**os.environ, "FIREWALL_FINALIZER_UNDER_TEST": str(mutant)},
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 1, completed.stdout + completed.stderr
    assert "FAILED" in completed.stdout and "write_rule" in completed.stdout


def test_driver_binding_and_controller_only_expectation() -> None:
    driver = (ROOT / "scripts/windows-oracle/run-firewall-oracle.sh").read_text()
    for name, path in {**FINALIZER["LOCAL_FILES"], **FINALIZER["DEPLOYED_FILES"]}.items():
        assert (ROOT / path).is_file()
        if name in FINALIZER["DEPLOYED_FILES"]:
            assert name in driver
        assert b"\r\n" not in (ROOT / path).read_bytes() if "firewall" in name else True
    pushes = [line for line in driver.splitlines() if "-LocalPath" in line]
    assert pushes and not any("expected.json" in line for line in pushes)
    guest = (ROOT / FINALIZER["DEPLOYED_FILES"]["run-firewall-policy.ps1"]).read_text()
    assert "expected.json" not in guest
    publication = runpy.run_path(str(ROOT / "scripts/windows-oracle/finalize_publication_run.py"))
    export_chain = {k: v for k, v in publication["LOCAL_FILES"].items() if v.startswith("src/")}
    assert export_chain.items() <= FINALIZER["LOCAL_FILES"].items()


@pytest.mark.parametrize("dirty", [False, True])
def test_main_banks_candidate_hashes_and_gates_dirty_source(evidence, monkeypatch, dirty) -> None:
    import hashlib

    result, _, run, candidate = evidence
    (run / "result.json").write_text(json.dumps(result))
    global_scope = FINALIZER["main"].__globals__
    monkeypatch.setitem(global_scope, "assert_bound_source_bytes", lambda *_: None)
    monkeypatch.setitem(
        global_scope,
        "manifest_bound_source",
        lambda repo, files: {
            name: {"sha256": hashlib.sha256((repo / path).read_bytes()).hexdigest()}
            for name, path in files.items()
        },
    )
    original_run = subprocess.run

    def synthetic_git(args, **kwargs):
        if args == ["git", "status", "--porcelain"]:
            return subprocess.CompletedProcess(args, 0, " M synthetic" if dirty else "", "")
        return original_run(args, **kwargs)

    monkeypatch.setattr(subprocess, "run", synthetic_git)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(FINALIZER_PATH),
            str(run),
            "--candidate-root",
            str(candidate),
            "--repo-root",
            str(ROOT),
            "--no-tag",
        ],
    )
    assert FINALIZER["main"]() == (1 if dirty else 0)
    verdict = json.loads((run / "verification.json").read_text())
    assert verdict["checks"]["source_tree_clean"] is not dirty
    assert verdict["passed"] is not dirty
    assert verdict["candidate"] == {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in candidate.iterdir()
    }
    assert verdict["source"]["paths"] == {**FINALIZER["LOCAL_FILES"], **FINALIZER["DEPLOYED_FILES"]}


@pytest.mark.parametrize("leg", ["read", "write"])
def test_extension_registration_is_recorded_without_certifying_a_tool_guid(evidence, leg) -> None:
    result, expected, run, candidate = evidence
    result[leg + "_leg"]["ad_attributes"]["gPCMachineExtensionNames"] = ""
    result[leg + "_leg"]["report_xml"] = "<GPO><Computer/></GPO>"
    checks, comparison = FINALIZER["grade"](result, expected, run, ROOT, candidate)
    assert all(checks.values())
    assert comparison["extension_observations"][leg] == {
        "gPCMachineExtensionNames": "",
        "gpmc_firewall_extension_rendered": False,
    }


@pytest.mark.parametrize(
    "malformation",
    [
        "duplicate_rule",
        "null_bytes",
        "missing_log",
        "identical_gpos",
        "foreign_store",
        "malformed_pol",
        "null_ad",
        "null_leg",
        "null_id",
    ],
)
def test_malformed_evidence_fails_closed(evidence, malformation) -> None:
    result, _, run, _ = evidence
    if malformation == "duplicate_rule":
        result["write_leg"]["rules_readback"].append(result["write_leg"]["rules_readback"][0])
    elif malformation == "null_bytes":
        result["write_leg"]["registry_pol_base64"] = None
    elif malformation == "missing_log":
        (run / "commands" / result["operations"][0]["stderr"]).unlink()
    elif malformation == "identical_gpos":
        result["write_leg"]["owned_gpo_id"] = result["read_leg"]["owned_gpo_id"]
    elif malformation == "foreign_store":
        result["read_leg"]["policy_store"] = "PersistentStore"
    elif malformation == "null_ad":
        result["read_leg"]["ad_attributes"] = None
    elif malformation == "null_leg":
        result["read_leg"] = None
    elif malformation == "null_id":
        result["read_leg"]["owned_gpo_id"] = None
    elif malformation == "malformed_pol":
        result["read_leg"]["registry_pol_base64"] = "!!!!"
    assert not all(grade(evidence).values())


def test_guest_command_wrapper_preserves_caller_gpo_identity_and_logs_failures(
    tmp_path: Path,
) -> None:
    import shutil

    pwsh = shutil.which("pwsh")
    if pwsh is None:
        pytest.skip("PowerShell unavailable")
    script = tmp_path / "wrapper-test.ps1"
    script.write_text(r"""
param([string]$Harness, [string]$LogRoot)
$ErrorActionPreference = 'Stop'
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $Harness, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'parser errors' }
$function = $ast.Find({param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
    $node.Name -eq 'Invoke-LaneCommand'
}, $true)
# Load only the logging helper, never the guest's policy operations.
. ([scriptblock]::Create($function.Extent.Text))
$result = [ordered]@{ operations = @() }
$commands = $LogRoot
$id = '11111111-1111-4111-8111-111111111111'
$name = 'StudioFwLane-synthetic'
$store = 'synthetic.test\StudioFwLane-synthetic'
$output = Invoke-LaneCommand 'Get-GPO' 'read' $null $id { "$id;$name;$store" }
if ($output -ne "$id;$name;$store") { throw "caller identity shadowed: $output" }
try {
    Invoke-LaneCommand 'Import-GPO' 'write' $null $id { throw 'synthetic refusal' }
    throw 'failure was swallowed'
} catch {
    if ($_.Exception.Message -ne 'synthetic refusal') { throw }
}
$result | ConvertTo-Json -Depth 6
""")
    completed = subprocess.run(
        [
            pwsh,
            "-NoProfile",
            "-File",
            str(script),
            "-Harness",
            str(ROOT / FINALIZER["DEPLOYED_FILES"]["run-firewall-policy.ps1"]),
            "-LogRoot",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    operations = json.loads(completed.stdout)["operations"]
    assert [op["ok"] for op in operations] == [True, False]
    assert operations[0]["subject"] == "11111111-1111-4111-8111-111111111111"
    assert operations[0]["policy_store"] is None
    assert "synthetic refusal" in (tmp_path / operations[1]["stderr"]).read_text(
        encoding="utf-8-sig"
    )
    assert "StudioFwLane-synthetic" in (tmp_path / operations[0]["stdout"]).read_text(
        encoding="utf-8-sig"
    )
