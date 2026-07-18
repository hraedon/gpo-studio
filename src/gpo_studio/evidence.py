"""Evidence packs and the public capability-matrix derivation (Plan 021 WP-4).

The parity program treats "GPMC parity" as a falsifiable, row-by-row matrix
rather than a single checkbox. This module is the mechanical heart of that
claim: it loads versioned evidence packs, refuses to derive public claims from
packs whose redaction or licensing gates are not satisfied, and derives each
public classification *only* from passing evidence.

Key invariants (enforced here and covered by tests):

- **No ``verified-rw`` without Windows AND endpoint evidence.** A public claim
  reaches ``verified-rw`` only when, for a given (capability, estate), at least
  one passing ``windows-side`` record AND one passing ``endpoint`` record exist.
  Windows-only passing evidence yields ``verified-ro``; endpoint-only yields
  ``unknown`` (insufficient — the Windows side was not observed).
- **Unsigned packs contribute nothing by default.** A pack is *signable* only
  when ``redaction_verified`` and ``licensing_complete`` are both true. The
  generator refuses to derive claims from a non-signable pack unless an explicit
  ``--allow-unsigned`` dev override is given, which stamps the output ``DRAFT``.
- **Policy classifications are taken as-is.** ``preserve-only``,
  ``intentional-deny``, and ``not-present-on-target`` are explicit operator
  policy (e.g. the permanent ``cpassword`` ban is ``intentional-deny``) and are
  surfaced directly from any record asserting them, regardless of evidence.
- **Local success alone is never evidence.** The generator reports which packs a
  claim was derived from so a reviewer can confirm the evidence is real, not a
  local smoke run.

The evidence-pack JSON schema is documented in
``docs/plan-021/reference-estates-and-evidence.md`` (``schema_version`` 1). The
1.0 ``release-evidence-report.json`` is treated as legacy ``schema_version`` 0.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, TextIO

PACK_SCHEMA_VERSION = 1

Classification = Literal[
    "verified-rw",
    "verified-ro",
    "preserve-only",
    "intentional-deny",
    "not-present-on-target",
    "unknown",
]
Outcome = Literal["pass", "fail", "skip", "expected_failure"]
EvidenceKind = Literal["windows-side", "endpoint"]
RecordSide = Literal["computer", "user", "both"]
EstateRole = Literal["DC", "member-server", "client"]
ContentClassification = Literal["in-repo", "hash-reference", "excluded"]

_POLICY_CLASSIFICATIONS: frozenset[str] = frozenset(
    {"preserve-only", "intentional-deny", "not-present-on-target"}
)
_VALID_CLASSIFICATIONS: frozenset[str] = frozenset(
    {
        "verified-rw",
        "verified-ro",
        "preserve-only",
        "intentional-deny",
        "not-present-on-target",
        "unknown",
    }
)
_VALID_OUTCOMES: frozenset[str] = frozenset(
    {"pass", "fail", "skip", "expected_failure"}
)
_VALID_KINDS: frozenset[str] = frozenset({"windows-side", "endpoint"})
_VALID_SIDES: frozenset[str] = frozenset({"computer", "user", "both"})
_VALID_ROLES: frozenset[str] = frozenset({"DC", "member-server", "client"})
_VALID_CONTENT: frozenset[str] = frozenset({"in-repo", "hash-reference", "excluded"})


class PackError(ValueError):
    """Raised when an evidence pack fails schema or invariant validation."""


@dataclass(frozen=True, slots=True)
class ContentItem:
    """A classified content item carried by an evidence pack.

    Licensing classification is mandatory before signing: every item must be
    ``in-repo``, ``hash-reference``, or ``excluded``.
    """

    content_id: str
    classification: ContentClassification
    sha256: str | None
    source_build: str | None
    regeneration_path: str | None
    license_note: str


@dataclass(frozen=True, slots=True)
class Estate:
    """The Windows reference estate a pack's evidence was gathered on."""

    os: str
    build: str
    role: EstateRole
    forest: str
    domain: str
    dc: str
    gpmc_version: str
    client_os: str | None = None

    def label(self) -> str:
        """Short human label for matrix rows, e.g. ``WS2025 DC``."""
        return f"{self.os} {self.role}".strip()


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    """One observed verification of a single capability on one estate."""

    capability: str
    cse_guid: str | None
    side: RecordSide
    action: str
    outcome: Outcome
    classification: Classification
    evidence_kind: EvidenceKind
    tool: str
    ms_doc: str
    evidence_hash: str
    notes: str = ""


@dataclass(frozen=True, slots=True)
class EvidencePack:
    """A versioned, signed-or-signable evidence pack."""

    schema_version: int
    pack_id: str
    generated_at: str
    source_commit: str
    operator: str
    redaction_verified: bool
    licensing_complete: bool
    estate: Estate
    records: tuple[EvidenceRecord, ...]
    content: tuple[ContentItem, ...] = field(default_factory=tuple)

    @property
    def signable(self) -> bool:
        """True only when redaction and licensing gates are both satisfied."""
        return self.redaction_verified and self.licensing_complete

    def passing(self) -> Iterator[EvidenceRecord]:
        """Yield only the records whose outcome is ``pass``."""
        return (r for r in self.records if r.outcome == "pass")


@dataclass(frozen=True, slots=True)
class PublicMatrixClaim:
    """A single derived public claim for one (capability, estate) pair."""

    capability: str
    estate: str
    classification: Classification
    has_windows: bool
    has_endpoint: bool
    tools: tuple[str, ...]
    ms_docs: tuple[str, ...]
    evidence_hashes: tuple[str, ...]
    source_packs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ExpectedFailure:
    """A capability that failed as expected (e.g. synthetic references)."""

    capability: str
    estate: str
    tool: str
    notes: str
    source_pack: str


@dataclass(frozen=True, slots=True)
class DerivationResult:
    """The full output of deriving a public matrix from packs."""

    claims: tuple[PublicMatrixClaim, ...]
    expected_failures: tuple[ExpectedFailure, ...]
    unsigned_pack_ids: tuple[str, ...]
    admitted_unsigned_ids: tuple[str, ...]
    source_pack_ids: tuple[str, ...]

    @property
    def derived_from_signable_packs(self) -> bool:
        return not self.unsigned_pack_ids and not self.admitted_unsigned_ids


def _as_mapping(raw: object, source: str) -> Mapping[str, object]:
    """Narrow a parsed JSON value to a string-keyed mapping or raise."""
    if not isinstance(raw, dict):
        raise PackError(f"{source} must be a JSON object")
    return raw  # JSON object keys are always strings.


def _required_str(data: Mapping[str, object], key: str, label: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise PackError(f"{label} must be a non-empty string")
    return value


def _optional_str(value: object, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise PackError(f"{label} must be a string or null")
    return value


def _parse_content_item(raw: object) -> ContentItem:
    data = _as_mapping(raw, "content item")
    content_id = _required_str(data, "content_id", "content item content_id")
    classification = data.get("classification")
    if classification not in _VALID_CONTENT:
        raise PackError(
            f"content item {content_id!r} has invalid classification {classification!r}"
        )
    sha256 = data.get("sha256")
    if sha256 is not None and not isinstance(sha256, str):
        raise PackError(f"content item {content_id!r} sha256 must be a string or null")
    source_build = _optional_str(
        data.get("source_build"), "content item source_build"
    )
    regeneration_path = _optional_str(
        data.get("regeneration_path"), "content item regeneration_path"
    )
    license_note = _optional_str(data.get("license_note"), "content item license_note") or ""
    # Per-classification required fields. `hash-reference` and `in-repo` content
    # is hashed, so both require a sha256; `excluded` content is not stored and
    # needs no hash. Every classification requires a license_note that records
    # why the item is placed in its class.
    if classification == "hash-reference":
        _require_content_field(content_id, "sha256", isinstance(sha256, str) and bool(sha256))
        _require_content_field(content_id, "source_build", source_build is not None)
        _require_content_field(content_id, "regeneration_path", regeneration_path is not None)
    elif classification == "in-repo":
        _require_content_field(content_id, "sha256", isinstance(sha256, str) and bool(sha256))
    _require_content_field(content_id, "license_note", bool(license_note))
    return ContentItem(
        content_id=content_id,
        classification=classification,  # type: ignore[arg-type]
        sha256=sha256 if isinstance(sha256, str) else None,
        source_build=source_build,
        regeneration_path=regeneration_path,
        license_note=license_note,
    )


def _require_content_field(
    content_id: str, field_name: str, present: bool
) -> None:
    if not present:
        raise PackError(
            f"content item {content_id!r} requires a non-empty {field_name}"
        )


def _parse_estate(raw: object) -> Estate:
    data = _as_mapping(raw, "estate")
    role = data.get("role")
    if role not in _VALID_ROLES:
        raise PackError(f"estate role {role!r} is not one of {sorted(_VALID_ROLES)}")
    return Estate(
        os=_required_str(data, "os", "estate.os"),
        build=_required_str(data, "build", "estate.build"),
        role=role,  # type: ignore[arg-type]
        forest=_required_str(data, "forest", "estate.forest"),
        domain=_required_str(data, "domain", "estate.domain"),
        dc=_required_str(data, "dc", "estate.dc"),
        gpmc_version=_required_str(data, "gpmc_version", "estate.gpmc_version"),
        client_os=_optional_str(data.get("client_os"), "estate.client_os"),
    )


def _parse_record(raw: object) -> EvidenceRecord:
    data = _as_mapping(raw, "record")
    capability = _required_str(data, "capability", "record capability")
    outcome = data.get("outcome")
    classification = data.get("classification")
    evidence_kind = data.get("evidence_kind")
    side = data.get("side")
    if outcome not in _VALID_OUTCOMES:
        raise PackError(
            f"record {capability!r} outcome {outcome!r} is not one of "
            f"{sorted(_VALID_OUTCOMES)}"
        )
    if classification not in _VALID_CLASSIFICATIONS:
        raise PackError(
            f"record {capability!r} classification {classification!r} is invalid"
        )
    if evidence_kind not in _VALID_KINDS:
        raise PackError(
            f"record {capability!r} evidence_kind {evidence_kind!r} is not "
            f"one of {sorted(_VALID_KINDS)}"
        )
    if side not in _VALID_SIDES:
        raise PackError(f"record {capability!r} side {side!r} is invalid")
    cse_guid = data.get("cse_guid")
    if cse_guid is not None and not isinstance(cse_guid, str):
        raise PackError(f"record {capability!r} cse_guid must be a string or null")
    return EvidenceRecord(
        capability=capability,
        cse_guid=cse_guid if isinstance(cse_guid, str) else None,
        side=side,  # type: ignore[arg-type]
        action=_required_str(data, "action", f"record {capability!r} action"),
        outcome=outcome,  # type: ignore[arg-type]
        classification=classification,  # type: ignore[arg-type]
        evidence_kind=evidence_kind,  # type: ignore[arg-type]
        tool=_required_str(data, "tool", f"record {capability!r} tool"),
        ms_doc=_required_str(data, "ms_doc", f"record {capability!r} ms_doc"),
        evidence_hash=_required_str(
            data, "evidence_hash", f"record {capability!r} evidence_hash"
        ),
        notes=_optional_str(data.get("notes"), f"record {capability!r} notes") or "",
    )


def load_pack(path: Path) -> EvidencePack:
    """Load and validate an evidence pack from a JSON file.

    Raises :class:`PackError` on any schema or invariant violation. A pack with
    ``schema_version`` other than :data:`PACK_SCHEMA_VERSION` is rejected so a
    future or stale schema cannot be silently misread.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise PackError(f"cannot read pack {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise PackError(f"invalid JSON in pack {path}: {exc}") from exc
    return parse_pack(raw, source=str(path))


def parse_pack(raw: object, source: str = "<inline>") -> EvidencePack:
    """Validate a parsed JSON object as an evidence pack."""
    data = _as_mapping(raw, f"pack {source}")
    schema_version = data.get("schema_version")
    if schema_version != PACK_SCHEMA_VERSION:
        raise PackError(
            f"pack {source} schema_version {schema_version!r} != "
            f"{PACK_SCHEMA_VERSION} (this build supports schema_version "
            f"{PACK_SCHEMA_VERSION} only)"
        )
    pack_id = _required_str(data, "pack_id", f"pack {source} pack_id")
    redaction_verified = data.get("redaction_verified")
    if not isinstance(redaction_verified, bool):
        raise PackError(f"pack {pack_id!r} redaction_verified must be boolean")
    licensing_complete = data.get("licensing_complete")
    if not isinstance(licensing_complete, bool):
        raise PackError(f"pack {pack_id!r} licensing_complete must be boolean")
    records_raw = data.get("records")
    if not isinstance(records_raw, list):
        raise PackError(f"pack {pack_id!r} records must be a list")
    records = tuple(_parse_record(r) for r in records_raw)
    content_raw = data.get("content", [])
    if not isinstance(content_raw, list):
        raise PackError(f"pack {pack_id!r} content must be a list")
    content = tuple(_parse_content_item(c) for c in content_raw)
    estate = _parse_estate(data.get("estate"))
    return EvidencePack(
        schema_version=PACK_SCHEMA_VERSION,
        pack_id=pack_id,
        generated_at=_required_str(data, "generated_at", f"pack {pack_id!r} generated_at"),
        source_commit=_required_str(data, "source_commit", f"pack {pack_id!r} source_commit"),
        operator=_required_str(data, "operator", f"pack {pack_id!r} operator"),
        redaction_verified=redaction_verified,
        licensing_complete=licensing_complete,
        estate=estate,
        records=records,
        content=content,
    )


def signability_report(pack: EvidencePack) -> list[str]:
    """Return the list of reasons a pack is not signable (empty if signable)."""
    reasons: list[str] = []
    if not pack.redaction_verified:
        reasons.append("redaction_verified is false")
    if not pack.licensing_complete:
        reasons.append("licensing_complete is false")
    return reasons


def _json_default(obj: object) -> object:
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    raise TypeError(f"not JSON serializable: {type(obj).__name__}")


def canonical_pack_bytes(pack: EvidencePack) -> bytes:
    """Return the canonical JSON encoding of a pack for hashing.

    Keys are sorted and whitespace is stripped so two independent encoders
    produce byte-identical output (the reproducible-build discipline the 1.0
    release established).
    """
    return json.dumps(
        pack, sort_keys=True, separators=(",", ":"), default=_json_default
    ).encode("utf-8")


def canonical_pack_hash(pack: EvidencePack) -> str:
    """Return the SHA-256 of the pack's canonical JSON encoding."""
    return hashlib.sha256(canonical_pack_bytes(pack)).hexdigest()


def _derive_classification(has_windows: bool, has_endpoint: bool) -> Classification:
    """Map evidence-kind coverage to a public classification.

    ``verified-rw`` requires both a Windows-side and an endpoint observation.
    Windows-only yields ``verified-ro``; endpoint-only or no evidence yields
    ``unknown`` (insufficient to claim a verified read/write on the estate).
    """
    if has_windows and has_endpoint:
        return "verified-rw"
    if has_windows:
        return "verified-ro"
    return "unknown"


def derive_claims(
    packs: Sequence[EvidencePack],
    *,
    allow_unsigned: bool = False,
) -> DerivationResult:
    """Derive the public capability matrix from one or more evidence packs.

    By default, packs that are not signable are excluded and their IDs are
    returned in ``unsigned_pack_ids``. Pass ``allow_unsigned=True`` to derive
    claims from unsigned packs anyway (the caller is responsible for stamping
    the output as a DRAFT — see :func:`render_matrix_markdown`).

    A ``verified-rw`` claim is emitted only when passing evidence for a
    (capability, estate) includes both a ``windows-side`` and an ``endpoint``
    record. Explicit policy classifications (``preserve-only``,
    ``intentional-deny``, ``not-present-on-target``) are taken as-is from any
    record asserting them. ``expected_failure`` records are collected into the
    expected-failures section rather than promoted to a verified claim.
    """
    unsigned: list[str] = []
    admitted_unsigned: list[str] = []
    signable: list[EvidencePack] = []
    for pack in packs:
        if not pack.signable and not allow_unsigned:
            unsigned.append(pack.pack_id)
        else:
            signable.append(pack)
            if not pack.signable:
                admitted_unsigned.append(pack.pack_id)

    claims_by_key: dict[tuple[str, str], PublicMatrixClaim] = {}
    policy_by_key: dict[tuple[str, str], Classification] = {}
    expected_failures: list[ExpectedFailure] = []
    source_ids: list[str] = []

    for pack in signable:
        source_ids.append(pack.pack_id)
        estate_label = pack.estate.label()
        for record in pack.records:
            key = (record.capability, estate_label)
            if record.outcome == "expected_failure":
                expected_failures.append(
                    ExpectedFailure(
                        capability=record.capability,
                        estate=estate_label,
                        tool=record.tool,
                        notes=record.notes,
                        source_pack=pack.pack_id,
                    )
                )
            if record.classification in _POLICY_CLASSIFICATIONS:
                policy_by_key[key] = record.classification
            if record.outcome != "pass":
                continue
            existing = claims_by_key.get(key)
            has_windows = (existing.has_windows if existing else False) or (
                record.evidence_kind == "windows-side"
            )
            has_endpoint = (existing.has_endpoint if existing else False) or (
                record.evidence_kind == "endpoint"
            )
            tools = _merge(existing.tools if existing else (), record.tool)
            docs = _merge(existing.ms_docs if existing else (), record.ms_doc)
            hashes = _merge(
                existing.evidence_hashes if existing else (), record.evidence_hash
            )
            packs_for_claim = _merge(
                existing.source_packs if existing else (), pack.pack_id
            )
            claims_by_key[key] = PublicMatrixClaim(
                capability=record.capability,
                estate=estate_label,
                classification="unknown",  # finalized below
                has_windows=has_windows,
                has_endpoint=has_endpoint,
                tools=tools,
                ms_docs=docs,
                evidence_hashes=hashes,
                source_packs=packs_for_claim,
            )

    finalized: list[PublicMatrixClaim] = []
    for key, claim in claims_by_key.items():
        if key in policy_by_key:
            cls = policy_by_key[key]
        else:
            cls = _derive_classification(claim.has_windows, claim.has_endpoint)
        finalized.append(
            PublicMatrixClaim(
                capability=claim.capability,
                estate=claim.estate,
                classification=cls,
                has_windows=claim.has_windows,
                has_endpoint=claim.has_endpoint,
                tools=claim.tools,
                ms_docs=claim.ms_docs,
                evidence_hashes=claim.evidence_hashes,
                source_packs=claim.source_packs,
            )
        )
    finalized.sort(key=lambda c: (c.capability, c.estate))
    expected_failures.sort(key=lambda f: (f.capability, f.estate))
    return DerivationResult(
        claims=tuple(finalized),
        expected_failures=tuple(expected_failures),
        unsigned_pack_ids=tuple(unsigned),
        admitted_unsigned_ids=tuple(admitted_unsigned),
        source_pack_ids=tuple(source_ids),
    )


def _md_cell(value: str) -> str:
    """Escape pipe characters for a markdown table cell."""
    return value.replace("|", "\\|")


def _merge(existing: tuple[str, ...], value: str) -> tuple[str, ...]:
    """Append *value* if not already present, preserving order."""
    if value in existing:
        return existing
    return (*existing, value)


def render_matrix_markdown(
    result: DerivationResult,
    *,
    stream: TextIO | None = None,
) -> str:
    """Render a derivation result as a markdown public capability matrix.

    The output is stamped ``DRAFT — UNSIGNED PACKS INCLUDED`` when any
    unsigned pack was admitted via ``allow_unsigned``.
    """
    lines: list[str] = []
    if result.admitted_unsigned_ids:
        lines.append(
            "> **DRAFT — UNSIGNED PACKS INCLUDED.** The following packs have not "
            "satisfied the redaction or licensing gates and MUST NOT be used as "
            "release evidence: "
            f"{', '.join(result.admitted_unsigned_ids)}"
        )
        lines.append("")
    lines.append("# GPO Studio public capability matrix (derived)")
    lines.append("")
    lines.append(
        f"Derived from {len(result.source_pack_ids)} signable pack(s): "
        f"{', '.join(result.source_pack_ids) or 'none'}."
    )
    lines.append("")
    lines.append(
        "Every ``verified-rw`` row below is backed by at least one passing "
        "Windows-side record and one passing endpoint record. A row is never "
        "promoted from a local smoke run alone."
    )
    lines.append("")
    lines.append(
        "| Capability | Estate | Classification | Windows | Endpoint | Tools | MS docs |"
    )
    lines.append("|---|---|---|---|---|---|---|")
    for claim in result.claims:
        lines.append(
            f"| {_md_cell(claim.capability)} | {_md_cell(claim.estate)} | "
            f"{_md_cell(claim.classification)} | "
            f"{'yes' if claim.has_windows else 'no'} | "
            f"{'yes' if claim.has_endpoint else 'no'} | "
            f"{_md_cell('; '.join(claim.tools))} | "
            f"{_md_cell('; '.join(claim.ms_docs))} |"
        )
    if not result.claims:
        lines.append("| _(none — no passing evidence yet)_ | | | | | | |")
    if result.expected_failures:
        lines.append("")
        lines.append("## Expected failures (synthetic-reference limitations)")
        lines.append("")
        lines.append("| Capability | Estate | Tool | Notes | Source pack |")
        lines.append("|---|---|---|---|---|")
        for fail in result.expected_failures:
            lines.append(
                f"| {_md_cell(fail.capability)} | {_md_cell(fail.estate)} | "
                f"{_md_cell(fail.tool)} | {_md_cell(fail.notes)} | "
                f"{_md_cell(fail.source_pack)} |"
            )
    output = "\n".join(lines) + "\n"
    if stream is not None:
        stream.write(output)
    return output
