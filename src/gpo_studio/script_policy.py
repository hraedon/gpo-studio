"""Script policy model and INI serialization for GPO startup/shutdown/logon/logoff scripts.

Implements the domain model for both legacy (batch/VBScript/JScript) and
PowerShell script policies, plus the Windows INI formats used in SYSVOL:

* ``scripts.ini`` for legacy scripts.
* ``psscripts.ini`` for PowerShell scripts.

The editor never writes to SYSVOL directly; these helpers produce the content
that is emitted as a reviewable artifact by the publication adapter.
"""

from __future__ import annotations

import configparser
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, assert_never

from .model import ValidationIssue

if TYPE_CHECKING:
    from .artifact_store import ArtifactStore

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
# INI serialization / parsing
# ---------------------------------------------------------------------------

_INI_SECTION_NAMES: dict[ScriptType, str] = {
    "startup": "Startup",
    "shutdown": "Shutdown",
    "logon": "Logon",
    "logoff": "Logoff",
}

_ORDER_TO_INI: dict[PowerShellExecutionOrder, str] = {
    "not_configured": "NotConfigured",
    "run_windows_powershell_scripts_first": "RunPowerShellFirst",
    "run_windows_powershell_scripts_last": "RunPowerShellLast",
}
_INI_TO_ORDER: dict[str, PowerShellExecutionOrder] = {
    v: k for k, v in _ORDER_TO_INI.items()
}


def _serialize_legacy_entries(entries: tuple[ScriptEntry, ...]) -> list[tuple[str, str]]:
    lines: list[tuple[str, str]] = []
    for idx, entry in enumerate(entries):
        lines.append((f"{idx}CmdLine", entry.original_name))
        lines.append((f"{idx}Parameters", entry.parameters))
    return lines


def _serialize_powershell_entries(
    entries: tuple[PowerShellScriptEntry, ...],
) -> list[tuple[str, str]]:
    lines: list[tuple[str, str]] = []
    for idx, entry in enumerate(entries):
        lines.append((f"{idx}CmdLine", entry.original_name))
        lines.append((f"{idx}Parameters", entry.parameters))
        lines.append((f"{idx}NoProfile", "1" if entry.no_profile else "0"))
        lines.append((f"{idx}NonInteractive", "1" if entry.non_interactive else "0"))
        if entry.execution == "asynchronous":
            lines.append((f"{idx}ExecutionMode", "1"))
        elif entry.execution == "synchronous":
            lines.append((f"{idx}ExecutionMode", "0"))
    return lines


def serialize_script_policy_ini(
    policy: ScriptPolicy, powershell: bool = False
) -> str:
    """Serialize a script policy to Windows INI text.

    *powershell* controls whether legacy or PowerShell entries are emitted.
    """
    lines: list[str] = []

    for script_type in _SCRIPT_TYPES:
        section = _INI_SECTION_NAMES[script_type]
        lines.append(f"[{section}]")
        if powershell:
            entries = getattr(policy, f"powershell_{script_type}")
            pairs = _serialize_powershell_entries(entries)
        else:
            entries = getattr(policy, script_type)
            pairs = _serialize_legacy_entries(entries)
        if pairs:
            lines.extend(f"{key}={value}" for key, value in pairs)
        else:
            lines.append("; no scripts configured")
        lines.append("")

    if powershell:
        lines.append("[Policy]")
        lines.append(f"RunLogonScriptsSync={1 if policy.run_logon_scripts_sync else 0}")
        lines.append(f"RunLogoffScriptsSync={1 if policy.run_logoff_scripts_sync else 0}")
        lines.append(f"LegacyScriptsFirst={1 if policy.legacy_scripts_first else 0}")
        lines.append(f"PowerShellOrder={_ORDER_TO_INI[policy.powershell_order]}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _parse_entry_key(key: str) -> tuple[int, str] | None:
    """Parse a numbered key like ``0CmdLine`` into (0, 'CmdLine')."""
    match = re.match(r"^(\d+)([A-Za-z]+)$", key)
    if not match:
        return None
    return int(match.group(1)), match.group(2)


def _parse_execution_mode(raw: str) -> ScriptExecution:
    match raw:
        case "1":
            return "asynchronous"
        case "0" | "":
            return "synchronous"
        case _:
            return "synchronous"


def _parse_bool(raw: str) -> bool:
    return raw.strip().lower() in ("1", "true", "yes")


def parse_script_policy_ini(ini_text: str, powershell: bool = False) -> ScriptPolicy:
    """Parse a Windows scripts/psscripts INI into a ScriptPolicy."""
    parser = configparser.ConfigParser(
        delimiters=("=",),
        comment_prefixes=(";",),
        empty_lines_in_values=False,
    )
    parser.optionxform = lambda option: option  # type: ignore[assignment,method-assign]  # preserve key case
    parser.read_string(ini_text)

    sections: dict[ScriptType, str] = {
        "startup": "Startup",
        "shutdown": "Shutdown",
        "logon": "Logon",
        "logoff": "Logoff",
    }

    legacy_entries: dict[ScriptType, list[ScriptEntry]] = {
        t: [] for t in _SCRIPT_TYPES
    }
    ps_entries: dict[ScriptType, list[PowerShellScriptEntry]] = {
        t: [] for t in _SCRIPT_TYPES
    }

    for script_type, section in sections.items():
        if not parser.has_section(section):
            continue
        entries: dict[int, dict[str, str]] = {}
        for key, value in parser.items(section):
            parsed = _parse_entry_key(key)
            if parsed is None:
                continue
            idx, prop = parsed
            entries.setdefault(idx, {})[prop] = value

        for idx in sorted(entries):
            props = entries[idx]
            cmdline = props.get("CmdLine", "")
            parameters = props.get("Parameters", "")
            if powershell:
                ps_entries[script_type].append(
                    PowerShellScriptEntry(
                        script_id=f"ps-{script_type}-{idx}",
                        artifact_id="",
                        original_name=cmdline,
                        parameters=parameters,
                        order=idx + 1,
                        script_type=script_type,
                        execution=_parse_execution_mode(props.get("ExecutionMode", "0")),
                        no_profile=_parse_bool(props.get("NoProfile", "0")),
                        non_interactive=_parse_bool(props.get("NonInteractive", "1")),
                    )
                )
            else:
                legacy_entries[script_type].append(
                    ScriptEntry(
                        script_id=f"{script_type}-{idx}",
                        artifact_id="",
                        original_name=cmdline,
                        parameters=parameters,
                        order=idx + 1,
                        script_type=script_type,
                    )
                )

    kwargs: dict[str, tuple[ScriptEntry, ...] | tuple[PowerShellScriptEntry, ...]] = {}
    for script_type in _SCRIPT_TYPES:
        if powershell:
            kwargs[f"powershell_{script_type}"] = tuple(ps_entries[script_type])
        else:
            kwargs[script_type] = tuple(legacy_entries[script_type])

    run_logon_scripts_sync = False
    run_logoff_scripts_sync = False
    legacy_scripts_first = True
    powershell_order: PowerShellExecutionOrder = "not_configured"

    if powershell and parser.has_section("Policy"):
        run_logon_scripts_sync = _parse_bool(
            parser.get("Policy", "RunLogonScriptsSync", fallback="0")
        )
        run_logoff_scripts_sync = _parse_bool(
            parser.get("Policy", "RunLogoffScriptsSync", fallback="0")
        )
        legacy_scripts_first = _parse_bool(
            parser.get("Policy", "LegacyScriptsFirst", fallback="1")
        )
        order_raw = parser.get("Policy", "PowerShellOrder", fallback="NotConfigured")
        powershell_order = _INI_TO_ORDER.get(order_raw, "not_configured")

    return ScriptPolicy(
        run_logon_scripts_sync=run_logon_scripts_sync,
        run_logoff_scripts_sync=run_logoff_scripts_sync,
        legacy_scripts_first=legacy_scripts_first,
        powershell_order=powershell_order,
        **kwargs,  # type: ignore[arg-type]
    )


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


def _artifact_name_or_fallback(
    artifact_store: ArtifactStore | None, artifact_id: str, fallback: str
) -> str:
    if not artifact_store or not artifact_id:
        return fallback
    artifact = artifact_store.get_artifact(artifact_id)
    return artifact.metadata.original_name if artifact else fallback


def preview_script_policy(
    policy: ScriptPolicy,
    side: Literal["computer", "user"],
    artifact_store: ArtifactStore | None = None,
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
            name = _artifact_name_or_fallback(
                artifact_store, entry.artifact_id, entry.original_name
            )
            effective_command = f"{name} {entry.parameters}".strip()
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
            name = _artifact_name_or_fallback(
                artifact_store, entry.artifact_id, entry.original_name
            )
            effective_command = (
                f"powershell.exe -ExecutionPolicy Bypass -File {name} "
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
