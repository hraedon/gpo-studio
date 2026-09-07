# Artifact store scope

`artifact_store.py` is a local integrity and policy utility. It has no Windows
wire contract and is not Windows-certified by Plan 033 or Plan 034.

The intake checks are deliberately limited: an EICAR test marker is rejected,
and a small set of regex heuristics marks possible exposed secrets as
`suspicious`. A `clean` result means only that these local checks found
nothing; it is not an antivirus or malware-free assertion.

Executable files may remain in the local store, but `.exe` and `.dll` files are
not publication-eligible while `signer` is only free-text metadata. A future
verified signer-ingestion contract must be defined before executable publication
is supported.

Content hashes remain the immutable canonical artifact identity. Repeated
byte-identical arrivals return the first row unchanged and append a
`duplicate-arrival` provenance event containing the later name, owner, and
source.

`check_publication_safety()` is a read-only eligibility check. Approval records
human review state; a later check may return ineligible because content or
policy no longer passes, while preserving the approved status and provenance.
