#!/usr/bin/env bash
# Plan 034: the fdeploy lane.
#
# Builds the candidate on the controller (R3's banked fdeploy files, verbatim
# and with only Flags changed, each in its own GPMC backup), imports each into
# its own disposable GPO on the guest, takes a fresh Get-GPOReport -ReportType
# Xml and a Backup-GPO there, and grades Windows' report and re-exported bytes
# against Studio's reader. The expectation never leaves the controller,
# because the guest is the thing being measured.
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
# Short on purpose: Windows PowerShell 5.1 stops at MAX_PATH (260) and a
# re-exported backup nests deep below the run directory. The candidate builder
# bounds the longest guest path from these three lines (GUEST_PATH_LIMIT).
# `\\$STAMP`, not `\$STAMP`: inside double quotes bash reads `\$` as a literal
# dollar, which made every run's root the constant `C:\gpo-studio\fd$STAMP`.
GUEST_ROOT="C:\gpo-studio\fd\\$STAMP"
GUEST_SCRIPTS="$GUEST_ROOT\s"
GUEST_OUT="$GUEST_ROOT\o"
CANDIDATE_DIR="${TMPDIR:-/tmp}/gpo-studio/fdeploy-candidate-$STAMP"
LOCAL_DIR="${TMPDIR:-/tmp}/gpo-studio/fdeploy-run-$STAMP"
BUILDER_STDOUT="$CANDIDATE_DIR/builder.stdout.txt"
mkdir -p "$CANDIDATE_DIR" "$LOCAL_DIR/deployed"
(cd "$REPO_ROOT" && uv run python scripts/plan-033/build-fdeploy-candidate.py "$CANDIDATE_DIR") |
    tee "$BUILDER_STDOUT"

PREPARE="if(Test-Path '$GUEST_ROOT'){throw 'run root exists'}; New-Item -ItemType Directory -Force '$GUEST_SCRIPTS','$GUEST_OUT'|Out-Null"
psdirect -Action exec -Command "$PREPARE" >/dev/null
psdirect -Action push -LocalPath "$SCRIPT_DIR/run-fdeploy-lane.ps1" \
    -RemotePath "$GUEST_SCRIPTS\run-fdeploy-lane.ps1" >/dev/null
psdirect -Action push -LocalPath "$CANDIDATE_DIR/fdeploy-cases.zip" \
    -RemotePath "$GUEST_SCRIPTS\candidate.zip" >/dev/null

set +e
psdirect -Action exec -TimeoutSeconds 1200 -Command \
    "& '$GUEST_SCRIPTS\run-fdeploy-lane.ps1' -CandidateZip '$GUEST_SCRIPTS\candidate.zip' -OutputDir '$GUEST_OUT'"
GUEST_STATUS=$?
set -e
RUN_DIR=$(psdirect -Action exec -Command \
    "\$d=@(Get-ChildItem '$GUEST_OUT' -Directory); if(\$d.Count-ne 1){throw 'expected one run'}; \$d[0].FullName")
RUN_DIR=$(printf '%s' "$RUN_DIR" | tr -d '\r\n')
psdirect -Action pull -RemotePath "$RUN_DIR" -LocalPath "$LOCAL_DIR" >/dev/null
psdirect -Action pull -RemotePath "$GUEST_SCRIPTS\run-fdeploy-lane.ps1" \
    -LocalPath "$LOCAL_DIR/deployed" >/dev/null
cp "$BUILDER_STDOUT" "$LOCAL_DIR/"
# Controller-side scripts and bound modules are recorded by the finalizer as
# (commit, path, sha256) (WI-062); only the guest-deployed script is banked.

echo "LOCAL_RUN_DIR=$LOCAL_DIR"
echo "CANDIDATE_DIR=$CANDIDATE_DIR"
# The guest's exit status goes to the finalizer: a failed guest run is still
# graded and banked, but it can never pass or be tagged.
set +e
uv run --project "$REPO_ROOT" python "$SCRIPT_DIR/finalize_fdeploy_run.py" "$LOCAL_DIR" \
    --candidate-root "$CANDIDATE_DIR" --repo-root "$REPO_ROOT" --guest-status "$GUEST_STATUS"
FINALIZER_STATUS=$?
set -e
if [[ $GUEST_STATUS -ne 0 ]]; then
    exit "$GUEST_STATUS"
fi
exit "$FINALIZER_STATUS"
