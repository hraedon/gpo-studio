#!/usr/bin/env bash
# Plan 033 WP-3 security-template conformance run, against the disposable
# evidence estate.
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
#           bash scripts/windows-oracle/run-wp3-oracle.sh
#
# This harness never invokes `secedit /configure`. That restriction originally
# came from sharing a host with another project; it stays because the lane's
# claim is about template conformance, not about applying policy. It creates no
# AD object at all -- its only state is a temporary database inside its own run
# directory -- so unlike WP-0/WP-1B/WP-2 it has nothing on the estate to reap.
#
# Re-pointing a lane is not a variable change: every certification is bound to
# the environment recorded in its own manifest, so a new host needs a fresh
# qualification run and a re-freeze of docs/plan-033/environment-spec.md. The
# transport is recorded in the verdict.

set -euo pipefail

TRANSPORT=psdirect
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

: "${GPO_STUDIO_LAB_HOST:?GPO_STUDIO_LAB_HOST not set}"
: "${GPO_STUDIO_LAB_GUEST:?GPO_STUDIO_LAB_GUEST not set}"
# psdirect.ps1 enforces the composed acb checkout itself; fail early here so the
# lane does not build a candidate it cannot deliver.
: "${HYPERV_CONTROL_USERNAME:?composed acb checkout missing (cred:lab-hyperv-control)}"
: "${GUEST_BOOTSTRAP_USERNAME:?composed acb checkout missing (cred:lab-guest-bootstrap)}"

psdirect() {
    pwsh -NoProfile -File "$SCRIPT_DIR/psdirect.ps1" \
        -LabHost "$GPO_STUDIO_LAB_HOST" -Guest "$GPO_STUDIO_LAB_GUEST" "$@"
}

# Every run owns a private tree on the guest -- see the comment in
# run-wp2-oracle.sh. WP-3 shared `expected.json` with WP-2 at one fixed guest
# path, which is the concrete way two lanes could hand each other the wrong
# answer key.
STAMP="$(date +%Y%m%d%H%M%S)-$$"
GUEST_RUN_ROOT="C:\\gpo-studio\\runs\\$STAMP"
GUEST_SCRIPTS="$GUEST_RUN_ROOT\\scripts"
GUEST_OUT="$GUEST_RUN_ROOT\\out"

CANDIDATE_DIR="/tmp/opencode/wp3-candidate-$STAMP"
uv run python scripts/plan-033/build-wp3-candidate.py "$CANDIDATE_DIR"

LOCAL_DIR="/tmp/opencode/wp3-oracle-run-$STAMP"
mkdir -p "$LOCAL_DIR/deployed"

PREPARE="if (Test-Path '$GUEST_RUN_ROOT') { throw 'run root already exists: $GUEST_RUN_ROOT' }; New-Item -ItemType Directory -Force -Path '$GUEST_SCRIPTS','$GUEST_OUT' | Out-Null"
RUN_SELECT="\$d = @(Get-ChildItem '$GUEST_OUT' -Directory); if (\$d.Count -ne 1) { throw ('expected exactly one run directory under $GUEST_OUT, found ' + \$d.Count) }; \$d[0].FullName"

psdirect -Action exec -Command "$PREPARE" >/dev/null
psdirect -Action push -LocalPath "$SCRIPT_DIR/run-wp3-security-template.ps1" \
    -RemotePath "$GUEST_SCRIPTS\\run-wp3-security-template.ps1" >/dev/null
psdirect -Action push -LocalPath "$CANDIDATE_DIR/candidate.inf" \
    -RemotePath "$GUEST_SCRIPTS\\candidate.inf" >/dev/null
psdirect -Action push -LocalPath "$CANDIDATE_DIR/expected.json" \
    -RemotePath "$GUEST_SCRIPTS\\expected.json" >/dev/null

psdirect -Action exec -TimeoutSeconds 360 -Command \
    "& '$GUEST_SCRIPTS\\run-wp3-security-template.ps1' -CandidatePath '$GUEST_SCRIPTS\\candidate.inf' -ExpectedPath '$GUEST_SCRIPTS\\expected.json' -OutputDir '$GUEST_OUT'"

RUN_DIR=$(psdirect -Action exec -Command "$RUN_SELECT")
RUN_DIR=$(printf '%s' "$RUN_DIR" | tr -d '\r\n' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')
if [[ -z "$RUN_DIR" ]]; then
    echo "ERROR: no WP-3 run directory produced" >&2
    exit 1
fi

# Retrieve the harness files that actually executed on Windows (not source-tree
# copies) so the finalizer can bind them to the recorded commit.
# Pulling a directory delivers its CONTENTS, matching `scp -r host:dir/. local/`.
psdirect -Action pull -RemotePath "$RUN_DIR" -LocalPath "$LOCAL_DIR" >/dev/null
psdirect -Action pull -RemotePath "$GUEST_SCRIPTS\\run-wp3-security-template.ps1" \
    -LocalPath "$LOCAL_DIR/deployed" >/dev/null

# These scripts ran locally, so retain the exact source-tree bytes. Execute the
# retained finalizer copy so the verdict-producing code is itself evidence.
cp "$SCRIPT_DIR/run-wp3-oracle.sh" \
    "$SCRIPT_DIR/finalize_wp3_run.py" \
    "$REPO_ROOT/scripts/plan-033/build-wp3-candidate.py" \
    "$SCRIPT_DIR/psdirect.ps1" \
    "$LOCAL_DIR/"

echo "LOCAL_RUN_DIR=$LOCAL_DIR"
echo "CANDIDATE_DIR=$CANDIDATE_DIR"
# --candidate-root is not optional: it is what makes the verdict's yardstick the
# candidate this controller built, rather than the copy the guest returned.
uv run python "$LOCAL_DIR/finalize_wp3_run.py" "$LOCAL_DIR" \
    --candidate-root "$CANDIDATE_DIR" --repo-root "$REPO_ROOT" --transport "$TRANSPORT"
