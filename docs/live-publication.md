# Responsible live publication

Status: design proposal. None of this document is implemented by the current
web application. Live publication must remain optional at build, deployment,
and workspace level.

## Executive decision

Do not add domain credentials or Group Policy write calls to the GPO Studio web
process. Add a separate, Windows-only publisher that accepts a narrow, signed,
typed change artifact after approval.

```text
author browser                           production domain
      │                                        ▲
      ▼                                        │ supported APIs
unprivileged control plane                    │
      │ propose                               │
      ▼                                        │
review + approval ── signed artifact ──▶ Windows publisher
                                            (gMSA, one domain,
                                             outbound-only control)
```

This follows the useful shape of Advanced Group Policy Management: edit away
from production, retain versions, separate Editor/Reviewer/Approver duties, and
deploy only an approved revision. It does not depend on AGPM itself.

## Why the publisher is a separate system

A GPO has two coordinated halves:

- the Group Policy Container (GPC), an object in Active Directory; and
- the Group Policy Template (GPT), a file tree in SYSVOL.

The directory object includes the file-system path, enabled flags, CSE extension
lists, and an AD version. `gpt.ini` carries a file-system version. User changes
increment the upper 16 bits and computer changes increment the lower 16 bits.
The supported Windows management stack coordinates these details; direct LDAP
and SMB mutation would make GPO Studio responsible for recreating a distributed
protocol and every CSE's metadata behavior.

Therefore:

- use the Windows GroupPolicy module and GPMC interfaces for production writes;
- do not directly edit a live `Registry.pol`, `gpt.ini`, GPC attribute, or GPP
  XML file over LDAP/SMB;
- pin each publication attempt to one writable domain controller through the
  supported API's server option;
- treat replication as asynchronous verification, never as part of an atomic
  commit claim.

Microsoft references:

- [GPMC prerequisites and delegated permissions](https://learn.microsoft.com/en-us/windows-server/identity/ad-ds/manage/group-policy/group-policy-management-console)
- [GPO container attributes and versioning](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-gpod/d360d288-d7d5-49a9-83be-603805da1379)
- [GPO file-system version update](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-gpol/59bb540a-64f4-4c52-9c55-5ca2fd2c0270)
- [GPO version-number update requirements](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-gpol/70fd86b1-926a-4dcf-9ce7-6f9d2149c20d)
- [GPMC COM interface catalogue](https://learn.microsoft.com/en-us/previous-versions/windows/desktop/gpmc/gpmc-interfaces)

## Deployment modes

Live publication is an installation-time feature with three modes:

| Mode | Behavior | Intended use |
|---|---|---|
| `offline` | No publisher configuration or publish routes | Default, labs and analysis |
| `plan-only` | Signed artifact and scripts may be downloaded | Human-run change process |
| `managed` | Approved artifacts may enter a publisher queue | Mature controlled environments |

Moving to a stronger mode requires a deployment administrator. A workspace
author cannot enable it. The UI must always show the active mode and target
forest/domain; it must never make a plan-only export resemble a completed live
deployment.

## Components

### Control plane

The existing FastAPI service remains unprivileged and may run on Linux. Before
managed publication it needs:

- OIDC or integrated Windows authentication;
- server-derived actor identity (never an `actor` string from request JSON);
- an authorization policy engine scoped by domain, GPO, OU/site, setting class,
  environment, and operation;
- immutable proposal and approval records;
- canonical artifact creation, hashing, and signing;
- a durable job queue containing artifact references, never credentials;
- read-only target inventory from a separate collector.

It must not contain LDAP bind credentials, gMSA material, arbitrary PowerShell,
WinRM credentials, or a writable SYSVOL mount.

### Approval signer

Approval binds all of the following into one signed statement:

- unsigned payload SHA-256 digest and schema version;
- target forest, domain, and target GPO GUID (or `create-new` intent);
- exact allowed operation kinds;
- expected live-state fingerprint;
- approver identities and policy decision;
- issue/change-ticket reference;
- earliest execution time, expiry, and optional change window;
- publisher audience and single-use job ID.

An approval is invalid after any artifact edit. Production should require two
distinct humans for risky changes, and always require `author != approver`.
Signature keys belong in an OS/HSM-backed keystore separate from the publisher
identity. Key rotation and revocation must be supported from the first managed
release.

### Windows publisher

Run a small service on a dedicated, domain-joined Windows member server with
the Group Policy Management feature installed. Prefer a compiled .NET service
that invokes a fixed adapter library. If PowerShell is used internally, invoke
only constructed commands from typed values in a constrained runspace. Never
execute a script supplied by an artifact.

The publisher:

- establishes outbound-only mTLS communication to claim work;
- verifies artifact signature, schema, audience, expiry, and job uniqueness;
- validates every operation against its local allow-list;
- reads current state and recomputes the precondition fingerprint;
- makes a local protected backup and durable journal before mutation;
- uses supported GroupPolicy/GPMC operations against a pinned DC;
- verifies each step and the final semantic result;
- emits an append-only result record and Windows Event Log events;
- has no interactive logon, RDP, general WinRM, or inbound web listener.

One publisher installation serves one trust boundary. Do not use a single
highly privileged worker across unrelated forests.

## Identity and least privilege

Use group managed service accounts (gMSAs) so the control plane never handles a
password. Deny interactive and remote-interactive logon and restrict where each
gMSA can run.

“Full write” should not mean one omnipotent identity. Define publisher
capability profiles, ideally backed by separate gMSAs or isolated JEA endpoints:

| Profile | Rights | Explicitly lacks |
|---|---|---|
| Settings editor | Read + edit settings on allow-listed GPOs | Link, delete, create, modify security |
| SOM linker | Link GPO permission on allow-listed OUs/sites/domain | GPO settings and ACL modification |
| Creator | Create GPO plus initialize only | Existing-GPO modification, link, delete |
| Security manager | Modify GPO permissions/filtering | Settings, link, delete |
| Lifecycle manager | Disable/quarantine; eventual delete | Settings and security edits |

GPMC distinguishes GPO edit rights from the permission to link on a site,
domain, or OU. Preserve that distinction. Do not make the publisher a Domain
Admin, Enterprise Admin, or unrestricted member of Group Policy Creator Owners.
Permission discovery must be a preflight check, not a reason to elevate.

The GroupPolicy module exposes permission levels such as `GpoEdit`,
`GpoEditDeleteModifySecurity`, `SomCreateGpo`, and `SomLink`; use these as the
starting vocabulary for capability mapping rather than inventing a single
“GPO admin” bit. See
[`Set-GPPermission`](https://learn.microsoft.com/en-us/powershell/module/grouppolicy/set-gppermission?view=windowsserver2025-ps).

## Typed publication artifact

The publisher accepts canonical JSON, not PowerShell. A conceptual envelope:

```json
{
  "schema_version": 1,
  "job_id": "job-018f",
  "created_at": "2026-07-11T20:00:00Z",
  "target": {
    "publisher_audience": "publisher-production-a",
    "forest": "example.test",
    "domain": "example.test",
    "intent": "existing",
    "gpo_guid": "11111111-2222-3333-4444-555555555555",
    "dc_selector": "site:admin-site"
  },
  "precondition": {
    "collected_at": "2026-07-11T19:55:00Z",
    "semantic_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
    "user_version": { "ad": 12, "sysvol": 12 },
    "computer_version": { "ad": 31, "sysvol": 31 },
    "links_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
    "acl_sha256": "0000000000000000000000000000000000000000000000000000000000000000"
  },
  "operations": [
    {
      "kind": "registry.set",
      "operation_id": "op-1",
      "reason": "Enable the approved example control",
      "side": "computer",
      "key": "HKLM\\Software\\Policies\\Example",
      "name": "Enabled",
      "registry_type": "DWord",
      "value": 1
    }
  ],
  "approval": {
    "payload_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
    "policy_version": "production-v1",
    "not_before": "2026-07-12T01:00:00Z",
    "expires_at": "2026-07-12T02:00:00Z",
    "signatures": [
      {
        "key_id": "approval-key-1",
        "approver_subject": "oidc-subject-123",
        "algorithm": "Ed25519",
        "value": "base64-signature-placeholder"
      }
    ]
  }
}
```

The closed initial design contract is checked in as
[`publisher-job.schema.json`](spec/publisher-job.schema.json). It is a review
artifact, not yet an executable wire contract; the worker must not accept it
until canonicalization, signature, authorization, and negative-test suites are
implemented.

The signed payload is the complete top-level object **excluding** `approval`.
Canonicalize that payload using
[RFC 8785 JSON Canonicalization Scheme](https://www.rfc-editor.org/rfc/rfc8785.html),
hash the canonical UTF-8 bytes with SHA-256, and bind `payload_sha256` plus the
approval metadata into each signature. This avoids a self-referential digest.
The implementation must use one precisely specified signature input structure
and published test vectors; concatenating fields informally is forbidden.
QWORD values are canonical unsigned decimal strings in the wire contract,
because RFC 8785/I-JSON cannot losslessly represent every 64-bit integer as a
JSON number. The publisher parses them with an explicit unsigned 64-bit bound.

Rules:

- reject unknown fields and unknown operation kinds;
- cap artifact, string, list, and embedded-file sizes;
- normalize GUIDs, DNs, registry paths, SIDs, and Unicode before hashing;
- disallow relative paths, device paths, ADS names, UNC paths except in fields
  whose typed policy explicitly permits them, and archive path traversal;
- never deserialize language-native objects or accept command fragments;
- preserve unknown imported CSE data byte-for-byte unless that CSE is being
  edited by a supported adapter;
- make every operation independently identifiable and idempotent.

## State fingerprint and optimistic concurrency

The browser's workspace revision is not a sufficient production precondition.
At proposal time, capture a live baseline and compute a semantic fingerprint of:

- GPO GUID, display name, status, WMI filter reference, and modification time;
- AD and SYSVOL user/computer version pairs;
- normalized settings and CSE extension lists;
- security descriptor and security filtering;
- every link target, enabled/enforced state, and order;
- relevant SOM inheritance flags;
- the pinned target DC and collection timestamp.

Immediately before writing, the publisher reads the same fields from its pinned
DC. Any mismatch puts the job in `diverged`; it does not “merge whatever is
there” and does not silently regenerate approval. An author must rebase the
draft, review the new diff, and obtain fresh approval.

Version equality is necessary but not sufficient. Semantic hashing protects
against unusual changes that preserve or wrap counters and against incomplete
inventory. Conversely, hashes should exclude irrelevant timestamps and
serialization order so equivalent state compares equal.

## Publication state machine

```text
draft → proposed → preflighted → approved → queued → leased
                                                    │
                   ┌────────────────────────────────┤
                   ▼                                ▼
                diverged                       executing
                                                    │
                     ┌───────────────┬──────────────┼──────────────┐
                     ▼               ▼              ▼              ▼
                  succeeded      rolled_back   partial/manual   cancelled*
```

`cancelled` is allowed only before the first mutation. Once execution begins,
“cancel” means finish the current supported operation and enter recovery; it
must not kill the process between GPC and GPT updates.

Every transition is append-only and includes actor/service identity, time,
prior-state hash, resulting-state hash, publisher host, DC, operation index,
and error details with secrets redacted.

## Execution protocol

### Common preflight

1. Verify signature, approval policy, expiry, job uniqueness, and local target
   allow-list.
2. Select one writable DC and record its identity. Do not fail over mid-job.
3. Check DC health, SYSVOL reachability, time skew, disk space, and required
   GroupPolicy/GPMC versions.
4. Read and compare the complete expected-state fingerprint.
5. Resolve every target DN, SID, WMI filter, file reference, and ADMX/CSE
   dependency without mutation.
6. Evaluate risk policy and change-window rules.
7. Create a `Backup-GPO` backup and hash it. Separately capture links and SOM
   inheritance because they are external to the GPO and are not restored with
   it. Also capture WMI association, status, ownership, and ACL independently
   so restore/import behavior can be verified rather than assumed.
8. Flush the durable local journal before the first write.

Microsoft's `Backup-GPO` and `Import-GPO`/`Restore-GPO` are the supported
backup/import primitives. `Import-GPO` supports migration tables for principal
and UNC mappings. References:
[`Backup-GPO`](https://learn.microsoft.com/en-us/powershell/module/grouppolicy/backup-gpo?view=windowsserver2025-ps),
[`Import-GPO`](https://learn.microsoft.com/en-us/powershell/module/grouppolicy/import-gpo?view=windowsserver2025-ps).
GPMC documents that restore includes the original GPO identity, settings, and
ACL, but cannot restore SOM links; import transfers settings only and leaves the
target identity, ACL, WMI association, and links unchanged:
[`GPO operations supported by GPMC`](https://learn.microsoft.com/en-us/previous-versions/windows/desktop/gpmc/gpo-operations-supported-by-the-gpmc).

### New GPO

The safest path is additive:

1. Create the GPO unlinked. `New-GPO` creates an unlinked object by default.
2. Record the actual GUID assigned by AD; do not assume a workspace GUID can be
   forced onto a new production GPO.
3. Disable both sides while populating when supported by the chosen adapter.
4. Apply settings, CSE metadata, filtering, and delegation.
5. Read back and validate the complete GPO.
6. Set intended side status.
7. Create links last, narrowest/lowest-risk targets first.
8. Read links and policy back from the same DC, then mark locally committed.
9. Verify replication asynchronously before declaring estate convergence.

[`New-GPO`](https://learn.microsoft.com/en-us/powershell/module/grouppolicy/new-gpo?view=windowsserver2025-ps)
documents the unlinked default and the actual returned GPO identity.

### Existing GPO

There is no claim of atomic visibility across all settings and replicas:

1. Recheck the fingerprint after backup.
2. Apply setting operations through supported APIs, grouped by side/CSE.
3. Verify each group before moving on.
4. Apply WMI and security filtering only after settings validate.
5. Apply link changes last. Link reordering must use a complete desired order
   for the target, because changing one link affects precedence among others.
6. Read back a semantic report and compare it to the approved desired state.
7. If an operation fails, stop forward progress and run compensation.

For a broad replacement, importing an approved, complete GPMC backup into the
existing target can be safer than hundreds of individual mutations, but only
after the backup writer has passed Windows round-trip testing for every present
CSE. Never relabel the current partial Studio bundle as a complete GPMC backup.

### Links, ACLs, and deletes

- Link updates require separate authorization on each target SOM. A valid GPO
  approval does not imply authority to link it.
- Enforced links, domain-root links, site links, DC OU links, and block
  inheritance changes always require enhanced approval.
- ACL edits require a before/after effective-rights report and lockout checks.
  Refuse removal of the publisher's recovery access in the same job.
- “Delete” is a lifecycle: disable/unlink, quarantine for a configurable period,
  then hard-delete under a new approval. Default Domain Policy and Default
  Domain Controllers Policy are permanently deny-listed for deletion.
- A hard delete is never an automatic rollback action.

## Rollback and crash recovery

AD plus SYSVOL does not offer the publisher a cross-store transaction. Use a
saga with explicit compensation and do not promise rollback where the evidence
cannot prove it.

| Failure point | Required behavior |
|---|---|
| Before first write | Mark failed/diverged; production unchanged |
| During supported setting operation | Stop, inspect state, restore backup to the same GUID, verify |
| During link mutation | Restore the captured complete link order/state on every touched SOM |
| During ACL/filter mutation | Restore captured descriptor/filter, then verify effective rights |
| Publisher crash | Lease expires; same host recovers from local journal and observes state before acting |
| DC/SYSVOL unavailable after write | Enter `partial/manual`; never retry blindly against another DC |
| Compensation fails | Enter `partial/manual`, page an operator, preserve all artifacts and evidence |

The queue must not redeliver an executing job to another worker as if it were
new. On restart, a worker loads its durable journal, recomputes live state, and
chooses one of: prove operation complete, safely continue at the next step,
compensate, or stop for manual intervention.

Rollback success means the captured semantic state, links, ACLs, filters, and
versions have been read back and verified—not merely that `Restore-GPO` returned
without throwing.

## Risk policy

Classify both the setting and its blast radius. Examples requiring enhanced
approval or an explicit maintenance window:

- Default Domain Policy / Default Domain Controllers Policy;
- links at a forest site, domain root, or Domain Controllers OU;
- enforced links, block inheritance, or link-order changes;
- security filtering, delegation, ownership, WMI filters, and loopback;
- scripts, scheduled tasks, services, software deployment, local groups, user
  rights, firewall, Defender exclusions, certificate auto-enrollment, LAPS,
  credential delegation, audit policy, and authentication settings;
- settings containing a UNC path or executable reference;
- policy affecting more than an operator-configured object/device threshold;
- deletion, mass unlink, or disabling either side of a deployed GPO.

Policy rules are versioned code. Their version and decision trace become part
of the signed approval. A policy engine outage fails closed.

## CSE support and the meaning of “full write”

PowerShell covers GPO lifecycle, reports, backup/import/restore, links,
permissions, registry policy, and registry preferences. GPMC exposes broader
management COM interfaces, but GPMC historically launches the Group Policy
Object Editor for individual setting editors. There is no single supported
generic `Set-GPOSetting` API for every client-side extension.

Implement adapters, not generic file writes:

The current API-to-capability mapping is maintained in
[`write-interface-matrix.md`](write-interface-matrix.md).

| Adapter state | Publication behavior |
|---|---|
| `read-write-verified` | Typed authoring, Windows round-trip, positive client application test |
| `import-preserve` | May carry an unchanged CSE through a complete backup/import |
| `read-only` | Display and diff only; block edits |
| `unknown` | Preserve bytes where possible; block publication if a rewrite could lose data |

An adapter earns `read-write-verified` only after it passes:

1. schema/serializer unit tests;
2. GPMC open/edit/report round-trip without normalization surprises;
3. backup/import/restore into a fresh GPO;
4. client-side `gpupdate` and Resultant Set verification;
5. removal/Not Configured behavior;
6. mixed-CSE preservation tests;
7. rollback and crash-injection tests.

Never store or generate Group Policy Preferences `cpassword`. Reject it during
import and publication.

## Observability and evidence

Emit structured records for proposal, approval, lease, every operation,
compensation, and verification. Required fields include:

- job, proposal, workspace revision, artifact, and approval IDs/hashes;
- human and service identities;
- target forest/domain/GPO/SOM and publisher/DC identities;
- before, desired, after, and rollback semantic hashes;
- backup hash and protected location identifier;
- exact typed operation and supported adapter version;
- Windows correlation/activity ID, duration, result, and sanitized error;
- replication verification status by DC.

Send records to Windows Event Log and an append-only remote sink. The natural
family integration is regista for hash-chained provenance, with gpo-lens doing
an independent post-publication export and semantic verification. The writer
must not be its own only auditor.

## Security controls

- TLS is necessary but not sufficient: use mutual authentication, audience-
  restricted signed artifacts, job expiry, nonce/job replay protection, and
  publisher-side policy.
- No arbitrary PowerShell, script text, LDAP filter, XPath, archive path, or CSE
  identifier from the browser reaches an execution primitive.
- Treat ADMX/ADML, GPO backups, scripts, installers, and preference XML as
  untrusted input. Scan, size-limit, canonicalize, and preserve provenance.
- Embedded artifacts use content-addressed storage and allow-listed types.
  Executables/scripts require signatures and a separate approval.
- Publisher egress is restricted to domain services, control-plane queue, time,
  update, and audit endpoints. Inbound traffic is denied.
- Logs redact values marked sensitive. Policy settings must never become a
  general secret-distribution mechanism.
- Patch the dedicated host; enable Defender, application control, PowerShell
  logging if PowerShell is used, and central alerting.
- Rate-limit and serialize writes per GPO and per SOM. One GPO cannot have two
  active publication leases.

## Required test estate

Do not ship managed mode based on mocks or Samba alone. Maintain ephemeral,
resettable Windows labs with:

- supported Windows Server/DC versions and forest functional levels;
- a domain-joined publisher host with the exact production hardening;
- clients representing supported Windows versions;
- multi-DC replication, intentional latency/failure, DFSR pause, and DC loss;
- least-privilege positive and negative permission cases;
- concurrent GPMC edit races;
- every supported CSE and every value/action variant;
- crash injection before/after each durable journal boundary;
- malicious artifacts, signature replay, stale approvals, archive traversal,
  oversized values, and Unicode normalization cases;
- canary OU publication followed by `gpupdate`, report/RSoP checks, gpo-lens
  export, rollback, and convergence verification.

Release evidence should include the compatibility matrix, test artifact hashes,
and a successful restore drill. “The cmdlet returned zero” is not sufficient.

## Rollout phases and hard gates

### Phase 0 — plan-only hardening

- Replace request-supplied actor with authenticated identity.
- Canonical artifact schema, semantic diff, hashes, signatures, and approvals.
- Complete GPMC backup import/export for the first supported adapters.
- No managed publisher.

### Phase 1 — read-only publisher

- Deploy the Windows service with no write rights.
- Exercise mTLS, queue leases, target selection, preflight, fingerprints,
  backups, reports, journaling, and audit for at least one normal change cycle.
- Prove that compromise of the control plane cannot make the publisher execute
  an unsigned or out-of-policy operation.

### Phase 2 — canary creation

- Grant only create/populate rights in a dedicated lab domain/OU.
- Allow new, unlinked GPOs; no existing-GPO edits or links.
- Require human GPMC comparison and deletion after validation.

### Phase 3 — production canary

- Allow a small setting adapter set on allow-listed canary GPOs and OUs.
- Two-person approval, change window, backup, client verification, and automatic
  stop on any divergence.
- Run long enough to cover crash recovery and a real restore drill.

### Phase 4 — bounded production

- Expand adapter-by-adapter and capability-profile-by-profile.
- Security/ACL, domain-root link, and deletion profiles remain separately
  disabled until their own gates pass.

### Phase 5 — broad compatibility

- Claim only the matrix rows actually verified. “Full write” means the platform
  can orchestrate every supported GPMC lifecycle operation; it does not mean
  unknown third-party CSEs can be safely synthesized.

## Non-negotiable go-live checklist

Managed publication stays disabled unless all answers are yes:

- Is the web/control plane free of domain write credentials?
- Is actor identity authenticated and authorization target-scoped?
- Is author/reviewer/approver separation enforced for production?
- Is approval cryptographically bound to exact desired and expected state?
- Does the publisher reject unknown, unsigned, expired, replayed, and diverged
  work locally?
- Are all operations typed and allow-listed, with no arbitrary execution path?
- Is the worker non-admin and least-privileged for the requested capability?
- Is the backup plus external link/ACL/filter snapshot complete and verified?
- Has crash recovery been tested at every mutation boundary?
- Is `partial/manual` surfaced and paged without blind retry?
- Has the adapter passed Windows/GPMC/client round-trip tests?
- Are high-blast-radius targets deny-listed or enhanced-approved?
- Can an independent collector verify the resulting estate?
- Has a restore drill succeeded in the target class of environment?

If any answer is no, plan-only mode is the responsible product behavior.
