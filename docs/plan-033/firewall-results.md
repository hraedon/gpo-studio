# Firewall policy lane

**Current qualification (2026-10-09):** `firewall-20261009080610-2829523`, 36/36, on
clean frozen `99405618105edaa4b408be92047f83b6a24dd217`
([evidence](wp3-evidence/release110-rerun-20261009/firewall/verification.json),
[release 1.1.0 successor batch](release110-successors-batch.md)), tagged
`evidence/firewall-20261009080610-2829523`. It replaces the release 1.1.0 batch's run below: the WI-080/081/082 fix
changed `gpp.py`, `gpp_adapters.py`, `canonical.py`, `report_parity.py` and
`backup_inventory.py`, and this lane binds some of them.

**Previous qualification (2026-10-09):** `firewall-20261009001906-2614294`, 36/36, on
clean frozen `de9736ed3a4148b91cf2267fbe4640e260cc232f`
([evidence](wp3-evidence/release110-20261009/firewall/verification.json),
[release 1.1.0 batch](release110-batch.md)), tagged
`evidence/firewall-20261009001906-2614294`. The run below
stopped binding when batch 2 and WI-078 changed files it binds. This run's
write leg imported Studio's export registered with the firewall tool GUID
(`B05566AC`), the native pair, where the first run's carried `D02B1F72`
(WI-077: GPME display is still unmeasured).

**Previous qualification (2026-10-08):** `firewall-20261008094055-2092337`,
36/36, on clean commit `a6e0002dac0d65d6ae2b969a23636bf284061da1`
([evidence](wp3-evidence/firewall-20261008/firewall/verification.json)), tagged
`evidence/firewall-20261008094055-2092337`. This is the lane's first
certification. An exploratory run passed 36/36 earlier the same day at a commit
the review fixes then superseded; it is not banked and certifies nothing.

The lane, the codec and the reasoning behind each assertion are in
[the codec and lane design](firewall-codec.md). This page records what the
certifying run showed and what it did not.

## What ran

One run on the estate member server, LabMS01: Windows Server 2025 Standard,
build 26100, Windows PowerShell 5.1, domain role 3, frozen profile, `psdirect`
transport. Both GPOs were disposable and never linked. The run left no
`StudioFwLane*` rule in the PersistentStore before or after, and cleanup
removed both GPOs by exact name.

**Read leg: Windows authors, Studio reads.** `New-NetFirewallRule` and
`Set-NetFirewallProfile -PolicyStore "<domain>\<gpo>"` authored the 13
measured rule shapes and the Domain and Private profile settings into a fresh
GPO. Studio's codec parsed the Registry.pol Windows wrote:

- zero unrecognised records, zero unknown rule tokens, no issues;
- the parsed policy equals the authored policy, field for field;
- every record's original bytes equal the codec's emission for that record.

The NetSecurity cmdlets then read every rule (port, address, application,
service, interface-type, interface and security filters) and all three
profiles back, and the readback equals the controller's expectation. Six
readback differences from the wire are **named normalizations**, each graded as
its own check rather than absorbed: ICMPv4 rules report `LocalPort=RPC`,
`RPC-EPMap` reads back as `RPCEPMap`, `IFType=Lan` as `Wired`, an
authenticated `ByPass` as `Allow` plus `OverrideBlockRules`, an IPv4 subnet in
mask form, and `LocalSubnet` collapsed from its two families.

**Write leg: Studio authors, Windows reads.** Studio built the same policy
through `to_registry_settings`, a `GPO` and the unchanged
`gpmc_backup_bundle` exporter. `Import-GPO` imported that backup into a second
fresh GPO, with no migration table. Then:

- Windows' Machine Registry.pol is **byte-identical** to the candidate's
  (7,560 bytes, SHA-256 `25ad9748…de081` on both sides);
- it parses with zero unrecognised records and equals the expected policy;
- cmdlet readback equals the expectation, every rule ID is present and there
  are no extra rules, with the same six named normalizations.

The verdict hashes every candidate file and raw artifact and binds the lane
files, the codec and the export chain (16 files) by commit, path and SHA-256.
`tests/test_firewall_lane_evidence.py` regrades the banked record with the
shipping finalizer and rebuilds the candidate byte for byte.

**Review.** An independent Claude reviewer and DeepSeek both passed the lane
after two fix rounds, before this run.

## The tool-GUID observation

The finalizer records each leg's `gPCMachineExtensionNames` without asserting
it, and whether GPMC's report renders a Windows Firewall extension:

| Leg | `gPCMachineExtensionNames` | GPMC report renders the firewall extension |
|---|---|---|
| read (native authoring) | `[{35378EAC-…}{B05566AC-FE9C-4368-BE01-7A4CBB6CBA11}]` | yes |
| write (Studio's backup) | `[{35378EAC-…}{D02B1F72-3407-48AE-BA88-E8213C6761F1}]` | yes |

Both register the Registry client-side extension (`35378EAC`). The tool GUID
differs: native authoring writes the firewall snap-in's (`B05566AC`), and
Studio's exporter writes the Administrative Templates one (`D02B1F72`), because
`export.py` registers every machine Registry.pol that way. The lane did not
change `export.py`; it is bound by the publication, scripts-metadata and now
firewall lanes. Import, byte equality, cmdlet readback and GPMC's report do not
depend on the tool GUID. Whether the **Group Policy Management Editor** shows
the imported rules under its Windows Defender Firewall node with `D02B1F72`
alone was not measured. [WI-077](../work-items.md#wi-077--the-firewall-export-registers-the-administrative-templates-tool-guid)
tracks it.

## What this does not establish

- **GPME editability.** No one opened either GPO in the editor. The report
  renders; editing is unmeasured.
- **Endpoint application.** Nothing was linked, and no client processed the
  policy. The evidence is PolicyStore readback, not resultant firewall state.
- **Values outside the tranche.** One concrete policy was measured: 13 rule
  shapes and the Domain/Private profile literals in the design's tables. The
  codec refuses everything else rather than emitting it with a warning:
  other protocols, profile combinations, address forms, Public profile
  settings, profile values other than the measured literals, and any rule with
  an unknown token.
- **Other builds.** One build, WS2025 26100.
- **IPsec, Public Key, wired and wireless policy.** Out of scope for 1.x
  (operator ruling 2026-10-07).

## Re-run

From a clean checkout on the controller, with a qualified Hyper-V host:

```bash
export GPO_STUDIO_LAB_HOST='<qualified-hyperv-host>'
export GPO_STUDIO_LAB_GUEST='LabMS01'
acb exec cred:lab-hyperv-control cred:lab-guest-bootstrap -- \
  bash scripts/windows-oracle/run-firewall-oracle.sh
```

Any edit to a bound file expires this verdict; see
[the bound-source cost table](bound-source-cost.md).
