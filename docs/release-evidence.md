# Release evidence manifest — GPO Studio 1.0.0.dev0

> **Date:** 2026-07-16
> **Source commit:** pending reviewed release-candidate commit
> **Status:** development evidence; not approved for a 1.0 tag

This manifest distinguishes automated evidence already reproduced from
release-candidate evidence that still requires an external environment. A
placeholder or smoke observation never promotes a capability-matrix row.

## Reproduced automated evidence

- Python: 1,227 passed and 10 platform skips after the Plan 020 review fixes.
- Branch coverage: 84.60% overall; API, backup, canonical, export, GPP,
  PowerShell validation, Registry.pol, store, validation, and workspace
  operations all exceed their separately enforced risk-based floors.
- Strict mypy and Ruff: clean.
- Frontend: Prettier, ESLint, and 15 Vitest tests pass.
- Browser: seven Chromium journeys and the Firefox smoke pass; covered states
  have no serious or critical axe findings.
- Installed wheel: version metadata, CLI, health API, workspace creation,
  registry mutation, Studio bundle export, PowerShell plan, static UI, and
  workspace integrity pass from a clean Python 3.13 environment.
- Wheel and source distribution build successfully and include the required
  license, security, changelog, and contribution documents. With
  `SOURCE_DATE_EPOCH=315532800`, two independent builds are byte-identical.
- Static safety and identifier-gate regression checks pass.

The reviewed CI adds risk-based branch-coverage floors, bounded Hypothesis
properties for critical parsers/codecs, production-dependency vulnerability
and license checks, history secret scanning, a reproducible installed-sdist
test, a CycloneDX SBOM, and an operational upgrade/rollback rehearsal. Their
remote run IDs and artifact hashes must be recorded after the candidate commit
lands; local success alone is not release evidence.

## Limited Windows smoke observation

A Windows Server 2025 smoke run exercised generated-plan GPO creation,
`REG_DWORD` and `REG_SZ` commands, and side status. It found a concrete defect:
`New-GPO -Comment ''` is rejected. The generator now emits
`Created by GPO Studio` when the description is empty, with a regression test.

This smoke is deliberately **not** accepted as Plan 017 release evidence:

- it used Domain Admin rather than a documented least-privileged test role;
- it covered only two of six registry types and no delete operation;
- it did not exercise links, desired security-filter reconciliation, WMI,
  GPP Groups/Registry, ILT, or both policy sides;
- `Backup-GPO` backed up the live test GPO; it did not open, import, restore, or
  compare the Studio-generated GPMC backup artifact;
- no sanitized raw report, artifact hashes, cleanup record, or source commit
  was attached.

Consequently every Win-lab cell in `docs/capability-matrix.md` remains pending.

## Accessibility evidence

Automated semantics, keyboard behavior, focus behavior, and axe checks pass.
The hands-on NVDA/Chromium and NVDA/Firefox sessions in
`docs/browser-accessibility-checklist.md` remain pending and are release
candidate blockers under the current Plan 019/020 acceptance language.

## Schema and artifact identity

- Workspace schema version: 1
- Application version: 1.0.0.dev0
- Source commit: pending
- Wheel SHA-256: pending candidate build
- Source distribution SHA-256: pending candidate build
- CycloneDX SBOM SHA-256: pending candidate build
- Release checksums/provenance attestations: pending release tag

## Known limitations

- GPMC backup import supports the repository's documented
  `manifest.xml`/`bkupInfo.xml` shape but not modern `Backup.xml` backups. The
  capability remains preview and must not be described as modern-GPMC verified.
- The generated PowerShell plan intentionally does not apply WMI assignments or
  GPP content; operators must use the reviewed artifact path described in the
  capability matrix.
- Actor identity is claimed and unauthenticated in the single-operator 1.0
  deployment profile.

## Release blockers

1. Run the complete Plan 017 matrix with least-privileged credentials and
   attach sanitized reports, hashes, cleanup status, and the candidate commit.
2. Complete and record the Plan 019 hands-on screen-reader sessions.
3. Land the reviewed Plan 020 pipeline and record all successful remote jobs,
   candidate artifact hashes, SBOM hash, and upgrade/rollback output.
4. Cut `1.0.0-rc.1` only after items 1–3 refer to the same clean commit.

No `1.0.0` tag may be created while this section is non-empty.
