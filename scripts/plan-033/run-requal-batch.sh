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
#
# Each lane runs under a wall-clock budget (the LANES table; override all with
# GPO_STUDIO_LANE_BUDGET_SECONDS). A lane that exceeds it has its whole process
# tree killed -- acb, the runner, every pwsh beneath it, and the finalizer --
# and is recorded with exit_status 124 and timed_out true; the batch moves on
# to the next lane. Whenever a lane ends -- on its own, with any status, or at
# its budget -- anything it left running (detached or not) is killed before the
# next lane starts, and the count is recorded as processes_killed. Nothing is
# retried; a lane's own exit status is recorded as it was.
#
# Lanes run inside systemd user scopes as a second containment layer (the
# batch refuses to start without one). If a lane's supervisor dies or its
# scope still holds processes afterwards, the driver kills the scope, records
# containment_lost with exit_status 125, and STOPS the batch (exit 4). A lane
# whose supervisor is stopped by a signal is recorded cancelled, with 128 + the
# signal, and also stops the batch (exit 5).
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

# name | runner | budget | environment (space-separated KEY=VALUE, expanded at run time)
#
# budget: the lane's wall-clock limit in seconds, covering the runner AND its
# finalizer. It is a backstop, not a schedule: every transport call inside a
# lane is bounded by psdirect.ps1 already, so a lane reaches its budget only
# when something no inner bound covers has wedged (a builder, a finalizer, a
# wait loop). Each is at least the sum of the runner's explicit guest
# -TimeoutSeconds plus half an hour, rounded up -- generous by design: the
# longest lane of the last batch took 190 s, and report-parity alone allows its
# guest work 3600 s. GPO_STUDIO_LANE_BUDGET_SECONDS overrides every selected lane.
LANES=(
    "wp0|run-windows-oracle.sh|2400|GPO_STUDIO_LAB_GUEST=$MEMBER"
    "wp1b|run-wp1b-oracle.sh|3600|GPO_STUDIO_LAB_GUEST=$MEMBER"
    "wp2|run-wp2-oracle.sh|2400|GPO_STUDIO_LAB_GUEST=$MEMBER"
    "wp3-member|run-wp3-oracle.sh|2400|GPO_STUDIO_LAB_GUEST=$MEMBER GPO_STUDIO_WP3_KERBEROS=0"
    "wp3-dc|run-wp3-oracle.sh|2400|GPO_STUDIO_LAB_GUEST=$DC GPO_STUDIO_WP3_KERBEROS=1"
    "object-security|run-object-security-oracle.sh|2400|GPO_STUDIO_LAB_GUEST=$MEMBER"
    "scripts-metadata|run-scripts-backup-oracle.sh|2400|GPO_STUDIO_LAB_GUEST=$MEMBER"
    "publication|run-publication-oracle.sh|2400|GPO_STUDIO_LAB_GUEST=$MEMBER"
    # Plan 034 lanes banked after the first batch (BANKED_AFTER_THE_BATCH): each
    # runs on the member server alone.
    "lifecycle|run-lifecycle-oracle.sh|3600|GPO_STUDIO_LAB_GUEST=$MEMBER"
    "report-parity|run-report-parity-oracle.sh|7200|GPO_STUDIO_LAB_GUEST=$MEMBER"
    "firewall|run-firewall-oracle.sh|3600|GPO_STUDIO_LAB_GUEST=$MEMBER"
    "fdeploy|run-fdeploy-oracle.sh|3600|GPO_STUDIO_LAB_GUEST=$MEMBER"
    "endpoint|run-endpoint-oracle.sh|9000|GPO_STUDIO_LAB_AUTHOR_GUEST=$MEMBER GPO_STUDIO_LAB_ENDPOINT_GUEST=$CLIENT"
    "lsdou-precedence|run-rsop-oracle.sh|10800|GPO_STUDIO_RSOP_SCENARIO=lsdou-precedence GPO_STUDIO_LAB_AUTHOR_GUEST=$MEMBER GPO_STUDIO_LAB_ENDPOINT_GUEST=$CLIENT"
    "disabled-block-enforced|run-rsop-oracle.sh|10800|GPO_STUDIO_RSOP_SCENARIO=disabled-block-enforced GPO_STUDIO_LAB_AUTHOR_GUEST=$MEMBER GPO_STUDIO_LAB_ENDPOINT_GUEST=$CLIENT"
    "wmi-filtering|run-rsop-oracle.sh|10800|GPO_STUDIO_RSOP_SCENARIO=wmi-filtering GPO_STUDIO_LAB_AUTHOR_GUEST=$MEMBER GPO_STUDIO_LAB_ENDPOINT_GUEST=$CLIENT"
    "wmi-filtering-error|run-rsop-oracle.sh|10800|GPO_STUDIO_RSOP_SCENARIO=wmi-filtering-error GPO_STUDIO_LAB_AUTHOR_GUEST=$MEMBER GPO_STUDIO_LAB_ENDPOINT_GUEST=$CLIENT"
    "computer-security-filtering|run-rsop-oracle.sh|10800|GPO_STUDIO_RSOP_SCENARIO=computer-security-filtering GPO_STUDIO_LAB_AUTHOR_GUEST=$MEMBER GPO_STUDIO_LAB_ENDPOINT_GUEST=$CLIENT"
    "computer-security-filtering-deny-read|run-rsop-oracle.sh|10800|GPO_STUDIO_RSOP_SCENARIO=computer-security-filtering-deny-read GPO_STUDIO_RSOP_USER=$RSOP_USER GPO_STUDIO_LAB_AUTHOR_GUEST=$MEMBER GPO_STUDIO_LAB_ENDPOINT_GUEST=$CLIENT"
    "loopback-merge|run-rsop-user-oracle.sh|12600|GPO_STUDIO_RSOP_SCENARIO=loopback-merge GPO_STUDIO_RSOP_USER=$RSOP_USER GPO_STUDIO_LAB_AUTHOR_GUEST=$MEMBER GPO_STUDIO_LAB_ENDPOINT_GUEST=$CLIENT"
    "loopback-replace|run-rsop-user-oracle.sh|12600|GPO_STUDIO_RSOP_SCENARIO=loopback-replace GPO_STUDIO_RSOP_USER=$RSOP_USER GPO_STUDIO_LAB_AUTHOR_GUEST=$MEMBER GPO_STUDIO_LAB_ENDPOINT_GUEST=$CLIENT"
    "user-side-disabled|run-rsop-user-oracle.sh|12600|GPO_STUDIO_RSOP_SCENARIO=user-side-disabled GPO_STUDIO_RSOP_USER=$RSOP_USER GPO_STUDIO_LAB_AUTHOR_GUEST=$MEMBER GPO_STUDIO_LAB_ENDPOINT_GUEST=$CLIENT"
    "user-security-filtering|run-rsop-user-oracle.sh|12600|GPO_STUDIO_RSOP_SCENARIO=user-security-filtering GPO_STUDIO_RSOP_USER=$RSOP_USER GPO_STUDIO_LAB_AUTHOR_GUEST=$MEMBER GPO_STUDIO_LAB_ENDPOINT_GUEST=$CLIENT"
    "user-security-filtering-deny|run-rsop-user-oracle.sh|12600|GPO_STUDIO_RSOP_SCENARIO=user-security-filtering-deny GPO_STUDIO_RSOP_USER=$RSOP_USER GPO_STUDIO_LAB_AUTHOR_GUEST=$MEMBER GPO_STUDIO_LAB_ENDPOINT_GUEST=$CLIENT"
    "user-security-filtering-read-deny|run-rsop-user-oracle.sh|12600|GPO_STUDIO_RSOP_SCENARIO=user-security-filtering-read-deny GPO_STUDIO_RSOP_USER=$RSOP_USER GPO_STUDIO_LAB_AUTHOR_GUEST=$MEMBER GPO_STUDIO_LAB_ENDPOINT_GUEST=$CLIENT"
    # Last on purpose: the one lane that reboots the client mid-run (WI-069).
    "computer-security-filtering-group-deny|run-rsop-oracle.sh|10800|GPO_STUDIO_RSOP_SCENARIO=computer-security-filtering-group-deny GPO_STUDIO_LAB_AUTHOR_GUEST=$MEMBER GPO_STUDIO_LAB_ENDPOINT_GUEST=$CLIENT"
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

for knob in GPO_STUDIO_LANE_BUDGET_SECONDS GPO_STUDIO_LANE_KILL_GRACE_SECONDS; do
    if [[ -n "${!knob:-}" && ! "${!knob}" =~ ^[1-9][0-9]*$ ]]; then
        echo "refusing: $knob must be a positive integer" >&2
        exit 2
    fi
done
# Seconds between SIGTERM and SIGKILL when a lane ends with processes left.
LANE_KILL_GRACE="${GPO_STUDIO_LANE_KILL_GRACE_SECONDS:-15}"
SUPERVISOR="$SCRIPT_DIR/lane-supervisor.py"
# Recorded when the supervisor's own report is missing or the lane's scope
# still held processes after it: containment can no longer be vouched for.
CONTAINMENT_LOST_STATUS=125
# Batch exit statuses beyond 1/2/3: a lane's containment was lost (4), or a
# lane was cancelled (5). Either stops the batch at once.
BATCH_CONTAINMENT_LOST=4
BATCH_CANCELLED=5

# The second containment layer: every lane runs in its own systemd user scope
# (a cgroup), which nothing the lane starts can leave. The supervisor is the
# first layer and does the orderly work; the scope is what lets this driver
# find and kill the lane anyway if the supervisor itself dies. Without a user
# manager there is no second layer, and the batch refuses to start rather
# than run lanes it could lose track of.
if ! systemd-run --user --scope --quiet --collect \
        --unit="gpo-studio-lane-probe-$$" -- true >/dev/null 2>&1; then
    echo "refusing: lane containment needs 'systemd-run --user --scope' (a systemd user manager)" >&2
    exit 2
fi

# The pids left in a scope, one per line (nothing once the scope is gone).
scope_pids() {
    local cgroup
    cgroup="$(systemctl --user show -p ControlGroup --value "$1.scope" 2>/dev/null)" || return 0
    [[ -n "$cgroup" && -r "/sys/fs/cgroup$cgroup/cgroup.procs" ]] || return 0
    cat "/sys/fs/cgroup$cgroup/cgroup.procs"
}

# Kill everything in a scope and wait for it to empty. Returns non-zero if it
# would not empty within 15 s.
clear_scope() {
    local unit=$1 _
    systemctl --user kill --signal=SIGKILL "$unit.scope" >/dev/null 2>&1
    for _ in $(seq 1 150); do
        [[ -z "$(scope_pids "$unit")" ]] && break
        sleep 0.1
    done
    systemctl --user stop "$unit.scope" >/dev/null 2>&1
    [[ -z "$(scope_pids "$unit")" ]]
}

# run_bounded <deadline-epoch> <log> <command...>
# Run the command under lane-supervisor.py, inside its own systemd user scope:
# a new session, the log appended, and -- whether the command exits by itself
# or the deadline passes -- every process it started, detached or not, TERM'd,
# KILL'd after the grace, and reaped before this returns. Returns the
# command's own status (124 when the deadline killed it). Sets WATCHDOG_FIRED
# when the deadline did the killing (a lane may exit 124 by itself; psdirect
# does on its own deadline), CANCELLED when the supervisor was stopped by a
# signal, CONTAINMENT_LOST when the supervisor gave no report or its scope
# still held processes afterwards (both after killing the scope), and adds to
# PROCESSES_KILLED every process cleanup had to signal.
run_bounded() {
    local deadline=$1 log=$2 report status unit leftovers parsed valid
    shift 2
    report="$(mktemp)"
    unit="gpo-studio-lane-$(date +%s%N)-$$"
    systemd-run --user --scope --quiet --collect --unit="$unit" -- \
        python3 "$SUPERVISOR" --deadline "$deadline" --grace "$LANE_KILL_GRACE" \
        --log "$log" --report "$report" -- "$@"
    status=$?
    leftovers="$(scope_pids "$unit" | wc -l)"
    # The report is believed only if it is exactly what the supervisor writes:
    # a JSON object with these four fields and these types. Anything else -- a
    # missing, truncated or garbled report -- means containment is unknown.
    parsed="$(python3 -c '
import json, sys
try:
    r = json.load(open(sys.argv[1], encoding="utf-8"))
except (OSError, ValueError):
    sys.exit(1)
ok = (
    isinstance(r, dict) and set(r) == {"status", "timed_out", "cancelled", "killed"}
    and type(r["status"]) is int and 0 <= r["status"] <= 255
    and type(r["timed_out"]) is bool and type(r["cancelled"]) is bool
    and type(r["killed"]) is int and r["killed"] >= 0
)
if not ok:
    sys.exit(1)
print(r["status"], int(r["timed_out"]), int(r["cancelled"]), r["killed"])' "$report" 2>/dev/null)"
    valid=$?
    if [[ $valid -ne 0 || $leftovers -ne 0 ]]; then
        # The supervisor died (or left processes behind): nothing vouches for
        # this lane's containment any more. Kill the whole scope, record it,
        # and let the caller stop the batch.
        CONTAINMENT_LOST=1
        echo "=== watchdog: CONTAINMENT LOST (supervisor status $status, report $([[ $valid -eq 0 ]] && echo valid || echo missing or invalid), $leftovers process(es) left in scope $unit); killing the scope" >>"$log"
        if clear_scope "$unit"; then
            echo "=== watchdog: scope $unit is empty" >>"$log"
        else
            echo "=== watchdog: scope $unit could NOT be emptied: $(scope_pids "$unit" | tr '\n' ' ')" >>"$log"
        fi
        PROCESSES_KILLED=$((PROCESSES_KILLED + leftovers))
        rm -f "$report" "$report.partial"
        return "$CONTAINMENT_LOST_STATUS"
    fi
    read -r status sup_timed_out sup_cancelled sup_killed <<<"$parsed"
    rm -f "$report" "$report.partial"
    [[ $sup_timed_out -eq 1 ]] && WATCHDOG_FIRED=1
    [[ $sup_cancelled -eq 1 ]] && CANCELLED=1
    PROCESSES_KILLED=$((PROCESSES_KILLED + sup_killed))
    return "$status"
}

for row in "${LANES[@]}"; do
    IFS='|' read -r name runner budget envs <<<"$row"
    selected "$name" || continue
    budget="${GPO_STUDIO_LANE_BUDGET_SECONDS:-$budget}"
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
    echo "=== $name ($runner) started $started, budget ${budget}s"
    # One deadline for the runner and its finalizer together.
    deadline=$(( $(date +%s) + budget ))
    : >"$log"
    WATCHDOG_FIRED=0
    CANCELLED=0
    CONTAINMENT_LOST=0
    PROCESSES_KILLED=0
    set +e
    # shellcheck disable=SC2086 # the lane's KEY=VALUE pairs split on purpose
    # The lane's environment is set INSIDE the acb exec, so what the runner
    # sees is exactly this table plus acb's credential variables.
    # `env -u` first: a computer-scope lane must not inherit the principal the
    # user lanes need, and its candidate builder refuses one it was handed.
    # A lane is never retried: it authors policy, and a second run is the
    # operator's decision.
    run_bounded "$deadline" "$log" \
        acb exec cred:lab-hyperv-control cred:lab-guest-bootstrap -- \
        env -u GPO_STUDIO_RSOP_USER TMPDIR="$TMPDIR" $envs \
        bash "scripts/windows-oracle/$runner"
    status=$?
    # run-windows-oracle.sh (WP-0) stops at the run directory and prints the
    # finalizer command instead of running it; every other runner finalizes
    # itself. Run exactly the command it printed, so the record is the same
    # as a by-hand run.
    next="$(sed -n 's/^NEXT: //p' "$log" | tail -1)"
    if [[ $status -eq 0 && $CANCELLED -eq 0 && $CONTAINMENT_LOST -eq 0 && -n "$next" ]]; then
        run_bounded "$deadline" "$log" bash -c "$next"
        status=$?
    fi
    set -e
    timed_out=$WATCHDOG_FIRED
    completed="$(date -u +%Y-%m-%dT%H:%M:%S.%6N+00:00)"
    run_dir="$(sed -n 's/^LOCAL_RUN_DIR=//p' "$log" | tail -1)"
    python3 - "$PROGRESS" "$name" "$runner" "$COMMIT" "$started" "$completed" "$status" "$run_dir" \
        "$budget" "$timed_out" "$PROCESSES_KILLED" "$CANCELLED" "$CONTAINMENT_LOST" <<'PY'
import json, sys
(path, name, runner, commit, started, completed, status, run_dir, budget, timed_out, strays,
 cancelled, lost) = sys.argv[1:]
with open(path, "a", encoding="utf-8") as fh:
    fh.write(json.dumps({
        "name": name, "runner": runner, "commit": commit,
        "started_utc": started, "completed_utc": completed,
        "exit_status": int(status), "local_run_dir": run_dir or None,
        "budget_seconds": int(budget), "timed_out": timed_out == "1",
        "processes_killed": int(strays),
        "cancelled": cancelled == "1", "containment_lost": lost == "1",
    }) + "\n")
PY
    if [[ $timed_out -eq 1 ]]; then
        echo "=== $name TIMED OUT after its ${budget}s budget; its process tree was killed"
    fi
    echo "=== $name exit=$status run_dir=${run_dir:-<none>}"
    ran=$((ran + 1))
    [[ $status -eq 0 ]] || failures=$((failures + 1))
    # Neither of these is a lane result to move past: stop the batch here.
    if [[ $CONTAINMENT_LOST -eq 1 ]]; then
        echo "batch STOPPED: $name's containment was lost (see its log); no further lane runs" >&2
        exit "$BATCH_CONTAINMENT_LOST"
    fi
    if [[ $CANCELLED -eq 1 ]]; then
        echo "batch STOPPED: $name was cancelled; no further lane runs" >&2
        exit "$BATCH_CANCELLED"
    fi
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
