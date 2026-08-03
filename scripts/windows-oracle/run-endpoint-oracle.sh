#!/usr/bin/env bash
# Plan 033 endpoint lane. Two guests, one experiment.
#
# The lane this replaces ran on a single machine that was both GPMC-capable and
# the endpoint. The evidence estate has no such machine: the client carries the
# frozen client build family and therefore has to be the endpoint, but it has no
# GroupPolicy module, no ActiveDirectory module, and no route to the
# Feature-on-Demand source that would install them. That is the isolation
# invariant working, not a gap to fill. The measurements are in
# docs/plan-033/endpoint-lane-design.md.
#
# So the lane is split:
#
#   author   run-endpoint-author.ps1 on the MEMBER SERVER -- disposable OU, move
#            the client's computer account into it, import and link the
#            candidate, push replication; later, tear all of it down.
#   observe  run-endpoint-observe.ps1 on the CLIENT -- refresh policy, poll
#            until the client itself reports the GPO applied, wait for the
#            Scheduled Tasks CSE to complete a pass, record what it created,
#            then unregister the tasks it created.
#
# Neither half reaches the other. This script sequences them, each over its own
# PowerShell Direct connection to its own guest, so no guest-to-guest channel is
# introduced into an estate whose whole point is that it has none.
#
# CLEANUP RUNS EVEN WHEN THE OBSERVATION FAILS. The authoring half has moved a
# real computer account out of its real OU and linked a policy to it; an
# observation error is not a reason to leave that in place. The trap below is
# the mechanism, and its failure is reported as a lane failure in its own right
# rather than folded into whatever went wrong first.
#
# Usage:
#
#     GPO_STUDIO_LAB_HOST=<hyper-v host> \
#     GPO_STUDIO_LAB_AUTHOR_GUEST=<member server> \
#     GPO_STUDIO_LAB_ENDPOINT_GUEST=<client> \
#         ACB_VAULT_ENV=~/.claude/evidence-lab.env \
#         acb exec cred:lab-hyperv-control cred:lab-guest-bootstrap -- \
#             bash scripts/windows-oracle/run-endpoint-oracle.sh
#
# Re-pointing a lane is not a variable change: every certification is bound to
# the environment recorded in its own manifest, so a transport or host change
# needs a fresh qualification run and a re-freeze of
# docs/plan-033/environment-spec.md.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

: "${GPO_STUDIO_LAB_HOST:?GPO_STUDIO_LAB_HOST not set}"
: "${GPO_STUDIO_LAB_AUTHOR_GUEST:?GPO_STUDIO_LAB_AUTHOR_GUEST not set (the GPMC-capable member server)}"
: "${GPO_STUDIO_LAB_ENDPOINT_GUEST:?GPO_STUDIO_LAB_ENDPOINT_GUEST not set (the client)}"
# psdirect.ps1 enforces the composed acb checkout itself; fail early here so the
# lane does not build candidates it cannot deliver.
: "${HYPERV_CONTROL_USERNAME:?composed acb checkout missing (cred:lab-hyperv-control)}"
: "${GUEST_BOOTSTRAP_USERNAME:?composed acb checkout missing (cred:lab-guest-bootstrap)}"

# Guest names are interpolated into single-quoted PowerShell string literals in
# the commands below, so a name containing a quote would end the literal and
# inject the rest as code. These are operator-supplied. A VM name has no business
# containing anything outside this set, so the check costs nothing and removes
# the injection entirely rather than trying to escape it.
for guest in "$GPO_STUDIO_LAB_AUTHOR_GUEST" "$GPO_STUDIO_LAB_ENDPOINT_GUEST"; do
    if [[ ! "$guest" =~ ^[A-Za-z0-9._-]+$ ]]; then
        echo "ERROR: guest name '$guest' contains characters this lane will not pass to the guest." >&2
        exit 2
    fi
done

if [[ "$GPO_STUDIO_LAB_AUTHOR_GUEST" == "$GPO_STUDIO_LAB_ENDPOINT_GUEST" ]]; then
    echo "ERROR: author and endpoint guests are the same VM ('$GPO_STUDIO_LAB_AUTHOR_GUEST')." >&2
    echo "       The lane is two-guest by measurement, not by preference; a single-guest" >&2
    echo "       run would either author on a machine that cannot, or claim client" >&2
    echo "       evidence from a server build." >&2
    exit 2
fi

GUEST_SCRIPTS='C:\gpo-studio\scripts'
GUEST_OUT='C:\gpo-studio\out'
GUEST_STATE="$GUEST_SCRIPTS"'\endpoint-state.json'

author() {
    pwsh -NoProfile -File "$SCRIPT_DIR/psdirect.ps1" \
        -LabHost "$GPO_STUDIO_LAB_HOST" -Guest "$GPO_STUDIO_LAB_AUTHOR_GUEST" "$@"
}
endpoint() {
    pwsh -NoProfile -File "$SCRIPT_DIR/psdirect.ps1" \
        -LabHost "$GPO_STUDIO_LAB_HOST" -Guest "$GPO_STUDIO_LAB_ENDPOINT_GUEST" "$@"
}

# Harness scripts are invoked through a per-invocation execution-policy bypass.
#
# The client refuses `& script.ps1` outright: Windows client SKUs default to an
# execution policy of Restricted where the server defaults to RemoteSigned.
# Found live -- the authoring half ran and the observation half did not.
#
# The fix deliberately does NOT set the policy on the guest. This lane certifies
# the client's Group Policy behaviour, and persistently reconfiguring the machine
# under test is how a harness starts measuring itself instead of the product.
# `-ExecutionPolicy Bypass` applies to one process and leaves nothing behind.
#
# powershell.exe is called directly rather than through Start-Process because
# the driver parses the harness's stdout, which Start-Process would swallow. It
# is a native executable, so a failing harness sets $LASTEXITCODE rather than
# throwing -- without the explicit check the transport reports success for a
# script that died.
run_guest_script() {
    printf "& powershell.exe -NoProfile -ExecutionPolicy Bypass -File %s; if (\$LASTEXITCODE -ne 0) { throw \"harness exited \$LASTEXITCODE\" }" "$1"
}

STAMP="$(date +%Y%m%d%H%M%S)"
CANDIDATE_DIR="/tmp/opencode/endpoint-candidate-$STAMP"
LOCAL_DIR="/tmp/opencode/endpoint-run-$STAMP"
mkdir -p "$LOCAL_DIR/author" "$LOCAL_DIR/observe" "$LOCAL_DIR/deployed"

uv run python scripts/plan-033/build-endpoint-candidate.py "$CANDIDATE_DIR"

PREPARE="New-Item -ItemType Directory -Force -Path '$GUEST_SCRIPTS','$GUEST_OUT' | Out-Null; Get-ChildItem '$GUEST_OUT' -Directory -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force; Remove-Item -LiteralPath '$GUEST_STATE' -Force -ErrorAction SilentlyContinue"

# ------------------------------------------------------------------ author ---
author -Action exec -Command "$PREPARE" >/dev/null
author -Action push -LocalPath "$SCRIPT_DIR/run-endpoint-author.ps1" \
    -RemotePath "$GUEST_SCRIPTS\\run-endpoint-author.ps1" >/dev/null
author -Action push -LocalPath "$CANDIDATE_DIR/candidate.zip" \
    -RemotePath "$GUEST_SCRIPTS\\candidate.zip" >/dev/null
author -Action push -LocalPath "$CANDIDATE_DIR/expected.json" \
    -RemotePath "$GUEST_SCRIPTS\\expected.json" >/dev/null

# The ENDPOINT's payload is staged BEFORE the authoring setup runs, even though
# the observation half is not invoked until afterwards. The trap's
# verify_endpoint needs run-endpoint-observe.ps1 and expected.json to be present
# on the client; staging them only just before the observation would leave every
# early-exit path with a teardown it cannot perform.
endpoint -Action exec -Command "$PREPARE" >/dev/null
endpoint -Action push -LocalPath "$SCRIPT_DIR/run-endpoint-observe.ps1" \
    -RemotePath "$GUEST_SCRIPTS\\run-endpoint-observe.ps1" >/dev/null
endpoint -Action push -LocalPath "$CANDIDATE_DIR/expected.json" \
    -RemotePath "$GUEST_SCRIPTS\\expected.json" >/dev/null

CLEANUP_DONE=0
cleanup_author() {
    # Idempotent: the trap fires on both the success path and every failure
    # path, and the success path calls this explicitly so that a cleanup failure
    # fails the lane rather than being swallowed by an exiting shell.
    [[ "$CLEANUP_DONE" == "1" ]] && return 0
    CLEANUP_DONE=1
    echo "--- authoring cleanup ---"
    author -Action exec -TimeoutSeconds 900 -Command \
        "$(run_guest_script "'$GUEST_SCRIPTS\\run-endpoint-author.ps1' -Phase cleanup -StatePath '$GUEST_STATE'")"
}

VERIFY_DONE=0
verify_endpoint() {
    # The CLIENT's teardown, and it belongs in the trap for the same reason the
    # authoring cleanup does.
    #
    # It was originally only on the happy path, which left a gap: between the
    # authoring setup succeeding and the observation half running, the GPO is
    # linked to an OU containing the client. Any exit in that window -- a failed
    # push, a transport hiccup, a killed pull -- would tear down the AD side and
    # never touch the client. If the client's own background refresh had landed
    # inside the window, its scheduled tasks would exist, and nothing would have
    # looked for them.
    #
    # Runs AFTER cleanup_author, always, because its whole value is that the
    # policy is gone by the time it refreshes.
    [[ "$VERIFY_DONE" == "1" ]] && return 0
    VERIFY_DONE=1
    echo "--- endpoint verification ---"
    endpoint -Action exec -TimeoutSeconds 900 -Command \
        "$(run_guest_script "'$GUEST_SCRIPTS\\run-endpoint-observe.ps1' -Phase verify -ExpectedPath '$GUEST_SCRIPTS\\expected.json' -OutputDir '$GUEST_OUT' -TargetGpo '$TARGET_GPO'")"
}

on_exit() {
    local status=0
    cleanup_author || {
        status=1
        echo "WARNING: authoring cleanup failed; the estate may hold a disposable OU, a linked GPO, or a displaced computer account" >&2
    }
    verify_endpoint || {
        status=1
        echo "WARNING: endpoint verification failed; the client may still carry the run's scheduled tasks" >&2
    }
    return $status
}
# TARGET_GPO is referenced by verify_endpoint and may be unset if the trap fires
# before setup reported it; an empty value makes the verify phase skip its
# gpresult check rather than fail, which is the right behaviour when no GPO was
# ever linked.
TARGET_GPO=""
trap on_exit EXIT

SETUP_OUT=$(author -Action exec -TimeoutSeconds 1500 -Command \
    "$(run_guest_script "'$GUEST_SCRIPTS\\run-endpoint-author.ps1' -Phase setup -StatePath '$GUEST_STATE' -CandidateZip '$GUEST_SCRIPTS\\candidate.zip' -ExpectedPath '$GUEST_SCRIPTS\\expected.json' -OutputDir '$GUEST_OUT' -TargetComputer '$GPO_STUDIO_LAB_ENDPOINT_GUEST'")")

TARGET_GPO=$(printf '%s' "$SETUP_OUT" | tr -d '\r' | sed -n 's/^TARGET_GPO=//p' | head -1)
AUTHOR_WORK_DIR=$(printf '%s' "$SETUP_OUT" | tr -d '\r' | sed -n 's/^WORK_DIR=//p' | head -1)
if [[ -z "$TARGET_GPO" || -z "$AUTHOR_WORK_DIR" ]]; then
    echo "ERROR: authoring setup did not report a target GPO and work directory" >&2
    printf '%s\n' "$SETUP_OUT" >&2
    exit 1
fi
echo "TARGET_GPO=$TARGET_GPO"

# ----------------------------------------------------------------- observe ---
# Deliberately not `set -e`-fatal: the observation half can fail legitimately
# (a GPO that never arrives is a real outcome), and its failure must not skip
# the evidence pull or pre-empt the trap's cleanup with a bare exit.
OBSERVE_STATUS=0
OBSERVE_OUT=$(endpoint -Action exec -TimeoutSeconds 2400 -Command \
    "$(run_guest_script "'$GUEST_SCRIPTS\\run-endpoint-observe.ps1' -Phase observe -ExpectedPath '$GUEST_SCRIPTS\\expected.json' -OutputDir '$GUEST_OUT' -TargetGpo '$TARGET_GPO'")") || OBSERVE_STATUS=$?
printf '%s\n' "$OBSERVE_OUT"

OBSERVE_WORK_DIR=$(printf '%s' "$OBSERVE_OUT" | tr -d '\r' | sed -n 's/^WORK_DIR=//p' | head -1)
if [[ -z "$OBSERVE_WORK_DIR" ]]; then
    # Fall back to the newest run directory: the script writes its result in a
    # finally block, so evidence usually exists even when the script threw.
    OBSERVE_WORK_DIR=$(endpoint -Action exec -Command \
        "(Get-ChildItem '$GUEST_OUT' -Directory -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName" \
        | tr -d '\r\n' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//') || true
fi

# -------------------------------------------------------------------- pull ---
# Before cleanup, because the authoring teardown unlinks the GPO and the pull
# must capture the estate as the observation saw it.
if [[ -n "$OBSERVE_WORK_DIR" ]]; then
    endpoint -Action pull -RemotePath "$OBSERVE_WORK_DIR" -LocalPath "$LOCAL_DIR/observe" >/dev/null
fi
endpoint -Action pull -RemotePath "$GUEST_SCRIPTS\\run-endpoint-observe.ps1" \
    -LocalPath "$LOCAL_DIR/deployed" >/dev/null

# --------------------------------------------------------------- teardown ----
# Explicit, so a cleanup failure is a lane failure. The trap stays armed for the
# paths that never reach here.
cleanup_author

author -Action pull -RemotePath "$AUTHOR_WORK_DIR" -LocalPath "$LOCAL_DIR/author" >/dev/null
author -Action pull -RemotePath "$GUEST_SCRIPTS\\run-endpoint-author.ps1" \
    -LocalPath "$LOCAL_DIR/deployed" >/dev/null

# The client's DURABLE state, checked only now.
#
# The observation half unregistered the tasks while the GPO was still linked, so
# its absence claim was provisional: any refresh before the unlink would have
# recreated every GPP Replace item. This phase refreshes policy AFTER the
# teardown, so nothing can bring them back, and re-queries. The finalizer treats
# a missing or unclean verify result as a lane failure -- an endpoint left
# carrying the run's tasks is exactly the claim this harness exists to refuse.
mkdir -p "$LOCAL_DIR/verify"
VERIFY_STATUS=0
verify_endpoint >/dev/null || VERIFY_STATUS=$?
endpoint -Action pull -RemotePath "$GUEST_OUT\\verify" -LocalPath "$LOCAL_DIR/verify" >/dev/null || true
if [[ "$VERIFY_STATUS" -ne 0 ]]; then
    echo "WARNING: post-teardown verification exited $VERIFY_STATUS; the client may still carry run state" >&2
fi

# Locally-executed scripts: the source-tree copy IS the executed copy.
cp "$SCRIPT_DIR/run-endpoint-oracle.sh" "$SCRIPT_DIR/psdirect.ps1" \
    "$REPO_ROOT/scripts/plan-033/build-endpoint-candidate.py" "$LOCAL_DIR/"

echo "LOCAL_RUN_DIR=$LOCAL_DIR"
echo "CANDIDATE_DIR=$CANDIDATE_DIR"
if [[ "$OBSERVE_STATUS" -ne 0 ]]; then
    echo "NOTE: the observation half exited $OBSERVE_STATUS; the finalizer decides whether that is a verdict or a lane failure." >&2
fi

uv run python scripts/windows-oracle/finalize_endpoint_run.py "$LOCAL_DIR" \
    --candidate-root "$CANDIDATE_DIR" --repo-root "$REPO_ROOT" --transport psdirect
