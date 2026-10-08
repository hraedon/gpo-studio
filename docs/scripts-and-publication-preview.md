# Exporting startup scripts and previewing publication

Two panels, both added under Plan 034. Neither writes to Active Directory or
SYSVOL. The Scripts panel produces a GPMC backup for an administrator to import.
The publication preview only shows a plan.

Both surfaces are **lane-backed and awaiting requalification**: the lanes that
measured them (Scripts metadata and publication completeness) must re-run on
the estate after the 2026-10 requalification batch before their verdicts bind
the shipping code again. See the
[capability matrix](capability-matrix.md#post-10-domain-layers--landed-but-not-surfaced).

## Export startup scripts

1. Select a policy, then open **Scripts** in the workspace sidebar.
2. Add entries under **Startup scripts** (`scripts.ini`) and **PowerShell
   startup scripts** (`psscripts.ini`). Each entry is a command and optional
   parameters. Entries run in the order listed; use **Move up** and **Move
   down** to change it.
3. Select **Preview** to see the two INI files exactly as the backup will carry
   them, with the backup ID and the ZIP's SHA-256.
4. Select **Download backup** to save the ZIP. Import it with `Import-GPO` (or
   GPMC's Import Settings) as you would any backup.

The backup lists the scripts. It does **not** contain them: copy the script
files to where the commands expect them yourself.

### What the panel refuses, and why

The Scripts metadata lane (R10) imported one shape into Windows: two legacy and
one PowerShell **computer startup** entry, PowerShell run first, on a policy
with nothing else in it. Anything outside that is refused with a reason rather
than exported with a warning.

| Refused | Code | Why |
|---|---|---|
| User-side scripts (logon, logoff) | `scripts_user_side_unmeasured` | The user side would register the machine tool GUID, unmeasured. WI-071 tracks extending the lane |
| Shutdown scripts, and logon/logoff on the computer side | `scripts_trigger_unmeasured` | Only startup was imported |
| PowerShell run last, or no order set | `scripts_powershell_order_unmeasured` | Only "run PowerShell scripts first" was imported. A legacy-only policy needs no order |
| A policy that also has registry settings or preferences | `scripts_with_other_content_unmeasured` | No lane imported scripts together with other content, including the order of the Scripts entry in the extension list |
| A policy with a disabled side | `scripts_disabled_side_unmeasured` | The lane's policy had both sides enabled |
| No entries | `scripts_policy_empty` | An empty policy would register the Scripts extension with nothing to process |
| A line break or other control character | `script_control_character` | It would start a new line in the INI file |

Asynchronous entries, the PowerShell profile and interactive switches, and the
logon/logoff synchronous flags are refused by the export code itself
(`inexpressible_native_script_state`): the native files have no field for them.
A timeout or "legacy scripts first" setting cannot be sent at all, because the
files cannot carry it.

The panel offers only the shape it can export. The API
(`POST /api/gpos/{guid}/gpmc-backup-with-scripts` and `.../preview`) accepts the
full `ScriptPolicy` vocabulary so that each refusal arrives with its code.

### What the export does not establish

Every response carries these limitations (the ZIP in its
`X-GPO-Studio-Limitations` header):

- `payload_not_carried`: no script bodies are included. Windows accepted the
  metadata without them.
- `execution_unmeasured`: no lane has run the scripts or observed their order
  on a computer.
- `gpme_editing_unmeasured`: whether the Group Policy Management Editor can
  edit the imported entries has not been measured.
- `one_entry_shape_measured`: other entry counts, commands and parameters use
  the same encoding but were not themselves imported.

## Preview publication

1. Select a policy, then select **Publication preview** beside the export
   buttons.
2. Choose where the plan would publish: **Active Directory and SYSVOL**,
   **Active Directory only** or **SYSVOL only**.

The panel lists every step the publication planner would take, grouped by where
it acts, each with a coverage badge:

- **Measured**: the publication-completeness lane grades this kind of step
  against what Windows produces after `Import-GPO`: `update_gpt_ini`,
  `write_registry_pol`, and `copy_gpp_xml` for the two preference families
  the lane imported (computer Services, user Drives). `update_extension_lists`
  is measured only when every registration the list carries is one the lane
  imported: registry settings on either side, computer Services, user Drives.
  Under "Files the plan does not write", `GPO.cmt` is marked measured when the
  policy has no description: the lane checked that Windows writes none.
- **Unmeasured**: no lane covers it. This includes links, security filtering
  and the WMI filter association (`Import-GPO` restores none of them),
  `write_gpo_comment`, other preference families, an extension list that
  registers any other family or side (its extra entries and their order were
  never compared; the step names the families), and every rollback step.
- **Refused**: the planner will not publish this policy. The reasons are
  listed above the steps, and are the same errors `validate_publication_plan`
  reports.

The plan's payload digest binds its steps; the random plan ID is not shown. The
API is `GET /api/gpos/{guid}/publication-plan?target=ad|sysvol|both`.

### What the preview does not establish

- `nothing_here_writes`: it is a review of a plan. No step runs. Publication
  goes through the GPMC backup, `Import-GPO` and the reviewed `apply.ps1`.
- `ad_side_steps_unmeasured`: links, security filtering and WMI filter steps
  have no lane coverage.
- `one_shape_measured`: one policy shape was measured. A measured badge says
  the step is of a graded kind, not that every policy publishes the same way.
- `out_of_model_content_not_planned`: scripts and security templates are not on
  the GPO model, so the plan has no steps for them.
- `rollback_unmeasured`: no rollback step has been executed by any lane.
