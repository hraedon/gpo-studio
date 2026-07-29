# WP-1A Supplementary Authoring Matrix — Production-Derived

Derived from a 71-GPO production export. Patterns sanitized to lab
identifiers. Prioritized by production frequency and parser risk.

Frequency in source: Registry(31) > Printers(17) > Groups(8) >
Services(3) = PowerOptions(3) = Drives(3) > Folders(2) = Files(2) >
Shortcuts(1) = ScheduledTasks(1)

---

## Priority 1: High-frequency, ILT-heavy

### Printers (17 production instances)

GPO: `WI01A-Printers-GPMC` (User Configuration → Preferences →
Control Panel Settings → Printers → New → Shared Printer)

Production pattern: SharedPrinter with action="U", FilterOrgUnit ILT
(single and multi-OU OR groups), bypassErrors="1", default flag.

| # | Action | Printer path | Default | Comment | ILT |
|---|--------|-------------|---------|---------|-----|
| 1 | Update | `\\printsv\Lab-Color` | Yes | Lab color printer | FilterOrgUnit: `OU=Lab-WS,OU=Workstations,DC=ad,DC=hraedon,DC=com` |
| 2 | Update | `\\printsv\Lab-Mono` | No | | FilterOrgUnit: `OU=Lab-WS1,...` AND (OR: `OU=Lab-WS2,...`, `OU=Lab-WS3,...`) |
| 3 | Delete | `\\printsv\Old-Printer` | — | | (none) |

**Key patterns to exercise:**
- `default="1"` vs `default="0"`
- `skipLocal`, `deleteAll`, `persistent`, `deleteMaps`, `port` attrs
- Multiple FilterOrgUnit with `bool="OR"` chaining
- `userContext="0"` and `directMember="0"` on FilterOrgUnit

---

### Groups — production pattern supplement

Already captured. Production confirms: action="U", groupSid,
groupName, single ADD member, bypassErrors absent (defaults apply).
No new items needed.

---

## Priority 2: Medium-frequency, variant-heavy

### Services (3 production instances)

GPO: `WI01A-Services-GPMC` (Computer Configuration → Preferences →
Control Panel Settings → Services → New)

Production pattern: NTService with startupType="NOCHANGE",
serviceAction="START", failure recovery settings.

| # | Service name | Startup type | Service action | Timeout | Recovery (1st/2nd/3rd) | Reset delay | Restart delay |
|---|-------------|-------------|----------------|---------|----------------------|-------------|---------------|
| 1 | WinRM | No change | Start | 30 | Restart/Restart/Restart | 0 | 60000 |
| 2 | Spooler | Automatic | Restart | 60 | Restart/Restart/None | 86400 | 30000 |
| 3 | W32Time | No change | Stop | 30 | None/None/None | 0 | 0 |

**Key patterns to exercise:**
- `startupType="NOCHANGE"` vs explicit (AUTO_START, DEMAND_START, DISABLED)
- `serviceAction="START"` / `"RESTART"` / `"STOP"`
- Failure recovery: `firstFailure`, `secondFailure`, `thirdFailure` = RESTART/RECOVER/REBOOT/NONE
- ~~`resetFailCountDelay` and `restartServiceDelay` in milliseconds~~ —
  **corrected 2026-07-29 against the committed capture.** The table above
  records authored intent; the observed bytes disagree with "milliseconds":
  `resetFailCountDelay` appears verbatim in seconds (86400 for 1 day) while
  `restartServiceDelay` appears at 1000x the authored millisecond value
  (60000 ms → `60000000`, 30000 ms → `30000000`). GPMC also *omits*
  recovery attributes it never wrote (Spooler has no `thirdFailure`;
  W32Time has no recovery attributes at all). Full derivation and the
  confirmation-capture open question:
  `tests/fixtures/scenarios/gpp-services/native-recovery-units.json`
  (WI-022).
- Element is `<NTService>` inside `<NTServices>` root

---

### Power Options (3 production instances)

GPO: `WI01A-Power-GPMC` (Computer Configuration → Preferences →
Control Panel Settings → Power Options → New → Power Plan (At least
Windows 7))

Production pattern: `GlobalPowerOptionsV2` element (NOT legacy
`<PowerOption>`), FilterCollection with negated FilterOs predicates,
extensive power plan settings.

| # | Action | Plan name | Default | Key settings | ILT |
|---|--------|-----------|---------|-------------|-----|
| 1 | Update | High Performance (clone) | Yes | displayOff=0, sleepAfter=0, hibernate=0, lidClose=SLEEP | FilterCollection: NOT(OS=XP) AND NOT(OS=2K3) AND NOT(OS=2K3R2) |

**Key patterns to exercise:**
- Element is `<GlobalPowerOptionsV2>` not `<PowerOption>` — parser must handle
- `nameGuid` attribute (GUID of the power plan)
- `requireWakePwdAC/DC`, `turnOffHDAC/DC`, `sleepAfterAC/DC`
- `allowHybridSleepAC/DC` = ON/OFF
- `lidCloseAC/DC`, `pbActionAC/DC`, `strtMenuActionAC/DC` = SLEEP/SHUT_DOWN/HIBERNATE/DO_NOTHING
- `procStateMinAC/DC`, `procStateMaxAC/DC` (percentage integers)
- `adaptiveAC/DC` = ON/OFF
- Battery settings: `lowBatteryLvlAC/DC`, `critBatteryLvlAC/DC`, actions
- `FilterCollection` wrapping multiple negated `FilterOs` predicates

**⚠ PARSER RISK:** The current `GppPowerOptions` dataclass likely does
not model `GlobalPowerOptionsV2`. Check if the parser handles this
element or if it's captured as unknown content.

---

## Priority 3: Lower-frequency, edge-case-heavy

### Files (2 production instances)

GPO: `WI01A-Files-GPMC` (User Configuration)

Production pattern: File with action="U", wildcard `fromPath`,
`bypassErrors="1"`.

| # | Action | Source (fromPath) | Target (targetPath) | RO | Archive | Hidden | Suppress |
|---|--------|-------------------|---------------------|-----|---------|--------|----------|
| 1 | Update | `\\filesrv\app\vendor\*.*` | `%APPDATA%\Vendor App\` | No | No | No | No |
| 2 | Create | `\\filesrv\source\cönfig.xml` | `%TEMP%\Ünïcödé <"&>.xml` | Yes | Yes | Yes | Yes |
| 3 | Delete | (any) | `%USERPROFILE%\Desktop\old.txt` | — | — | — | — |

**Key patterns:** wildcard `*.*` in fromPath, trailing backslash in
targetPath (directory target), Unicode + XML entities.

---

### Folders (2 production instances)

GPO: `WI01A-Folders-GPMC` (User Configuration)

Production pattern: Folder with action="U", embedded `&quot;` entities
in name/path, `disabled="0"`, `bypassErrors="1"`.

| # | Action | Path | RO | Archive | Hidden |
|---|--------|------|-----|---------|--------|
| 1 | Update | `%APPDATA%\Vendor App\` | No | No | No |
| 2 | Create | `%USERPROFILE%\Pröjects <"&>` | Yes | Yes | Yes |

**⚠ PARSER RISK:** Production data has `name="&quot;"` and
`path="&quot;%userprofile%\...&quot;"` — literal embedded quote
entities. This tests XML entity handling in attribute values.

---

### Shortcuts (1 production instance)

GPO: `WI01A-Shortcuts-GPMC` (User Configuration)

Production pattern: Shortcut with action="R", `targetType="FILESYSTEM"`,
`pidl=""`, FilterGroup ILT, `removePolicy="1"`, `userContext="0"`.

| # | Action | Name | Target | Args | Start In | Icon | Shortcut Path | ILT |
|---|--------|------|--------|------|----------|------|---------------|-----|
| 1 | Replace | `Lab Manager` | `C:\Program Files\LabTools\manager.exe` | `--config lab` | `C:\Program Files\LabTools` | `C:\Program Files\LabTools\icon.ico, 0` | `%CommonDesktopDir%\Lab Tools\Manager` | FilterGroup: `HRAENET\Lab-WS-Group` |
| 2 | Create | `Ünïcödé App` | `notepad.exe` | `/A` | | | `%USERPROFILE%\Desktop\Ü <"&>.lnk` | (none) |
| 3 | Delete | `Old App` | — | — | — | — | — | — |

**Key patterns to exercise:**
- `targetType="FILESYSTEM"` (vs `SHELL` for special folders)
- `pidl=""` attribute (empty but present)
- `shortcutKey="0"` attribute
- `window=""` attribute (empty)
- `removePolicy="1"` + `userContext="0"` combination
- FilterGroup with `userContext="0"`, `primaryGroup="0"`, `localGroup="0"`
- `shortcutPath` with `%CommonDesktopDir%` variable

---

### Scheduled Tasks — production supplement

Already captured with TaskV2. Production confirms: ImmediateTaskV2 with
Task version **1.3** (our WS2025 canary produced 1.2). The version
difference is authoring-tool dependent, not OS dependent.

**Additional item for existing GPO:**

| # | Element | Action | Name | RunAs | Task version | Notes |
|---|---------|--------|------|-------|-------------|-------|
| (add to WI01A-SchedTasks-GPMC) | ImmediateTaskV2 | Create | `Defender-Activation` | NT AUTHORITY\System | **1.3** | S4U logon, HighestAvailable, StartWhenAvailable=true, UNC command path |

This tests that the parser handles both Task version 1.2 and 1.3.

---

## Priority 4: Not in production, include for completeness

### Registry (GPP, not .pol) — 31 production instances

Already handled. Production GPP Registry uses standard
`<Registry>` elements with hive/key/value patterns. No new
fixture needed beyond existing coverage.

### INI Files, Data Sources, Devices, Regional Options, Applications

Not present in the 71-GPO production export. Include one minimal
item each only if GPMC offers them (see primary authoring guide
Groups G–Q). These are low-risk completeness items.

---

## Cross-cutting production patterns to exercise

| Pattern | Where seen | Test |
|---------|-----------|------|
| FilterOrgUnit ILT | Printers (all 17) | New — OU targeting |
| FilterCollection (nested ILT) | PowerOptions | New — nested predicate container |
| FilterOrgUnit with bool="OR" chaining | Printers | New — multi-OU OR |
| bypassErrors="1" (stop_on_error=false) | All adapters | Already covered |
| removePolicy="1" | Shortcuts | Already covered |
| Wildcard paths (`*.*`) | Files | New |
| Embedded `&quot;` entities | Folders | New — XML entity edge case |
| Task version 1.3 | ScheduledTasks | New — add to existing GPO |
| `targetType="FILESYSTEM"` + `pidl` | Shortcuts | New |
| `startupType="NOCHANGE"` | Services | New |
| Failure recovery settings | Services | New |
| GlobalPowerOptionsV2 element | PowerOptions | New — **parser risk** |

---

## GPMC session plan (consolidated)

Total GPOs to author: **8 new + 1 supplement**

| # | GPO | Config | Items | Est. time |
|---|-----|--------|-------|-----------|
| 1 | WI01A-Printers-GPMC | User | 3 SharedPrinter | 10 min |
| 2 | WI01A-Services-GPMC | Computer | 3 NTService | 10 min |
| 3 | WI01A-Power-GPMC | Computer | 1 GlobalPowerOptionsV2 | 10 min |
| 4 | WI01A-Files-GPMC | User | 3 File | 5 min |
| 5 | WI01A-Folders-GPMC | User | 2 Folder | 5 min |
| 6 | WI01A-Shortcuts-GPMC | User | 3 Shortcut | 10 min |
| 7 | WI01A-EnvVars-GPMC | User | 4 Environment | 5 min |
| 8 | WI01A-IniFiles-GPMC | User | 3 Ini | 5 min |
| 9 | (supplement) WI01A-SchedTasks-GPMC | Computer | +1 ImmediateTaskV2 v1.3 | 3 min |

Optional (if GPMC offers them):
- WI01A-DataSources-GPMC (1 ODBC DSN)
- WI01A-Devices-GPMC (1 enable/disable)
- WI01A-Regional-GPMC (1 locale)
- WI01A-Apps-GPMC (1 legacy app)

Same workflow: author in GPMC Editor, close/reopen each item,
Backup-GPO, transfer to `/tmp/gpp-gpmc-native-3`.
