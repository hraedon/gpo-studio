# GPO Studio

GPO Studio is an offline-first, web-based Group Policy authoring workbench. It
brings the useful shape of GPMC—GPO inventory, Computer/User configuration,
link intent, validation, and revision history—to a browser without giving that
browser a privileged connection to Active Directory.

The current milestone edits registry-based policy, which covers a large and
important part of Administrative Templates and Group Policy Preferences. It
creates deterministic native `Registry.pol` files plus a reviewable PowerShell
publication plan. It does **not** claim full GPMC parity yet; see
[`docs/roadmap.md`](docs/roadmap.md) for the compatibility map.
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

## Included in v0.1

- Browser inventory and creation of draft GPOs.
- GPO name, description, lifecycle state, and Computer/User side enablement.
- Registry settings for `REG_SZ`, `REG_EXPAND_SZ`, `REG_BINARY`, `REG_DWORD`,
  `REG_QWORD`, and `REG_MULTI_SZ`, including delete operations.
- Domain/OU link intent with enabled, enforced, and order fields.
- Deterministic validation, including side/hive mismatch, duplicate setting,
  invalid DN, value range, and disabled-but-populated warnings.
- Optimistic concurrency so a stale browser cannot silently overwrite work.
- Immutable actor/reason revision history and restore-as-new-revision.
- Deterministic ZIP export containing:
  - `manifest.json` — complete, versioned draft and validation result;
  - `Machine/Registry.pol` and `User/Registry.pol` — native PReg files;
  - `apply.ps1` — a human-reviewable GroupPolicy-module publication plan.
- Local-only default binding (`127.0.0.1`) and no AD/SYSVOL write code.

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
| `src/gpo_studio/export.py` | Publication bundle and PowerShell plan |
| `src/gpo_studio/api.py` | FastAPI delivery layer |
| `src/gpo_studio/static/` | Dependency-free browser application |

## License

MIT
