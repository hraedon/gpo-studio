# Corpus topology redaction guidelines

Plan 022 WP-1 requires a representative ADMX/ADML test corpus: one that models
common production patterns without mirroring any real Active Directory estate.
Follow these rules when adding or editing fixtures under
`tests/fixtures/corpus/`.

## Representative patterns

A representative corpus keeps the shape and semantics seen across many real
deployments, but not the structure of any one domain. Do not build it by
sanitizing a copy of a reference estate. Invent namespaces, categories, policy
counts, CSE orderings, and policy combinations that are plausible but match no
production environment.

## What must be reshaped

Corpus authors MUST invent these properties from scratch for every corpus file:

- **GPO counts and policy inventories**: use a different number of policies, a
  different mix of Machine/User/Both classes, and different policy names than
  any real estate.
- **CSE presence and ordering**: do not copy CSE GUID orderings or
  client-side-extension mappings from a real domain. Use synthetic categories
  and supported-on definitions.
- **Category ancestry**: build multi-level category trees, but choose IDs,
  display names, and parent relationships that do not mirror any vendor or
  domain layout.
- **Policy combinations**: bundle synthetic policies into files in a way that is
  coherent for testing but unrelated to how any real central store is
  organized.
- **Registry paths and value names**: use synthetic roots such as
  `Software\Policies\TestLab\...` and never real product, site, or host paths.
- **Namespaces**: declare synthetic target namespaces such as
  `Synthetic.Policies.*` or `TestLab.Policies.*`. `using` references point to
  other synthetic namespaces. Reference a real vendor namespace only in a
  fixture that explicitly tests cross-vendor collision.

## What can be preserved

These are universal ADMX/ADML mechanics and may be kept as they are:

- **Individual policy semantics**: account lockout, audit, Windows Update,
  Defender, user desktop, and network policies are representative as long as
  their names, paths, and combinations are invented.
- **Element and control types**: text, multiline text, dropdown lists, lists,
  checkboxes, decimal values, and their ADML presentation bindings are general
  patterns.
- **Registry value types**: `REG_DWORD`, `REG_QWORD`, `REG_SZ`, and delete
  behavior are generic mechanisms.
- **ADMX/ADML structural forms**: `policyNamespaces`, `categories`,
  `supportedOn`, `enabledValue`/`disabledValue`, `enabledList`/`disabledList`,
  `explicitValue` lists, and `class="Both"` policies are part of the
  Microsoft schema and can be used verbatim.

## Checklist for corpus authors

Before adding or editing a corpus fixture, verify:

- [ ] All names are synthetic: no real domain names, host names, SIDs, GPO
      names, OU names, site names, or user/service account names.
- [ ] Registry paths use a synthetic root such as `Software\Policies\TestLab\...`.
- [ ] Namespace declarations use `Synthetic.Policies.*` or `TestLab.Policies.*`.
- [ ] Policy counts, class mix, and category ancestry differ from any known
      real estate.
- [ ] ADML string references all resolve in the same file or another fixture
      in this corpus; no `$(string.X)` or `$(presentation.X)` remains
      unresolved after parsing.
- [ ] The corpus parses without silent loss: every policy, category,
      supported-on definition, namespace declaration, element, and
      presentation control that appears in the ADMX/ADML is represented in
      the parsed catalogue.
- [ ] Fixtures use the real-world ADMX namespace
      `http://schemas.microsoft.com/GroupPolicy/2006/07/PolicyDefinitions`
      and the matching ADML namespace so the corpus exercises the parser path
      used by real central stores.
- [ ] No fixture relies on a structure that is a structural clone of a
      Microsoft, vendor, or customer template pack.

## Current corpus

The fixtures under `tests/fixtures/corpus/` are organized by representative
policy domain:

- `security_baseline` — account lockout, audit, and list-based security
  options.
- `windows_update` — active hours, defer modes, allow lists, and delete-on
  disable behavior.
- `defender` — real-time protection, enum sample consent, additive lists, and
  multiline exclusions.
- `user_config` — wallpaper, Start Menu layout, taskbar lock, and screen saver
  timeout.
- `network` — 64-bit bandwidth limit, DNS server lists, firewall profiles,
  and a Both-class multi-element policy.

The corpus exercises namespaces with `using` references, supported-on
definitions, category ancestry, every supported presentation control type,
`enabledValue`/`disabledValue` with decimal/string/longDecimal/delete forms,
`enabledList`/`disabledList`, `explicitValue` lists, and `class="Both"`
policies. It leaves out `deleteKey` and supersedence constructs until the parser
models them, so that every fixture in the directory still passes the "parses
without silent loss" check.
