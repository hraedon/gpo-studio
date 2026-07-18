# Windows quickstart

This guide installs GPO Studio for one Windows administrator and keeps it
available only on that computer. It does not require Administrator privileges,
IIS, a Windows service, Git, `uv`, or PowerShell script execution.

GPO Studio 1.0 is a local, single-operator application. The supported Windows
deployment is:

```text
your browser -> http://127.0.0.1:8765 -> GPO Studio -> local SQLite workspace
```

Do not change `127.0.0.1` to a server name or `0.0.0.0`. GPO Studio has no
login screen and does not terminate TLS. A shared or unattended deployment
needs an authenticated reverse proxy, TLS, service lifecycle management, and a
separate security review; it is not a supported 1.0 installation.
The future authenticated, multi-user service profile is tracked in
[`Plan 032`](../plans/032-hardened-hosted-control-plane.md).

## What you need

- A supported Windows desktop or server where you can sign in interactively.
- 64-bit Python 3.13 or 3.14.
- Microsoft Edge or Firefox ESR.
- Internet access during installation so `pip` can obtain the wheel's Python
  dependencies, unless your administrator provides an internal package source.
- These two files from the same GPO Studio release:
  - `gpo_studio-<version>-py3-none-any.whl`
  - `SHA256SUMS`

The commands below use Windows PowerShell 5.1, which is included with supported
Windows versions. Run PowerShell as your normal user, not as Administrator.

## 1. Install Python once

If this command prints Python 3.13, continue to step 2:

```powershell
py -3.13 --version
```

If your organization provides Python 3.14 instead, run `py -3.14 --version`
and replace `-3.13` with `-3.14` in step 3.

Otherwise, download a 64-bit Python 3.13 installer from the
[official Python website](https://www.python.org/downloads/windows/). Choose
**Install Now** for the current user and leave the Python Launcher option
enabled. Close and reopen PowerShell, then run the version command again.

The Python project's
[Windows installation guide](https://docs.python.org/3.13/using/windows.html)
explains the installer and `py` launcher in more detail. Do not select the
experimental free-threaded build.

## 2. Download and verify the release

Download the wheel and `SHA256SUMS` from the same GitHub release into your
Downloads folder. Open that folder in File Explorer, click the address bar,
type `powershell`, and press Enter.

Use the files listed under **Assets** on the
[GitHub Releases page](https://github.com/hraedon/gpo-studio/releases). The green
**Code** button's ZIP file and Git source checkouts contain source code, not a
built wheel. For release-candidate testing, expand the prerelease entry and
download its wheel and `SHA256SUMS`; do not substitute a wheel from an Actions
run or a local build when recording release-gate evidence.

Copy this entire block into PowerShell. It stops with an error if it finds no
wheel, more than one wheel, no matching checksum, or a damaged file.

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

Keep the same PowerShell window open and copy this block:

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

The final command should show GPO Studio's help text. The application and its
workspace are now under `%LOCALAPPDATA%\GPO Studio`. These commands deliberately
do not activate the virtual environment, so PowerShell execution-policy
settings do not get in the way.

If your approved Python version is 3.14 instead, replace `-3.13` with `-3.14`
in the one `py` command.

## 4. Start and stop the application

In the same window, run:

```powershell
& $App run --host 127.0.0.1 --port 8765 --database (Join-Path $Data "gpo-studio.db")
```

Leave that PowerShell window open while using GPO Studio. Open Microsoft Edge
or Firefox and go to <http://127.0.0.1:8765>. The first start creates the
workspace database.

To stop GPO Studio, return to PowerShell and press **Ctrl+C** once. Closing the
PowerShell window also stops it.

On a later day, open PowerShell normally and use this complete start block:

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
& $App workspace check --database $Backup --full
```

Keep both the `.db` file and its `.meta.json` sidecar. Copy important backups
to a separately protected location. See
[workspace backup and recovery](workspace-recovery.md) for restore procedures
and retention guidance.

## Upgrade to another release

1. Stop GPO Studio with **Ctrl+C**.
2. Create and verify a backup using the commands above.
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

If an upgrade fails, stop the application and follow the
[backup and restore procedures](workspace-recovery.md#backup-and-restore-procedures).
Do not delete the backup that preceded the upgrade.

## Uninstall

Stop GPO Studio first. To remove only the application while preserving the
workspace and backups:

```powershell
$Venv = Join-Path $env:LOCALAPPDATA "GPO Studio\venv"
Remove-Item -Recurse -Force $Venv
```

The `data` and `backups` folders remain. Delete the entire
`%LOCALAPPDATA%\GPO Studio` folder only if you intentionally want to remove all
workspaces and backups.

## Troubleshooting

### `py` is not recognized

Close and reopen PowerShell after installing Python. If it still fails, rerun
the official installer, choose **Modify**, and enable the Python Launcher.

### PowerShell opened in the wrong folder

In File Explorer, open the folder containing the wheel and `SHA256SUMS`, click
the address bar, type `powershell`, and press Enter. Running `Get-Location`
shows the current folder.

### More than one wheel was found

Move old GPO Studio wheels out of the download folder. Keep exactly one wheel
and the `SHA256SUMS` file from its release, then rerun the verification block.

### The port is already in use

Close an older GPO Studio PowerShell window. If another local application owns
port 8765, choose a different loopback port in both the start command and URL,
for example `--port 8766` and `http://127.0.0.1:8766`.

### Windows Firewall prompts for access

Do not enable public or private network access. The supported bind address is
`127.0.0.1`, which is reachable only from the same computer.

### The browser cannot connect

Confirm that the PowerShell window is still open and does not show an error.
Use the literal URL <http://127.0.0.1:8765>, not the computer's hostname.

### Installation cannot reach the Internet

The GPO Studio wheel does not contain its third-party dependencies. Ask your
administrator for access to an approved Python package source or for an
offline wheelhouse containing GPO Studio and all locked dependencies. Do not
download replacement packages from unofficial websites.
