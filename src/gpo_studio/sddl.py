"""Parse and generate SDDL (Security Descriptor Definition Language) strings."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, assert_never


class SddlError(ValueError):
    """Malformed or unsupported SDDL content."""


@dataclass(frozen=True, slots=True)
class Ace:
    type: Literal["ALLOWED", "DENIED"]
    flags: tuple[str, ...]
    rights: tuple[str, ...]
    object_guid: str
    inherit_object_guid: str
    trustee_sid: str


@dataclass(frozen=True, slots=True)
class Acl:
    aces: tuple[Ace, ...]


@dataclass(frozen=True, slots=True)
class SecurityDescriptor:
    owner_sid: str
    group_sid: str
    dacl: Acl | None
    sacl: Acl | None


_SECTION_RE = re.compile(r"([OGDS]):")
_ACE_RE = re.compile(r"\(([^)]*)\)")

_MAX_SDDL_SIZE = 256 * 1024
_MAX_ACE_COUNT = 10000


def _split_codes(s: str) -> tuple[str, ...]:
    if len(s) % 2 != 0:
        raise SddlError(f"odd-length code string: {s!r}")
    return tuple(s[i : i + 2] for i in range(0, len(s), 2))


def _ace_type(type_str: str) -> Literal["ALLOWED", "DENIED"]:
    if type_str == "A":
        return "ALLOWED"
    if type_str == "D":
        return "DENIED"
    raise SddlError(f"unknown ACE type: {type_str!r}")


def parse_ace(ace_str: str) -> Ace:
    """Parse a single ACE string without outer parentheses."""
    parts = ace_str.split(";")
    if len(parts) != 6:
        raise SddlError(f"ACE must have 6 fields, got {len(parts)}: {ace_str!r}")
    type_str, flags_str, rights_str, object_guid, inherit_object_guid, trustee_sid = parts
    return Ace(
        type=_ace_type(type_str),
        flags=_split_codes(flags_str),
        rights=_split_codes(rights_str),
        object_guid=object_guid,
        inherit_object_guid=inherit_object_guid,
        trustee_sid=trustee_sid,
    )


def format_ace(ace: Ace) -> str:
    """Format an Ace as a string without outer parentheses."""
    if ace.type == "ALLOWED":
        type_char = "A"
    elif ace.type == "DENIED":
        type_char = "D"
    else:
        assert_never(ace.type)
    return ";".join(
        [
            type_char,
            "".join(ace.flags),
            "".join(ace.rights),
            ace.object_guid,
            ace.inherit_object_guid,
            ace.trustee_sid,
        ]
    )


def _parse_acl(acl_str: str) -> Acl:
    ace_matches = list(_ACE_RE.finditer(acl_str))
    if len(ace_matches) > _MAX_ACE_COUNT:
        raise SddlError(
            f"SDDL has {len(ace_matches)} ACEs, exceeds {_MAX_ACE_COUNT}"
        )
    aces = tuple(parse_ace(m.group(1)) for m in ace_matches)
    remaining = _ACE_RE.sub("", acl_str)
    if "(" in remaining or ")" in remaining:
        raise SddlError(f"unmatched parentheses in ACL: {acl_str!r}")
    return Acl(aces=aces)


def _mask_parens(s: str) -> str:
    return _ACE_RE.sub(lambda m: " " * len(m.group(0)), s)


def parse_sddl(sddl: str) -> SecurityDescriptor:
    """Parse an SDDL string into a SecurityDescriptor."""
    if len(sddl.encode("utf-8")) > _MAX_SDDL_SIZE:
        raise SddlError(f"SDDL string exceeds {_MAX_SDDL_SIZE} bytes")
    masked = _mask_parens(sddl)
    matches = list(_SECTION_RE.finditer(masked))
    if not matches:
        raise SddlError("empty or malformed SDDL string")
    sections: dict[str, str] = {}
    for i, m in enumerate(matches):
        key = m.group(1)
        if key in sections:
            raise SddlError(f"duplicate section: {key}")
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(sddl)
        sections[key] = sddl[start:end].strip()
    owner_sid = sections.get("O", "")
    group_sid = sections.get("G", "")
    dacl = _parse_acl(sections["D"]) if "D" in sections else None
    sacl = _parse_acl(sections["S"]) if "S" in sections else None
    return SecurityDescriptor(
        owner_sid=owner_sid,
        group_sid=group_sid,
        dacl=dacl,
        sacl=sacl,
    )


def format_sddl(sd: SecurityDescriptor) -> str:
    """Format a SecurityDescriptor as an SDDL string."""
    parts: list[str] = []
    if sd.owner_sid:
        parts.append(f"O:{sd.owner_sid}")
    if sd.group_sid:
        parts.append(f"G:{sd.group_sid}")
    if sd.dacl is not None:
        parts.append("D:" + "".join(f"({format_ace(ace)})" for ace in sd.dacl.aces))
    if sd.sacl is not None:
        parts.append("S:" + "".join(f"({format_ace(ace)})" for ace in sd.sacl.aces))
    return "".join(parts)
