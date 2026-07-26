from __future__ import annotations

from pathlib import Path

from gpo_studio.artifact_store import ArtifactStore
from gpo_studio.script_policy import (
    PowerShellScriptEntry,
    ScriptEntry,
    ScriptPolicy,
    parse_script_policy_ini,
    preview_script_policy,
    quote_parameter,
    serialize_script_policy_ini,
    validate_parameters,
)


def _entry(**kwargs: object) -> ScriptEntry:
    defaults: dict[str, object] = {
        "script_id": "s1",
        "artifact_id": "a" * 64,
        "original_name": "test.bat",
    }
    defaults.update(kwargs)
    return ScriptEntry(**defaults)  # type: ignore[arg-type]


def _ps_entry(**kwargs: object) -> PowerShellScriptEntry:
    defaults: dict[str, object] = {
        "script_id": "ps1",
        "artifact_id": "b" * 64,
        "original_name": "test.ps1",
    }
    defaults.update(kwargs)
    return PowerShellScriptEntry(**defaults)  # type: ignore[arg-type]


class TestScriptEntryValidation:
    def test_valid_entry(self) -> None:
        entry = _entry()
        assert entry.validate() == ()

    def test_empty_artifact_id(self) -> None:
        entry = _entry(artifact_id="")
        issues = entry.validate()
        assert any(i.code == "empty_artifact_id" and i.severity == "error" for i in issues)

    def test_empty_original_name(self) -> None:
        entry = _entry(original_name="")
        issues = entry.validate()
        assert any(
            i.code == "empty_original_name" and i.severity == "error" for i in issues
        )

    def test_negative_timeout(self) -> None:
        entry = _entry(timeout_seconds=-1)
        issues = entry.validate()
        assert any(i.code == "negative_timeout" and i.severity == "error" for i in issues)

    def test_unsafe_parameters(self) -> None:
        entry = _entry(parameters="foo | bar")
        issues = entry.validate()
        assert any(
            i.code == "unquoted_metacharacter" and i.severity == "error" for i in issues
        )

    def test_quoted_metacharacter_allowed(self) -> None:
        entry = _entry(parameters='"foo | bar"')
        assert entry.validate() == ()


class TestPowerShellScriptEntryValidation:
    def test_valid_entry(self) -> None:
        entry = _ps_entry()
        assert entry.validate() == ()

    def test_empty_artifact_id(self) -> None:
        entry = _ps_entry(artifact_id="")
        issues = entry.validate()
        assert any(i.code == "empty_artifact_id" for i in issues)

    def test_empty_original_name(self) -> None:
        entry = _ps_entry(original_name="")
        issues = entry.validate()
        assert any(i.code == "empty_original_name" for i in issues)

    def test_no_profile_and_non_interactive_defaults(self) -> None:
        entry = _ps_entry()
        assert entry.no_profile is False
        assert entry.non_interactive is True


class TestScriptPolicy:
    def test_scripts_for_type_ordering(self) -> None:
        policy = ScriptPolicy(
            startup=(
                _entry(script_id="l2", order=2),
                _entry(script_id="l1", order=1),
            ),
            powershell_startup=(_ps_entry(script_id="p1", order=3),),
        )
        ordered = policy.scripts_for_type("startup")
        assert [s.script_id for s in ordered] == ["l1", "l2", "p1"]

    def test_scripts_for_type_powershell_first(self) -> None:
        policy = ScriptPolicy(
            startup=(_entry(script_id="l1", order=2),),
            powershell_startup=(_ps_entry(script_id="p1", order=1),),
            legacy_scripts_first=False,
        )
        ordered = policy.scripts_for_type("startup")
        assert [s.script_id for s in ordered] == ["p1", "l1"]

    def test_duplicate_artifact_id_warning(self) -> None:
        policy = ScriptPolicy(
            startup=(
                _entry(script_id="a", artifact_id="same"),
                _entry(script_id="b", artifact_id="same"),
            )
        )
        issues = policy.validate()
        assert any(i.code == "duplicate_artifact" and i.severity == "warning" for i in issues)

    def test_duplicate_order_error(self) -> None:
        policy = ScriptPolicy(
            startup=(
                _entry(script_id="a", order=1),
                _entry(script_id="b", order=1),
            )
        )
        issues = policy.validate()
        assert any(i.code == "duplicate_order" and i.severity == "error" for i in issues)

    def test_powershell_async_timeout_warning(self) -> None:
        policy = ScriptPolicy(
            powershell_startup=(
                _ps_entry(execution="asynchronous", timeout_seconds=30),
            )
        )
        issues = policy.validate()
        assert any(
            i.code == "async_timeout_ignored" and i.severity == "warning" for i in issues
        )


class TestParameterSafety:
    def test_safe_parameters(self) -> None:
        assert validate_parameters("-Server db -Port 1433") == ()

    def test_pipe_injection(self) -> None:
        issues = validate_parameters("foo | whoami")
        assert any(i.code == "unquoted_metacharacter" for i in issues)

    def test_ampersand(self) -> None:
        issues = validate_parameters("foo & bar")
        assert any(i.code == "unquoted_metacharacter" for i in issues)

    def test_redirect(self) -> None:
        issues = validate_parameters("foo > C:\\out.txt")
        assert any(i.code == "unquoted_metacharacter" for i in issues)

    def test_backslash_does_not_mask_metacharacter(self) -> None:
        # On Windows, backslash is a path separator, not an escape. A pipe
        # following a backslash must still be flagged.
        issues = validate_parameters("C:\\folder\\|command")
        assert any(i.code == "unquoted_metacharacter" for i in issues)

    def test_variable_expansion(self) -> None:
        issues = validate_parameters("${env:USERNAME}")
        assert any(i.code == "variable_expansion" for i in issues)

    def test_environment_variable_path_warning(self) -> None:
        issues = validate_parameters("-Path %TEMP%\\payload.exe")
        assert any(
            i.code == "environment_variable_path" and i.severity == "warning"
            for i in issues
        )

    def test_length_limit(self) -> None:
        issues = validate_parameters("x" * 8192)
        assert any(i.code == "command_line_too_long" for i in issues)

    def test_newline_rejected(self) -> None:
        issues = validate_parameters("foo\nbar")
        assert any(i.code == "newline_in_parameters" for i in issues)


class TestQuoteParameter:
    def test_simple_value(self) -> None:
        assert quote_parameter("hello") == '"hello"'

    def test_value_with_spaces(self) -> None:
        assert quote_parameter("hello world") == '"hello world"'

    def test_value_with_quotes(self) -> None:
        assert quote_parameter('hello "world"') == '"hello ""world"""'

    def test_empty_value(self) -> None:
        assert quote_parameter("") == '""'


class TestIniSerialization:
    def test_legacy_round_trip(self) -> None:
        policy = ScriptPolicy(
            startup=(_entry(script_id="s1", original_name="a.bat", parameters="-q"),),
            shutdown=(
                _entry(
                    script_id="s2",
                    original_name="b.bat",
                    parameters="-Confirm:$false",
                ),
            ),
        )
        ini = serialize_script_policy_ini(policy, powershell=False)
        parsed = parse_script_policy_ini(ini, powershell=False)
        assert len(parsed.startup) == 1
        assert parsed.startup[0].original_name == "a.bat"
        assert parsed.startup[0].parameters == "-q"
        assert len(parsed.shutdown) == 1
        assert parsed.shutdown[0].original_name == "b.bat"

    def test_powershell_round_trip(self) -> None:
        policy = ScriptPolicy(
            powershell_startup=(
                _ps_entry(
                    script_id="p1",
                    original_name="x.ps1",
                    parameters="-Verbose",
                    no_profile=True,
                    non_interactive=True,
                    execution="asynchronous",
                ),
            ),
            run_logon_scripts_sync=True,
            legacy_scripts_first=False,
            powershell_order="run_windows_powershell_scripts_first",
        )
        ini = serialize_script_policy_ini(policy, powershell=True)
        parsed = parse_script_policy_ini(ini, powershell=True)
        assert len(parsed.powershell_startup) == 1
        entry = parsed.powershell_startup[0]
        assert entry.original_name == "x.ps1"
        assert entry.parameters == "-Verbose"
        assert entry.no_profile is True
        assert entry.non_interactive is True
        assert entry.execution == "asynchronous"
        assert parsed.run_logon_scripts_sync is True
        assert parsed.legacy_scripts_first is False
        assert parsed.powershell_order == "run_windows_powershell_scripts_first"

    def test_empty_sections(self) -> None:
        policy = ScriptPolicy()
        ini = serialize_script_policy_ini(policy, powershell=False)
        assert "[Startup]" in ini
        assert "[Shutdown]" in ini
        assert "[Logon]" in ini
        assert "[Logoff]" in ini
        parsed = parse_script_policy_ini(ini, powershell=False)
        assert parsed.startup == ()
        assert parsed.shutdown == ()
        assert parsed.logon == ()
        assert parsed.logoff == ()

    def test_missing_sections(self) -> None:
        parsed = parse_script_policy_ini("[Startup]\n0CmdLine=x.bat\n", powershell=False)
        assert len(parsed.startup) == 1
        assert parsed.shutdown == ()
        assert parsed.logon == ()
        assert parsed.logoff == ()


class TestExecutionPreview:
    def test_computer_side_runs_as_system(self) -> None:
        policy = ScriptPolicy(startup=(_entry(script_id="s1", original_name="a.bat"),))
        previews = preview_script_policy(policy, "computer")
        assert len(previews) == 1
        assert previews[0].runs_as == "SYSTEM"

    def test_user_side_runs_as_logged_on_user(self) -> None:
        policy = ScriptPolicy(
            logon=(_entry(script_id="s1", original_name="a.bat"),)
        )
        previews = preview_script_policy(policy, "user")
        assert previews[0].runs_as == "logged-on user"

    def test_async_warning(self) -> None:
        policy = ScriptPolicy(
            startup=(_entry(script_id="s1", execution="asynchronous"),)
        )
        previews = preview_script_policy(policy, "computer")
        assert any("asynchronous" in risk for risk in previews[0].risks)

    def test_powershell_preview_includes_bypass(self) -> None:
        policy = ScriptPolicy(
            powershell_startup=(_ps_entry(script_id="p1", original_name="x.ps1"),)
        )
        previews = preview_script_policy(policy, "computer")
        assert len(previews) == 1
        assert "powershell.exe" in previews[0].effective_command
        assert "ExecutionPolicy Bypass" in previews[0].effective_command

    def test_preview_uses_artifact_name(self, tmp_path: Path) -> None:
        store = ArtifactStore(str(tmp_path / "artifacts.db"))
        meta = store.store_artifact(b"# script", "renamed.bat")
        policy = ScriptPolicy(
            startup=(
                _entry(
                    script_id="s1",
                    artifact_id=meta.artifact_id,
                    original_name="original.bat",
                ),
            )
        )
        previews = preview_script_policy(policy, "computer", artifact_store=store)
        assert previews[0].effective_command.startswith("renamed.bat")
