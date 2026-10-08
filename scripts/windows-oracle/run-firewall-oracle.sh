#!/usr/bin/env bash
# Plan 034 firewall author/read and import/read lane.
#
# Builds on the controller, authors one GPO natively and imports a second,
# then retrieves readback, bytes, reports and strict cleanup evidence.
# expected.json stays on the controller.
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
RUN_ID="firewall-$STAMP"
GUEST_ROOT="C:\gpo-studio\runs\firewall-$STAMP"
GUEST_SCRIPTS="$GUEST_ROOT\scripts"
GUEST_OUT="$GUEST_ROOT\out"
CANDIDATE_DIR="${TMPDIR:-/tmp}/gpo-studio/firewall-candidate-$STAMP"
LOCAL_DIR="${TMPDIR:-/tmp}/gpo-studio/firewall-run-$STAMP"
BUILDER_STDOUT="$CANDIDATE_DIR/builder.stdout.txt"
mkdir -p "$CANDIDATE_DIR" "$LOCAL_DIR/deployed"
uv run python "$REPO_ROOT/scripts/plan-033/build-firewall-candidate.py" "$CANDIDATE_DIR" |
    tee "$BUILDER_STDOUT"

PREPARE="if(Test-Path '$GUEST_ROOT'){throw 'run root exists'}; New-Item -ItemType Directory -Force '$GUEST_SCRIPTS','$GUEST_OUT'|Out-Null"
psdirect -Action exec -Command "$PREPARE" >/dev/null
psdirect -Action push -LocalPath "$SCRIPT_DIR/run-firewall-policy.ps1" \
    -RemotePath "$GUEST_SCRIPTS\run-firewall-policy.ps1" >/dev/null
psdirect -Action push -LocalPath "$SCRIPT_DIR/cleanup-firewall-policy.ps1" \
    -RemotePath "$GUEST_SCRIPTS\cleanup-firewall-policy.ps1" >/dev/null

cleanup_run() {
    psdirect -Action exec -Command \
        "& '$GUEST_SCRIPTS\cleanup-firewall-policy.ps1' -RunId '$RUN_ID'"
}
# The controller invokes cleanup independently of the timed guest job/finally.
# Keep it armed through retrieval/finalization, including early shell failures.
trap cleanup_run EXIT
cleanup_run
psdirect -Action push -LocalPath "$CANDIDATE_DIR/studio-firewall-backup.zip" \
    -RemotePath "$GUEST_SCRIPTS\candidate.zip" >/dev/null

psdirect -Action push -LocalPath "$CANDIDATE_DIR/authoring.json" \
    -RemotePath "$GUEST_SCRIPTS\authoring.json" >/dev/null

set +e
psdirect -Action exec -TimeoutSeconds 600 -Command \
    "& '$GUEST_SCRIPTS\run-firewall-policy.ps1' -CandidateZip '$GUEST_SCRIPTS\candidate.zip' -AuthoringJson '$GUEST_SCRIPTS\authoring.json' -OutputDir '$GUEST_OUT' -RunId '$RUN_ID'"
GUEST_STATUS=$?
cleanup_run
CLEANUP_STATUS=$?
set -e
if [[ $CLEANUP_STATUS -ne 0 ]]; then
    exit "$CLEANUP_STATUS"
fi
RUN_DIR=$(psdirect -Action exec -Command \
    "\$d=@(Get-ChildItem '$GUEST_OUT' -Directory); if(\$d.Count-ne 1){throw 'expected one run'}; \$d[0].FullName")
RUN_DIR=$(printf '%s' "$RUN_DIR" | tr -d '\r\n')
psdirect -Action pull -RemotePath "$RUN_DIR" -LocalPath "$LOCAL_DIR" >/dev/null
psdirect -Action pull -RemotePath "$GUEST_SCRIPTS\run-firewall-policy.ps1" \
    -LocalPath "$LOCAL_DIR/deployed" >/dev/null
psdirect -Action pull -RemotePath "$GUEST_SCRIPTS\cleanup-firewall-policy.ps1" \
    -LocalPath "$LOCAL_DIR/deployed" >/dev/null
cp "$BUILDER_STDOUT" "$LOCAL_DIR/"
# Locally-executed scripts and bound modules: the source-tree copy IS the
# executed copy, and WI-062 stops banking byte copies of it -- the finalizer
# records each as (commit, path, sha256) and git at the commit holds the
# bytes that assert_bound_source_bytes proved identical across tree, index
# and HEAD.

echo "LOCAL_RUN_DIR=$LOCAL_DIR"
echo "CANDIDATE_DIR=$CANDIDATE_DIR"
set +e
uv run python "$SCRIPT_DIR/finalize_firewall_run.py" "$LOCAL_DIR" \
    --candidate-root "$CANDIDATE_DIR" --repo-root "$REPO_ROOT"
FINALIZER_STATUS=$?
set -e
if [[ $GUEST_STATUS -ne 0 ]]; then
    exit "$GUEST_STATUS"
fi
exit "$FINALIZER_STATUS"
