#!/usr/bin/env bash
# Run under: ACB_VAULT_ENV=~/.claude/vault.env acb exec cred:svc-da -- bash scripts/windows-oracle/run-wp3-oracle.sh

set -euo pipefail

# Oracle target. Defaults to the historical shared host so no certified lane
# changes behaviour. Override to run against the disposable lab estate --
# but re-pointing a lane requires a fresh qualification run and a re-freeze
# of docs/plan-033/environment-spec.md, not just this variable: every
# certification is bound to the environment recorded in its own manifest.
HOST="${GPO_STUDIO_ORACLE_HOST:-cw-admin@mvmcitest01}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
: "${UPN:?UPN not injected}" "${PASSWORD:?PASSWORD not injected}"

CANDIDATE_DIR="/tmp/opencode/wp3-candidate-$(date +%Y%m%d%H%M%S)"
uv run python scripts/plan-033/build-wp3-candidate.py "$CANDIDATE_DIR"

ssh "$HOST" 'New-Item -ItemType Directory -Force -Path C:\gpo-studio\scripts,C:\gpo-studio\out | Out-Null'
scp "$SCRIPT_DIR/run-wp3-security-template.ps1" "$SCRIPT_DIR/remote-run.ps1" "$HOST:C:/gpo-studio/scripts/"
scp "$CANDIDATE_DIR/candidate.inf" "$CANDIDATE_DIR/expected.json" "$HOST:C:/gpo-studio/scripts/"
scp "$SCRIPT_DIR/remote-run.ps1" "$HOST:C:/gpo-studio/remote-run.ps1"

UPN_PS="${UPN//"'"/"''"}"
PW_PS="${PASSWORD//"'"/"''"}"
# Disposable-lab tradeoff: the encoded launcher and schtasks /RP argument are
# transient but decodable by a privileged observer. Never use this harness in a
# shared environment; ACB prevents the secret from returning to this process.
LAUNCHER="& C:\gpo-studio\remote-run.ps1 -Upn '$UPN_PS' -Pw '$PW_PS' -Harness 'wp3'"
ENCODED=$(printf '%s' "$LAUNCHER" | iconv -t UTF-16LE | base64 -w0)
ssh "$HOST" "powershell -NoProfile -EncodedCommand $ENCODED"

RUN_DIR=$(ssh "$HOST" 'powershell -NoProfile -Command "(Get-ChildItem C:\gpo-studio\out -Directory -Filter wp3-security-template-* | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName"')
RUN_DIR=$(printf '%s' "$RUN_DIR" | tr -d '\r\n' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')
if [[ -z "$RUN_DIR" ]]; then
    echo "ERROR: no WP-3 run directory produced" >&2
    exit 1
fi

LOCAL_DIR="/tmp/opencode/wp3-oracle-run-$(date +%Y%m%d%H%M%S)"
mkdir -p "$LOCAL_DIR/deployed"
REMOTE_PATH=$(printf '%s' "$RUN_DIR" | sed 's#\\#/#g')
scp -r "${HOST}:${REMOTE_PATH}/." "$LOCAL_DIR/"

# Retrieve the actual deployed Windows harness files after execution.
scp "$HOST:C:/gpo-studio/scripts/run-wp3-security-template.ps1" "$LOCAL_DIR/deployed/"
scp "$HOST:C:/gpo-studio/scripts/remote-run.ps1" "$LOCAL_DIR/deployed/"
scp "$HOST:C:/gpo-studio/remote-run.ps1" "$LOCAL_DIR/deployed/remote-run-launcher.ps1"

# These scripts ran locally, so retain the exact source-tree bytes. Execute the
# retained finalizer copy so the verdict-producing code is itself evidence.
cp "$SCRIPT_DIR/run-wp3-oracle.sh" \
    "$SCRIPT_DIR/finalize_wp3_run.py" \
    "$REPO_ROOT/scripts/plan-033/build-wp3-candidate.py" \
    "$LOCAL_DIR/"

echo "LOCAL_RUN_DIR=$LOCAL_DIR"
uv run python "$LOCAL_DIR/finalize_wp3_run.py" "$LOCAL_DIR" --repo-root "$REPO_ROOT"
