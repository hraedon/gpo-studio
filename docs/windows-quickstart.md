# Windows quickstart

This guide installs GPO Studio for one Windows administrator, reachable only
from that computer. You do not need Administrator rights, IIS, a Windows
service, Git, `uv`, or permission to run PowerShell scripts.

GPO Studio is a local application for a single operator. The supported
Windows deployment is:

```text
your browser -> http://127.0.0.1:8765 -> GPO Studio -> local SQLite workspace
```

Do not change `127.0.0.1` to a server name or `0.0.0.0`. GPO Studio has no
login screen and does not terminate TLS. A shared or unattended deployment
would need an authenticated reverse proxy, TLS, service lifecycle management
and a separate security review, and is not a supported installation. An
authenticated multi-user service is tracked in
[`Plan 032`](../plans/032-hardened-hosted-control-plane.md).

## What you need

- A supported Windows desktop or server where you can sign in interactively.
- 64-bit Python 3.13 or 3.14.
- Microsoft Edge or Firefox ESR.
- Internet access during installation, so `pip` can download the wheel's
  Python dependencies, unless your administrator provides an internal package
  source.
- These two files from the same GPO Studio release:
  - `gpo_studio-<version>-py3-none-any.whl`
  - `SHA256SUMS`

The commands use Windows PowerShell 5.1, which ships with supported Windows
versions. Run PowerShell as your normal user, not as Administrator.

## 1. Install Python once

If this prints Python 3.13, go to step 2:

```powershell
py -3.13 --version
```

If your organization provides Python 3.14 instead, run `py -3.14 --version`
and replace `-3.13` with `-3.14` in step 3.

Otherwise, download the 64-bit Python 3.13 installer from the
[official Python website](https://www.python.org/downloads/windows/). Choose
**Install Now** for the current user and leave the Python Launcher option
enabled. Do not select the experimental free-threaded build. Close and reopen
PowerShell, then run the version command again.

The Python
[Windows installation guide](https://docs.python.org/3.13/using/windows.html)
explains the installer and the `py` launcher in more detail.

## 2. Download and verify the release

On the [GitHub Releases page](https://github.com/hraedon/gpo-studio/releases),
download the wheel and `SHA256SUMS` listed under **Assets** for one release
into your Downloads folder. The ZIP from the green **Code** button, like any
Git checkout, contains source code, not a built wheel.

For release-candidate testing, expand the prerelease entry and download its
wheel and `SHA256SUMS`. When recording release-gate evidence, do not substitute
a wheel from an Actions run or a local build.

Open the Downloads folder in File Explorer, click the address bar, type
`powershell` and press Enter. Paste this whole block into PowerShell. It stops
with an error if it finds no wheel, more than one wheel, no matching checksum,
or a damaged file.

```powershell
$Wheels = @(Get-ChildItem -File .\gpo_studio-*.whl)
if ($Wheels.Count -ne 1) {
    throw "Expected exactly one gpo_studio wheel in this folder; found $($Wheels.Count)."
}
$Wheel = $Wheels[0]
$Pattern = [regex]::Escape($Wheel.Name) + '$'
$ChecksumLine = Select-String -Path .\SHA256SUMS -Pattern $Pattern | Select-Object -First 1
if (-not $ChecksumLine) {
    throw "SHA256SUMS has no entry for $($Wheel.Name)."
}
$Expected = ($ChecksumLine.Line -split '\s+')[0].ToUpperInvariant()
$Actual = (Get-FileHash -Algorithm SHA256 $Wheel.FullName).Hash
if ($Actual -ne $Expected) {
    throw "Checksum mismatch. Delete the downloads and obtain the release again."
}
Write-Host "Checksum verified for $($Wheel.Name)"
```

Do not continue unless the last line says `Checksum verified`.

## 3. Install GPO Studio

In the same PowerShell window, run:

```powershell
$Root = Join-Path $env:LOCALAPPDATA "GPO Studio"
$Venv = Join-Path $Root "venv"
$Data = Join-Path $Root "data"
$Backups = Join-Path $Root "backups"
New-Item -ItemType Directory -Force $Root, $Data, $Backups | Out-Null
py -3.13 -m venv $Venv
$Python = Join-Path $Venv "Scripts\python.exe"
$App = Join-Path $Venv "Scripts\gpo-studio.exe"
& $Python -m pip install --upgrade pip
& $Python -m pip install $Wheel.FullName
& $App --help
```

The last command should print GPO Studio's help text. The application and
its workspace are now under `%LOCALAPPDATA%\GPO Studio`. The commands do not
activate the virtual environment, so PowerShell execution policy does not
block them.

If your approved Python is 3.14, change `-3.13` to `-3.14` in the `py`
command.

## 4. Start and stop the application

In the same window, run:

```powershell
& $App run --host 127.0.0.1 --port 8765 --database (Join-Path $Data "gpo-studio.db")
```

Keep that window open while you use GPO Studio. In Microsoft Edge or Firefox,
go to <http://127.0.0.1:8765>. The first start creates the workspace database.

To stop GPO Studio, press **Ctrl+C** once in the PowerShell window, or close
the window.

To start it again later, open PowerShell normally and run:

```powershell
$Root = Join-Path $env:LOCALAPPDATA "GPO Studio"
$App = Join-Path $Root "venv\Scripts\gpo-studio.exe"
$Database = Join-Path $Root "data\gpo-studio.db"
& $App run --host 127.0.0.1 --port 8765 --database $Database
```

## 5. Confirm the installation

With GPO Studio running, open a second PowerShell window and run:

```powershell
Invoke-RestMethod http://127.0.0.1:8765/api/health
```

The result should report a healthy application. Then follow the
[five-minute guided workflow](installation.md#five-minute-guided-workflow) to
create a disposable policy and export a bundle.

## Back up the workspace

Stop GPO Studio before a planned upgrade. Then run:

```powershell
$Root = Join-Path $env:LOCALAPPDATA "GPO Studio"
$App = Join-Path $Root "venv\Scripts\gpo-studio.exe"
$Database = Join-Path $Root "data\gpo-studio.db"
$BackupFolder = Join-Path $Root "backups"
$Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$Backup = Join-Path $BackupFolder "workspace-$Stamp.db"
& $App workspace backup --database $Database --output $Backup
# Verify the backup without touching it: restore a throwaway copy and check that.
$Probe = Join-Path $BackupFolder "verify-$Stamp.db"
& $App workspace restore $Backup $Probe
& $App workspace check --database $Probe --full
Remove-Item "$Probe*"  # the copy and its lock file
```

The restore itself verifies the backup's SHA-256 against its sidecar, its
schema version and its row counts; the check then proves the restored copy is
intact. Do not continue unless both commands succeed.

> **Never run `workspace check` on a backup file.** In GPO Studio 1.0.0,
> `workspace check` writes its result into the database it checks. Run on a
> backup, that changes the file after its `.meta.json` sidecar recorded the
> file's SHA-256, and every later restore of that backup fails with `Backup
> database checksum mismatch` (WI-072). 1.1.0's check is read-only, but the
> procedures here must also work while 1.0.0 is installed, so they verify a
> backup by restoring it to a throwaway file and checking that file instead.

Keep both the `.db` file and its `.meta.json` sidecar, and copy important
backups to a separately protected location. Restore and retention are covered
in [workspace backup and recovery](workspace-recovery.md).

## Upgrade to another release

1. Stop GPO Studio with **Ctrl+C**.
2. Create and verify a backup using the commands above. Make it **before**
   you install the new wheel, with the release you are upgrading from, and
   note the backup's file name: it is your only way back (see
   [Roll back an upgrade](#roll-back-an-upgrade)).
3. Download the new wheel and its `SHA256SUMS` into an otherwise empty folder.
4. Run the checksum block from step 2.
5. Run this block in the same PowerShell window:

```powershell
$Root = Join-Path $env:LOCALAPPDATA "GPO Studio"
$Python = Join-Path $Root "venv\Scripts\python.exe"
$App = Join-Path $Root "venv\Scripts\gpo-studio.exe"
& $Python -m pip install --upgrade $Wheel.FullName
& $App --help
```

6. Start GPO Studio and confirm the health endpoint and existing policies.
   The health result reports the new `version` and the workspace's
   `schema_version`.

A release can change the workspace format. The first start of such a release
upgrades the workspace file in place, and it does not make a backup for you.
After that, the earlier release refuses to open the file. For example, 1.1.0
moves the workspace from schema 1 to schema 4, and 1.0.0 then stops at start-up
with `Workspace schema version 4 is newer than this version of GPO Studio
supports (1)`. That is why the backup in step 2 must come first.

If the upgrade fails, stop the application and follow the
[backup and restore procedures](workspace-recovery.md#backup-and-restore-procedures).
Do not delete the backup that preceded the upgrade.

### Roll back an upgrade

Rolling back means reinstalling the earlier wheel **and** restoring the backup
you made before the upgrade. Reinstalling the wheel alone is not enough once
the new release has started, because the earlier release cannot read the
upgraded workspace. Changes made after the upgrade are not in that backup.
The restore keeps the upgraded file beside it as a `.bak` file, so those
changes are not deleted.

1. Stop GPO Studio with **Ctrl+C**.
2. Put the earlier release's wheel and its `SHA256SUMS` in an otherwise empty
   folder and run the checksum block from step 2 of the install.
3. In the same PowerShell window, set `$Backup` to the pre-upgrade backup and
   run:

```powershell
$Root = Join-Path $env:LOCALAPPDATA "GPO Studio"
$Python = Join-Path $Root "venv\Scripts\python.exe"
$App = Join-Path $Root "venv\Scripts\gpo-studio.exe"
$Database = Join-Path $Root "data\gpo-studio.db"
$Backup = Join-Path $Root "backups\workspace-YYYYMMDD-HHMMSS.db"  # your pre-upgrade backup
& $Python -m pip install $Wheel.FullName
& $App workspace restore $Backup $Database --replace
& $App workspace check --database $Database --full
```

`workspace restore` refuses a backup whose SHA-256 no longer matches its
sidecar, so it is the verification step for the backup. The check runs on the
restored workspace, never on the backup.

4. Start GPO Studio and confirm the health endpoint reports the earlier
   `version` and that your policies are present.

Never restore a backup taken *after* the upgrade into the earlier release: it
holds the upgraded format, and the earlier release refuses it. More detail is
in [workspace recovery](workspace-recovery.md#upgrading-and-rolling-back-across-a-schema-change).

## Uninstall

Stop GPO Studio first. To remove the application but keep the workspace and
backups:

```powershell
$Venv = Join-Path $env:LOCALAPPDATA "GPO Studio\venv"
Remove-Item -Recurse -Force $Venv
```

The `data` and `backups` folders remain. Delete the whole
`%LOCALAPPDATA%\GPO Studio` folder only if you want to remove all workspaces
and backups.

## Troubleshooting

### `py` is not recognized

Close and reopen PowerShell after installing Python. If it still fails, rerun
the official installer, choose **Modify**, and enable the Python Launcher.

### PowerShell opened in the wrong folder

In File Explorer, open the folder with the wheel and `SHA256SUMS`, click the
address bar, type `powershell` and press Enter. `Get-Location` shows the
current folder.

### More than one wheel was found

Move old GPO Studio wheels out of the folder. Keep exactly one wheel and the
`SHA256SUMS` from its release, then rerun the verification block.

### The port is already in use

Close any older GPO Studio PowerShell window. If another application owns
port 8765, use a different loopback port in both the start command and the URL,
for example `--port 8766` and `http://127.0.0.1:8766`.

### Windows Firewall prompts for access

Do not allow public or private network access. The supported bind address,
`127.0.0.1`, is reachable only from the same computer.

### The browser cannot connect

Check that the PowerShell window is still open and shows no error. Use the
literal URL <http://127.0.0.1:8765>, not the computer's hostname.

### Installation cannot reach the Internet

The GPO Studio wheel does not include its third-party dependencies. Ask your
administrator for access to an approved Python package source, or for an
offline wheelhouse with GPO Studio and all its locked dependencies. Do not
download replacement packages from unofficial websites.
