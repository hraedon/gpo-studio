# WI-062: the manifest-form requalification batch

**Status:** executed 2026-09-10/11. **The estate repair this note describes is
now [WI-069](../work-items.md), with a capture plan and a read-only collector in
[`estate-clock-dns-repair.md`](estate-clock-dns-repair.md)** -- including the
step this note did not take: that the records were *deleted*, rather than
present and unserved, was concluded through a resolver and never checked
against the directory. 21 of 22 runs passed on frozen harness
`f5cad5777ec4ad3ed0857717d511b057852db6ba`: WP-0's manifest plus 20 lane
verdicts, all schema version 2. The 22nd run -- the computer group-deny lane
-- is owed by an estate repair and stays in
`PENDING_REQUALIFICATION` with its reason recorded in the registry test.

## What this batch changed

Every pack in it binds its controller-side harness by
**(commit, path, sha256)** instead of byte copies: lane verdicts carry
`source.paths` and `source.banked_copies`, WP-0's manifest carries
`source.bound`, and no pack holds a copy of `oracle_evidence.py`, a finalizer,
a driver, or a controller-side bound product module. The decision and its
standalone-verification trade-off are
[written down](bound-source-manifest.md). The batch exists because
`oracle_evidence.py`, every finalizer and every driver changed to implement
that policy, which invalidated every live verdict at once.

WI-061 rode the same batch: the store now files each retained native XML
document once (`retained_documents` + `snapshot_documents`, schema v4), and
the Scripts/publication lanes -- the two that bind `model.py`-adjacent product
code -- re-earned their verdicts on the new storage.

## The estate repair this batch needed

The 2026-09-08 WI-059 batch ran on an estate whose DC had never been reverted.
Reverting to `estate-current-20260905` for this session resurrected two latent
problems, and one of them was not fixable:

- The checkpoint restores the DC to its frozen clock while the clients keep
  host-synced real time. A ~6-day Kerberos skew between client and DC breaks
  machine policy processing (the client's security context never initiates),
  which is exactly what the endpoint lane measures.
- Setting the DC forward to real time -- correctly, verified against the
  controller -- works for minutes and then kills **domain-wide DC discovery**:
  every dynamic DC-locator DNS record (`_ldap._tcp.dc._msdcs...`, and even the
  host A records) is deleted. Reproduced four times, with DNS server
  scavenging disabled, with per-zone aging disabled, with lockout threshold
  zero, and with records force-re-registered immediately after the jump. The
  deletion mechanism was not identified; the estate needs a repair that
  restores the DC to real time without it (or a fresh DC build) before the
  group-deny lane can run.
- The batch therefore ran on the **frozen checkpoint timeline**: all three
  guests reverted, client time-sync disabled before boot, clients aligned
  behind the DC. Guest-stamped run ids in this batch carry 2026-09-05 dates;
  controller-stamped records carry real 2026-09-10/11. The two are not
  comparable, which is why the batch manifest's cleanup capture is not
  timestamp-ordered against the runs.

The group-deny lane is the one lane that **reboots the client mid-run** (the
machine token must carry the group the run just authored). A rebooting client
with time-sync disabled lands on its own re-anchored clock, ~4 days from the
DC, and the observation cannot settle. Its three attempts are preserved in
`C:\temp\lab\wi062-batch\attempt-archive\` on the controller.

## Evidence

| Experiment | Passing run |
|---|---|
| wp0 | `live-synthetic-registry-basic-20260905190936-7000` |
| wp1b | `wp1b-writer-20260905191051-4805` |
| wp2 | `wp2-native-import-20260905191148-9446` |
| wp3-member | `wp3-security-template-20260905191215-9060` |
| wp3-dc | `wp3-security-template-20260905191240-8844` |
| object-security | `object-security-20260905191252-4253` |
| scripts-metadata | `scripts-r10-20260905191308-8174` |
| publication | `publication-completeness-20260905191330-7322` |
| endpoint | `endpoint-observe-20260905191405-9825` |
| lsdou-precedence | `rsop-observe-20260905191621-5031` |
| disabled-block-enforced | `rsop-observe-20260905191734-7320` |
| wmi-filtering | `rsop-observe-20260905191842-4912` |
| wmi-filtering-error | `rsop-observe-20260905191950-6255` |
| computer-security-filtering | `rsop-observe-20260905192059-1061` |
| computer-security-filtering-deny-read | `rsop-observe-20260905190717-8841` |
| loopback-merge | `rsop-user-observe-20260905190914-6034` |
| loopback-replace | `rsop-user-observe-20260905191128-2282` |
| user-side-disabled | `rsop-user-observe-20260905191309-1112` |
| user-security-filtering | `rsop-user-observe-20260909173922-1159` |
| user-security-filtering-deny | `rsop-user-observe-20260909174151-2079` |
| user-security-filtering-read-deny | `rsop-user-observe-20260909174332-6160` |

Packs live under `<family>-evidence/wi062-20260910/<name>/`. The
[batch manifest](wi062-batch.json) records every banked file's SHA-256. All
runs passed with `source.dirty=false` at the same revision; every passing run
has an `evidence/<run-id>` tag. The [post-batch directory
check](wi062-cleanup/directory.json) confirms both accounts were restored and
no experiment OUs, GPOs, groups or WMI filters survived; it was captured after
the last lane in wall order.

The retired WI-059 verdicts and their tags are preserved unchanged; the
registry test moves the 20 replaced verdicts to `RETIRED_VERDICTS` and keeps
the group-deny verdict pending.
