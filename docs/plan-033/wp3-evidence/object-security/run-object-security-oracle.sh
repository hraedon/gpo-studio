#!/usr/bin/env bash
# Plan 034 object-security round trip on the disposable member server.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
: "${GPO_STUDIO_LAB_HOST:?GPO_STUDIO_LAB_HOST not set}"
: "${GPO_STUDIO_LAB_GUEST:?GPO_STUDIO_LAB_GUEST not set}"
: "${HYPERV_CONTROL_USERNAME:?composed hypervisor credential missing}"
: "${GUEST_BOOTSTRAP_USERNAME:?composed guest credential missing}"

psdirect() {
    pwsh -NoProfile -File "$SCRIPT_DIR/psdirect.ps1" \
        -LabHost "$GPO_STUDIO_LAB_HOST" -Guest "$GPO_STUDIO_LAB_GUEST" "$@"
}

STAMP="$(date +%Y%m%d%H%M%S)-$$"
GUEST_ROOT="C:\\gpo-studio\\runs\\object-security-$STAMP"
GUEST_SCRIPTS="$GUEST_ROOT\\scripts"
GUEST_OUT="$GUEST_ROOT\\out"
CANDIDATE_DIR="/tmp/opencode/object-security-candidate-$STAMP"
LOCAL_DIR="/tmp/opencode/object-security-run-$STAMP"
uv run python scripts/plan-033/build-object-security-candidate.py "$CANDIDATE_DIR"
mkdir -p "$LOCAL_DIR/deployed"

PREPARE="if (Test-Path '$GUEST_ROOT') { throw 'run root already exists' }; New-Item -ItemType Directory -Force -Path '$GUEST_SCRIPTS','$GUEST_OUT' | Out-Null"
psdirect -Action exec -Command "$PREPARE" >/dev/null
psdirect -Action push -LocalPath "$SCRIPT_DIR/run-object-security-template.ps1" \
    -RemotePath "$GUEST_SCRIPTS\\run-object-security-template.ps1" >/dev/null
psdirect -Action push -LocalPath "$CANDIDATE_DIR/candidate.inf" \
    -RemotePath "$GUEST_SCRIPTS\\candidate.inf" >/dev/null
psdirect -Action push -LocalPath "$CANDIDATE_DIR/expected.json" \
    -RemotePath "$GUEST_SCRIPTS\\expected.json" >/dev/null
set +e
psdirect -Action exec -TimeoutSeconds 360 -Command \
    "& '$GUEST_SCRIPTS\\run-object-security-template.ps1' -CandidatePath '$GUEST_SCRIPTS\\candidate.inf' -ExpectedPath '$GUEST_SCRIPTS\\expected.json' -OutputDir '$GUEST_OUT'"
GUEST_STATUS=$?
set -e

RUN_DIR=$(psdirect -Action exec -Command \
    "\$d=@(Get-ChildItem '$GUEST_OUT' -Directory); if (\$d.Count -ne 1) { throw 'expected one run directory' }; \$d[0].FullName")
RUN_DIR=$(printf '%s' "$RUN_DIR" | tr -d '\r\n' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')
[[ -n "$RUN_DIR" ]] || { echo 'ERROR: no object-security run directory' >&2; exit 1; }
psdirect -Action pull -RemotePath "$RUN_DIR" -LocalPath "$LOCAL_DIR" >/dev/null
psdirect -Action pull -RemotePath "$GUEST_SCRIPTS\\run-object-security-template.ps1" \
    -LocalPath "$LOCAL_DIR/deployed" >/dev/null
cp "$SCRIPT_DIR/run-object-security-oracle.sh" \
   "$SCRIPT_DIR/finalize_object_security_run.py" \
   "$REPO_ROOT/scripts/plan-033/build-object-security-candidate.py" \
   "$SCRIPT_DIR/psdirect.ps1" \
   "$REPO_ROOT/src/gpo_studio/object_security.py" \
   "$REPO_ROOT/src/gpo_studio/security_template.py" \
   "$REPO_ROOT/src/gpo_studio/sddl.py" \
   "$REPO_ROOT/src/gpo_studio/oracle_evidence.py" \
   "$LOCAL_DIR/"
echo "LOCAL_RUN_DIR=$LOCAL_DIR"
echo "CANDIDATE_DIR=$CANDIDATE_DIR"
set +e
uv run python "$LOCAL_DIR/finalize_object_security_run.py" "$LOCAL_DIR" \
    --candidate-root "$CANDIDATE_DIR" --repo-root "$REPO_ROOT"
FINALIZER_STATUS=$?
set -e
if [[ $GUEST_STATUS -ne 0 ]]; then
    exit "$GUEST_STATUS"
fi
exit "$FINALIZER_STATUS"
