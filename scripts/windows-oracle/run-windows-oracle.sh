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
scp "$REPO_ROOT/tests/fixtures/recipes/synthetic-registry-basic.json" "$HOST:C:/gpo-studio/recipe.json"

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
LOCAL_DIR="/tmp/opencode/oracle-run-${RUN_LABEL}"
rm -rf "$LOCAL_DIR"
mkdir -p "$LOCAL_DIR"
scp -r "${HOST}:$(printf '%s' "$RUN_DIR" | sed 's#\\#/#g')/." "$LOCAL_DIR/" 2>/dev/null

echo "=== retrieved files ==="
find "$LOCAL_DIR" -type f | sort

echo "LOCAL_RUN_DIR=$LOCAL_DIR"
