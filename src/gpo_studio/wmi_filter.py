"""WMI filter parsing, linting, and loopback helpers for offline authoring."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal, assert_never, cast, get_args

from .model import ValidationError, ValidationIssue

WmiQueryGroup = tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WqlIssue:
    severity: Literal["error", "warning"]
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class WmiFilterDetail:
    id: str
    name: str
    namespace: str = "root\\cimv2"
    query_groups: tuple[WmiQueryGroup, ...] = field(default_factory=tuple)
    description: str = ""
    owner_sid: str = ""
    references: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class LoopbackConfig:
    mode: Literal["disabled", "merge", "replace"] = "disabled"


def _strip_string_literals(query: str) -> tuple[str, bool]:
    """Return the query with string literals replaced by spaces.

    The second return value is True when the last string literal is not
    terminated.  WQL string literals are wrapped in double quotes; backslash
    is not an escape character in WQL, so a literal ends at the next unescaped
    double quote.  This helper lets syntactic checks ignore content inside
    literals.
    """
    result = list(query)
    unterminated = False
    i = 0
    while i < len(query):
        if query[i] != '"':
            i += 1
            continue
        start = i
        i += 1
        while i < len(query) and query[i] != '"':
            i += 1
        if i >= len(query):
            unterminated = True
            end = len(query) - 1
        else:
            end = i
            i += 1
        for j in range(start, end + 1):
            result[j] = " "
    return "".join(result), unterminated


def lint_wql(query: str) -> tuple[WqlIssue, ...]:
    """Syntactic-only WQL lint.  No runtime evaluation is attempted."""
    stripped = query.strip()
    if not stripped:
        return (
            WqlIssue(severity="error", code="empty_query", message="Query is empty."),
        )
    if len(query) > 4096:
        return (
            WqlIssue(
                severity="error",
                code="query_too_long",
                message="Query exceeds 4096 characters.",
            ),
        )
    if any(ord(c) < 32 and c not in "\t\n\r" for c in query):
        return (
            WqlIssue(
                severity="error",
                code="invalid_chars",
                message="Query contains invalid control characters.",
            ),
        )

    no_literals, unterminated = _strip_string_literals(stripped)
    normalized = re.sub(r"\s+", " ", no_literals).strip().upper()

    issues: list[WqlIssue] = []

    if unterminated:
        issues.append(
            WqlIssue(
                severity="error",
                code="unterminated_string",
                message="String literal is not terminated.",
            )
        )

    parens = 0
    for ch in no_literals:
        if ch == "(":
            parens += 1
        elif ch == ")":
            parens -= 1
            if parens < 0:
                break
    if parens != 0:
        issues.append(
            WqlIssue(
                severity="error",
                code="unbalanced_parens",
                message="Unbalanced parentheses.",
            )
        )

    if not re.search(r"\bFROM\b", normalized):
        issues.append(
            WqlIssue(
                severity="error",
                code="missing_from",
                message="Missing FROM clause.",
            )
        )
    elif not re.search(r"\bWHERE\b", normalized):
        issues.append(
            WqlIssue(
                severity="warning",
                code="missing_where",
                message="Missing WHERE clause; filter will match every instance.",
            )
        )

    if re.search(r"\bSELECT\s+\*", normalized):
        issues.append(
            WqlIssue(
                severity="warning",
                code="select_star",
                message="SELECT * returns all properties; prefer explicit properties.",
            )
        )

    return tuple(issues)


def parse_multi_query(raw: str) -> tuple[WmiQueryGroup, ...]:
    """Split semicolon-separated WQL into query groups.

    String literals are respected so semicolons inside literals do not split
    the query.  Each non-empty query becomes a single-element group.
    """
    groups: list[WmiQueryGroup] = []
    current: list[str] = []
    in_string = False
    buffer: list[str] = []

    for ch in raw:
        if ch == '"':
            in_string = not in_string
            buffer.append(ch)
        elif ch == ";" and not in_string:
            part = "".join(buffer).strip()
            if part:
                current.append(part)
            if current:
                groups.append(tuple(current))
            current = []
            buffer = []
        else:
            buffer.append(ch)

    trailing = "".join(buffer).strip()
    if trailing:
        current.append(trailing)
    if current:
        groups.append(tuple(current))

    return tuple(groups)


def serialize_multi_query(groups: tuple[WmiQueryGroup, ...]) -> str:
    """Serialize query groups back to semicolon-separated WQL."""
    parts: list[str] = []
    for group in groups:
        for query in group:
            query = query.strip()
            if query:
                parts.append(query)
    return "; ".join(parts)


LoopbackMode = Literal["disabled", "merge", "replace"]


def validate_loopback_config(mode: str) -> LoopbackConfig:
    """Validate and return a LoopbackConfig for the supplied mode."""
    if mode not in get_args(LoopbackMode):
        raise ValidationError([
            ValidationIssue(
                severity="error",
                code="invalid_loopback_mode",
                message=(
                    f"Invalid loopback mode: {mode!r}. "
                    "Must be one of disabled, merge, replace."
                ),
                path="mode",
            )
        ])
    return LoopbackConfig(mode=cast(Literal["disabled", "merge", "replace"], mode))


def describe_loopback(config: LoopbackConfig) -> str:
    """Return a plain-language description of the loopback mode."""
    match config.mode:
        case "disabled":
            return (
                "Loopback processing is disabled. User policies apply based on "
                "the user's location in AD."
            )
        case "merge":
            return (
                "Merge mode: user policies from the computer's GPOs are merged "
                "with the user's own GPOs. User's own GPOs take precedence."
            )
        case "replace":
            return (
                "Replace mode: only the user policies from the computer's GPOs "
                "apply. The user's own GPO user policies are ignored."
            )
        case _:
            assert_never(config.mode)


def check_filter_deletion_safe(filter_detail: WmiFilterDetail) -> tuple[str, ...]:
    """Return warnings if the filter is still referenced by GPOs."""
    warnings: list[str] = []
    for guid in filter_detail.references:
        warnings.append(
            f"Filter is referenced by GPO {guid}. "
            "Deleting will remove the WMI filter association."
        )
    return tuple(warnings)

