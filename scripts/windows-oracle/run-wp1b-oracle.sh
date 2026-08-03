#!/usr/bin/env bash
# Plan 033 WP-1B writer-conformance run.
#
# Two transports, selected by GPO_STUDIO_ORACLE_TRANSPORT:
#
#   ssh       (default) the historical shared host, which answers SSH directly.
#             Runs the harness through the remote-run.ps1 scheduled-task
#             launcher, because an SSH non-interactive session gets a network
#             logon that cannot authenticate outward to AD.
#
#             ACB_VAULT_ENV=~/.claude/vault.env \
#                 acb exec cred:svc-da -- bash scripts/windows-oracle/run-wp1b-oracle.sh
#
#   psdirect  the disposable evidence estate, whose guests have no networking at
#             all. Reaches them controller -> WinRM -> Hyper-V host -> PowerShell
#             Direct -> guest via psdirect.ps1, and invokes the harness DIRECTLY:
#             PowerShell Direct carries the credential through the hypervisor, so
#             the resulting logon does authenticate outward. Measured on the
#             estate -- New-GPO, Backup-GPO, SYSVOL enumeration, Remove-GPO all
#             succeed over this transport with no scheduled task. That also
#             removes the schtasks /RP password argument, so this path is both
#             simpler and safer than the one it replaces.
#
#             GPO_STUDIO_ORACLE_TRANSPORT=psdirect \
#             GPO_STUDIO_LAB_HOST=<hyper-v host> GPO_STUDIO_LAB_GUEST=<guest> \
#                 ACB_VAULT_ENV=~/.claude/evidence-lab.env \
#                 acb exec cred:lab-hyperv-control cred:lab-guest-bootstrap -- \
#                     bash scripts/windows-oracle/run-wp1b-oracle.sh
#
# Re-pointing a lane is not a variable change: every certification is bound to
# the environment recorded in its own manifest, so a transport or host change
# needs a fresh qualification run and a re-freeze of
# docs/plan-033/environment-spec.md. The transport is recorded in the verdict.

set -euo pipefail

TRANSPORT="${GPO_STUDIO_ORACLE_TRANSPORT:-ssh}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

GUEST_SCRIPTS='C:\gpo-studio\scripts'
GUEST_OUT='C:\gpo-studio\out'

case "$TRANSPORT" in
    ssh)
        HOST="${GPO_STUDIO_ORACLE_HOST:-cw-admin@mvmcitest01}"
        : "${UPN:?UPN not injected}" "${PASSWORD:?PASSWORD not injected}"
        ;;
    psdirect)
        : "${GPO_STUDIO_LAB_HOST:?GPO_STUDIO_LAB_HOST not set}"
        : "${GPO_STUDIO_LAB_GUEST:?GPO_STUDIO_LAB_GUEST not set}"
        # psdirect.ps1 enforces the composed acb checkout itself; fail early
        # here so the lane does not build candidates it cannot deliver.
        : "${HYPERV_CONTROL_USERNAME:?composed acb checkout missing (cred:lab-hyperv-control)}"
        : "${GUEST_BOOTSTRAP_USERNAME:?composed acb checkout missing (cred:lab-guest-bootstrap)}"
        ;;
    *)
        echo "ERROR: unknown transport '$TRANSPORT' (expected ssh or psdirect)" >&2
        exit 2
        ;;
esac

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

if [[ "$TRANSPORT" == "ssh" ]]; then
    ssh "$HOST" "$PREPARE"
    scp "$SCRIPT_DIR/run-wp1b-writer.ps1" "$SCRIPT_DIR/remote-run.ps1" "$HOST:C:/gpo-studio/scripts/"
    scp -r "$CANDIDATE_DIR" "$HOST:C:/gpo-studio/scripts/wp1b"
    scp "$SCRIPT_DIR/remote-run.ps1" "$HOST:C:/gpo-studio/remote-run.ps1"

    UPN_PS="${UPN//"'"/"''"}"
    PW_PS="${PASSWORD//"'"/"''"}"
    # Disposable-lab tradeoff: the encoded launcher and schtasks /RP argument are
    # transient but decodable by a privileged observer. Never use this harness in a
    # shared environment; ACB prevents the secret from returning to this process.
    LAUNCHER="& C:\gpo-studio\remote-run.ps1 -Upn '$UPN_PS' -Pw '$PW_PS' -Harness 'wp1b'"
    ENCODED=$(printf '%s' "$LAUNCHER" | iconv -t UTF-16LE | base64 -w0)
    ssh "$HOST" "powershell -NoProfile -EncodedCommand $ENCODED"

    RUN_DIR=$(ssh "$HOST" "powershell -NoProfile -Command \"$LATEST_RUN\"")
else
    psdirect -Action exec -Command "$PREPARE" >/dev/null
    psdirect -Action push -LocalPath "$SCRIPT_DIR/run-wp1b-writer.ps1" \
        -RemotePath "$GUEST_SCRIPTS\\run-wp1b-writer.ps1" >/dev/null
    psdirect -Action push -LocalPath "$CANDIDATE_DIR" -RemotePath "$GUEST_SCRIPTS\\wp1b" >/dev/null

    # No launcher and no encoded credential: the harness runs directly under the
    # brokered identity this transport already carries. The 25-minute deadline
    # that remote-run.ps1 applied on the SSH path moves onto the transport call.
    psdirect -Action exec -TimeoutSeconds 1500 -Command \
        "& '$GUEST_SCRIPTS\\run-wp1b-writer.ps1' -CandidateRoot '$GUEST_SCRIPTS\\wp1b' -OutputDir '$GUEST_OUT'"

    RUN_DIR=$(psdirect -Action exec -Command "$LATEST_RUN")
fi

RUN_DIR=$(printf '%s' "$RUN_DIR" | tr -d '\r\n' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')
if [[ -z "$RUN_DIR" ]]; then
    echo "ERROR: no WP-1B run directory produced" >&2
    exit 1
fi

# Retrieve the harness files that actually executed on Windows (not source-tree
# copies) so the finalizer can bind them to the recorded commit.
if [[ "$TRANSPORT" == "ssh" ]]; then
    REMOTE_PATH=$(printf '%s' "$RUN_DIR" | sed 's#\\#/#g')
    scp -r "${HOST}:${REMOTE_PATH}/." "$LOCAL_DIR/"
    scp "$HOST:C:/gpo-studio/scripts/run-wp1b-writer.ps1" "$LOCAL_DIR/deployed/"
    scp "$HOST:C:/gpo-studio/scripts/remote-run.ps1" "$LOCAL_DIR/deployed/"
    scp "$HOST:C:/gpo-studio/remote-run.ps1" "$LOCAL_DIR/deployed/remote-run-launcher.ps1"
else
    # Pulling a directory delivers its CONTENTS, matching `scp -r host:dir/. local/`.
    psdirect -Action pull -RemotePath "$RUN_DIR" -LocalPath "$LOCAL_DIR" >/dev/null
    psdirect -Action pull -RemotePath "$GUEST_SCRIPTS\\run-wp1b-writer.ps1" \
        -LocalPath "$LOCAL_DIR/deployed" >/dev/null
fi

# Locally-executed scripts: the source-tree copy IS the executed copy.
cp "$SCRIPT_DIR/run-wp1b-oracle.sh" "$REPO_ROOT/scripts/plan-033/build-wp1b-candidates.py" "$LOCAL_DIR/"
if [[ "$TRANSPORT" == "psdirect" ]]; then
    cp "$SCRIPT_DIR/psdirect.ps1" "$LOCAL_DIR/"
fi

echo "LOCAL_RUN_DIR=$LOCAL_DIR"
echo "CANDIDATE_DIR=$CANDIDATE_DIR"
uv run python scripts/windows-oracle/finalize_wp1b_run.py "$LOCAL_DIR" \
    --candidate-root "$CANDIDATE_DIR" --repo-root "$REPO_ROOT" --transport "$TRANSPORT"
