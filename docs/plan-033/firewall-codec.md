# Firewall codec and the next lane

Status: codec implemented, **not surfaced**; capture-backed, not Windows-verified
by a Studio write lane. Native fixture dated 2026-10-08; operator scope ruling
2026-10-07. Plan 034 exit remains codec → lane → surface. [WI-070](../work-items.md#wi-070--firewall-codec-needs-a-write-lane-before-a-surface)
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

## Proposed write leg (requires an estate session)

1. Build/hash-bind a Studio-origin machine policy from the independent typed
   fixture expectations, including all 13 rules and both configured profiles.
   Bind the codec, serializer, candidate builder, harness and finalizer sources.
2. Import that artifact into a disposable, unlinked GPO through the explicit
   administrator adapter. Assert no User Registry.pol or host PersistentStore
   writes and that Public values and Private default outbound remain absent.
3. Assert native AD extension metadata: Registry CSE
   `{35378EAC-683F-11D2-A89A-00C04FBBCFA2}` paired with firewall snap-in tool
   `{B05566AC-FE9C-4368-BE01-7A4CBB6CBA11}`, and no User extension. Capture
   the version changes, Backup-GPO payload, Registry.pol, report and cmdlet filters.
4. Compare Windows readback with independently authored expected fields for
   every rule/profile and every application/service/address/port/interface/security
   filter. Assert the named normalizations individually, including the ICMPv4
   anomaly. Reject missing/extra rules and collapsed profiles.
5. Assert Windows accepts the Studio-origin bytes; re-export/read the policy
   and compare record identity, registry type, data and token order. If Windows
   rewrites anything beyond file record ordering, bank it as a named measured fact
   before changing the codec. Add isolated write/mutate controls that change a
   rule and a profile; a read leg on native bytes alone cannot certify a writer.
6. Check cleanup and unchanged PersistentStore before/after. Bank candidate/raw
   hashes and a rerunnable verdict. Define GPMC editability and endpoint processing
   as separate claims; cmdlet readback alone proves neither.

`export.py` is bound by two live lanes and remains untouched. Its extension
registration will need a reviewed proposal: detect emitted firewall keys and
register the Registry CSE with the measured B05566AC tool GUID. Do not use the
D02B1F72 GUID for this capture. Batch any export change with the publication and
scripts-metadata lane requalification, or compose a separate unbound adapter
and prove its unchanged base export equal to the bound one. Only after the
firewall write verdict should an operator surface consume this codec.
