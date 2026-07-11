# Intune migration planner

Status: feasibility assessment and recommended design  
Decision: appropriate and high-value, if framed as migration planning rather
than automatic GPO-to-Intune translation

## Executive recommendation

Add an **Intune Migration Planner** to GPO Studio, but do not build or market a
generic GPO-to-Intune transpiler.

Microsoft Intune already provides Group Policy Analytics. It imports GPO report
XML, reports MDM support, identifies ready/unsupported/deprecated settings, and
can create a Settings Catalog policy from supported settings. Microsoft calls
the migration best effort, notes that some settings map to alternatives, routes
some settings such as Firewall and AppLocker to Endpoint Security instead, and
acknowledges that some migrations fail because values or required child settings
do not line up.

GPO Studio should integrate with that evidence rather than try to permanently
reverse-engineer Microsoft's changing private mapping catalogue.

The differentiated product question is:

> Given the policy intent that actually reaches this device/user cohort today,
> what modern-management configuration would reproduce the required outcome,
> what cannot be reproduced, how should it be assigned, and how can we cut over
> without conflicting policy or losing evidence?

That is broader, more useful, and more honest than “convert this GPO.”

Microsoft references:

- [Import and analyze GPOs with Group Policy Analytics](https://learn.microsoft.com/en-us/intune/device-configuration/import-group-policy-analytics)
- [Migrate imported GPOs to Settings Catalog](https://learn.microsoft.com/en-us/intune/device-configuration/migrate-group-policy)
- [Cloud-native endpoint planning](https://learn.microsoft.com/en-us/intune/solutions/cloud-native-endpoints/planning-guide)
- [Settings Catalog](https://learn.microsoft.com/en-us/intune/device-configuration/settings-catalog/)

## Why a literal converter is the wrong abstraction

### 1. Microsoft owns the freshest mapping

Intune's mapping changes as Settings Catalog and CSP coverage change. Group
Policy Analytics automatically updates its MDM-support result when Microsoft
updates the mapping. A bundled Studio lookup table would immediately acquire a
freshness and false-confidence problem.

Studio can independently map settings when it has strong public identifiers
(for example, an exact Policy CSP/ADMX mapping), but Microsoft tenant analysis
should be treated as the strongest current mapping evidence where available.

Current Microsoft Analytics coverage and limitations must be recorded alongside
each imported result. Microsoft currently documents parsing for Policy,
PassportForWork, BitLocker, Firewall, and AppLocker CSPs plus Group Policy
Preferences, and notes that some non-ADMX analysis is accurate only for English
source settings. These are service capabilities, not timeless Studio constants;
the connector/importer records the documentation/service version and date.

### 2. GPO boundaries are historical packaging, not necessarily good Intune design

A GPO may mix security settings, preferences, scripts, software, printers,
certificates, and user/device policy. Intune may represent those through:

- Settings Catalog;
- Endpoint Security policies;
- security baselines;
- compliance policies;
- applications or application configuration;
- scripts/remediations;
- Wi-Fi, VPN, certificate, update, enrollment, or other dedicated workloads;
- a documented manual replacement process;
- no modern equivalent because the original requirement is obsolete.

One GPO can therefore become several Intune objects, while settings from many
GPOs may belong in one coherent modern baseline. Preserving one-to-one policy
packaging would preserve accidental complexity and increase conflict risk.

### 3. OU links do not translate to Intune assignments

GPO application is built from site/domain/OU links, inheritance, enforcement,
link order, security filtering, WMI filtering, loopback, and CSE-specific
targeting. Intune configuration is assigned to Microsoft Entra user/device
groups and can be refined with supported assignment filters.

An OU is useful source evidence, but it is not an Intune assignment primitive.
The migration must explicitly choose or create the Entra group/filter model and
compare its resulting membership with the current AD/GPO cohort.

Microsoft recommends separating user and device group targeting and using
filters to refine the selected group. It also warns against mixed user/device
include/exclude patterns:
[Intune assignment performance recommendations](https://learn.microsoft.com/en-us/intune/fundamentals/filters/performance-recommendations).

### 4. GPO precedence and Intune conflicts differ

Intune configuration profiles do not recreate site/domain/OU precedence. When
multiple configuration policies set different values, Intune reports a
conflict that must be resolved. The planner should flatten known GPO precedence
into a deliberate desired value for each target cohort, not generate layers of
contradictory profiles and hope that GPO ordering semantics survive.

Reference:
[Intune policy conflict behavior](https://learn.microsoft.com/en-us/intune/device-configuration/troubleshoot-device-profiles).

### 5. Coexistence is itself a migration problem

During transition, a hybrid/co-managed endpoint may receive both GPO and MDM
configuration. `MDMWinsOverGP` only applies to settings in Policy CSP and not to
equivalent settings exposed through other CSPs such as Defender. Microsoft
recommends avoiding duplicate configuration outside that bounded behavior
because the result can be a race with no guaranteed winner.

Reference:
[ControlPolicyConflict Policy CSP](https://learn.microsoft.com/en-us/windows/client-management/mdm/policy-csp-controlpolicyconflict).

The planner must therefore produce a cutover sequence, not merely destination
JSON.

## Recommended unit of planning

Support three related entry points while making their roles explicit.

### Per GPO: readiness and provenance

Useful for:

- owner-facing assessment;
- mapping every configured source setting;
- producing GPO report XML for Microsoft Group Policy Analytics;
- identifying deprecated, unsupported, and workload-rerouted settings;
- tracking which source objects can eventually be retired.

Not sufficient for:

- determining the value that actually wins at a target;
- designing assignments;
- avoiding duplication across multiple GPOs;
- accounting for security/WMI/loopback/runtime scope.

### Per OU/SOM: topology-derived intent

Useful for:

- computing inherited GPO order and conflicts;
- identifying Computer/User settings declared at a location;
- exposing enforced links, block inheritance, and loopback caveats;
- starting a mapping from AD organizational structure to target populations.

Still not a final Intune design because users/devices, security filters, WMI,
sites, and runtime state can divide one OU into multiple cohorts.

### Per cohort: recommended migration plan

This should be the primary output. A cohort is an explicit set or rule for
users/devices that share:

- the same resolved desired setting values;
- the same user/device scope;
- the same supported assignment representation;
- the same OS/edition/applicability requirements;
- the same migration ring and coexistence state.

Examples:

- corporate Windows 11 workstations in a canary device group;
- shared devices receiving device-scoped policy and user-scoped loopback-like
  behavior;
- finance users on corporate Windows devices, expressed as a user group plus a
  supported Windows/corporate-device assignment filter;
- legacy devices that must remain on GPO because a required behavior has no MDM
  equivalent.

The planner may begin with a selected GPO or OU, but it should normalize the
answer into cohorts, desired intent, destination workloads, and assignments.

## Mapping model

Separate **disposition**, **equivalence**, and **evidence strength**. A single
“supported: yes/no” field is too lossy.

### Disposition

| Disposition | Meaning |
|---|---|
| `migrate` | Represent the requirement with an Intune workload |
| `replace` | Use a different modern control that satisfies the requirement |
| `retire` | Requirement is obsolete or inappropriate for cloud-native endpoints |
| `retain_gpo` | Keep GPO for this cohort during or after the current project |
| `manual` | Human design/implementation is required |
| `blocked` | Required dependency, target support, or evidence is missing |
| `unknown` | No defensible conclusion yet |

### Equivalence

| Level | Meaning |
|---|---|
| `exact` | Same Windows management behavior, scope, value, and removal semantics |
| `behavioral` | Different configuration surface with a verified equivalent outcome |
| `approximate` | Similar outcome with documented semantic differences |
| `alternative` | Modern redesign addresses the underlying requirement differently |
| `none` | No suitable Intune/MDM behavior |
| `unassessed` | Insufficient evidence |

### Evidence strength

From strongest to weakest:

1. Current Microsoft Group Policy Analytics result from the target tenant.
2. Current target-tenant Settings Catalog/CSP definition with exact stable
   identifier, scope, type, applicability, and value mapping.
3. Public Microsoft CSP documentation with an explicit ADMX/GP mapping.
4. Studio mapping verified in a Windows/Intune test tenant and client lab.
5. Curated expert mapping with citations and expiry.
6. Name/registry/description similarity candidate requiring review.
7. No evidence.

Heuristics may suggest candidates but can never produce `exact`, automatically
enter a deployable artifact, or count as confirmed migration support.

Every mapping record includes:

- source GPO/setting identity and source state;
- winning/overridden status for the selected cohort;
- destination workload and stable definition ID where available;
- destination scope, type, value, dependencies, and applicability;
- disposition, equivalence, evidence strength, confidence, and rationale;
- known semantic differences and removal behavior;
- evidence source, tenant/catalogue version, retrieval time, and expiry;
- reviewer decision and organizational override history.

## Destination workload routing

The first decision is often the workload, not the setting ID.

| Source intent | Preferred destination investigation |
|---|---|
| Administrative Template / Policy CSP mapping | Settings Catalog |
| Defender, Firewall, attack surface reduction, disk encryption, account protection | Endpoint Security or security baseline before generic Settings Catalog |
| Required security posture measurement | Compliance policy, possibly with a separate configuration policy |
| Software deployment | Intune Apps/Win32 application workflow |
| Certificates, Wi-Fi, VPN | Dedicated Intune profile and certificate infrastructure |
| Windows Update policy | Update rings, feature/quality update policies, Autopatch where applicable |
| Local groups/privilege | Endpoint Security account protection or Endpoint Privilege Management where appropriate |
| Scripts/tasks/files used as configuration glue | Reassess requirement; app packaging or remediations only after security review |
| User preferences | Prefer retirement or user choice unless a current requirement justifies enforcement |
| Legacy domain-only behavior | Retain GPO, replace architecture, or declare no equivalent |

This routing must remain extensible and tenant/license aware. A feature existing
in Intune does not prove that the target tenant is licensed, configured, or
operationally ready to use it.

## Scope translation

Scope planning is a first-class artifact with membership evidence.

### Inputs

- GPO links, order, enabled/enforced state, sites, and inheritance blocks;
- selected OU/SOM descendants;
- Computer/User side status;
- security filtering and explicit principal membership snapshot;
- WMI filters and available endpoint properties;
- loopback mode;
- item-level targeting;
- known user/device objects and Entra correlation identifiers;
- current Entra groups and Intune assignment filters;
- platform, OS build, edition, ownership, and enrollment state;
- explicit coverage gaps.

### Translation outcomes

| GPO mechanism | Possible Intune representation | Required caveat |
|---|---|---|
| OU/device population | Existing or proposed Entra device group | OU membership must be materialized/correlated; it is not itself an assignment |
| OU/user population | Existing or proposed Entra user group | Membership lifecycle and sync latency must be owned |
| Security filtering | Include/exclude group or cohort split | Preserve user/device type; compare exact memberships |
| WMI filter | Assignment filter, dynamic group, separate policy, or manual | Only when required properties/semantics are available |
| Site link | Group/filter based on an available maintained property | AD site is not a native Intune assignment concept |
| Enforced/inheritance/order | Flatten to resolved desired value by cohort | Do not reproduce GPO precedence as conflicting Intune policies |
| Loopback | Explicit device/user assignment design | Intune scope behavior differs and must be tested |
| GPP item-level targeting | Assignment split/filter or workload-native condition | Often cannot be represented one-for-one |

### Membership comparison

For each proposed assignment, report:

- source cohort count and collection coverage;
- target group/filter evaluated count where access permits;
- objects in both;
- source-only objects that would lose policy;
- target-only objects that would newly gain policy;
- unknown/unmatched identities;
- membership data timestamps and expected propagation delay;
- whether the group is maintained manually, dynamically, by synchronization,
  or by an external identity process.

No proposed assignment is `verified` until the target membership has been
compared. Creating one Entra group per OU should be presented as one option,
not the default architecture.

## Intune tenant integration modes

### Mode A — offline advisory

No Microsoft Graph or tenant access.

Inputs:

- GPO Studio/gpo-lens estate;
- optionally exported Intune Group Policy Analytics CSV/report;
- optionally exported Settings Catalog policy JSON and tenant inventory;
- pinned public CSP/ADMX mapping packs with provenance and expiry.

Outputs:

- mapping candidates and confirmed imported Microsoft results;
- policy/workload decomposition;
- assignment design questions;
- migration gaps and cutover plan;
- GPO XML/report export ready for manual Intune analysis.

This mode is appropriate for air-gapped planning and should remain useful even
when no tenant connector is ever configured.

Settings Catalog currently supports exporting and importing policy JSON through
the Intune admin center. Studio may ingest a tenant export as evidence, but it
must preserve the export metadata and must not assume every generated object is
accepted by that import path without validation in the destination tenant.

### Mode B — connected read-only

Use a separately configured Microsoft Graph connector with least-privilege read
permissions and an active Intune tenant/license.

Read:

- current Settings Catalog definitions and applicability;
- existing Intune configuration/endpoint-security policies where supported;
- assignments, groups/filters, scope tags, and policy conflicts where available;
- Group Policy Analytics migration reports already present in the tenant;
- target catalogue/API version and national-cloud availability.

The relevant Settings Catalog and Group Policy Analytics Graph surfaces are
currently documented under Microsoft Graph `/beta`, which Microsoft says is
subject to more frequent change. The connector must version-pin, cache evidence,
feature-detect, and degrade cleanly rather than making a beta API a core/offline
dependency.

References:

- [Settings Catalog definition API](https://learn.microsoft.com/en-us/graph/api/intune-deviceconfigv2-devicemanagementconfigurationsettingdefinition-list?view=graph-rest-beta)
- [Group Policy migration report API](https://learn.microsoft.com/en-us/graph/api/intune-gpanalyticsservice-grouppolicymigrationreport-list?view=graph-rest-beta)

### Mode C — proposal export

Generate a reviewable, deterministic migration package:

- source/effective-intent evidence;
- destination object proposals split by workload;
- stable setting-definition IDs and values where verified;
- proposed assignments but no live group creation;
- prerequisites, dependencies, license assumptions, and manual steps;
- coexistence/cutover/rollback/verification runbook;
- source and destination semantic assertions.

Do not label arbitrary JSON as an Intune-importable policy unless it conforms to
a Microsoft-supported import format and has passed target-tenant validation.
Graph request previews should be labeled as API proposals.

### Mode D — managed Intune publication

This is optional and later. It requires the same discipline as AD publication:

- authenticated author/reviewer/approver roles;
- exact signed desired state and target tenant;
- expected-state compare-and-swap against existing policy/assignment versions;
- separate least-privilege Graph application/service identity;
- typed allow-listed Graph operations, never arbitrary requests;
- create-unassigned first, read back, then assign after separate review;
- canary groups, monitoring, rollback, evidence, and audit;
- independent feature flags for policy creation, assignment, group/filter
  creation, update, and deletion.

Intune writes do not belong in the AD Windows publisher. They are a separate
cloud trust boundary and connector/profile.

## Migration plan output

For a GPO, OU, or cohort, generate one coherent report.

### 1. Executive readiness

- settings considered, effective, overridden, unknown, and excluded;
- readiness by disposition and evidence strength, not a single inflated percent;
- target cohorts and estimated membership;
- destination workloads and object count;
- critical blockers and manual redesigns;
- coexistence and cutover risk;
- collection/tenant/catalogue freshness.

### 2. Setting ledger

One row per source intent:

- source GPO and setting;
- why it applies/wins for the cohort;
- current value;
- business requirement/owner if known;
- destination disposition/workload/setting/value;
- equivalence and evidence;
- behavior differences, prerequisites, and reviewer decision.

### 3. Destination policy architecture

Proposed policies grouped by purpose and workload, not blindly by source GPO.
Show duplicate/overlap/conflict analysis against existing Intune policies.

### 4. Assignment plan

Proposed include/exclude groups and filters, membership comparison, user/device
scope, assignment limitations, owner, and lifecycle.

### 5. Coexistence and cutover plan

For each cohort/wave:

1. Confirm enrollment, licensing, prerequisites, and target membership.
2. Deploy unassigned or canary Intune objects.
3. Validate target values and endpoint behavior.
4. Remove or narrow the corresponding GPO scope in the correct order.
5. Avoid relying on `MDMWinsOverGP` outside its documented Policy CSP scope.
6. Monitor Intune conflicts, GPO application, endpoint state, and user impact.
7. Advance, pause, or restore the prior GPO scope based on explicit gates.
8. Retire the source GPO only after every dependent cohort is migrated or
   intentionally retained.

### 6. Verification assertions

- desired destination policy exists with exact settings;
- assignments match the approved cohort within stated coverage;
- endpoint reports show successful application;
- conflicting GPO/Intune configuration is absent;
- required behavior is observed on representative clients;
- source GPO is unlinked/disabled/retained exactly as planned;
- rollback path remains available for the agreed window.

## Practicality assessment

| Capability | Practicality | Reason |
|---|---:|---|
| Per-GPO readiness report | High | Source data already exists; Microsoft Analytics can supply current support evidence |
| Export GPO XML for Intune analysis | High | Standard GPO report XML is already part of the ecosystem |
| Join imported Analytics CSV/report to Studio settings | High | Deterministic correlation plus manual review |
| Offline exact mapping for public Policy CSP/ADMX pairs | Medium-high | Strong identifiers exist, but catalogue freshness must be managed |
| Route settings to Settings Catalog vs Endpoint Security/etc. | Medium-high | Valuable curated rules; tenant/license/version dependent |
| Per-OU inherited-setting plan | High within current gpo-lens bounds | Existing topology engine provides the right starting data |
| Exact per-object source scope | Medium | Requires complete principals, groups, sites, WMI, loopback, and runtime evidence |
| Automatic OU → Entra assignment | Low as a universal feature | No one-to-one scope model; group lifecycle is an identity architecture decision |
| Proposed cohort/group/filter design | High | Useful as explicit reviewed intent with membership diff |
| Existing Intune conflict analysis | Medium-high with tenant read access | Graph coverage/API stability varies; normalize multiple workloads |
| Generate Graph policy proposals | Medium | APIs and setting-instance shapes are complex and often beta |
| Automatically create unassigned policies | Medium later | Feasible with Graph and strong approval/read-back controls |
| Automatically assign and cut over | High risk, appropriate only late | Broad device impact, async application, coexistence, and rollback complexity |
| “Convert every GPO exactly” | Impractical/inappropriate | Different management models, unsupported settings, and obsolete requirements |

## Recommended delivery sequence

### Phase 1 — local migration assessment

- Add migration-plan domain model and confidence taxonomy.
- Export per-GPO report XML for manual Group Policy Analytics import.
- Import Microsoft's exported readiness CSV/report and correlate settings.
- Add curated workload routing and public CSP mapping packs with timestamps.
- Produce per-GPO setting ledgers and readiness reports.
- No tenant connector and no Intune writes.

This is the best first slice: it reuses Microsoft's mapping, remains local-first,
and immediately adds better source evidence and explanation.

### Phase 2 — OU/cohort intent planner

- Use gpo-lens topology/merge outputs to calculate declared winning settings.
- Add source scope inputs and confidence/coverage labels.
- Cluster cohorts by desired setting set and user/device scope.
- Design proposed Entra group/filter assignments and membership import/diff.
- Produce policy decomposition, coexistence, cutover, and verification plans.

This is the primary differentiator.

### Phase 3 — read-only tenant connector

- Register a least-privilege Graph connector.
- Snapshot current Settings Catalog definitions, migration reports, policies,
  assignments, groups, filters, applicability, and scope tags where supported.
- Diff proposed policies against existing Intune configuration across workloads.
- Cache every tenant-derived mapping as versioned evidence.
- Degrade to imported/offline evidence if beta surfaces change.

### Phase 4 — proposal generation and lab validation

- Generate deterministic Settings Catalog/Endpoint Security Graph proposals.
- Validate setting instances against the target tenant without assigning them.
- Create only in a lab/test tenant under approval.
- Add Windows client application/removal/coexistence test matrix.
- Correlate observed Intune results with source semantic assertions.

### Phase 5 — controlled Intune publication

- Separate cloud publisher trust boundary and signed jobs.
- Create unassigned policies first.
- Require separate assignment approval and canary wave.
- Monitor per-setting/device results and source GPO removal.
- Support verified rollback and an honest partial/manual outcome.
- Expand workload by workload, never via a generic Graph proxy.

## Acceptance criteria for the first release

- A user can select one or more GPOs and export the correct report XML for
  Microsoft Group Policy Analytics.
- A Microsoft readiness export can be imported and joined without name-only
  guessing when stable source identities are available.
- Every source setting appears exactly once in the ledger, including unknown,
  overridden, deprecated, unsupported, and excluded settings.
- Evidence source and freshness are visible for every mapping.
- Heuristic matches are clearly review-required and cannot become `exact`.
- Output routes Firewall/AppLocker-like cases away from generic migration when
  current Microsoft evidence says another workload is appropriate.
- Per-GPO reports never claim assignment equivalence.
- An OU plan includes inherited order, conflicts, coverage, scope caveats, and a
  cohort/assignment design section.
- The output contains a coexistence and GPO-removal sequence.
- The feature works without an Intune tenant or network connection.
- No Graph write permission or managed Intune creation exists in the first
  release.

## Security and privacy considerations

- GPO reports reveal security posture, scripts, paths, principals, and internal
  structure. Upload to Microsoft Group Policy Analytics is an explicit user
  action with clear tenant/data-handling notice; offline mode uploads nothing.
- Graph tokens are stored only by a connector-grade credential facility, never
  in workspace JSON, bundles, logs, or browser storage.
- Default connected permission is read-only. Read/write consent is a separate
  installation and approval event.
- Imported mappings and reports are untrusted input with size, encoding, CSV
  formula-injection, archive, XML, and identifier validation.
- Tenant IDs, policy IDs, groups, and assignment membership are sensitive estate
  data with access controls, retention, redaction, and provenance.
- Suggested scripts/remediations cannot be used as an escape hatch for every
  unsupported setting; executable content has a separate threat model and
  approval class.
- An optional model may explain mappings but cannot establish equivalence,
  choose assignments, approve, or produce deployable operations.

## Final product position

The capability should be named and described as:

> **Intune Migration Planner** — Analyze GPO intent and scope, incorporate
> Microsoft's current migration evidence, design modern Intune policies and
> assignments, expose gaps and semantic differences, and produce a verified
> staged cutover plan.

Avoid names such as “Convert to Intune” or promises of automatic parity.

The best experience begins wherever the operator is working:

- **On a GPO:** “Assess modern-management options.”
- **On an OU/SOM:** “Plan migration for this scope.”
- **On a resultant/cohort view:** “Design Intune target state.”
- **Across the estate:** “Build a migration campaign.”

This fits the maximalist GPO Studio vision extremely well. It uses the existing
normalized model, topology, conflict, provenance, policy, approval, environment,
and verification work instead of building a disconnected conversion wizard.
