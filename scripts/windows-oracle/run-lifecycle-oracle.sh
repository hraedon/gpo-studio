#!/usr/bin/env bash
# Plan 034: the same-domain lifecycle lane.
#
# Builds the expectation (lifecycle.SCOPE_SURVIVAL plus each restore plan's
# claims) on the controller, runs the native author/operate/read-back script on
# the guest, pulls the run back, and grades every survival cell. The
# expectation never leaves the controller, because the guest is the thing being
# measured.
#
# Usage, from a clean checkout:
#
#   ACB_VAULT_ENV=~/.claude/evidence-lab.env \
#     acb exec cred:lab-hyperv-control cred:lab-guest-bootstrap -- \
#       env GPO_STUDIO_LAB_HOST=<hyper-v host> GPO_STUDIO_LAB_GUEST=<member server> \
#         bash scripts/windows-oracle/run-lifecycle-oracle.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"
: "${GPO_STUDIO_LAB_HOST:?}"
: "${GPO_STUDIO_LAB_GUEST:?}"
: "${HYPERV_CONTROL_USERNAME:?}"
: "${GUEST_BOOTSTRAP_USERNAME:?}"

psdirect() {
    pwsh -NoProfile -File "$SCRIPT_DIR/psdirect.ps1" \
        -LabHost "$GPO_STUDIO_LAB_HOST" -Guest "$GPO_STUDIO_LAB_GUEST" "$@"
}

STAMP="$(date +%Y%m%d%H%M%S)-$$"
GUEST_ROOT="C:\gpo-studio\runs\lifecycle-$STAMP"
GUEST_SCRIPTS="$GUEST_ROOT\scripts"
GUEST_OUT="$GUEST_ROOT\out"
CANDIDATE_DIR="${TMPDIR:-/tmp}/gpo-studio/lifecycle-candidate-$STAMP"
LOCAL_DIR="${TMPDIR:-/tmp}/gpo-studio/lifecycle-run-$STAMP"
BUILDER_STDOUT="$CANDIDATE_DIR/builder.stdout.txt"
mkdir -p "$CANDIDATE_DIR" "$LOCAL_DIR/deployed"
uv run python scripts/plan-033/build-lifecycle-candidate.py "$CANDIDATE_DIR" |
    tee "$BUILDER_STDOUT"

PREPARE="if(Test-Path '$GUEST_ROOT'){throw 'run root exists'}; New-Item -ItemType Directory -Force '$GUEST_SCRIPTS','$GUEST_OUT'|Out-Null"
psdirect -Action exec -Command "$PREPARE" >/dev/null
psdirect -Action push -LocalPath "$SCRIPT_DIR/run-lifecycle.ps1" \
    -RemotePath "$GUEST_SCRIPTS\run-lifecycle.ps1" >/dev/null

set +e
psdirect -Action exec -TimeoutSeconds 900 -Command \
    "& '$GUEST_SCRIPTS\run-lifecycle.ps1' -OutputDir '$GUEST_OUT'"
GUEST_STATUS=$?
set -e
RUN_DIR=$(psdirect -Action exec -Command \
    "\$d=@(Get-ChildItem '$GUEST_OUT' -Directory); if(\$d.Count-ne 1){throw 'expected one run'}; \$d[0].FullName")
RUN_DIR=$(printf '%s' "$RUN_DIR" | tr -d '\r\n')
psdirect -Action pull -RemotePath "$RUN_DIR" -LocalPath "$LOCAL_DIR" >/dev/null
psdirect -Action pull -RemotePath "$GUEST_SCRIPTS\run-lifecycle.ps1" \
    -LocalPath "$LOCAL_DIR/deployed" >/dev/null
cp "$BUILDER_STDOUT" "$LOCAL_DIR/"
# Locally-executed scripts and bound modules are recorded by the finalizer as
# (commit, path, sha256) per WI-062; no byte copy rides in the pack.

echo "LOCAL_RUN_DIR=$LOCAL_DIR"
echo "CANDIDATE_DIR=$CANDIDATE_DIR"
set +e
uv run python "$SCRIPT_DIR/finalize_lifecycle_run.py" "$LOCAL_DIR" \
    --candidate-root "$CANDIDATE_DIR" --repo-root "$REPO_ROOT"
FINALIZER_STATUS=$?
set -e
if [[ $GUEST_STATUS -ne 0 ]]; then
    exit "$GUEST_STATUS"
fi
exit "$FINALIZER_STATUS"
