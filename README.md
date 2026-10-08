# GPO Studio

GPO Studio is a local, browser-based workbench for authoring Group Policy
offline. It covers the GPMC basics (GPO inventory, Computer and User
configuration, links, validation and revision history) without giving the
browser a privileged connection to Active Directory. You draft and review in
GPO Studio, export the result, and an administrator publishes it separately.

- What each feature can and cannot do, action by action:
  [`docs/capability-matrix.md`](docs/capability-matrix.md).
- The optional live-write design, kept separate from the web process:
  [`docs/live-publication.md`](docs/live-publication.md), with its threat model
  in [`docs/publisher-threat-model.md`](docs/publisher-threat-model.md).
- The long-term product and engineering plan:
  [`plans/001-maximalist-platform.md`](plans/001-maximalist-platform.md).
- Moving GPOs to modern management: the evidence-backed
  [Intune Migration Planner](docs/intune-migration-planner.md). It is a planner,
  not a one-to-one converter.

[`gpo-lens`](../gpo-lens/) is the read-only counterpart. GPO Studio is a
separate tool so that gpo-lens can keep its enforced read-only guarantee.

## Run it

On Windows, installing a release? Use the
[Windows quickstart](docs/windows-quickstart.md). It needs no Git, `uv`, IIS,
service installation or virtual-environment activation.

From a source checkout:

```bash
uv sync --extra dev
uv run gpo-studio --database ./gpo-studio.db
```

Open <http://127.0.0.1:8765>. API documentation is at
<http://127.0.0.1:8765/docs>.

Without `uv`:

```bash
python -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/gpo-studio
```

Full install and configuration options are in
[`docs/installation.md`](docs/installation.md).

## Verify

```bash
uv run pytest -q
uv run ruff check .
uv run mypy src
```

## Capabilities (1.0)

GPO Studio 1.0 is an offline authoring workbench for a single operator.

- **Registry policy**: raw `REG_SZ`, `REG_EXPAND_SZ`, `REG_BINARY`,
  `REG_DWORD`, `REG_MULTI_SZ` and `REG_QWORD` values with set and delete
  actions, plus ADMX-backed policy from a searchable catalogue.
- **GPO links**: target, enabled, enforced and order.
- **Security filters**: principal, permission, inheritable, target type, SID.
- **WMI filters**: name, query, description and language, with a reusable
  filter catalogue.
- **GPP Groups and Registry**: action, members and type-aware values, with six
  Item-Level Targeting (ILT) predicate types (ou, group, registry, ip\_range,
  environment, wmi\_query).
- **Side enablement**: separate Computer and User toggles.
- **Revision history**: immutable revisions with actor and reason, and restore.
- **Import**: gpo-lens estate snapshots, single-GPO GPMC backups, and optional
  migration tables (preview).
- **Export**: a deterministic Studio bundle (manifest, `Registry.pol`,
  PowerShell plan, GPP XML) and a native GPMC backup for verified extension
  families.
- **Safety gates**: `cpassword` is blocked at every boundary. Unknown CSE
  content is inventoried and hashed but cannot be re-emitted.

The PowerShell plan applies registry values, links, security filtering and
side status. It does **not** apply WMI filter assignment or GPP content; those
are in the GPMC backup export only.

The full Windows compatibility matrix and the hands-on NVDA acceptance gate
passed for 1.0. The environments, artifact identities, limitations and the one
accepted minor accessibility observation are in
[`docs/release-evidence.md`](docs/release-evidence.md).

### Since 1.0

The 1.0 contract above has not changed. Since 1.0:

- **Live (reachable from the API or browser):** scope of management,
  delegation, AD discovery and full GPP adapter coverage. Three post-1.0 layers
  are also reachable through narrow, lane-certified endpoints: RSOP prediction
  (`rsop.py`), and the emission direction of `policy_families.py` and
  `object_security.py`.
- **Folder Redirection review:** a sidebar panel reads a native `fdeploy` file,
  or compares an earlier copy with a current one. It shows raw flags,
  structural issues and the reader's limits, based on the banked R3 capture. It
  does not author or apply policy. See the
  [file review guide](docs/folder-redirection-review.md).
- **Not reachable:** the other Plans 025–032 domain layers are implemented
  but not wired to the API or browser. The
  [capability matrix](docs/capability-matrix.md#post-10-domain-layers--landed-but-not-surfaced)
  lists exactly what is and is not reachable.

Treat those domain layers as unproven drafts, not finished code waiting to be
wired up. Their wire behaviour is a hypothesis about Windows, and every layer
an external oracle has examined so far has needed correction. See
[`docs/domain-layer-status.md`](docs/domain-layer-status.md).

Nine of the seventeen layers have had oracle contact, through eleven manual
evidence requests run in September 2026. Five needed correction, one was
confirmed correct, and one had its scope invalidated rather than its code. The
per-request record is in
[`docs/manual-evidence-requests.md`](docs/manual-evidence-requests.md#where-each-result-lives).
The capability matrix marks these results `capture-backed`. That means measured
wire facts only. It is **not** a re-runnable lane and not a step toward
promotion. [Plan 034](plans/034-post-1.0-layer-reconciliation.md) turns them
into capabilities or into explicit out-of-scope rulings.

Windows verification is per capability and is never inherited from
implementation. So far
[Plan 033](plans/033-windows-external-oracle-validation.md) has certified WP-0,
WP-1A and WP-2, and measured parts of WP-1B and WP-3.

## Safety model

```text
browser → local API → SQLite draft + immutable revisions
                        │
                        └─ export.zip → administrator review → AD publication
```

The web process has no LDAP client, SMB client, GroupPolicy remoting or SYSVOL
write path. Publishing is a separate human action. Keeping that boundary also
leaves room for four-eyes approval, signing, CI validation and an isolated
privileged worker later.

The generated plan is a starting point for controlled publication. It is not a
transactional deployment engine. Test it in a lab, review it, and run it with
delegated GPO permissions. Native Windows behaviour and CSE-specific details
still apply.

Operator references:

- [Windows quickstart](docs/windows-quickstart.md)
- [Installation and configuration](docs/installation.md)
- [Manual NVDA validation runbook](docs/nvda-validation-runbook.md)
- [Workspace backup and recovery](docs/workspace-recovery.md)
- [Import resource limits](docs/import-resource-limits.md)

## Project layout

| Path | Responsibility |
|---|---|
| `src/gpo_studio/model.py` | Frozen domain contracts |
| `src/gpo_studio/store.py` | SQLite snapshots, revisions, concurrency |
| `src/gpo_studio/validation.py` | Deterministic preflight checks |
| `src/gpo_studio/registry_pol.py` | Native PReg parser/serializer |
| `src/gpo_studio/export.py` | Publication bundle, native GPMC backup emission (`Backup.xml` v2.0), and PowerShell plan |
| `src/gpo_studio/api.py` | FastAPI delivery layer |
| `src/gpo_studio/admx.py` | ADMX/ADML catalogue ingestion |
| `src/gpo_studio/policy_config.py` | ADMX policy-to-registry resolution |
| `src/gpo_studio/gpp.py` | GPP Groups and Registry XML framework |
| `src/gpo_studio/ilt.py` | Item-Level Targeting predicates |
| `src/gpo_studio/sddl.py` | SDDL parser and formatter |
| `src/gpo_studio/estate.py` | gpo-lens estate import |
| `src/gpo_studio/migration.py` | GPMC migration table parsing and application |
| `src/gpo_studio/backup.py` | GPMC backup reader with CSE inventory |
| `src/gpo_studio/canonical.py` | Canonical serialization and semantic hashing |
| `src/gpo_studio/diff.py` | Two-way and three-way GPO diff |
| `src/gpo_studio/identity.py` | Actor identity abstraction |
| `src/gpo_studio/payload.py` | Publisher payload canonicalization |
| `src/gpo_studio/wmi_catalogue.py` | WMI filter catalogue |
| `src/gpo_studio/import_export.py` | Backup import/export domain logic |
| `src/gpo_studio/gpp_adapters.py` | Per-family GPP adapters |
| `src/gpo_studio/som.py` | Scope of management, links, inheritance, loopback |
| `src/gpo_studio/delegation.py` | Delegation and effective rights |
| `src/gpo_studio/wmi_filter.py` | WMI filter objects and associations |
| `src/gpo_studio/ad_discovery.py` | Discovery script generation and JSON parsing |
| `src/gpo_studio/static/` | Dependency-free browser application |

Release and lab tooling, driven by `scripts/`: `conformance.py`,
`oracle_evidence.py`, `oracle_harness.py`, `payload.py`, `provenance.py`,
`ps_plan_validator.py`, `remediation_corpus.py`.

`src/` also holds Plans 025–032 domain layers that are landed and
unit-tested but **not reachable from any operator surface**:
`network_security`, `script_policy`, `artifact_store`, `lifecycle`,
`publication`, `publisher` and `hosting`. `script_policy` and `publication`
have Windows evidence lanes but no surface yet. `publisher` and `hosting` are
out of scope for 1.x and kept as seeds, and `artifact_store` is due for
deletion. `rsop`, `policy_families` and `object_security` have endpoints, and
`security_template` is reached through the last two. `fdeploy` reads Folder
Redirection files at its own endpoint. `gpmc_interop` holds only the issue type
`publication` uses. `certification` (WI-056), `software_install` and
`folder_redirection` (2026-10-07) were deleted. See
[the capability matrix](docs/capability-matrix.md#post-10-domain-layers--landed-but-not-surfaced).

## License

MIT
