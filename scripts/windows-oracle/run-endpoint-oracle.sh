#!/usr/bin/env bash
# Plan 033 WP-1B step 5 endpoint run (WI-018, WI-021).
# Run under: ACB_VAULT_ENV=~/.claude/vault.env acb exec cred:svc-da -- bash scripts/windows-oracle/run-endpoint-oracle.sh
set -euo pipefail
HOST="cw-admin@mvmcitest01"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
: "${UPN:?UPN not injected}" "${PASSWORD:?PASSWORD not injected}"

STAMP="$(date +%Y%m%d%H%M%S)"
CAND="/tmp/opencode/endpoint-candidate-$STAMP"
uv run python scripts/plan-033/build-endpoint-candidate.py "$CAND"

ssh "$HOST" 'New-Item -ItemType Directory -Force -Path C:\gpo-studio\scripts,C:\gpo-studio\out | Out-Null'
scp -q "$SCRIPT_DIR/run-endpoint.ps1" "$SCRIPT_DIR/remote-run.ps1" "$HOST:C:/gpo-studio/scripts/"
scp -q "$CAND/candidate.zip" "$CAND/expected.json" "$HOST:C:/gpo-studio/scripts/"
scp -q "$SCRIPT_DIR/remote-run.ps1" "$HOST:C:/gpo-studio/remote-run.ps1"

UPN_PS="${UPN//"'"/"''"}"; PW_PS="${PASSWORD//"'"/"''"}"
LAUNCHER="& C:\gpo-studio\remote-run.ps1 -Upn '$UPN_PS' -Pw '$PW_PS' -Harness 'endpoint'"
ENCODED=$(printf '%s' "$LAUNCHER" | iconv -t UTF-16LE | base64 -w0)
ssh "$HOST" "powershell -NoProfile -EncodedCommand $ENCODED"

RUN_DIR=$(ssh "$HOST" 'powershell -NoProfile -Command "(Get-ChildItem C:\gpo-studio\out -Directory | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName"')
RUN_DIR=$(printf '%s' "$RUN_DIR" | tr -d '\r\n' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')
[[ -n "$RUN_DIR" ]] || { echo "ERROR: no endpoint run directory produced" >&2; exit 1; }

LOCAL="/tmp/opencode/endpoint-run-$STAMP"
mkdir -p "$LOCAL"
scp -q -r "${HOST}:$(printf '%s' "$RUN_DIR" | sed 's#\\#/#g')/." "$LOCAL/"
echo "LOCAL_RUN_DIR=$LOCAL"
