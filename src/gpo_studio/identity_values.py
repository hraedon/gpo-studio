"""One validator per identity kind: SID, DN, GUID, time, date, account name.

Every place that accepts, imports, previews or writes an identity calls these,
and nothing else: the store's validation, the GPMC backup and estate imports,
the publication preview and the serialized-preference check
(:func:`gpo_studio.validation.serialized_identity_issues`). Before 2026-10-17
each caller had its own regex, and each review found the next one that let a
trailing LF, a non-breaking space, a dangling escape or a lowercase prefix reach
Groups.xml, the PowerShell plan or a task payload (final-r4 to r7).

Each function returns ``None`` for a valid value and otherwise a short reason.
All of them judge the whole value: nothing is stripped first, so a value is
valid only if the value as stored is.

Characters refused everywhere an identity is read (:func:`unsafe_characters`):
Unicode controls (Cc), format characters (Cf, the bidirectional overrides and
zero-width characters among them), line and paragraph separators (Zl, Zp),
and every space separator but U+0020 (Zs: NBSP, the thin and ideographic
spaces). Surrogates, private-use and unassigned code points (Cs, Co, Cn) too.
"""

from __future__ import annotations

import calendar
import re
import unicodedata
from typing import Literal

#: Categories never valid in an identity.
_REFUSED_CATEGORIES = frozenset({"Cc", "Cf", "Zl", "Zp", "Cs", "Co", "Cn"})
#: A GPP environment-variable token, e.g. ``%LogonUser%`` or ``%LocalTimeXmlEx%``.
TOKEN = re.compile(r"%[A-Za-z][A-Za-z0-9_]*%", re.ASCII)
_SID = re.compile(r"S-1-[0-9]+(?:-[0-9]+)+", re.ASCII)
_GUID = r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}"
_BARE_GUID = re.compile(_GUID, re.ASCII)
_BRACED_GUID = re.compile(r"\{" + _GUID + r"\}", re.ASCII)
_TIME = re.compile(r"([0-9]{1,2}):([0-9]{2})(?::([0-9]{2}))?", re.ASCII)
_DATE = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})", re.ASCII)
_DATETIME = re.compile(
    r"([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.[0-9]{1,7})?"
    r"(?:Z|[+-]([0-9]{2}):([0-9]{2}))?",
    re.ASCII,
)
#: The DN attribute types Studio accepts: domains, OUs and containers.
DN_TYPES = frozenset({"CN", "OU", "DC"})
#: RFC 4514: characters that must be escaped anywhere in an attribute value.
_DN_SPECIAL = frozenset('"+,;<>\\=')
#: RFC 4514 section 2.4: what may follow a backslash besides two hex digits.
_DN_ESCAPABLE = frozenset(' "#+,;<=>\\')


def unsafe_characters(value: str) -> list[str]:
    """The characters no identity may contain, as ``U+XXXX`` names."""
    found = []
    for character in value:
        category = unicodedata.category(character)
        if category in _REFUSED_CATEGORIES or (category == "Zs" and character != " "):
            found.append(f"U+{ord(character):04X}")
    return found


def _characters(value: str) -> str | None:
    refused = unsafe_characters(value)
    if refused:
        return f"contains {', '.join(sorted(set(refused)))}"
    return None


def sid_problem(value: str) -> str | None:
    """A SID exactly as Windows writes it: ``S-1-<authority>-<sub>[-<sub>...]``."""
    if not value:
        return "is empty"
    return _characters(value) or (None if _SID.fullmatch(value) else "is not a SID (S-1-...)")


def guid_problem(
    value: str, *, braces: Literal["optional", "required", "forbidden"] = "optional"
) -> str | None:
    """A GUID, with both braces or neither (or as ``braces`` demands)."""
    if not value:
        return "is empty"
    bad = _characters(value)
    if bad:
        return bad
    braced = _BRACED_GUID.fullmatch(value) is not None
    bare = _BARE_GUID.fullmatch(value) is not None
    if braces == "required" and not braced:
        return "is not a braced GUID"
    if braces == "forbidden" and not bare:
        return "is not a GUID without braces"
    if not (braced or bare):
        return "is not a GUID"
    return None


def name_problem(value: str) -> str | None:
    """An account, group, computer or domain name as typed into a policy.

    Any printable text, tokens such as ``%LogonDomain%\\%LogonUser%`` included,
    but no refused character and no leading or trailing space.
    """
    if not value:
        return "is empty"
    bad = _characters(value)
    if bad:
        return bad
    if value != value.strip(" "):
        return "has leading or trailing space"
    return None


def principal_problem(value: str) -> str | None:
    """A principal written as a name or as a SID: a SID-shaped value must be one."""
    bad = name_problem(value)
    if bad:
        return bad
    if value[:2].casefold() == "s-" and value[2:3].isdigit():
        return sid_problem(value)
    return None


def security_principal_problem(value: str) -> str | None:
    """A principal in a security template list (``GptTmpl.inf``).

    Windows writes a SID with a leading star (``*S-1-5-32-544``) and accepts a
    bare account name. The star form must be a SID (DeepSeek final-r8 N6). A
    name must also hold no comma or double quote: those are the list's own
    separators and quoting, so either would turn one principal into two.
    """
    if value.startswith("*"):
        return sid_problem(value[1:])
    bad = principal_problem(value)
    if bad:
        return bad
    for separator in ',"':
        if separator in value:
            return f"contains {separator!r}, which separates or quotes template principals"
    return None


def _clock(hour: str, minute: str, second: str | None) -> bool:
    return int(hour) < 24 and int(minute) < 60 and (second is None or int(second) < 60)


def _calendar(year: str, month: str, day: str) -> bool:
    """A real day: month 1-12 and the day within that month (DeepSeek final-r8 N2)."""
    y, m, d = int(year), int(month), int(day)
    return y >= 1601 and 1 <= m <= 12 and 1 <= d <= calendar.monthrange(y, m)[1]


def time_problem(value: str) -> str | None:
    """A scheduled-task time: ``H:MM[:SS]``, an ISO 8601 date-time, or a token."""
    if not value:
        return "is empty"
    bad = _characters(value)
    if bad:
        return bad
    match = _TIME.fullmatch(value)
    if match is not None:
        return None if _clock(*match.groups()) else "is not a time of day"
    return datetime_problem(value)


def datetime_problem(value: str) -> str | None:
    """An ISO 8601 date-time as Task Scheduler writes it, or a GPP token."""
    if not value:
        return "is empty"
    bad = _characters(value)
    if bad:
        return bad
    if TOKEN.fullmatch(value):
        return None
    match = _DATETIME.fullmatch(value)
    if match is None:
        return "is not an ISO 8601 date-time (YYYY-MM-DDTHH:MM:SS)"
    year, month, day, hour, minute, second, offset_hours, offset_minutes = match.groups()
    if not (_calendar(year, month, day) and _clock(hour, minute, second)):
        return "is not a valid date-time"
    if offset_hours is not None and (
        int(offset_minutes) > 59 or int(offset_hours) * 60 + int(offset_minutes) > 14 * 60
    ):  # at most 14:00, so not +14:59 (DeepSeek final-r9 N5)
        return "has a UTC offset outside -14:00..+14:00"
    return None


def date_problem(value: str) -> str | None:
    """A date: ``YYYY-MM-DD``."""
    if not value:
        return "is empty"
    bad = _characters(value)
    if bad:
        return bad
    match = _DATE.fullmatch(value)
    if match is None or not _calendar(*match.groups()):
        return "is not a date (YYYY-MM-DD)"
    return None


def _dn_value_problem(raw: str) -> str | None:
    """One RDN value, escapes and all (RFC 4514 section 2.4)."""
    if not raw:
        return "has an empty RDN value"
    decoded = bytearray()
    index = 0
    while index < len(raw):
        character = raw[index]
        if character == "\\":
            if index + 1 >= len(raw):
                return "ends in a dangling escape"
            following = raw[index + 1]
            if following in _DN_ESCAPABLE:
                decoded += following.encode("utf-8")
                index += 2
                continue
            pair = raw[index + 1 : index + 3]
            if len(pair) == 2 and all(c in "0123456789abcdefABCDEF" for c in pair):
                decoded.append(int(pair, 16))
                index += 3
                continue
            return "has an incomplete escape"
        if character in _DN_SPECIAL:
            return f"has an unescaped {character!r} in an RDN value"
        if character == " " and (index == 0 or index == len(raw) - 1):
            return "has an unescaped leading or trailing space in an RDN value"
        if character == "#" and index == 0:
            return "has an unescaped leading '#' in an RDN value"
        decoded += character.encode("utf-8")
        index += 1
    try:
        text = decoded.decode("utf-8")
    except UnicodeDecodeError:
        return "has escapes that are not UTF-8"
    return _characters(text)


def _split_unescaped(value: str, separator: str) -> list[str]:
    parts: list[str] = []
    start = index = 0
    while index < len(value):
        if value[index] == "\\":
            index += 2
            continue
        if value[index] == separator:
            parts.append(value[start:index])
            start = index + 1
        index += 1
    parts.append(value[start:])
    return parts


def dn_problem(value: str, *, types: frozenset[str] = DN_TYPES, min_rdns: int = 2) -> str | None:
    """A distinguished name per RFC 4514, as Studio writes and Windows reads one.

    RDNs of the given attribute types (``CN``, ``OU``, ``DC``) separated by
    unescaped commas, each one attribute-value pair or several joined by an
    unescaped ``+`` (a multi-valued RDN, RFC 4514 section 3; DeepSeek final-r8
    N8); values with every special character escaped -- ``=`` included, which
    the RFC only recommends -- no unescaped leading or trailing space or
    leading ``#``, no dangling or incomplete escape; and no refused character,
    whether written or escaped.
    """
    if not value:
        return "is empty"
    bad = _characters(value)
    if bad:
        return bad
    rdns = _split_unescaped(value, ",")
    if len(rdns) < min_rdns:
        return f"has fewer than {min_rdns} RDNs"
    for rdn in rdns:
        for pair in _split_unescaped(rdn, "+"):
            attribute, separator, raw = pair.partition("=")
            if not separator:
                return "has an RDN without '='"
            if attribute.upper() not in types:
                return f"has an RDN of type {attribute!r}, not one of {sorted(types)}"
            problem = _dn_value_problem(raw)
            if problem:
                return problem
    return None
