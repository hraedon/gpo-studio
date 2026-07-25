# Plan 022 — REVIEW AND REFINE gate: decision record (2026-07-25)

Status: **passed**. This document is the outcome of the
`REVIEW AND REFINE — REQUIRED` gate in
[`plans/022-administrative-templates-and-starter-gpo-parity.md`](../../plans/022-administrative-templates-and-starter-gpo-parity.md).
The gate required testing the parser and settings browser against the first
full Microsoft template corpus and at least three vendor packs, then reviewing
parser generality, identity rules, and UI scalability.

---

## Corpus tested

| Source | ADMX files | Policies | Categories | Load errors |
|---|---|---|---|---|
| Microsoft Windows Server 2025 (`C:\Windows\PolicyDefinitions` on `mvmcitest01`) | 223 | 3 532 | 429 | 0 (after bug fixes) |
| Google Chrome (`dl.google.com` policy_templates.zip) | 2 | 727 | 39 | 0 |
| Mozilla Firefox (`github.com/mozilla/policy-templates` v8.0) | 2 | 407 | 48 | 0 |
| Adobe Reader DC (`github.com/nsacyber/Windows-Secure-Host-Baseline`) | 1 | 44 | 8 | 0 |
| **Total** | **228** | **4 710** | **524** | **0** |

The Microsoft corpus was read from the lab machine `mvmcitest01`
(Windows Server 2025, domain-joined to `ad.hraedon.com`). Vendor packs were
downloaded to `/tmp` for testing only — none are committed to the repository
(per Plan 021 Decision 4, ADMX content is `hash-reference` or `excluded`).

---

## Decision 1 — Parser generality: four bugs found and fixed, parser now passes

The initial corpus run exposed four bugs that caused silent data loss or
crashes. All were fixed with test coverage before the gate was assessed.

### Bug 1 (P0) — `supportedOn` definitions not parsed

`_parse_supported_on` looked for `<definition>` as a direct child of
`<supportedOn>`, but real ADMX nests definitions inside a `<definitions>`
wrapper. Result: 0 of 201 supportedOn definitions were parsed; the settings
browser fell back to raw ref strings (e.g. `SUPPORTED_Windows7OrBITS35`).

**Fix:** descend into the `<definitions>` child; handle both the wrapper and
legacy direct-child shapes. After the fix all 201 definitions resolve to
human-readable display names.

### Bug 2 (P0) — `multiText` element kind mismatch

The element kind_map used `"multitext"` (lowercase) but the actual ADMX tag is
`multiText` (capital T). 93 elements were parsed as `"unknown"`, making
affected policies unconfigurable (`ValidationError` on unknown kinds).

**Fix:** corrected the kind_map key to `"multiText"`.

### Bug 3 (P1) — `encoding='unicode'` not handled

`Search.admx` is UTF-16 LE and declares `encoding='unicode'` — a Windows alias
Python's ElementTree does not recognize. This crashed `load_catalogue`.

**Fix:** `_normalize_xml_encoding` in `xml_safety.py` detects the UTF-16 BOM,
strips the `encoding` declaration, and re-encodes to UTF-8 before parsing.

### Bug 4 (P1) — `load_catalogue` crashes on first file error

A single malformed ADMX file aborted the entire catalogue load with no recovery.

**Fix:** per-file `build_catalogue` is now wrapped in try/except; errors are
collected into `AdmxCatalogue.load_errors` and logged as warnings; loading
continues with remaining files.

### Bug 5 (P2) — `find_adml` case-sensitive on Linux

Three ADML files (`InetRes.adml`, `KDC.adml`, `Messaging.adml`) have mixed-case
names that didn't match on case-sensitive filesystems.

**Fix:** `find_adml` now falls back to a case-insensitive directory scan when
the exact path doesn't exist.

### Post-fix corpus result

After all fixes, the full 223-file Microsoft corpus parses with **zero errors**
and **3 532 policies** discovered. All six element kinds (`boolean`, `decimal`,
`text`, `enum`, `list`, `multiText`) are handled. Feature coverage:

| Feature | Count | % of 3 532 |
|---|---|---|
| Has elements | 1 624 | 46.0 % |
| Has presentation | 1 624 | 46.0 % |
| Has value_name (on/off) | 2 149 | 60.8 % |
| Has explicit enabledValue | 1 744 | 49.4 % |
| Has explicit disabledValue | 1 711 | 48.4 % |
| Has enabledList | 133 | 3.8 % |
| Has disabledList | 147 | 4.2 % |

---

## Decision 2 — Identity rules: namespace-aware qualified ID is correct

The corpus exposed **14 bare-name collisions** across namespaces within the
Microsoft corpus and **23 cross-vendor collisions** between Chrome and Firefox
(e.g. `ExtensionSettings`, `AllowFileSelectionDialogs`).

The parser handles these correctly:
- Bare-name lookup raises `AmbiguousPolicyError` with candidate qualified IDs.
- Qualified lookup (`namespace:name`) resolves unambiguously.
- The settings browser marks ambiguous policies with an `ambiguous` badge and
  lists `ambiguous_with` candidates.

The `qualified_id` design (`namespace:name`) is validated as the correct
cross-file identity. No refinement needed.

---

## Decision 3 — UI scalability: truncation and count logic passes

The `configured_settings` API endpoint truncates to `limit` (default 200) and
returns both the truncated list and the total count. The frontend displays
`"N of M"` when truncation occurs.

Verified with 251 configured policies: API returns 200 items,
`resolved_count` = 251, frontend shows `"200 of 251"`. Namespace collision
detection and settings browser round-trip both pass.

---

## Decision 4 — No new shared value/control types exposed

The corpus did not expose any shared value or control types beyond those
already supported. All six ADMX element kinds, all three policy classes
(Machine / User / Both), list variants (explicitValue, valuePrefix, plain),
enabled/disabled value lists, delete-value/delete-key behavior, and
presentation references are handled.

Plans 023–031 do not require refinement based on corpus findings.

---

## Code-hardening performed during this gate

Three charter-compliance and correctness issues from the prior session's
reflection were addressed and adversarially reviewed:

1. **`delete_starter_gpo` audit trail** — the method previously hard-deleted the
   GPO and all revisions without recording actor/reason, violating the charter's
   "every mutation creates an immutable revision." A `deletion_log` table (v1→v2
   migration) now records the deletion in the same transaction before hard-delete.
2. **Bulk action partial-failure safety** — the frontend split bulk state changes
   by side and made sequential API calls, risking partial updates. The backend
   now accepts per-policy sides; the frontend sends a single atomic request.
3. **OpenAPI documentation** — `configured_settings` now has Pydantic response
   models documenting all fields including `ambiguous` and `ambiguous_with`.

An adversarial review (nemotron) confirmed transaction atomicity, actor/reason
capture, migration idempotency, and `assert_never` charter compliance. Four
additional test gaps (audit rollback on conflict, Both-class without side,
backward compatibility, `policy_sides` validation) were addressed.

---

## Contract state after this gate

- Plan 022 (Administrative Templates + Starter GPO parity) is **complete**. All
  four work packages (ADMX/ADML semantics, template repositories, authoring and
  reporting, Starter GPO lifecycle) are implemented, lab-verified, and
  adversarially reviewed.
- The parser handles the full Microsoft Windows Server 2025 corpus and three
  vendor packs (Google Chrome, Mozilla Firefox, Adobe Reader DC) with zero
  errors.
- Plans 023–031 proceed without refinement; no new shared value/control types
  were exposed by the corpus.
- Plan 023 (Estate, scope, delegation, WMI, and loopback parity) is next in
  sequence and may begin implementation.

## Open follow-ups

1. **Legacy ADM parser** — `adm.py` handles common `.adm` patterns but no real
   `.adm` files were found on the lab machine (`C:\Windows\INF` is empty on
   Server 2025). The parser has synthetic test coverage only. If real `.adm`
   files become available, test against them.
2. **Starter GPO derivation prefix** — `derive_gpo_from_starter` copies setting
   IDs verbatim; `fork_gpo` prefixes with `forked-`. Consider a `derived-`
   prefix for traceability.
3. **Cross-tab Starter GPO list refresh** — the Starter GPO list dialog does not
   refresh when a Starter GPO is created/deleted from another browser tab.
   Low priority (same-tab refresh works correctly).
