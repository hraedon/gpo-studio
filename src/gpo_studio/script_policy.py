"""Script policy model for GPO startup/shutdown/logon/logoff scripts.

Implements the domain model for both legacy (batch/VBScript/JScript) and
PowerShell script policies, with parameter validation and an execution
preview.

This module writes no INI. The certified writer for the native
``scripts.ini`` / ``psscripts.ini`` byte shape is
``gpo_studio.export.gpmc_backup_bundle(gpo, scripts=...)``, which the Plan 034
Scripts metadata lane measures. The pre-R2 serializer and parser that used to
live here wrote an unmeasured shape and were deleted by the 2026-10-07 operator
ruling (``docs/direction-2026-10-07-plan-034-completion.md``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal, assert_never

from .model import ValidationIssue

ScriptType = Literal["startup", "shutdown", "logon", "logoff"]
ScriptExecution = Literal["synchronous", "asynchronous"]
PowerShellExecutionOrder = Literal[
    "not_configured",
    "run_windows_powershell_scripts_first",
    "run_windows_powershell_scripts_last",
]

_SCRIPT_TYPES: tuple[ScriptType, ...] = ("startup", "shutdown", "logon", "logoff")

# Windows command-line length limit (CreateProcess maximum).
_MAX_COMMAND_LINE_LENGTH = 8191

# Shell metacharacters that must not appear unquoted/escaped in parameters.
_SHELL_METACHARS = frozenset("|&;><`")

# Environment-variable patterns that are dangerous when expanded by cmd.exe.
_BLOCKED_ENV_PATTERNS = (r"%TEMP%", r"%TMP%", r"%APPDATA%", r"%LOCALAPPDATA%")


@dataclass(frozen=True, slots=True)
class ScriptEntry:
    script_id: str
    artifact_id: str
    original_name: str
    parameters: str = ""
    order: int = 1
    script_type: ScriptType = "startup"
    execution: ScriptExecution = "synchronous"
    timeout_seconds: int = 0

    def validate(self) -> tuple[ValidationIssue, ...]:
        """Validate the script entry."""
        issues: list[ValidationIssue] = []
        if not self.artifact_id:
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="empty_artifact_id",
                    message="artifact_id must not be empty",
                    path=f"script.{self.script_id}.artifact_id",
                )
            )
        if not self.original_name:
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="empty_original_name",
                    message="original_name must not be empty",
                    path=f"script.{self.script_id}.original_name",
                )
            )
        if self.timeout_seconds < 0:
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="negative_timeout",
                    message="timeout_seconds must be non-negative",
                    path=f"script.{self.script_id}.timeout_seconds",
                )
            )
        issues.extend(
            ValidationIssue(
                severity=issue.severity,
                code=issue.code,
                message=issue.message,
                path=f"script.{self.script_id}.parameters",
            )
            for issue in validate_parameters(self.parameters)
        )
        return tuple(issues)


@dataclass(frozen=True, slots=True)
class PowerShellScriptEntry:
    script_id: str
    artifact_id: str
    original_name: str
    parameters: str = ""
    order: int = 1
    script_type: ScriptType = "startup"
    execution: ScriptExecution = "synchronous"
    timeout_seconds: int = 0
    no_profile: bool = False
    non_interactive: bool = True

    def validate(self) -> tuple[ValidationIssue, ...]:
        """Validate the PowerShell script entry."""
        issues: list[ValidationIssue] = []
        if not self.artifact_id:
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="empty_artifact_id",
                    message="artifact_id must not be empty",
                    path=f"powershell_script.{self.script_id}.artifact_id",
                )
            )
        if not self.original_name:
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="empty_original_name",
                    message="original_name must not be empty",
                    path=f"powershell_script.{self.script_id}.original_name",
                )
            )
        if self.timeout_seconds < 0:
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="negative_timeout",
                    message="timeout_seconds must be non-negative",
                    path=f"powershell_script.{self.script_id}.timeout_seconds",
                )
            )
        issues.extend(
            ValidationIssue(
                severity=issue.severity,
                code=issue.code,
                message=issue.message,
                path=f"powershell_script.{self.script_id}.parameters",
            )
            for issue in validate_parameters(self.parameters)
        )
        return tuple(issues)


@dataclass(frozen=True, slots=True)
class ScriptPolicy:
    """Complete script policy for one GPO side (computer or user)."""

    startup: tuple[ScriptEntry, ...] = field(default_factory=tuple)
    shutdown: tuple[ScriptEntry, ...] = field(default_factory=tuple)
    logon: tuple[ScriptEntry, ...] = field(default_factory=tuple)
    logoff: tuple[ScriptEntry, ...] = field(default_factory=tuple)
    powershell_startup: tuple[PowerShellScriptEntry, ...] = field(default_factory=tuple)
    powershell_shutdown: tuple[PowerShellScriptEntry, ...] = field(default_factory=tuple)
    powershell_logon: tuple[PowerShellScriptEntry, ...] = field(default_factory=tuple)
    powershell_logoff: tuple[PowerShellScriptEntry, ...] = field(default_factory=tuple)
    powershell_order: PowerShellExecutionOrder = "not_configured"
    run_logon_scripts_sync: bool = False
    run_logoff_scripts_sync: bool = False
    legacy_scripts_first: bool = True

    def scripts_for_type(
        self, script_type: ScriptType
    ) -> tuple[ScriptEntry | PowerShellScriptEntry, ...]:
        """Return all scripts (legacy + PowerShell) for *script_type*, ordered."""
        legacy = getattr(self, script_type)
        ps = getattr(self, f"powershell_{script_type}")
        combined = (
            list(legacy) + list(ps)
            if self.legacy_scripts_first
            else list(ps) + list(legacy)
        )
        return tuple(sorted(combined, key=lambda entry: entry.order))

    def validate(self) -> tuple[ValidationIssue, ...]:
        """Validate the complete policy."""
        issues: list[ValidationIssue] = []

        for script_type in _SCRIPT_TYPES:
            entries = getattr(self, script_type)
            ps_entries = getattr(self, f"powershell_{script_type}")

            seen_artifacts: set[str] = set()
            seen_orders: set[int] = set()
            for entry in entries:
                issues.extend(entry.validate())
                if entry.artifact_id in seen_artifacts:
                    issues.append(
                        ValidationIssue(
                            severity="warning",
                            code="duplicate_artifact",
                            message=(
                                f"script {script_type} references artifact_id "
                                f"{entry.artifact_id} more than once"
                            ),
                            path=f"policy.{script_type}",
                        )
                    )
                if entry.order in seen_orders:
                    issues.append(
                        ValidationIssue(
                            severity="error",
                            code="duplicate_order",
                            message=(
                                f"order value {entry.order} is not unique within "
                                f"{script_type} scripts"
                            ),
                            path=f"policy.{script_type}",
                        )
                    )
                seen_artifacts.add(entry.artifact_id)
                seen_orders.add(entry.order)

            seen_ps_artifacts: set[str] = set()
            seen_ps_orders: set[int] = set()
            for entry in ps_entries:
                issues.extend(entry.validate())
                if entry.artifact_id in seen_ps_artifacts:
                    issues.append(
                        ValidationIssue(
                            severity="warning",
                            code="duplicate_artifact",
                            message=(
                                f"powershell script {script_type} references "
                                f"artifact_id {entry.artifact_id} more than once"
                            ),
                            path=f"policy.powershell_{script_type}",
                        )
                    )
                if entry.order in seen_ps_orders:
                    issues.append(
                        ValidationIssue(
                            severity="error",
                            code="duplicate_order",
                            message=(
                                f"order value {entry.order} is not unique within "
                                f"powershell {script_type} scripts"
                            ),
                            path=f"policy.powershell_{script_type}",
                        )
                    )
                seen_ps_artifacts.add(entry.artifact_id)
                seen_ps_orders.add(entry.order)

                if entry.execution == "asynchronous" and entry.timeout_seconds > 0:
                    issues.append(
                        ValidationIssue(
                            severity="warning",
                            code="async_timeout_ignored",
                            message=(
                                f"PowerShell script {entry.script_id} is asynchronous; "
                                "timeout_seconds is ignored"
                            ),
                            path=f"policy.powershell_{script_type}.{entry.script_id}",
                        )
                    )

        return tuple(issues)


def _has_unquoted_metacharacters(value: str) -> bool:
    """Return True if *value* contains an unquoted shell metacharacter.

    On Windows, backslash is a path separator, not an escape character (cmd.exe
    uses ``^`` and PowerShell uses backtick for escaping), so it is treated as a
    normal character here.
    """
    in_single = False
    in_double = False
    for ch in value:
        if ch == '"' and not in_single:
            in_double = not in_double
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
            continue
        if ch in _SHELL_METACHARS and not in_single and not in_double:
            return True
    return False


def validate_parameters(parameters: str) -> tuple[ValidationIssue, ...]:
    """Validate script parameters for command-line safety."""
    issues: list[ValidationIssue] = []

    if len(parameters) > _MAX_COMMAND_LINE_LENGTH:
        issues.append(
            ValidationIssue(
                severity="error",
                code="command_line_too_long",
                message=(
                    f"parameters exceed {_MAX_COMMAND_LINE_LENGTH} character "
                    "Windows command-line limit"
                ),
                path="parameters",
            )
        )

    if "\n" in parameters or "\r" in parameters:
        issues.append(
            ValidationIssue(
                severity="error",
                code="newline_in_parameters",
                message="parameters must not contain newline characters",
                path="parameters",
            )
        )

    if _has_unquoted_metacharacters(parameters):
        issues.append(
            ValidationIssue(
                severity="error",
                code="unquoted_metacharacter",
                message="parameters contain an unquoted shell metacharacter",
                path="parameters",
            )
        )

    if re.search(r"\$\{[^}]*\}", parameters):
        issues.append(
            ValidationIssue(
                severity="error",
                code="variable_expansion",
                message="parameters must not contain ${...} variable expansion",
                path="parameters",
            )
        )

    blocked_env = [p for p in _BLOCKED_ENV_PATTERNS if p.lower() in parameters.lower()]
    if blocked_env:
        issues.append(
            ValidationIssue(
                severity="warning",
                code="environment_variable_path",
                message=(
                    f"parameters reference environment variables that expand to "
                    f"paths: {', '.join(blocked_env)}"
                ),
                path="parameters",
            )
        )

    return tuple(issues)


def quote_parameter(value: str) -> str:
    """Safely quote a parameter value for use in a command line.

    Wraps the value in double quotes and escapes internal double quotes by
    doubling them, which is the convention expected by Windows CommandLineToArgvW
    and most shells.
    """
    if not value:
        return '""'
    escaped = value.replace('"', '""')
    return f'"{escaped}"'


# ---------------------------------------------------------------------------
# Execution preview
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ScriptExecutionPreview:
    script_id: str
    original_name: str
    script_type: ScriptType
    execution: ScriptExecution
    effective_command: str
    runs_as: str
    trigger: str
    risks: tuple[str, ...] = ()


def preview_script_policy(
    policy: ScriptPolicy,
    side: Literal["computer", "user"],
) -> tuple[ScriptExecutionPreview, ...]:
    """Generate execution previews for every script in *policy*."""
    match side:
        case "computer":
            runs_as = "SYSTEM"
        case "user":
            runs_as = "logged-on user"
        case _:
            assert_never(side)
    previews: list[ScriptExecutionPreview] = []

    def _execution_risks(execution: ScriptExecution, timeout: int) -> list[str]:
        risks: list[str] = []
        match execution:
            case "asynchronous":
                risks.append(
                    "script runs asynchronously; success/failure is not awaited"
                )
                if timeout > 0:
                    risks.append("timeout is ignored for asynchronous execution")
            case "synchronous":
                pass
            case _:
                assert_never(execution)
        if timeout == 0:
            risks.append("no timeout configured")
        return risks

    for script_type in _SCRIPT_TYPES:
        trigger = script_type.capitalize()
        for entry in getattr(policy, script_type):
            risks = _execution_risks(entry.execution, entry.timeout_seconds)
            effective_command = f"{entry.original_name} {entry.parameters}".strip()
            previews.append(
                ScriptExecutionPreview(
                    script_id=entry.script_id,
                    original_name=entry.original_name,
                    script_type=entry.script_type,
                    execution=entry.execution,
                    effective_command=effective_command,
                    runs_as=runs_as,
                    trigger=trigger,
                    risks=tuple(risks),
                )
            )

        for entry in getattr(policy, f"powershell_{script_type}"):
            risks = _execution_risks(entry.execution, entry.timeout_seconds)
            if not entry.no_profile:
                risks.append("PowerShell profile is loaded")
            if not entry.non_interactive:
                risks.append("PowerShell runs in interactive mode")
            effective_command = (
                f"powershell.exe -ExecutionPolicy Bypass -File {entry.original_name} "
                f"{entry.parameters}".strip()
            )
            previews.append(
                ScriptExecutionPreview(
                    script_id=entry.script_id,
                    original_name=entry.original_name,
                    script_type=entry.script_type,
                    execution=entry.execution,
                    effective_command=effective_command,
                    runs_as=runs_as,
                    trigger=trigger,
                    risks=tuple(risks),
                )
            )

    return tuple(previews)
