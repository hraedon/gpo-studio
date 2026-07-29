#!/usr/bin/env bash
# Plan 033 WP-1B writer-conformance run.
# Run under: ACB_VAULT_ENV=~/.claude/vault.env acb exec cred:svc-da -- bash scripts/windows-oracle/run-wp1b-oracle.sh

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

STAMP="$(date +%Y%m%d%H%M%S)"
CANDIDATE_DIR="/tmp/opencode/wp1b-candidates-$STAMP"
uv run python scripts/plan-033/build-wp1b-candidates.py "$CANDIDATE_DIR"

ssh "$HOST" 'New-Item -ItemType Directory -Force -Path C:\gpo-studio\scripts,C:\gpo-studio\out | Out-Null; Remove-Item -Recurse -Force C:\gpo-studio\scripts\wp1b -ErrorAction SilentlyContinue; Get-ChildItem C:\gpo-studio\out -Directory -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force'
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

RUN_DIR=$(ssh "$HOST" 'powershell -NoProfile -Command "(Get-ChildItem C:\gpo-studio\out -Directory | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName"')
RUN_DIR=$(printf '%s' "$RUN_DIR" | tr -d '\r\n' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')
if [[ -z "$RUN_DIR" ]]; then
    echo "ERROR: no WP-1B run directory produced" >&2
    exit 1
fi

LOCAL_DIR="/tmp/opencode/wp1b-oracle-run-$STAMP"
mkdir -p "$LOCAL_DIR/deployed"
REMOTE_PATH=$(printf '%s' "$RUN_DIR" | sed 's#\\#/#g')
scp -r "${HOST}:${REMOTE_PATH}/." "$LOCAL_DIR/"

# Retrieve the harness files that actually executed on Windows (not source-tree
# copies) so the finalizer can bind them to the recorded commit.
scp "$HOST:C:/gpo-studio/scripts/run-wp1b-writer.ps1" "$LOCAL_DIR/deployed/"
scp "$HOST:C:/gpo-studio/scripts/remote-run.ps1" "$LOCAL_DIR/deployed/"
scp "$HOST:C:/gpo-studio/remote-run.ps1" "$LOCAL_DIR/deployed/remote-run-launcher.ps1"

# Locally-executed scripts: the source-tree copy IS the executed copy.
cp "$SCRIPT_DIR/run-wp1b-oracle.sh" "$REPO_ROOT/scripts/plan-033/build-wp1b-candidates.py" "$LOCAL_DIR/"

echo "LOCAL_RUN_DIR=$LOCAL_DIR"
echo "CANDIDATE_DIR=$CANDIDATE_DIR"
uv run python scripts/windows-oracle/finalize_wp1b_run.py "$LOCAL_DIR" \
    --candidate-root "$CANDIDATE_DIR" --repo-root "$REPO_ROOT"
