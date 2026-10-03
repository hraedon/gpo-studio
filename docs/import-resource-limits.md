# Import resource limits

GPO Studio treats imported policy data as untrusted and checks these limits
before committing anything to the workspace. An import that exceeds a limit is
rejected, not truncated.

| Input | Limit |
| --- | ---: |
| HTTP mutation request body | 10 MiB |
| GPMC backup file | 50 MiB |
| GPMC backup total file content | 500 MiB |
| GPMC backup filesystem entries | 10,000 |
| GPMC backup directory depth | 100 |
| GPMC backup GPOs | 100 |
| Backup XML elements | 100,000 |
| Backup XML depth | 100 |
| Backup XML text or tail slot | 1 MiB |
| Backup XML attribute value | 4,096 characters |
| Migration table | 10 MiB |
| Registry.pol records | 100,000 |
| `REG_MULTI_SZ` items | 10,000 |
| Estate GPOs | 1,000 |
| Estate JSON nodes | 10,000 |
| Estate JSON depth | 64 |
| GPP XML | 10 MiB |
| GPP XML elements | 100,000 |
| GPP XML depth | 100 |
| GPP XML text or tail slot | 1 MiB |
| GPP XML attribute value | 4,096 characters |
| ILT XML | 1 MiB |
| ILT XML elements | 10,000 |
| ILT XML depth | 50 |
| ILT XML text or tail slot | 65,536 characters |
| ILT XML attribute value | 4,096 characters |
| `fdeploy` file | 1 MiB of native UTF-16LE bytes, BOM included |
| `fdeploy` parse warnings listed | 1,000 (a count of the rest follows) |
| `fdeploy` sections | 5,000 |
| `fdeploy` entries per section | 5,000 |
| SDDL text | 256 KiB |
| SDDL ACEs | 10,000 |
| ADMX or ADML file | 10 MiB |
| ADMX/ADML XML elements | 100,000 |
| ADMX/ADML XML depth | 100 |
| WMI catalogue file | 50 MiB |
| Workspace-backup metadata sidecar | 64 KiB |

Also enforced:

- `REG_DWORD` values must be between 0 and 2^32-1.
- `REG_QWORD` values must be between 0 and 2^64-1.
- XML entity declarations are rejected, not expanded.

These are safety ceilings, not recommended sizes. An import near a ceiling
takes longer and uses proportionally more memory.

Stage GPMC backups and migration tables only in the configured inbox. The inbox
workflow stays a preview feature until the UI exposes it directly.
