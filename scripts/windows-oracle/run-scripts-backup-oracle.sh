#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
: "${GPO_STUDIO_LAB_HOST:?}"
: "${GPO_STUDIO_LAB_GUEST:?}"
: "${HYPERV_CONTROL_USERNAME:?}"
: "${GUEST_BOOTSTRAP_USERNAME:?}"

psdirect() {
    pwsh -NoProfile -File "$SCRIPT_DIR/psdirect.ps1" \
        -LabHost "$GPO_STUDIO_LAB_HOST" -Guest "$GPO_STUDIO_LAB_GUEST" "$@"
}

STAMP="$(date +%Y%m%d%H%M%S)-$$"
GUEST_ROOT="C:\gpo-studio\runs\scripts-r10-$STAMP"
GUEST_SCRIPTS="$GUEST_ROOT\scripts"
GUEST_OUT="$GUEST_ROOT\out"
CANDIDATE_DIR="/tmp/opencode/scripts-r10-candidate-$STAMP"
LOCAL_DIR="/tmp/opencode/scripts-r10-run-$STAMP"
BUILDER_STDOUT="$CANDIDATE_DIR/builder.stdout.txt"
mkdir -p "$CANDIDATE_DIR" "$LOCAL_DIR/deployed"
uv run python scripts/plan-033/build-scripts-backup-candidate.py "$CANDIDATE_DIR" |
    tee "$BUILDER_STDOUT"

PREPARE="if(Test-Path '$GUEST_ROOT'){throw 'run root exists'}; New-Item -ItemType Directory -Force '$GUEST_SCRIPTS','$GUEST_OUT'|Out-Null"
psdirect -Action exec -Command "$PREPARE" >/dev/null
psdirect -Action push -LocalPath "$SCRIPT_DIR/run-scripts-backup-import.ps1" \
    -RemotePath "$GUEST_SCRIPTS\run-scripts-backup-import.ps1" >/dev/null
psdirect -Action push -LocalPath "$CANDIDATE_DIR/studio-scripts-backup.zip" \
    -RemotePath "$GUEST_SCRIPTS\candidate.zip" >/dev/null

set +e
psdirect -Action exec -TimeoutSeconds 420 -Command \
    "& '$GUEST_SCRIPTS\run-scripts-backup-import.ps1' -CandidateZip '$GUEST_SCRIPTS\candidate.zip' -OutputDir '$GUEST_OUT'"
GUEST_STATUS=$?
set -e
RUN_DIR=$(psdirect -Action exec -Command \
    "\$d=@(Get-ChildItem '$GUEST_OUT' -Directory); if(\$d.Count-ne 1){throw 'expected one run'}; \$d[0].FullName")
RUN_DIR=$(printf '%s' "$RUN_DIR" | tr -d '\r\n')
psdirect -Action pull -RemotePath "$RUN_DIR" -LocalPath "$LOCAL_DIR" >/dev/null
psdirect -Action pull -RemotePath "$GUEST_SCRIPTS\run-scripts-backup-import.ps1" \
    -LocalPath "$LOCAL_DIR/deployed" >/dev/null
cp "$BUILDER_STDOUT" "$LOCAL_DIR/"
cp "$SCRIPT_DIR/run-scripts-backup-oracle.sh" \
    "$SCRIPT_DIR/finalize_scripts_backup_run.py" "$SCRIPT_DIR/psdirect.ps1" \
    "$REPO_ROOT/scripts/plan-033/build-scripts-backup-candidate.py" \
    "$REPO_ROOT/src/gpo_studio/"{export.py,script_policy.py,canonical.py,gpp.py,model.py,registry_pol.py,validation.py,oracle_evidence.py,xml_safety.py} \
    "$LOCAL_DIR/"
cp "$SCRIPT_DIR/finalize_scripts_backup_run.py" "$REPO_ROOT/src/gpo_studio/oracle_evidence.py" "$LOCAL_DIR/"

echo "LOCAL_RUN_DIR=$LOCAL_DIR"
echo "CANDIDATE_DIR=$CANDIDATE_DIR"
set +e
uv run python "$LOCAL_DIR/finalize_scripts_backup_run.py" "$LOCAL_DIR" \
    --candidate-root "$CANDIDATE_DIR" --repo-root "$REPO_ROOT"
FINALIZER_STATUS=$?
set -e
if [[ $GUEST_STATUS -ne 0 ]]; then
    exit "$GUEST_STATUS"
fi
exit "$FINALIZER_STATUS"
