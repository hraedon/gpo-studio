"""Content-addressed encoding for the native XML a snapshot retains.

WI-061. An import retains ``Backup.xml`` and ``gpreport.xml`` as exact base64
bytes on the GPO's :class:`~gpo_studio.model.BackupInventory`, and
``GPO.to_dict()`` is ``asdict``, so every snapshot the store wrote carried its
own full copy of both documents -- workspace growth was O(revisions x report
size), bounded only by the 50MB per-file import cap.

The persistence layer now stores each distinct document once, in the
``retained_documents`` table, keyed by the SHA-256 of the *decoded* bytes, and
leaves digest references in the snapshot JSON. Rehydration happens at the
store's read boundary, so every GPO handed out in memory still carries exact
bytes and no consumer of the store API observes the encoding.

A snapshot written before schema v4 keeps its inline bytes: the v4 migration
rewrites them, but a snapshot that still carries base64 alongside no digest
reads back unchanged. The two forms never mix -- a field has either bytes or
a digest, never both.

The parsed Folder Redirection document (``GPO.fdeploy``, WI-068) had the same
growth, worse: ``asdict`` serializes ``raw_text`` *and* every view derived
from it (sections, their lines, preamble, parse warnings), so each revision
stored the file several times over. :func:`extract_fdeploy` reduces the
serialized document to its native file bytes -- UTF-16LE with its BOM, the
form ``decode_fdeploy`` accepts -- files them in the same table under the
same rule (SHA-256 of the exact native bytes), and leaves
``{"native_digest": ...}``. :func:`apply_fdeploy` re-parses those bytes back
into the full ``asdict`` form, so a rehydrated snapshot equals what
``json.loads(json.dumps(gpo.to_dict()))`` produced before. A snapshot written
before this change carries the inline document and reads back unchanged.

:func:`extract_documents` and :func:`apply_documents` stay XML-only: the
schema v4 migration is written against them, and a migration's behaviour
must not move under it. The store calls :func:`extract_snapshot_documents`
and :func:`apply_snapshot_documents`, which cover both.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict
from typing import Any, NamedTuple

from .fdeploy import FdeployError, decode_fdeploy, parse_fdeploy

#: (bytes field, digest field) pairs inside a serialized ``backup_inventory``.
DOCUMENT_FIELDS: tuple[tuple[str, str], ...] = (
    ("backup_xml_base64", "backup_xml_digest"),
    ("report_xml_base64", "report_xml_digest"),
)


#: The one key a digest-form serialized ``fdeploy`` carries.
FDEPLOY_DIGEST_FIELD = "native_digest"

#: The byte-order mark ``decode_fdeploy`` requires and R3 measured.
_UTF16LE_BOM = b"\xff\xfe"


class SnapshotDocumentError(Exception):
    """A snapshot's retained documents cannot be encoded or rehydrated."""


class RetainedDocument(NamedTuple):
    """One document extracted from a snapshot: its digest and exact bytes."""

    digest: str
    content_base64: str
    byte_length: int


def extract_documents(data: dict[str, Any]) -> list[RetainedDocument]:
    """Replace inline document bytes in ``data`` with digest references.

    Mutates the ``backup_inventory`` mapping inside ``data`` (when present and
    non-empty) and returns the documents it removed. An empty result means
    there was nothing to extract and ``data`` was left untouched.

    Digests are taken over the decoded native bytes, not the base64 spelling,
    so the same document stored by two imports identifies once.
    """
    inventory = data.get("backup_inventory")
    if not isinstance(inventory, dict):
        return []
    extracted: list[RetainedDocument] = []
    for bytes_field, digest_field in DOCUMENT_FIELDS:
        content = inventory.get(bytes_field)
        if not isinstance(content, str) or not content:
            continue
        try:
            raw = base64.b64decode(content, validate=True)
        except (binascii.Error, ValueError) as error:
            raise SnapshotDocumentError(
                f"backup_inventory.{bytes_field} is not valid base64: {error}"
            ) from error
        digest = hashlib.sha256(raw).hexdigest()
        extracted.append(
            RetainedDocument(digest=digest, content_base64=content, byte_length=len(raw))
        )
        inventory[bytes_field] = ""
        inventory[digest_field] = digest
    return extracted


def snapshot_digests(data: dict[str, Any]) -> list[str]:
    """Every document digest a snapshot references, in field order.

    The retained XML first, then the ``fdeploy`` document's native bytes.
    """
    digests: list[str] = []
    inventory = data.get("backup_inventory")
    if isinstance(inventory, dict):
        for _, digest_field in DOCUMENT_FIELDS:
            digest = inventory.get(digest_field)
            if isinstance(digest, str) and digest:
                digests.append(digest)
    fdeploy = data.get("fdeploy")
    if isinstance(fdeploy, dict):
        digest = fdeploy.get(FDEPLOY_DIGEST_FIELD)
        if isinstance(digest, str) and digest:
            digests.append(digest)
    return digests


def apply_documents(
    data: dict[str, Any], lookup: Callable[[str], str | None]
) -> None:
    """Rehydrate digest references in ``data`` using ``lookup``.

    The inverse of :func:`extract_documents`: each digest field is replaced by
    the document's base64 bytes and removed. A digest whose document cannot be
    found is a corrupt workspace, not an empty inventory, and raises.
    """
    inventory = data.get("backup_inventory")
    if not isinstance(inventory, dict):
        return
    for bytes_field, digest_field in DOCUMENT_FIELDS:
        digest = inventory.get(digest_field)
        if not isinstance(digest, str) or not digest:
            continue
        content = lookup(digest)
        if content is None:
            raise SnapshotDocumentError(
                f"snapshot references retained document {digest} "
                f"({bytes_field}) that the workspace does not hold"
            )
        inventory[bytes_field] = content
        del inventory[digest_field]


def extract_fdeploy(data: dict[str, Any]) -> list[RetainedDocument]:
    """Replace a serialized ``fdeploy`` document with a digest reference.

    The document's authority is ``raw_text``; every other key ``asdict``
    wrote is a view ``fdeploy_from_dict`` recomputes and never reads, so it is
    dropped rather than stored. The text is filed as the native file's bytes
    -- the BOM followed by ``raw_text`` in UTF-16LE -- because that is the
    only spelling that is both lossless for any ``str`` the parser accepts and
    the same content-addressing rule the retained XML already uses: the
    SHA-256 of the exact native bytes. For a document ``read_backup`` parsed,
    the digest is therefore the file's own, equal to ``native_digest()`` and
    to the backup inventory's row for ``fdeploy1.ini``.

    Mutates ``data`` and returns the document it removed; an absent document
    (``None``) or one already in digest form returns nothing and is left as
    it is.
    """
    fdeploy = data.get("fdeploy")
    if fdeploy is None:
        return []
    if not isinstance(fdeploy, dict):
        raise SnapshotDocumentError("fdeploy is not a serialized document")
    if FDEPLOY_DIGEST_FIELD in fdeploy:
        _require_digest_form(fdeploy)
        return []
    raw_text = fdeploy.get("raw_text")
    if not isinstance(raw_text, str):
        raise SnapshotDocumentError("fdeploy has no raw_text string")
    try:
        native = _UTF16LE_BOM + raw_text.encode("utf-16-le")
    except UnicodeEncodeError as error:
        raise SnapshotDocumentError(
            f"fdeploy.raw_text cannot be encoded as UTF-16LE: {error}"
        ) from error
    digest = hashlib.sha256(native).hexdigest()
    data["fdeploy"] = {FDEPLOY_DIGEST_FIELD: digest}
    return [
        RetainedDocument(
            digest=digest,
            content_base64=base64.b64encode(native).decode("ascii"),
            byte_length=len(native),
        )
    ]


def apply_fdeploy(
    data: dict[str, Any], lookup: Callable[[str], str | None]
) -> None:
    """Rehydrate a digest-form ``fdeploy`` into its full serialized form.

    The inverse of :func:`extract_fdeploy`. The retained bytes are checked
    against their digest, decoded with the strict native codec and re-parsed,
    and the document is written back in the JSON form ``asdict`` gave it, so
    revision snapshots served through the API keep their shape. A missing,
    altered or undecodable document is a corrupt workspace and raises. An
    inline (pre-change) document is left exactly as stored.
    """
    fdeploy = data.get("fdeploy")
    if not isinstance(fdeploy, dict) or FDEPLOY_DIGEST_FIELD not in fdeploy:
        return
    digest = _require_digest_form(fdeploy)
    content = lookup(digest)
    if content is None:
        raise SnapshotDocumentError(
            f"snapshot references retained document {digest} "
            "(fdeploy) that the workspace does not hold"
        )
    try:
        native = base64.b64decode(content, validate=True)
    except (binascii.Error, ValueError) as error:
        raise SnapshotDocumentError(
            f"retained document {digest} (fdeploy) is not valid base64: {error}"
        ) from error
    if hashlib.sha256(native).hexdigest() != digest:
        raise SnapshotDocumentError(
            f"retained document {digest} (fdeploy) does not hash to its digest"
        )
    try:
        document = parse_fdeploy(decode_fdeploy(native))
    except FdeployError as error:
        raise SnapshotDocumentError(
            f"retained document {digest} (fdeploy) does not parse: {error}"
        ) from error
    # asdict yields tuples; the stored form was JSON, whose arrays read back
    # as lists. Round-trip through JSON so the rehydrated form is that one.
    data["fdeploy"] = json.loads(json.dumps(asdict(document)))


def extract_snapshot_documents(data: dict[str, Any]) -> list[RetainedDocument]:
    """Every document the store files for a snapshot: retained XML, then fdeploy."""
    return [*extract_documents(data), *extract_fdeploy(data)]


def apply_snapshot_documents(
    data: dict[str, Any], lookup: Callable[[str], str | None]
) -> None:
    """Rehydrate every digest reference :func:`extract_snapshot_documents` left."""
    apply_documents(data, lookup)
    apply_fdeploy(data, lookup)


def _require_digest_form(fdeploy: dict[str, Any]) -> str:
    """Return the digest of a digest-form ``fdeploy``, refusing a mixed form.

    A reference carries nothing but the digest. Inline keys beside it would
    be two authorities for one document, which no writer produces.
    """
    digest = fdeploy.get(FDEPLOY_DIGEST_FIELD)
    if set(fdeploy) != {FDEPLOY_DIGEST_FIELD} or not isinstance(digest, str) or not digest:
        raise SnapshotDocumentError(
            "fdeploy mixes a digest reference with inline content"
            if set(fdeploy) != {FDEPLOY_DIGEST_FIELD}
            else "fdeploy digest reference is empty"
        )
    return digest
