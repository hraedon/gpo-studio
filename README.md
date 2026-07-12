# GPO Studio

GPO Studio is an offline-first, web-based Group Policy authoring workbench. It
brings the useful shape of GPMC—GPO inventory, Computer/User configuration,
link intent, validation, and revision history—to a browser without giving that
browser a privileged connection to Active Directory.

The 1.0 product edits raw and ADMX-backed registry policy, GPO links, security
filters, WMI filters, and the GPP Groups and Registry subsets with six ILT
predicates. It creates deterministic native `Registry.pol` files, GPMC backup
and Studio bundle exports, and a reviewable PowerShell publication plan. The
full capability contract — including per-action fidelity, states, and
limitations — is in [`docs/capability-matrix.md`](docs/capability-matrix.md).
The optional live-write architecture is deliberately separate and documented
in [`docs/live-publication.md`](docs/live-publication.md), with its threat model
in [`docs/publisher-threat-model.md`](docs/publisher-threat-model.md).
The long-horizon maximalist product and engineering program is
[`plans/001-maximalist-platform.md`](plans/001-maximalist-platform.md).
The recommended GPO-to-modern-management capability is an evidence-backed
[`Intune Migration Planner`](docs/intune-migration-planner.md), not a literal
one-to-one converter.

## Why a separate tool from gpo-lens?

[`gpo-lens`](../gpo-lens/) has a valuable, enforced read-only charter. Folding
write-oriented behavior into it would weaken a boundary that operators can
currently trust. GPO Studio is the authoring counterpart: a local draft and
review surface with publication kept at an explicit adapter boundary.

## Capabilities

GPO Studio 1.0 (in development) supports the following areas as a single-operator
offline authoring workbench. See
[`docs/capability-matrix.md`](docs/capability-matrix.md) for the full contract
with per-action fidelity, capability states, and known limitations.

- **Registry policy** — raw `REG_SZ`, `REG_EXPAND_SZ`, `REG_BINARY`,
  `REG_DWORD`, `REG_MULTI_SZ`, `REG_QWORD` with set/delete actions, plus
  ADMX-backed policy configuration via a searchable catalogue.
- **GPO links** — target, enabled, enforced, and order.
- **Security filters** — principal, permission, inheritable, target type, SID.
- **WMI filters** — name, query, description, language, with a reusable filter
  catalogue.
- **GPP Groups and Registry** — action, members, values, type-aware, with
  six ILT predicate types (ou, group, registry, ip\_range, environment,
  wmi\_query).
- **Side enablement** — independent computer/user toggles.
- **Revision history** — immutable actor/reason revisions and restore.
- **Import** — gpo-lens estate snapshots, single-GPO GPMC backups, optional
  migration tables (preview).
- **Export** — deterministic Studio bundle (manifest, PReg, PowerShell plan,
  GPP XML) and GPMC backup.
- **Safety gates** — cpassword blocked at every boundary; unknown CSE content
  inventoried and hashed but not re-emittable.

The PowerShell plan applies registry values, links, security filtering, and
side status. It does **not** apply WMI filter assignment or GPP content —
those are included in GPMC backup export only.

Windows-lab verification has not yet been performed.

## Run it

```bash
uv sync --extra dev
uv run gpo-studio --database ./gpo-studio.db
```

Open <http://127.0.0.1:8765>. The API is documented at
<http://127.0.0.1:8765/docs>.

To run without `uv`:

```bash
python -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/gpo-studio
```

## Verify

```bash
uv run pytest -q
uv run ruff check .
uv run mypy src
```

## Safety model

```text
browser → local API → SQLite draft + immutable revisions
                        │
                        └─ export.zip → administrator review → AD publication
```

The web process has no LDAP client, SMB client, GroupPolicy remoting, or SYSVOL
write path. Publishing requires a separate human action. That boundary also
makes four-eyes approval, signing, CI validation, and later privileged worker
isolation straightforward.

The generated plan is a starting point for controlled publication, not a
transactional deployment engine. Test it in a lab, review it, and use delegated
GPO permissions. Native Windows behavior and CSE-specific details still apply.

## Project layout

| Path | Responsibility |
|---|---|
| `src/gpo_studio/model.py` | Frozen domain contracts |
| `src/gpo_studio/store.py` | SQLite snapshots, revisions, concurrency |
| `src/gpo_studio/validation.py` | Deterministic preflight checks |
| `src/gpo_studio/registry_pol.py` | Native PReg parser/serializer |
| `src/gpo_studio/export.py` | Publication bundle, GPMC backup, and PowerShell plan |
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
| `src/gpo_studio/static/` | Dependency-free browser application |

## License

MIT
