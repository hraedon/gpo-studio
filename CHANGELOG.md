# Changelog

All notable changes to GPO Studio are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Current version: `1.1.0`. The previous final release was `1.0.0`.

## [Unreleased]

- Require registry-recovery previews to match the inspected source, preserving
  exact row facts and order. A creation conflict retires its current review
  while retaining operator inputs; revision changes explain fresh inspection,
  and retired responses leave newer reviews alone (WI-091).

- Preview an explicit keep/omit choice for every source registry row and create
  a separate registry-only draft with fresh UUIDs. Preserve chosen native fields
  and the original policy/history; show omitted policy components and advisory
  editor identity warnings separately from validation and export eligibility
  (WI-087). Native Windows policy qualification remains separate.
  Default draft names reserve space for the recovery suffix and preserve
  Unicode characters, including long names that already carry that suffix.

- Roll back workspace writes when a Python exception or cancellation interrupts
  creation, editing, baseline import, or recovery deletion. Previously, some
  failures left partial rows visible on the connection and prevented the next
  write. The original exception is preserved; baseline items committed before
  a later item fails remain imported (WI-089).

- Keep raw ADMX rows visible as unresolved when their stored side or hive no
  longer matches their structured editor identity. Preserve native state
  observations and prevent those rows from supplying decoded child values.

- Refuse ADMX configuration that introduces duplicate native registry addresses,
  including conflicts with retained forked/imported/raw rows and overlapping
  writes in a single or bulk policy operation, before committing a revision.

- Observe complete native scalar-marker patterns as well as policy lists; expose
  uncertainty and unresolved historical state records at moved addresses while
  preserving child values, raw history and independent export eligibility.
- Document that fork-prefixed/imported ADMX rows are not automatically selected
  by reconfiguration and require explicitly reviewed recovery.

- Read raw legacy list ordinals without integer-conversion failures and keep
  unorderable digit-like ordinals visible as unresolved records, while preserving
  previously successful decimal spellings, source names and historical list order.
- Show policy identities when accepted ADMX display names are missing or blank,
  keeping configured-policy controls and catalogue review visibly labelled.

- Preserve uncertainty from historical state records outside current list-policy
  patterns, regardless of the current writer's identity family; keep ordinary
  legacy child ordinals separate from policy-state evidence.

- Observe complete Enabled and Disabled list-policy patterns in the settings browser;
  show Indeterminate for identical, incomplete or unobservable patterns without changing
  legal authoring states, exports or stored policy records.
- Admit legacy ADMX IDs only after checking complete writer languages and fully
  assembled owner frames, including side boundaries and arbitrary list ordinals;
  retain canonical empty policy owners accepted by the parser and writer.
- Plan configured-setting element bindings once per policy/side, keeping historical
  state/fixed-list writes out of editable lists and requiring address evidence for
  legacy disabled-element records. Preserve uncertain records visibly and keep
  list-heavy historical reads from repeatedly scanning the source schema.
- Address legacy registry setting IDs exactly in edit, comment and delete actions,
  including URL delimiters, percent escapes and slashes.

- Keep multiply parseable legacy ADMX identities unresolved and refuse automatic
  reconfiguration even when current catalogue addresses or edited sides appear unique;
  preserve raw records/history and document explicitly reviewed recovery.

- Compute the ADMX identity family once per configured policy/side group so large
  policies avoid repeated schema scans in the settings browser.

- Keep fixed policy-list values separate from editable lists when their generated
  setting names would overlap, including empty editable lists.
- Reject duplicate resolved policy/side operations in bulk authoring. Preserve
  distinct ADMX writes with source-stable structured IDs where legacy names
  collide, and replace exactly reviewed policy rows rather than overlapping
  textual prefixes. Fully qualified URI policy IDs work through the existing
  detail/configure routes.
- Refuse duplicate registry editor identities before commits and ambiguous
  ID-targeted edits, comments or deletions. Keep legacy heads/history readable;
  document explicit recovery without silently dropping another registry record.

- Resolve prior warnings only after explicitly classified committed writes; keep
  semantic POST previews free of transport-level UI side effects.
- Release pending read controls independently of retired result publication, and
  resume uncovered read-only views once with their original error authority.
  Keep newer drafts, warnings, requests and dialog sessions in control.
- Preserve intentional focus moves during a dialog's initial focus frame.

- Preserve every ADMX source element ID, including `__proto__`, when collecting
  Enabled field values for submission.
- Keep workspace header actions and long policy titles inside the viewport at desktop
  and narrow widths, while retaining scrolling tabs and registry tables.
- Restore keyboard focus to the same row action after an owned save redraws the
  table. Newer editors, policy changes and intentional focus moves retire an old
  dialog's scheduled focus return.
- Share read ownership across views and previews so retired responses cannot replace
  current warnings, draft values, reviewed selections or pending download controls.
  Keep useful background cache admission and dispatched mutation outcomes intact.
- Retire ADMX lookups when search text changes; keep obsolete lookup and catalogue-read
  failures out of newer views, warnings and drafts while retaining active failure messages.
- Refresh reopened Starter libraries after committed changes and keep the ordinary
  policy sidebar consistent with server membership without changing unrelated editors.
- Retire deleted policy views and older responses; preserve unsaved editor values
  and require a renewed review before saving to a recreated policy identity.
- Report request failures through the current view or editor, preserving newer
  warnings and separating completed imports from later list-refresh failures.
- Bind reviewed downloads to the policy revision and review hash, retaining inline
  export errors without affecting reopened reviews.
- Replace the conflict comparison's loading message after an initial read fails,
  while preserving an already reviewed comparison if a later refresh fails.
- Keep retired policy-read errors out of newer editors and announce pending saves
  outside busy forms; distinguish policy changes from catalogue reloads in bulk guidance.
- Refresh stale conflict comparisons and require renewed reapply consent; load the
  current revision without rewinding newer policy data or replacing reopened editors.
- Preserve confirmed policy revisions when older reads finish; refresh lists after
  cancelled estate imports without selecting beneath a newer editor.
- Preserve reopened Starter GPO and GPMC import drafts after older saves finish;
  freeze pending controls and support Enter in the backup import form.
- Keep automatic list/detail refreshes from changing a newer editor's policy, and
  retain summaries learned after a list request without pinning deleted entries.
- Fix Cancel and Close across dialogs with invalid required fields, and make Enter
  submit creation instead of silently dismissing it.
- Add an ADMX folder dialog with source counts, load diagnostics and refreshed policy
  browsers; add `--admx-dir` for repeatable startup without environment variables.
- Keep ADMX configuration on the selected editable GPO, show persistent save errors,
  and prevent duplicate pending configuration requests.
- Discard obsolete estate-file selections and late policy navigation/diff responses;
  refresh revision comparisons when switching policies.
- Prevent duplicate pending draft creation and keep retry values intact.
- Keep bulk policy selections scoped to the current GPO and configuration side,
  including selections hidden by filters.
- Use a compact two-column tool layout on narrow screens.
- Keep dialog headers and actions outside the scrolling form body, so keyboard
  focus stays visible on narrow screens; label configuration checkboxes and retain
  field help through validation errors.
- Keep exact multi-string registry values in the workspace using JSON array editors,
  including spaces, semicolons and empty members; reject malformed input before
  saving and explain Registry.pol's existing single-empty-string limitation.
- Bind History restore actions and pending edits to their original GPO/revision and
  editor session, so late responses preserve a newer selection or reopened form.
- Freeze pending editor controls while keeping Close/Cancel usable; reopened
  editors can start independent requests without older completions unlocking them.
- Keep workspace creation/import usable during background navigation, close only
  the completed editor, and prevent old list responses from hiding newer drafts.
- Show duplicate-name conflicts inline instead of opening revision recovery.
- Refresh all loaded ADMX sources after reloads, including background completion
  after reopening the folder dialog, while preserving the next chosen path.
- Keep archived policies read-only in the settings browser, including bulk actions.
- Report unreadable ADMX folders with actionable JSON errors while retaining the
  previous catalogue; reject empty or whitespace-only `--admx-dir` values.
- Bind policy configuration and bulk changes to the reviewed ADMX catalogue,
  so a folder reload in another tab cannot silently change the policy mapping.
  Keep unsaved configuration values on a catalogue conflict and require review.
- Keep persistent errors visible through background reads and older request
  completions; provide an explicit Dismiss action that returns keyboard focus.
- Keep each ADMX target bound to the revision displayed when its editor opened;
  late sidebar updates cannot silently advance it. Explicit conflict review
  updates that target's revision, and archived targets require another draft.
- Show a visible saving message in pending editors and explain that closing the
  dialog keeps the save running.
- Keep visible captions on preference members, registry values and targeting
  fields after they are filled, with keyboard-friendly layouts on narrow screens.
- Preserve significant whitespace in ADMX folder paths so the UI loads the exact
  chosen folder; reject whitespace-only paths before sending a load request.
- Scope the authenticated IIS shared-service profile and its acceptance gates
  in `docs/iis-shared-service-assessment.md` (WI-086); no hosted mode is enabled.

## [1.1.0] - 2026-10-20

> **Final release.** 1.1.0 promotes the `1.1.0-rc.1` candidate and adds the
> work done on it since: security fixes (below), a hardening of everything
> Studio writes and stores, three GPP preservation fixes (WI-080, WI-081,
> WI-082), and a new release process. This section lists what changed since
> `1.1.0-rc.1`. Everything else that is new in 1.1.0 since 1.0.0 is recorded,
> unchanged, in the [1.1.0-rc.1](#110-rc1---2026-10-09) section below, which
> also carries the upgrade notes from 1.0.0.
>
> The release's live evidence (the requalification of every lane at the
> release's freeze commit, and what 1.1.0 does and does not claim) is in the
> [1.1.0 evidence manifest](docs/release-evidence-1.1.0.md). The owner's
> hands-on NVDA acceptance was waived for 1.1.0 and is recorded there as not
> run.
>
> The changes since the candidate were developed and reviewed on PR #107, in
> review rounds `final-r1` to `final-r24`. In outline: rounds 1 to 5 built the
> release gate and its preparation and swept whole-value validators; rounds 6
> to 10 moved identity checking into one place, made every writer check what it
> writes, and made egress validation the boundary; rounds 11 to 13 redesigned
> how imported (retained) GPP content is kept and edited; rounds 14 to 19 made
> stored data read by its type and refused content never break a read; rounds
> 20 to 24 gave a GPO whose stored values cannot be read a way out and bound a
> replacing import to its source GPO. The entries below are grouped by theme
> and state the final behaviour; where a later round replaced an earlier one,
> only the later behaviour is described.

### Security

The release candidate `1.1.0rc1` (published as `v1.1.0-rc.1`) contained the
defects below, and the second and third classes also affect `1.0.0`. Affected
releases were checked against the `v1.0.0` and `v1.1.0-rc.1` tags. No
`1.0.x` patch is planned for them: `1.0.x` is no longer a supported line (see
`SECURITY.md`), so run `1.1.0` or later.

- **A value could change the structure of an artifact Studio writes.**
  Studio's line- and record-oriented writers wrote values as given, so a value
  holding a line break, a NUL or another control, format or separator
  character became structure. Three classes:
  - **`GptTmpl.inf` line injection (`1.1.0rc1` only).** In the `GptTmpl.inf`
    that `POST /api/security-template/policy-families` and
    `POST /api/security-template/object-security` render, a user-right
    principal or right name became an additional `[Privilege Rights]` entry,
    granting a right the request did not name, and a service name, registry or
    file path, or security descriptor became an additional row. These
    endpoints and their writer are new in 1.1.0; `1.0.0` has neither.
  - **PowerShell code execution from typographic quotes (`1.0.0` and
    `1.1.0rc1` for `plan.ps1` and `apply.ps1`; `1.1.0rc1` only for the AD
    discovery scripts).** The PowerShell plan (`plan.ps1`, and `apply.ps1` in
    `export.zip`) escaped only the ASCII `'` inside its single-quoted
    literals, while PowerShell also reads U+2018 to U+201B as single quotes,
    so a value holding one could end the literal and the rest of the value
    would run as code when the plan was run. In `1.0.0` the plan's own
    validator accepted such a value. The AD discovery scripts
    (`GET /api/discovery/*/script`, which an administrator is told to run on a
    domain-joined machine) had the same quoting: a typographic quote in the
    domain, search base, search term or object class closed its `param()`
    default and ran the rest as code. The discovery scripts are new in 1.1.0.
  - **`Registry.pol` record splitting (`1.0.0` only).** `1.0.0`'s validation
    refused a control character in a registry key but not in a registry value
    name, and the writer wrote the name as given. Windows reads a name up to
    its NUL, so such a name could end early and the rest of it be read as
    further policy records. `1.1.0rc1`'s serializer still lacked its own NUL
    check, but its validation refused NUL in every text field before a value
    could be stored or exported, so the released candidate was not affected.

  Fixed: every writer now checks each name and value as it writes it and
  refuses what its format cannot carry intact (see the behaviour changes
  below); every PowerShell value goes through one quoting function that
  doubles every PowerShell single-quote character, the plan validator reads
  literals as PowerShell does, and a test runs each generated script under
  PowerShell with these values and checks that nothing in them runs; the
  `Registry.pol` writer refuses a NUL in a name and the store refuses a
  control, format or separator character in a registry key or value name.
  Operators who generated a plan or an `export.zip` with `1.0.0` or
  `1.1.0rc1`, or a `Registry.pol` with `1.0.0`, from GPOs whose names,
  descriptions or values came from an untrusted source should regenerate them
  with 1.1.0 and review them before applying; Studio never runs a plan itself.
  This section is the disclosure for these issues: no separate advisory is
  published, and no `1.0.x` patch is planned.

### Behaviour changes

Read these before moving from `1.1.0rc1`. Unless a note says otherwise,
workspaces load unchanged and nothing is migrated (the schema is still 4).

- **Every identity Studio writes is checked as written.**
  `src/gpo_studio/identity_values.py` holds one validator per kind: SID,
  distinguished name (RFC 4514: escapes, a dangling or incomplete `\`, an
  unescaped leading or trailing space; an unescaped `=` in a value is refused
  too, stricter than the RFC; a multi-valued RDN such as `CN=a+OU=b,DC=x` is a
  DN), GUID, trigger time, date-time (a real day, no 31 February; a UTC offset
  at most 14:00 in total, so `+14:59` is refused), date, and account or group
  name. Each judges the whole value and refuses control, format, line and
  paragraph separator characters and every space but the ASCII one (a
  non-breaking space among them); a trailing newline was previously accepted
  by many patterns that ended in `$`. The store, the publication preview, GPMC
  backup import, estate import, AD discovery, lifecycle, migration and object
  security call these instead of their own patterns, and the whole-value
  matching also covers the workflow grammar, the restore plan, the estate GUID
  check, the firewall thumbprint check, lane scope names and the publication
  remote parser. The release gate's path check refuses every Unicode control
  and format character (a bidirectional override among them).
  **What operators see:** a GPP group or member SID, security-filter SID or
  principal, link DN, user, service account, task `runAs`, task trigger time
  and times (`StartBoundary`, `EndBoundary`), `UserId` or `GroupId`, and an
  item-level targeting group, user, OU, computer, domain or date value with
  such a character is an error, as is an item `uid`, `FilterRunOnce@id` or
  `clsid` that is not a braced GUID and a power scheme's `nameGuid` or
  `schemeGuid` that is not a GUID. A malformed group or security-filter SID is
  an error, not a warning, and a lowercase `s-1-...` in a principal field is a
  malformed SID (a migration table's lowercase SID is still read as a SID). A
  preference mutation or GPMC backup import that carries one is refused (422),
  as is an estate import whose GUID, link or security filter does. The store
  refuses such a link or filter outright (other draft errors still wait for
  the ready transition). A scheduled task's trigger time with whitespace or a
  control character is refused (`unwritable_preferences`) instead of becoming
  a `StartBoundary` Windows silently rejects, and an empty TaskV2
  `StartBoundary`, `EndBoundary`, `UserId` or `GroupId` is refused rather than
  skipped. Identity error messages name the value, so the store's "only new
  errors" rule no longer lets one stored bad value be swapped for a
  different one.
- **Existing GPOs with a newly invalid value.** Nothing is migrated and every
  workspace loads. A GPO stored before this release that holds a value these
  rules now refuse (a link, security filter or preference value with a control
  character, a malformed identity, a GPO name or GPP value with a control
  character, a registry name holding a control character, a malformed
  item uid) keeps it, but its `export.zip`, `gpmc-backup` and `plan.ps1`
  answer 422 naming the field or value, and it cannot move to ready, until it
  is corrected. Correct it through the API as any edit: `PUT` the link,
  security filter or preference item with the corrected value, or delete it
  (`DELETE`). An edit is refused only for an error it adds, so fixing or
  deleting one bad item of several succeeds while the rest remain, and an
  unrelated edit is not blocked; a link or filter edit is checked for its own
  values only. A GPMC backup holding such a value has to be corrected at its
  source (or in GPMC) before it is imported again. The security-template render
  endpoints are stateless: only the request changes.
- **Every writer refuses what its format cannot carry.**
  `src/gpo_studio/emit_integrity.py` holds one table per output format and each
  writer checks what it emits against it: the GPP XML writer (typed items,
  retained native elements and attributes, imported files), the GPMC backup
  documents, `GptTmpl.inf` (policy families and object security), `scripts.ini`
  and `psscripts.ini`, the PowerShell plan, `Registry.pol` and the plain-text
  report. JSON outputs are not listed: JSON escapes every control character.
  Every value refuses Unicode controls, format characters, line and paragraph
  separators and lone surrogates, except where the format encodes them
  explicitly: TAB and LF in an XML value classified as multi-line text (a
  description, a message body); any character in the plan's `-Comment` and
  registry data (each control is written as a `[char]` code, and a character
  above U+FFFF as `[char]::ConvertFromUtf32(...)`, because `[char]` is one
  UTF-16 unit and an astral format character was previously an invalid cast
  that stopped the plan with earlier statements applied); any character but
  NUL in `Registry.pol` names and string data; and the report writes each as a
  visible `\uXXXX` (`\UXXXXXXXX` above U+FFFF). Firewall rule fields and script
  command lines refuse every control, format and separator character. A name a
  table does not classify (a vendor attribute retained from an import) is held
  to a one-line rule, and a service name, task name and every other free-text
  value must be one line.
  **Error codes.** A GPP value a writer refuses is `invalid_preference_value`
  or `control_character_in_preference_value`; the message names the file, the
  element and attribute, and the value. New validation errors:
  `control_character_in_gpo_name`; `unwritable_registry_name` (a registry key
  or value name holding NUL, which ends a `Registry.pol` name, or another
  control, format or separator character, which the plan cannot write as one
  line; a `;` is valid, see Fixed); `unwritable_registry_data` (string data
  holding NUL); `value_outside_closed_set` (below).
  **Security-template renders.** The two render endpoints answer 422
  `unwritable_template_value` for a right name that is not a `Se...` constant,
  a principal that is neither a starred SID (`*S-1-...`, which must be a SID)
  nor a name without a comma or double quote, any value that is not one line, a
  double quote inside a quoted row field, or anything the INF reader would
  split, strip, continue or read as a comment; their request models refuse the
  same first, and the object-security request models require the descriptor
  (and a service's startup mode). Advisory issues (risk, an unparseable
  descriptor) still do not block a render. `powershell_plan` validates its own
  input, so no caller gets a plan for a GPO with a validation error, and the
  plain-text report escapes such characters instead of writing them (a
  description's own lines stay lines).
- **AD discovery values are checked.** A discovery value must be of its kind (a
  domain a name, a search base a DN, an object class a token) or the script
  route answers 422, and the scripts quote through `emit_integrity.ps_literal`,
  as the plan does.
- **Egress is the boundary.** Every API route is declared: the 15 that return
  or embed an artifact (`export.zip`, `plan.ps1`, `report.txt`, the GPMC
  backups and their scripts preview, the six discovery scripts, the two
  security-template renders, the firewall render) with the format their result
  is validated against before the response leaves (`egress.py`), and the other
  99 with the reason they return none. Mounted applications are walked
  (the one mount, `/assets`, is accepted only as Studio's own static-files
  application over its package directory). PowerShell is tokenized as
  PowerShell reads it and its structure compared with what the writer produces
  from neutral input, against a closed grammar with typed holes
  (`egress.PLAN_GRAMMAR`), so the writer's own `$desiredApply = @()` and
  `-Value @()` are accepted; `GptTmpl.inf` must parse to exactly the sections
  the response declares, and `inf_base64` must be its encoding; INI, XML and
  `Registry.pol` are re-read and held to their tables (`Registry.pol` must
  re-serialize to the same bytes); a ZIP's entries, not only names, are read
  (a duplicate member, a missing required member, `manifest.json`, `apply.ps1`
  and both `Registry.pol` files, or `manifest.xml`, `Backup.xml` and
  `bkupInfo.xml`, and an empty archive are refused), and each member is
  validated by its format. A refusal is a 422 `unwritable_artifact_value` (from
  the GPP writer, `gpp_content_refused`). **Only a writer's named refusal is a
  422; nothing else is:** the catch is at the writer or builder call
  (`egress.writer_boundary`) and only for the writers' named refusals
  (`GppError`, `IltError`, `RegistryPolError`, `DeterministicZipError`,
  `FdeployError`, `SecurityTemplateError`, `SddlError`,
  `FirewallValidationError`), each answered with its own reason, sanitized (one
  printable line, bounded) in the 422 and the log, which records the type and
  that reason, not a traceback. Store access, validation and response building
  keep their 500. A capability is its download: the plan, Studio bundle and
  GPMC backup routes return `egress.checked_plan`, `checked_studio_bundle` and
  `checked_gpmc_backup`, and the capabilities ask exactly those functions, so an
  advertised artifact is one that writes, and a writer's refusal never breaks
  a read (a GPO payload reports it as a disabled capability with its reason).
  The route-level check also holds archive bytes no checked builder produced to
  the closed vocabulary. The recorded refusals behind a GPO's capabilities are
  cached by the GPO's content (on a GPO with 3,000 settings and 300 groups a
  read took about 2.5 s every time; later reads of the same revision take about
  0.5 s); the cache is locked and tolerates a GPO it cannot hash.
- **Retained (imported) GPP content is kept, never supplied.** A GPP item
  imported from GPMC carries what Studio does not type: attributes, children,
  raw targeting predicates. Clients can no longer send it. The group, registry,
  member and value add and edit bodies carry typed fields and ids only
  (`extra="forbid"`: no `unknown_*`, raw predicate, item `id` or registry
  `uid`), so a request that carries raw or retained content gets the generic,
  sanitized request-validation 422 (`extra_forbidden`) and clients keep an
  imported predicate by sending its id; the store also enforces, as an internal
  invariant (`raw_content_not_accepted`), that retained content only ever comes
  from an import. The server merges an edit onto the stored item
  under the store lock (`gpp_edit`). Every item and every targeting predicate
  (typed, of a type Studio does not author, or raw) has a stable
  server-assigned id (`ilt_filter.ids` in responses). In a targeting list an
  entry `{id}` keeps that predicate as stored, `{id, type, ...}` edits a
  predicate of an authorable type (a type change drops that predicate's own
  retained attributes), an entry without an id adds a new typed predicate, a
  stored id left out is deleted with exactly its own retained content, and the
  list order is the new order. Raw and non-authorable predicates can be kept,
  moved or deleted, never edited, so an imported `FilterDate` no longer blocks
  editing the rest of its item; members work the same way. Responses show
  retained content read-only, and the mutation responses carry
  `unknown_props_children` for groups and registry items. A 422 for a field a
  request may not carry, or for a value of the wrong shape inside a GPP body
  (raw XML where a member or registry value belongs), does not echo the value.
  The writer refuses a retained attribute (item, targeting predicate,
  file-root, member or registry value) that would overwrite one it has already
  set. Before this, a request could pass `unknown_attrs: [["disabled","0"]]`
  beside `common.disabled: true` and ship `disabled="0"`, or add a raw
  `FilterRunOnce` element.
  **Provenance is recorded at acquisition.** The backup import mints a record
  (a digest bound to family, item and predicate, member or value id and scope)
  for each retained fragment (`gpp_provenance`); a fork carries the records
  with its items, a clone re-keys them onto the copy's fresh ids and gives the
  copy a fresh run-once id, an item or whole-revision restore copies the records
  the revision holds and creates none, and every other mutation keeps the
  records of the fragments it keeps and can add neither a fragment nor a
  record. No record crosses a scope. Egress allows an element outside the closed
  vocabulary only where an import record holds it, per file (an element
  retained in one file does not admit it in another file of the same side);
  retained content without a record is kept and shown (`retained_unverified`)
  but held to the vocabulary (which gains the 28 targeting elements of the
  Plan 021 inventory and the `LocalPrinter`, `PortPrinter` and `ImmediateTask`
  item tags, each from a source in the repository, so valid Microsoft GPP such
  as `FilterBattery` exports), and a refusal names only the items that hold
  the refused element, with the remedy: re-import the GPO from its source
  backup, or delete them. For a Printers backup the refusal remains
  `unsupported_native_gpp_extension` (unmeasured extension registration),
  unchanged.
  **Existing workspaces** are upgraded conservatively by a one-time migration
  (`workspace_meta.gpp_provenance`): a fragment is recorded only if the
  revision that first holds it is revision 1 and that revision was a backup
  import (`archived`, provenance line `Imported from GPMC backup {GUID}`) or
  estate import, or a fork whose parent held the fragment recorded at fork
  time. Revision rows hold actor, free-text reason, timestamp and snapshot and
  no operation name, so those revision-1 facts are what identify an import.
  Everything else is unverified. The migration reads revision 1 first and
  nothing else for a GPO that was not imported or forked, and a snapshot the
  model cannot load, or loads only with problems, records nothing (the reason
  is logged and listed in `workspace_meta.gpp_provenance_unrecorded`), so a
  1.0.0 workspace holding a namespaced retained child in a historical revision
  still opens. A snapshot that gains no record is left byte-identical;
  predicate ids for one written before them are derived from its item on load.
  A legacy raw `FilterRunOnce` predicate (stored by the pre-final-r11 API) is
  read as the item's apply-once option and run-once id, so a clone of it gets
  its own identity and exports.
  **Downgrade note.** A 1.1.0 release candidate (the same schema, 4) can open a
  workspace this build has opened; it ignores the import records and predicate
  ids and drops them when it writes. That can only leave imported content
  unverified (exports then refuse its out-of-vocabulary elements until the GPO
  is re-imported), never verify anything. To go back, restore the pre-upgrade
  backup ([workspace recovery](docs/workspace-recovery.md#upgrading-and-rolling-back-across-a-schema-change)).
- **Stored data is read by its type, and a value that cannot be read blocks
  the GPO instead of the server.** The typed load (`stored_types`) checks each
  stored value against its field's annotation (every annotated field of a GPO,
  its items, preferences and import records) and replaces a value of the wrong
  type, a missing required field or an unreadable import record by its
  default, reporting it as `stored_value_wrong_type` (an error), so the GPO is
  blocked. Previously every loader read booleans by truthiness, so an estate
  import of `"false"` stored and exported `true` for a GPO's sides, a link's
  enabled and enforced flags and a security filter's flags, and an array in a
  closed-set field crashed the membership check (500 on read, after a committed
  mutation and on import). An estate import or an inline diff reference
  carrying such a value is refused (422), as is a malformed estate member (a
  link order of "x", an unknown permission). Because the read replaced the
  value, Studio will not store a change over it (an edit, a fork, a derived
  GPO, a copy of its settings or a restore of one of its items would store the
  default and unblock the GPO) and each is refused (422) until the GPO is
  re-imported or a revision without the value is restored. Out-of-range stored
  values read and never refuse the read: a document position of -1 is a
  replaced value, and a named loader refusal no tolerance covers reads its
  collection as empty and replaced. Every field the model types as a closed set
  (42 of them, `closed_sets`) is held to its set: validation reports a value
  outside it as `value_outside_closed_set`, so imports and mutations refuse it
  and a stored one blocks the GPO, and reading such a GPO tolerates the value
  (a string outside its set is kept as stored; the closed-set check compares
  types too). `GppRegistryValue.registry_type` is closed by validation (the six
  types; `""` only for a key-only item) and `GppRegistry.hive` by the load (one
  of the five `HKEY_` names; another is kept and blocks the GPO);
  `IltPredicate.bool_op` is deliberately open, preserved verbatim from an
  import. Older stored shapes are rewritten into the current one by the typed
  load (`stored_types.LEGACY_REWRITES`): a bare array of targeting predicates,
  the `predicates` and `unknown_predicates` shape, a printer's `use_local`, a
  service's pre-WI-081 delay, recovery and `append_arguments` keys, and a
  multi-value registry item (`values`). They read exactly as the loader they
  replaced read them (an element `uid` and a value `default` are promoted as
  before, a bare targeting array's predicates are read by their three keys
  only, and a stored predicate's `negate` and `value` are required); a legacy
  value of the wrong type is a replaced value like any other (`use_local:
  "false"` exported `skipLocal="1"`), and a delay is scaled only when it is a
  number, of any sign. One difference is declared, not matched: an `os`
  predicate in a bare array keeps its version criterion (the old reading
  silently broadened it, WI-021).
- **Refused stored content never breaks a read and is never written.** Stored
  preference data is read tolerantly everywhere. Retained content a current
  rule refuses (a namespace, a reserved-name collision, an element record that
  no longer validates) is kept as stored and reported as a validation error
  (`stored_preferences_refused`), so the GPO and its history stay readable and
  every content artifact refuses it with the remedy: delete the item, or
  re-import from the source backup. A colliding stored document order is
  dropped with a warning, and malformed predicate ids or provenance records are
  ignored (ids re-derived, content unverified), never fatal. `plan.ps1`,
  `export.zip` (with `apply.ps1`), both GPMC backups, the scripts preview and
  the publication preview refuse such a GPO (422, with the validation errors,
  before any artifact is built or digested); `report.txt` still renders, as the
  diagnostic exception (it summarizes and escapes, never embeds retained
  bytes, so the operator can see what is refused and the remedy, and its
  capability stays enabled). A GPO's detail read, list, revision list and every
  GPO-returning mutation call no preference writer, archive builder or
  publication digester on such a GPO (every capability probe stops at a
  validation error, and review digests, reports and diffs read marked records
  raw), and the builders refuse first, not only their callers. Every mutation
  and creation validates, and builds the payload it answers with, before it
  commits (`store.register_answer_check`), so a failure cannot follow a durable
  revision: the revision moves exactly when a mutation answers 2xx.
  An empty change (`copy-settings` from a source with no settings, an empty bulk
  policy change) makes the same expected-revision and defaulted-value checks as
  any other, and no other mutation returns before them. The answer built before
  a commit is handed back through the request (`store.answer_scope`) rather
  than a process-global dictionary, and answering a GPO the request committed
  with anything but the committed object raises. `report.txt` no longer fails
  (500) on a stored 1.0.0-era raw targeting predicate that does not parse: a
  tolerant load records it as refused content, and the report's inventory notes
  any writer refusal instead of raising.
- **A GPO whose stored values cannot be read has exactly three ways out**, and
  the message names them (`stored_types.RECOVERIES`): restore a readable
  earlier revision; replace the GPO's content from a fresh import of its source
  backup (`POST /api/backups/import` with `replace_guid` and
  `expected_revision`: a new revision whose content is wholly the import, with
  freshly minted provenance records); or delete it
  (`DELETE /api/gpos/{guid}?confirm={guid}`, which deletes only such a GPO and
  only with its guid repeated as `confirm`). The deletion is not lossy: in its
  transaction every stored row of the GPO (head, every revision snapshot with
  the provenance records inside it, the retained documents they reference) is
  copied byte for byte into additive `deleted_*` archive tables, created on
  first use (the schema version is unchanged), and
  [`docs/workspace-recovery.md`](docs/workspace-recovery.md) gives the restore
  script, which a test runs as written. Each deletion has its own
  `deletion_id`, carried with `deleted_at` by every archive row, so delete,
  restore and delete again in one second do not collide; the runbook's restore
  script inserts a row only where an identical one is not already there (a
  second run changes nothing, and a different row under a restored key fails the
  whole restore rather than being overwritten). Archive tables of an earlier
  shape (the pre-release `5c6ea8a` wrote one without `deletion_id`) are
  renamed to `<table>_preshape_<n>`, never dropped or changed, and the runbook
  documents that shape and a restore script for it; a name taken by any schema
  object (table, view, index, trigger), compared without regard to case, is
  skipped.
- **A replacing import must be of the very same source GPO.** A GPO GUID is not
  unique across estates, so a replacement (`replace_guid`) now needs the source
  GPO's whole identity: a native import records the GPO GUID, the domain DNS
  name and the manifest's `GPODomainGuid` in its import record
  (`BackupInventory`, kept only in the stored revision, never in the GPO the API
  serves, an artifact, the export manifest or either digest), and a replacement
  must match all three as revision 1, which is immutable, recorded them
  (`replace_source_mismatch`). A GPO whose first import recorded no domain
  identity (before 1.1.0, an estate entry, a Studio-format bundle) is refused
  outright (`replace_source_unrecorded`), not half-checked, since a domain-only
  match would still accept a rebuilt same-named domain; such a GPO keeps its
  other recoveries. A replacement is refused for a readable GPO
  (`replace_not_a_recovery`), as the delete is, and for a fork: only a GPO whose
  first revision is the import qualifies (`replace_not_the_import`). Every
  identity document in the backup (`manifest.xml`, a top-level `bkupInfo.xml`
  and the `bkupInfo.xml` nested in the backup instance directory) must name the
  same GPO GUID and, where stated, the same domain DNS name and domain GUID,
  before an import model is built, for an import and a replacement alike. Every
  entry whose name case-folds to the backup ID counts as the backup instance
  directory; more than one is ambiguous, and the backup is refused, as is one
  that is not a directory or is a symbolic link, and a directory that cannot be
  listed fails the import with a backup error. A backup without content keeps
  its domain GUID. The recorded identity is dropped at the serializer from every
  response body (`model.without_stored_only`, scoped to import records), and a
  replacement's mismatch message does not name it. The identity fields are left
  out of a GPO's dict and digests when empty, so every earlier import keeps its
  bytes and digests. A test holds the public dict, both digests, the Studio
  bundle and its `manifest.json` of all 19 native captures to the SHA-256 that
  `5c6ea8a`'s source produces (`tests/fixtures/native_artifacts_5c6ea8a.json`).
- **The restore plan and the firewall surface cite the lane and the release
  evidence document instead of a run id.** `POST /api/lifecycle/restore-plan`'s
  `evidence` object loses `run_id`, `commit` and `verdict` and gains
  `release_evidence`, the URL of the release evidence document at the running
  version's tag
  (`https://github.com/hraedon/gpo-studio/blob/vX.Y.Z/docs/release-evidence-X.Y.Z.md`),
  which names the lane's certifying run and commit; `lane`, `cells_measured`
  and `cells_agreeing` are unchanged, as is every survival cell. The firewall
  endpoints' `policy_store_readback_not_application` limitation names the
  `firewall-policy-gpmc` lane and that URL instead of a run id; its code is
  unchanged. Shipped code named runs that the next batch retires, and the
  final-tag gate forbids editing `src/` after the freeze commit to re-point
  them. No frontend reads these fields.
- **Python 3.13 and 3.14 only (`requires-python = ">=3.13,<3.15"`).** The
  metadata said `>=3.13`, which admits Python 3.15, but some locked dependencies
  (pydantic-core, and librt and pyyaml in the dev extra) have no 3.15 wheel and
  installs refuse to build one. `uv.lock` was regenerated for the cap only:
  every package version is unchanged.
- **Performance.** Building each answer before its commit makes `create_gpo`
  about 2.5 times slower at the store level for an empty GPO; a request did the
  same work after the commit before, and the reviewers measured no material
  change for a 200-GPO estate import.
- **`Registry.pol` is read by the format.** Names are read up to their NUL and
  then the separator is required, one UTF-16 code unit at a time; string data
  must be NUL-terminated (two NULs for `REG_MULTI_SZ`) when read, which every
  capture in the repository is. See Fixed for the `;` case this corrects.

### Added

- **Server-side Clone and Restore for GPP items.**
  `POST .../preferences/{groups,registry}/{id}/clone` copies the stored item
  under the store lock with fresh ids (a group may take a new name; a registry
  clone takes none; the imported `uid`, element record and run-once id stay
  with the original), and `POST .../{id}/restore` with a `revision` puts the
  item back as that revision stored it. Neither takes item content. The
  workbench's old Clone dialog mode is gone.
- **An optional `common` object on group and registry add and edit**, and
  `common` in group and registry responses (see WI-082 under Fixed).
- **Workbench editing of retained content by id.** An imported read-only
  predicate can be moved (up and down) or removed, the edit dialog shows the
  item's retained content read-only, each predicate's AND or OR join is shown in
  its row and in the expression preview, and a move that regroups an expression
  with an OR is warned about. A predicate is edited only when its join is AND (a
  save kept rewriting another stored `bool` to AND); a raw predicate's join is
  read from the start tag in either quote style (`bool='OR'` previously showed
  as AND and moved without a warning), a join it cannot read shows as `?` and is
  treated as one that can regroup the expression; an operating-system predicate
  is authored as class, version, type, edition and service pack (the workbench
  and the API author the same seven types, held equal by a test) and needs at
  least one criterion set (a stored, partly empty one still saves, without
  forcing a change; a fully empty one is kept by its id but not re-sent). The
  publication panel shows a 422's issues. A Playwright journey covers deleting
  a typed predicate beside a raw one, editing an item holding `FilterDate`,
  moving a raw OR, clone and restore.
- **Releases publish `requirements-lock.txt`, and the Windows quickstart
  installs from it.** The file is the runtime closure of `uv.lock`, pinned by
  SHA-256, listed in `SHA256SUMS`, attested with the distributions and attached
  to the release. The quickstart installs it with `pip install --require-hashes`,
  then the wheel with `--no-deps --no-index`, and adds an optional
  `gh attestation verify` step. A release without the file keeps the old
  install.

### Fixed

- **An item-level targeting filter Studio cannot write can no longer break the
  GPO.** A raw filter predicate that did not parse (an undeclared prefix,
  malformed XML) was stored as given and failed only when serialized: the
  request answered 500 after committing, and every later read or export of that
  GPO answered 500. The group and registry routes now accept typed predicates
  and stable ids only, so a raw predicate in a request is refused (the generic
  request-validation 422, see Behaviour changes). A padded IP range
  (" 10.0.0.0/8 ") had the same effect, because validation stripped it and the
  writer did not; the writer now strips it too. And as a backstop, the store
  writes every changed preference collection before committing it and refuses
  with `unwritable_preferences` (422) whatever the writer cannot write, so no
  request can leave a GPO unreadable. Pre-existing; found by the WI-080 reviews.
  A raw predicate without `bool` joins with AND, as it is read.
- **A stored GPO's preferences are exported as Windows wrote them, fixed and
  requalified (WI-080).** Studio kept an imported GPO's preference XML only in
  memory, so every export of a stored GPO (the GPMC backup, `export.zip`, the
  publication planner) rebuilt it from the typed model, edited or not. That
  dropped every `Properties` attribute the model does not type, among them a
  printer's `default` ("set as the default printer"), an environment variable's
  `partial`, a scheduled task's `logonType`, a shortcut's `comment`,
  `shortcutKey`, `targetType` and `pidl`, a drive's `thisDrive`, `allDrives` and
  `userName`, and a folder's `delete*` options. It also minted a new
  `FilterRunOnce` id, so a migration through Studio re-applied every apply-once
  item on every client; turned `window=""` into `Normal`; dropped a filter
  group's name beside its SID; added default-valued attributes the source never
  had; and reordered attributes. Measured on the committed native captures: 18
  of 19 changed.

  Import now keeps each preference item's element as Windows wrote it
  (`native_xml`, all 20 families) and the imported run-once id
  (`common.run_once_id`), and both are stored. An unchanged item is written
  exactly as imported. In an edited one, what the edit changed is written from
  the model, in its imported position, and everything else stays as imported;
  each merge is parsed back, and the model's own rendering is written instead
  wherever the merge would not mean what the model means. Every element written
  for an edited item is also held to the model's intended values: a typed value
  the edit changed that would read back as the imported one is refused, never
  exported, and that is checked per scalar: an edit must read back as itself. A
  task's edited command is now written into its `<Task>` payload (a TaskV2's
  `arguments` edit was lost even before this fix), whether or not the task was
  imported: without an import record the payload is the record, a set field that
  differs from it is an edit, and an empty one is unset only when no command
  field is set (a task built from a payload alone); next to a set field it could
  be a deliberate clear, and the export is refused rather than keeping the old
  value. A payload edit and a command edit that disagree are refused, as is a
  typed field with no wire form, such as a printer's generic `action` or a
  folder's `suppress`. A registry item's action as the workbench shows and edits
  it, which was never written, is now read from the value's action on import and
  applied to it by an API edit. The run-once id survives turning apply-once off
  and on again. A cpassword is refused as an element as well as an attribute, at
  any depth, in any case, namespace or encoding (UTF-16 in either byte order,
  with or without a byte order mark), on import, at the API, on load and on every
  export (before, the element form passed every check and reached `export.zip`,
  and a UTF-16 file evaded the check entirely). The API neither serves nor
  accepts a retained element, in any JSON body (revision snapshots and diffs
  included) or inline diff reference; an item in a namespace is not retained on import. No new
  retained store (unknown attributes, unknown children, raw filter predicates)
  may hold a namespaced name, and every retained attribute name must be a plain
  NCName (no literal `xmlns:x` or `x:extra` key): a new import and the API
  refuse one. Content stored before this fix stays readable instead: it is
  loaded as stored, reported as the validation error
  `stored_preferences_refused`, and blocked from every content artifact until it
  is repaired (see Behaviour changes). The credential check reads the local part after any prefix, so
  `x:cpassword` is refused at intake. The review diff compares what a retained
  element makes the export write, not the element itself, so a re-import of
  Studio's own export is not a change. Workspaces need no migration: a GPO
  stored before this fix keeps its digests and backup id, and its export is byte
  for byte what `1.1.0rc1` wrote except where WI-081 below corrected an attribute
  name or order (Files, Folders, Printers, Shortcuts, immediate tasks), measured
  against the records that commit stored
  (`tests/fixtures/gpp-store-baseline-bd84b3a`). Review digests change only for a
  GPO imported from GPMC after the fix, because its export does (re-importing
  Studio's own backup leaves them unchanged). The report-parity inventory now
  reads the typed model alone (`gpp.model_only`), as it always meant to. Covered
  by `tests/test_gpp_native_preservation.py`, which compares every native capture
  with Studio's output through import, storage and the public `export.zip` and
  `gpmc-backup` routes, attribute by attribute and in order, and fails on the
  code before the fix. The fix changes `gpp.py`, `gpp_adapters.py`,
  `canonical.py` and `report_parity.py`.
- **A GPMC default printer reads as the default, and the GPP writer types only
  attribute names Windows writes, fixed and requalified (WI-081).** Printers'
  typed `set_default` and `use_local` were read and written as `setDefault` and
  `useLocal`, which no capture contains; GPMC writes `default` and `skipLocal`.
  So the model, the API, reports and diffs showed a GPMC default printer as not
  the default, and an authored or edited printer wrote attributes Windows has not
  been seen to read. They now read and write GPMC's names, in GPMC's order, and
  the field is `skip_local`; Studio's older output and stored items are still
  read under the old names, and digests do not move. An audit of every writer
  against the native captures found three more names none contains, now no longer
  written: Folders `suppress`, Shortcuts `Properties@name` (a shortcut is named
  on its item, as GPMC names it; the typed name is written there whenever set, so
  a rename persists) and an immediate task's `program`, `arguments` and `startIn`
  where its `<Task>` payload already holds them.
  `tests/test_gpp_typed_attribute_names.py` pins the audit: every `Properties`
  attribute the writer types appears in a capture. Families with no capture are
  not claimed.
- **A workbench edit keeps a preference item's common options, fixed and
  requalified (WI-082).** The group and registry edit payloads carried no common
  options and the store replaced the item, so every edit reset apply-once
  (dropping its run-once filter), disabled, remove-when-not-applied,
  run-in-user-context and stop-on-error. An add or edit now takes an optional
  `common` object; options it leaves out are carried over from the item it
  replaces (defaults for a new item), as is the run-once id, which the API cannot
  set. Group and registry responses now show `common`. Covered through the real
  API for every option on both families (`tests/test_gpp_common_option_edits.py`),
  and by saving every group and registry item of every native capture back
  through the API, after which the export must still equal the capture.
- **A `;` in a registry key or value name is valid.** MS-GPREG 2.2.1 terminates
  each name with NUL and puts the separator after it, so a `;` inside a name is
  data. The `Registry.pol` reader used to search for the first `;` byte pair,
  which split such a name and could split a character whose bytes contain
  `3B 00`; it now reads each name up to its NUL. The store still refuses a
  control, format or separator character in a name, since the plan writes names
  as single-line literals.
- **Registry data with CRLF is accepted end to end.** The plan writes any
  character of registry data or a description, each control as a `[char]` term,
  so data the store accepts (a legal notice with line breaks) is no longer
  refused by `plan.ps1` and `export.zip` after being advertised.
- **Lone surrogates.** `Registry.pol` refuses one with a clean error instead of
  a `UnicodeEncodeError`, and the report of a legacy GPO holding one renders,
  saying why it has no digest, instead of failing.
- **`GptTmpl.inf` writing and reading.** The check runs before the lossless path
  that returns a parsed template's original text; that path returns the original
  only when every physical line passes the one-line rule and none lies outside a
  section (a continuation line is never returned unchanged), and otherwise the
  template is rebuilt and checked; a preserved line may not read back as a
  section header, an entry, a malformed row or a continuation, and what is
  written must read back as the same sections and entries. A fixed section's key
  is checked by shape against a closed Windows vocabulary or pattern
  (`INF_KEYS`), derived from the writers by the completeness test, so a valid
  Windows key (`EnableAdminAccount`, `NewGuestName`, `LSAAnonymousNameLookup`)
  is written, never refused. A row is `"key",code,"descriptor"` with a
  descriptor (secedit requires the three fields), a spaced row is a row, and a
  `[Registry Keys]` or `[File Security]` row whose quoted key holds `=` is read
  and written as a row. The family writers check each principal and group member
  before joining the list; a service row needs a startup mode or a descriptor,
  and a security option a value. A request-model refusal carries the documented
  code `unwritable_template_value`.
- **A GPO payload is built for a hostile stored GPO without 500s.** Fixes
  found by type and closed-set fuzzing: no route answers 500 on a closed-set
  string outside its set (an estate import stored `registry_type="REG_FOO"` and
  the GPO's detail, plan and both bundles then answered 500); `GPO.to_dict` is
  plain `asdict` for a GPO without load problems, so the Studio bundle's manifest
  is the bytes it was (a GPO with them writes `load_problems: []`, and the
  export manifest does not carry a tolerant load's derived problems);
  `load_problems` is neither stored nor served; the digests catch only
  `UnicodeError` (a writer's named refusal is read as the raw record inside
  `retained_rendering`, a defect is a 500).
- **Reading an upgraded or imported workspace.** The 1.0.0 upgrade invariant pins
  `ilt_filter.ids` as bookkeeping, and a 1.0.0 snapshot with targeting keeps its
  derived ids stable and stays byte-identical.
- **`anyio` and `cryptography` are updated in `uv.lock`** (anyio 4.14.1 to
  4.15.1, cryptography 49.0.0 to 50.0.2): `pip-audit --strict` against the
  locked runtime set reported known vulnerabilities in both pinned versions (run
  2026-10-09). The previous CI audit installed the wheel from its version ranges,
  which pip resolves to the newest matching release, so it audited different
  versions than the lock pins.

### Release process

- **1.1.0 final is tagged on a records-only descendant of its freeze commit,
  and the release gate checks it** (owner decision, 2026-10-17; Plan 036, the
  D-9 addendum). Every live lane is requalified at one freeze commit F (the full
  batch), banked in a commit whose parent is F that changes only evidence and
  records, approved in a records-only commit after it, and `v1.1.0` goes on that
  approval commit. This closes WI-083 for 1.1.0 by construction instead of
  through Plan 036's WP-0, which moves to post-1.1.0 work under WI-084. The
  release evidence report gains `freeze` (the commit F and its batch manifest;
  report schema 3), which an approved report must carry. For a final tag only,
  `scripts/check_release_manifest.py` refuses unless the history from F is
  linear and is the banking commit, any records-only commits and the approval
  commit (the tagged one, changing only the approval); the tagged tree differs
  from F only in the batch's banked files and a closed list of release records
  (so `src/`, `scripts/`, `pyproject.toml`, `uv.lock`, `.github/`,
  `.gitattributes`, `README.md`, `CHANGELOG.md` and every test are byte-identical
  to F); the verdict registry only adds the batch's verdicts with their lanes'
  finalizers at F, and refuses a registry whose retired and pending lists overlap
  or name an unregistered verdict; and every live verdict and the WP-0 manifest
  is the batch's run, recorded at F, with its `evidence/` tag at F, covering
  every lane live at F. A verdict pending requalification at F is accepted only
  if that batch retires it (every release candidate verdict is pending, because
  the identity-validation changes edited files every lane binds). It proves the
  tree is F plus consistent evidence recorded at F, not that the estate produced
  the evidence, which rests on the controller transcripts and the banking
  review. It needs full history, so the release `publish` job checks out with
  `fetch-depth: 0` as `release-identity` already did; a shallow clone is
  refused. Release candidate tags are checked as before; an approved release
  that still lists an item as pending is refused; the approval commit must keep
  both remaining-item lists (non-empty, no item still marked pending) rather
  than delete them, and the gate checks that, not that each item is truly
  closed; an empty commit between the freeze commit and the tag is refused;
  malformed JSON that Python itself cannot decode (an integer past the digit
  limit, nesting past the recursion limit) is a clean gate refusal; and its
  remote-tag check accepts the annotated tag object as well as the commit,
  for whichever `GITHUB_SHA` carries. `tests/test_release_freeze_gate.py` holds
  each refusal against temporary git repositories. The owner's hands-on NVDA
  acceptance is waived for 1.1.0 and recorded as not run.
  `release-evidence-1.1.0.md` gives one approval sequence, which agrees with
  Plan 036.
- **What the gate refuses in a banked pack.** A pack may not add importable
  source or extension modules (`.py`, `.pyc`, `.pyo`, `.pyd`, `.pyw`, `.so`) or
  `.pth` path hooks. Archives are allowed, and real packs bank the builder's
  `candidate.zip` and `fdeploy-cases.zip`: an archive is importable only from a
  directory on `sys.path`, none of the pack directories is on it, and every test
  is byte-identical to F.
- **Preparing the freeze commit.** Banked batches keep their bytes by a
  `.gitattributes` pattern instead of a rule per batch label (no tracked file's
  attributes change). The evidence tests read "the current batch" from the
  release evidence report's `freeze` (`tests/current_qualification.py`), so
  banking a new batch needs no test edit, and new tests check a freeze batch once
  one exists. The verdict tables moved out of `tests/test_committed_evidence.py`
  into `docs/plan-033/verdict-registry.json` (canonical ASCII JSON), so banking
  edits data, never a Python file, and the module refuses to run a finalizer that
  is not a bare `finalize_*.py` in `scripts/windows-oracle/`. The sdist no longer
  carries the release records, any banked evidence (about 6,000 files) or
  `tests/`, so the distributions built at a final tag are the ones built at its
  freeze commit (`tests/test_release_artifacts_from_freeze.py`). `README.md`
  names no run, and `retarget` no longer rewrites it (README is the wheel's long
  description). The capability matrix header no longer names `1.1.0` in prose
  (its test ignores link destinations). `bank-requal-batch.py manifest` refuses
  to run without `--cleanup` (the post-batch directory check the gate requires)
  unless told `--without-cleanup`, and `retarget` no longer rewrites a commit
  that lanes outside the new batch still bind (it rewrote all 19 unchanged lanes'
  citations in `platforms.json` before this fix).
- **The tagged run records the automated evidence the approval says it does.**
  `scripts/record_release_identity.py` replaces the published manifest's
  "To be resolved" Automated evidence paragraph, and adds
  `evidence.automated_evidence` and a fuller `release_identity` to the published
  report: the workflow run's URL, id, attempt and ref; that `release-identity`,
  `ci`, `identifier-gate` and `verify` passed before publish (which `needs:`
  them); that `verify` checked coverage against the floor (the total is not
  carried to the publish job, so it is not recorded); and the artifact hashes.
  It validates each GitHub variable with `fullmatch` (a trailing newline was
  accepted, as the gate's anchored validators now also reject), records the
  commit `GITHUB_SHA` resolves to (with the tag object beside it when an
  annotated tag was pushed; `finalize_sbom.py` derives the SBOM serial from that
  commit too), fills in the published manifest's `Source commit` header, and
  refuses, writing nothing, if a GitHub variable is missing or malformed, if the
  placeholder is not there exactly once, or if placeholder text survives
  anywhere in either published file. A test pins that it runs before the
  published files are hashed, attested and released.
- **Every Python install in `ci.yml` and `release.yml` resolves from
  `uv.lock`** (`uv sync --locked`, or the exported hash-pinned requirements for
  the clean-environment installs of the built wheel and sdist); before, only the
  Windows test job did. Build, SBOM and audit tools come from a locked `release`
  dependency group, the build uses its hatchling without isolation, and CI runs
  `uv lock --check`. The project build itself uses the locked backend too:
  `[tool.uv]` turns isolation off for gpo-studio, lists every locked dependency
  in `no-build-package`, and makes the locked `build-backend` group (hatchling,
  editables) a default group, with requirements exports using
  `--no-default-groups`; `scripts/check_locked_build_backend.py`, run in CI,
  proves it offline against a cache holding only that group, with an isolated
  build failing as control. `tests/test_locked_installs.py` holds the rule over
  the parsed workflows, the smoke script and the package.json scripts they run:
  every command must equal one entry of a closed table of reviewed templates
  (literal tokens and a few tightly patterned slots, each with its reason), as
  must every `env:` entry, redirection target and PowerShell step, and a command
  that needs a lock export, `npm ci` or a built wheel needs it earlier in the
  same job, and a checkout after an install or build invalidates the
  prerequisite that install or build had established (a checkout can replace
  what it produced). Matching is on the words as written; inline interpreter code is
  refused (the few inline Python checks moved to `scripts/`); no tracked file may
  configure the tools the templates run (`.npmrc`, `pip.conf`, `uv.toml`,
  `sitecustomize.py`, ...); and it checks workflow text, not what the code it lets
  run does (`tests/workflow_grammar.py` lists its limits). Each action is allowed
  only at its reviewed revision, with its behaviour-selecting inputs (setup-uv's
  `version`, setup-python's and setup-node's versions) required, and CI installs
  uv 0.12.0, the version that generated `uv.lock`, in every job.
- **Supply-chain details.** CI no longer installs PSScriptAnalyzer from PSGallery
  by version: it fetches the 1.25.0 package, checks its SHA-256 against the value
  in `ci.yml`, and imports it from the job's temporary directory.
  `package-lock.json` now carries `resolved` and `integrity` for every package
  (84 of 148 entries had neither, so `npm ci` fetched them by version with
  nothing to check them against; the values were taken from the registry for the
  exact locked versions, and a clean `npm ci` verified them). CI runs Playwright
  from `node_modules/.bin`, never through `npx`, and every Python command in the
  workflows and the smoke script runs with `-I`, so a module in the checkout
  cannot stand in for the locked tool it names. The files each locked tool
  discovers on its own (pytest 9's `pytest.toml` among them) are listed against
  the tool's locked version, and a version change fails a test until the list is
  re-read. Names are compared as the Windows job's NTFS compares them, tracked
  symlinks are refused, and every accepted script is also traced through bash.
  CI's pip installs are wheel-only (`--only-binary :all:`; the project's own sdist
  builds without isolation against the locked backend), every `uv.lock` package
  has a wheel each CI platform accepts (judged by `packaging.tags`), and `npm ci`
  runs with `--ignore-scripts`. `npm run lint` lists its files after `--` instead
  of the `*.config.mjs` glob (which a tracked file named `--config=...` could have
  turned into an ESLint option); no template may hold such a glob, and no tracked
  name may start with `-`. The npm scripts call their tools by local path
  (`node_modules/.bin/...`). The dependency audit covers every platform's locked
  packages (pip-audit evaluates markers against the Linux runner, so `colorama`,
  Windows only, was not audited; the markers are now dropped from the audited
  copy).
- **CI on PR #107.** The history secret scan's one finding (`generic-api-key` on
  a vocabulary-table row of `emit_integrity.py` in `f728e22`,
  `secondFailure="token"`: "token" names a value kind) is ignored by its exact
  fingerprint in `.gitleaksignore`; the scanner's rules are unchanged. Windows:
  `scripts/check_safety.py` and `scripts/generate_evidence_report.py` read UTF-8
  explicitly, and a test holds every text open in `src/` and `scripts/` to an
  explicit encoding (the evidence-bound firewall harness excepted, by name); the
  release-artifact test copies the tree to a short temporary root, under
  MAX_PATH; the attribute test passes paths to `git check-attr` on stdin; the
  two-spellings test skips on a case-folding filesystem, detected, with the
  reader covered by a given listing. Timeouts: `test` 25 to 40 minutes (3.13
  measures branch coverage with the C tracer, about 28 minutes), `test-windows`
  40 to 60 minutes (about 37 minutes measured) and later 90 minutes.
- **The first `v1.1.0` tag did not publish, so 1.1.0 was re-frozen.** Release
  workflow run 39361251167 cancelled `verify` (the suite with branch coverage on
  Python 3.13) at its 25-minute limit; `verify` is now 60 minutes and `test` 55
  (35 minutes measured in that run), and the tag moves to the new approval.
  The gate now also requires every commit between the freeze and the approval
  to keep both release records a candidate's, so A approves both at once.

### Evidence

- `scripts/mutation_check_roundtrip.py`, run by a new `mutation-check` CI job,
  restores 20 defects in throwaway copies of the tree (parsers that read nothing,
  serialisers that drop what they were given, comparators that always agree, the
  WI-080 and WI-081 export defects) and requires the tests named for each to
  fail. Its first run found three survivors, and the two tests behind them are
  strengthened: the fdeploy serializer guard never triggered the 2026-09-11
  "return `raw_text` when it re-parses" defect it was written against, and the
  unmodelled-attribute preservation test checked only an attribute WI-081 had
  since typed, so it passed with the retained element ignored entirely. Review
  then found two more survivors, now killed by new tests and kept as mutants:
  `Registry.pol` `**del.` and `**delvals.` markers read case-sensitively (every
  test spelled them in lower case), and report parity dropping the GPP item UIDs
  Windows reported.
- `tests/test_release_downgrade_refusal.py` re-executes 1.0.0's own version
  guards, vendored verbatim from the tag (and compared with it when present),
  against what this tree writes: 1.0.0 refuses the upgraded workspace without
  changing a byte of it, and refuses a backup of it from the sidecar this tree's
  `backup_workspace` writes today. It also requires a release-written workspace
  fixture for every schema a release left behind, and holds this tree to refusing
  a newer workspace without writing to it.
- **The legacy-load oracle.** A differential test runs the `58b80aa` `gpp` and
  `stored_types` (vendored verbatim under `tests/oracles` with their provenance
  and blob digests, checked against git where the commit is present, so the test
  needs no history) as an oracle and, over 800 generated legacy cases (uids at
  entry and value level, missing and extra keys, every legacy key of the
  loader-key table), requires the same model and identical bytes wherever the old
  loader accepted well-typed input, a refusal wherever it refused, and a refusal
  of every ill-typed one. The oracle runs none of the current code: its whole
  `58b80aa` import closure (fifteen modules, each with its blob's SHA-256) is
  vendored and served by an import hook that serves nothing else, and loading
  asserts that no oracle module holds anything of the current package.
- **Fuzzing and sweeps.** A type fuzz plants null, a boolean, a number, a string,
  an array and an object in each of the 448 stored fields of a GPO holding every
  kind of item: the load, validation and payload never raise, a wrong type blocks
  every content export, every GPO route answers without a 500 for every field
  outside the preference families and for each field of a group, member,
  targeting predicate, common options and registry item and value, a mutation
  commits exactly when it answers, and a stored boolean reaches the plan exactly
  or the plan is refused. That exhaustive run takes over ten minutes, so it runs
  in CI's own Linux job (`type-fuzz-full`, `GPO_STUDIO_FULL_FUZZ=1`); the default
  suite plants every JSON type in every field of a GPO cut down to that field's
  item and drives every route with one field of each kind (about 80 s). Further
  tests plant -1 and 2^63 in every field that admits an integer, every JSON type
  and both integers in every legacy printer and service key, and a value outside
  its set in every closed-set field of every preference item carrying a retained
  record (driving the load, payload, report, digests, a diff and a committed
  edit; the exhaustive forms run in `type-fuzz-full`). A sweep plants sentinel
  identity values in a native import and calls every route of the route table
  (GETs and mutations, with bodies that let the GPO routes work), searching each
  ZIP response's members decompressed, nested archives included, and finds none
  in any response, JSON or artifact; a control shows it sees a leak. Profiling
  tests hold the GPO-payload routes (enumerated from the route table and from
  every handler that builds a GPO payload) to calling no writer on refused
  content, and `tests/test_egress.py` enumerates the routes at run time and fails
  on one not declared or an artifact route not wrapped, makes the writer behind
  each artifact route emit something hostile and requires a 422; the plan grammar
  is held equal to what the writer produces over an exhaustive generator and a
  property test over random GPOs. `tests/test_emit_integrity.py` runs every writer
  over the native captures, the stored baselines, the lane candidates' templates
  and synthetic inputs covering every typed family, targeting predicate and
  common option, and fails on any emitted name the tables do not classify.
  `tests/test_powershell_execution.py` runs the plan and each discovery script
  under `pwsh` with every single-quote character and code after it, and the
  U+FFFF/U+10000 boundary, and skips where `pwsh` is absent.
  `tests/test_identity_fields.py` feeds each hostile shape through each field and
  route and holds the repair path, and `tests/test_whole_value_validators.py`
  lists the lane-bound modules whose whole-value matching was swept and fails on
  any new site.
- **Requalification.** The release 1.1.0 successor batch is banked
  ([batch note](docs/plan-033/release110-successors-batch.md)): the WI-080,
  WI-081 and WI-082 fix changed `gpp.py`, `gpp_adapters.py`, `canonical.py`,
  `report_parity.py` and `backup_inventory.py`, which exactly six release 1.1.0
  verdicts bind, and those six lanes (WP-1B, scripts-metadata, publication,
  report parity, firewall, fdeploy) re-ran at `9940561` and all passed (schema 3
  manifest: `test_scope_tool` and `exec_failed` false on every row), with a clean
  post-batch directory check; WI-080, WI-081 and WI-082 close. Later changes
  (identity validation, the writers, egress, stored-data typing) edited files
  every lane binds, so every release candidate verdict is pending
  requalification, and 1.1.0's live evidence is the full 26-lane batch at the
  freeze commit, recorded in
  [the 1.1.0 evidence manifest](docs/release-evidence-1.1.0.md).

## [1.1.0-rc.1] - 2026-10-09

> **Release candidate, not the final 1.1.0 record.** `1.1.0rc1` is published
> as a GitHub prerelease so that its wheel and checksum are fixed for the
> owner's hands-on NVDA acceptance; it is not approved for production use.
> Every Plan 034 exit has landed, and the
> [release 1.1.0 batch](docs/plan-033/release110-batch.md) requalified all 26
> lanes at one commit. The
> [1.1.0 evidence manifest](docs/release-evidence-1.1.0.md) is marked for this
> candidate and lists what remains before final approval (target 2026-10-31;
> see [the direction](docs/direction-2026-10-07-plan-034-completion.md)). When
> 1.1.0 is approved it gets its own entry, as 1.0.0 did after its candidates.
> On 2026-10-08 the entries were regrouped from a single chronological list
> into the sections below. Their text is unchanged except for three kinds of
> edit: notes marked *Later:*, which record where a newer entry superseded a
> statement; parenthetical cross-references where one entry was split across
> two sections; and entries marked *New in this draft* (or, added while
> preparing this candidate, *New in this candidate*).

> Post-1.0 development has added much more to `src/` than to the
> operator-facing product. The entries below mark **surfaced** capabilities
> (reachable from the API or browser application) separately from **domain
> layers** (implemented and unit-tested, reachable from neither). No post-1.0
> capability is Windows-verified except where an entry cites a Plan 033
> workpackage.
>
> As of 2026-07-29 the unsurfaced domain layers are classed as **unproven
> drafts, not assets awaiting wiring**. That is a claim about correctness, not
> only reach: every layer an external oracle has examined has needed
> correction. See [`docs/domain-layer-status.md`](docs/domain-layer-status.md).

### Upgrading from 1.0.0

*New in this draft.* Measured against a workspace written by the 1.0.0
release itself (`tests/fixtures/release-1.0.0-workspace/`,
`tests/test_release_upgrade_from_1_0_0.py`).

- **The workspace schema moves from 1 to 4, in place, with no automatic
  backup.** The first start of 1.1.0 migrates the file. Every stored snapshot
  survives byte for byte, and every field 1.0.0 served is served unchanged.
  After the migration 1.0.0 refuses the file
  (`Workspace schema version 4 is newer than this version of GPO Studio
  supports (1)`) and refuses to restore a backup of it. **Back up with 1.0.0
  before upgrading.** Rolling back means reinstalling 1.0.0 and restoring that
  backup. See [the Windows quickstart](docs/windows-quickstart.md#roll-back-an-upgrade)
  and [workspace recovery](docs/workspace-recovery.md#upgrading-and-rolling-back-across-a-schema-change).
- **`Registry.pol` bytes can change for unchanged policy.** Within a key,
  records are now ordered delete-all-values, delete, then set (see Changed).
  A GPO with a delete action exports the same records in a different order,
  so its file hash differs from the one 1.0.0 produced.
- **Review digests change for GPOs with preference items.** The canonical
  form now carries empty entries for the preference families added by Plan
  024, so `policy_semantic_sha256` and `review_model_sha256` differ from the
  values 1.0.0 recorded, although no value changed. Digests of GPOs without
  preference items are unchanged.
- **GPP Registry native GPMC backup export is back** (batch 2, WI-075). From
  WP-1B until batch 2 it was refused, because native GPP output is an
  allowlist of captured families (Plan 033 WP-2, WI-046) and Registry's pair
  had never been captured. Batch 2 captured it and every item shape, so a GPO
  with GPP Registry items exports again; a default-value item is still refused
  (`unmeasured_gpp_registry_shape`).
- **GPP Registry wire values change.** REG_DWORD and REG_QWORD are written as
  fixed-width upper-case hex and REG_MULTI_SZ as a space-joined value plus a
  `<Values>` list, as Windows writes them. A Studio 1.0 bundle's decimal or
  `;`-joined GPP Registry values are refused on re-import (re-author them);
  workspaces are unaffected, since they store the typed model. A native
  REG_QWORD imported by 1.0 was read as decimal and should be re-imported.
- **Archives are stored, not deflated.** Every ZIP (GPMC backup, Studio bundle)
  is written with STORED members in code-point order so it is byte-identical on
  every platform. Files are larger and their hashes differ from 1.0's; the
  members' contents do not change.
- **Text XML cannot carry is refused.** A lone surrogate, a control character
  other than TAB and LF, or a carriage return (outside Registry.pol data) is
  refused with `text_not_xml_writable`. A 1.0 workspace holding such text still
  opens and lists; that GPO's exports are refused until the named fields are
  edited.
- **Preference document order is recorded from 1.1.0 imports onward**
  (WI-072, WI-073). A GPO imported by 1.0.0 has no recorded order, so it keeps
  writing what 1.0.0 wrote: scheduled tasks before immediate tasks and groups
  before local users, whatever order the source file had. Re-import the backup
  with 1.1.0 to recover the source's processing order. Root content 1.0.0
  retained but never wrote (Power Options' `GlobalPowerOptionsV2`, an adapter
  root's attributes) is now written for those GPOs too, so their exported
  files gain it. Review digests of stored GPOs are unchanged: the canonical
  form carries document order only where it differs from the grouped order.
- **Do not run 1.0.0's `workspace check` on a backup** (WI-074). It writes into
  the checked file, and the backup can then no longer be restored. Verify a
  pre-upgrade backup by restoring it to a throwaway path and checking that
  copy, as the updated runbooks do.

### Added

- Added the firewall surface (Plan 034, WI-076), over the firewall lane's first
  certification, `firewall-20261008094055-2092337` (36/36 at `a6e0002`, on
  LabMS01, WS2025 26100, PowerShell 5.1), now banked under
  `docs/plan-033/wp3-evidence/firewall-20261008/` and live. Its read leg parses
  rules Windows authored with `New-NetFirewallRule -PolicyStore` with zero
  unrecognised records; its write leg imports Studio's backup with
  `Import-GPO` and gets Studio's Registry.pol back byte for byte. See
  [the results](docs/plan-033/firewall-results.md).
  - **Render.** `POST /api/network-security/firewall/render` turns typed rules
    and per-profile settings into `registry_settings` in the shape
    `POST /api/gpos/{guid}/settings` accepts, plus the raw rule strings. It
    writes nothing. Anything outside the measured tranche is a 422 with the
    codec's issue code. A test holds the output for the certified request
    equal to the lane builder's, and shows those settings, posted to a GPO and
    exported, give the Registry.pol Windows returned.
  - **Decode.** `GET /api/gpos/{guid}/firewall-policy` decodes a GPO's firewall
    records, imported GPOs included (`empty`, `decoded`, `legacy` or
    `refused`), with unknown rule tokens preserved and flagged. A test holds
    the decode of the banked native fixture equal to the finalizer's parse.
  - Every response carries `policy_store_readback_not_application`,
    `representative_tranche_only`, `ipsec_pki_wired_wireless_out_of_scope`,
    `gpme_display_unmeasured` and `single_build_measured`.
  - **Breaking (unsurfaced module):** `network_security.py`'s legacy
    `FirewallRule`, `FirewallPolicy` and `FirewallProtocol` are removed; the
    module re-exports `firewall_policy`'s classes under explicit names, and
    `assess_network_security` treats the firewall as disabled only when every
    profile is explicitly `enabled=False`. IPsec, Public Key, wired and
    wireless stay out of scope for 1.x.
  - The capability matrix marks the firewall `lane-backed and surfaced`, Plan
    034's row reaches `yes` for the firewall, and Plan 025 leaves the
    unsurfaced domain-layer set. WI-076 is closed. WI-077 records the one
    observation the lane made without asserting: Studio's export registers the
    `D02B1F72` tool GUID where native authoring registers `B05566AC`; GPMC's
    report rendered both, and GPME display is unmeasured.
  - The firewall lane binds the export chain, so with the lifecycle and
    report-parity lanes also banked, `gpp.py`, `model.py`, `registry_pol.py`
    and `xml_safety.py` now cost four lanes, `canonical.py`, `export.py` and
    `validation.py` three, and `publication.py` two (`bound-source-cost.md`
    regenerated).

- Banked the same-domain lifecycle lane and added its restore-plan surface
  (Plan 034). Run `lifecycle-20261008093248-2000-c76d10eb3f2849fe` passed at
  `3513052` on a clean tree, on LabMS01 (WS2025, PowerShell 5.1). All 30 cells
  of `lifecycle.SCOPE_SURVIVAL` (five GPMC operations by six scope dimensions)
  agreed with what Windows did, as did the five plan-identity claims and the
  backup bridge, and cleanup was proven empty. The pack is
  `docs/plan-033/wp7-evidence/lifecycle/`. `tests/test_lifecycle_verdict.py`
  re-grades its raw `result.json` with the shipping finalizer and holds the
  table equal to the observed cells. See
  [the results](docs/plan-033/lifecycle-results.md), which also record the
  `Backup.xml` WMI wire shape (`MSFT_SomFilter.ID="{id}",Domain="DOMAIN"` plus
  `WMIFilterName`), the DC/member clock-skew finding from exploratory run 1,
  and the one residual that cannot be closed from the client.
  - **Restore plan.** `POST /api/lifecycle/restore-plan` takes a workspace GPO
    imported from a Windows backup, an operation (`restore_in_place`,
    `import_into_existing`, `import_as_new`, `copy`, `copy_with_acl`), the
    target arguments and optional `existing_gpo_names`. It returns
    `generate_restore_plan`'s plan: cmdlet, target identity,
    `requires_target_absent`, preconditions and warnings. Each survival cell
    is marked measured and cites the run, except a WMI cell for a backup
    whose `WMIFilter` reference is not the measured `MSFT_SomFilter` shape in
    the GPO's own domain with a `WMIFilterName`: that cell is `measured: false`
    with an `unmeasured_reason`. Every response carries six
    limitations: `studio_executes_nothing`, `same_domain_only`,
    `cross_domain_out_of_scope`, `one_topology_measured`,
    `deleted_gpo_restore_unmeasured` and `target_state_unchecked`. These are
    refused with 422 and a code: GPOs authored in Studio and forks of an import
    (the lane measured Windows backups only). Which GPOs count as the direct
    import of a backup is read from their own immutable revision 1 and retained
    `Backup.xml`, never from fields an edit can change. `import_into_existing`
    by `-TargetName` (the lane used `-TargetGuid` only), cross-domain targets,
    taken target names, and malformed target arguments. The surface composes in
    `api.py` and touches no bound file.
  - `lifecycle` reaches `yes` for its same-domain half in Plan 034's table and
    is `lane-backed and surfaced` in the capability matrix. Cross-domain stays
    out of scope by the 2026-10-07 ruling. Plan 028 leaves the unsurfaced
    domain-layer set: `gpmc_interop`, its other module, was reduced to the one
    type `publication` imports.

- Added two Plan 034 surfaces over the lanes the
  [Plan 034 batch](docs/plan-033/plan034-batch.md) requalified:
  Scripts metadata (`scripts-r10-20261008074828-8492`, 20/20) and publication
  completeness (`publication-completeness-20261008074904-1047`, 21/21), both
  at frozen commit `263f196`. Neither surface touches a bound file: both
  compose in `api.py`. With a current verdict and a delivery surface each,
  `script_policy` and `publication` reach `yes` in Plan 034's table and are
  `lane-backed and surfaced` in the capability matrix, and Plans 026 and 030
  leave the unsurfaced domain-layer set (`publisher.py` stays out of scope for
  1.x, code retained). See [the operator guide](docs/scripts-and-publication-preview.md).
  - **Scripts export.** `POST /api/gpos/{guid}/gpmc-backup-with-scripts`
    returns a GPMC backup carrying `scripts.ini`/`psscripts.ini`, built by
    `gpmc_backup_bundle(gpo, scripts=...)` and `native_backup_refusal`
    unchanged. `.../preview` returns both INI texts read out of the same ZIP,
    its SHA-256, warnings and limitations. A test holds the endpoint's bytes
    equal to the R10 lane builder's for the certified request, and a second
    holds its INI files equal to the banked native GPMC capture. A preview
    response that arrives after an edit, reopen or close is discarded. Shapes the lane
    did not measure are refused with 422 and a code, not warned about:
    user-side scripts (WI-071), shutdown/logon/logoff, PowerShell run last or
    unordered, a GPO with registry or preference content, and a GPO with a
    disabled side. Every response carries `payload_not_carried`,
    `execution_unmeasured`, `gpme_editing_unmeasured` and
    `one_entry_shape_measured` (the ZIP in `X-GPO-Studio-Limitations`). A new
    `scripts_export` artifact capability advertises the GPO-level refusals. A
    "Scripts" sidebar panel builds the request, previews the files with the
    limitations above them, and downloads the backup.
  - **Publication preview.** `GET /api/gpos/{guid}/publication-plan?target=`
    returns the planner's steps, rollback steps, planned SYSVOL paths, payload
    digest and validator issues, without `plan_id`, and marks every step
    `measured`, `unmeasured` or `refused`. `measured` is exactly the step kinds
    the publication-completeness lane grades, derived from its builder and
    finalizer by a test; `refused` is read off `validate_publication_plan`.
    An `update_extension_lists` step is `measured` only when every family/side
    it registers is one the lane imported, otherwise `unmeasured` with a
    `coverage_reason` naming the families. Nothing writes, and `publisher.py` stays unreachable. A "Publication
    preview" button beside the export actions opens the plan grouped by SYSVOL
    and Active Directory, with coverage badges, limitations on top, and a
    banner saying nothing here writes.
- Plan 023: scope of management, delegation, WMI filters and loopback,
  **surfaced**. `som.py`, `delegation.py`, `wmi_filter.py` and
  `ad_discovery.py` back new API endpoints for GPO links, loopback validation
  and description, AD discovery script generation and ingest
  (`/api/discovery/*`), and effective-rights evaluation. Discovery generates
  PowerShell and parses its JSON output. It does no network I/O itself, so the
  offline-first charter holds. *Later:* these modules are surfaced but not
  Windows-verified, and 1.1.0 carries them as stated limitations
  ([capability matrix](docs/capability-matrix.md#other-modules)).
- Plan 024: full GPP adapter coverage, **surfaced**. `gpp_adapters.py` extends
  the 1.0 Groups/Registry slice across the in-box preference families through
  `gpp.py`, `canonical.py` and `import_export.py`. *Later:* surfaced but not
  Windows-verified, as above.
- Plan 029: RSOP prediction, **certified in twelve measured regions, then
  surfaced** (WI-030, 2026-08-06). `POST /api/rsop/compute` predicts the
  effective policy for a computer/user pair over a topology supplied in the
  request body. `POST /api/rsop/compare` computes two predictions and reports
  where the effective settings differ. A thin browser panel ("RSOP prediction"
  in the rail) covers `compute` only: the target is entered as form fields and
  the topology as JSON, with no builder, because the workspace holds draft
  policies rather than an estate to build a topology from.
  The twelve certifying scenarios ran against a real Windows 11 26200 client.
  They cover LSDOU ordering, link order, inheritance and its blocking,
  enforcement, disabled links and sides, security filtering with denies on both
  Apply and Read, user scope, and loopback merge and replace.
  `docs/capability-matrix.md` lists them and states what is **not** certified,
  including WI-049's two filter cells, which the surface exposes and which were
  settled by reasoning rather than measurement. Every response announces two
  limitations instead of leaving them to the docs: `gpo_status_is_not_per_side`
  (WI-032: applied-GPO status collapses to "applied on at least one side" and
  cannot answer for each side) and, when the caller sets one of the fields,
  `slow_link_and_safe_mode_are_not_evaluated` (WI-036). This is the first
  post-1.0 layer to meet both halves of the exit condition in
  `docs/domain-layer-status.md`. *Later:* both limitations are gone. WI-032
  made the answer per side and WI-036 removed the fields (see Changed and
  Removed). WI-049's cells were measured (see Changed).
- Plan 034 WP-3: `policy_families.py` is reachable.
  `POST /api/security-template/policy-families` renders the account, audit,
  user-rights and security-options families as a `GptTmpl.inf`: text for
  reading, UTF-16LE/BOM/CRLF bytes for writing. It covers only the emission
  direction that its member and DC lanes certified (21/21 each at `4e27f27`).
  It does not parse a template back, because that direction reaches its oracle
  only through a GPMC snap-in and no lane certifies it. Every response carries
  the three limits the lane did not reach: `/configure` is never invoked, one
  tranche of values was measured, and GPME editing is unmeasured. A
  member-server render omits the Kerberos section, as the lane's finalizer
  requires. This is the second layer to leave the unproven-draft set, after
  `rsop.py`, and the first from Plan 025, whose three other modules remain in
  it.
  The composition lives in `api.py` because the serializers, their codec and
  the candidate builder are all in the verdicts' bound file set.
  `tests/test_policy_family_surface.py` holds it equal to the certified
  builder's in both scopes, so a second composition cannot drift.
- Plan 034 WP-3: `object_security.py` is reachable.
  `POST /api/security-template/object-security` renders registry-key,
  file-system and service security as a `GptTmpl.inf`. It covers only the
  three families its lane certified (18/18 at `f5cad577`, propagation codes
  0/1/2 and startup codes 2/3/4), and only the emission direction. Restricted
  groups cannot be rendered, because no lane has read that serializer. Every
  response carries four limits, including `acl_content_is_not_judged`
  (WI-055's ruling), shown where a caller reads the answer instead of as an
  empty `issues` list that looks like approval. *Later:* requalified in the
  Plan 034 batch. The live verdict is the successor at `1fb3f56` (20/20).
  Restricted groups are now lane-certified, but this endpoint still does not
  render them.
- Added the "Security template" panel: one dialog for both Plan 034 WP-3
  surfaces, switched by a mode selector. Like the RSOP panel, it renders
  `limitations` **above** the answer. It is kept thin: families are entered as
  JSON, and only `scope` is a form field. The `scope` control is hidden for
  object security, which has no such distinction. An empty validation list is
  shown with WI-055's ruling beside it, not as a bare "no issues". Seven
  browser tests, including an axe scan of the open dialog, which the
  workspace-wide scan cannot reach because it runs with every dialog closed.
- Plan 034 WP-4 is decided: Folder Redirection is a **read target**, and the
  writer is deferred behind R12
  ([the decision](docs/scope-decision-2026-09-11-folder-redirection.md)). The
  read half is `src/gpo_studio/fdeploy.py`: a strict UTF-16LE/BOM codec, a
  lossless parse, structural validation, review rendering, and a diff keyed on
  `(folder GUID, principal)`. It is reachable at
  `POST /api/folder-redirection/fdeploy`. Before this, an operator saw
  `fdeploy1.ini` only as a 458-byte SHA-256 in the unmodeled-file inventory, and
  the module named `folder_redirection.py` addressed neither it nor the marker
  beside it.
  Four limits are carried in the module and in every response, not only in
  documentation:
  - `Flags` is carried as the integer Windows wrote, and **no bit is named**.
    One capture is one observation of a ten-bit word (WI-066).
  - One capture is also one shape, so multi-folder and multi-principal
    documents are unmeasured.
  - Twelve of the thirteen folder names are documented Windows constants that
    no lane has measured. An unrecognised GUID reports null rather than a guess.
  - There is no writer.

  No lane has read this artifact in either direction, so the banked R3 capture
  stands in for one: the reader is tested against bytes hash-bound to what GPMC
  wrote. That is stronger than a round trip through Studio's own output and
  weaker than a verdict.
  The parse reaches no `GPO`, so an imported backup's reports and diffs are
  unchanged. That field would land in `model.py`, which two live verdicts bind
  (WI-068, filed against the batch that owes WI-063 through WI-065).
  *Later:* WI-068 put the parse on `GPO`; see the next entry.
- Imported backups now carry their Folder Redirection file (WI-068).
  `read_backup` parses `User/Documents & Settings/fdeploy1.ini` onto
  `GPO.fdeploy`. The policy report renders it, and the GPO diffs compare it by
  folder and principal, including in the browser's three-way comparison. It is
  import provenance: it is in the review digest and not in the policy-semantic
  digest. GPMC backup export refuses a GPO carrying it, because Studio has no
  writer for the file (WI-066). The edit touches `model.py`, `canonical.py` and
  `export.py`, so WI-068 stays open until the publication and scripts-metadata
  lanes re-run in the estate requalification batch. *Later:* the batch re-ran
  both lanes, and WI-068 is closed.
- Added a Folder Redirection browser panel that reviews native `fdeploy` files
  and compares an earlier copy with a current one, through the existing API. It
  preserves the uploaded bytes, shows raw flags and both files' structural
  diagnostics, and distinguishes an empty redirection diff from identical file
  bytes. Each selected file is limited to 1 MiB. Changing files or closing the
  panel stops pending responses from restoring stale results. The panel keeps
  the reader's single-capture evidence limits; WI-066 and WI-068 remain open.
  *Later:* WI-068 is closed; WI-066 remains open.
- WI-060: native backup imports keep the original XML documents and a complete
  payload file inventory. Plain-text reports show imported native settings,
  including unmodeled Scripts commands, explicitly as a historical snapshot.
  Payload bytes still require the original backup. The regression compares 27
  Windows-produced backups, and the two affected qualifications each passed
  21/21 on fresh clean-source runs. See
  [the measured scope and evidence](docs/plan-033/backup-report-fidelity.md).
- WI-023 surfaces the modern `FilterOs` family-token limitation as a preflight
  warning and in Studio bundle manifests: `WINTHRESHOLDSRV` cannot distinguish
  Server 2016/2019/2022/2025, and `WINTHRESHOLD` cannot distinguish Windows 10
  from Windows 11. Imported OS criteria are shown read-only in the browser and
  survive edits instead of being silently dropped. For build-specific
  targeting, operators are directed to WMI or registry predicates.
- The browser application has a dark colour theme, **surfaced**. Every colour
  in `studio.css` now goes through a design token, and a `data-theme`
  attribute on `<html>` selects the palette. `Auto` follows the operating
  system; an explicit Dark or Light choice is saved per browser. The bootstrap
  (`static/js/theme.js`) is a classic head script, so the theme is resolved
  before first paint under the `script-src 'self'` CSP. A browser test runs the
  axe scan under the dark palette, holding it to the same accessibility bar as
  the light one. A choice made in one tab reaches the application's other tabs
  through a `storage` listener instead of waiting for a reload. Engines that
  ship only the deprecated `MediaQueryList.addListener` (Safari 13 and
  earlier) still follow the system while the mode is `Auto`.
- Plans 025–028, 030–032: domain layers, **not surfaced**. Security settings
  (`security_template.py`, `object_security.py`, `network_security.py`,
  `policy_families.py`), script and managed-artifact policy
  (`script_policy.py`, `artifact_store.py`), software installation and folder
  redirection (`software_install.py`, `folder_redirection.py`), GPMC lifecycle
  and interop (`lifecycle.py`, `gpmc_interop.py`), controlled publication
  (`publication.py`, `publisher.py`), certification (`certification.py`) and
  the hosted control plane (`hosting.py`) are implemented and unit-tested, but
  no API endpoint, UI module or export path reaches them. They are not operator
  capabilities and are excluded from the 1.0 contract; see
  `docs/capability-matrix.md`. The publication modules are pure and emit no
  writes, so the web process still never writes to AD or SYSVOL. `hosting.py`
  does not make a hosted mode available. *Later (state at this draft,
  2026-10-08):* `policy_families.py`, `object_security.py`,
  `script_policy.py` and `publication.py` are lane-backed and surfaced
  (above), as are the firewall half of `network_security.py` (through
  `firewall_policy.py`) and the same-domain half of `lifecycle.py`.
  `backup.py` and `report.py` are certified over their existing import and
  report surfaces for the families the corpus exercises (see Evidence).
  `artifact_store.py`, `software_install.py`, `folder_redirection.py` and
  `certification.py` are deleted, and `gpmc_interop.py` is reduced to one type
  (see Removed). `publisher.py` and `hosting.py` are out of scope for 1.x, as
  are IPsec, Public Key, wired and wireless policy and cross-domain lifecycle.
  `fdeploy.py` is lane-backed and surfaced for its read target (see Evidence);
  decoding `Flags` and any writer stay deferred under WI-066. No Plan 034 exit
  is open at this draft.

### Changed

- *New in this draft (batch 2):* **Deterministic archives everywhere (batch 2).** `gpo_studio.deterministic_zip`
  writes every ZIP Studio and the lane builders produce -- `gpmc_backup_bundle`,
  `export_bundle`, and the report-parity and fdeploy candidates (the WP-1B, WP-2,
  publication, scripts-metadata and firewall candidates come from
  `gpmc_backup_bundle`) -- with members sorted by code point, 1980-01-01
  timestamps, `create_system=3`, fixed attributes and STORED members. Deflate is
  not used: CPython's Windows build links a different deflate implementation
  than Linux, so no deflate setting yields identical bytes. The product exports
  are STORED too; the ZIP is a transport container (`Expand-Archive` and
  `zipfile` unpack it, `Import-GPO` reads the extracted folder), so only its
  size changes. Names that are unsafe or differ only by case are refused. The
  lane evidence tests (firewall, report-parity, fdeploy) assert the exact archive
  hash on every platform again, and pin that a Windows-default `create_system`
  changes nothing. The banked candidates were deflated, so those tests fail
  until the 1.1.0 requalification re-banks them.
- Within each key, `Registry.pol` records are now ordered delete-all-values,
  delete, then set. Registry settings gain a third action,
  `delete_all_values`, accepted by the settings API. It serializes as a
  `**delvals.` record and emits `Remove-GPRegistryValue -ValueName '*'` in the
  PowerShell plan. *New in this draft:* this landed in `6af849e` (2026-07-21)
  and was never recorded here. The 1.0.0 upgrade fixture found it, because
  1.0.0 ordered records by value name alone, so the same policy exported
  different bytes. No capability-matrix row covers `delete_all_values`.
- Plan 033 WP-2: deterministic native GPMC backup emission with distinct
  backup and GPO identities, v2 `Backup.xml`, native `DomainSysvol/GPO` paths,
  verified Registry and GPP extension profiles, and a Windows Server 2025
  `Import-GPO`/re-backup/cleanup oracle lane. Native GPP output is now a strict
  allowlist: Drive Maps, Local Users and Groups, Scheduled Tasks and Services
  are emitted. GPP Registry and uncaptured families must use the Studio bundle
  until their native extension metadata is independently verified. Services
  extension metadata is capture-backed, and its Studio-origin candidate passes
  `Import-GPO`, GPMC report comparison and `Backup-GPO` semantic comparison.
- WI-061: revision snapshots no longer each carry a full copy of the retained
  native XML. Schema v4 stores each distinct document once
  (`retained_documents`, keyed by the SHA-256 of the decoded bytes), with
  references from each snapshot. Every store read rehydrates the documents, so
  no API consumer sees the encoding. A v3 workspace migrates in place, and
  deleting the last snapshot that holds a document removes it. This batch
  re-earns the bound Scripts and publication qualifications.
- WI-061 (part): rows from `GET /api/gpos` and `GET /api/starter-gpos` no
  longer include WI-060's retained native XML. Rows report
  `has_backup_inventory`, and the detail endpoint serves the snapshot. The
  workbench refetches the list on load and after every mutation. The
  per-revision copies of the same bytes remain open. *Later:* closed by
  schema v4 (previous entry).
- RSOP prediction answers each side separately (WI-032). Each GPO row carries
  `computer_status` and `user_status`, and a result reports
  `computer_applied_gpos` and `user_applied_gpos`. Promoting the WP-9 lane's
  applied-set comparison from advisory to gated found a real over-report on its
  first run: the model reported a GPO as applied to a side it carried nothing
  for, which Windows omits. Corrected and re-certified across thirteen RSOP
  runs.
- The RSOP request models now refuse unknown keys, so a caller who sends one
  gets a 422 instead of a prediction that silently ignored it. (Split from the
  WI-036 entry under Removed.)
- WI-049 (corpus half): the Plan 033 RSOP corpus now has a row for each of the
  three filtering regions the model answers by reasoning rather than
  measurement: a read deny naming the user, resolved on the computer side; an
  Apply deny naming the computer, resolved on the user side; and a deny that
  matches through a group rather than by name. They are filter edits on two
  scenarios the lanes already run, not a session of their own. Each takes the
  top link order, so a wrong answer costs the predicted *winner* rather than one
  absent value.

  **Measured on the estate on 2026-09-06; all three agreed with the model**
  (`rsop-observe-20260906184434-8187` and
  `rsop-user-observe-20260906185345-9222`). A user-named read deny left the GPO
  applying on the computer side, a computer-named Apply deny left it applying
  on the user side, and a group-matched deny blocked. The API's
  `answer_rests_on_a_reasoned_cell` limitation is **removed** with them. It
  existed only while those cells were unmeasured; calling a measured answer
  reasoned would be the same defect as a matrix that says `failed` for a
  supported capability. **Operator-visible**: callers reading that code will
  stop seeing it. Still unmeasured: a deny matched through a *computer's*
  group, which now has its own item (WI-054).
- `object_security.validate()` does not judge ACL content, and that is now a
  recorded ruling (WI-055) rather than an unexplained gap. The RSOP surface's
  `limitations` array is therefore empty: each of the three limitations it
  carried was closed by fixing what it disclosed. (`certification.py`'s
  deletion, WI-056, from the same entry, is under Removed.)
- `POST /api/security-template/policy-families` now declares
  `empty_sections_unmeasured` when a family renders as a bare section header.
  `UserRightsFamily` and `SecurityOptionsFamily` always emit their section,
  while the object-security families omit theirs when empty. The two disagree,
  every section in the certified candidate had entries, and only the non-empty
  behaviour is measured. The limit is declared only when the render produced a
  bare header, so it is an exact property of the answer, not a guess about the
  caller.
- Plain-text GPO reports now count all 21 typed preference families per scope,
  including drives, services, scheduled tasks and immediate tasks. Native
  backup fixtures cover these counts. This is not full Get-GPOReport parity.
- The native backup's script refusals no longer say "Use the Studio
  publication bundle instead". That bundle carries no scripts at all; the
  message now says the GPMC backup with scripts is the only export that
  carries them, in the measured shape only, and names the change that makes
  the policy exportable. (Split from the requalification-batch retirements
  under Removed.)
- Publication planning now marks preserved CSE metadata and files as
  unsupported for SYSVOL targets and fails validation, instead of silently
  omitting them. The regression uses the qualified native Scripts rebackup.
  Generated scripts remain review-only and refuse all unverified operations.
  *Later:* the script generator is deleted (see Removed), and the planner is
  now reachable read-only through the publication preview (see Added).
- Wide policy tables keep their row actions reachable: the actions cell sticks
  to the right edge of the scrolling table card, so Edit, Comment and Delete
  no longer disappear behind an unsignalled horizontal scroll. Placeholders are
  styled differently from values, at a contrast ratio that meets WCAG AA
  against the light canvas (the first colour chosen did not). Secondary buttons
  have a quiet hover state. On narrow viewports the rail's footer is shown, not
  hidden, so its workspace status is not desktop-only.
- Plan 022 closed: the REVIEW AND REFINE gate passed on 2026-07-25
  (`docs/plan-022/gate-decision-2026-07-25.md`), with ADMX parser fixes and
  code hardening.
- Recorded the 2026-10-07 rulings for the modules no lane binds in the
  capability matrix, Plan 034 and the Plans 025–032 status lines:
  `security_template` exits through its consumers; `publisher` and `hosting`
  are out of scope for 1.x and kept; IPsec, Public Key, wired and wireless
  policy are out of scope for 1.x; `artifact_store` is deleted in the
  requalification batch. The matrix's Scripts row now says lane-backed rather
  than capture-backed. (The deletions from the same entry are under Removed.)
- Documentation: corrected the recorded status of Plans 021 and 023–032, which
  said `proposed (post-1.0)` although their implementations were already
  committed. Each now records whether its domain layer is surfaced and whether
  it has Windows evidence. The capability matrix and README gained an explicit
  inventory of landed-but-unreachable modules, so the matrix again matches what
  `src/` contains.
- Documentation: removed pre-release status language from `SECURITY.md` and
  Plans 017/019/020, wrote the 1.0.x support, compatibility and deprecation
  policy, and refined Plan 021 with a provisional target matrix, corpus
  licensing and redaction rules, and a pre-review spike boundary.
- Documentation, *new in this draft*: the Windows quickstart is no longer
  worded for 1.0 only, and it and the workspace recovery runbook now describe
  upgrading across a schema change and rolling back by restoring the
  pre-upgrade backup. `installation.md` points the schema-mismatch error at that
  procedure.
- Adopted `ruff` 0.16 and its expanded default rule set.
- Risk-based coverage floors now include `src/gpo_studio/evidence.py` (90%).

### Removed

- Requalification batch: retired the PowerShell publication script and the
  modules the 2026-10-07 operator ruling puts out of scope. The affected lanes
  (publication completeness and Scripts metadata) are **awaiting
  requalification**: their verdicts bind the pre-batch bytes of
  `publication.py`, `script_policy.py` and `export.py` until the estate re-runs
  them. No row moves to `yes`. *Later:* the batch requalified both lanes (see
  Evidence), and the Scripts export and publication preview surfaces then
  took both rows to `yes` (see Added).
  - `publication.py` no longer generates a script. `generate_publication_script`,
    `PowerShellPublicationScript`, the empty `_WINDOWS_VERIFIED_OPERATIONS`
    allowlist and their helpers are deleted, with their tests. The script
    copied files straight into SYSVOL, which `docs/live-publication.md` forbids.
    Publication goes through the native GPMC backup and `Import-GPO` plus
    `apply.ps1`. The planner, `validate_publication_plan`,
    `planned_sysvol_paths` and `payload_digest` are unchanged apart from the
    disabled-side refusal (under Fixed) and the lost `store=` argument below.
  - Deleted the pre-R2 `serialize_script_policy_ini` / `parse_script_policy_ini`
    from `script_policy.py`. They wrote an INI shape no Windows capture
    contains; the certified writer is `gpmc_backup_bundle(gpo, scripts=...)`.
    `preview_script_policy` is kept; nothing in the product calls it.
  - Deleted `artifact_store.py` and its tests. Delivering script or executable
    payloads is out of scope for 1.x. `validate_publication_plan` lost its
    optional `store=` cross-check and `preview_script_policy` its optional
    `artifact_store=` argument.
- Acted on the 2026-10-07 Plan 034 completion rulings
  ([the rulings](docs/direction-2026-10-07-plan-034-completion.md)) for the
  modules no lane binds, so no evidence expires:
  - Deleted `software_install.py` (writing was ruled out on 2026-09-06, and it
    had no consumer) and `folder_redirection.py` (superseded by `fdeploy.py`),
    with their tests. Both scope decisions carry a dated addendum.
  - Reduced `gpmc_interop.py` to `InteropIssue`, which `publication.py`
    imports. The interop checks are deleted. The importable predicate treated
    "Studio cannot emit this" as "GPMC cannot import this", and would have
    flagged 15 of the 26 production GPOs in the R6 census. `is_gpmc_editable`
    had no oracle. A test pins the remaining type's shape.
- Removed `slow_link`, `safe_mode`, `simulate_slow_link` and
  `simulate_safe_mode` from the RSOP model and API (WI-036). They were accepted
  and never read. (The RSOP surface first shipped after 1.0.0, so no released
  version accepted these fields.)
- `certification.py` is deleted as superseded (WI-056).
- `scripts/windows-oracle/remote-run.ps1`, the scheduled-task launcher, and
  every lane's SSH branch. The launcher existed only to get a logon token that
  a non-interactive SSH session cannot provide. It took the credential as a
  `schtasks /RP` argument, which was transient but decodable by a privileged
  observer on the host for as long as the task existed. PowerShell Direct
  carries the credential through the hypervisor and needs no launcher, so the
  removal is a security improvement. Certifications produced on the retired
  transport are not retracted, but their evidence packs can no longer be
  re-verified in this tree. `build_harness_inputs` reports that explicitly
  instead of defaulting to a file set that no longer exists.

### Fixed

Operator-facing:

- *New in this candidate:* **RSOP and Security template output that scrolls
  sideways is reachable from the keyboard.** A result table wider than the
  dialog (a blocked GPO's reasons are enough) scrolled horizontally inside a
  container that could not take focus, so a keyboard user could not scroll
  it. Each RSOP result table, the Security template's validation-issue table
  and its rendered `GptTmpl.inf` block now sit in focusable regions named
  after their content. Found by a new axe scan of the open RSOP dialog in the
  light and the dark theme, populated and with validation errors
  (`tests/browser/rsop.spec.mjs`), which closes the gap the workspace-wide
  scan leaves for closed dialogs, and by a narrow-window scan of a rendered
  security template.
- *New in this draft:* **GPP files keep their document order and retained root
  content after an edit, fixed and awaiting requalification (WI-072,
  WI-073).** Found by the report-parity offline differ, which re-renders the
  typed model as Studio writes it after any edit. Two defects:
  - WI-072: `serialize_gpp` rebuilt every adapter file from its typed items
    alone, so content the import retained at the root was dropped on the
    first edit. The native Power Options capture's `GlobalPowerOptionsV2`
    (the Windows 7+ power plan) was lost this way, and so was any adapter
    root's unknown attribute (a root-level `disabled="1"`, say) or child
    element (a printer kind or folder option Studio does not type). Every
    family now writes them: Groups and Registry already did; the eighteen
    adapter families did not.
  - WI-073: `ScheduledTasks.xml` interleaves `TaskV2` and `ImmediateTaskV2`,
    and `Groups.xml` can interleave `Group` and `User`. The model holds each
    family in its own list and wrote them grouped, so an edited GPO changed
    the order Windows processes its items. Both native scheduled-task
    captures showed it. No other GPP file holds more than one typed family.

  The model keeps its per-family lists and API. Import now records each typed
  item's index among its root's children (`document_position`) and each
  retained root child's (`GppCollection.root_unknown_positions`), and
  `serialize_gpp` writes every root child in that order. Within a family the
  list stays authoritative: reordering a family swaps its items between the
  family's own slots, deleting an item frees its slot, and an item inserted
  between positioned items follows its list predecessor. No recorded position
  can reorder two items of one family against their list order, even where
  slots tie (a legacy multi-value `<Registry>` expands into items sharing one
  slot). An item with no
  recorded position, such as a group added through the API or anything stored
  before 1.1.0, is written after every positioned item in the order Studio
  always used, so a collection without positions writes the same bytes as
  before. An API edit keeps the edited item's slot. Positions are persisted in
  the workspace snapshot; a stored order that cannot be honoured is refused on
  load, naming the slot: an unknown family, a position that is not an integer
  from 0 to 99999 (the XML parser's element bound, so no import can exceed it),
  a count that does not match, or two root children claiming one slot (within
  a family, across families, or against a retained child). Gaps, a legacy
  Registry expansion's shared slot, and the per-family copies Groups.xml and
  ScheduledTasks.xml keep of one retained child (which must agree) are
  accepted. Those per-family copies must also agree when an in-memory
  collection is written: differing copies are refused rather than merged.
  There is no longer a way to write a GPP file without its retained root
  content: `serialize_gpp_groups` and `serialize_gpp_registry` write the whole
  file as `serialize_gpp` does (Groups.xml now includes its local users), the
  unused item-only `ADAPTER_SERIALIZE_FUNCTIONS` map is removed, and the
  item-only `serialize_gpp_<family>` helpers are documented as test fragments,
  with a test that fails if production code calls one. Both the canonical digest and the GPO diff compare the resulting order,
  not the recorded numbers, so digests of stored GPOs do not move.
  Covered by `tests/test_gpp_document_order.py` (the native Power and
  scheduled-task captures edited, deleted from, reordered and added to; an
  interleaved `Groups.xml` through the store's group endpoints; every family's
  root unknowns in place; a workspace reopen; data in the pre-1.1 shape;
  Hypothesis properties over random interleavings). The report-parity lane
  now requires its three formerly divergent cases to agree with Windows
  exactly (see Evidence). Both items close when the requalification run banks.
  *Later:* the release 1.1.0 batch banked at `de9736e`; the three cases agreed
  exactly (`report-parity-20261009001727-3532`) and WI-072 and WI-073 are
  closed.

- *New in this draft:* **A user-side scheduled task with no principal ran as
  SYSTEM, fixed and awaiting requalification (WI-079).** `serialize_gpp`
  built Scheduled Tasks through `_build_adapter_root`, which dropped the
  collection's scope, so a user-side TaskV2 with an empty `run_as` was
  written with `runAs` and the payload's `UserId` set to `NT AUTHORITY\System`,
  the computer default. Every user-side TaskV2 in the native captures runs as
  `%LogonDomain%\%LogonUser%`, which `serialize_gpp_scheduled_tasks` already
  wrote. The scope now reaches every item serializer that takes it (only
  Scheduled Tasks has a scope-dependent default; a test pins that list to the
  serializers' signatures). Imported tasks carry their own `runAs` and are
  unaffected, and no lane candidate holds a user-side task, so no lane
  expectation moves. Covered by `tests/test_gpp_task_scope.py`, grounded in the
  native captures. *Later:* requalified by the release 1.1.0 batch at
  `de9736e`; WI-079 is closed.

- *New in this draft (batch 2):* **GPP Registry native export, fixed and awaiting batch-2 requalification
  (WI-075).** Since WP-1B the GPMC backup export refused every GPO with a GPP
  Registry item, narrowing the 1.0 contract the capability matrix still
  claimed. A native capture (2026-10-08, `tests/fixtures/native-gpp-registry-gpmc/WI01A-Registry-GPMC`)
  measured the extension pair `[{B087BE9D-…}{BEE07A6A-…}]` on both sides and
  the wire form, and showed Studio's reader and writer were both wrong:
  REG_DWORD/REG_QWORD are fixed-width upper-case hex (a native DWORD did not
  import; a QWORD was read as decimal), REG_MULTI_SZ is space-joined plus a
  `<Values>` list, and `<Registry>`/`<Properties>` carry `status`, `image`,
  braced upper-case `uid`, `displayDecimal` and `default` in a fixed order.
  The writer and reader now follow the capture (pinned against the native
  bytes in `tests/test_gpp_registry_native.py`), the pair is registered in
  `export._GPP_EXTENSION_PROFILES` (the one source for the backup, the
  publication planner and `EMITTED_EXTENSION_GUIDS`), and pre-batch-2 stored
  imports are re-typed on load. A revision-2 capture
  (`tests/fixtures/native-gpp-registry-gpmc/WI01A-RegistryShapes-GPMC`, from
  `scripts/plan-033/capture-gpp-registry-native.ps1`) measured Delete
  (`image="3"`), REG_BINARY (upper-case hex, no separators) and key-only items
  (named by the key, typed `REG_SZ`), so they export and publish. A full
  action x type matrix capture (`WI01A-RegistryMatrix-GPMC`, 28 items) then
  pinned every action x type pair and key-only x action as a whole item: the
  measured set is now an explicit list of those 28 pairs, held equal to the
  native bytes, rather than composed from parts. The GPP Registry captures live
  in `tests/fixtures/native-gpp-registry-gpmc` and are part of the report-parity
  corpus (see below). Default-value
  items stay refused (`unmeasured_gpp_registry_shape`) by the export and the
  planner: the GroupPolicy module has no `-Default` parameter, so nothing
  measured them. The decimal and `;`-joined forms Studio wrote before batch 2,
  and REG_BINARY that is not whole hex bytes, are refused on read.
  *Later:* requalified by the release 1.1.0 batch at `de9736e`
  (`wp1b-writer-20261009001221-5737`); WI-075 is closed.
- *New in this draft (batch 2):* **Firewall policy registers the firewall snap-in tool half.** A machine
  Registry.pol holding only `SOFTWARE\Policies\Microsoft\WindowsFirewall`
  keys now registers `[{35378EAC-…}{B05566AC-…}]`, as native authoring did
  (`tests/fixtures/native-firewall-gpmc`), instead of the
  Administrative Templates `{D02B1F72-…}`. Mixed firewall-and-other content is
  unchanged: no capture records it (WI-075).
- *New in this draft (batch 2):* **Text XML cannot carry never reaches storage (batch-2 review of `343fecf`).**
  A REG_MULTI_SZ element holding an escaped lone surrogate committed a revision
  and then made the GPO, its backup and the whole workspace list return 500;
  NUL, VT, FF and U+FFFF were accepted and exported as XML no parser accepts.
  Fixed generally: one predicate (`xml_safety.xml_text_problem`, the XML 1.0
  `Char` production; CR also refused in every string written into XML --
  names, descriptions, GPP text and attributes -- since XML reads it back as
  LF, and kept only in Registry.pol data and the fdeploy INI) and one walk over every string in the model (`unwritable_text`). The
  store refuses to write any revision that fails it, whatever endpoint or field
  the text came through; the API refuses such a JSON body up front (422 with
  the JSON path); `validate_gpo` reports it (`text_not_xml_writable`). A legacy
  stored revision still reads and lists (JSON escapes; digests `null`), every
  exporter refuses it with that code, and an artifact that cannot encode stored
  text is a 422 (`stored_text_not_encodable`), never a 500. A Hypothesis
  property test throws arbitrary Unicode, surrogates and controls included, at
  GPP and GPO text and holds no-500 and byte-exact round trips of accepted text.

- *New in this draft (batch 2):* **Batch-2 review fixes (two independent reviews of `303ae6c`).**
  - Every lane whose candidate bytes flow through `deterministic_zip.py` now
    binds it (WP-1B, WP-2, publication, scripts-metadata, firewall,
    report-parity, fdeploy, endpoint); WP-1B, WP-2 and endpoint also bind
    `export.py`, and WP-1B the writers it certifies (`gpp.py`,
    `gpp_adapters.py`, `writer_conformance.py`). A test derives the rule from
    each builder's imports, and verdicts finalized before the change keep their
    narrower table in `HISTORICAL_BOUND_FILES`.
  - `deterministic_zip` refuses NUL and control characters (zipfile truncated
    `"a\0x"` to `"a"`, shadowing a real member), Windows-forbidden characters
    and drive/stream colons, reserved device names (`NUL.txt`, `COM1`), trailing
    dots/spaces, and a file that is also another member's directory.
  - GPP Registry `<Values>` content Studio does not model (attributes, extra
    children, a second `<Values>`) is refused on read instead of dropped on
    the next edit. A list value for a non-multi-string type is refused by the
    writer, validation and the API (it was written `;`-joined and read back as
    one string), and a key-only item carrying a value is an unmeasured shape.
  - `report_parity` classifies report extensions by full QName (the
    `xsi:type` prefix's namespace plus local name) against a table read off the
    repository's Windows reports; an unknown namespace is `unmeasured:`. A
    registry Extension's children are routed by their own namespace, so policy
    settings and a GPP Registry container in one Extension are split. The GPP
    Registry report namespace has one definition (`gpp.GPP_REGISTRY_REPORT_NAMESPACE`).
  - The WP-1B `native_shape_matches_corpus` check now inspects emitted GPP
    Registry XML against the captured shape (root/item clsids, attribute order,
    image per action, hex encodings, `<Values>`), with the shape constants
    pinned to the native captures; a non-native writer fails it.
  - The native-bytes tests assert Studio's explicit common options carry
    exactly `removePolicy="0" userContext="0" bypassErrors="0"` for a model with
    the native items' meaning, instead of setting them aside.

- *New in this draft (batch 2):* **Batch-2 re-review fixes (Sol, `2ecc025`).** `deterministic_zip.py` and
  `writer_conformance.py` are pinned `text eol=lf` (a CRLF checkout refused
  every archive lane), and a test holds every file any finalizer binds to
  `text eol=lf` or `-text`. A key-only item's empty list is written as the
  measured `value=""` and stored as `""` (it used to commit and then make every
  read of the GPO a 500); preference content the writer refuses now reaches
  the client as a refusal, and a sweep of every registry item shape the API
  accepts holds that later reads and exports never 500. `<Values>` is held to an
  exact allowlist (no namespace, no attributes, whitespace-only text and
  tails). A registry report Extension whose declared type is the other
  measured family's is surfaced as `unmeasured:declared ...`, and GPP Registry
  report items must have the measured `<Registry>`/`<Properties>` shape. The
  superscript device names (`COM¹`, `LPT²`, `LPT³`) are refused in archives.

- *New in this draft (batch 2):* **Review tidy (N5–N8).** The object-security finalizer's comment no longer
  claims a v1 pack can be re-graded to anything but failure (N5). A refused
  native export reports every reason, cpassword first and the fdeploy refusal
  last (N6). SID patterns in `object_security.py` and the object-security
  finalizer are ASCII-only, so a Unicode-digit look-alike is not a SID (N7).
  `run-requal-batch.sh` resolves `<batch-dir>` without creating it, so its
  in-repo refusal leaves nothing behind (N8).
- *New in this draft:* `gpo-studio workspace check` no longer changes the
  database it checks (WI-074). It recorded its result in `workspace_meta`, so
  checking a backup changed the file after its sidecar recorded the SHA-256,
  and `workspace restore` then refused the backup with `Backup database
  checksum mismatch`. The runbooks prescribed exactly that check before a
  rollback. The check now opens the file read-only and leaves no side files
  beside a backup. **1.0.0 still has the defect**, so the runbooks now verify
  a backup by restoring a throwaway copy and checking the copy.
- WI-044: a GPO with a **deny** security filter advertised its PowerShell plan
  and Studio export bundle as available, then refused both downloads with HTTP
  422. WI-041's refusal is correct and unchanged. The problem was that
  `artifact_capabilities` derived availability from `validate_gpo`, which has
  no deny rule. The condition now lives in one place, `export.plan_refusal()`,
  which both the export path and the capability payload consult, so the
  operator is told up front, with the reason, before pressing a button.
  **Surfaced** (API and browser application).
- WI-046: the same defect as WI-044 in the next capability entry. A GPO with a
  **GPP Registry** preference advertised `gpmc_export` as available, then
  refused the native backup with `unsupported_native_gpp_extension`. Native
  backup covers four GPP families and `Registry` is not one of them, but
  neither `validate_gpo` nor the preserved-content count could see that.
  `export.native_backup_refusal()` now derives the advertisement by running the
  refusing code instead of restating its conditions. **Surfaced** (API and
  browser application).
- Fixed four defects in the fdeploy reader, found by a second-opinion review
  before it merged; three of them were introduced by the author. The review was
  **not** cross-lineage, although the first version of this entry said so: the
  reviewer was a Claude subagent, the same lineage as the author, so it carries
  none of the independence a cross-lineage review is cited for. The defects are
  real either way.
  - A `Flags` value longer than 4300 digits reached `int()`, which refuses that
    conversion and raises a bare `ValueError`. A 10 KB request returned 500 from
    a handler whose docstring says this surface never produces a server fault.
    The digit pattern is now bounded.
  - `is_marker` meant "no sections", so a file of prose was reported as the
    empty marker GPMC writes, validated clean, and described in a report line
    that said Windows wrote it: three false statements about a native artifact.
    It now makes one accurate statement.
  - `validate_fdeploy` was quadratic and its result unbounded. One document
    within the size cap took 4.19 s and produced a 5.4 MB answer; a capped
    document now takes 0.045 s.
  - `format_fdeploy` returned its input whenever a reparse matched, so
    `format(parse(t)) == t` reduced to `t == t`. Both round-trip tests passed
    against a parser mutated to return no sections at all. That is the
    self-consistency check AGENTS.md rejects, in a test whose docstring cited
    WI-064 to claim otherwise. The serializer now always rebuilds from the
    parsed document, which is why the document carries the file's preamble and
    each section's verbatim lines. The same mutation now fails twelve tests.
- Retained inventory paths are now deduplicated exactly, not
  case-insensitively. `read_backup` keys its file map case-sensitively, and
  feeding its own output back through the validator could refuse a capture the
  importer had just produced. Reports also state that native names and values
  are reproduced verbatim from the source domain.
- The topbar action links (`Policy report`, `Review PowerShell`,
  `GPMC backup`) rendered in default link blue, because `a.button` never got
  an ink colour; only real `<button>` elements did. Long GPO names also wrapped
  the topbar inside its fixed 114px height, folding the action labels onto two
  lines. The bar now grows instead, and action labels never wrap.

Domain layers (not reachable by an operator):

- The publication planner now refuses a GPO with a disabled computer or user
  side (`unsupported_side_status`). It had no step for the directory
  object's `flags` attribute, which `apply.ps1` sets through `GpoStatus`, so a
  plan executed as written would have published the side enabled (WI-070).
- Publication plans now register the client-side extensions their SYSVOL
  content requires, and publish a GPO's comment (WI-057, WI-058). Both gaps
  were measured against Windows, not reasoned: without the extension lists, a
  plan produced a GPO whose files were all correct and which applied nothing.
  The values come from the exporter's measured vocabulary instead of being
  restated. Content that cannot be registered accurately (an unverified GPP
  family, or a SYSVOL-only target that cannot reach a directory attribute) is
  refused rather than guessed. The planner remains unsurfaced and review-only,
  and the fix itself is not yet Windows-verified. *Later:* the batch's
  publication lane grades the plan's own extension-list claims (see Evidence).
- WI-064: the restricted-groups writer emits `S-1-5-32-544__Members` where
  Windows exports `*S-1-5-32-544__Members`; it stars every SID in the entry's
  value but not the one in its key. The parser strips a leading star, so Studio
  read its own output back into the model that produced it and the round trip
  stayed clean. Filed, not fixed: the module is bound by its verdict.
  *Later:* fixed in the batch (`6d66ce9`, then `8b1a5b4`, which stars a
  principal only when it is a SID) and closed.
- WI-065: `SystemServicesFamily.validate` reports `unparseable_service_sddl`
  when `raw_sddl` is set and `security_descriptor` is `None`. Only
  `from_template` populates that field, so a directly built model is reported
  as malformed just because it was never parsed. Validating the object-security
  lane's own candidate gives three such errors for a descriptor Windows
  accepted. As a workaround the surface parses on construction; the check
  itself is filed against the same batch. *Later:* fixed in the batch
  (`validate` parses `raw_sddl` on demand) and closed.
- WI-022 corrects the GPP Services typed model and wire shape against the
  native GPMC capture: native recovery names and omission semantics,
  `thirdFailure`, exact delay preservation, and captured extension metadata.
  It also covers the complete MS-GPPREF startup, service-action and
  failure-action vocabularies, the protocol-defined restart/program fields,
  GPMC report comparison, and a new WP-1B Services candidate. Its first Windows
  Server 2025 writer run passed, but a later manual capture invalidated the
  delay semantics. WI-024 corrected and recertified the candidate in
  clean-source run `wp1b-writer-20260730164352-5286`. Services is not
  endpoint-applied, and cannot be authored through the browser or API.
- WI-024 uses a dedicated GPMC Services recovery capture to correct the
  remaining wire assumptions: GPMC emits `RUNCMD`/`REBOOT`, millisecond
  restart-service and restart-computer delays, and boolean `append=1`, and
  omits `serviceAction` for No change. It also confirms `program`, `args`,
  `restartMessage`, `accountName` and `interact`. The corrected isolated and
  mixed Services candidates pass the full WP-1B Windows writer lane.
- WI-052: `profiles_for_actor` matched an actor against a profile *id*, so
  `effective_capabilities("p1")` returned profile `p1`'s capabilities for
  nobody in particular, while a real principal got nothing. `PublisherProfile`
  now has a `principals` field, and `profiles_for_actor` resolves against it.
  A profile granted to nobody matches nobody and raises a validation warning,
  so the configuration is visible. Domain layer; no operator-facing change.

Lab and development tooling:

- WI-063: eight `run-*-oracle.sh` lane runners are committed with CRLF line
  endings and no longer parse under `bash`. Filed, not fixed: all sixteen
  affected files are hash-bound by the WI-062 batch, so renormalizing them
  would break the live-harness binding for 19 of the 21 banked verdicts and
  cost an estate requalification. The estate owes one anyway for its 22nd
  lane. `tests/test_lane_runner_line_endings.py` holds the line until then.
  The cause is `-text` in `.gitattributes`, which pins committed bytes in both
  directions and so removes the worktree/index disagreement that WI-059's
  guard detects. `text eol=lf`, already used for every `src/gpo_studio/*.py`
  in the same file, pins LF and keeps the guard. *Later:* fixed in the batch
  (`17448d5`): the controller trees are `text eol=lf` and every runner passes
  `bash -n`. Closed.
- WI-037: a lane's staging step removed every directory under the guest's
  output root, so each run deleted the evidence a person needed to explain why
  the previous one failed. The three shared-root drivers now keep the newest
  five run directories and sweep the guest's `scripts` directory, which staging
  owns. Keeping run directories made the "newest output directory" fallback
  unsafe in a new way: it could pull the *previous* run's observation, and the
  finalizer would grade it as the current one. The fallback now requires an
  observation-bearing directory created after a guest-side clock reading taken
  just before the observation, and refuses anything but exactly one match. For
  the same reason, the endpoint lane's `verify` phase no longer writes to a
  fixed path; it is per-invocation. Lab tooling; no operator-facing change.
  **Closed**: fourteen runs re-certified the affected lanes on 2026-09-06, and
  the retention was confirmed on the guests rather than inferred: the previous
  run's observation survived where the old staging would have deleted it. The
  first run also found a defect in the fix: `$(verify_endpoint)` ran the phase
  in a subshell, so its idempotency flag never reached the driver's shell, and
  the EXIT trap repeated the whole post-teardown verification.
- WI-025 (code half): the WP-1B and endpoint lane verdicts named the candidate
  artifacts they were graded against but hashed none of them, so nobody could
  re-check the comparison. Both finalizers now record a SHA-256 for every file
  under `--candidate-root`. They refuse a run whose candidate root is missing a
  required artifact, instead of recording a shorter block that still looks
  complete. WP-6B's implementation is the model. **Closed** by
  `wp1b-writer-20260906183513-1195` (7/7, fifteen candidate hashes) and
  `endpoint-observe-20260906185837-7523`, both committed with their blocks
  populated.
- WI-053: from 2026-08-03 the endpoint lane's only committed certification
  escaped every evidence gate, because its filename matched neither prefix the
  coverage guard globs for. When WI-037 changed two files it binds, every RSOP
  verdict went red while this one stayed silent. The re-certification is
  promoted under a covered name and mapped in `LANE_VERDICTS`. The guard now
  requires every JSON file in an evidence directory to be either verdict-named
  or listed with a reason in `NON_VERDICT_EVIDENCE_FILES`, so an unusually
  named verdict can no longer escape. A control test fails if the pattern stops
  matching the endpoint certification. Lab tooling; no operator-facing change.
- WI-045: a committed lane verdict binds its harness files by SHA-256, but no
  test checked that those hashes still matched the tree. The RSOP verdicts had
  twice been re-run for exactly this reason, and both times a person caught it,
  not CI. Live certifications are now hash-checked against the working tree.
  Retained history is listed in `RETIRED_VERDICTS`, and a control test fails if
  a retired verdict still matches, so the exemption list cannot be used to
  silence the check. Lab tooling; no operator-facing change.
- Windows frontend formatting checks now accept checkout line endings without
  reformatting the source. (The NetSecurity measurement from the same entry is
  under Evidence.)
- `jsonschema` was declared only as a uv dependency group, so `uv sync`
  installed it locally but CI's `pip install -e '.[dev]'` did not, and the
  Plan 033 WP-1A fixture tests failed on CI with `ModuleNotFoundError`. It is
  now in the `dev` extra and the duplicate group is gone, leaving one source of
  truth for development dependencies.

### Security

- *New in this draft:* the release workflow no longer publishes on a stale
  approval. It grepped `docs/release-evidence.md`, the 1.0.0 manifest, for
  the approval string, so any later tag would have passed on 1.0.0's approval.
  `scripts/check_release_manifest.py` now takes status and version from
  `docs/release-evidence-report-<version>.json`, held to a strict schema
  (exact keys and types, no duplicate keys): `version` must equal the package
  version and `status` must be `approved` for a final tag or `candidate` for
  an RC (`draft` never releases). The Markdown manifest is held to a lexical
  contract on its bytes, not rendered: printable ASCII and LF only, no `<`, no
  character references, no code fences or deep indentation, plain unspliced
  links, a fixed header whose line 5 must be the status line for the JSON
  status, and the word "status" followed by a colon nowhere else. Four review
  rounds had found ways for CommonMark rendering to show a reader something
  other than what a parser checked (entities, HTML blocks, quoted fences,
  Unicode folding, linked headings); the contract forbids the constructs
  instead of modelling the renderer, and every one of those probes is kept as
  a test. Approving a release is two edits: the JSON `status` (and `version`)
  and line 5 of the manifest.
  `__version__` must be bound exactly once and match what Hatchling reads, and
  the built wheel's and sdist's metadata versions must equal the approved
  version before anything is attested. It fails closed on anything else.
  Before publishing, the workflow
  also requires every `ci.yml` job (including `test-windows`), the identifier
  gate and the existing verify job to pass on the tagged commit, and the commit
  to be on `main`. Immediately before `gh release create`, it requires the
  remote tag to still peel to the commit the run built, because
  `--verify-tag` only checks that a tag of that name exists.
  `tests/test_release_manifest_gate.py` pins each part, including every
  bypass the reviews demonstrated against earlier versions of the gate.
- The static safety gate now applies forbidden-import categories to the **web
  process** (the modules reachable from `api.py`, directly or transitively),
  which is what the charter constrains, instead of to the whole `src/`
  directory. Lab or release tooling that never runs in a request path may be
  granted a category exemption. `scripts/check_safety.py` fails if an exempt
  module ever becomes reachable from `api.py`, so an exemption cannot quietly
  widen into a charter breach.
- `oracle_evidence.py` imports `subprocess` to run `git` for evidence
  provenance, which broke the static safety gate's blanket ban and the
  `static-safety` CI job. The module is lab tooling and the web process cannot
  reach it, so it now has a reachability-enforced exemption instead of the ban
  being weakened.
- The fdeploy reader's input bounds (under Fixed): an over-long `Flags` value
  no longer produces a server fault, and validation is no longer quadratic in
  a document within the size cap.
- The retired SSH launcher passed a lab credential as a `schtasks /RP`
  argument (under Removed). Lab tooling only.
- WI-051: the publisher's separation-of-duties control existed only on the
  path that builds an approval through `approve_request`. The gates took no
  principal at all, `decided_by` was hardcoded empty, and a directly built
  self-approved request (the shape persistence produces when it rehydrates
  state) passed with zero validation issues. The gates now take a required
  `actor`, fill `decided_by` from it, and run a `separation_of_duties_gate`
  that re-derives the requester/approver comparison through
  `hosting.can_self_approve`. It refuses a self-approved request, a publishing
  actor who approved the request, or a missing principal.
  `ApprovalRequest.validate()` carries the same check structurally, and
  `_approval_gate`'s four previously untested refusal branches now have tests.
  Domain layer; no operator-facing change. `publisher.py` is out of scope for
  1.x.

### Evidence

Windows evidence and the lab tooling that produces it. None of these entries
is a capability by itself; see Added for what an operator can reach.

- *New in this draft:* **Banked the release 1.1.0 requalification batch**
  ([batch note](docs/plan-033/release110-batch.md)). All 26 lanes passed on
  one frozen commit, `de9736e` (batch 2, the chunked psdirect transport and
  the WI-072/WI-073/WI-079 GPP fixes), driven by
  `scripts/plan-033/run-requal-batch.sh` under its per-lane watchdog: WP-0
  plus 25 lane verdicts, the lifecycle, report-parity, firewall and fdeploy
  lanes included for the first time in a batch. No lane timed out, was
  cancelled or lost containment. Every previously live verdict is retired;
  `PENDING_REQUALIFICATION` is empty. Report parity covers 30 backups (the
  three GPP Registry captures included) and accepts no Studio defect; the
  firewall write leg carries the native `B05566AC` registration; the post-batch
  directory check is clean. An attempt at `2f21e7c` (25 of 26; report parity
  hung on the transport defect) was superseded unbanked and is recorded in the
  manifest. The packs were banked with the new
  `scripts/plan-033/bank-requal-batch.py`, and `tests/test_release110_batch.py`
  reads run ids from the schema 2 manifest. `environment-spec.md`,
  `platforms.json`, the capability matrix (GPP Registry and GPMC backup export
  rows now certified), the results headers, the bound-source cost table and the
  lifecycle and firewall surfaces' citations name the new runs. WI-072,
  WI-073, WI-075, WI-078 and WI-079 close.
- *New in this draft (batch 2):* **Batch 2 changed files that live lane
  verdicts bind** (`gpp.py`, `export.py`, `publication.py`, `xml_safety.py`,
  `object_security.py`, `report_parity.py`, the new `deterministic_zip.py`
  and several builders and finalizers), so the publication, scripts-metadata,
  WP-1B, WP-2, report-parity, firewall, object-security, fdeploy and endpoint
  verdicts banked before it no longer bind the shipping code. The single
  1.1.0 requalification of every lane at one commit covers them; until it
  runs, those lanes cite pre-batch-2 evidence (WI-075). *Later:* it ran and
  passed every lane at `de9736e` (see the entry above).
- *New in this draft (batch 2):* **WP-1B lane:** new `gppregistry-both` candidate and GPP Registry items in
  `mixed-all`; GPMC report markers are namespace-qualified, since Registry.pol
  and GPP Registry both render as `RegistrySettings`. Live verdicts for the
  wp1b, publication, scripts-metadata and object-security lanes no longer bind
  the shipping files and need the batch-2 run.
- *New in this draft (batch 2):* **Report parity covers GPP Registry.** The report-parity corpus gains the
  three GPP Registry captures (30 cases); `report_parity` inventories Windows'
  GPP Registry extension under `Windows/Registry:RegistrySettings`, since the
  report gives it Registry.pol's local name. Studio's import matches Windows'
  report for all 39 captured items.
- *New in this draft (batch 2):* **`run-requal-batch.sh`** drives the lifecycle, report-parity, firewall and
  fdeploy lanes too (member server; the client-rebooting group-deny lane stays
  last), and a test holds the lane table equal to the repository's runners.

- Re-banked the fdeploy lane: `fd-20261008121347-3151`, 29/29 on clean commit
  `df713ef`, now holds `docs/plan-033/wp4-evidence/fdeploy/` in place of
  `fd-20261008102559-9746` (below), with byte-identical candidate bytes. Two
  harness defects were fixed first, and both edited bound files:
  - The candidate builder sorted `Path` objects, which compare
    case-insensitively on Windows, so the archive's member order depended on
    the controller's OS (PR #98's Windows CI). It now sorts on ordinal path
    components, as the report-parity builder does, with Windows-ordered
    regression tests. The evidence tests hold the archive's exact hash on
    POSIX and its members (names, order, timestamps, compression, attributes,
    bytes) everywhere.
  - The driver's guest root was `C:\gpo-studio\fd\$STAMP` inside double
    quotes, so bash expanded it to the constant `C:\gpo-studio\fd$STAMP`. Every
    run after the first collided with it, and the estate re-run refused twice
    with "run root exists". The driver now writes `fd\\$STAMP`, and a test
    evaluates its path lines in bash and requires a fresh root per run.
  The old pack is in git history and its tag is preserved. See
  [the results' history](docs/plan-033/fdeploy-results.md#history).

- Banked the fdeploy lane's certifying run, `fd-20261008102559-9746`: 29/29 on
  clean commit `6b76fad`, on the estate member server (WS2025 26100, Windows
  PowerShell 5.1). It ran four cases: R3's GPMC-written `fdeploy1.ini` verbatim
  (`Flags=1021`) and three builder-written `Flags`-only variants (1020, 1023,
  3069). For each, `Import-GPO` put the candidate's exact bytes in SYSVOL,
  `Backup-GPO` re-exported `fdeploy1.ini` and `fdeploy.ini` byte for byte, and
  Studio's `read_backup` over Windows' own backup agreed with a fresh
  `Get-GPOReport` row for row (folder, principal SID, destination). Each report
  named the GPO the run created. `fdeploy.py` is now `lane-backed and surfaced`
  for its read target, Plan 034's `fdeploy` row reaches `yes` for reading, and
  Plan 027 leaves the unsurfaced domain-layer set. **Not certified:** decoding
  `Flags`, other `Flags` values, multi-folder and multi-principal documents,
  and any writer, which stays deferred under WI-066 until R12. Windows' option
  rendering per `Flags` is recorded as data for WI-066 and matches the
  2026-10-08 probe; it is not asserted. The pack is under
  `docs/plan-033/wp4-evidence/fdeploy/`, and
  `tests/test_fdeploy_lane_evidence.py` re-runs the shipping finalizer over it
  and rebuilds the candidate byte for byte. See
  [the results](docs/plan-033/fdeploy-results.md). An exploratory pass at
  `379e59b` binds pre-review source and is recorded as history only.
  `bound-source-cost.md` is regenerated: `model.py`, `gpp.py` and `xml_safety.py`
  now cost three lanes, and `backup.py`, `backup_inventory.py`, `safe_io.py`,
  `fdeploy.py` and `fdeploy_parity.py` cost one.
  With the report-parity, firewall and lifecycle lanes also live (merged from
  `main`), the regenerated table prices `model.py`, `gpp.py` and `xml_safety.py`
  at five lanes, `registry_pol.py` at four, `backup.py` at three and
  `backup_inventory.py` at two.

- Banked the report-parity lane's certifying run,
  `report-parity-20261008104512-7480`: 25/25 checks on LabMS01 (Windows Server
  2025, build 26100, PowerShell 5.1) at clean commit `a1c280b`, over all 27
  Windows-produced corpus backups plus one GPO authored on the guest. The lane
  imports each backup with `Import-GPO`, takes a fresh `Get-GPOReport
  -ReportType Xml`, and compares it by side and report family with Studio's
  typed import of the same backup. With this verdict, `backup` and `report`
  reach `yes` in Plan 034 for the families Studio models and the corpus
  exercises: registry (`REG_SZ`/`REG_DWORD`), Drive Maps, Environment, Files,
  Folders, Ini Files, Local Users and Groups, Printers, Scheduled Tasks,
  Services and Shortcuts (Power Options is not certified: its only case passes
  on the WI-072 divergence). The surfaces are the existing
  import and plain-text report. That closes Plan 034 WP-2 items 2 and 3.
  - The named exclusions are ADMX `<Policy>` rendering, Scripts, scope (links,
    security filtering, WMI filters) and preference properties beyond the
    action. Seven Studio families have no capture and are not claimed.
  - WI-072 (the power plan is dropped on write) and WI-073 (scheduled and
    immediate task interleaving is lost on write) stay open. The verdict
    accepts them only as pinned known divergences, on the three cases that
    show them. *Later:* both are fixed in `gpp.py` (see Fixed) and wait on
    the requalification run. Their allowances are removed from
    `report_parity.KNOWN_DIVERGENCES`, the candidate builder pins the three
    cases in `MUST_AGREE_CASE_IDS`, and the finalizer's new
    `fixed_work_item_cases_agree_exactly` check requires each to be in the run
    and equal to Windows' fresh report with no divergence of any kind. A
    regression now fails the lane. The banked pack no longer rebuilds under
    the changed builder until the requalification replaces it.
  - The pack replaces the first banked pass, which was at `1a31feb`. PR #94's
    Windows CI showed the candidate builder sorted `Path` objects, whose
    order is case-insensitive on Windows, so the archive's bytes depended on
    the controller's OS. The builder now sorts on ordinal path components, a
    Windows-ordered regression test covers it, and the guest runner's by-ID
    lookup records its failure instead of an empty `catch`. Both files are
    bound, so the lane was re-run at `a1c280b`, with an identical candidate
    and comparison.
  - The pack is in the manifest form, under
    `docs/plan-033/wp2-evidence/report-parity/`. It is registered in
    `LANE_VERDICTS`, the environment spec and `platforms.json`, and
    `tests/test_report_parity_evidence.py` rehashes it, rebuilds its candidate
    and re-derives every comparison. The five newly bound modules are pinned
    `eol=lf`. The cost table is regenerated: editing `gpp.py`, `model.py`,
    `registry_pol.py` or `xml_safety.py` now costs three lanes. See
    [the results](docs/plan-033/report-parity-results.md).

- *New in this draft:* a workspace written by the 1.0.0 release itself.
  `scripts/generate_release_workspace_fixture.py` drives the `v1.0.0` tag,
  installed from its own lockfile, through its HTTP API. It writes two synthetic
  GPOs with 19 revisions and a 1.0.0-written backup, records what 1.0.0
  served, and observes the rollback (1.0.0 refusing the upgraded workspace and
  a backup of it, then serving the restored pre-upgrade backup).
  `tests/test_release_upgrade_from_1_0_0.py` holds the upgrade lossless,
  re-runs 1.0.0's schema guard from the tag, and pins the three output
  differences listed under "Upgrading from 1.0.0". Exported `Registry.pol`
  files are decoded by a PReg reader in the test itself and compared with the
  bytes 1.0.0 exported and the settings 1.0.0 served, so a serializer that
  drops a record type fails (checked by mutation). The provenance pins the
  writer: the `v1.0.0` commit, its `uv.lock` digest and a digest of the
  installed package files, which the test re-derives from the tag. It also
  records 1.0.0's `workspace check` invalidating a backup copy (WI-074).
  The upgrade rehearsal
  (`scripts/rehearse_upgrade_rollback.py`), which CI and the release run
  against the built wheel, now covers this workspace as well as the synthetic
  schema-0 one. The earlier fixtures were written by this repository's own SQL.
- Banked the Plan 034 requalification batch
  ([batch note](docs/plan-033/plan034-batch.md)). All 22 lanes passed on frozen
  commit `263f196`, driven by `scripts/plan-033/run-requal-batch.sh`, on an
  estate running at real time: WP-0 plus 21 schema-version-2 lane verdicts,
  including the computer group-deny lane, which last passed in the WI-059
  batch. `8b1a5b4` changed `object_security.py` after the freeze (a
  `[Group Membership]` principal is starred only when it is a SID), so the
  object-security lane was re-run at `1fb3f56`
  (`object-security-20261008082348-9729`, 20/20), and that successor is the
  live verdict. The 20 WI-062 verdicts, the pending WI-059 group-deny verdict
  and the batch's own superseded object-security verdict are retired, with
  their packs and tags unchanged. `PENDING_REQUALIFICATION` is empty.
  `tests/test_plan034_batch.py` pins the manifest, every banked hash, the
  digests at each commit, the live set, the cleanup capture, and regrades of
  the RSoP, endpoint and WP-1B records by today's finalizers.
  `environment-spec.md`, `platforms.json`, the capability matrix and the
  bound-source cost table now cite these runs.
  - Closed WI-063 (the LF-renormalized runners are re-earned), WI-064 (Windows
    re-exported the star-SID `[Group Membership]` rows that
    `RestrictedGroupsFamily` builds, including the predicted `__Memberof`),
    WI-065, WI-068, WI-069 and WI-070. WI-069 closed because the lane was
    unblocked by re-baselining at real time. The mechanism of the old DNS
    deletion was never identified.
  - Restricted groups are lane-certified but still not surfaced. The
    object-security endpoint's `restricted_groups_not_surfaced` message
    predates the certifying run.
- Proposed, not built: extending the Scripts metadata lane to a user-side
  logon script and a computer-side shutdown script (WI-071). The user-side
  Scripts extension pair Studio writes has never been measured, and the
  round trip cannot detect a wrong one.
- WI-069 now tracks the estate repair that blocks the 22nd lane. It previously
  had no number, while three open items (WI-063, WI-064, WI-065) waited on the
  estate run it blocks, and the only account of the failure was one paragraph
  in the WI-062 batch note that four other documents pointed to.
  `docs/plan-033/estate-clock-dns-repair.md` adds the capture plan, and
  `scripts/plan-033/collect-dc-clock-dns.ps1` adds a three-phase, read-only
  collector. It reads LDAP node state and replication metadata rather than
  resolver answers, and the scavenging and aging settings as configured rather
  than as remembered. Neither has been run: this host has no lab credential
  capability provisioned, so the transport is unavailable from it.
  The item also records a step that was skipped. The conclusion that the
  DC-locator records were *deleted* came from a resolver. Through a resolver,
  absent, tombstoned and present-but-unserved records look the same; over LDAP
  they are three different findings, and the third is plausible on exactly the
  machine whose clock has just jumped. The account may still be right, but the
  step from symptom to mechanism was never taken, and it is one read.
  *Later:* closed by the batch, which unblocked the lane by re-baselining at
  real time.
- WI-067: the R3 fixture's provenance called
  `{FDD39AD0-238F-46AF-ADB4-6C85480369C7}` the Folder Redirection CSE GUID. That
  GUID keys the redirected *folder*. The CSE GUID is
  `{25537BA6-77A8-11D2-9B6C-0000F8080861}`, which R6's census counted and which
  appears in neither captured file. The repository contradicted itself two
  paragraphs apart, and nothing caught it because no code read the file. The
  constant is renamed, and the provenance record carries a dated correction
  instead of a silent rewrite. Closed after checking the upstream R3 envelope,
  which never carried the mistake: it records the CSE GUID in
  `ad.gPCUserExtensionNames` and the folder GUID in `fdeploy.entries.1.key`. The
  false label came only from this repository's derived provenance note.
- WI-066: R3 answered one of the four questions it was designed to answer. The
  request authors two folders: Documents in Basic mode with three non-default
  options, and Pictures in Advanced mode with two groups at defaults, the
  second there so the first could be read against it. Step 4 was never
  authored, so the banked capture is one folder, one principal and one
  `Flags=1021`: nine bits set against four modelled booleans, with no control.
  Nothing was misrecorded. The result was entered against the scope-changing
  question R3 was asked, which it answers decisively, and the binding table had
  no column for what a capture did not settle. R3's row now records that. R12
  re-requests step 4 and adds a third folder that makes one flag bit derivable
  rather than only constrained; it is a console session, not a lane. Checked
  while filing: R3 is the only request whose claim is narrower than its body.
- Plan 034 WP-4: the Folder Redirection scope brief
  (`docs/scope-brief-2026-09-11-folder-redirection.md`). It is not a ruling:
  the plan makes this a decision, and the brief gathers what the decision needs
  without making it. It used no estate time. It ran the offline discriminator
  the survey asked for and no one had run: an advanced policy with three group
  rules produces one registry tuple, carrying neither the group SIDs nor the
  four option flags that R3 shows Windows encoding as `Flags=1021`. So
  `to_registry_settings()` is not a Folder Redirection writer at any level of
  detail. The CSE appears in 0 of the same 26 production GPOs that ruled
  Software Installation out, but the two costs that made *that* ruling easy
  are absent here: this capture is already taken, and `fdeploy1.ini` is a
  458-byte UTF-16LE INI whose oracle is `Backup-GPO`, not an undocumented
  binary. Recommendation: read target, with writing deferred rather than
  refused. `tests/test_folder_redirection_scope.py` pins the code facts and
  fails when a ruling is acted on.
- Added `docs/plan-033/bound-source-cost.md`, which lists for each file the
  lanes that must be re-run if it is edited.
  `scripts/plan-033/report-bound-source-cost.py` generates it from the live
  verdicts, and `tests/test_bound_source_cost.py` guards it. The information
  was always complete but spread across twenty-one packs, so pricing a change
  meant opening all of them. `oracle_evidence.py` and `psdirect.ps1` cost the
  whole estate; `model.py`, `export.py` and `validation.py` cost two lanes
  each; the rest of `src/gpo_studio/` costs nothing, which describes lane
  coverage, not code quality. AGENTS.md links it so the next session reads it
  before editing.
- WI-062: evidence packs no longer bank byte copies of controller-side bound
  source. Verdicts (schema version 2) record `(commit, path, sha256)` for every
  bound file. `harness_matches_source` covers the half deployed to the guest,
  WP-0's manifest lists the orchestrator files in `source.bound`, and
  `test_committed_evidence.py` re-derives recorded digests from git at each
  verdict's own commit. Historical packs and tags are untouched. The
  requalification batch banked WP-0 plus 20 schema-version-2 verdicts on one
  frozen harness. The computer group-deny lane is pending the estate repair
  its batch note describes. See [the decision](docs/plan-033/bound-source-manifest.md)
  and [the batch](docs/plan-033/wi062-batch.md). *Later:* the Plan 034 batch
  passed the group-deny lane and retired these 20 verdicts in favour of its
  own.
- WI-059: every oracle finalizer now refuses working-tree/index/HEAD byte drift
  before writing verdicts or tags, including with `--no-tag`. All 21 live lane
  verdicts and WP-0 were requalified in one frozen estate batch. Exact captures
  and historical records are preserved. See
  [the completed batch](docs/plan-033/wi059-harness-batch.md).
- WI-028: measured `SearchedSOM` persistence in the client's RSoP WMI namespace
  after a verified OU deletion and policy refresh. The investigation documents
  a scoped-use strategy for future SOM assertions; current lanes do not grade
  these historical rows. See
  [the findings](docs/plan-033/wi028-searched-som-investigation.md).
- Plan 034: the unsurfaced policy-family serializers now feed a repeatable
  Windows WP-3 lane. Native validation found and corrected `AuditDSAccess` and
  rejected two speculative Kerberos fields, which were removed. Final member
  and DC runs each pass 21 checks, with retained raw evidence and source
  bindings. The lane records the DC role and retains failure evidence. See
  [the results](docs/plan-033/wp3-policy-family-results.md).
- Plan 034: the object-security serializer lane passed 19/19 checks on the
  clean member server, retaining the exact six registry/file rows and three
  service rows in the evidence pack. ACL application and content suitability
  remain out of scope under WI-055. See
  [the results](docs/plan-033/object-security-results.md).
- Plan 034: the Scripts metadata lane passed 21/21 checks for Import-GPO,
  Get-GPOReport and Backup-GPO rebackup. Script payload execution remains out
  of scope.
- Plan 034: publication-plan completeness has a repeatable Windows lane, which
  passed 21/21 on the clean member server. It compares the plan's own account
  of what it would write with the SYSVOL tree and extension-list attributes
  Windows produces from the same content. That comparison found WI-057, and
  only it could have: a round trip never asks what a third party would have
  had to write. The lane measures the plan, not a publication. Nothing writes
  to SYSVOL or AD, and the operation allowlist stays empty. See
  [the results](docs/plan-033/publication-completeness-results.md).
  *Later:* the allowlist was deleted with the script generator, and the
  batch's run of this lane grades 21 checks, including `GPT.INI` and a real
  owned GUID.
- Artifact-store scope is now explicit: the EICAR marker and secret heuristics
  are local checks; executable publication requires a future verified signer
  path; duplicate arrivals append provenance without replacing the canonical
  row; and publication eligibility remains read-only. See
  [the scope ruling](docs/plan-033/artifact-store-scope.md). *Later:*
  `artifact_store.py` is deleted (see Removed).
- NetSecurity availability and isolated firewall GPO authoring and readback
  were measured; the network model remains unverified. (Split from the Windows
  formatting entry under Fixed.)
- WI-054: every nesting row in the corpus put the disposable group in the
  USER's token, so the model's answer about a group in a CLIENT's machine token
  was unit-tested but never run on the estate, although the API accepted that
  input. A new computer-scope scenario authors an APPLY deny whose only
  identity is a group the client's computer account joins. The lane reboots
  the client so the machine token carries the membership (a machine token is
  minted at boot, and nothing lighter refreshes it). The observation half
  confirms the membership from the machine token and, independently, from the
  directory, and the computer finalizer gained the user lane's token gate.
  Measured the same day: the model said blocked and Windows agreed
  (`rsop-observe-20260906221638-4687`), and twelve other runs from the same
  tree re-certified the lanes the change retired. The first run found that the
  reboot makes boot-time policy processing a second applier; the observe half
  now records this as `boot_applied_values` instead of misreading it as
  unattributable residue. The same change removed the dead
  `reaches_reasoned_cell` disclosure that WI-049's closure left in the builder.
  Lab tooling; no operator-facing change.
- Plan 033: lane verdicts now check what they claim to check. An adversarial
  review round (three reviewers, hazard-scoped, one cross-lineage) found that
  WP-2 and WP-3 graded themselves against the copy of `expected.json` the guest
  returned instead of the candidate the controller built. That also made two of
  WP-2's named checks structurally unfalsifiable. It also found that WP-1B, the
  lane that qualified the estate, had no environment gate at all. Both lanes now
  take `--candidate-root` and have a `candidate_delivered_intact` check, and
  WP-1B gates on `FROZEN_ENVIRONMENT` like the others. Every run now owns a
  private directory tree on the guest, so concurrent runs on one guest can no
  longer pick up each other's evidence. `cleanup_succeeded` means the GPO was
  removed, or an independent enumeration shows nothing by that name. Orphaned
  GPOs are reaped and the reap is recorded. Two fail-open defaults
  (`native_shape_findings` absent, `check_git=False`) are closed.
- Plan 033: **every evidence lane now runs against the disposable evidence
  estate over PowerShell Direct, and the SSH transport is retired.** WP-0, WP-2
  and WP-3 were ported alongside WP-1B and the endpoint lane, each with its own
  qualification run on the estate (recorded in the Qualified environments table
  in `docs/plan-033/environment-spec.md`, with committed verdicts and evidence
  tags). WP-0's certification, whose commit had been orphaned by a squash
  merge, is re-earned on a commit that resolves. Lane finalizers now check the
  recorded environment against `FROZEN_ENVIRONMENT` instead of private copies
  of the profile. WP-2 had not been checking its environment at all, and WP-3's
  copy had drifted into pinning an exact PowerShell servicing revision and
  gating on an LGPO hash that the 2026-07-29 re-freeze had already removed.
- Plan 033 WP-0: the Windows external-oracle evidence contract,
  owning-boundary matrix, frozen environment spec, conservative XML normalizer
  v1, fixture recipe schema, and the two-phase harness. `run-evidence.ps1`
  captures raw evidence on the domain-joined host, and
  `finalize_oracle_run.py` is the single authority for source provenance,
  normalization and comparison binding. Certified pass on a clean tree with a
  full integrity pack: harness scripts, recipe and orchestrator are hashed
  input artifacts bound to the recorded commit, every artifact rehashes intact,
  and an independent LDAPS re-query confirms cleanup.
- Plan 033 WP-1A: native-origin GPMC corpus, authoring guide, and canary
  fixtures authored in GPMC itself.
- Plan 033 remediation scenario corpus: **validation infrastructure, not a
  capability**. Thirteen provenance-graded scenarios across four families
  (gpp-services for WI-022, security-template areas for Plan 025/WP-3,
  rsop-topology for Plan 029/WP-6, ilt-os for WI-023) under
  `tests/fixtures/scenarios/`; a machine-readable test-platform registry
  (`platforms.json`) extending `docs/plan-033/environment-spec.md`; and the
  `remediation_corpus.py` loader, which enforces referential integrity,
  readiness honesty (no `ready` claim on an unqualified platform) and
  sha256-pinned native-capture anchors. Executable WI-022 characterization
  probes pin today's parse/writer divergence and flip when the fix lands. The
  corpus records expected Windows behaviour for the Plans 025–032 remediation
  programme. Nothing in it is oracle-executed yet, and no capability claim
  changes.
- Plan 021 WP-1: authoritative GPMC capability inventory
  (`docs/plan-021/capability-inventory.md`). A versioned, pre-gate matrix of
  GPMC lifecycle, scope and report surfaces; principal-bearing fields; every
  in-box CSE and editor by GUID, side, storage, OS, deprecation and management
  API; the GPP item/action/option space; and the complete ILT predicate AST.
  Each row is classified (`verified-rw` … `unknown`) and linked to Microsoft
  documentation and lab evidence. No row is `verified-rw` without Windows and
  endpoint evidence.
- Plan 021 WP-4: reference estates and evidence schema
  (`docs/plan-021/reference-estates-and-evidence.md`): the provisional Windows
  target matrix (WS2019/2022/2025 + Win11; Win10 behind an ESU decision), the
  ADMX/ADML licensing classification rules, the redaction contract enforced by
  the identifier gate, the versioned evidence-pack JSON schema, and the
  negative/downgrade fixture requirements.
- Plan 021 WP-4: public matrix generator (`scripts/generate_public_matrix.py`
  and `src/gpo_studio/evidence.py`). It loads versioned evidence packs, refuses
  to derive claims from packs whose redaction or licensing gates are not
  satisfied, and derives a public capability matrix containing only claims
  backed by passing evidence. A `verified-rw` claim requires both a passing
  Windows-side record and a passing endpoint record.

## [1.0.0] - 2026-07-18

### Changed

- Promoted `1.0.0rc3` after the complete Windows NVDA acceptance journey
  passed in Edge and the Firefox ESR smoke journey passed. This promotion
  contains no application-behavior changes from the tested candidate.

## [1.0.0-rc.3] - 2026-07-18

### Fixed

- JavaScript modules are now served as `text/javascript` on Windows regardless
  of the machine's MIME registry. Previously, a `text/plain` association made
  browsers reject every module under the application's `nosniff` policy,
  leaving all browser controls inert.
- Added a GPO Studio favicon.

## [1.0.0-rc.2] - 2026-07-18

### Fixed

- CycloneDX release SBOMs now receive a deterministic UUID serial number before
  GitHub attestation. The reproducible generator intentionally omitted this
  optional field, but GitHub's attestation parser requires it. The workflow now
  uses the current pinned `actions/attest` interface directly.

## [1.0.0-rc.1] - 2026-07-18

### Added

#### Capability contract and canonical model (Plan 015)

- Capability contract with explicit states: `supported`, `preview`,
  `preserved`, `blocked`, and `out of scope`. Replaces the stale roadmap
  table. Per-action fidelity documented for authoring, import, export,
  PowerShell plan, diff, and Windows-lab verification.
- Split semantic hashes: `policy_semantic_sha256` covers every field that
  changes effective policy or publication intent; `review_model_sha256`
  additionally covers review-relevant annotations and preserved CSE
  metadata.
- Exhaustive validation with `typing.assert_never()` dispatch on closed
  variant sets, so adding a new enum or kind fails the type check at every
  unhandled site.
- Complete two-way and three-way diff for GPP collections, ILT predicates,
  CSE metadata, side enablement, domain, and GPO-level metadata. Stable
  identities for GPP elements; link conflict detection in three-way diff.
- Golden vectors for canonical digests so other implementations can
  reproduce them.
- Stable issue codes and field paths returned by validation for browser
  field mapping.
- `ready` transition guarded: a GPO cannot enter it with validation errors,
  unknown CSE content, unresolved conflicts, or unsupported preview content.

#### GPP end-to-end authoring (Plan 016)

- GPP Groups and Registry authoring API with optimistic-concurrency CRUD
  endpoints under `/api/gpos/{guid}/preferences/...`.
- Browser editors for GPP Groups (action, members, remove-all flags,
  description) and GPP Registry (action, key, typed values) with inline
  sub-editors.
- ILT predicate editor supporting six types: `ou`, `group`, `registry`,
  `ip_range`, `environment`, `wmi_query`, with negation and AND combination.
- Plain-language ILT preview beside the serialized structure.
- Unknown XML attributes, unknown child elements, and unknown ILT predicate
  types preserved losslessly through import/export round-trips.
- Typed Pydantic request/response models and OpenAPI examples for every
  supported GPP action and value type.

#### Windows and GPMC interoperability (Plan 017)

- Versioned synthetic compatibility corpus covering all registry types,
  deletes, side state, link and security shapes, WMI, GPP Groups and
  Registry actions, all ILT predicates, Unicode, unknown content, malformed
  inputs, cpassword, migration tables, and partial or corrupt backups.
- Import conformance tests comparing normalized Studio model to expected
  semantics field by field.
- PowerShell plan validator with closed allowlist checking structure,
  assignment ordering, command shapes, pipes, semicolons, backticks,
  dangerous aliases, and case-insensitive cmdlet spelling.
- Three adversarial review rounds fixing real bypasses in multiline quoted
  strings, case-insensitive PowerShell names and aliases, and user-scope
  GPP coverage.
- WP-5 Windows lab validation on Windows Server 2025: all 12
  conformance-corpus fixtures exercised through their PowerShell plans on a
  domain controller (all six registry types, deletes, side enablement,
  idempotency, `Backup-GPO`). Sanitized, hash-pinned evidence report
  (`docs/release-evidence-report.json` +
  `scripts/generate_evidence_report.py`), capability-matrix Win-lab column
  promotions, and a root-cause diagnosis of the `Import-GPO` `Backup.xml`
  v2.0 incompatibility recorded as a known issue.

#### Workspace and runtime hardening (Plan 018)

- Versioned workspace schema with forward-only, transactional migrations,
  preflight checks, and backup before any destructive migration. Unknown
  newer schemas refused with an actionable error.
- CLI commands: `workspace check` (quick and full integrity), `workspace
  backup` (SQLite online backup API with WAL checkpoint), and `workspace
  restore` (crash-safe with rollback, retains old database).
- Atomic metadata sidecar recording schema version, application version,
  GPO count, revision count, and source/backup SHA-256 digests.
- Startup quick-check with health degradation and `/api/workspace/integrity`
  endpoint.
- Bounded untrusted input: total bytes, file count, directory depth, XML
  element count, text/attribute length, GPO count, PReg record count,
  `REG_MULTI_SZ` item count, and per-file size limits on every import path.
- Race-resistant file handling on POSIX (openat) and Windows (NtOpenFile
  with RootDirectory walk and identity verification).
- Loopback binding enforced by default. Non-loopback bind requires
  `GPO_STUDIO_UNSAFE_BIND=1`.
- Host header and mutation Origin validation to reduce DNS-rebinding abuse.
- Content-Security-Policy, `X-Content-Type-Options`, conservative referrer
  policy, and cache controls on API and artifact responses.
- Structured local logs with request ID, operation, GPO GUID, revision,
  outcome, and duration. Policy values, SIDs, paths, and request bodies
  are never logged.
- Numeric resource limits: `REG_DWORD` [0, 2^32-1], `REG_QWORD`
  [0, 2^64-1], `REG_MULTI_SZ` max 10,000 items, PReg max 100,000 records,
  backup max 100 GPOs, migration table max 10 MiB, per-file max 50 MiB,
  total backup max 500 MiB, max 10,000 filesystem entries, max depth 100,
  request body max 10 MiB.

#### Browser quality and accessibility (Plan 019)

- Browser test foundation: pinned ESLint, Prettier, Vitest, Playwright,
  and axe-core toolchain. CI exercises the packaged CLI against a temporary
  real SQLite workspace in Chromium, with a Firefox smoke baseline and
  failure traces/screenshots.
- Concurrency conflict recovery UX: 409 responses retain unsaved form
  values, fetch the current revision, and offer a structured compare/reapply
  flow. No destructive change is silently retried.
- Persistent error alerts for offline, server-down, and partial-import
  states instead of relying on transient toast messages.
- Export review boundary showing both semantic digests, validation state,
  preserved content, and artifact capability before download.
- Revision-to-revision diff selection rendering all Plan 015 diff kinds.
- Deterministic inert text policy report suitable for code review or change
  tickets, with no active content.
- Archived import detection: edit actions disabled, explicit fork path
  required before editing.
- GPP clone, atomic reorder, per-item revision restore, and destructive
  confirmation dialogs.
- Accessibility: semantic keyboard tabs, labelled and focus-managed
  dialogs, field-error relationships, persistent announcements, visible
  focus, target sizing, forced-colors and reduced-motion handling, narrow
  reflow. Automated axe checks report no serious or critical violations in
  covered primary states.
- Seven end-to-end release journeys automated in CI: raw registry
  author-review-export, ADMX configuration, estate fork and three-way
  conflict, GPMC backup import and GPP edit, security and WMI filter stale
  conflict, revision restore, and edge cases (max QWORD, Unicode, long
  values, server errors, narrow viewport).

#### Release engineering and 1.0 gates (Plan 020)

- Single version source: `pyproject.toml` reads `__version__` dynamically from
  `src/gpo_studio/__init__.py` via hatchling. Package metadata and `__version__`
  are verified consistent in CI.
- Installed-package smoke test: CI builds wheel, installs in clean Python 3.13
  venv, verifies CLI entry point, API functionality (create GPO, add settings,
  export bundle, generate plan), static UI, workspace integrity check, sdist
  excludes, and sdist required files.
- GitHub Actions pinned by immutable commit SHA with least job permissions on
  all CI jobs.
- Dependency vulnerability scanning via `pip-audit` in CI.
- SBOM generation (CycloneDX) for the shipped wheel, uploaded as artifact.
- Static safety checks (`scripts/check_safety.py`): AST-based scan for
  forbidden imports (ldap, smb, win32, subprocess, shlex), unsafe XML parsing
  (ET.fromstring/ET.parse without bounded wrapper), and publication code in the
  web process.
- Identifier gate fail-closed behavior tested (`tests/test_identifier_gate.py`).
- `SECURITY.md` security policy with supported versions, vulnerability
  reporting, threat boundaries, deployment model, and known considerations.
- `CHANGELOG.md` (this file).
- `CONTRIBUTING.md` with the complete local gate, fixture-safety rules, and
  change expectations.
- `docs/installation.md` covering installation, configuration, data location,
  privacy, troubleshooting, Windows-lab compatibility, and a five-minute
  guided workflow.
- `docs/release-evidence.md` release evidence manifest with test summaries,
  Windows lab report, and known issues.
- A limited Windows smoke run exercised generated-plan GPO creation, DWORD and
  string registry commands, and side status. It found the empty-comment defect
  below, but does not satisfy the per-capability evidence matrix; all rows
  remain pending.
- Risk-based branch-coverage floors, bounded parser/codec properties, production
  dependency vulnerability and license checks, history secret scanning,
  reproducible builds, sdist installation, and an upgrade/rollback rehearsal.
- Tag-triggered release workflow producing checksums, CycloneDX SBOM, GitHub
  provenance/SBOM attestations, and exact-artifact installation tests. The
  sanitized Windows lab evidence report is attached to each release and
  covered by the checksums and attestation.
- Separate fail-closed publication gates for prerelease candidates and the
  final release: an RC can be published as immutable test material while the
  final tag still requires an explicitly approved evidence manifest. Attached
  evidence resolves the tagged commit and wheel, sdist, and SBOM hashes.
- `docs/windows-quickstart.md`: single-operator Windows installation guide
  requiring no Git, `uv`, IIS, or service installation.
- `docs/nvda-validation-runbook.md`: scripted manual NVDA screen-reader
  session for the open Plan 019 acceptance gate.
- Plan 032 (hardened hosted control plane) authored as the executable plan
  for Plan 001 Phase 3, sequenced before Plan 030 controlled publication.

### Changed

- DWORD and QWORD request values are represented as validated decimal strings
  at the JSON boundary, then converted to Python integers after range
  checking. Prevents browser `Number` precision loss for QWORD values
  above 2^53-1. Same contract applied to ADMX decimal and enum elements.
- Mutation validation centralized so API and store callers cannot diverge.
- GPP Registry model uses a one-value-per-item invariant matching the
  MS-GPPREF one-element-per-item model. Each `<Registry>` element maps to
  exactly one domain object with one value, one UID, one ILT filter, and
  one set of element metadata.
- Unknown CSE content is inventoried (file path, SHA-256 hash, size) and
  included in `review_model_sha256`, but GPMC backup export is blocked when
  unknown CSE content is present because the bytes cannot be faithfully
  reproduced.
- Identifier gate promoted to a required CI check with tested fail-closed
  behavior.
- Capability documentation now matches executable endpoints and export
  behavior. The roadmap is superseded by the capability matrix.
- PowerShell plan accuracy documented: registry values, links, security
  filtering, and side status are actionable; WMI filter assignment and GPP
  content are not applied by the plan and are included in GPMC backup
  export only.

### Security

- `cpassword` attributes structurally detected and rejected at every
  boundary (GPMC backup import, Studio bundle export, GPMC backup export,
  authoring). Detector covers namespace-qualified variants (e.g.
  `x:cpassword`) and mixed-case forms.
- Non-loopback binding refused without `GPO_STUDIO_UNSAFE_BIND=1`
  acknowledgment. The CLI exits with an explanatory error.
- XML entity declarations rejected rather than expanded (billion-laughs
  protection) on all XML parsers.
- Symlink rejection and path-traversal guards enforced on all archive and
  inbox import paths.
- Request body size streaming enforced with a 10 MiB ceiling.
- Logs and error bodies sanitized: policy values, SIDs, paths, and request
  bodies are never logged. Error messages do not leak absolute filesystem
  paths or SQL.
- PowerShell plan validated through a closed allowlist before execution,
  rejecting unexpected command shapes, aliases, backticks, and
  case-insensitive cmdlet variants.
- Claimed actor identity is untrusted in 1.0 and must never be treated as
  authenticated audit identity.

### Fixed

- GPP and ILT collection changes now update `policy_semantic_sha256`.
  Previously, modifying a GPP collection did not change the semantic hash,
  making it unusable as a review or approval boundary.
- Link and GPP concurrent edits produce three-way conflicts rather than
  silent merges.
- Max QWORD (2^64-1) round-trips from browser-shaped JSON without precision
  loss. Previously, browser `Number` serialization truncated values above
  2^53-1.
- Stale revision mutations fail with HTTP 409 instead of silently
  overwriting. Optimistic concurrency (`expected_revision`) enforced
  consistently.
- Legacy snapshot loading and metadata precedence corrected for pre-018
  workspace imports.
- Multi-string (`REG_MULTI_SZ`) browser rendering and newline splitting
  fixed; report values rendered as human-readable semicolon-separated text
  instead of Python representation syntax.
- GPP reorder conflict detection in two-way and three-way diff.
- GPP collection validated before persisting imported GPO content.
- Root-level XML attributes and unknown root children on `<Groups>` and
  `<RegistrySettings>` preserved through import/export round-trips.
- ILT predicate interleaving order of typed and unknown predicates
  preserved through round-trips.
- `cpassword` namespace-qualified bypass closed after adversarial review.
- GPP reorder kind narrowed to a `Literal` with regression test proving
  invalid kinds never enter the mutation transaction.
- Identical revision comparisons rejected before either revision is loaded.
- GPP module shared state cleared on dialog close, preventing cancel-cycle
  leaks.
- Generated PowerShell plan emitted `New-GPO -Comment ''` when GPO had no
  description, causing a validation error (New-GPO requires non-empty
  comment). Now emits `-Comment 'Created by GPO Studio'` as fallback. Found
  during a limited Windows smoke run.
- Generated PowerShell plan emitted `REG_BINARY` values as a bare
  `[byte[]](...)` cast, which fails parameter binding under Windows
  PowerShell 5.1. Now parenthesized (`([byte[]](...))`), with
  `([byte[]]@())` for empty values. Found during the Plan 017 WP-5 lab
  validation.
- `Registry.pol` serialization omitted the UTF-16LE null terminator on key
  and value-name strings that Windows includes. Serializer now emits the
  terminator; parser strips it and remains compatible with unterminated
  legacy files. Found during the Plan 017 WP-5 lab validation.
- GPMC backup `Registry.pol` included the `HKLM\`/`HKCU\` hive prefix in
  key paths; Windows infers the hive from the `Machine`/`User` directory
  and a prefixed key produces incorrect paths on import. Found during the
  Plan 017 WP-5 lab validation.
