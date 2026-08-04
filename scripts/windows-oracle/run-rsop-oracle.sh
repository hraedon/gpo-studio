#!/usr/bin/env bash
# Plan 033 WP-6B RSOP lane. Two guests, one prediction, one experiment.
#
#   author   run-rsop-author.ps1 on the MEMBER SERVER -- build the OU tree,
#            create and link six GPOs across site/domain/parent/child, move the
#            client's computer account into the child OU; later, tear all of it
#            down and PROVE the teardown by re-querying.
#   observe  run-rsop-observe.ps1 on the CLIENT -- refresh policy, settle on
#            evidence, capture gpresult /x /scope:computer with artifact-based
#            assertions, and read the winning registry values.
#
# Neither half reaches the other. This script sequences them, each over its own
# PowerShell Direct connection to its own guest, so no guest-to-guest channel is
# introduced into an estate whose whole point is that it has none.
#
# CLEANUP RUNS EVEN WHEN THE OBSERVATION FAILS, and it matters more here than in
# the endpoint lane. That lane confined itself to one disposable OU; this one
# links policy at the DOMAIN ROOT and at the SITE, which reaches every machine
# in the estate including the domain controller. An observation error is not a
# reason to leave that in place. The trap below is the mechanism, and its
# failure is reported as a lane failure in its own right rather than folded into
# whatever went wrong first.
#
# THE PREDICTION IS BUILT BEFORE ANYTHING IS APPLIED and is never re-derived
# afterwards. The finalizer reads it from the candidate directory, not from
# anything the guests returned.
#
# Usage:
#
#     GPO_STUDIO_LAB_HOST=<hyper-v host> \
#     GPO_STUDIO_LAB_AUTHOR_GUEST=<member server> \
#     GPO_STUDIO_LAB_ENDPOINT_GUEST=<client> \
#         ACB_VAULT_ENV=~/.claude/evidence-lab.env \
#         acb exec cred:lab-hyperv-control cred:lab-guest-bootstrap -- \
#             bash scripts/windows-oracle/run-rsop-oracle.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

: "${GPO_STUDIO_LAB_HOST:?GPO_STUDIO_LAB_HOST not set}"
: "${GPO_STUDIO_LAB_AUTHOR_GUEST:?GPO_STUDIO_LAB_AUTHOR_GUEST not set (the GPMC-capable member server)}"
: "${GPO_STUDIO_LAB_ENDPOINT_GUEST:?GPO_STUDIO_LAB_ENDPOINT_GUEST not set (the client)}"
: "${HYPERV_CONTROL_USERNAME:?composed acb checkout missing (cred:lab-hyperv-control)}"
: "${GUEST_BOOTSTRAP_USERNAME:?composed acb checkout missing (cred:lab-guest-bootstrap)}"

# Guest names are interpolated into single-quoted PowerShell string literals
# below, so a name containing a quote would end the literal and inject the rest
# as code. These are operator-supplied; a VM name has no business containing
# anything outside this set.
for guest in "$GPO_STUDIO_LAB_AUTHOR_GUEST" "$GPO_STUDIO_LAB_ENDPOINT_GUEST"; do
    if [[ ! "$guest" =~ ^[A-Za-z0-9._-]+$ ]]; then
        echo "ERROR: guest name '$guest' contains characters this lane will not pass to the guest." >&2
        exit 2
    fi
done

if [[ "$GPO_STUDIO_LAB_AUTHOR_GUEST" == "$GPO_STUDIO_LAB_ENDPOINT_GUEST" ]]; then
    echo "ERROR: author and endpoint guests are the same VM ('$GPO_STUDIO_LAB_AUTHOR_GUEST')." >&2
    echo "       The lane is two-guest by measurement, not by preference." >&2
    exit 2
fi

GUEST_SCRIPTS='C:\gpo-studio\scripts'
GUEST_OUT='C:\gpo-studio\out'
GUEST_STATE="$GUEST_SCRIPTS"'\author-state.json'

author() {
    pwsh -NoProfile -File "$SCRIPT_DIR/psdirect.ps1" \
        -LabHost "$GPO_STUDIO_LAB_HOST" -Guest "$GPO_STUDIO_LAB_AUTHOR_GUEST" "$@"
}
endpoint() {
    pwsh -NoProfile -File "$SCRIPT_DIR/psdirect.ps1" \
        -LabHost "$GPO_STUDIO_LAB_HOST" -Guest "$GPO_STUDIO_LAB_ENDPOINT_GUEST" "$@"
}

# Per-invocation execution-policy bypass: Windows client SKUs default to
# Restricted where Server defaults to RemoteSigned, so without this the
# authoring half runs and the observation half does not. The guest's policy is
# deliberately NOT changed -- reconfiguring the machine under test is how a
# harness starts measuring itself. powershell.exe is a native executable, so a
# failing harness sets $LASTEXITCODE rather than throwing; without the explicit
# check the transport reports success for a script that died.
run_guest_script() {
    printf "& powershell.exe -NoProfile -ExecutionPolicy Bypass -File %s; if (\$LASTEXITCODE -ne 0) { throw \"harness exited \$LASTEXITCODE\" }" "$1"
}

STAMP="$(date +%Y%m%d%H%M%S)"
CANDIDATE_DIR="${TMPDIR:-/tmp}/rsop-candidate-$STAMP"
LOCAL_DIR="${TMPDIR:-/tmp}/rsop-run-$STAMP"
mkdir -p "$LOCAL_DIR/author" "$LOCAL_DIR/observe" "$LOCAL_DIR/deployed"

# The estate's domain is read from the authoring guest rather than assumed, so
# the candidate's DNs describe the directory this run will actually touch.
DOMAIN=$(author -Action exec -Command '(Get-CimInstance Win32_ComputerSystem).Domain' \
    | tr -d '\r\n' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')
if [[ -z "$DOMAIN" ]]; then
    echo "ERROR: could not read the domain from $GPO_STUDIO_LAB_AUTHOR_GUEST" >&2
    exit 1
fi
echo "DOMAIN=$DOMAIN"

uv run python "$REPO_ROOT/scripts/plan-033/build-rsop-candidate.py" "$CANDIDATE_DIR" \
    --domain "$DOMAIN" --computer-name "$GPO_STUDIO_LAB_ENDPOINT_GUEST"

PREPARE="New-Item -ItemType Directory -Force -Path '$GUEST_SCRIPTS','$GUEST_OUT' | Out-Null; Get-ChildItem '$GUEST_OUT' -Directory -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force; Remove-Item -LiteralPath '$GUEST_STATE' -Force -ErrorAction SilentlyContinue"

# ------------------------------------------------------------------ stage ---
author -Action exec -Command "$PREPARE" >/dev/null
author -Action push -LocalPath "$SCRIPT_DIR/run-rsop-author.ps1" \
    -RemotePath "$GUEST_SCRIPTS\\run-rsop-author.ps1" >/dev/null
author -Action push -LocalPath "$CANDIDATE_DIR/topology.json" \
    -RemotePath "$GUEST_SCRIPTS\\topology.json" >/dev/null

# The endpoint's payload is staged BEFORE the authoring setup runs. Staging it
# only just before the observation would leave every early-exit path in the
# window where policy is linked with no way to run anything on the client.
endpoint -Action exec -Command "$PREPARE" >/dev/null
endpoint -Action push -LocalPath "$SCRIPT_DIR/run-rsop-observe.ps1" \
    -RemotePath "$GUEST_SCRIPTS\\run-rsop-observe.ps1" >/dev/null
endpoint -Action push -LocalPath "$CANDIDATE_DIR/expected.json" \
    -RemotePath "$GUEST_SCRIPTS\\expected.json" >/dev/null

CLEANUP_DONE=0
cleanup_author() {
    # Idempotent: the trap fires on both the success path and every failure
    # path, and the success path calls this explicitly so a cleanup failure
    # fails the lane rather than being swallowed by an exiting shell.
    [[ "$CLEANUP_DONE" == "1" ]] && return 0
    CLEANUP_DONE=1
    echo "--- authoring cleanup ---"
    author -Action exec -TimeoutSeconds 1500 -Command \
        "$(run_guest_script "'$GUEST_SCRIPTS\\run-rsop-author.ps1' -Phase cleanup -StatePath '$GUEST_STATE'")"
}

REFRESH_DONE=0
refresh_endpoint() {
    # A post-teardown refresh on the client, so the machine does not sit holding
    # policy that no longer exists in the directory. Managed values under
    # Software\Policies are removed by the CSE when the GPO stops applying --
    # but only once the client refreshes, and nothing else in this lane makes it
    # do so after the unlink.
    #
    # Runs AFTER cleanup_author, always, because its whole value is that the
    # policy is gone by the time it refreshes.
    [[ "$REFRESH_DONE" == "1" ]] && return 0
    REFRESH_DONE=1
    echo "--- endpoint post-teardown refresh ---"
    endpoint -Action exec -TimeoutSeconds 900 -Command \
        "& gpupdate.exe /force /target:computer /wait:300; \$null = \$LASTEXITCODE; (Get-ItemProperty -LiteralPath 'HKLM:\\Software\\Policies\\StudioLab' -ErrorAction SilentlyContinue | Out-String)"
}

on_exit() {
    local status=0
    cleanup_author || {
        status=1
        echo "WARNING: authoring cleanup failed; the estate may hold disposable OUs, linked GPOs at the DOMAIN ROOT or SITE, or a displaced computer account" >&2
    }
    refresh_endpoint || {
        status=1
        echo "WARNING: endpoint refresh failed; the client may still carry the run's policy values" >&2
    }
    return $status
}
trap on_exit EXIT

# ----------------------------------------------------------------- author ---
SETUP_OUT=$(author -Action exec -TimeoutSeconds 2400 -Command \
    "$(run_guest_script "'$GUEST_SCRIPTS\\run-rsop-author.ps1' -Phase setup -StatePath '$GUEST_STATE' -TopologyPath '$GUEST_SCRIPTS\\topology.json' -OutputDir '$GUEST_OUT' -TargetComputer '$GPO_STUDIO_LAB_ENDPOINT_GUEST'")")
printf '%s\n' "$SETUP_OUT"

AUTHOR_WORK_DIR=$(printf '%s' "$SETUP_OUT" | tr -d '\r' | sed -n 's/^WORK_DIR=//p' | head -1)
if [[ -z "$AUTHOR_WORK_DIR" ]]; then
    echo "ERROR: authoring setup did not report a work directory" >&2
    exit 1
fi

# ---------------------------------------------------------------- observe ---
# Deliberately not `set -e`-fatal: the observation half can fail legitimately,
# and its failure must not skip the evidence pull or pre-empt the trap's
# cleanup with a bare exit.
OBSERVE_STATUS=0
OBSERVE_OUT=$(endpoint -Action exec -TimeoutSeconds 3000 -Command \
    "$(run_guest_script "'$GUEST_SCRIPTS\\run-rsop-observe.ps1' -ExpectedPath '$GUEST_SCRIPTS\\expected.json' -OutputDir '$GUEST_OUT'")") || OBSERVE_STATUS=$?
printf '%s\n' "$OBSERVE_OUT"

OBSERVE_WORK_DIR=$(printf '%s' "$OBSERVE_OUT" | tr -d '\r' | sed -n 's/^WORK_DIR=//p' | head -1)
if [[ -z "$OBSERVE_WORK_DIR" ]]; then
    # The script writes its result before exiting, so evidence usually exists
    # even when it threw.
    OBSERVE_WORK_DIR=$(endpoint -Action exec -Command \
        "(Get-ChildItem '$GUEST_OUT' -Directory -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName" \
        | tr -d '\r\n' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//') || true
fi

# ------------------------------------------------------------------- pull ---
# Before cleanup, so the pull captures the estate as the observation saw it.
if [[ -n "$OBSERVE_WORK_DIR" ]]; then
    endpoint -Action pull -RemotePath "$OBSERVE_WORK_DIR" -LocalPath "$LOCAL_DIR/observe" >/dev/null
fi
endpoint -Action pull -RemotePath "$GUEST_SCRIPTS\\run-rsop-observe.ps1" \
    -LocalPath "$LOCAL_DIR/deployed" >/dev/null

# --------------------------------------------------------------- teardown ---
# Explicit, so a cleanup failure is a lane failure. The trap stays armed for the
# paths that never reach here.
cleanup_author

author -Action pull -RemotePath "$AUTHOR_WORK_DIR" -LocalPath "$LOCAL_DIR/author" >/dev/null
author -Action pull -RemotePath "$GUEST_STATE" -LocalPath "$LOCAL_DIR/author" >/dev/null
author -Action pull -RemotePath "$GUEST_SCRIPTS\\run-rsop-author.ps1" \
    -LocalPath "$LOCAL_DIR/deployed" >/dev/null

refresh_endpoint >/dev/null || echo "WARNING: post-teardown refresh exited non-zero" >&2

# Locally-executed scripts: the source-tree copy IS the executed copy.
cp "$SCRIPT_DIR/run-rsop-oracle.sh" "$SCRIPT_DIR/psdirect.ps1" \
    "$REPO_ROOT/scripts/plan-033/build-rsop-candidate.py" "$LOCAL_DIR/"

echo "LOCAL_RUN_DIR=$LOCAL_DIR"
echo "CANDIDATE_DIR=$CANDIDATE_DIR"
if [[ "$OBSERVE_STATUS" -ne 0 ]]; then
    echo "NOTE: the observation half exited $OBSERVE_STATUS; the finalizer decides whether that is a verdict or a lane failure." >&2
fi

uv run python "$REPO_ROOT/scripts/windows-oracle/finalize_rsop_run.py" "$LOCAL_DIR" \
    --candidate-root "$CANDIDATE_DIR" --repo-root "$REPO_ROOT" --transport psdirect
