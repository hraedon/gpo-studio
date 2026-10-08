# Artifact store scope

`artifact_store.py` is a local integrity and policy utility. It has no Windows
wire contract and is not Windows-certified by Plan 033 or Plan 034.

The intake checks are limited. An EICAR test marker is rejected, and a small
set of regex heuristics marks possible exposed secrets as `suspicious`. A `clean` result means only that these local checks found
nothing; it is not an antivirus or malware-free assertion.

Executable files may stay in the local store, but `.exe` and `.dll` files are
not publication-eligible while `signer` is only free-text metadata. Executable
publication needs a verified signer-ingestion contract first.

The content hash is the immutable canonical artifact identity. Repeated
byte-identical arrivals return the first row unchanged and append a
`duplicate-arrival` provenance event containing the later name, owner, and
source.

`check_publication_safety()` is a read-only eligibility check. Approval records
human review state. A later check may return ineligible because the content or
policy no longer passes; the approved status and provenance are kept.
