# WP-1A Corpus Coverage Matrix

Status per adapter. ✓ = genuine GPMC capture exists, — = not applicable,
✗ = needs GPMC authoring session.

## Drive Maps (Drives/Drives.xml)

| Dimension | Covered | Gap |
|-----------|---------|-----|
| User scope | ✓ (4 items) | — |
| Computer scope | — | Drive Maps is user-scope only (no computer configuration in GPMC) |
| Action: Create | ✓ (H: Unicode) | — |
| Action: Update | ✓ (M:) | — |
| Action: Replace | ✓ (P:) | — |
| Action: Delete | ✓ (X:) | — |
| All common options | ✓ (across items) | — |
| ILT expression | ✓ (FilterOs + FilterRunOnce) | — |
| Unicode path | ✓ (H: `\\filesrv\shäre-ünïcode`) | — |
| XML-sensitive chars | ✓ (H: label `Ünïcödé <"&> label`) | — |
| Empty/default values | ✓ (X: item 3) | — |

## Local Groups (Groups/Groups.xml)

| Dimension | Covered | Gap |
|-----------|---------|-----|
| Computer scope | ✓ (3 items) | — |
| User scope | ✓ (1 item) | — |
| Action: Create | ✓ (user-scope dev-team) | — |
| Action: Update | ✓ (Administrators) | — |
| Action: Replace | ✓ (Power Users) | — |
| Action: Delete | ✓ (test-delete-group) | — |
| Member ADD | ✓ | — |
| Member REMOVE | ✓ (userAction="REMOVE") | — |
| deleteAllUsers/Groups | ✓ (Power Users) | — |
| All common options | Partial | apply_once not exercised on groups |
| ILT expression | ✓ (FilterGroup + FilterRunOnce on user-scope) | — |
| Unicode member name | ✓ (HRAENET\ünïcode-test-group) | — |

## Scheduled Tasks (ScheduledTasks/ScheduledTasks.xml)

| Dimension | Covered | Gap |
|-----------|---------|-----|
| Computer scope | ✓ (3 items) | — |
| User scope | ✓ (2 items) | — |
| TaskV2 Create | ✓ (user-scope "Create Task") | — |
| TaskV2 Update | ✓ (GpoStudio-Cleanup) | — |
| TaskV2 Replace | ✓ ("Replace Test", multiple triggers) | — |
| TaskV2 Delete | ✓ ("Delete Test", SendEmail action) | — |
| ImmediateTaskV2 Create | ✓ (GpoStudio-Init) | — |
| Multiple triggers | ✓ (weekly + monthly on "Replace Test") | — |
| Weekly/Monthly trigger | ✓ | — |
| SessionStateChangeTrigger | ✓ (RemoteConnect) | — |
| ShowMessage action | ✓ | — |
| SendEmail action | ✓ | — |
| All common options | Partial | apply_once, disabled not exercised |
| ILT expression | ✓ (FilterDomain on user-scope) | — |
| Unicode in command/args | ✓ (user-scope "Create Task") | — |
| Mixed-CSE GPO | ✓ (User/Drives + Machine/Groups + Machine/SchedTasks) | — |

## Cross-cutting

| Dimension | Status |
|-----------|--------|
| Mixed-CSE fixture (one GPO, multiple adapters) | ✓ |
| Unicode and XML-sensitive values | ✓ |
| Deliberate unknown attrs/children | Partial (native unknowns captured; no artificial injection) |
| Unknown-content preservation (import→export) | Partial (no-edit verbatim; edited preserves unknown_attrs/children/task_xml/unknown_props_children) |
| Public backup-import path integration | ✓ (read_backup + collect_gpp_collections; full API endpoint test pending WP-1B) |

## Capture batch 2 (2026-07-27) — eight additional GPP families

Genuine GPMC captures for `EnvVars`, `Files`, `Folders`, `IniFiles`, `Power`,
`Printers`, `Services`, and `Shortcuts`, plus a richer Scheduled Tasks capture
(`WI01A-SchedTasksFull-GPMC`: six items across both sides, all four action
codes, both `TaskV2` and `ImmediateTaskV2`). Ingested under
`tests/fixtures/native-gpp-gpmc/`; sanitization rows appended to
`sanitization-record.json`.

The new Scheduled Tasks capture is a superset of `WI01A-SchedTasks-GPMC` and is
committed under a distinct name rather than replacing it. Consolidating the two
is an open cleanup.

### Extension metadata recovered

Every capture's `Backup.xml` yields the CSE/tool GUID pair for its family —
the metadata `export._GPP_EXTENSION_PROFILES` needs to emit that family into a
native backup. The Scheduled Tasks pair in this batch matches Studio's existing
pinned value exactly, independently confirming that pin.

### Validation status of this batch

| Capture | Status |
|---|---|
| `SchedTasksFull` | cross-validated (report vs backup agree through Studio) |
| `EnvVars`, `Files`, `Folders`, `IniFiles`, `Power` | **corpus only — not validated.** Their families are outside `writer_conformance.NATIVE_GPP_FAMILIES`, so a comparison summarizes to empty on both sides and would pass vacuously. Deliberately excluded from the cross-validation test. |
| `Printers`, `Services`, `Shortcuts` | ~~blocked by parser defects (WI-019)~~ — **fixed 2026-07-28.** All three now import (HTTP 201 through the real endpoint; previously 422). They parse into typed items but are **not** in the report-vs-backup cross-validation set, because their families are outside `NATIVE_GPP_FAMILIES` and would compare empty-to-empty. Covered by `test_wi019_captures_now_import`. |

### Authoring constraint observed

GPMC **rejects `"` in the name field** for Shortcuts and Files, so those two
captures carry no double-quote adversarial case; the character appears only in
Scheduled Tasks arguments, where GPMC permits it. This is a Windows-side
authoring constraint, not a gap in the capture. It raises an open
validation-parity question: Studio does not currently reject `"` in shortcut or
file names, so it accepts input GPMC would refuse.

## `WI01A-NestedILT-GPMC` (2026-07-27) — the P2 fixture

Authored specifically to settle prediction P2, which the batch-2 captures could
not test. `FilterCollection` appears in exactly one other capture
(`WI01A-Power-GPMC`), but P1 came true there — the whole `GlobalPowerOptionsV2`
item is preserved as unknown content, so its nested ILT never reaches
`parse_ilt`. P1 being correct made P2 unobservable in the same fixture.

One Drive Maps item (`N:`), chosen because Drive Maps is already cross-validated,
natively emittable, and parses typed — so the collection actually reaches the ILT
parser. Its targeting is:

```
FilterGroup    GPOSTUDIO\p2-outer     AND      <- typed, before
FilterCollection                      AND
    FilterOrgUnit  OU=Lab-WS2,…       AND
    FilterOrgUnit  OU=Lab-WS1,…       OR
FilterDomain   ad.hraedon.com         AND      <- typed, after
```

Four properties make it discriminating:

1. the collection's children are *mapped* predicate types, so a flattening bug
   would surface them as top-level typed predicates — the only way to tell
   "preserved as a group" from "silently flattened";
2. typed predicates sit on **both sides**, the only arrangement that can catch
   a reordering;
3. `AND` outside with `OR` inside, because grouping is the point —
   `A AND (B OR C)` is not `A AND B OR C`;
4. no OS filter, deliberately, so a P2 failure cannot be confused with WI-021.

**Result: P2 confirmed.** `ilt_filter.items` is three entries in document order —
typed `group`, one opaque string holding the entire `FilterCollection` subtree
with both `FilterOrgUnit` children and their `bool` operators, typed `domain`.
It survives re-serialization from the typed model (via `mark_edited`, so the
verbatim source bytes are dropped and the XML is rebuilt), and the GPMC report
agrees with the backup. Pinned by `test_nested_ilt_collection_is_preserved_whole_and_in_order`
and `test_nested_ilt_survives_reserialization_from_the_typed_model`.

Nested ILT is therefore **preserve-only**: correct and lossless, but not
modelled. Studio cannot display, edit, or reason about a targeting collection.

### WI-019 postscript — the blast radius was larger than first recorded

The first note on these three captures said the families are listed out-of-scope
in the capability matrix, so a parser defect was "not a false shipped claim".
That understated it. `collect_gpp_collections` raises `GppError`, and
`api.import_backup` maps that to `StudioError("Invalid or malformed policy data
in backup")` — rejecting the **entire backup**, not the offending family.

`GMPC backup import (single-GPO)` *is* a supported capability. So a genuine
GPMC backup containing any Services item, a Replace printer, or a shortcut with
an unset window style could not be imported at all, and the operator got a
message naming neither the family nor the value.

Measured end-to-end through the HTTP endpoint before and after the fix:

| capture | before | after |
|---|---|---|
| `WI01A-Services-GPMC` | 422 | **201** |
| `WI01A-Printers-GPMC` | 422 | **201** |
| `WI01A-Shortcuts-GPMC` | 422 | **201** |
| `WI01A-DriveMaps-GPMC` (control) | 201 | 201 |

One existing unit test asserted the numeric `startupType` codes and had to be
corrected. It was self-consistent — Studio round-tripping its own output — but
wrong against Windows. That is the same trap this plan exists to catch, and it
survived inside the test suite until a native capture contradicted it.
