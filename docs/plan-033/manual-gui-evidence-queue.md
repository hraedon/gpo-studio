# Plan 033 manual and GUI evidence queue

Status: active 2026-07-30. This is the consolidated operator queue for work
that cannot be completed through the automated Linux-to-Windows harness. A
checked box means evidence was captured, not merely that the UI was opened.

## Safety rules

- Use only the isolated lab and an unlinked disposable GPO.
- Do not enter or capture a real service-account password. GPO Studio blocks
  `cpassword`; this lane does not test password handling.
- Keep raw output outside git until it has passed identifier and secret
  review. Stage it under `C:\gpo-studio\manual`, then let the agent retrieve,
  inspect, sanitize, and curate it.
- Do not apply the WI-022 policy to an endpoint. This capture is about what
  GPMC writes, not changing a live service.
- Leave the disposable GPO unlinked. Tell the agent when capture is complete;
  the automated cleanup will remove it and strictly re-query for absence.

## WI-022: Services recovery fields

Purpose: settle the fields that MS-GPPREF defines but the existing native
capture does not exercise, and settle whether GPMC writes `REBOOT` and
`RUNCOMMAND` recovery actions.

- [x] RDP to `mvmcitest01` and open Group Policy Management.
- [x] Create unlinked GPO `GPOStudio-WI022-Manual-7-30-330PM`.
- [x] Edit Computer Configuration > Preferences > Control Panel Settings >
  Services.
- [x] Add an Update item for the built-in `Spooler` service.
- [x] Set first failure to Run a Program, second failure to Restart the
  Service, and subsequent failure to Restart the Computer.
- [x] Set reset-fail-count to 2 days and restart-service delay to 7 minutes.
- [x] Set the recovery program to `C:\Windows\System32\cmd.exe`, arguments to
  `/c exit 0`, and enable the UI option that appends the failure count.
- [x] Set restart-computer delay to 3 minutes and message to
  `Synthetic GPO Studio recovery evidence`.
- [x] Add a second Update item for `W32Time`; select Local System and enable
  desktop interaction if GPMC permits that combination. Do not select or enter
  a named account or password.
- [x] Save and close the editor, then take screenshots of both completed
  property dialogs with every recovery field visible.
- [x] In an elevated Windows PowerShell 5.1 session, run the capture commands
  below after replacing `<GPO-NAME>` with the exact disposable name.

```powershell
$root = 'C:\gpo-studio\manual\wi022-services'
New-Item -ItemType Directory -Force -Path $root, "$root\backup" | Out-Null
Backup-GPO -Name '<GPO-NAME>' -Path "$root\backup" -Comment 'WI-022 manual GPMC capture'
Get-GPOReport -Name '<GPO-NAME>' -ReportType Xml -Path "$root\gpreport.xml"
```

- [x] Tell the agent the exact GPO name and that capture is complete. The agent
  will retrieve `C:\gpo-studio\manual\wi022-services`, compare `Services.xml`
  with the report, sanitize the fixture, and remove the disposable GPO.

Expected questions answered by this one capture:

- Does GPMC emit `RUNCOMMAND` and `REBOOT`, or a different vocabulary?
- Are command fields written as `program`, `args`, and `append`, and what is
  the value/type of `append`?
- What units does a distinctive 7-minute restart-service delay use?
- How are `restartComputerDelay` and `restartMessage` represented?
- Does current GPMC emit `accountName` and `interact` for Local System?

Capture result (2026-07-30): all questions settled. GPMC emitted `RUNCMD`,
`REBOOT`, `program`, `args`, `append="1"`, `restartMessage`,
`accountName="LocalSystem"`, and `interact="1"`. Seven minutes became
`restartServiceDelay="420000"`; three minutes became
`restartComputerDelay="180000"`, proving both restart delays are milliseconds.
No change omitted `serviceAction`. The sanitized fixture is
`tests/fixtures/native-gpp-gpmc/WI01A-ServicesRecovery-GPMC/`. Brokered cleanup
removed the disposable GPO and a strict `Get-GPO -All` re-query returned zero
matching objects. Because these observations invalidate the accepted WI-022
delay and literal assumptions, WI-024 tracks the correction and clean WP-1B
recertification rather than reopening the completed item.

## WI-023: endpoint family collision

The complete Server 2025 GPMC Product dropdown was already transcribed on
2026-07-28; no additional dropdown transcription is required. Browser warning,
API preservation, export-manifest warning, Chromium, Firefox smoke, and
automated accessibility coverage are also automated.

- [ ] Identify or provide one disposable domain-joined Windows Server 2016
  endpoint and one disposable domain-joined Windows Server 2025 endpoint in the
  same isolated evidence domain.
- [ ] Confirm both endpoints may receive a temporary unlinked-then-linked test
  GPO, run `gpupdate /force`, write one synthetic HKLM marker, and be restored
  from snapshot or cleaned afterward.
- [ ] Provide the host names to the agent. No manual GPMC authoring is needed;
  the agent will generate the exact `WINTHRESHOLDSRV` candidate, link it only to
  disposable test OUs, collect registry and Group Policy operational-log
  evidence from both hosts, and perform strict cleanup.
- [ ] Optional but valuable: identify a similarly disposable Windows 10 and
  Windows 11 pair so the `WINTHRESHOLD` client collision can receive the same
  endpoint proof.

## Workflow gates

- [x] Human-review and accept WI-022 and WI-023 after their adversarial passes.
- [x] Authorize and create source commit `b4b9049`, then rerun WP-1B from the
  clean tree. Certified run `wp1b-writer-20260730151953-6878` passed all seven
  candidates under the pre-capture Services semantics; the earlier dirty-tree
  exploratory run was not promoted.
- [ ] Commit the WI-024 capture-backed correction and rerun WP-1B from that
  clean source before recertifying Services.
