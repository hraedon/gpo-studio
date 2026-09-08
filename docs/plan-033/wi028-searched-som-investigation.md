# WI-028: deleted SOMs survive in the computer RSoP namespace

**Status:** investigated on 2026-09-07 (Phoenix; captures cross midnight UTC).
WI-028 is closed under its warning-and-scoping-strategy condition. This is
diagnostic evidence, not a new precedence capability or a lane verdict.

The stale rows exist in `root/rsop/computer:RSOP_SOM`, and a fresh
`gpresult /x /scope:computer` reports them. Deleting an OU and successfully
forcing computer policy does not remove its row on the tested client. This
establishes the retention location and a reproducible persistence sequence;
it does not establish GPSvc's internal purge rules or a supported reset method.

## What was measured

The estate client was Windows 11 Enterprise build 26200. Before the experiment,
AD showed its computer account back in `CN=Computers`, with no residual
`StudioRsop*` or `GPOStudioLab*` OUs. Nevertheless, both the local WMI query and
fresh XML contained six OU rows from the preceding two RSOP runs.

A single empty OU, `WI028-20260908-0003`, was created through the AD cmdlets.
The client's account was moved there; no GPO was created or linked and no
inheritance setting was changed. After a forced computer refresh, both views
were captured. The account was then restored to its exact original DN, the
OU was deleted, and both restoration and absence were re-queried. Another
forced refresh preceded the final capture.

| Capture | WMI SOM rows | XML SOM rows | Probe OU present | Session's current SOM |
|---|---:|---:|---|---|
| Before | 9 | 9 | No | Domain |
| During, after refresh | 10 | 10 | Yes | Probe OU |
| After verified deletion and refresh | 10 | 10 | Yes | Domain |

The probe row's order, reason, type and block flags were unchanged after
deletion. WMI was queried both before and after each XML capture; the two
WMI samples agreed in every capture. The session scope changed with the
client's location, but `Session1` and its creation time remained unchanged
across all three captures. The creation time was from July 29, not this run.

That last distinction is documented by Microsoft: `RSOP_Session.creationTime`
is the time the session or namespace was created. It is not a policy-cycle
timestamp. `RSOP_SOM` exposes identity and reason keys, order, type and block
flags, but no per-row collection timestamp. See Microsoft's
[RSOP_Session](https://learn.microsoft.com/en-us/previous-versions/windows/desktop/policy/rsop-session)
and [RSOP_SOM](https://learn.microsoft.com/en-us/previous-versions/windows/desktop/policy/rsop-som)
class definitions.

## Required scoping strategy for a future SOM lane

**An unfiltered `SearchedSOM` list is historical diagnostic data. Neither a
successful refresh, a fresh XML filename nor the session creation time makes
every row current.** The existing WP-6/WP-9 lanes do not grade this section;
their applied-GPO and registry comparisons remain their evidence paths.

For a future lane to use SOM rows:

1. Assign unique OU identities to each experiment and record their exact DNs,
   directory-provided canonical paths and object GUIDs in the author record.
   Keep XML canonical paths distinct from WMI DNs; never match a substring or
   merely strip the domain from either representation.
2. Capture a baseline before creation and require every experiment OU to be
   absent from both WMI and XML. Reuse of a name or an existing row makes that
   attempt inconclusive; choose a new identity for a new attempt.
3. Record the account's authoritative current DN, actual ancestor topology,
   scope, and policy-processing interval. During the experiment, select only
   exact experiment identities and the appropriate reason (`Normal` versus
   `Loopback`). Require exactly one row per expected identity/reason and
   agreement between the WMI and XML fields. Missing, duplicate or ambiguous
   matches cannot satisfy an assertion.
4. Preserve excluded rows as diagnostics. Shared local/site/domain identities
   cannot be proved fresh by this before/after identity test. Do not grade
   their order or block flags without separate freshness evidence. Existing
   directory objects alone are also insufficient: a retained row may name an
   object that still exists but was not searched in this cycle.
5. Preserve the author, observation and cleanup records separately. A retained
   SOM row is not evidence of failed AD cleanup; verify cleanup against AD.

The experiment proves the value of fresh OU identities for identifying a
run's new rows. It does **not** qualify block/enforcement semantics, changes
within an already-used OU, user scope, loopback reason 2, or shared scope
freshness. Those assertions still need their own live lane. Reboot behaviour,
namespace deletion and WMI repository reset were not tested; no reset is
recommended or built into the collector.

## Evidence and reproduction

[The banked evidence](wi028-evidence/provenance.json) contains exact WMI
snapshot bytes, extracted `ComputerResults/SearchedSOM` rows, the AD author and
cleanup observations, SHA-256s of the original raw captures, and the collector
hash. Full raw XML remains in the private controller archive. The extracted
JSON explicitly names its source XML hash and XPath; it is not represented as
the full XML document. Tests pin these bytes and the before/during/after
observation, not a Windows-wide guarantee.

Run `scripts/plan-033/collect-searched-som.ps1` on the client through the
existing transport with a new `-OutputDirectory` for each capture. Its default
is read-only apart from writing capture files; `-ForceRefresh` explicitly runs
`gpupdate /force /target:computer /wait:120` before collection. It refuses an
existing output directory and failed native commands. Keep raw captures
private pending identifier review.

Reproduce the OU experiment only in the disposable estate: save the computer
object GUID and original parent, create a uniquely named empty OU, move only
that computer, capture during, and restore/delete in a `finally` cleanup.
Re-query the original computer DN and the OU's absence before the after
capture. Stop on unexpected object identity or location changes. No changes to
the existing conformance harness are needed for this diagnostic sequence.
