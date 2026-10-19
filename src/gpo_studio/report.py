"""Deterministic inert-text policy review reports."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from .backup_inventory import inventory_report_lines
from .canonical import policy_semantic_sha256, review_model_sha256
from .emit_integrity import escape_line
from .fdeploy import (
    FDEPLOY_POLICY_PATH,
    FdeployDocument,
    fdeploy_report_lines,
    native_digest,
    validate_fdeploy,
)
from .gpp import GppCollection, has_refused_content, raw_records
from .model import GPO, ValidationIssue
from .report_parity import ReportParityError, studio_inventory
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


def _inventory_lines(gpo: GPO) -> list[str]:
    """The inventory the report-parity lane compares with Get-GPOReport.

    The report is the diagnostic exception among a GPO's artifacts: it renders
    for stored data every content export refuses, so it must never fail on it
    (DeepSeek final-r16 B1). Stored content a current rule refuses is not
    written to inventory it -- the validation section names it and the remedy
    -- and any writer refusal (`egress.writer_errors`: `GppError`, the ILT
    codec's `IltError` and the rest) is the same "unavailable" note, with its
    reason and never the stored bytes.
    """
    if any(has_refused_content(collection) for collection in gpo.gpp_collections):
        return ["(inventory unavailable: stored preferences are refused; see Validation)"]
    if any(issue.severity == "error" for issue in validate_gpo(gpo)):
        # The inventory is written by the preference writers, which assume a
        # valid GPO (a closed set holds, a value has its type): never run them
        # on one validation refuses (final-r18).
        return ["(inventory unavailable: the GPO has validation errors; see Validation)"]
    from .egress import sanitized, writer_errors

    refusals: tuple[type[BaseException], ...] = (ReportParityError, *writer_errors())
    try:
        inventory = studio_inventory(gpo)
    except refusals as error:
        # Only the writers' named refusals (final-r18): anything else is a
        # defect, and fails as one.
        return [f"(inventory unavailable: {sanitized(error)})"]
    return [
        line
        for family in inventory.families
        for line in (
            f"{family.side}/{family.family}: {len(family.items)} item(s)",
            *(f"  {item.label()}" for item in family.items),
        )
    ]


def _format_value(value: str | int | list[str]) -> str:
    if isinstance(value, list):
        return "; ".join(value)
    return str(value)


def _fdeploy_lines(document: FdeployDocument) -> list[str]:
    """The imported Folder Redirection file, rendered for review (WI-068).

    Opens with the path, size and SHA-256 of the native bytes so the section
    joins to the same file's row in the imported source inventory, says what
    Studio will and will not do with it, then hands the body to
    ``fdeploy_report_lines`` -- the one renderer the endpoint also uses -- and
    closes with ``validate_fdeploy``'s structural findings.
    """
    digest, size = native_digest(document)
    lines = [
        f"Source: {FDEPLOY_POLICY_PATH}: {size} bytes; SHA-256 {digest}",
        "Read only: imported from the backup; Studio has no writer for this file "
        "(WI-066), so it is not published or exported.",
        *fdeploy_report_lines(document),
    ]
    lines += [
        f"Structural [{issue.severity.upper()}] {issue.code} at {issue.path}: {issue.message}"
        for issue in validate_fdeploy(document)
    ]
    return lines


def _digest(function: Callable[[GPO], str], gpo: GPO) -> str:
    """A digest, or why a legacy GPO has none: a stored lone surrogate cannot
    be encoded, so it cannot be hashed, and the report still renders and names
    the field to repair (DeepSeek final-r10 N5)."""
    try:
        return function(gpo)
    except UnicodeError:
        return "(none: stored text cannot be encoded; see Validation)"


def policy_report(gpo: GPO) -> str:
    """Return a stable plain-text summary suitable for a ticket or review.

    For a GPO with a validation error -- the report is the one artifact such a
    GPO gets -- no writer runs on its retained records: the digests read them
    raw (`gpp.raw_records`, DeepSeek final-r19 B1).
    """
    issues = validate_gpo(gpo)
    with raw_records(any(issue.severity == "error" for issue in issues)):
        return _policy_report(gpo, issues)


def _policy_report(gpo: GPO, issues: list[ValidationIssue]) -> str:
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
        f"Policy semantic SHA-256: {_digest(policy_semantic_sha256, gpo)}",
        f"Review model SHA-256: {_digest(review_model_sha256, gpo)}",
        "",
    ]
    # A description is the one multi-line value: each of its lines is a line.
    lines += _section(
        "Description", gpo.description.split("\n") if gpo.description else []
    )
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
    lines += _section("Settings inventory (Windows report families)", _inventory_lines(gpo))
    if gpo.fdeploy is not None:
        lines += _section("Folder Redirection (fdeploy1.ini)", _fdeploy_lines(gpo.fdeploy))
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
    # Every value is one report line: a control, format or separator character
    # in one is written as a visible \uXXXX, so no value can start a line that
    # reads as the report's own (final-r8: every writer checks what it writes).
    return "\n".join(escape_line(line) for line in lines).rstrip() + "\n"
