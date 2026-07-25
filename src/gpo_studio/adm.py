"""Legacy .adm Administrative Template parser (preserve/read-only mode)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from .model import RegistrySetting, RegistryType, Side

_DWORD: RegistryType = "REG_DWORD"
_SZ: RegistryType = "REG_SZ"

_CLASS_MAP: dict[str, tuple[Side, Literal["HKLM", "HKCU"]]] = {
    "MACHINE": ("computer", "HKLM"),
    "USER": ("user", "HKCU"),
}

_PRECEDENCE = {
    "on": 3,
    "action_on": 3,
    "default": 2,
    "off": 1,
    "action_off": 1,
}


@dataclass(frozen=True, slots=True)
class _Candidate:
    source: str
    line: int
    side: Side
    hive: Literal["HKLM", "HKCU"]
    key: str
    value_name: str
    registry_type: RegistryType
    value: str | int


def _strip_comment(line: str) -> str:
    in_quote = False
    chars: list[str] = []
    for c in line:
        if c == '"':
            in_quote = not in_quote
        elif c == ';' and not in_quote:
            break
        chars.append(c)
    return ''.join(chars).strip()


def _tokenize(line: str) -> list[str]:
    parts = re.findall(r'"[^"]*"|[^\s"]+', line)
    return [p[1:-1] if p.startswith('"') and p.endswith('"') else p for p in parts]


def _find_block(stack: list[dict[str, Any]], kind: str) -> dict[str, Any] | None:
    for block in reversed(stack):
        if block["kind"] == kind:
            return block
    return None


def _resolve_key_value(ctx: dict[str, Any], stack: list[dict[str, Any]]) -> tuple[str, str]:
    key = ctx.get("key", "")
    value_name = ctx.get("value_name", "")
    if not key:
        policy = _find_block(stack, "policy")
        if policy is not None:
            key = policy.get("key", "")
    if not value_name:
        policy = _find_block(stack, "policy")
        if policy is not None:
            value_name = policy.get("value_name", "")
    return key, value_name


def _parse_value(
    tokens: list[str], start: int, type_token: str, source: str
) -> tuple[RegistryType, str | int] | None:
    kind = type_token.upper()
    if kind == "NUMERIC":
        if len(tokens) <= start:
            return None
        try:
            return _DWORD, int(tokens[start])
        except ValueError:
            return None
    if kind == "TEXT":
        if len(tokens) <= start:
            return None
        return _SZ, tokens[start]
    if kind == "CHECKBOX":
        if len(tokens) > start:
            try:
                return _DWORD, int(tokens[start])
            except ValueError:
                return None
        default = 1 if source in ("on", "action_on", "default") else 0
        return _DWORD, default
    return None


def _record_candidate(
    source: str,
    line: int,
    current_class: tuple[Side, Literal["HKLM", "HKCU"]] | None,
    ctx: dict[str, Any],
    stack: list[dict[str, Any]],
    type_token: str,
    tokens: list[str],
    value_start: int,
    candidates: list[_Candidate],
    warnings: list[str],
) -> None:
    if current_class is None:
        warnings.append(f"line {line}: {source} ignored outside CLASS")
        return
    key, value_name = _resolve_key_value(ctx, stack)
    if not key or not value_name:
        warnings.append(f"line {line}: {source} missing KEYNAME or VALUENAME")
        return
    parsed = _parse_value(tokens, value_start, type_token, source)
    if parsed is None:
        warnings.append(f"line {line}: unsupported type {type_token!r}")
        return
    registry_type, value = parsed
    candidates.append(
        _Candidate(
            source=source,
            line=line,
            side=current_class[0],
            hive=current_class[1],
            key=key,
            value_name=value_name,
            registry_type=registry_type,
            value=value,
        )
    )


def parse_adm(data: str) -> tuple[list[RegistrySetting], list[str]]:
    """Parse legacy .adm file content. Returns (settings, warnings)."""
    candidates: list[_Candidate] = []
    warnings: list[str] = []
    stack: list[dict[str, Any]] = []
    current_class: tuple[Side, Literal["HKLM", "HKCU"]] | None = None

    for line_no, raw in enumerate(data.splitlines(), start=1):
        line = _strip_comment(raw)
        if not line:
            continue
        tokens = _tokenize(line)
        if not tokens:
            continue
        keyword = tokens[0].casefold()

        if keyword == "class":
            if len(tokens) < 2:
                warnings.append(f"line {line_no}: CLASS missing name")
                continue
            mapping = _CLASS_MAP.get(tokens[1].upper())
            if mapping is None:
                warnings.append(f"line {line_no}: unknown CLASS {tokens[1]!r}")
                continue
            if current_class is not None:
                while stack and stack[-1]["kind"] != "class":
                    stack.pop()
                if stack and stack[-1]["kind"] == "class":
                    stack.pop()
            current_class = mapping
            stack.append({"kind": "class"})
            continue

        if keyword == "category":
            stack.append({"kind": "category"})
            continue

        if keyword == "policy":
            name = tokens[1] if len(tokens) > 1 else ""
            stack.append({"kind": "policy", "key": "", "value_name": "", "name": name})
            continue

        if keyword == "part":
            part_type = tokens[2].upper() if len(tokens) > 2 else ""
            stack.append({"kind": "part", "key": "", "value_name": "", "part_type": part_type})
            continue

        if keyword in ("actionliston", "actionlistoff"):
            source = "on" if keyword == "actionliston" else "off"
            policy = _find_block(stack, "policy")
            if policy is None:
                warnings.append(f"line {line_no}: {tokens[0]} outside POLICY")
                continue
            key = policy.get("key", "")
            value_name = policy.get("value_name", "")
            stack.append({
                "kind": "actionlist",
                "key": key,
                "value_name": value_name,
                "source": source,
            })
            continue

        if keyword == "end":
            if len(tokens) < 2:
                warnings.append(f"line {line_no}: END missing keyword")
                continue
            target = tokens[1].casefold()
            matched = False
            while stack:
                popped = stack.pop()
                if (
                    (target == "class" and popped["kind"] == "class")
                    or (target == "category" and popped["kind"] == "category")
                    or (target == "policy" and popped["kind"] == "policy")
                    or (target == "part" and popped["kind"] == "part")
                    or (target == "actionlist" and popped["kind"] == "actionlist")
                ):
                    matched = True
                    break
            if not matched:
                warnings.append(f"line {line_no}: END {tokens[1]!r} without matching block")
                continue
            if target == "class":
                current_class = None
            continue

        top: dict[str, Any] | None = stack[-1] if stack else None

        if keyword == "keyname":
            if top is None or top["kind"] not in ("policy", "part", "actionlist"):
                warnings.append(f"line {line_no}: KEYNAME outside valid block")
                continue
            if len(tokens) < 2:
                warnings.append(f"line {line_no}: KEYNAME missing value")
                continue
            top["key"] = tokens[1]
            continue

        if keyword == "valuename":
            if top is None or top["kind"] not in ("policy", "part", "actionlist"):
                warnings.append(f"line {line_no}: VALUENAME outside valid block")
                continue
            if len(tokens) < 2:
                warnings.append(f"line {line_no}: VALUENAME missing value")
                continue
            top["value_name"] = tokens[1]
            continue

        if keyword in ("valueon", "valueoff"):
            source = "on" if keyword == "valueon" else "off"
            ctx: dict[str, Any] | None
            if top is not None and top["kind"] in ("policy", "part"):
                ctx = top
            else:
                ctx = _find_block(stack, "policy") or _find_block(stack, "part")
            if ctx is None:
                warnings.append(f"line {line_no}: {tokens[0]} outside POLICY or PART")
                continue
            if len(tokens) < 2:
                warnings.append(f"line {line_no}: {tokens[0]} missing type")
                continue
            type_token = tokens[1]
            _record_candidate(
                source, line_no, current_class, ctx, stack,
                type_token, tokens, 2, candidates, warnings,
            )
            continue

        if keyword == "default":
            if top is None or top["kind"] != "part":
                warnings.append(f"line {line_no}: DEFAULT outside PART")
                continue
            type_token = top.get("part_type", "")
            if not type_token or type_token not in ("NUMERIC", "TEXT", "CHECKBOX"):
                warnings.append(f"line {line_no}: DEFAULT unsupported part type {type_token!r}")
                continue
            _record_candidate(
                "default", line_no, current_class, top, stack,
                type_token, tokens, 1, candidates, warnings,
            )
            continue

        if keyword == "value":
            if top is None or top["kind"] != "actionlist":
                warnings.append(f"line {line_no}: VALUE outside ACTIONLIST")
                continue
            source = "action_" + top.get("source", "on")
            if len(tokens) < 2:
                warnings.append(f"line {line_no}: VALUE missing type")
                continue
            type_token = tokens[1]
            _record_candidate(
                source, line_no, current_class, top, stack,
                type_token, tokens, 2, candidates, warnings,
            )
            continue

    for block in stack:
        warnings.append(f"unclosed {block['kind']} block")

    seen: dict[tuple[str, str, str, str], _Candidate] = {}
    for cand in candidates:
        identity = (cand.side, cand.hive, cand.key.casefold(), cand.value_name.casefold())
        existing = seen.get(identity)
        if existing is None or _PRECEDENCE[cand.source] > _PRECEDENCE[existing.source]:
            seen[identity] = cand

    settings: list[RegistrySetting] = []
    for idx, cand in enumerate(seen.values(), start=1):
        settings.append(
            RegistrySetting(
                id=f"legacy-adm-{idx}",
                side=cand.side,
                hive=cand.hive,
                key=cand.key,
                value_name=cand.value_name,
                registry_type=cand.registry_type,
                value=cand.value,
                action="set",
                comment="",
            )
        )
    return settings, warnings
