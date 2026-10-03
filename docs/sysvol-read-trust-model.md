# SYSVOL read-trust model

GPO Studio ingests ADMX/ADML Administrative Template files from local copies
of SYSVOL-style directories, GPMC backup extracts, vendor packs, or curated
repositories. Ingestion is read-only. The web process never writes to Active
Directory, SYSVOL, or any domain path.

## Trust boundary

Imported ADMX/ADML content is **untrusted input** until it has been:

1. Bounded by the safe XML parser (size, depth, element count, text length,
   attribute length, and entity expansion limits).
2. Mechanically classified for licensing (Microsoft, vendor, or synthetic).
3. Reduced to hashes when the classification forbids redistribution.

The parser and classifier run inside the application process, on a copy of the
files. No bytes cross the boundary back to AD or SYSVOL.

## Retention rules

Every ingested file receives a mechanical license classification:

| Classification | Retention | Use case |
|---|---|---|
| `in-repo` | Full file may be kept in the workspace / repository | Synthetic or originally authored templates |
| `hash-reference` | SHA-256 only; transient copyrighted bytes are discarded | Microsoft or third-party vendor ADMX/ADML |
| `excluded` | Not retained at all | Operator-supplied deny-list entries |

Microsoft-copyrighted content (a declared `Microsoft.*` namespace, a known
Microsoft file name, or a Microsoft copyright string in ADML) and non-Microsoft
vendor content are never classified as `in-repo`. If a source claims `in-repo`
but any of its files is mechanically classified as `hash-reference` or
`excluded`, ingestion is rejected.

## No transient copyrighted retention

`_scan_directory` reads each file once to compute a SHA-256 and, for ADMX
files, to inspect namespaces and the paired ADML. It does not store the raw
bytes in `TemplateFile`, `TemplateSource`, or the workspace. Only hashes and
parsed policy metadata cross the trust boundary into the catalogue.

## Parser hardening

ADMX/ADML parsing uses `xml_safety.parse_xml_bounded` with conservative
limits (10 MiB per file, 100,000 elements, depth 100). It rejects entity
declarations instead of expanding them. A malformed file is reported on its own
and does not abort ingestion of the rest.
