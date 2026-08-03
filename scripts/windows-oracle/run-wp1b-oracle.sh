#!/usr/bin/env bash
# Plan 033 WP-1B writer-conformance run, against the disposable evidence estate.
#
# The estate's guests have no networking at all, so they are reached
# controller -> WinRM -> Hyper-V host -> PowerShell Direct -> guest via
# psdirect.ps1. The harness is invoked DIRECTLY: PowerShell Direct carries the
# credential through the hypervisor, so the resulting logon authenticates
# outward to AD and SYSVOL. There is no scheduled-task launcher and therefore no
# schtasks /RP password argument.
#
#   GPO_STUDIO_LAB_HOST=<hyper-v host> GPO_STUDIO_LAB_GUEST=<guest> \
#       ACB_VAULT_ENV=~/.claude/evidence-lab.env \
#       acb exec cred:lab-hyperv-control cred:lab-guest-bootstrap -- \
#           bash scripts/windows-oracle/run-wp1b-oracle.sh
#
# Re-pointing a lane is not a variable change: every certification is bound to
# the environment recorded in its own manifest, so a new host needs a fresh
# qualification run and a re-freeze of docs/plan-033/environment-spec.md. The
# transport is recorded in the verdict.

set -euo pipefail

TRANSPORT=psdirect
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

GUEST_SCRIPTS='C:\gpo-studio\scripts'
GUEST_OUT='C:\gpo-studio\out'

: "${GPO_STUDIO_LAB_HOST:?GPO_STUDIO_LAB_HOST not set}"
: "${GPO_STUDIO_LAB_GUEST:?GPO_STUDIO_LAB_GUEST not set}"
# psdirect.ps1 enforces the composed acb checkout itself; fail early here so the
# lane does not build candidates it cannot deliver.
: "${HYPERV_CONTROL_USERNAME:?composed acb checkout missing (cred:lab-hyperv-control)}"
: "${GUEST_BOOTSTRAP_USERNAME:?composed acb checkout missing (cred:lab-guest-bootstrap)}"

psdirect() {
    pwsh -NoProfile -File "$SCRIPT_DIR/psdirect.ps1" \
        -LabHost "$GPO_STUDIO_LAB_HOST" -Guest "$GPO_STUDIO_LAB_GUEST" "$@"
}

STAMP="$(date +%Y%m%d%H%M%S)"
CANDIDATE_DIR="/tmp/opencode/wp1b-candidates-$STAMP"
uv run python scripts/plan-033/build-wp1b-candidates.py "$CANDIDATE_DIR"

LOCAL_DIR="/tmp/opencode/wp1b-oracle-run-$STAMP"
mkdir -p "$LOCAL_DIR/deployed"

PREPARE="New-Item -ItemType Directory -Force -Path '$GUEST_SCRIPTS','$GUEST_OUT' | Out-Null; Remove-Item -Recurse -Force '$GUEST_SCRIPTS\\wp1b' -ErrorAction SilentlyContinue; Get-ChildItem '$GUEST_OUT' -Directory -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force"
LATEST_RUN="(Get-ChildItem '$GUEST_OUT' -Directory | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName"

psdirect -Action exec -Command "$PREPARE" >/dev/null
psdirect -Action push -LocalPath "$SCRIPT_DIR/run-wp1b-writer.ps1" \
    -RemotePath "$GUEST_SCRIPTS\\run-wp1b-writer.ps1" >/dev/null
psdirect -Action push -LocalPath "$CANDIDATE_DIR" -RemotePath "$GUEST_SCRIPTS\\wp1b" >/dev/null

# WP-1B imports, reports, re-backs-up and removes one GPO per candidate, so it
# needs materially longer than the single-GPO lanes.
psdirect -Action exec -TimeoutSeconds 1500 -Command \
    "& '$GUEST_SCRIPTS\\run-wp1b-writer.ps1' -CandidateRoot '$GUEST_SCRIPTS\\wp1b' -OutputDir '$GUEST_OUT'"

RUN_DIR=$(psdirect -Action exec -Command "$LATEST_RUN")
RUN_DIR=$(printf '%s' "$RUN_DIR" | tr -d '\r\n' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')
if [[ -z "$RUN_DIR" ]]; then
    echo "ERROR: no WP-1B run directory produced" >&2
    exit 1
fi

# Retrieve the harness files that actually executed on Windows (not source-tree
# copies) so the finalizer can bind them to the recorded commit.
# Pulling a directory delivers its CONTENTS, matching `scp -r host:dir/. local/`.
psdirect -Action pull -RemotePath "$RUN_DIR" -LocalPath "$LOCAL_DIR" >/dev/null
psdirect -Action pull -RemotePath "$GUEST_SCRIPTS\\run-wp1b-writer.ps1" \
    -LocalPath "$LOCAL_DIR/deployed" >/dev/null

# Locally-executed scripts: the source-tree copy IS the executed copy.
cp "$SCRIPT_DIR/run-wp1b-oracle.sh" "$REPO_ROOT/scripts/plan-033/build-wp1b-candidates.py" \
    "$SCRIPT_DIR/psdirect.ps1" "$LOCAL_DIR/"

echo "LOCAL_RUN_DIR=$LOCAL_DIR"
echo "CANDIDATE_DIR=$CANDIDATE_DIR"
uv run python scripts/windows-oracle/finalize_wp1b_run.py "$LOCAL_DIR" \
    --candidate-root "$CANDIDATE_DIR" --repo-root "$REPO_ROOT" --transport "$TRANSPORT"
