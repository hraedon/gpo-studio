# Firewall codec and the next lane

Status: codec and two-leg lab lane implemented, **not surfaced**; capture-backed,
Windows lane not yet run or verified. Native fixture dated 2026-10-08; operator scope ruling
2026-10-07. Plan 034 exit remains codec → lane → surface. [WI-076](../work-items.md#wi-076--firewall-codec-needs-a-write-lane-before-a-surface)
tracks the remaining work.

`firewall_policy.py` reads/validates/emits the measured machine Registry.pol
tranche. It does not import `network_security.py` or change any lane-bound
source. The capture script and replayable sanitizer live under `scripts/plan-033/`.
The fixture includes capture JSON, report XML, the complete sanitized backup,
unchanged Registry.pol and per-file provenance hashes. Sanitized backup security
descriptors are nonfunctional placeholders, following the GPP convention;
these fixtures are evidence, not publication packages.

The legacy `network_security.py` firewall model stays in place to keep its
existing tests and callers compatible. It assumes global/default profile
settings and cannot faithfully represent the capture. After the lane certifies
the new codec, adapt its consumers and replace its firewall half with explicit
re-exports from `firewall_policy.py`; do not silently alias incompatible classes.
IPsec, Public Key, wired and wireless policy authoring are out of scope for 1.x
under the operator ruling. Firewall `IFType=Lan` remains part of the measured
firewall rule vocabulary; this does not qualify wired network policy authoring.

## Measured contract

- HKLM/machine only, `PolicyVersion` DWORD 545, rule strings `v2.33`.
- Per-profile optional values: `None` emits nothing. Public has no values.
  Inbound block is 1, outbound allow is 0. `DisableNotifications=1` represents
  cmdlet `NotifyOnListen=False`. Logging false is an explicit DWORD 0.
- Action/active/direction then protocol and repeated profiles. Any omits tokens.
  Protocol numbers: 6, 17, 1, 58, 47; Any omits Protocol.
- Single ports and RPC/RPC-EPMap; remote range uses `RPort2_10` before `RPort`.
  ICMP4 type/code 8:0 has no port token. LA4 host, RA4 mask subnet, RA6 CIDR
  subnet; remote LocalSubnet expands into both RA4 and RA6.
- App, Svc, IFType=Lan, Name, Desc, EmbedCtxt, Edge=TRUE and the measured
  authenticated ByPass/RMauth/Security form in their native order.

Supported literal values and token combinations are limited to the capture.
New token combinations are refused because their ordering is unmeasured.
Unknown rule tokens retain positions and text, including repeats, but cannot be
emitted. Unknown registry records are returned separately and callers must
retain or explicitly review them. Invalid known records raise
`FirewallValidationError` with `ValidationIssue` codes. Public configuration,
other protocols/keywords, local port ranges/lists, other ICMP type/codes,
interfaces, security descriptors, profile action variants and log sizes are
refused. Variable names, ports and text can change within the measured shapes;
the capture does not establish arbitrary-value Windows acceptance.

Windows' file order is root, Domain profile, rules, Private profile; profile
values retain authoring order. Studio's existing Registry.pol serializer sorts
by key/value name. Tests compare every emitted record byte-for-byte against the
native file slices, and the complete sorted record set against native records.
Whole-file equality holds when emitted record chunks are placed in native order;
the sorted whole-file bytes intentionally differ. No bound serializer was edited.

Named readback normalizations are in fixture provenance: ICMPv4 LocalPort=RPC
without LPort, RPC endpoint spelling, Lan/Wired, authenticated ByPass appearing
as Allow plus OverrideBlockRules, CIDR/mask form, and LocalSubnet family collapse.
They are assertions the lane must name, not errors it may silently normalize away.

## Implemented lane (requires an estate session)

The controller builds a typed `FirewallPolicy` with all 13 measured rules and
Domain/Private settings, leaving Public and Private default outbound absent.
`build-firewall-candidate.py` refuses all codec or GPO validation issues, then
uses `to_registry_settings` → `GPO` → the unchanged `gpmc_backup_bundle` export
path to emit `studio-firewall-backup.zip`. It emits `authoring.json` in
NetSecurity parameter vocabulary and controller-only `expected.json` containing
the typed policy, every expected rule/profile field, six explicitly named
readback normalization rules and each expected raw Registry.pol record chunk.
All three files receive SHA-256 lines in builder stdout (WI-025).

`run-firewall-policy.ps1` runs on LabMS01 under Windows PowerShell 5.1. Its read
leg creates a disposable unlinked GPO, authors via `New-NetFirewallRule` and
`Set-NetFirewallProfile -PolicyStore "$Domain\$name"`, then captures the rule,
port/address/application/service/interface type/interface/security filters and
all three profiles. The write leg creates a second disposable unlinked GPO and
imports Studio's archive via `Import-GPO`, then takes the same observations.
Every policy operation records its effective PolicyStore (filter cmdlets use
InputObject from that store), subject, success and stdout/stderr filenames. CIM
and AD values become plain strings/integers before JSON. Each leg captures
Machine Registry.pol base64, directory extension metadata and Get-GPOReport XML.
The guest checks PersistentStore for zero `StudioFwLane*` rules before and after,
removes both owned GPOs in `finally`, and strictly re-queries all GPOs for their
names and IDs. It never links either GPO.

`finalize_firewall_run.py` independently gates schema, authoring/import success,
zero unrecognised Windows records/tokens, complete parsed policy equality,
record-set equality with per-record **original byte** equality, complete cmdlet
readback on both legs, every write rule ID and absence of extras. It names each
of the six normalizations as a separate check in each leg. Scoped operation
logs, no links, unchanged PersistentStore, cleanup, LabMS01 member role, frozen
environment, deployed script hashes, delivery of both archive and authoring JSON,
no harness error and clean bound source are also required. Missing data fails
closed. Expected data never travels to the guest. The verdict hashes every
candidate file and raw artifact and binds the lane files, codec and publication
export chain by commit/path/SHA-256, with the same evidence-tag convention.

The finalizer **records**, without asserting a tool GUID, each leg's
`gPCMachineExtensionNames` and whether its report renders a Windows Firewall
extension. The read/write comparison supplies the evidence for the B05566AC
registration question while keeping the bound exporter unchanged. A passing
cmdlet/byte verdict alone does not establish GPMC editability, endpoint processing
or an operator surface. No estate run has been performed for this implementation.

Run from this worktree on the controller, with a qualified Hyper-V host selected
in `GPO_STUDIO_LAB_HOST` and `GPO_STUDIO_LAB_GUEST=LabMS01`:

```bash
export GPO_STUDIO_LAB_HOST='<qualified-hyperv-host>'
export GPO_STUDIO_LAB_GUEST='LabMS01'
acb exec cred:lab-hyperv-control cred:lab-guest-bootstrap -- \
  bash scripts/windows-oracle/run-firewall-oracle.sh
```

The composed acb checkout provides `HYPERV_CONTROL_USERNAME`,
`HYPERV_CONTROL_PASSWORD`, `GUEST_BOOTSTRAP_USERNAME` and
`GUEST_BOOTSTRAP_PASSWORD`; the driver/transport consume them from the environment.
The driver uses a 600-second guest timeout, prints `LOCAL_RUN_DIR=` and
`CANDIDATE_DIR=`, retrieves the deployed guest script and artifacts, and finalizes
itself. To regrade the banked pack without creating an evidence tag:

```bash
uv run python scripts/windows-oracle/finalize_firewall_run.py "$LOCAL_RUN_DIR" \
  --candidate-root "$CANDIDATE_DIR" --repo-root "$PWD" --no-tag
```

`export.py` is bound by two live lanes and remains untouched. Its extension
registration will need a reviewed proposal: detect emitted firewall keys and
register the Registry CSE with the measured B05566AC tool GUID. Do not use the
D02B1F72 GUID for this capture. Batch any export change with the publication and
scripts-metadata lane requalification, or compose a separate unbound adapter
and prove its unchanged base export equal to the bound one. Only after the
firewall write verdict should an operator surface consume this codec.
