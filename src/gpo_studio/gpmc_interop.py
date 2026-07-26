"""GPMC interoperability checks for Studio-authored GPOs and backups."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from .backup import _GUID_RE as _BACKUP_GUID_RE
from .model import GPO
from .validation import validate_gpo
from .wmi_filter import lint_wql

InteropCheckLevel = Literal["pass", "warning", "error"]


@dataclass(frozen=True, slots=True)
class InteropIssue:
    check: str                  # what was checked
    level: InteropCheckLevel
    message: str
    component: str = ""         # which GPO component is affected


@dataclass(frozen=True, slots=True)
class GpmcInteropReport:
    gpo_guid: str
    gpo_name: str
    issues: tuple[InteropIssue, ...]
    is_gpmc_editable: bool      # can GPMC edit this without normalization surprises?
    is_gpmc_importable: bool    # can GPMC import this?
    summary: str = ""


# GPMC imports backup directories; a single GPO normally stays under these.
_GPMC_SETTINGS_WARNING_THRESHOLD = 10000

# CSE GUIDs that Studio understands and can emit. Unknown CSEs are preserved in
# cse_metadata but block ready transition because GPMC may not round-trip them.
_KNOWN_CSE_GUIDS = frozenset({
    "{35378EAC-683F-11D2-A89A-00C04FBBCFA2}",  # Registry
    "{3125E937-EB16-4b4c-9934-544FC6D24D26}",  # GPP Groups
    "{A3CC7818-8A30-4e0c-91C5-A4EA4B5A8DAB}",  # GPP Registry
})


_GPO_GUID_RE = re.compile(
    r"^\{?[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\}?$"
)


def _issue(level: InteropCheckLevel, check: str, message: str, component: str = "") -> InteropIssue:
    return InteropIssue(level=level, check=check, message=message, component=component)


def check_gpmc_interop(gpo: GPO) -> GpmcInteropReport:
    """Check if a GPO is interoperable with GPMC."""
    issues: list[InteropIssue] = []

    # Identity / GUID
    if not _GPO_GUID_RE.match(gpo.guid):
        issues.append(
            _issue("error", "gpo_guid_format", f"GPO GUID is not valid: {gpo.guid!r}", "gpo")
        )

    # Registry settings
    for setting in gpo.settings:
        expected_hive = "HKLM" if setting.side == "computer" else "HKCU"
        if setting.hive != expected_hive:
            issues.append(
                _issue(
                    "error",
                    "registry_hive",
                    f"{setting.side.title()} setting {setting.id!r} uses {setting.hive!r}; "
                    f"GPMC expects {expected_hive}.",
                    f"settings/{setting.id}",
                )
            )
        if not setting.key.strip() or setting.key.startswith("\\") or setting.key.endswith("\\"):
            issues.append(
                _issue(
                    "error",
                    "registry_key_format",
                    f"Setting {setting.id!r} has an invalid registry key format.",
                    f"settings/{setting.id}",
                )
            )
        if setting.registry_type not in {
            "REG_SZ",
            "REG_EXPAND_SZ",
            "REG_BINARY",
            "REG_DWORD",
            "REG_MULTI_SZ",
            "REG_QWORD",
        }:
            issues.append(
                _issue(
                    "error",
                    "registry_value_type",
                    f"Setting {setting.id!r} uses unsupported registry type "
                    f"{setting.registry_type!r}.",
                    f"settings/{setting.id}",
                )
            )

    setting_count = len(gpo.settings)
    if setting_count > _GPMC_SETTINGS_WARNING_THRESHOLD:
        issues.append(
            _issue(
                "warning",
                "settings_count",
                f"GPO contains {setting_count} registry settings; "
                f"very large GPOs may exceed GPMC UI limits.",
                "settings",
            )
        )

    # GPP collections
    for collection in gpo.gpp_collections:
        scope = collection.scope
        if scope not in ("computer", "user"):
            issues.append(
                _issue(
                    "error",
                    "gpp_scope",
                    f"GPP collection has unsupported scope {scope!r}.",
                    "gpp_collections",
                )
            )
            continue
        for group in collection.groups:
            if not group.name.strip():
                issues.append(
                    _issue(
                        "error",
                        "gpp_group_name",
                        "GPP group has an empty name.",
                        f"gpp_collections/{scope}/groups",
                    )
                )
        for reg in collection.registry:
            if not reg.key.strip():
                issues.append(
                    _issue(
                        "error",
                        "gpp_registry_key",
                        "GPP registry item has an empty key.",
                        f"gpp_collections/{scope}/registry",
                    )
                )

    # Security filters
    for sf in gpo.security_filters:
        if sf.sid and not re.match(r"^S-\d+(?:-\d+)+$", sf.sid):
            issues.append(
                _issue(
                    "error",
                    "security_filter_sid",
                    f"Security filter {sf.id!r} has invalid SID format {sf.sid!r}.",
                    f"security_filters/{sf.id}",
                )
            )

    # WMI filter
    if gpo.wmi_filter is not None:
        wf = gpo.wmi_filter
        if not wf.name.strip():
            issues.append(
                _issue(
                    "error",
                    "wmi_filter_name",
                    "WMI filter name is empty.",
                    "wmi_filter/name",
                )
            )
        if not wf.query.strip():
            issues.append(
                _issue(
                    "warning",
                    "wmi_filter_query",
                    "WMI filter query is empty.",
                    "wmi_filter/query",
                )
            )
        else:
            for wql_issue in lint_wql(wf.query):
                issues.append(
                    _issue(
                        wql_issue.severity,
                        "wmi_filter_wql",
                        wql_issue.message,
                        "wmi_filter/query",
                    )
                )

    # Links
    _DN = re.compile(r"^(?:CN|OU|DC)=[^,=]+(?:,(?:CN|OU|DC)=[^,=]+)+$", re.IGNORECASE)
    for link in gpo.links:
        if not _DN.fullmatch(link.target.strip()):
            issues.append(
                _issue(
                    "error",
                    "link_target",
                    f"Link {link.id!r} target {link.target!r} is not a valid DN.",
                    f"links/{link.id}",
                )
            )

    # CSE metadata
    for entry in gpo.cse_metadata:
        if not _BACKUP_GUID_RE.match(entry.guid):
            issues.append(
                _issue(
                    "error",
                    "cse_guid_format",
                    f"CSE metadata entry has invalid GUID {entry.guid!r}.",
                    "cse_metadata",
                )
            )
        if entry.guid not in _KNOWN_CSE_GUIDS:
            issues.append(
                _issue(
                    "error",
                    "unknown_cse_guid",
                    f"Unknown CSE GUID {entry.guid!r}; GPMC may not preserve this extension.",
                    "cse_metadata",
                )
            )

    # Cross-check with deterministic Studio validation to surface anything that
    # would also break GPMC serialization.
    for v_issue in validate_gpo(gpo):
        if v_issue.severity == "error":
            level: InteropCheckLevel = "error"
        else:
            level = "warning"
        issues.append(
            _issue(
                level,
                f"studio_validation:{v_issue.code}",
                v_issue.message,
                v_issue.path,
            )
        )

    has_errors = any(i.level == "error" for i in issues)
    has_warnings = any(i.level == "warning" for i in issues)

    if has_errors:
        summary = "GPO has interoperability errors that should be fixed before GPMC import."
    elif has_warnings:
        summary = "GPO can be imported by GPMC but may produce normalization warnings."
    else:
        summary = "GPO is fully interoperable with GPMC."

    return GpmcInteropReport(
        gpo_guid=gpo.guid,
        gpo_name=gpo.name,
        issues=tuple(issues),
        is_gpmc_editable=not has_errors and not has_warnings,
        is_gpmc_importable=not has_errors,
        summary=summary,
    )


def check_backup_importable(backup_manifest: dict[str, Any]) -> GpmcInteropReport:
    """Check if a GPMC backup can be imported into Studio."""
    issues: list[InteropIssue] = []

    gpo_guid = backup_manifest.get("id", "")
    gpo_name = backup_manifest.get("name", "")

    required_fields = ("id", "name", "domain", "timestamp")
    for field in required_fields:
        if not backup_manifest.get(field):
            issues.append(
                _issue(
                    "error",
                    "missing_required_field",
                    f"Backup manifest is missing required field {field!r}.",
                    "manifest",
                )
            )

    if gpo_guid and not _BACKUP_GUID_RE.match(gpo_guid):
        issues.append(
            _issue(
                "error",
                "backup_guid_format",
                f"Backup GPO GUID {gpo_guid!r} is not valid.",
                "manifest/id",
            )
        )

    xml_files = backup_manifest.get("xml_files", {})
    if not isinstance(xml_files, dict):
        issues.append(
            _issue(
                "error",
                "xml_files_format",
                "Backup manifest xml_files must be a mapping.",
                "manifest/xml_files",
            )
        )
    else:
        for filename, content in xml_files.items():
            if not isinstance(filename, str) or not filename.lower().endswith(".xml"):
                issues.append(
                    _issue(
                        "warning",
                        "xml_filename",
                        f"Backup file {filename!r} does not look like an XML file.",
                        "manifest/xml_files",
                    )
                )
            if isinstance(content, bytes):
                if not content.lstrip().startswith(b"<"):
                    issues.append(
                        _issue(
                            "error",
                            "invalid_xml_content",
                            f"Backup file {filename!r} does not contain XML data.",
                            "manifest/xml_files",
                        )
                    )
                # Reject obviously binary content.
                if b"\x00" in content[:1024]:
                    issues.append(
                        _issue(
                            "error",
                            "binary_xml_content",
                            f"Backup file {filename!r} contains binary data.",
                            "manifest/xml_files",
                        )
                    )

    encrypted = backup_manifest.get("encrypted", False)
    if encrypted:
        issues.append(
            _issue(
                "error",
                "encrypted_backup",
                "Encrypted backups cannot be parsed offline.",
                "manifest",
            )
        )

    has_errors = any(i.level == "error" for i in issues)
    if has_errors:
        summary = "Backup cannot be safely imported into Studio."
    else:
        summary = "Backup appears importable into Studio."

    return GpmcInteropReport(
        gpo_guid=gpo_guid,
        gpo_name=gpo_name,
        issues=tuple(issues),
        is_gpmc_editable=False,
        is_gpmc_importable=not has_errors,
        summary=summary,
    )
