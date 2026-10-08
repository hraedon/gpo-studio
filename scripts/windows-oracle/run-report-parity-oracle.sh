#!/usr/bin/env bash
# Plan 034: the report-parity lane.
#
# Builds the candidate on the controller (every Windows-produced backup in the
# corpus, plus Studio's inventory of each), imports each backup into its own
# disposable GPO on the guest, takes a fresh Get-GPOReport -ReportType Xml,
# authors one more GPO there with Set-GPRegistryValue, and grades Windows'
# reports against Studio's import of the same bytes. The expectation never
# leaves the controller, because the guest is the thing being measured.
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
GUEST_ROOT="C:\gpo-studio\runs\report-parity-$STAMP"
GUEST_SCRIPTS="$GUEST_ROOT\scripts"
GUEST_OUT="$GUEST_ROOT\out"
CANDIDATE_DIR="${TMPDIR:-/tmp}/gpo-studio/report-parity-candidate-$STAMP"
LOCAL_DIR="${TMPDIR:-/tmp}/gpo-studio/report-parity-run-$STAMP"
BUILDER_STDOUT="$CANDIDATE_DIR/builder.stdout.txt"
mkdir -p "$CANDIDATE_DIR" "$LOCAL_DIR/deployed"
(cd "$REPO_ROOT" && uv run python scripts/plan-033/build-report-parity-candidate.py "$CANDIDATE_DIR") |
    tee "$BUILDER_STDOUT"

PREPARE="if(Test-Path '$GUEST_ROOT'){throw 'run root exists'}; New-Item -ItemType Directory -Force '$GUEST_SCRIPTS','$GUEST_OUT'|Out-Null"
psdirect -Action exec -Command "$PREPARE" >/dev/null
psdirect -Action push -LocalPath "$SCRIPT_DIR/run-report-parity.ps1" \
    -RemotePath "$GUEST_SCRIPTS\run-report-parity.ps1" >/dev/null
psdirect -Action push -LocalPath "$CANDIDATE_DIR/report-parity-cases.zip" \
    -RemotePath "$GUEST_SCRIPTS\candidate.zip" >/dev/null

set +e
psdirect -Action exec -TimeoutSeconds 3600 -Command \
    "& '$GUEST_SCRIPTS\run-report-parity.ps1' -CandidateZip '$GUEST_SCRIPTS\candidate.zip' -OutputDir '$GUEST_OUT'"
GUEST_STATUS=$?
set -e
RUN_DIR=$(psdirect -Action exec -Command \
    "\$d=@(Get-ChildItem '$GUEST_OUT' -Directory); if(\$d.Count-ne 1){throw 'expected one run'}; \$d[0].FullName")
RUN_DIR=$(printf '%s' "$RUN_DIR" | tr -d '\r\n')
psdirect -Action pull -RemotePath "$RUN_DIR" -LocalPath "$LOCAL_DIR" >/dev/null
psdirect -Action pull -RemotePath "$GUEST_SCRIPTS\run-report-parity.ps1" \
    -LocalPath "$LOCAL_DIR/deployed" >/dev/null
cp "$BUILDER_STDOUT" "$LOCAL_DIR/"
# Controller-side scripts and bound modules are recorded by the finalizer as
# (commit, path, sha256) (WI-062); only the guest-deployed script is banked.

echo "LOCAL_RUN_DIR=$LOCAL_DIR"
echo "CANDIDATE_DIR=$CANDIDATE_DIR"
set +e
uv run --project "$REPO_ROOT" python "$SCRIPT_DIR/finalize_report_parity_run.py" "$LOCAL_DIR" \
    --candidate-root "$CANDIDATE_DIR" --repo-root "$REPO_ROOT"
FINALIZER_STATUS=$?
set -e
if [[ $GUEST_STATUS -ne 0 ]]; then
    exit "$GUEST_STATUS"
fi
exit "$FINALIZER_STATUS"
