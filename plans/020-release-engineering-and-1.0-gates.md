# Plan 020 — Release engineering and 1.0 gates

Status: implemented and accepted — v1.0.0 released 2026-07-18. The tagged
remote pipeline ran through rc.1–rc.3, the hands-on NVDA acceptance journey
passed on the promoted candidate, and the release evidence is recorded in
`docs/release-evidence.md`. No Plan 020 work remains open.
Scope: turn the verified application into a supportable, reproducible 1.0
release with explicit evidence and rollback
Depends on: Plans 015 through 019

## Purpose

Plans 015–019 produced a verified development tree with more than 1,200 Python
tests plus strict typing, frontend unit tests, and real-browser automation. Plan
020 turns that tree into reproducible installed artifacts with explicit supply
chain, compatibility, accessibility, upgrade, rollback, and release evidence.
A 1.0 requires a repeatable process and one exact reviewed artifact identity,
not only a version bump.

## WP-1 — Packaging and installed-product tests

- Add a single version source and verify package metadata/API version agree.
- Build sdist and wheel in CI, install each into a clean environment, launch the
  CLI, open the packaged static UI, create a workspace, and export a fixture.
- Verify source archives contain required docs/license and exclude caches,
  databases, local hooks/configuration, forbidden identifiers, and test output.
- Define supported Python versions and remove untested claims.
- Add reproducibility checks or document the unavoidable build metadata.

## WP-2 — Quality and security gates

- Measure branch coverage by subsystem and set risk-based floors; do not use one
  aggregate percentage to hide untested UI/export paths.
- Add fuzz/property jobs for parsers and deterministic codecs with bounded CI
  budgets and persisted minimized regressions.
- Add dependency vulnerability/license scanning, secret scanning, and SBOM
  generation for the shipped wheel.
- Pin GitHub Actions by immutable commit and configure least job permissions.
- Add static checks for unsafe XML APIs, direct AD/SMB dependencies, shell
  execution, and forbidden web-process publication code.
- Keep the identifier gate as a required check and test its fail-closed behavior.

## WP-3 — Documentation and operator experience

- Rewrite README/roadmap to match the Plan 015 capability contract.
- Add installation, configuration, backup/recovery, upgrade, troubleshooting,
  data-location, privacy, threat-boundary, and Windows-lab compatibility docs.
- Add `SECURITY.md`, `CHANGELOG.md`, contribution/test guidance, and a release
  support policy.
- Document every environment variable and CLI option with secure defaults.
- Include an example synthetic workspace/catalogue and a five-minute guided
  author-review-export workflow.

## WP-4 — Release candidate process

- Cut an immutable release candidate from a clean main branch. RC1 retained the
  failed private-repository/SBOM-attestation attempt; RC2 is the first candidate
  eligible for publication.
- Publish the RC as a clearly marked prerelease with its wheel, source
  distribution, checksums, SBOM, and evidence. Candidate publication precedes
  hands-on external gates so every result can name the exact artifact tested;
  publishing an RC is not approval for the final release.
- Run normal CI, installed-artifact tests, browser/accessibility journeys,
  parser fuzz budget, migration/recovery drills, and the Windows lab suite.
- Conduct focused reviews of trust boundaries, canonical/hash completeness,
  PowerShell generation, import resource limits, SQLite concurrency, and
  `cpassword` handling.
- Publish a release evidence manifest containing source commit, artifact hashes,
  SBOM hash, test summaries, Windows lab report, schema versions, and known
  limitations.
- Require at least one clean upgrade/rollback rehearsal using a copy of a
  representative synthetic pre-1.0 workspace.

## WP-5 — 1.0 release and rollback

- Resolve all release-blocking findings or record non-blockers in known issues
  with owner and target version.
- Tag the exact reviewed commit and publish signed checksums and artifacts.
- Validate installation from the published artifact, not the build workspace.
- Preserve the prior release and workspace backup instructions for rollback.
- Define the 1.0.x policy: backward-compatible workspace/bundle reading,
  security-fix handling, and deprecation windows.

## Global 1.0 exit criteria

- All Plan 015–019 acceptance gates pass.
- Unit, API, parser, installed-package, browser, accessibility, migration,
  recovery, concurrency, and Windows interoperability suites are green.
- No known issue can cause silent policy loss, incomplete review hashes/diffs,
  QWORD corruption, unsafe publication, or workspace-history loss.
- The shipped capability matrix makes no unverified GPMC compatibility claim.
- The web process contains no AD/SYSVOL write path and refuses unsafe remote
  exposure under the 1.0 deployment profile.
- Release artifacts, evidence, SBOM, checksums, schema versions, upgrade path,
  and rollback instructions are published together.

## Recommended milestone order

```text
M0  Plan 015: release contract and cross-cutting correctness
M1  Plan 016 + Plan 018: complete GPP vertical slice and harden workspace
M2  Plan 017 + Plan 019: external interoperability and browser evidence
M3  Plan 020: release candidate, remediation, and 1.0.0
```

Plans 016 and 018 can overlap after Plan 015 contracts stabilize. Plan 017
fixture work can begin earlier, but release conformance should test the final
Plan 016 formats. Plan 020 gates are established early and enforced fully at RC.

## Release review — 2026-07-16

The first WP-1–4 handoff passed its base unit, type, lint, frontend, build, and
installed-wheel checks. Independent review found that the release evidence
overstated a narrow Windows smoke run: it used over-privileged credentials,
covered only DWORD/REG_SZ and side status, did not test a Studio-generated GPMC
backup, and attached no sanitized report or artifact hashes. Those observations
do not promote any capability-matrix row.

The review added the missing risk-based branch-coverage gate, bounded Hypothesis
parser/codec properties, license and secret scanning, reproducible wheel/sdist
checks, installed-sdist validation, an operational synthetic upgrade/rollback
rehearsal, and an attested tag-release workflow. It also removed local privileged
lab helper scripts from the candidate and converted the evidence manifest to a
fail-closed blocker ledger.

WP-5 may begin only when the complete least-privileged Plan 017 matrix and the
Plan 019 hands-on screen-reader pass are attached to the same immutable RC and
all remote candidate jobs are green. The RC must therefore be published under
WP-4 before the hands-on screen-reader pass; only the final tag requires the
evidence manifest's `approved for release` status.
