#!/usr/bin/env bash
# Plan 033 WP-0 evidence-harness run: setup -> collect -> cleanup -> raw manifest.
#
# Two transports, selected by GPO_STUDIO_ORACLE_TRANSPORT:
#
#   ssh       (default) the historical shared host, which answers SSH directly.
#             Runs the harness through the remote-run.ps1 scheduled-task
#             launcher, because an SSH non-interactive session gets a network
#             logon that cannot authenticate outward to AD.
#
#             ACB_VAULT_ENV=~/.claude/vault.env \
#                 acb exec cred:svc-da -- bash scripts/windows-oracle/run-windows-oracle.sh [success|fail]
#
#   psdirect  the disposable evidence estate, whose guests have no networking at
#             all. Reaches them controller -> WinRM -> Hyper-V host -> PowerShell
#             Direct -> guest via psdirect.ps1, and invokes the harness DIRECTLY:
#             PowerShell Direct carries the credential through the hypervisor, so
#             the resulting logon does authenticate outward. That also removes
#             the schtasks /RP password argument, so this path is both simpler
#             and safer than the one it replaces.
#
#             GPO_STUDIO_ORACLE_TRANSPORT=psdirect \
#             GPO_STUDIO_LAB_HOST=<hyper-v host> GPO_STUDIO_LAB_GUEST=<guest> \
#                 ACB_VAULT_ENV=~/.claude/evidence-lab.env \
#                 acb exec cred:lab-hyperv-control cred:lab-guest-bootstrap -- \
#                     bash scripts/windows-oracle/run-windows-oracle.sh [success|fail]
#
# `fail` deliberately applies a setting to a non-existent GPO. It is not a
# broken run: it is how the lane demonstrates that a real failure produces a
# parser-valid `fail` manifest rather than a missing one.
#
# Re-pointing a lane is not a variable change: every certification is bound to
# the environment recorded in its own manifest, so a transport or host change
# needs a fresh qualification run and a re-freeze of
# docs/plan-033/environment-spec.md.

set -euo pipefail

MODE="${1:-success}"
TRANSPORT="${GPO_STUDIO_ORACLE_TRANSPORT:-ssh}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

GUEST_ROOT='C:\gpo-studio'
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
        # here so the lane does not deploy a harness it cannot drive.
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

if [[ "$MODE" == "fail" ]]; then
  FAIL_FLAG="-FailInjected"
  RUN_LABEL="fail"
else
  FAIL_FLAG=""
  RUN_LABEL="success"
fi

RECIPE_SOURCE="$REPO_ROOT/tests/fixtures/recipes/synthetic-registry-basic.json"
PREPARE="New-Item -ItemType Directory -Force -Path '$GUEST_SCRIPTS','$GUEST_OUT' | Out-Null; Get-ChildItem '$GUEST_OUT' -Directory -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force"
LATEST_RUN="(Get-ChildItem '$GUEST_OUT' -Directory | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName"

echo "=== deploying harness (transport=$TRANSPORT) ==="
if [[ "$TRANSPORT" == "ssh" ]]; then
    ssh "$HOST" "$PREPARE"
    scp "$SCRIPT_DIR/run-evidence.ps1" "$SCRIPT_DIR/common.psm1" "$SCRIPT_DIR/remote-run.ps1" "$HOST:C:/gpo-studio/scripts/"
    scp "$SCRIPT_DIR/remote-run.ps1" "$HOST:C:/gpo-studio/remote-run.ps1"
    scp "$RECIPE_SOURCE" "$HOST:C:/gpo-studio/scripts/recipe.json"
else
    psdirect -Action exec -Command "$PREPARE" >/dev/null
    psdirect -Action push -LocalPath "$SCRIPT_DIR/run-evidence.ps1" \
        -RemotePath "$GUEST_SCRIPTS\\run-evidence.ps1" >/dev/null
    psdirect -Action push -LocalPath "$SCRIPT_DIR/common.psm1" \
        -RemotePath "$GUEST_SCRIPTS\\common.psm1" >/dev/null
    psdirect -Action push -LocalPath "$RECIPE_SOURCE" \
        -RemotePath "$GUEST_SCRIPTS\\recipe.json" >/dev/null
fi

# Record the deployed harness inputs (scripts + recipe) with their hashes and
# the deploy-time commit.  This happens on the control host (which has git) and
# BEFORE the credential boundary, so no secret is involved.  finalize re-verifies
# the deployed bytes against this record and against the recorded commit.
#
# The record names its own transport, because the two deploy different files:
# there is no launcher to bind over psdirect, and the transport script that
# replaces it runs on the controller.
DEPLOY_COMMIT=$(git -C "$REPO_ROOT" rev-parse HEAD)
HARNESS_INPUTS_JSON=$(mktemp /tmp/opencode/harness-inputs.XXXXXX.json)
uv run python - "$REPO_ROOT" "$SCRIPT_DIR" "$DEPLOY_COMMIT" "$HARNESS_INPUTS_JSON" "$TRANSPORT" <<'PYEOF'
import hashlib
import json
import sys
from pathlib import Path

repo_root, script_dir, commit, out_path, transport = (
    Path(sys.argv[1]),
    Path(sys.argv[2]),
    sys.argv[3],
    Path(sys.argv[4]),
    sys.argv[5],
)
sources = {
    "scripts/run-evidence.ps1": script_dir / "run-evidence.ps1",
    "scripts/common.psm1": script_dir / "common.psm1",
    "scripts/recipe.json": repo_root / "tests/fixtures/recipes/synthetic-registry-basic.json",
    "orchestrator/run-windows-oracle.sh": script_dir / "run-windows-oracle.sh",
}
if transport == "ssh":
    sources["scripts/remote-run.ps1"] = script_dir / "remote-run.ps1"
else:
    sources["orchestrator/psdirect.ps1"] = script_dir / "psdirect.ps1"
files = {}
for rel, src in sources.items():
    data = src.read_bytes()
    files[rel] = {"sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)}
out_path.write_text(
    json.dumps({"commit": commit, "transport": transport, "files": files}, indent=2)
    + "\n",
    encoding="utf-8",
)
PYEOF

if [[ "$TRANSPORT" == "ssh" ]]; then
    scp "$HARNESS_INPUTS_JSON" "$HOST:C:/gpo-studio/harness-inputs.json"
else
    psdirect -Action push -LocalPath "$HARNESS_INPUTS_JSON" \
        -RemotePath "$GUEST_ROOT\\harness-inputs.json" >/dev/null
fi
rm -f "$HARNESS_INPUTS_JSON"

echo "=== running harness (mode=$RUN_LABEL) ==="
if [[ "$TRANSPORT" == "ssh" ]]; then
    # Encode a tiny launcher as UTF-16LE base64 for -EncodedCommand.  The secret
    # is embedded in the encoded blob (not plaintext argv) and is never printed to
    # stdout, so it never returns to the calling agent's context.  remote-run.ps1
    # (already on the host) creates and runs the scheduled task as svc-da.
    #
    # Single quotes in the secret are doubled (PowerShell's single-quote escape) so
    # they cannot terminate the literal or inject code.  This is a disposable-lab
    # helper: the base64 blob is transient in argv and decodable, which is the
    # accepted tradeoff for this isolated environment.
    UPN_PS="${UPN//"'"/"''"}"
    PW_PS="${PASSWORD//"'"/"''"}"
    if [[ -n "$FAIL_FLAG" ]]; then
      LAUNCHER="& C:\\gpo-studio\\remote-run.ps1 -Upn '$UPN_PS' -Pw '$PW_PS' -FailFlag '$FAIL_FLAG'"
    else
      LAUNCHER="& C:\\gpo-studio\\remote-run.ps1 -Upn '$UPN_PS' -Pw '$PW_PS'"
    fi
    ENCODED=$(printf '%s' "$LAUNCHER" | iconv -t UTF-16LE | base64 -w0)
    ssh "$HOST" "powershell -NoProfile -EncodedCommand $ENCODED"

    RUN_DIR=$(ssh "$HOST" "powershell -NoProfile -Command \"$LATEST_RUN\"")
else
    # No launcher and no encoded credential: the harness runs directly under the
    # brokered identity this transport already carries. The 6-minute deadline
    # that remote-run.ps1 applied on the SSH path moves onto the transport call.
    psdirect -Action exec -TimeoutSeconds 360 -Command \
        "& '$GUEST_SCRIPTS\\run-evidence.ps1' -RecipePath '$GUEST_SCRIPTS\\recipe.json' -OutputDir '$GUEST_OUT' $FAIL_FLAG"

    RUN_DIR=$(psdirect -Action exec -Command "$LATEST_RUN")
fi

# Trim CR/LF/surrounding whitespace WITHOUT xargs (xargs treats backslashes as
# escapes and would strip the Windows path separators).
RUN_DIR=$(printf '%s' "$RUN_DIR" | tr -d '\r\n' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')
echo "RUN_DIR=$RUN_DIR"

if [[ -z "$RUN_DIR" ]]; then
  echo "ERROR: no run dir produced" >&2
  exit 1
fi

echo "=== retrieving run dir ==="
# Use a unique local dir so stale, permission-locked files from a previous run
# can never block this retrieval.
LOCAL_DIR="/tmp/opencode/oracle-run-${RUN_LABEL}-$(date +%Y%m%d%H%M%S)"
mkdir -p "$LOCAL_DIR/scripts" "$LOCAL_DIR/orchestrator"
if [[ "$TRANSPORT" == "ssh" ]]; then
    REMOTE_PATH=$(printf '%s' "$RUN_DIR" | sed 's#\\#/#g')
    if ! scp -r "${HOST}:${REMOTE_PATH}/." "$LOCAL_DIR/"; then
      echo "ERROR: scp retrieval failed for $REMOTE_PATH" >&2
      exit 1
    fi
else
    # Pulling a directory delivers its CONTENTS, matching `scp -r host:dir/. local/`.
    psdirect -Action pull -RemotePath "$RUN_DIR" -LocalPath "$LOCAL_DIR" >/dev/null
fi

if [[ ! -f "$LOCAL_DIR/manifest.raw.json" ]]; then
  echo "ERROR: retrieved run dir has no manifest.raw.json" >&2
  exit 1
fi

# Pull the deployed harness inputs (the exact scripts + recipe that ran) and the
# deploy-time record into the local run dir so finalize can verify them against
# the recorded commit.
if [[ "$TRANSPORT" == "ssh" ]]; then
    scp "$HOST:C:/gpo-studio/scripts/run-evidence.ps1" "$HOST:C:/gpo-studio/scripts/common.psm1" "$HOST:C:/gpo-studio/scripts/recipe.json" "$HOST:C:/gpo-studio/scripts/remote-run.ps1" "$LOCAL_DIR/scripts/"
    scp "$HOST:C:/gpo-studio/harness-inputs.json" "$LOCAL_DIR/harness-inputs.json"
else
    for name in run-evidence.ps1 common.psm1 recipe.json; do
        psdirect -Action pull -RemotePath "$GUEST_SCRIPTS\\$name" \
            -LocalPath "$LOCAL_DIR/scripts" >/dev/null
    done
    psdirect -Action pull -RemotePath "$GUEST_ROOT\\harness-inputs.json" \
        -LocalPath "$LOCAL_DIR" >/dev/null
fi

# The orchestrator and the transport run on the control host (never deployed to
# the guest), so copy the exact files that were hashed at deploy time.
cp "$SCRIPT_DIR/run-windows-oracle.sh" "$LOCAL_DIR/orchestrator/run-windows-oracle.sh"
if [[ "$TRANSPORT" == "psdirect" ]]; then
    cp "$SCRIPT_DIR/psdirect.ps1" "$LOCAL_DIR/orchestrator/psdirect.ps1"
fi

echo "=== retrieved files ==="
find "$LOCAL_DIR" -type f | sort

echo "LOCAL_RUN_DIR=$LOCAL_DIR"
echo "NEXT: uv run python scripts/windows-oracle/finalize_oracle_run.py $LOCAL_DIR --repo-root $REPO_ROOT"
