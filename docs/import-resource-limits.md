# Import resource limits

GPO Studio treats imported policy data as untrusted. The limits below are
enforced before imported content is committed to the workspace. Exceeding a
limit rejects the import; limits are not truncation targets.

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
| SDDL text | 256 KiB |
| SDDL ACEs | 10,000 |
| ADMX or ADML file | 10 MiB |
| ADMX/ADML XML elements | 100,000 |
| ADMX/ADML XML depth | 100 |
| WMI catalogue file | 50 MiB |
| Workspace-backup metadata sidecar | 64 KiB |

`REG_DWORD` values must be between 0 and 2^32-1. `REG_QWORD` values must be
between 0 and 2^64-1. XML entity declarations are rejected rather than
expanded.

These are safety ceilings, not recommended operating sizes. Imports near a
ceiling can take longer and consume proportionally more memory. Operators
should stage GPMC backups and migration tables only in the configured inbox;
the inbox workflow remains a preview feature until it is exposed directly in
the UI.
