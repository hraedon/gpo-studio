# Publisher threat model

This document scopes the optional managed-publication subsystem proposed in
[`live-publication.md`](live-publication.md). The current GPO Studio release has
no publisher and therefore no domain-write credential.

## Assets

- Integrity and availability of domain Group Policy.
- AD/SYSVOL consistency and replication health.
- Publisher gMSA authority and signing keys.
- Approval integrity and separation of duties.
- GPO backups, drafts, embedded scripts/installers, and audit history.
- Identity of authors, reviewers, approvers, and publishers.

## Trust boundaries

1. Browser to control plane.
2. Control plane to identity provider and approval signer.
3. Control plane/queue to Windows publisher.
4. Publisher to AD DS, SYSVOL, and each selected DC.
5. Publisher to backup storage and remote audit sink.
6. Imported ADMX, GPMC backups, and embedded artifacts to every parser.

## Principal threats and required mitigations

| Threat | Consequence | Required controls |
|---|---|---|
| Web compromise submits a malicious job | Domain-wide policy compromise | Publisher verifies independent signature, target allow-list, operation policy, expiry, and expected state |
| Stolen author session self-approves | Unauthorized deployment | Distinct roles, `author != approver`, phishing-resistant MFA, two approvers for high risk |
| Queue message replay | Repeated or delayed mutation | Globally unique job ID, nonce, single-use durable ledger, short expiry |
| Artifact swapped after approval | Reviewed diff differs from execution | Canonical digest in every approval signature; publisher recomputes it |
| Arbitrary PowerShell injection | Publisher host/domain takeover | Typed operations only; no script evaluation; constructed API calls; constrained worker |
| Zip/path traversal or parser bomb | Host overwrite/DoS | No extraction by path, size/count/depth caps, safe parsers, content-addressed blobs |
| Overprivileged service account | Forest compromise after worker breach | Separate capability identities, per-GPO/SOM delegation, no DA/EA, deny interactive logon |
| Confused deputy targets another domain/GPO | Cross-boundary write | Signature audience includes publisher/domain/GPO; local allow-list; normalize and re-resolve IDs |
| TOCTOU after review | Overwrites an administrator's edit | Complete live fingerprint immediately before write; diverge and require reapproval |
| Crash between GPC/GPT or link steps | Partial production state | Supported APIs, pinned DC, durable step journal, observation before retry, compensation, manual terminal state |
| Malicious/compromised DC | False state or bad writes | DC allow-list and health, signed audit, post-write checks from independent collectors/other DCs |
| Rollback artifact tampering | Recovery installs malicious state | Protected storage, content hash, encryption/access control, restore verification |
| Audit deletion | Changes become deniable | Local Windows log plus append-only remote/hash-chained sink |
| GPP secret material imported | Credential disclosure | Reject `cpassword`; secret scanning; sensitive-field classification |
| Dangerous but syntactically valid policy | Broad outage or weakened security | Blast-radius/risk policy, enhanced approval, canary, maintenance window, deny-list |
| Privilege escalation through embedded script/MSI | Code execution on clients | Signed content, provenance, malware scan, separate artifact approval and setting-class authorization |
| Link-order race | Unexpected precedence | Lock per SOM, fingerprint complete ordered link set, write/verify complete desired order |
| Replication lag interpreted as failure | Unsafe repeated write | Never repeat mutation on another DC; track asynchronous convergence separately |

## Abuse cases to test

- Submit a valid artifact under a different GPO, domain, publisher, or approval.
- Change Unicode normalization or JSON ordering without changing visible text.
- Reuse a completed job or an approval after expiry.
- Race a native GPMC edit between preflight and execution.
- Crash before and after every mutation and journal flush.
- Remove the worker's own recovery permission in an ACL change.
- Create two link jobs that reorder the same SOM.
- Insert `../`, absolute, UNC, device, ADS, and symlink-like archive paths.
- Send unknown CSEs, unknown JSON fields, extreme nesting, huge binaries, and
  malformed Registry.pol/GPP XML.
- Hide `cpassword` using case, namespace, encoding, or archive nesting tricks.
- Attempt domain-root/DC-OU/enforced links through a low-risk approval route.
- Make the selected DC unavailable after the first successful write.
- Cause backup success but link/ACL snapshot failure; execution must not start.
- Compromise the queue/control plane and prove unsigned jobs remain inert.

## Residual risks

Even with these controls:

- a correctly approved policy can cause an outage;
- Microsoft and third-party CSEs may have behaviors not captured by static
  validation;
- replication and client application are eventually consistent;
- restoring GPO content does not undo effects already applied to clients;
- a compromised publisher with valid delegated authority can abuse that scope;
- a malicious quorum of authorized approvers can authorize a malicious change.

The operating model must therefore retain canaries, maintenance windows,
endpoint telemetry, independent verification, privilege review, key rotation,
and practiced incident response.

