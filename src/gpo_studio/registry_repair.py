"""Read-only registry identity diagnostics and explicit registry-only recovery.

Ownership claims come from stored editor IDs, never today's catalogue or a
matching registry address. Recovery keeps selected native records as raw rows
with fresh identities; it does not reconstruct ADMX authoring intent.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from dataclasses import asdict, dataclass, replace
from typing import Literal
from uuid import uuid4

from .model import GPO, RegistrySetting, ValidationError, ValidationIssue
from .policy_config import legacy_setting_parts, parse_structured_setting_id

# Historical IDs are unbounded stored text. Avoid quadratic legacy-frame
# materialisation on a diagnostic read; retain the exact ID in record_json.
_MAX_ID_LENGTH = 8192
_MAX_OWNER_FRAMES = 32
_SIDE_FRAME = re.compile(r"-(computer|user)-")


@dataclass(frozen=True, slots=True)
class EditingWarning:
    code: str
    message: str
    rows: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class RowIdentity:
    claims: tuple[tuple[str, str], ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RepairChoice:
    row: int
    action: Literal["keep_raw", "omit"]


@dataclass(frozen=True, slots=True)
class RepairRow:
    row: int
    record_json: str
    identity: RowIdentity


@dataclass(frozen=True, slots=True)
class RegistryRepairSource:
    source_guid: str
    source_name: str
    source_revision: int
    domain: str
    computer_enabled: bool
    user_enabled: bool
    rows: tuple[RepairRow, ...]
    omitted_components: dict[str, int]
    warnings: tuple[EditingWarning, ...]


@dataclass(frozen=True, slots=True)
class RegistryRepairPreview:
    source: RegistryRepairSource
    name: str
    description: str
    actor: str
    reason: str
    choices: tuple[RepairChoice, ...]
    kept_count: int
    review_sha256: str


def row_identity(setting: RegistrySetting) -> RowIdentity:
    """Report stored claims without choosing among historical producers."""
    identity = setting.id
    if (
        len(identity) > _MAX_ID_LENGTH
        or sum(1 for _ in _SIDE_FRAME.finditer(identity)) > _MAX_OWNER_FRAMES
    ):
        return RowIdentity((), ("identity_analysis_limit",))
    structured = parse_structured_setting_id(identity)
    if structured is not None:
        claims = ((structured.policy_id, structured.side),)
        warnings = ("owner_side_mismatch",) if structured.side != setting.side else ()
        return RowIdentity(claims, warnings)
    legacy = tuple(
        dict.fromkeys((owner, side) for owner, side, _suffix in legacy_setting_parts(identity))
    )
    if legacy:
        if len(legacy) > 1:
            return RowIdentity(legacy, ("ambiguous_owner",))
        return RowIdentity(legacy, ("owner_side_mismatch",) if legacy[0][1] != setting.side else ())
    if identity.startswith("admx2:") or identity.startswith("admx-"):
        return RowIdentity((), ("malformed_owner",))
    if identity.startswith("forked-") or identity.startswith("imported-"):
        return RowIdentity((), ("unowned_copied_row",))
    return RowIdentity((), ())


_MESSAGES = {
    "identity_analysis_limit": (
        "The stored ID is too complex to analyse. Review the exact raw record."
    ),
    "owner_side_mismatch": (
        "The stored owner claims another side; reconfiguration does not select this row."
    ),
    "ambiguous_owner": (
        "The stored ID has multiple possible owners. No current catalogue or address "
        "proves which produced it."
    ),
    "malformed_owner": (
        "The stored ADMX editor ID cannot be decoded; reconfiguration may leave this raw row."
    ),
    "unowned_copied_row": (
        "This copied/imported row has no ADMX editor owner. Not configured can leave it; "
        "configuring an overlapping address can be refused."
    ),
}


def editing_warnings(gpo: GPO) -> tuple[EditingWarning, ...]:
    """Advisory only: these do not decide validation or export eligibility."""
    identities: dict[str, list[int]] = defaultdict(list)
    warnings: dict[str, list[int]] = defaultdict(list)
    for index, setting in enumerate(gpo.settings):
        identities[setting.id].append(index)
        for warning in row_identity(setting).warnings:
            warnings[warning].append(index)
    result = [
        EditingWarning(
            "duplicate_editor_id",
            "These rows share an editor ID. An unrelated edit can be refused until "
            "identities are repaired.",
            tuple(rows),
        )
        for rows in identities.values()
        if len(rows) > 1
    ]
    result.extend(
        EditingWarning(code, _MESSAGES[code], tuple(rows)) for code, rows in warnings.items()
    )
    return tuple(result)


def _invalid_choice(message: str) -> ValidationError:
    return ValidationError(
        [ValidationIssue("error", "registry_repair_choices", message, "choices")]
    )


def ordered_choices(gpo: GPO, choices: tuple[RepairChoice, ...]) -> tuple[RepairChoice, ...]:
    """Ordinals distinguish duplicate IDs. Every source row needs one choice."""
    by_row: dict[int, RepairChoice] = {}
    for choice in choices:
        if type(choice.row) is not int or not 0 <= choice.row < len(gpo.settings):
            raise _invalid_choice("Each choice must identify an existing source row ordinal.")
        if choice.row in by_row:
            raise _invalid_choice("Each source row needs exactly one choice.")
        if choice.action not in ("keep_raw", "omit"):
            raise _invalid_choice("Choose keep_raw or omit for each source row.")
        by_row[choice.row] = choice
    if len(by_row) != len(gpo.settings):
        raise _invalid_choice("Choose keep_raw or omit for every source row, including clean rows.")
    return tuple(by_row[index] for index in range(len(gpo.settings)))


def repair_source(source: GPO) -> RegistryRepairSource:
    # Strings keep QWORD values exact through a JavaScript preview. Applying
    # submits choices, not values parsed or rewritten by the browser.
    rows = tuple(
        RepairRow(
            index,
            json.dumps(asdict(setting), ensure_ascii=True, sort_keys=True),
            row_identity(setting),
        )
        for index, setting in enumerate(source.settings)
    )
    omitted = {
        "links": len(source.links),
        "security_filters": len(source.security_filters),
        "wmi_filter": int(source.wmi_filter is not None),
        "preference_collections": len(source.gpp_collections),
        "preserved_cse_entries": len(source.cse_metadata),
        "backup_inventory": int(source.backup_inventory is not None),
        "folder_redirection": int(source.fdeploy is not None),
    }
    return RegistryRepairSource(
        source.guid,
        source.name,
        source.revision,
        source.domain,
        source.computer_enabled,
        source.user_enabled,
        rows,
        omitted,
        editing_warnings(source),
    )


def repair_preview(
    source: GPO, choices: tuple[RepairChoice, ...], *, name: str, actor: str, reason: str
) -> RegistryRepairPreview:
    decisions = ordered_choices(source, choices)
    description = (
        f"Registry-only recovery from {source.guid} revision {source.revision}. "
        "Selected native registry rows have new raw editor identities. "
        "Other policy components and the original description were not copied."
    )
    preview = RegistryRepairPreview(
        repair_source(source),
        name.strip(),
        description,
        actor,
        reason,
        decisions,
        sum(choice.action == "keep_raw" for choice in decisions),
        "",
    )
    # Only the public facts actually reviewed here enter this digest. In
    # particular, never hash or disclose stored-only import source identities.
    digest = hashlib.sha256(
        json.dumps(
            {"scope": "registry-only-v1", **asdict(preview)},
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    ).hexdigest()
    return replace(preview, review_sha256=digest)


def repaired_settings(
    source: GPO, choices: tuple[RepairChoice, ...]
) -> tuple[RegistrySetting, ...]:
    return tuple(
        replace(
            setting,
            id=str(uuid4()),
            value=list(setting.value) if isinstance(setting.value, list) else setting.value,
        )
        for setting, choice in zip(source.settings, ordered_choices(source, choices), strict=True)
        if choice.action == "keep_raw"
    )
