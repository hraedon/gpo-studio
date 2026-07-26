"""Folder redirection model for GPO Studio.

Models the user-side folder redirection policy: per-folder redirection targets
(basic / advanced-by-group), per-rule paths, path safety helpers, and a
migration impact assessment. Emits ``User Shell Folders`` registry settings for
the folders that have a known value-name mapping.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal, assert_never

from .model import ValidationIssue

RedirectionFolder = Literal[
    "documents",
    "desktop",
    "appdata_roaming",
    "appdata_local",
    "start_menu",
    "pictures",
    "music",
    "videos",
    "downloads",
    "contacts",
    "links",
    "saved_games",
    "searches",
]

RedirectionTarget = Literal[
    "basic",          # redirect to a single location
    "advanced",       # redirect based on group membership
    "not_configured",  # no redirection
]

# Registry key where Windows stores redirected shell folder paths.
_USER_SHELL_FOLDERS_KEY = (
    r"HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
)

_MAX_PATH = 260
_MAX_EXTENDED_PATH = 32767

# Characters that are invalid in a Windows path (after stripping any \\?\ prefix).
_INVALID_PATH_CHARS = (
    frozenset('<>"|*?') | frozenset(chr(c) for c in range(32))
)

_USERNAME_VAR = "%USERNAME%"
_SYSTEM_VARS = ("%SYSTEMROOT%", "%WINDIR%")
_TEMP_VARS = ("%TEMP%", "%TMP%")

_MULTIPLE_BACKSLASH = re.compile(r"\\{2,}")


def _is_drive_root(path: str) -> bool:
    """Return True if *path* is a drive root like ``C:\\``."""
    return (
        len(path) == 3
        and path[0].isalpha()
        and path[1] == ":"
        and path[2] == "\\"
    )


def _folder_registry_value(folder: RedirectionFolder) -> str | None:
    """Map a redirection folder to its ``User Shell Folders`` value name.

    Returns None for folders that have no documented shell-folders mapping.
    """
    match folder:
        case "documents":
            return "Personal"
        case "desktop":
            return "Desktop"
        case "appdata_roaming":
            return "AppData"
        case "appdata_local":
            return "Local AppData"
        case "start_menu":
            return "Start Menu"
        case "pictures":
            return "My Pictures"
        case "music":
            return "My Music"
        case "videos":
            return "My Video"
        case "downloads":
            return "{374DE290-123F-4565-9164-39C4925E467B}"
        case "contacts" | "links" | "saved_games" | "searches":
            return None
        case _:
            assert_never(folder)


@dataclass(frozen=True, slots=True)
class RedirectionRule:
    """A single redirection target rule (used in advanced mode)."""

    group_sid: str = ""
    group_name: str = ""
    target_path: str = ""

    def validate(self) -> tuple[ValidationIssue, ...]:
        """Validate the rule's target path."""
        issues: list[ValidationIssue] = []
        rule_id = self.group_sid or self.group_name or "default"
        base_path = f"redirection_rule.{rule_id}.target_path"

        if not self.target_path:
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="empty_target_path",
                    message="target_path must not be empty",
                    path=base_path,
                )
            )
        else:
            if ".." in self.target_path:
                issues.append(
                    ValidationIssue(
                        severity="error",
                        code="target_path_traversal",
                        message="target_path contains '..' path traversal",
                        path=base_path,
                    )
                )
            # A local path with no environment variables is not roaming-capable.
            if not self.target_path.startswith("\\\\") and "%" not in self.target_path:
                issues.append(
                    ValidationIssue(
                        severity="warning",
                        code="target_path_not_unc",
                        message="target_path should be a UNC path for roaming",
                        path=base_path,
                    )
                )

        return tuple(issues)


@dataclass(frozen=True, slots=True)
class FolderRedirection:
    """Per-folder redirection configuration."""

    folder: RedirectionFolder
    target: RedirectionTarget = "not_configured"
    basic_path: str = ""
    rules: tuple[RedirectionRule, ...] = field(default_factory=tuple)
    grant_exclusive_rights: bool = True
    move_contents: bool = True
    remove_redirect_on_policy_removal: bool = False
    also_redirect_subfolders: bool = True

    def validate(self) -> tuple[ValidationIssue, ...]:
        """Validate folder redirection configuration."""
        issues: list[ValidationIssue] = []
        base_path = f"folder_redirection.{self.folder}"

        match self.target:
            case "not_configured":
                pass
            case "basic":
                if not self.basic_path:
                    issues.append(
                        ValidationIssue(
                            severity="error",
                            code="empty_basic_path",
                            message="basic_path must not be empty when target=basic",
                            path=f"{base_path}.basic_path",
                        )
                    )
                if self.rules:
                    issues.append(
                        ValidationIssue(
                            severity="warning",
                            code="rules_ignored_in_basic_mode",
                            message="rules are ignored when target=basic",
                            path=f"{base_path}.rules",
                        )
                    )
            case "advanced":
                if not self.rules:
                    issues.append(
                        ValidationIssue(
                            severity="error",
                            code="no_advanced_rules",
                            message="at least one rule is required when target=advanced",
                            path=f"{base_path}.rules",
                        )
                    )
                if self.basic_path:
                    issues.append(
                        ValidationIssue(
                            severity="warning",
                            code="basic_path_ignored",
                            message="basic_path is ignored when target=advanced",
                            path=f"{base_path}.basic_path",
                        )
                    )
            case _:
                assert_never(self.target)

        if self.basic_path and ".." in self.basic_path:
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="basic_path_traversal",
                    message="basic_path contains '..' path traversal",
                    path=f"{base_path}.basic_path",
                )
            )

        if not self.grant_exclusive_rights:
            issues.append(
                ValidationIssue(
                    severity="warning",
                    code="no_exclusive_rights",
                    message=(
                        "grant_exclusive_rights=False may allow other users to "
                        "access the redirected folder"
                    ),
                    path=f"{base_path}.grant_exclusive_rights",
                )
            )

        if not self.move_contents and self.remove_redirect_on_policy_removal:
            issues.append(
                ValidationIssue(
                    severity="warning",
                    code="orphaned_data_risk",
                    message=(
                        "move_contents=False with "
                        "remove_redirect_on_policy_removal=True may orphan data"
                    ),
                    path=f"{base_path}.move_contents",
                )
            )

        for rule in self.rules:
            issues.extend(rule.validate())

        return tuple(issues)

    def effective_path(self, username: str = _USERNAME_VAR) -> str:
        """Compute the effective path for *username*.

        Substitutes ``%USERNAME%`` in ``basic_path`` or the first rule's
        ``target_path``. Returns an empty string when not configured.
        """
        match self.target:
            case "not_configured":
                return ""
            case "basic":
                return self.basic_path.replace(_USERNAME_VAR, username)
            case "advanced":
                if self.rules:
                    return self.rules[0].target_path.replace(_USERNAME_VAR, username)
                return ""
            case _:
                assert_never(self.target)


@dataclass(frozen=True, slots=True)
class FolderRedirectionPolicy:
    """Complete folder redirection policy for a GPO (user side only)."""

    folders: tuple[FolderRedirection, ...] = field(default_factory=tuple)

    def get_folder(self, folder: RedirectionFolder) -> FolderRedirection | None:
        """Return the redirection config for *folder*, or None."""
        for entry in self.folders:
            if entry.folder == folder:
                return entry
        return None

    def configured_folders(self) -> tuple[FolderRedirection, ...]:
        """Return only folders that have redirection configured."""
        return tuple(f for f in self.folders if f.target != "not_configured")

    def validate(self) -> tuple[ValidationIssue, ...]:
        """Validate the whole policy."""
        issues: list[ValidationIssue] = []
        seen: set[str] = set()

        for entry in self.folders:
            if entry.folder in seen:
                issues.append(
                    ValidationIssue(
                        severity="error",
                        code="duplicate_folder",
                        message=f"duplicate folder {entry.folder!r}",
                        path=f"folder_redirection_policy.{entry.folder}",
                    )
                )
            seen.add(entry.folder)

        if not self.configured_folders():
            issues.append(
                ValidationIssue(
                    severity="warning",
                    code="empty_policy",
                    message="no folders are configured; policy has no effect",
                    path="folder_redirection_policy",
                )
            )

        for entry in self.folders:
            issues.extend(entry.validate())

        return tuple(issues)

    def to_registry_settings(self) -> tuple[tuple[str, str, str], ...]:
        """Convert to ``User Shell Folders`` registry settings.

        Returns tuples of ``(registry_path, value_name, value_data)``. Only
        configured folders with a known shell-folders mapping are emitted.
        """
        settings: list[tuple[str, str, str]] = []
        for entry in self.folders:
            match entry.target:
                case "not_configured":
                    continue
                case "basic" | "advanced":
                    value_name = _folder_registry_value(entry.folder)
                    if value_name is not None:
                        settings.append(
                            (_USER_SHELL_FOLDERS_KEY, value_name, entry.effective_path())
                        )
                case _:
                    assert_never(entry.target)
        return tuple(settings)


def validate_redirection_path(path: str) -> tuple[ValidationIssue, ...]:
    """Validate a folder redirection target path.

    Rules:
    - Must not be empty
    - Must not contain ".." (path traversal)
    - Must not contain invalid characters for Windows paths
    - UNC paths (\\\\server\\share) are preferred -> warn if local path
    - Must not reference %SYSTEMROOT% or %WINDIR% -> error (system folder)
    - Must not reference %TEMP% or %TMP% -> warning (temporary location)
    - Maximum length 260 chars (MAX_PATH) unless UNC with \\\\?\\ prefix
    """
    issues: list[ValidationIssue] = []
    base_path = "redirection_path"

    if not path:
        issues.append(
            ValidationIssue(
                severity="error",
                code="empty_path",
                message="path must not be empty",
                path=base_path,
            )
        )
        return tuple(issues)

    if ".." in path:
        issues.append(
            ValidationIssue(
                severity="error",
                code="path_traversal",
                message="path contains '..' path traversal",
                path=base_path,
            )
        )

    # Invalid characters (strip the \\?\ extended-length prefix before checking).
    check_path = path[4:] if path.startswith("\\\\?\\") else path
    found = _INVALID_PATH_CHARS.intersection(check_path)
    if found:
        issues.append(
            ValidationIssue(
                severity="error",
                code="invalid_path_characters",
                message="path contains invalid characters",
                path=base_path,
            )
        )

    upper = path.upper()
    if any(var in upper for var in _SYSTEM_VARS):
        issues.append(
            ValidationIssue(
                severity="error",
                code="system_folder_redirect",
                message="path must not reference %SYSTEMROOT% or %WINDIR%",
                path=base_path,
            )
        )

    if any(var in upper for var in _TEMP_VARS):
        issues.append(
            ValidationIssue(
                severity="warning",
                code="temp_location_redirect",
                message="path references a temporary location; data may be lost",
                path=base_path,
            )
        )

    if not path.startswith("\\\\"):
        issues.append(
            ValidationIssue(
                severity="warning",
                code="local_path",
                message="path is not a UNC path; local redirection limits roaming",
                path=base_path,
            )
        )

    is_extended = path.startswith("\\\\?\\")
    max_len = _MAX_EXTENDED_PATH if is_extended else _MAX_PATH
    if len(path) > max_len:
        issues.append(
            ValidationIssue(
                severity="error",
                code="path_too_long",
                message=f"path exceeds maximum length of {max_len} characters",
                path=base_path,
            )
        )

    return tuple(issues)


def normalize_redirection_path(path: str) -> str:
    """Normalize a redirection path.

    - Convert forward slashes to backslashes
    - Remove trailing backslash (unless root)
    - Collapse multiple consecutive backslashes
    """
    if not path:
        return path

    path = path.replace("/", "\\")
    prefix = ""
    if path.startswith("\\\\?\\"):
        prefix = "\\\\?\\"
        path = path[4:]
    elif path.startswith("\\\\"):
        prefix = "\\\\"
        path = path[2:]

    path = _MULTIPLE_BACKSLASH.sub(lambda m: "\\", path)

    if len(path) >= 2 and path.endswith("\\") and not _is_drive_root(path) and path != "\\":
        path = path[:-1]

    return prefix + path


@dataclass(frozen=True, slots=True)
class MigrationAssessment:
    """Migration impact assessment for a single folder."""

    folder: RedirectionFolder
    source_path: str
    target_path: str
    estimated_risk: Literal["low", "medium", "high"]
    warnings: tuple[str, ...] = ()


_RISK_RANK: dict[str, int] = {"low": 0, "medium": 1, "high": 2}


def _bump_risk(
    current: Literal["low", "medium", "high"],
    proposed: Literal["low", "medium", "high"],
) -> Literal["low", "medium", "high"]:
    """Return the higher of two risk levels."""
    return current if _RISK_RANK[current] >= _RISK_RANK[proposed] else proposed


def assess_redirection_migration(
    policy: FolderRedirectionPolicy,
    current_paths: dict[str, str] | None = None,
) -> tuple[MigrationAssessment, ...]:
    """Assess the migration impact of applying folder redirection.

    Rules:
    - Redirecting AppData -> high risk (application compatibility)
    - Redirecting to a network path -> medium risk (network dependency)
    - Redirecting Desktop or Documents -> medium risk (user-visible)
    - move_contents=False -> warning about orphaned data

    Note: the ``> 10GB estimated data`` rule cannot be applied offline because no
    size data is available; it is documented for completeness only.
    """
    paths = current_paths or {}
    assessments: list[MigrationAssessment] = []

    for entry in policy.configured_folders():
        folder = entry.folder
        target_path = entry.effective_path()
        source_path = paths.get(folder, "")
        risk: Literal["low", "medium", "high"] = "low"
        warnings: list[str] = []

        if folder in ("appdata_roaming", "appdata_local"):
            risk = _bump_risk(risk, "high")
            warnings.append(
                "redirecting AppData may cause application compatibility issues"
            )

        if target_path.startswith("\\\\"):
            risk = _bump_risk(risk, "medium")
            warnings.append("network target path; folder unavailable when offline")

        if folder in ("desktop", "documents"):
            risk = _bump_risk(risk, "medium")
            warnings.append(f"redirecting {folder} is user-visible")

        if not entry.move_contents:
            warnings.append(
                "move_contents is False; existing data will not be migrated"
            )

        assessments.append(
            MigrationAssessment(
                folder=folder,
                source_path=source_path,
                target_path=target_path,
                estimated_risk=risk,
                warnings=tuple(warnings),
            )
        )

    return tuple(assessments)
