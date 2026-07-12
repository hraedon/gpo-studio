"""Parse gpo-lens estate exports into GPO model objects."""

from __future__ import annotations

from typing import Any

from .model import GPO, ValidationError, ValidationIssue
from .store import gpo_from_dict
from .validation import validate_gpo


def parse_estate(data: dict[str, Any]) -> list[GPO]:
    if data.get("kind") != "gpo-lens-estate":
        raise ValidationError([
            ValidationIssue(
                severity="error",
                code="invalid_estate_kind",
                message="Estate JSON must have kind 'gpo-lens-estate'.",
                path="kind",
            )
        ])
    gpos_raw = data.get("gpos", [])
    if not isinstance(gpos_raw, list):
        raise ValidationError([
            ValidationIssue(
                severity="error",
                code="invalid_gpos_field",
                message="The 'gpos' field must be a list.",
                path="gpos",
            )
        ])
    gpos: list[GPO] = []
    issues: list[ValidationIssue] = []
    for raw in gpos_raw:
        if not isinstance(raw, dict):
            raise ValidationError([
                ValidationIssue(
                    severity="error",
                    code="invalid_gpo_entry",
                    message="Each GPO entry must be a JSON object.",
                    path="gpos",
                )
            ])
        original_guid = str(raw.get("guid", ""))
        gpo_dict: dict[str, Any] = {
            "guid": original_guid,
            "name": raw.get("display_name", ""),
            "description": raw.get("description", ""),
            "domain": raw.get("domain", "studio.local"),
            "computer_enabled": raw.get("computer_enabled", True),
            "user_enabled": raw.get("user_enabled", True),
            "settings": raw.get("settings", []),
            "links": raw.get("links", []),
            "security_filters": raw.get("security_filters", []),
            "wmi_filter": raw.get("wmi_filter"),
            "status": "archived",
            "source_guid": original_guid,
        }
        gpo = gpo_from_dict(gpo_dict)
        gpo_issues = [i for i in validate_gpo(gpo) if i.severity == "error"]
        if gpo_issues:
            issues.extend(gpo_issues)
        gpos.append(gpo)
    if issues:
        raise ValidationError(issues)
    return gpos
