"""Deterministic inert-text policy review reports."""

from __future__ import annotations

from collections.abc import Iterable

from .backup_inventory import inventory_report_lines
from .canonical import policy_semantic_sha256, review_model_sha256
from .gpp import GppCollection
from .model import GPO
from .validation import validate_gpo


def _gpp_item_counts(collection: GppCollection) -> tuple[tuple[str, int], ...]:
    return (
        ("groups", len(collection.groups)),
        ("registry", len(collection.registry)),
        ("environment variables", len(collection.environment)),
        ("INI files", len(collection.ini_files)),
        ("regional options", len(collection.regional_options)),
        ("power options", len(collection.power_options)),
        ("devices", len(collection.devices)),
        ("folder options", len(collection.folder_options)),
        ("data sources", len(collection.data_sources)),
        ("drives", len(collection.drives)),
        ("files", len(collection.files)),
        ("folders", len(collection.folders)),
        ("network shares", len(collection.network_shares)),
        ("printers", len(collection.printers)),
        ("shortcuts", len(collection.shortcuts)),
        ("applications", len(collection.applications)),
        ("services", len(collection.services)),
        ("local users", len(collection.local_users)),
        ("local groups", len(collection.local_groups)),
        ("scheduled tasks", len(collection.scheduled_tasks)),
        ("immediate tasks", len(collection.immediate_tasks)),
    )


def _section(title: str, lines: Iterable[str]) -> list[str]:
    body = list(lines)
    return [title, "-" * len(title), *(body or ["(none)"]), ""]


def _format_value(value: str | int | list[str]) -> str:
    if isinstance(value, list):
        return "; ".join(value)
    return str(value)


def policy_report(gpo: GPO) -> str:
    """Return a stable plain-text summary suitable for a ticket or review."""

    issues = validate_gpo(gpo)
    lines = [
        "GPO Studio policy report",
        "========================",
        f"Name: {gpo.name}",
        f"GUID: {gpo.guid}",
        f"Revision: {gpo.revision}",
        f"Status: {gpo.status}",
        f"Domain: {gpo.domain}",
        f"Computer configuration: {'enabled' if gpo.computer_enabled else 'disabled'}",
        f"User configuration: {'enabled' if gpo.user_enabled else 'disabled'}",
        f"Policy semantic SHA-256: {policy_semantic_sha256(gpo)}",
        f"Review model SHA-256: {review_model_sha256(gpo)}",
        "",
    ]
    lines += _section("Description", [gpo.description] if gpo.description else [])
    lines += _section(
        "Validation",
        (
            f"[{issue.severity.upper()}] {issue.code} at {issue.path or '(policy)'}: "
            f"{issue.message}"
            for issue in issues
        ),
    )
    lines += _section(
        "Registry policy settings",
        (
            f"{item.side}/{item.hive} {item.key} :: {item.value_name or '(Default)'} "
            f"[{item.registry_type}, {item.action}] = {_format_value(item.value)}"
            for item in gpo.settings
        ),
    )
    lines += _section(
        "Links",
        (
            f"{item.target} (order={item.order}, enabled={item.enabled}, enforced={item.enforced})"
            for item in gpo.links
        ),
    )
    lines += _section(
        "Security filters",
        (
            f"{item.principal} ({item.permission}, {item.target_type}, "
            f"inheritable={item.inheritable}, sid={item.sid or '(unspecified)'})"
            for item in gpo.security_filters
        ),
    )
    lines += _section(
        "WMI filter",
        (
            [
                f"{gpo.wmi_filter.name} [{gpo.wmi_filter.language}]",
                gpo.wmi_filter.query,
            ]
            if gpo.wmi_filter
            else []
        ),
    )
    lines += _section(
        "Group Policy Preferences",
        (
            f"{collection.scope}: "
            + ", ".join(f"{count} {label} item(s)" for label, count in _gpp_item_counts(collection))
            for collection in gpo.gpp_collections
        ),
    )
    lines += _section(
        "Unmodeled extension files (metadata only)",
        (
            line
            for entry in gpo.cse_metadata
            for line in (
                f"{entry.side}/{entry.guid}: {len(entry.files)} file(s); original bytes not stored",
                *(
                    f"  {file.relative_path}: {file.size} bytes; SHA-256 {file.content_hash}"
                    for file in entry.files
                ),
            )
        ),
    )
    if gpo.backup_inventory is not None:
        lines += _section(
            "Imported Windows inventory (source snapshot)",
            inventory_report_lines(gpo.backup_inventory),
        )
    return "\n".join(lines).rstrip() + "\n"
