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
"""

from __future__ import annotations

import base64
import binascii
import hashlib
from collections.abc import Callable
from typing import Any, NamedTuple

#: (bytes field, digest field) pairs inside a serialized ``backup_inventory``.
DOCUMENT_FIELDS: tuple[tuple[str, str], ...] = (
    ("backup_xml_base64", "backup_xml_digest"),
    ("report_xml_base64", "report_xml_digest"),
)


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
    """Every document digest a snapshot references, in field order."""
    inventory = data.get("backup_inventory")
    if not isinstance(inventory, dict):
        return []
    digests: list[str] = []
    for _, digest_field in DOCUMENT_FIELDS:
        digest = inventory.get(digest_field)
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
