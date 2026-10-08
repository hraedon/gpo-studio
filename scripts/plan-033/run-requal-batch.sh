#!/usr/bin/env bash
# Drive an estate requalification batch: every lane, in order, on ONE frozen
# commit, with each run's wall-clock window, exit status and local run
# directory appended to a progress log the batch manifest is built from.
#
# The batches before this one were driven by hand from a Windows controller,
# and nothing recorded what was invoked beyond the passing run ids. This
# driver is that record: the lane table below is the batch's definition, and
# the progress log is what happened. It does not finalize, bank or tag
# anything itself -- each runner's own finalizer does that, exactly as when a
# lane is started by hand.
#
# Usage:
#   GPO_STUDIO_LAB_HOST=<hyper-v host> GPO_STUDIO_RSOP_USER=<logged-on principal> \
#   ACB_VAULT_ENV=~/.claude/evidence-lab.env \
#     bash scripts/plan-033/run-requal-batch.sh <batch-dir> [lane ...]
#
# With no lane names, every lane in LANES runs. The tree must be clean: a
# verdict minted from a dirty tree is refused by its finalizer anyway, and
# failing here costs no estate time.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
: "${GPO_STUDIO_LAB_HOST:?GPO_STUDIO_LAB_HOST not set}"
BATCH_DIR="${1:?usage: run-requal-batch.sh <batch-dir> [lane ...]}"
shift

MEMBER="${GPO_STUDIO_LAB_MEMBER:-LabMS01}"
DC="${GPO_STUDIO_LAB_DC:-LabDC01}"
CLIENT="${GPO_STUDIO_LAB_CLIENT:-LabCL01}"
RSOP_USER="${GPO_STUDIO_RSOP_USER:-}"

# name | runner | environment (space-separated KEY=VALUE, expanded at run time)
LANES=(
    "wp0|run-windows-oracle.sh|GPO_STUDIO_LAB_GUEST=$MEMBER"
    "wp1b|run-wp1b-oracle.sh|GPO_STUDIO_LAB_GUEST=$MEMBER"
    "wp2|run-wp2-oracle.sh|GPO_STUDIO_LAB_GUEST=$MEMBER"
    "wp3-member|run-wp3-oracle.sh|GPO_STUDIO_LAB_GUEST=$MEMBER GPO_STUDIO_WP3_KERBEROS=0"
    "wp3-dc|run-wp3-oracle.sh|GPO_STUDIO_LAB_GUEST=$DC GPO_STUDIO_WP3_KERBEROS=1"
    "object-security|run-object-security-oracle.sh|GPO_STUDIO_LAB_GUEST=$MEMBER"
    "scripts-metadata|run-scripts-backup-oracle.sh|GPO_STUDIO_LAB_GUEST=$MEMBER"
    "publication|run-publication-oracle.sh|GPO_STUDIO_LAB_GUEST=$MEMBER"
    # Plan 034 lanes banked after the first batch (BANKED_AFTER_THE_BATCH): each
    # runs on the member server alone.
    "lifecycle|run-lifecycle-oracle.sh|GPO_STUDIO_LAB_GUEST=$MEMBER"
    "report-parity|run-report-parity-oracle.sh|GPO_STUDIO_LAB_GUEST=$MEMBER"
    "firewall|run-firewall-oracle.sh|GPO_STUDIO_LAB_GUEST=$MEMBER"
    "fdeploy|run-fdeploy-oracle.sh|GPO_STUDIO_LAB_GUEST=$MEMBER"
    "endpoint|run-endpoint-oracle.sh|GPO_STUDIO_LAB_AUTHOR_GUEST=$MEMBER GPO_STUDIO_LAB_ENDPOINT_GUEST=$CLIENT"
    "lsdou-precedence|run-rsop-oracle.sh|GPO_STUDIO_RSOP_SCENARIO=lsdou-precedence GPO_STUDIO_LAB_AUTHOR_GUEST=$MEMBER GPO_STUDIO_LAB_ENDPOINT_GUEST=$CLIENT"
    "disabled-block-enforced|run-rsop-oracle.sh|GPO_STUDIO_RSOP_SCENARIO=disabled-block-enforced GPO_STUDIO_LAB_AUTHOR_GUEST=$MEMBER GPO_STUDIO_LAB_ENDPOINT_GUEST=$CLIENT"
    "wmi-filtering|run-rsop-oracle.sh|GPO_STUDIO_RSOP_SCENARIO=wmi-filtering GPO_STUDIO_LAB_AUTHOR_GUEST=$MEMBER GPO_STUDIO_LAB_ENDPOINT_GUEST=$CLIENT"
    "wmi-filtering-error|run-rsop-oracle.sh|GPO_STUDIO_RSOP_SCENARIO=wmi-filtering-error GPO_STUDIO_LAB_AUTHOR_GUEST=$MEMBER GPO_STUDIO_LAB_ENDPOINT_GUEST=$CLIENT"
    "computer-security-filtering|run-rsop-oracle.sh|GPO_STUDIO_RSOP_SCENARIO=computer-security-filtering GPO_STUDIO_LAB_AUTHOR_GUEST=$MEMBER GPO_STUDIO_LAB_ENDPOINT_GUEST=$CLIENT"
    "computer-security-filtering-deny-read|run-rsop-oracle.sh|GPO_STUDIO_RSOP_SCENARIO=computer-security-filtering-deny-read GPO_STUDIO_RSOP_USER=$RSOP_USER GPO_STUDIO_LAB_AUTHOR_GUEST=$MEMBER GPO_STUDIO_LAB_ENDPOINT_GUEST=$CLIENT"
    "loopback-merge|run-rsop-user-oracle.sh|GPO_STUDIO_RSOP_SCENARIO=loopback-merge GPO_STUDIO_RSOP_USER=$RSOP_USER GPO_STUDIO_LAB_AUTHOR_GUEST=$MEMBER GPO_STUDIO_LAB_ENDPOINT_GUEST=$CLIENT"
    "loopback-replace|run-rsop-user-oracle.sh|GPO_STUDIO_RSOP_SCENARIO=loopback-replace GPO_STUDIO_RSOP_USER=$RSOP_USER GPO_STUDIO_LAB_AUTHOR_GUEST=$MEMBER GPO_STUDIO_LAB_ENDPOINT_GUEST=$CLIENT"
    "user-side-disabled|run-rsop-user-oracle.sh|GPO_STUDIO_RSOP_SCENARIO=user-side-disabled GPO_STUDIO_RSOP_USER=$RSOP_USER GPO_STUDIO_LAB_AUTHOR_GUEST=$MEMBER GPO_STUDIO_LAB_ENDPOINT_GUEST=$CLIENT"
    "user-security-filtering|run-rsop-user-oracle.sh|GPO_STUDIO_RSOP_SCENARIO=user-security-filtering GPO_STUDIO_RSOP_USER=$RSOP_USER GPO_STUDIO_LAB_AUTHOR_GUEST=$MEMBER GPO_STUDIO_LAB_ENDPOINT_GUEST=$CLIENT"
    "user-security-filtering-deny|run-rsop-user-oracle.sh|GPO_STUDIO_RSOP_SCENARIO=user-security-filtering-deny GPO_STUDIO_RSOP_USER=$RSOP_USER GPO_STUDIO_LAB_AUTHOR_GUEST=$MEMBER GPO_STUDIO_LAB_ENDPOINT_GUEST=$CLIENT"
    "user-security-filtering-read-deny|run-rsop-user-oracle.sh|GPO_STUDIO_RSOP_SCENARIO=user-security-filtering-read-deny GPO_STUDIO_RSOP_USER=$RSOP_USER GPO_STUDIO_LAB_AUTHOR_GUEST=$MEMBER GPO_STUDIO_LAB_ENDPOINT_GUEST=$CLIENT"
    # Last on purpose: the one lane that reboots the client mid-run (WI-069).
    "computer-security-filtering-group-deny|run-rsop-oracle.sh|GPO_STUDIO_RSOP_SCENARIO=computer-security-filtering-group-deny GPO_STUDIO_LAB_AUTHOR_GUEST=$MEMBER GPO_STUDIO_LAB_ENDPOINT_GUEST=$CLIENT"
)

cd "$REPO_ROOT"
# Resolve WITHOUT creating: the refusals below must leave nothing behind, and
# an in-repo <batch-dir> created first would litter the tree it refuses to
# dirty (review N8). `realpath -m` resolves symlinks in the existing prefix.
batch_real="$(realpath -m -- "$BATCH_DIR")"
repo_real="$(pwd -P)"
if [[ "$batch_real/" == "$repo_real/"* ]]; then
    # The per-lane clean-tree guard would see the batch's own logs as a tree
    # move and refuse the second lane; bank packs into the repo afterwards.
    echo "refusing: <batch-dir> must be outside the repository ($batch_real)" >&2
    exit 2
fi
if [[ -n "$(git status --porcelain)" ]]; then
    echo "refusing: the tree is dirty; a batch runs on one clean commit" >&2
    exit 2
fi
COMMIT="$(git rev-parse HEAD)"
mkdir -p "$BATCH_DIR" "$BATCH_DIR/logs"
PROGRESS="$BATCH_DIR/progress.jsonl"
export TMPDIR="$BATCH_DIR/tmp"
mkdir -p "$TMPDIR"

wanted=("$@")
known=()
for row in "${LANES[@]}"; do known+=("${row%%|*}"); done
for w in "${wanted[@]}"; do
    if [[ " ${known[*]} " != *" $w "* ]]; then
        echo "refusing: unknown lane '$w' (known: ${known[*]})" >&2
        exit 2
    fi
done
failures=0
ran=0
selected() {
    [[ ${#wanted[@]} -eq 0 ]] && return 0
    local w
    for w in "${wanted[@]}"; do [[ "$w" == "$1" ]] && return 0; done
    return 1
}

for row in "${LANES[@]}"; do
    IFS='|' read -r name runner envs <<<"$row"
    selected "$name" || continue
    if [[ "$envs" == *"GPO_STUDIO_RSOP_USER="* && -z "$RSOP_USER" ]]; then
        echo "refusing $name: GPO_STUDIO_RSOP_USER is required for this lane" >&2
        exit 2
    fi
    if [[ "$(git rev-parse HEAD)" != "$COMMIT" || -n "$(git status --porcelain)" ]]; then
        echo "refusing $name: the tree moved during the batch" >&2
        exit 2
    fi
    log="$BATCH_DIR/logs/$name.log"
    started="$(date -u +%Y-%m-%dT%H:%M:%S.%6N+00:00)"
    echo "=== $name ($runner) started $started"
    set +e
    # shellcheck disable=SC2086 # the lane's KEY=VALUE pairs split on purpose
    # The lane's environment is set INSIDE the acb exec, so what the runner
    # sees is exactly this table plus acb's credential variables.
    # `env -u` first: a computer-scope lane must not inherit the principal the
    # user lanes need, and its candidate builder refuses one it was handed.
    acb exec cred:lab-hyperv-control cred:lab-guest-bootstrap -- \
        env -u GPO_STUDIO_RSOP_USER TMPDIR="$TMPDIR" $envs \
        bash "scripts/windows-oracle/$runner" >"$log" 2>&1
    status=$?
    # run-windows-oracle.sh (WP-0) stops at the run directory and prints the
    # finalizer command instead of running it; every other runner finalizes
    # itself. Run exactly the command it printed, so the record is the same
    # as a by-hand run.
    next="$(sed -n 's/^NEXT: //p' "$log" | tail -1)"
    if [[ $status -eq 0 && -n "$next" ]]; then
        set +e
        bash -c "$next" >>"$log" 2>&1
        status=$?
        set -e
    fi
    set -e
    completed="$(date -u +%Y-%m-%dT%H:%M:%S.%6N+00:00)"
    run_dir="$(sed -n 's/^LOCAL_RUN_DIR=//p' "$log" | tail -1)"
    python3 - "$PROGRESS" "$name" "$runner" "$COMMIT" "$started" "$completed" "$status" "$run_dir" <<'PY'
import json, sys
path, name, runner, commit, started, completed, status, run_dir = sys.argv[1:]
with open(path, "a", encoding="utf-8") as fh:
    fh.write(json.dumps({
        "name": name, "runner": runner, "commit": commit,
        "started_utc": started, "completed_utc": completed,
        "exit_status": int(status), "local_run_dir": run_dir or None,
    }) + "\n")
PY
    echo "=== $name exit=$status run_dir=${run_dir:-<none>}"
    ran=$((ran + 1))
    [[ $status -eq 0 ]] || failures=$((failures + 1))
done

# A batch that ran nothing, or in which any lane failed, is not a success --
# the progress log says which, and the exit status must not say otherwise.
if [[ $ran -eq 0 ]]; then
    echo "batch ran no lanes" >&2
    exit 3
fi
if [[ $failures -ne 0 ]]; then
    echo "batch finished with $failures failed lane(s) of $ran" >&2
    exit 1
fi
