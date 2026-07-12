"""Numeric contract for DWORD/QWORD registry values.

Browser JSON serializers lose precision above 2**53 - 1, so DWORD and QWORD
values cross the API boundary as canonical decimal strings and are converted to
Python integers after range checking.
"""

from __future__ import annotations

import re

_CANONICAL_DECIMAL = re.compile(r"^(?:0|[1-9][0-9]*)$")

_DWORD_MAX = 0xFFFFFFFF
_QWORD_MAX = 0xFFFFFFFFFFFFFFFF


def is_canonical_decimal(s: str) -> bool:
    return _CANONICAL_DECIMAL.fullmatch(s) is not None


def coerce_dword_qword(value: str | int, registry_type: str) -> int:
    if isinstance(value, bool):
        raise ValueError(
            f"{registry_type} value must be a decimal string or int, not bool"
        )
    if isinstance(value, str):
        if not is_canonical_decimal(value):
            raise ValueError(
                f"{registry_type} value must be a canonical decimal string "
                f"(digits only, no sign, exponent, fraction, whitespace, hex, "
                f"or leading zeros): {value!r}"
            )
        parsed = int(value)
    elif isinstance(value, int):
        parsed = value
    else:
        raise TypeError(
            f"{registry_type} value must be str or int, got {type(value).__name__}"
        )
    if registry_type == "REG_DWORD":
        lo, hi = 0, _DWORD_MAX
    elif registry_type == "REG_QWORD":
        lo, hi = 0, _QWORD_MAX
    else:
        raise ValueError(
            f"coerce_dword_qword only handles REG_DWORD/REG_QWORD, "
            f"got {registry_type!r}"
        )
    if not lo <= parsed <= hi:
        raise ValueError(
            f"{registry_type} value {parsed} is outside the range [0, {hi}]"
        )
    return parsed
