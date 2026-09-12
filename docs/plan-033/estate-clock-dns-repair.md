# The estate repair the batch owes: clock, DNS, and one unasked question

Status: **open, not diagnosed.** Nothing here is a measurement. This is a plan
for taking one, written offline because the estate is not reachable from the
host this was written on — no `cred:lab-hyperv-control` or
`cred:lab-guest-bootstrap` capability is provisioned here, so
`scripts/windows-oracle/run-*-oracle.sh` would fail before it reached the
Hyper-V host.

It exists because the debt had no number and no plan. The failure is recorded
in one paragraph of [`wi062-batch.md`](wi062-batch.md), referenced from
`environment-spec.md`, `CHANGELOG.md` and WI-062's and WI-063's bodies, and
every one of those is a pointer back to that same paragraph. It is now
**WI-069**, which is where a reader would look.

---

## What is recorded

From [`wi062-batch.md`](wi062-batch.md), verbatim in substance:

- The checkpoint `estate-current-20260905` restores the DC to a frozen clock
  while the clients keep host-synced real time. A ~6-day Kerberos skew between
  client and DC breaks machine policy processing.
- Setting the DC forward to real time — "correctly, verified against the
  controller" — works for minutes and then kills domain-wide DC discovery:
  every dynamic DC-locator record (`_ldap._tcp.dc._msdcs...`, and even the host
  A records) is deleted.
- Reproduced **four times**: with DNS server scavenging disabled, with per-zone
  aging disabled, with lockout threshold zero, and with records force-
  re-registered immediately after the jump.
- The deletion mechanism was not identified.
- Three attempts are archived at `C:\temp\lab\wi062-batch\attempt-archive\` on
  the controller. **Nothing from that archive is committed here**, so this
  repository holds no logs of the failure at all — only the paragraph.

The batch shipped by avoiding the problem: all three guests reverted, client
time-sync disabled before boot, clients aligned behind the DC. That works for
21 lanes and not for the 22nd, because the computer group-deny lane is the one
lane that reboots the client mid-run, and a rebooting client with time-sync
disabled re-anchors ~4 days from the DC.

## The question nobody asked, which is the cheapest one

**How was "deleted" established?**

If it was established through a resolver or through `Get-DnsServerResourceRecord`
— that is, through the DNS service — then "deleted" is a conclusion, not an
observation. Three different states are indistinguishable from there:

| State | What it means | How it looks to `nslookup` |
|---|---|---|
| Absent | the `dnsNode` object is gone from the directory | NXDOMAIN |
| Tombstoned | `dNSTombstoned` is TRUE; the object is still present | NXDOMAIN |
| Present, unserved | the record exists and the DNS service will not answer | NXDOMAIN or SERVFAIL |

The third is not exotic here. An AD-integrated zone is served out of the
directory, and the same Kerberos skew that broke machine policy processing can
break the DNS server's own access to it. A DC whose clock has just jumped is
exactly the machine where "the service cannot read its own zone" is plausible,
and it would present as every record vanishing at once — which is what was
reported, and which a genuine scavenging or tombstone mechanism would be
unlikely to do to *every* record simultaneously.

The discriminator is one LDAP read, and it is read-only: enumerate the
`dnsNode` objects directly. `scripts/plan-033/collect-dc-clock-dns.ps1` does
that and nothing else that writes.

**This does not mean the account is wrong.** It means the account records a
symptom and names a mechanism, and the step between the two was never taken.

## One fact already in the record that constrains the answer

"...and even the host A records."

Netlogon owns the DC-locator SRV records — `netlogon.dns` is its own list of
them. It does **not** own the host A record; that is the DNS client's dynamic
registration. A mechanism that reached both is therefore not a Netlogon
mechanism, which rules out the most commonly assumed cause and points at
something operating on zone objects generally: the directory, the DNS service's
view of it, or a per-object lifecycle.

## Hypotheses, cheapest discriminator first

None of these is asserted. Each is paired with the measurement that would
retire it, which is the only reason to list them.

**H1 — the records are present and the service is not serving them.**
*Discriminator:* the LDAP enumeration above, taken while discovery is broken.
If the objects are there with live `dnsRecord` values, no deletion happened and
every scavenging-shaped hypothesis below is moot. *Cost:* one read.

**H2 — tombstoned-node cleanup, which zone aging does not govern.**
In an AD-integrated zone a deleted record becomes a tombstoned `dnsNode` rather
than disappearing, and tombstoned nodes are reaped on an interval that is
configured separately from zone aging and scavenging. That separation is the
reason this hypothesis survives "scavenging was disabled" — but whether a
forward clock jump makes a population of nodes eligible at once is precisely
what is unmeasured, and it is a claim about a Windows internal this project has
no business asserting from memory.
*Discriminator:* `dNSTombstoned` and `dsTombstoneTimestamp` on the missing
nodes, plus the DNS server's tombstone interval as actually configured, plus
DNS Server events 2501/2502. *Cost:* included in the same capture.

**H3 — scavenging ran despite being disabled, or ran somewhere else.**
*Discriminator:* events 2501/2502 say whether a scavenging pass ran and how
many records it removed; the captured `Get-DnsServerScavenging` and
per-zone `Get-DnsServerZoneAging` say what the settings actually were rather
than what they were believed to be. A claim nobody can re-read is not a check.
*Cost:* included.

**H4 — something wrote the deletion, and the write has an origin.**
Attribute-level replication metadata records the last originating change to
`dnsRecord` and `dNSTombstoned`: which server, which USN, and when. If a
process removed these records, this names it; if nothing wrote them, that is
itself the answer and H1 gets much stronger.
*Discriminator:* `Get-ADReplicationAttributeMetadata` over the locator nodes.
*Cost:* included.

**H5 — the jump itself is the trigger, not the destination.**
Everything recorded describes a *running* DC being moved forward. Nothing
describes a DC that *booted* at real time. If the mechanism is the transition,
a fresh checkpoint taken at real time never encounters it.
*Discriminator:* boot the estate with host time-sync on, let the DC come up at
real time, capture, and leave it running for longer than "minutes". *Cost:* one
boot; no repair, no theory needed.

## The capture plan

Three phases into three directories, one command each, all read-only:

```powershell
# 1. before   -- frozen clock, discovery healthy
.\collect-dc-clock-dns.ps1 -OutputDirectory C:\temp\lab\wi069\before

# 2. after-jump -- immediately after setting the DC forward
.\collect-dc-clock-dns.ps1 -OutputDirectory C:\temp\lab\wi069\after-jump

# 3. broken   -- once DC discovery fails
.\collect-dc-clock-dns.ps1 -OutputDirectory C:\temp\lab\wi069\broken
```

Comparing the three is the point; a single capture settles nothing. The
collector writes a `snapshot.json` declaring
`"kind": "diagnostic-observation-not-conformance-verdict"` — the WI-028 shape,
not a lane verdict — plus a `hashes.json` over everything it wrote. It is
bound by no lane and adds no binding: a new file under `scripts/plan-033/` that
no finalizer's table names starts at cost zero.

**Before anything is committed**, the capture must go through the identifier
gate: it contains host names, zone names and distinguished names. The existing
`wi062-cleanup/directory.json` shows the accepted shape — the synthetic lab
domain is fine, work-domain identifiers are not — and
`collect-searched-som.ps1`'s own instruction applies unchanged: keep raw output
private until reviewed.

## Two ways to the 22nd lane that do not require solving this

The DNS mechanism is worth knowing. The group-deny lane does not depend on
knowing it, and conflating the two is what has kept a one-lane debt behind an
unsolved mystery for a week.

**A. Re-anchor the client at boot instead of disabling sync.**
The lane's actual blocker is narrow: the client reboots mid-run and, with
time-sync disabled, comes up on its own clock ~4 days from the DC. That is a
client-side problem with a client-side fix — a startup task that points the
client at the DC and resyncs before the observation window
(`w32tm /config /manualpeerlist:<dc> /syncfromflags:manual`, then
`w32tm /resync /force`). The client jumps *backwards* to meet the frozen DC,
which is the opposite of the operation that broke DNS and happens on a member
client that owns no zone objects. If this works, the frozen-timeline batch
gains its 22nd lane and the DC is never touched.

*What would make it fail:* the reboot's clock source is the virtual RTC rather
than the guest's own configuration, so the resync has to win a race against
whatever else the client does at boot. That is measurable in one reboot.

**B. Re-baseline the estate at real time (H5's corollary).**
Boot all three guests with host time-sync on, let them settle at real time, and
take a new `estate-current-<date>` checkpoint. The forward jump never happens
because nothing jumps. The cost is real and should not be understated: the
frozen baseline's determinism is lost, `environment-spec.md`'s fingerprint has
to be re-qualified, and every guest-stamped run id after that point stops being
comparable with the 2026-09-05 batch. It also destroys the evidence for
diagnosing the original failure, so **take the three captures above first** if
both are wanted.

## What closes WI-069

Either the mechanism is identified and the DC can be restored to real time
without losing discovery, **or** path A or B gets the estate to a state where
the computer group-deny lane can run and
`wp6-evidence/wi059-20260908/computer-security-filtering-group-deny/verification.json`
leaves `PENDING_REQUALIFICATION`. The second is what the batch actually owes;
the first is what the record is missing. They are not the same debt, and the
open item should not be closed by doing only one of them without saying which.
