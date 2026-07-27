#!/usr/bin/env bash
# Run under: ACB_VAULT_ENV=~/.claude/vault.env acb exec cred:svc-da -- bash scripts/windows-oracle/run-wp2-oracle.sh

set -euo pipefail

HOST="cw-admin@mvmcitest01"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
: "${UPN:?UPN not injected}" "${PASSWORD:?PASSWORD not injected}"

CANDIDATE_DIR="/tmp/opencode/wp2-candidate-$(date +%Y%m%d%H%M%S)"
uv run python scripts/plan-033/build-wp2-candidate.py "$CANDIDATE_DIR"

ssh "$HOST" 'New-Item -ItemType Directory -Force -Path C:\gpo-studio\scripts,C:\gpo-studio\out | Out-Null; Get-ChildItem C:\gpo-studio\out -Directory -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force'
scp "$SCRIPT_DIR/run-wp2-import.ps1" "$SCRIPT_DIR/remote-run.ps1" "$HOST:C:/gpo-studio/scripts/"
scp "$CANDIDATE_DIR/candidate.zip" "$CANDIDATE_DIR/expected.json" "$HOST:C:/gpo-studio/scripts/"
scp "$SCRIPT_DIR/remote-run.ps1" "$HOST:C:/gpo-studio/remote-run.ps1"

UPN_PS="${UPN//"'"/"''"}"
PW_PS="${PASSWORD//"'"/"''"}"
# Disposable-lab tradeoff: the encoded launcher and schtasks /RP argument are
# transient but decodable by a privileged observer. Never use this harness in a
# shared environment; ACB prevents the secret from returning to this process.
LAUNCHER="& C:\gpo-studio\remote-run.ps1 -Upn '$UPN_PS' -Pw '$PW_PS' -Harness 'wp2'"
ENCODED=$(printf '%s' "$LAUNCHER" | iconv -t UTF-16LE | base64 -w0)
ssh "$HOST" "powershell -NoProfile -EncodedCommand $ENCODED"

RUN_DIR=$(ssh "$HOST" 'powershell -NoProfile -Command "(Get-ChildItem C:\gpo-studio\out -Directory | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName"')
RUN_DIR=$(printf '%s' "$RUN_DIR" | tr -d '\r\n' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')
if [[ -z "$RUN_DIR" ]]; then
    echo "ERROR: no WP-2 run directory produced" >&2
    exit 1
fi

LOCAL_DIR="/tmp/opencode/wp2-oracle-run-$(date +%Y%m%d%H%M%S)"
mkdir -p "$LOCAL_DIR"
REMOTE_PATH=$(printf '%s' "$RUN_DIR" | sed 's#\\#/#g')
scp -r "${HOST}:${REMOTE_PATH}/." "$LOCAL_DIR/"
cp "$SCRIPT_DIR/run-wp2-import.ps1" "$SCRIPT_DIR/remote-run.ps1" "$SCRIPT_DIR/run-wp2-oracle.sh" "$REPO_ROOT/scripts/plan-033/build-wp2-candidate.py" "$LOCAL_DIR/"

echo "LOCAL_RUN_DIR=$LOCAL_DIR"
uv run python scripts/windows-oracle/finalize_wp2_import_run.py "$LOCAL_DIR" --repo-root "$REPO_ROOT"
