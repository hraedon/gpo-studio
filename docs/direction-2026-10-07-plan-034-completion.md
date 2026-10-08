# Direction: finish Plan 034 and ship 1.1.0 by 2026-10-31

**Operator rulings, 2026-10-07.** These supersede the *timing* half of
[`direction-2026-08-06`](direction-2026-08-06-reconciliation-and-lab-handover.md)
Ruling 1 ("we're not in a rush to release"). That ruling's substance still
stands: the goal is reconciling the post-1.0 layers, and
[`domain-layer-status.md`](domain-layer-status.md) still governs how a layer
stops being a draft.

## Ruling 0 — the target

> "Shippable release certainly, but finishing out plan 034 is the goal."

There is a deadline: **2026-10-31**. Every module Plan 034 lists exits in one of
two ways. Either it reaches `yes`, meaning a re-runnable lane and then a
delivery surface, in that order. Or it gets a recorded out-of-scope ruling. Plan
034's review gate is unchanged: **no module leaves this plan
`capture-backed`.** The release that carries the result is 1.1.0.

The estate and `mvmcitest01` are available for the duration. Estate access
stays serial between gpo-studio and windows-evidence-lab. Lanes run from the
controller host that holds the `cred:lab-*` capabilities.

## Rulings on the individual exits

The numbers in the table below are the order the work happens in, not
priorities.

| Module | Ruling | Exit |
|---|---|---|
| `publication.py` | **Retire the PowerShell script branch.** `generate_publication_script`, `PowerShellPublicationScript`, `_WINDOWS_VERIFIED_OPERATIONS` and their helpers are deleted. They copy files straight into SYSVOL, which [`live-publication.md`](live-publication.md) forbids. Publication goes through the native GPMC backup and `Import-GPO` plus `apply.ps1`, and nothing else. | `yes`: the existing completeness lane plus a read-only publication-plan surface |
| `script_policy.py` | The stale pre-R2 `scripts.ini` writer and parser are deleted with the branch above. The certified writer is the one in `export.py`. | `yes`: the existing metadata lane plus a Scripts export surface |
| `artifact_store.py` | **Delete.** Delivering script or executable payloads is out of scope for 1.x. The module is decoupled from `publication.py` and `script_policy.py` inside the requalification batch. | ruling (deleted) |
| `software_install.py` | **Delete.** Writing was already ruled out ([2026-09-06](scope-decision-2026-09-06-software-installation-and-certification.md)). The module has no consumer, and native content is kept by `cse_metadata`, not by this module. | ruling (deleted) |
| `folder_redirection.py` | **Delete.** Superseded by `fdeploy.py` ([2026-09-11](scope-decision-2026-09-11-folder-redirection.md)). | ruling (deleted) |
| `fdeploy.py` | **Build a lane.** Banked R3 bytes go through `Import-GPO`, then `Backup-GPO`/`Get-GPOReport`, and the result is compared with the parse. R12 is attempted through the console driver so that WI-066 can be answered. | `yes`, or the writer stays deferred under WI-066 |
| `gpmc_interop.py` | **Delete** everything except `InteropIssue`, which `publication.py` imports. The "importable" predicate equates *Studio cannot emit this* with *GPMC cannot import this*. `is_gpmc_editable` has no oracle. The R6 fact already lives in `export.py`'s vocabulary, which the publication lane certifies byte for byte. | ruling (reduced to one type) |
| `security_template.py` | **Exits through its consumers.** Three live verdicts bind it, in both directions: emit through every candidate, and decode of Windows-written `secedit /export` bytes in every finalizer. Two endpoints reach it. Reading GPME-authored `GptTmpl.inf` is out of scope until a Security Settings import surface is proposed. | `yes` (via consumers) |
| `network_security.py` | **Firewall: a codec, then a lane, then a surface.** IPsec, Public Key, wired and wireless are **out of scope** for 1.x. None appears in more than 2 of the 26 censused production GPOs, and none has an honest oracle short of a rewrite. If the firewall lane has no verdict by about 2026-10-24, the firewall half is ruled out too. | `yes` for firewall; ruling for the rest |
| `lifecycle.py` | Same-domain lane over `Backup-GPO`/`Restore-GPO`/`Import-GPO`/`Copy-GPO`, then a restore-plan surface. The cross-domain half is out of scope until the estate has a second domain or trust. | `yes` (same-domain); ruling (cross-domain) |
| `backup.py` / `report.py` | A report-parity lane against a fresh `Get-GPOReport -ReportType Xml`, preceded by an offline differ. | `yes` for the families Studio models |
| `publisher.py`, `hosting.py` | **Out of scope for 1.x; code retained** as Milestone 3 seeds. They are not counted as capabilities, and hosting's earlier "harden" verdict is unchanged. | ruling (retained) |

## How the work is ordered

1. **The requalification batch comes first.** WI-063 means that, on `main`, the
   documented entry point of eight lanes does not parse. A lane in that state
   cannot honestly be called re-runnable, so no `yes` can be claimed while it
   is open. Every bound-file edit that this programme can foresee rides the
   same batch: WI-063, WI-064, WI-065, WI-068, the publication and
   script-policy retirements, and the `artifact_store` decoupling. That way
   the estate pays once.
2. **New lanes are new files only.** The firewall, lifecycle, report-parity and
   fdeploy lanes add their own runners, guest scripts, builders and finalizers.
   They change no shared bound file, so they can run on their own schedule
   without expiring anything.
3. **Surfaces follow their verdicts.** Scripts and publication already have
   lanes, so their surfaces can be built now.
4. **Release engineering comes last:** a 1.1.0 evidence manifest, release gates
   that check the version rather than a stale string, and a workspace upgrade
   fixture written by 1.0.0.

Plans 023/024's surfaced-but-unverified modules (`som`, `delegation`,
`ad_discovery`, `wmi_filter`, `gpp_adapters`) are **not** Plan 034 modules.
They are carried into 1.1.0 as stated limitations, unless time remains after
Plan 034 closes.
