#!/usr/bin/env bash
# Plan 033 WP-0 evidence-harness run: setup -> collect -> cleanup -> raw manifest.
# Runs against the disposable evidence estate.
#
# The estate's guests have no networking at all, so they are reached
# controller -> WinRM -> Hyper-V host -> PowerShell Direct -> guest via
# psdirect.ps1. The harness is invoked DIRECTLY: PowerShell Direct carries the
# credential through the hypervisor, so the resulting logon authenticates
# outward to AD and SYSVOL. There is no scheduled-task launcher and therefore no
# schtasks /RP password argument.
#
#   GPO_STUDIO_LAB_HOST=<hyper-v host> GPO_STUDIO_LAB_GUEST=<guest> \
#       ACB_VAULT_ENV=~/.claude/evidence-lab.env \
#       acb exec cred:lab-hyperv-control cred:lab-guest-bootstrap -- \
#           bash scripts/windows-oracle/run-windows-oracle.sh [success|fail]
#
# `fail` deliberately applies a setting to a non-existent GPO. It is not a
# broken run: it is how the lane demonstrates that a real failure produces a
# parser-valid `fail` manifest rather than a missing one.
#
# Re-pointing a lane is not a variable change: every certification is bound to
# the environment recorded in its own manifest, so a new host needs a fresh
# qualification run and a re-freeze of docs/plan-033/environment-spec.md.

set -euo pipefail

MODE="${1:-success}"
TRANSPORT=psdirect
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

: "${GPO_STUDIO_LAB_HOST:?GPO_STUDIO_LAB_HOST not set}"
: "${GPO_STUDIO_LAB_GUEST:?GPO_STUDIO_LAB_GUEST not set}"
# psdirect.ps1 enforces the composed acb checkout itself; fail early here so the
# lane does not deploy a harness it cannot drive.
: "${HYPERV_CONTROL_USERNAME:?composed acb checkout missing (cred:lab-hyperv-control)}"
: "${GUEST_BOOTSTRAP_USERNAME:?composed acb checkout missing (cred:lab-guest-bootstrap)}"

psdirect() {
    pwsh -NoProfile -File "$SCRIPT_DIR/psdirect.ps1" \
        -LabHost "$GPO_STUDIO_LAB_HOST" -Guest "$GPO_STUDIO_LAB_GUEST" "$@"
}

# Every run owns a private tree on the guest -- see the comment in
# run-wp2-oracle.sh. Lanes share one guest, and selecting "the newest output
# directory" from a shared root let two overlapping runs finalize each other's
# evidence, silently, because the verdict shape is identical.
STAMP="$(date +%Y%m%d%H%M%S)-$$"
GUEST_RUN_ROOT="C:\\gpo-studio\\runs\\$STAMP"
GUEST_SCRIPTS="$GUEST_RUN_ROOT\\scripts"
GUEST_OUT="$GUEST_RUN_ROOT\\out"

if [[ "$MODE" == "fail" ]]; then
  FAIL_FLAG="-FailInjected"
  RUN_LABEL="fail"
else
  FAIL_FLAG=""
  RUN_LABEL="success"
fi

RECIPE_SOURCE="$REPO_ROOT/tests/fixtures/recipes/synthetic-registry-basic.json"

# Refuse a colliding root rather than reusing it: reuse is how a run inherits
# another run's files.
PREPARE="if (Test-Path '$GUEST_RUN_ROOT') { throw 'run root already exists: $GUEST_RUN_ROOT' }; New-Item -ItemType Directory -Force -Path '$GUEST_SCRIPTS','$GUEST_OUT' | Out-Null"

# Reap GPOs this lane orphaned. A harness killed between New-GPO and its cleanup
# leaves a uniquely-named GPO that no later run's by-name lookup will ever find,
# so orphans accumulate on a live domain across runs that each report success.
# Age-bounded at two hours so this can never reap a concurrent run's in-flight
# GPO, which would turn a cleanup into a sabotage. The prefix is the recipe's
# gpo_name_prefix; a recipe naming its GPOs otherwise needs its own reap.
REAP="Import-Module GroupPolicy -ErrorAction Stop; \$cutoff = (Get-Date).AddHours(-2); \$orphans = @(Get-GPO -All | Where-Object { \$_.DisplayName -like 'WP0-DryRun-Registry-*' -and \$_.CreationTime -lt \$cutoff }); foreach (\$o in \$orphans) { Remove-GPO -Guid \$o.Id -Confirm:\$false -ErrorAction Stop }; 'REAPED_ORPHANS=' + \$orphans.Count + ' ' + ((\$orphans | ForEach-Object { \$_.DisplayName }) -join ',')"

# Exactly one run directory can exist under a per-invocation output root. Assert
# it rather than sorting by write time: "newest" is a guess, "the only one" is a
# fact, and a harness that produced two or zero is a lane failure worth seeing.
RUN_SELECT="\$d = @(Get-ChildItem '$GUEST_OUT' -Directory); if (\$d.Count -ne 1) { throw ('expected exactly one run directory under $GUEST_OUT, found ' + \$d.Count) }; \$d[0].FullName"

echo "=== deploying harness ==="
psdirect -Action exec -Command "$PREPARE" >/dev/null
psdirect -Action exec -Command "$REAP"
psdirect -Action push -LocalPath "$SCRIPT_DIR/run-evidence.ps1" \
    -RemotePath "$GUEST_SCRIPTS\\run-evidence.ps1" >/dev/null
psdirect -Action push -LocalPath "$SCRIPT_DIR/common.psm1" \
    -RemotePath "$GUEST_SCRIPTS\\common.psm1" >/dev/null
psdirect -Action push -LocalPath "$RECIPE_SOURCE" \
    -RemotePath "$GUEST_SCRIPTS\\recipe.json" >/dev/null

# Record the deployed harness inputs (scripts + recipe) with their hashes and
# the deploy-time commit.  This happens on the control host (which has git) and
# BEFORE the credential boundary, so no secret is involved.  finalize re-verifies
# the deployed bytes against this record and against the recorded commit.
#
# The record names its own transport: it describes what produced the run rather
# than depending on how finalization was invoked.
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
    "orchestrator/psdirect.ps1": script_dir / "psdirect.ps1",
}
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

psdirect -Action push -LocalPath "$HARNESS_INPUTS_JSON" \
    -RemotePath "$GUEST_RUN_ROOT\\harness-inputs.json" >/dev/null
rm -f "$HARNESS_INPUTS_JSON"

echo "=== running harness (mode=$RUN_LABEL) ==="
psdirect -Action exec -TimeoutSeconds 360 -Command \
    "& '$GUEST_SCRIPTS\\run-evidence.ps1' -RecipePath '$GUEST_SCRIPTS\\recipe.json' -OutputDir '$GUEST_OUT' $FAIL_FLAG"

RUN_DIR=$(psdirect -Action exec -Command "$RUN_SELECT")
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
LOCAL_DIR="/tmp/opencode/oracle-run-${RUN_LABEL}-$STAMP"
mkdir -p "$LOCAL_DIR/scripts" "$LOCAL_DIR/orchestrator"
# Pulling a directory delivers its CONTENTS, matching `scp -r host:dir/. local/`.
psdirect -Action pull -RemotePath "$RUN_DIR" -LocalPath "$LOCAL_DIR" >/dev/null

if [[ ! -f "$LOCAL_DIR/manifest.raw.json" ]]; then
  echo "ERROR: retrieved run dir has no manifest.raw.json" >&2
  exit 1
fi

# Pull the deployed harness inputs (the exact scripts + recipe that ran) and the
# deploy-time record into the local run dir so finalize can verify them against
# the recorded commit.
for name in run-evidence.ps1 common.psm1 recipe.json; do
    psdirect -Action pull -RemotePath "$GUEST_SCRIPTS\\$name" \
        -LocalPath "$LOCAL_DIR/scripts" >/dev/null
done
psdirect -Action pull -RemotePath "$GUEST_RUN_ROOT\\harness-inputs.json" \
    -LocalPath "$LOCAL_DIR" >/dev/null

# The orchestrator and the transport run on the control host (never deployed to
# the guest), so copy the exact files that were hashed at deploy time.
cp "$SCRIPT_DIR/run-windows-oracle.sh" "$LOCAL_DIR/orchestrator/run-windows-oracle.sh"
cp "$SCRIPT_DIR/psdirect.ps1" "$LOCAL_DIR/orchestrator/psdirect.ps1"

echo "=== retrieved files ==="
find "$LOCAL_DIR" -type f | sort

echo "LOCAL_RUN_DIR=$LOCAL_DIR"
echo "NEXT: uv run python scripts/windows-oracle/finalize_oracle_run.py $LOCAL_DIR --repo-root $REPO_ROOT"
