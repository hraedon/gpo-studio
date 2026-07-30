# Plan 033 remediation scenario corpus

Status: landed 2026-07-29. The corpus is data plus its validator; no scenario
has been executed against a Windows oracle, and no scenario changes any
capability claim by itself.

## What this is

Plans 025–032 landed domain layers that diverged from Windows reality. The
remediation program proves each repaired behavior against native Windows
tooling, and this corpus is the durable record those proofs run against:

- **Scenarios** — one JSON file per expected behavior under
  `tests/fixtures/scenarios/<family>/<scenario-id>.json`, annotated with
  provenance (how the expectation is known), the platform that proves it,
  and the boundary each assertion belongs to.
- **Test platforms** — `tests/fixtures/scenarios/platforms.json`, the
  machine-readable registry of hosts, tools, and oracle lanes, extending
  `environment-spec.md`.

The JSON schemas (`remediation-scenario-v1.schema.json`,
`test-platform-registry-v1.schema.json`) are the cheap structural gate.
`src/gpo_studio/remediation_corpus.py` is the real validator: referential
integrity between scenarios and the registry, readiness honesty, anchor
integrity, and per-family payload shape. `tests/test_remediation_corpus.py`
keeps the corpus green.

## The two rules that make it durable

1. **Provenance is graded, not asserted.** Every scenario carries a tier:
   `native-observation` (anchored to captured native tooling output, with a
   sha256 the loader verifies so a changed capture breaks the corpus loudly),
   `spec-informed` (derived from MS-* documentation, not yet captured), or
   `hypothesis` (believed, must be proven before any claim). A scenario may
   record derivations and open questions; it may not present an unverified
   assumption as an expectation.
2. **Readiness is checked, not declared.** A scenario may not claim `ready`
   when its lane requires a host or tool that is not `frozen` in the
   registry; the loader rejects the file. A `blocked` scenario must name the
   gap in `blocked_reason`. The readiness map is itself a deliverable: it
   records which lanes execute today and exactly what blocks the rest.

## Current readiness map (2026-07-30)

| Family | Scenario | Readiness | Blocked on |
|---|---|---|---|
| gpp-services | native-recovery-units | ready | — |
| gpp-services | reader-no-silent-drop | ready | — |
| gpp-services | writer-parity-target | ready | certified by corrected WP-1B run `wp1b-writer-20260730164352-5286` |
| security-template | services-area, regkeys-filesecurity, group-membership, codec-edge-cases | blocked | member-ws2025-disposable qualification (open WP-3 PR-19 follow-up) |
| rsop-topology | lsdou-precedence, disabled-block-enforced, security-filtering, wmi-loopback-slowlink | blocked | client-win11 qualification |
| ilt-os | server-10x-collision, edition-union-expansion | ready | — |

The map is enforced by the loader and pinned by
`test_known_readiness_map`; it changes only with the corpus.

## Platform gaps the corpus exposes

1. **client-win11** (Windows 11 Enterprise 25H2, 26200 family) is listed in
   `environment-spec.md` as not yet tested; it was re-frozen from
   24H2/26100 to 25H2/26200 on 2026-07-29 before any endpoint evidence
   existed. The rsop-topology family and endpoint ILT confirmation ride on
   its qualification, which produces its own evidence manifest and an
   environment-spec update.
2. **member-ws2025-disposable** is the dedicated disposable host demanded by
   the open WP-3 PR-19 follow-up; the security-template lane may not expand
   beyond the certified account/audit/user-rights tranche without it. It is
   expected to land within the planned disposable `ad.labdomain.dev`
   three-VM estate that `environment-spec.md` now names as the successor to
   the shared host; any lane re-point requires a re-freeze and a fresh
   qualification run.
3. **secedit, gpresult, whoami** are inbox tools the lanes require that
   `environment-spec.md` does not version-pin; they ride host qualification.
   The WP-3 environment-qualification-profile follow-up itself landed on
   2026-07-29 (build-family qualification, LGPO de-gated to recorded
   provenance), which is why `powershell-5.1` qualifies on the
   `5.1.26100 family` and `lgpo` is `pending-qualification` in the registry
   until a lane genuinely invokes it.
4. **services in the writer lane**: WI-022 added Services to
   `writer_conformance.NATIVE_GPP_FAMILIES` and the WP-1B candidate set. The
   initial isolated and mixed candidates passed clean-source Windows run
   `wp1b-writer-20260730151953-6878`, but the later manual capture invalidated
   that run's delay semantics and opened WI-024. Corrected clean-source run
   `wp1b-writer-20260730164352-5286` passed all seven candidates from commit
   `716f43c` and recertified Services. This lane proves GPMC writer conformance,
   not endpoint application.

## Per-family payload contract

The JSON schema leaves `authored_intent` and `expected_native` opaque; the
shape below is enforced by `remediation_corpus._validate_family_payload`
and documented here. Adding a family means: schema enum entry, a branch in
`_validate_family_payload` (the `assert_never` makes an unhandled family a
type error), a directory, and a section in this file.

### gpp-services (WI-022 / WI-024)

- `authored_intent.items` (list, required): operator-meaning per item —
  service name, startup type, service action, timeout, recovery intent in
  human units.
- `expected_native.items` (list, required): per item, `properties_attrs`
  (exact attribute names/values on the wire), `omitted_attrs`, and
  optionally `must_not_contain_attrs`. `expected_native.derivations`
  records how observed bytes map to intent (units, omission rules), each
  tied to an anchor.

### security-template (Plan 025 / WP-3 areas)

- `authored_intent.sections` (list, required): `[name, entries]` pairs in
  INF order, plus `operator_meaning` prose per entry.
- `expected_native.entries` (list, required) and
  `expected_native.round_trip` (string, required; currently
  `secedit-validate-import-export`). `inf_excerpt` carries the expected
  wire text; `derivations` records code meanings and their provenance.

### rsop-topology (Plan 029 / WP-6)

- `authored_intent.topology` (object, required): `som`, `gpos`, `links`,
  and the conflict key whose winner is observable. Identifiers are
  synthetic; the harness substitutes lab values at execution.
- `expected_native.winners` (list) or `expected_native.per_mode` (list,
  for multi-mode scenarios such as loopback). Every conflicting value names
  a winner and a source GPO; `applied`/`denied` sets are recorded where the
  oracle exposes them.

### ilt-os (WI-023)

- `authored_intent.predicate` (object, required): the operator's goal and
  filter fields.
- `expected_native.match_semantics` (object, required): the token meaning,
  match surface, and — where the corpus exists — the verbatim predicate
  union from the anchor capture. `studio_must_surface` records the
  operator-facing limitation behavior WI-023 requires.

## Divergences already recorded by this corpus

1. **Services recovery units (WI-024, settled by dedicated capture).** GPMC
   writes `resetFailCountDelay` in seconds (2 days → `172800`) and both
   `restartServiceDelay` and `restartComputerDelay` in milliseconds (7 minutes
   → `420000`; 3 minutes → `180000`). The earlier 1000x hypothesis came from an
   unreliable intent note and is superseded by the screenshot-backed capture.
2. **Silent reader drops (WI-022, corrected in code).** The parser now types
   `thirdFailure`, `resetFailCountDelay`, and both typed millisecond delays, and
   the writer emits the native names. `TestWi022ServicesConformance` pins this
   against both genuine captures.
3. **Attribute omission rules (WI-024, corrected in code).** The service model
   distinguishes absent recovery values, omits `serviceAction` for No change,
   and omits recovery attributes GPMC did not write. The corrected writer-parity
   scenario passed clean WP-1B run `wp1b-writer-20260730164352-5286`.
4. **Services recovery vocabulary and fields (WI-024, capture-settled).** GPMC
   writes Run a Program as `RUNCMD`, Restart the Computer as `REBOOT`, the
   command as `program`/`args`, the append-failure-count checkbox as `append=1`,
   and restart message/delay with `restartMessage`/`restartComputerDelay`.
   Local System plus desktop interaction emits `accountName=LocalSystem` and
   `interact=1`. `BOOT`, `SYSTEM`, and `RESTART_IF_REQUIRED` remain
   protocol-defined but not exercised by this capture.
5. **semantic-manifest-v1 element enum (WI-022, corrected).** The enum now
   names genuine `NTService` and `GlobalPowerOptionsV2` elements rather than
   the nonexistent `Service` entry. The supplementary captures still carry no
   semantic manifests.
6. **Server 2016/2025 collision (WI-023, surfaced in code).** GPMC emits
   `version="WINTHRESHOLDSRV"` for the whole 10.0 server family and the
   FilterOs match surface has no build field; `ilt-os/server-10x-collision`
   records the operator-surfacing behavior. Studio now preserves the structured
   criteria through API/browser edits, shows the actual family scope beside an
   imported read-only OS predicate, and carries the warning in preflight and
   Studio bundle manifests. The remaining endpoint question still requires a
   qualified Server 2016/2025 pair; the warning does not depend on that run.
   `edition-union-expansion` pins GPMC's eleven-predicate union verbatim
   from the genuine capture.

## Extending the corpus

1. Add the scenario file under the right family directory; the file stem
   must equal `scenario_id`.
2. Choose the provenance tier honestly and anchor every native claim with a
   sha256.
3. If the lane needs a platform the registry lacks, add the host/tool row
   as `pending-qualification`; the loader then forces `blocked` until
   qualification lands.
4. Run `uv run pytest tests/test_remediation_corpus.py -q`.
