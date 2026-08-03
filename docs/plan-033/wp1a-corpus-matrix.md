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


## `WI01A-OS-ILT` (2026-07-28) — the OS-filter vocabulary capture

One Drive Maps item carrying one `FilterOs` predicate per entry the Windows
Server 2025 Targeting Editor offers: every Server 2025 entry and every
Windows 10 entry. Captured to close the open question in WI-021, since
MS-GPPREF's `version` enumeration stops at `WINTHRESHOLDSRV` and the spec
explicitly permits implementations to add values.

### Three findings

**1. The spec's `version` enumeration is complete in practice.** Nothing in the
capture exceeds `WINTHRESHOLDSRV`. Server 2025 reports `WINTHRESHOLDSRV`;
Windows 10 reports `WINTHRESHOLD`. GPMC's OS-filter vocabulary was never
extended past the Threshold generation.

**The complete Product dropdown** on Windows Server 2025 (operator
transcription, 2026-07-28), thirteen entries:

| # | dialog label | `version` | source |
|---|---|---|---|
| 1 | Windows XP | `XP` | inferred |
| 2 | Windows Server 2003 | `2K3` | inferred |
| 3 | Windows Server 2003 R2 | `2K3R2` | inferred |
| 4 | Windows Vista | `VISTA` | inferred |
| 5 | Windows Server 2008 | `2K8` | inferred |
| 6 | Windows 7 | `WIN7` | inferred |
| 7 | Windows Server 2008 R2 | `2K8R2` | inferred |
| 8 | Windows 8 | `WIN8` | inferred |
| 9 | Windows Server 2012 **Family** | `WIN8S` | inferred |
| 10 | Windows 8.1 | `WINBLUE` | inferred |
| 11 | Windows Server 2012 R2 **Family** | `WINBLUESRV` | inferred |
| 12 | Windows 10 | `WINTHRESHOLD` | **observed** |
| 13 | Windows Server 2025 **Family** | `WINTHRESHOLDSRV` | **observed** |

Only rows 12 and 13 are observed — those are the entries the capture exercised.
The rest are inferred from the one-to-one correspondence between the thirteen
dialog entries and the thirteen post-2000 values in the spec enumeration, in
order. The pre-XP values (`95`, `98`, `ME`, `NT`, `2K`) are not offered.

**No Server 2016 / 2019 / 2022 entries, and no Windows 11 entry.** The
consequence is worth stating plainly:

> **An ILT OS filter cannot distinguish Windows Server 2016 from Server 2025**,
> nor Windows 10 from Windows 11. Every server from the Threshold generation
> onward reports the same `version="WINTHRESHOLDSRV"`. An operator selecting
> "Windows Server 2025 Family" is matching every server 2016 and later, and
> there is no `FilterOs` expression for "Server 2022 only".

> **Endpoint-confirmed 2026-08-03.** The Windows 11 half of that collision is no
> longer an inference. The two-guest endpoint lane ran against a Windows 11
> Enterprise client (build 26200) on the evidence estate with a matched pair of
> rows: a `FilterOs` for `WINTHRESHOLD` **applied**, and one for
> `WINTHRESHOLDSRV` **did not**. Both polarities were needed — a client code
> that matches proves nothing on its own if the server code matches too — and
> each was authored twice, once by Studio and once by hand in the genuine GPMC
> shape, so a wrong product code would have shown up as the native control
> failing rather than as a Studio defect. Verdict:
> `wp1b-evidence/endpoint-result-phase4-estate.json`, finding `OS-VOCABULARY`.
>
> So `WINTHRESHOLD` really does cover Windows 11, and the dropdown's unqualified
> "Windows 10" label really is the trap it looked like.

To be fair to GPMC: three server entries are labelled **"Family"**, which is an
honest signal that the value spans a range rather than naming one release. But
"Windows Server 2025 Family" reads naturally as *the 2025 family* — variants of
2025 — rather than *2016 and later*, so the label mitigates the trap without
removing it. The client entries are labelled "Windows 10" with no "Family"
qualifier at all, despite `WINTHRESHOLD` covering Windows 11 too.

Targeting a specific modern build needs a different predicate —
`FilterRegistry` against the build number, or `FilterWmi` — not `FilterOs`.

**2. Four editions exist only in the spec's prose**, confirmed real by this
capture: `64STGSTD`, `64STGWKGRP`, `64MPPREM`, `64ESSSOL`. The published XSD
omits them. Accepting only the XSD would reject genuine GPMC output.

**3. `64PRO` appears in neither the XSD nor the prose.** It is known solely
because GPMC emitted it. This is the case the preserve-don't-reject design in
`IltOsCriteria` was built for: `unrecognized()` surfaced it on first contact
instead of the parse failing or the value silently degrading.

### Attribute decode, from the operator's view of the dialog

| dialog control | attribute | observed |
|---|---|---|
| Product | `version` | `WINTHRESHOLDSRV` (Server 2025), `WINTHRESHOLD` (Windows 10) |
| Edition | `edition` | 11 distinct values; see below |
| Server role | `type` | `SV` when set; **client entries offer no role** and emit `NE` |
| Release | `sp` | `Gold` = "No service packs installed"; `NE` = Any |

The capture covers every entry the dialog offers for those two products. The
XSD edition values absent from it (`AS`, `HM`, `MC`, `TPC`, `TSE`, `SBS`, `WEB`,
`64`, `64DC`, `SRV`, and the prose-only `64MPSTD`) belong to older operating
systems the dialog does not list for Server 2025 or Windows 10, so their
absence is expected rather than a coverage gap.

Each accepted edition value therefore carries visible provenance —
`OS_EDITION_XSD_VALUES`, `OS_EDITION_PROSE_ONLY_VALUES`, or
`OS_EDITION_CORPUS_OBSERVED_VALUES` — so nobody later has to guess whether a
value came from the specification or from observation.

Pinned by `test_every_gpmc_os_entry_parses_with_recognized_values`,
`test_gpmc_os_vocabulary_stops_at_the_threshold_generation`, and
`test_capture_exercises_undocumented_and_prose_only_editions`.
