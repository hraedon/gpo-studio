"""Parse untrusted JSON with one set of refusals (#140, #145).

``json.loads`` fails in more ways than ``JSONDecodeError``. Deep nesting raises
``RecursionError`` (#140), an integer literal longer than Python's int-string
limit (4,300 digits by default) raises a plain ``ValueError`` (#145), bytes that
are not UTF-8 raise ``UnicodeDecodeError``, and ``NaN``, ``Infinity`` and a
float such as ``1e400`` are accepted although they are not JSON numbers.
Each loader patched these one at a time; every loader of untrusted JSON now
calls :func:`loads_untrusted` and maps its single :class:`JsonInputError` to
its own error type.
"""

from __future__ import annotations

import json
import math
import sys


class JsonInputError(ValueError):
    """Untrusted input is not JSON this application accepts.

    ``reason`` is the refusal without a position; ``pos`` is the character
    offset of a syntax error, when there is one.
    """

    def __init__(self, message: str, pos: int | None = None, *, reason: str | None = None) -> None:
        super().__init__(message)
        self.reason = message if reason is None else reason
        self.pos = pos


def _refuse_constant(name: str) -> object:
    raise ValueError(f"{name} is not a JSON number")


def _finite_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise ValueError(f"{text[:40]} is out of range for a JSON number")
    return value


def _int(text: str) -> int:
    try:
        return int(text)
    except ValueError:
        digits = len(text) - text.startswith("-")
        raise ValueError(
            f"an integer of {digits} digits exceeds the "
            f"{sys.get_int_max_str_digits()}-digit limit"
        ) from None


def _deeper_than(value: object, limit: int) -> bool:
    # Iterative, so measuring the depth cannot itself recurse too deeply.
    stack = [(value, 0)]
    while stack:
        item, depth = stack.pop()
        if isinstance(item, dict):
            children = list(item.values())
        elif isinstance(item, list):
            children = item
        else:
            continue
        if depth >= limit:
            return True
        stack.extend((child, depth + 1) for child in children)
    return False


def loads_untrusted(
    data: str | bytes | bytearray,
    *,
    allow_non_finite: bool = False,
    max_depth: int | None = None,
) -> object:
    """Parse ``data``, raising :class:`JsonInputError` for every refusal.

    ``allow_non_finite`` keeps ``NaN``, ``Infinity`` and overflowing floats as
    Python floats, for callers (request bodies) whose schema validation reports
    them as field errors instead. ``max_depth`` refuses more than that many
    nested arrays and objects, for callers whose later processing recurses.
    """
    try:
        if allow_non_finite:
            value = json.loads(data, parse_int=_int)
        else:
            value = json.loads(
                data, parse_int=_int, parse_float=_finite_float, parse_constant=_refuse_constant
            )
    except json.JSONDecodeError as error:
        raise JsonInputError(str(error), error.pos, reason=error.msg) from None
    except UnicodeDecodeError:
        raise JsonInputError("not valid Unicode text") from None
    except RecursionError:
        raise JsonInputError("nested too deeply") from None
    except ValueError as error:
        raise JsonInputError(str(error)) from None
    if max_depth is not None and _deeper_than(value, max_depth):
        raise JsonInputError(f"nested more than {max_depth} levels deep")
    return value
