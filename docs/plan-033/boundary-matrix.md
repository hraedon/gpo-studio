# Plan 033 state-boundary matrix

Status: active contract for Windows external-oracle runs

Every semantic assertion in a Plan 033 run names exactly one oracle and one
boundary below. If a check crosses boundaries, split it into separate
assertions. A passing content comparison cannot stand in for passing AD or
endpoint evidence.

| Boundary value | State owned by the boundary | Preferred oracle | Explicitly excluded |
|---|---|---|---|
| `gpo-backup-content` | `Backup.xml`, `bkupInfo.xml`, manifest metadata, `GPT.INI`, Registry.pol, security templates, scripts, and CSE files under `DomainSysvol/GPO` | Native `Backup-GPO` output, GPMC report/editor, CSE-specific Windows parser | GPO DACL, WMI object, links, endpoint application |
| `gpo-ad-object-security` | The groupPolicyContainer object, AD attributes and versions, owner, DACL/SACL, and delegation/effective rights | Direct LDAP/ADSI read plus Windows security-descriptor APIs | Backup content, SOM links, token evaluation |
| `wmi-filter-object-association` | `msWMI-Som` object fields and the GPO's WMI-filter association | Direct LDAP/ADSI read and GroupPolicy module association readback | Endpoint WMI truth value, SOM links |
| `som-link-block-inheritance` | Site/domain/OU `gPLink`, link order, enabled/enforced state, and `gPOptions` block inheritance | Direct LDAP read of the exact SOM plus GroupPolicy cmdlet readback | GPO content, GPO DACL, endpoint application |
| `endpoint-resultant-state` | Token-dependent filtering, WMI evaluation, loopback, precedence, CSE processing, registry/files/services/tasks, and resultant state | `gpresult`/RSoP, direct endpoint state, and GroupPolicy operational events | Authoring-format validity by itself |

## Assertion rules

1. `oracle` identifies the independent Windows observation, not a Studio
   parse-format-parse round trip.
2. `boundary_owner` is one of the five exact values above.
3. Expected and observed hashes are hashes of the normalized semantic form
   identified by `normalizer_version`.
4. A comparison with an unsupported difference has `equal: false`, contains
   at least one human-readable difference, and cannot produce a passing
   capability state.
5. A capability requiring more than one boundary receives one assertion per
   boundary. For example, GPP publication normally has backup-content,
   SOM-link, and endpoint assertions.
6. Raw command output and lab artifacts remain in the controlled evidence
   store. Repository fixtures must be synthetic or sanitized and pass the
   identifier gate.

## Evidence-state rule

`pass` means all assertions required by that capability-matrix row passed and
cleanup completed. `fail` means at least one supported assertion differed or a
command failed. `unsupported` is an explicit capability downgrade, not a waived
failure. `inconclusive` means the run cannot support a claim, including missing
oracle data, missing event data where required, or incomplete cleanup.

The executable parser and normalizer are in
`src/gpo_studio/oracle_evidence.py`. The machine-readable manifest contract is
`windows-oracle-manifest-v1.schema.json`.
