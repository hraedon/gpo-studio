"""Parse gpo-lens estate exports into GPO model objects."""

from __future__ import annotations

import re
from typing import Any

from .model import GPO, ValidationError, ValidationIssue
from .store import gpo_from_dict
from .validation import validate_gpo

MAX_ESTATE_GPO_COUNT = 1000
_MAX_JSON_NESTING_DEPTH = 64
_MAX_ESTATE_ITEM_COUNT = 10000
_GUID_RE = re.compile(
    r"^\{?[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\}?$"
)


def _check_nesting_depth(obj: Any, depth: int = 0, count: list[int] | None = None) -> None:
    if count is None:
        count = [0]
    count[0] += 1
    if count[0] > _MAX_ESTATE_ITEM_COUNT:
        raise ValidationError([
            ValidationIssue(
                severity="error",
                code="too_many_items",
                message=f"Estate import exceeds {_MAX_ESTATE_ITEM_COUNT} total JSON nodes.",
                path="gpos",
            )
        ])
    if depth > _MAX_JSON_NESTING_DEPTH:
        raise ValidationError([
            ValidationIssue(
                severity="error",
                code="json_nesting_too_deep",
                message=f"JSON nesting depth exceeds {_MAX_JSON_NESTING_DEPTH}.",
                path="gpos",
            )
        ])
    if isinstance(obj, dict):
        for v in obj.values():
            _check_nesting_depth(v, depth + 1, count)
    elif isinstance(obj, list):
        for item in obj:
            _check_nesting_depth(item, depth + 1, count)


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
    _check_nesting_depth(data)
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
    if len(gpos_raw) > MAX_ESTATE_GPO_COUNT:
        raise ValidationError([
            ValidationIssue(
                severity="error",
                code="too_many_gpos",
                message=f"Estate import exceeds maximum GPO count of {MAX_ESTATE_GPO_COUNT}.",
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
        original_guid = str(raw.get("guid", "")).strip()
        if not original_guid or not _GUID_RE.match(original_guid):
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="invalid_guid_format",
                    message=f"Invalid or missing GPO GUID: {original_guid!r}",
                    path="guid",
                )
            )
            continue
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
            "cse_metadata": raw.get("cse_metadata", []),
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
