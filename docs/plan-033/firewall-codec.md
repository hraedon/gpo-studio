# Firewall codec and the next lane

Status: codec, lane and surface all landed (Plan 034 exit met, 2026-10-08).
The two-leg lane certified the codec as `firewall-20261008094055-2092337`
(36/36 at `a6e0002`; [results](firewall-results.md)), and
`POST /api/network-security/firewall/render` and
`GET /api/gpos/{guid}/firewall-policy` surface it from `api.py`.
`network_security.py`'s firewall half is now explicit re-exports of this codec.
[WI-076](../work-items.md#wi-076--firewall-codec-needs-a-write-lane-before-a-surface)
is closed; the tool-GUID question is
[WI-077](../work-items.md#wi-077--the-firewall-export-registers-the-administrative-templates-tool-guid).
Native fixture dated 2026-10-08; operator scope ruling 2026-10-07.

`firewall_policy.py` reads/validates/emits the measured machine Registry.pol
tranche. It does not import `network_security.py` or change any lane-bound
source. The capture script and replayable sanitizer live under `scripts/plan-033/`.
The fixture includes capture JSON, report XML, the complete sanitized backup,
unchanged Registry.pol and per-file provenance hashes. Sanitized backup security
descriptors are nonfunctional placeholders, following the GPP convention;
these fixtures are evidence, not publication packages.

The legacy `network_security.py` firewall model assumed global/default
profile settings and could not faithfully represent the capture. After the lane
certified the new codec, its firewall half was replaced (2026-10-08) with
explicit re-exports from `firewall_policy.py`, and its only consumer, its own
test module, was adapted; the incompatible legacy classes were removed, not
aliased.
IPsec, Public Key, wired and wireless policy authoring are out of scope for 1.x
under the operator ruling. Firewall `IFType=Lan` remains part of the measured
firewall rule vocabulary; this does not qualify wired network policy authoring.

## Accepted contract

HKLM/machine only, `PolicyVersion` DWORD 545, rule strings `v2.33` with a
trailing pipe. An entirely empty policy is accepted without PolicyVersion;
any configured output requires it. Profile fields can be omitted independently
(`None` emits nothing), but each present field must match its measured profile:

| Profile | Allowed fields and literal values |
|---|---|
| Domain | EnableFirewall=1, DefaultInboundAction=1 (block), DefaultOutboundAction=0 (allow), LogDroppedPackets=1, LogSuccessfulConnections=0, LogFileSize=8192, nonempty LogFilePath text |
| Private | EnableFirewall=1, DefaultInboundAction=1 (block), DisableNotifications=1 (`NotifyOnListen=False`), LogSuccessfulConnections=1 |
| Public | No fields |

Each rule must match **one complete row** below. Missing or extra fields, or
changing any literal into another row's value, is refused. Every row requires
a nonempty Name. Any profile/protocol omits its tokens; `—` means absent.
Only row 10 is disabled; all other rows require Active=TRUE.

| Capture rule | Direction | Action | Protocol | Profile tokens | Additional fields in native order (Name included) |
|---|---|---|---|---|---|
| 01 | In | Allow | 6 | Domain | LPort (numeric), Name |
| 02 | Out | Block | 17 | Domain, Private | RPort2_10 (range), RPort (numeric), Name |
| 03 | In | Allow | 1 | — | ICMP4=8:0, Name |
| 04 | In | Allow | 58 | — | Name |
| 05 | In | Block | — | — | LA4 (host), RA4 (subnet), RA6 (subnet), Name |
| 06 | Out | Allow | 6 | — | RPort (numeric), App, Name |
| 07 | In | Allow | 6 | — | LPort (numeric), Svc, Name |
| 08 | In | Allow | 6 | — | LPort=RPC, RA4=LocalSubnet, RA6=LocalSubnet, Name |
| 09 | In | Allow | 6 | — | LPort=RPC-EPMap, Name |
| 10 (Active=FALSE) | In | Allow | 6 | — | LPort (numeric), Name, Desc, EmbedCtxt |
| 11 | In | Allow | 17 | — | LPort (numeric), IFType=Lan, Name, Edge=TRUE |
| 12 | Out | Block | 47 | — | Name |
| 13 | In | ByPass | 6 | — | LPort (numeric), Name, RMauth=D:(A;;CC;;;WD), Security=Authenticate |

Tokens start with Action, Active, Dir, then Protocol if present, then Profile
tokens in table order, then the additional fields. Names/IDs, numeric ports,
App/Svc/Desc/EmbedCtxt/LogFilePath text and address payloads may vary within
their row's forms. Text is nonempty and excludes pipe, NUL, CR and LF; rule IDs
also exclude backslash and semicolon and must be unique ignoring case.
Numeric ports are canonical ASCII decimal 1–65535, without leading zeros;
range endpoints are ascending, distinct ports. Non-canonical decimals are
refused on both read and write.
RPC keywords cannot replace numeric ports in other rows. LA4 is an IPv4 host;
RA4 is one strict IPv4 subnet (prefix 1–31), emitted with a dotted mask;
RA6 is one strict IPv6 subnet (prefix 1–127), emitted as CIDR. Row 05 requires
both subnet families and excludes LocalSubnet, host and default-route prefixes;
row 08 requires LocalSubnet alone, expanded into both families. Address input
order may vary; emitted family order is RA4 then RA6.

Unknown rule tokens retain positions and text, including repeats, but cannot be
emitted. Unknown registry records are returned separately and callers must
retain or explicitly review them. Invalid known records raise
`FirewallValidationError` with `ValidationIssue` codes. All policy, profile,
rule and nested tuple fields are type-checked before wire operations;
`validate()` returns issues for runtime values that bypass annotations, and
`to_registry_settings()` raises `FirewallValidationError` for them. Without
PolicyVersion, firewall records are retained as unrecognised legacy input with
the named `firewall_legacy_without_policy_version` issue and an empty policy; legacy
Administrative Templates settings therefore do not crash this parser.
The capture does not establish arbitrary-value Windows acceptance, and the
future lane qualifies only its 13 concrete rules, not every variable payload.

Windows' file order is root, Domain profile, rules, Private profile; profile
values retain authoring order. Studio's existing Registry.pol serializer sorts
by key/value name. Tests compare every emitted record byte-for-byte against the
native file slices, and the complete sorted record set against native records.
The candidate archive test independently compares its Machine Registry.pol
slices with native slices after explicit candidate identity/payload substitutions;
it does not derive expectations from builder output or codec serialization.
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
records intended names before New-GPO (including a durable intent file), removes
GPOs with those exact names in `finally`, and strictly re-queries the run prefix.
The controller generates the run ID, naming GPOs `StudioFwLane-<run-id>-read`
and `-write`. Before arming its EXIT cleanup trap it calls
`cleanup-firewall-policy.ps1 -CheckOnly`, which refuses existing run-prefixed
GPOs without deleting them. The guest also refuses each existing target before
recording intent or creating it. The controller independently runs destructive
cleanup after the guest job and through the EXIT trap on early failures.
That helper only removes names beginning with `StudioFwLane-<run-id>-`, including
the terminal separator; other runs' GPOs are untouched. It never links either GPO.

`finalize_firewall_run.py` independently gates schema, authoring/import success,
zero unrecognised Windows records/tokens, complete parsed policy equality,
record-set equality with per-record **original byte** equality, complete cmdlet
readback on both legs, every write rule ID and absence of extras. It names each
of the six normalizations as a separate check in each leg; tests perturb every
readback field separately, including all four authenticated-bypass fields. Scoped
operation logs, no links (any LinksTo element, including an empty one, fails),
unchanged PersistentStore, cleanup, LabMS01 member role, frozen environment, deployed script hashes, delivery of both archive and authoring JSON,
no harness error and clean bound source are also required. Missing data fails
closed. Expected data never travels to the guest. The verdict hashes every
candidate file and raw artifact and binds the lane files, codec and publication
export chain by commit/path/SHA-256, with the same evidence-tag convention.

The write leg additionally requires zero unrecognised records/tokens, parsed
policy equality with the controller's typed expectation, and **whole-file byte
equality** with Machine Registry.pol inside the candidate ZIP. Import-GPO imports
backup settings; principal/UNC mapping is exposed through its optional migration
table ([Microsoft documentation](https://learn.microsoft.com/en-us/powershell/module/grouppolicy/import-gpo?view=windowsserver2025-ps)).
This lane uses no migration table and contains no estate principal/UNC payloads;
we therefore require unchanged policy bytes rather than assume an unmeasured
rewrite is legitimate. The verdict records candidate/Windows SHA-256, lengths
and equality even on byte mismatch. Any unexpected Windows rewrite must be
reviewed with estate evidence before this contract changes. Builder stdout
must contain exactly one matching SHA line for each of the three candidate files.

The finalizer **records**, without asserting a tool GUID, each leg's
`gPCMachineExtensionNames` and whether its report renders a Windows Firewall
extension. The read/write comparison supplies the evidence for the B05566AC
registration question while keeping the bound exporter unchanged. A passing
cmdlet/byte verdict alone does not establish GPMC editability, endpoint processing
or an operator surface. The certifying run recorded `B05566AC` for the read
leg and `D02B1F72` for the write leg, with the firewall extension rendered in
both reports ([results](firewall-results.md)).

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
scripts-metadata lane requalification (and now the firewall lane's), or
compose a separate unbound adapter and prove its unchanged base export equal to
the bound one. The certifying run showed import, byte equality, readback and
the GPMC report hold with `D02B1F72`, so no export change was made; GPME display
is unmeasured and tracked as WI-077.

## The surface

`api.py` composes the codec; no lane binds it.

- `POST /api/network-security/firewall/render` takes typed rules and
  per-profile settings (the codec's own field vocabulary, strict numbers and
  booleans, unknown fields refused) and returns `registry_settings`, each in
  the body shape `POST /api/gpos/{guid}/settings` accepts, the raw
  `rule_strings`, non-blocking `issues` and `limitations`. A request outside
  the measured tranche is a 422 carrying the codec's issue codes. It writes
  nothing.
- `GET /api/gpos/{guid}/firewall-policy` decodes the GPO's settings under the
  firewall key, imported GPOs included: `status` (`empty`, `decoded`,
  `legacy`, `refused`), rules with their stored rule strings and any unknown
  tokens, the three profiles, `unrecognised_records`, `issues` and
  `limitations`. A known record outside the tranche refuses the whole decode
  rather than interpreting part of it.

Every response carries `policy_store_readback_not_application`,
`representative_tranche_only`, `ipsec_pki_wired_wireless_out_of_scope`,
`gpme_display_unmeasured` and `single_build_measured`; a decode with unknown
rule tokens adds `unmodeled_tokens_preserved_not_editable`. Refusals carry
them too, beside `error` (or `detail`): codec and request-validation 422s, an
unknown GPO's 404, a wrong method's 405, an unparseable body's 400, and the
middleware's host (421), origin (403), chunked (400) and size (413) refusals,
which match the route by path because they run before routing.
`tests/test_firewall_surface.py` holds the render of the certified request
equal to the builder's emission and the banked `expected.json`, shows the
rendered settings posted to a GPO export Windows' write-leg Registry.pol byte
for byte, and holds the decode of the banked native fixture equal to what the
finalizer parses.
