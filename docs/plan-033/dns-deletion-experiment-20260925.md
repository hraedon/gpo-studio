# The DC-locator DNS deletion, instrumented (2026-09-25)

The follow-up to the 2026-09-10/11 WI-062 batch's unidentified deletion
mechanism (`wi062-batch.md`, "The estate repair this batch needed") and to the
2026-09-18 analysis (`C:\projects\gpo-studio-dns-mystery-20260918.md` on the
workstation, which refuted scavenging on four grounds and prescribed this
experiment): one instrumented restore/jump/observe cycle on the
windows-console-driver estate, designed to turn "mechanism not identified"
into a named process.

## Method (E1 from the analysis, plus its instrumentation)

The analysis's E1 — isolate the actor: restore the DC, jump the clock, then
do NOTHING else. No `dsregdns`, no NetLogon restart, no member work, for the
full observation window. Whatever happens, happens inside the DC alone.

- **Restore.** LabDC01 restored to its `domain-joined` checkpoint (minted
  2026-09-20 18:30:06Z) and booted. Guest answered PSDirect as
  `ad.labdomain.dev\Administrator` with clock **2026-09-20 18:29:12Z** vs host
  **2026-09-25 11:42:20Z** — the checkpoint-era state, −417,588 s.
- **Baseline captured before anything was armed** (both DNS views):
  36 records across the two zones (24 in `ad.labdomain.dev`, 12 in
  `_msdcs.ad.labdomain.dev`), 35 + 11 `dnsNode` objects, every dynamic record
  timestamped 2026-09-20 06:00Z, all controls OFF/static: server scavenging
  `False`, no zone aging on any zone, `labdc01` A records static. The
  analysis's §2.1 refutation holds on this generation too.
- **Instrumentation armed on the DC, post-restore, pre-jump** (all readback
  verified):
  - DNS server debug logging: Server 2025's `Set-DnsServerDiagnostics`
    dropped the doc-era `-UpdatePackets` parameter name (it is `-Updates`
    now) and refuses packet flags without `-QuestionTransactions` — both
    measured this session. Armed: updates + send/receive/full packets +
    question transactions + zone-data-write, tombstone, server-start/stop,
    zone-load events, log at `C:\Windows\System32\dns\dns.log`.
  - SACLs: `Everyone` / `Delete, DeleteTree, WriteProperty, WriteDacl` /
    Success+Failure / `All` inheritance on both `DomainDnsZones` and
    `ForestDnsZones` partition heads (the `ActiveDirectoryAuditRule` ctor
    surface on PS 5.1 required the 4-arg inheritance form + `Get-Acl -Audit`;
    readback one rule per partition).
  - `auditpol`: `Directory Service Access` and `Directory Service Changes`,
    Success and Failure.
- **The jump.** `Set-Date` from host UTC (tz-safe offset form) at
  **2026-09-25 11:47:33Z**, forward **+407,587 s** (4.72 days) — the same
  magnitude class as the WI-062 reproductions (~6 days) and the window-8
  occurrence (~7 h).

## Observations

T+0 = 11:47:33Z (the jump). All times UTC.

**E1 result: the records did not die.** Through T+92 the record set is
byte-for-byte the baseline — same 36 records, same owners, same types, same
2026-09-20 06:00Z timestamps — in both the DNS view and the AD `dnsNode`
view (35 + 11 nodes). Intermediate probes at T+32 and T+60 agree.

Three independent instruments all say "nothing happened":

1. **The directory audit stream: zero events.** `Directory Service Access`
   and `Directory Service Changes` armed Success+Failure, SACLs on both DNS
   partition heads covering Delete/DeleteTree/WriteProperty/WriteDacl with
   All inheritance — and **zero 5136/5137/5138/5139/5141 events** since the
   jump. Nothing wrote to or deleted from the DNS partitions at all.
2. **The DNS debug log: registration ran, and it never deleted.** 52 UPDATE
   packets in the window, exactly two bursts — **T+2 (11:49:52) and T+62
   (12:49:52)**, the hourly Netlogon registration pass — sourced from the
   DC's own two addresses (10.10.10.10 lab-isolated, 192.168.100.10
   compute-mgmt). Every packet **NOERROR**; the update sections carry the
   full locator record set as refreshes; and **the log contains zero delete
   signatures** (no `CLASS 254`/NONE, no `TYPE 255`/ANY entries anywhere).
   The full log is preserved at `C:\temp\lab\dns-exp-jump-window.log` on the
   controller (88,201 bytes).
3. **NETLOGON: zero events** (5774-5782) since the jump — no registration
   failures, no deregistrations.

**Gesture hunt (instrumentation still armed, E1's "do nothing" phase over):**

- **G1 — `Restart-Service NetLogon` at 13:20:45Z** (the analysis's mechanism
  2b: the documented 5775 deregistration-at-stop path): records intact 10
  minutes later, same counts, same names. **The NetLogon restart does not
  delete them.**
- **G2 — `nltest /dsregdns` at 13:31:12Z** (`NERR_Success`, "The command
  completed successfully"): records intact 10 minutes later, 35 + 11 nodes.
  **The force-re-registration gesture does not delete them either.**

**The complete wire record:** 156 UPDATE packets across the whole experiment
(two hourly passes at T+2 and T+62, the G1 restart's registration burst at
13:20, G2's burst at 13:31), every one **NOERROR**, and **zero delete
signatures in the 6,456-line log** (no `CLASS 254`/NONE, no `TYPE 255`/ANY).
The captured window through T+92 is preserved at
`C:\temp\lab\dns-exp-jump-window.log` on the controller (88,201 bytes); the
post-T+92 bursts are counted and verified delete-free in the closing read,
but their raw lines were not preserved (disarming debug logging wraps the
log — measured at close, 1,052 bytes fresh).

## What it means for the group-deny lane

Measured strength only, n=1, one generation:

- **The forward jump alone is exonerated on this baseline generation.** The
  analysis's mechanism 2.2 — a self-scheduled overdue-timer deletion storm
  triggered by the jump — did not occur in 92 minutes with zero external
  intervention: no deletes on the wire, no writes in the directory, no
  NETLOGON events. Mechanism 2b's NetLogon-restart variant is exonerated the
  same way (10 minutes post-restart, records intact).
- **The surviving suspects for the 2026-09-10/11 reproductions** are
  therefore: (a) something specific to the `estate-current-20260905`
  generation those repros ran on — its mint **predates the window-9
  clock-seed discipline** (the 2026-09-20 generation followed it), so the
  old baseline's internal timer state at mint time differs in exactly the
  way the analysis's prevention #1 predicts; or (b) the surrounding
  endpoint-lane activity (member policy processing and client reboots
  against the jumped DC), which no DC-local instrument can reproduce alone;
  or (c) a gesture combination not tested here.
- **Practical consequence: the group-deny lane's retry no longer needs to
  fear the restore/jump itself.** The lane runs on the 2026-09-20 generation
  with clock-seeded baselines; if the deletion mechanism was
  generation-specific, the retry is simply safe, and if it was
  lane-activity-specific, the retry IS the instrumented experiment (the
  canary's dc_locator check red-lines the failure mode the moment it
  appears). Either way the next retry produces knowledge.

## Estate disposition

Closed healthy: debug logging and DS audit policy disarmed (both restored to
their off-state, readback verified), records 24 + 12 intact, DC at real time,
and the windows-console-driver canary green 8/8 from mvmcc02 (exit 0) —
`dc_locator` and `kerberos` both green, which is precisely the failure pair
the historical deletion produced. SACLs on the two DNS partition heads are
left in place (inert with auditing off; they ride only this running instance
and die at the next checkpoint restore). All lab guests Running, CAroot Off,
as found. Server-2025 measurement notes for future instrumentation windows,
all hit this session: `Set-DnsServerDiagnostics` renamed `-UpdatePackets` to
`-Updates`; packet flags require `-QuestionTransactions` (or `-Answers`) set;
an all-off disable is rejected the same way (`-DebugLogging $false` is the
master switch); and the service holds an exclusive lock on `dns.log` — a
shared `FileStream` reads it, `[IO.File]::ReadAllBytes` does not.
