#!/usr/bin/env bash
# Drive the Plan 033 WP-0 Windows oracle run as svc-da via a scheduled task.
#
# Run under:  ACB_VAULT_ENV=~/.claude/vault.env acb exec cred:svc-da -- bash scripts/windows-oracle/run-windows-oracle.sh [success|fail]
#
# acb injects USERNAME/UPN/PASSWORD into this script's environment.  The secret
# is forwarded to the DC only as a scheduled-task argument; it is never echoed
# to stdout, so it never returns to the calling agent's context.

set -euo pipefail

MODE="${1:-success}"
HOST="cw-admin@mvmcitest01"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
: "${UPN:?UPN not injected}" "${PASSWORD:?PASSWORD not injected}"

if [[ "$MODE" == "fail" ]]; then
  FAIL_FLAG="-FailInjected"
  RUN_LABEL="fail"
else
  FAIL_FLAG=""
  RUN_LABEL="success"
fi

echo "=== deploying harness to $HOST ==="
ssh "$HOST" 'New-Item -ItemType Directory -Force -Path C:\gpo-studio\scripts,C:\gpo-studio\out | Out-Null; Get-ChildItem C:\gpo-studio\out -Directory -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force'
scp "$SCRIPT_DIR/run-evidence.ps1" "$SCRIPT_DIR/common.psm1" "$SCRIPT_DIR/remote-run.ps1" "$HOST:C:/gpo-studio/scripts/"
scp "$SCRIPT_DIR/remote-run.ps1" "$HOST:C:/gpo-studio/remote-run.ps1"
scp "$REPO_ROOT/tests/fixtures/recipes/synthetic-registry-basic.json" "$HOST:C:/gpo-studio/scripts/recipe.json"

# Record the deployed harness inputs (scripts + recipe) with their hashes and
# the deploy-time commit.  This happens on the control host (which has git) and
# BEFORE the credential boundary, so no secret is involved.  finalize re-verifies
# the deployed bytes against this record and against the recorded commit.
DEPLOY_COMMIT=$(git -C "$REPO_ROOT" rev-parse HEAD)
HARNESS_INPUTS_JSON=$(mktemp /tmp/opencode/harness-inputs.XXXXXX.json)
uv run python - "$REPO_ROOT" "$SCRIPT_DIR" "$DEPLOY_COMMIT" "$HARNESS_INPUTS_JSON" <<'PYEOF'
import hashlib
import json
import sys
from pathlib import Path

repo_root, script_dir, commit, out_path = (
    Path(sys.argv[1]),
    Path(sys.argv[2]),
    sys.argv[3],
    Path(sys.argv[4]),
)
sources = {
    "scripts/run-evidence.ps1": script_dir / "run-evidence.ps1",
    "scripts/common.psm1": script_dir / "common.psm1",
    "scripts/recipe.json": repo_root / "tests/fixtures/recipes/synthetic-registry-basic.json",
}
files = {}
for rel, src in sources.items():
    data = src.read_bytes()
    files[rel] = {"sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)}
out_path.write_text(
    json.dumps({"commit": commit, "files": files}, indent=2) + "\n", encoding="utf-8"
)
PYEOF
scp "$HARNESS_INPUTS_JSON" "$HOST:C:/gpo-studio/harness-inputs.json"
rm -f "$HARNESS_INPUTS_JSON"

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

echo "=== launching scheduled task (mode=$RUN_LABEL) ==="
ssh "$HOST" "powershell -NoProfile -EncodedCommand $ENCODED"

echo "=== locating newest run dir ==="
RUN_DIR=$(ssh "$HOST" 'powershell -NoProfile -Command "(Get-ChildItem C:\gpo-studio\out -Directory | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName"')
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
mkdir -p "$LOCAL_DIR"
REMOTE_PATH=$(printf '%s' "$RUN_DIR" | sed 's#\\#/#g')
if ! scp -r "${HOST}:${REMOTE_PATH}/." "$LOCAL_DIR/"; then
  echo "ERROR: scp retrieval failed for $REMOTE_PATH" >&2
  exit 1
fi

if [[ ! -f "$LOCAL_DIR/manifest.raw.json" ]]; then
  echo "ERROR: retrieved run dir has no manifest.raw.json" >&2
  exit 1
fi

# Pull the deployed harness inputs (the exact scripts + recipe that ran) and the
# deploy-time record into the local run dir so finalize can verify them against
# the recorded commit.
mkdir -p "$LOCAL_DIR/scripts"
scp "$HOST:C:/gpo-studio/scripts/run-evidence.ps1" "$HOST:C:/gpo-studio/scripts/common.psm1" "$HOST:C:/gpo-studio/scripts/recipe.json" "$LOCAL_DIR/scripts/"
scp "$HOST:C:/gpo-studio/harness-inputs.json" "$LOCAL_DIR/harness-inputs.json"

echo "=== retrieved files ==="
find "$LOCAL_DIR" -type f | sort

echo "LOCAL_RUN_DIR=$LOCAL_DIR"
echo "NEXT: uv run python scripts/windows-oracle/finalize_oracle_run.py $LOCAL_DIR --repo-root $REPO_ROOT"
