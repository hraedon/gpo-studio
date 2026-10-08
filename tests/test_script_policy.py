from __future__ import annotations

from gpo_studio.script_policy import (
    PowerShellScriptEntry,
    ScriptEntry,
    ScriptPolicy,
    preview_script_policy,
    quote_parameter,
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

    def test_preview_names_the_entry_by_its_original_name(self) -> None:
        """The preview reads the entry itself; no artifact store is consulted."""
        policy = ScriptPolicy(
            startup=(
                _entry(
                    script_id="s1",
                    artifact_id="b" * 64,
                    original_name="original.bat",
                    parameters="/q",
                ),
            )
        )
        previews = preview_script_policy(policy, "computer")
        assert previews[0].effective_command == "original.bat /q"


def test_the_pre_r2_ini_writer_and_parser_stay_deleted() -> None:
    """2026-10-07 ruling: the certified writer is export.gpmc_backup_bundle.

    The deleted pair wrote an unmeasured INI shape (a ``[Policy]`` section, a
    ``NoProfile``/``NonInteractive``/``ExecutionMode`` key per entry, comment
    stubs for empty triggers) that no Windows capture contains.
    """
    import gpo_studio.script_policy as script_policy

    assert not hasattr(script_policy, "serialize_script_policy_ini")
    assert not hasattr(script_policy, "parse_script_policy_ini")
