# Plan 022 — Administrative Templates and Starter GPO parity

Status: proposed (post-1.0)
Scope: complete ADMX/ADML semantics, central-store management, classic registry
policy reporting, and Starter GPO lifecycle
Depends on: Plan 021 review gate
Review gate: **REVIEW AND REFINE — REQUIRED after the template corpus tranche**

## WP-1 — Complete ADMX/ADML semantics

- Support namespaces, `using`, supersedence, supported-on graphs, localization,
  category ancestry, presentation references, list/value prefixes, compound
  policies, enabled/disabled value lists, delete-value/delete-key behavior, and
  every presentation/control variant found in the reference corpus.
- Model Enabled, Disabled, and Not Configured explicitly.
- Preserve unknown schema extensions and vendor annotations.
- Make locale display-only; semantic identity derives from template namespace,
  policy name, class, and registry mapping.

## WP-2 — Template repositories and target locks

- Ingest local PolicyDefinitions, domain Central Store, signed vendor packs,
  and offline curated packs without granting the web process domain writes.
- Detect namespace/file/string collisions, missing ADML, version drift, and
  changed registry mappings.
- Lock drafts to exact template hashes and target OS/support definitions.
- Provide upgrade previews and refuse silent remapping.

## WP-3 — Full authoring and reporting experience

- Implement typed controls, raw mappings, explain/support text, configured-state
  search, category navigation, copy/paste, comments, bulk changes, and policy
  state reset.
- Compare generated Registry.pol and normalized GPMC reports for all states.
- Support legacy ADM import in preserve/read-only mode unless the Plan 021
  matrix explicitly approves an editable compatibility adapter.

## WP-4 — Starter GPO lifecycle

- Discover, create, edit, rename, comment, back up, import, copy, restore, and
  delete Starter GPOs through supported GPMC interfaces.
- Preserve Starter GPO identity, template version, Computer/User registry
  settings, and provenance when deriving a normal GPO.
- Add migration and conflict handling for central-store/template drift.

## Acceptance gates

- The representative Microsoft/vendor corpus parses without silent loss.
- Every observed presentation form has UI/model/PReg/report golden tests.
- GPMC and Studio agree on all three states and mixed Computer/User policies.
- Template upgrades cannot change an existing draft without explicit review.
- Starter GPO lifecycle and derivation round-trip through GPMC.

## REVIEW AND REFINE — REQUIRED

After the first full Microsoft template corpus and at least three vendor packs,
stop to review parser generality, identity rules, and UI scalability. Refine the
remaining adapter plans if the corpus exposes new shared value/control types.

