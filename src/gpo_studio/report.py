"""Deterministic inert-text policy review reports."""

from __future__ import annotations

from collections.abc import Iterable

from .canonical import policy_semantic_sha256, review_model_sha256
from .model import GPO
from .validation import validate_gpo


def _section(title: str, lines: Iterable[str]) -> list[str]:
    body = list(lines)
    return [title, "-" * len(title), *(body or ["(none)"]), ""]


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
            f"[{item.registry_type}, {item.action}] = {item.value!r}"
            for item in gpo.settings
        ),
    )
    lines += _section(
        "Links",
        (
            f"{item.target} (order={item.order}, enabled={item.enabled}, "
            f"enforced={item.enforced})"
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
            f"{collection.scope}: {len(collection.groups)} group item(s), "
            f"{len(collection.registry)} registry item(s)"
            for collection in gpo.gpp_collections
        ),
    )
    lines += _section(
        "Preserved extension content",
        (
            f"{entry.side}/{entry.guid}: {len(entry.files)} file(s)"
            for entry in gpo.cse_metadata
        ),
    )
    return "\n".join(lines).rstrip() + "\n"
