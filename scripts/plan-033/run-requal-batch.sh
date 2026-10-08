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
# (Tests alone add a leading `--test-scope-tool <path>`; see TEST SEAM below.)
#
# <batch-dir> must be outside the repository, given as a canonical absolute
# path (no '.', '..' or symlink components), and PRIVATE: this user's, mode
# 0700 (created so if missing), with every ancestor owned by root or this user
# and writable by nobody else unless sticky. A shared batch directory is not
# supported. Stopping the driver with TERM, INT or HUP cancels the lane in
# flight -- its process tree killed and its scope verified empty -- records it
# cancelled, and exits 5.
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
# batch refuses to start without one). If a lane's supervisor dies, its report
# is invalid, or its scope is not verifiably empty afterwards (still holding
# processes, or unreadable after retries), the driver kills the scope through
# its cgroup, verifies it, records containment_lost with exit_status 125, and
# STOPS the batch (exit 4). If a scope cannot be created, the lane never
# starts, nothing is cleaned up by name, scope_failed is recorded with 125,
# and the batch stops (exit 4). A lane
# whose supervisor is stopped by a signal is recorded cancelled, with 128 + the
# signal, and also stops the batch (exit 5).
set -euo pipefail
# Everything this driver writes -- progress, logs, reports, ownership proofs,
# and whatever its lanes write under TMPDIR -- is private to this user,
# whatever umask it was started with.
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
: "${GPO_STUDIO_LAB_HOST:?GPO_STUDIO_LAB_HOST not set}"
# TEST SEAM selection is a command-line flag, so an inherited environment can
# never select it on its own (it also needs GPO_STUDIO_REQUAL_ALLOW_TEST_SCOPE=1,
# checked below). The former environment variable is refused, not ignored.
TEST_SCOPE_TOOL=""
if [[ "${1:-}" == "--test-scope-tool" ]]; then
    TEST_SCOPE_TOOL="${2:?--test-scope-tool needs a path}"
    shift 2
fi
if [[ -n "${GPO_STUDIO_REQUAL_TEST_SCOPE_TOOL:-}" ]]; then
    echo "refusing: GPO_STUDIO_REQUAL_TEST_SCOPE_TOOL is no longer honoured; the test seam is a command-line flag" >&2
    exit 2
fi
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
if [[ "$BATCH_DIR" != /* ]]; then
    echo "refusing: <batch-dir> must be an absolute path ($BATCH_DIR)" >&2
    exit 2
fi
if ! batch_real="$(realpath -m -- "$BATCH_DIR" 2>&1)"; then
    echo "refusing: <batch-dir> $BATCH_DIR cannot be resolved ($batch_real)" >&2
    exit 2
fi
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
# The batch directory holds every lane's ownership proof, report, log and
# progress row. It must be PRIVATE: a directory owned by this user with mode
# 0700 (created so if missing; anything else is refused, not repaired), and
# its path CANONICAL (absolute; no '.', '..', '//', trailing '/' or symlink
# component), and every ancestor up to / owned by root or this user and not
# writable by others unless sticky -- the user's private group counts as
# others -- so nobody else can rename or replace any directory on the way. With that, no
# other user can create or alter anything inside it; progress.jsonl is still
# reserved exclusively at 0600 before any lane runs, and checked on every
# append.
me="$(id -u)"
refuse_unsafe() {
    echo "refusing: $1" >&2
    exit 2
}
check_private() {  # check_private <path> <dir|file>: ours, not a symlink, writable by nobody else
    local path=$1 kind=$2 mode owner
    [[ -L "$path" ]] && refuse_unsafe "$path is a symlink"
    if [[ $kind == dir ]]; then
        [[ -d "$path" ]] || refuse_unsafe "$path is not a directory"
    else
        [[ -f "$path" ]] || refuse_unsafe "$path is not a regular file"
    fi
    read -r mode owner < <(stat -c '%a %u' -- "$path")
    [[ "$owner" == "$me" ]] || refuse_unsafe "$path is not owned by this user"
    (( 8#$mode & 8#022 )) && refuse_unsafe "$path is writable by others (mode $mode)"
    return 0
}
# check_batch_dir <before|after>: the batch directory's path must already be
# CANONICAL -- absolute, with no `.`, `..`, empty or trailing components and
# no symlink anywhere along it -- so the one path checked is the one used.
# Every existing ancestor must be owned by root or this user and not writable
# by others unless sticky. Before creation the leaf (and any missing parents)
# may be absent; after it, the batch directory must be this user's with mode
# 0700 exactly.
check_batch_dir() {
    python3 - "$BATCH_DIR" "$1" <<'PY'
import os, stat, sys

uid = os.getuid()
path, phase = sys.argv[1], sys.argv[2]


def refuse(where, why):
    print(f"refusing: {where} {why}", file=sys.stderr)
    sys.exit(2)


if not path.startswith("/"):
    refuse(path, "(the batch directory) must be an absolute path")
parts = path.split("/")[1:]
if path == "/" or any(part in ("", ".", "..") for part in parts):
    refuse(path, "(the batch directory) must be canonical: no '.', '..', '//' or trailing '/'")

prefix = ""
for i, part in enumerate(parts):
    prefix += "/" + part
    leaf = i == len(parts) - 1
    try:
        st = os.lstat(prefix)
    except FileNotFoundError:
        if phase == "after":
            refuse(prefix, "vanished while the batch directory was being set up")
        break  # the rest is created by this driver, 0700 and ours
    except OSError as error:
        refuse(prefix, f"cannot be examined: {error}")
    if stat.S_ISLNK(st.st_mode):
        refuse(prefix, "is a symlink; the batch directory's path must be canonical")
    if not stat.S_ISDIR(st.st_mode):
        refuse(prefix, "is not a directory")
    if leaf:
        mode = stat.S_IMODE(st.st_mode)
        if st.st_uid != uid or mode != 0o700:
            refuse(prefix, f"(the batch directory) must be this user's, with mode 0700 (it is {mode:04o})")
        continue
    if st.st_uid not in (0, uid):
        refuse(prefix, "(an ancestor of the batch directory) is owned by another user")
    if st.st_mode & 0o022 and not st.st_mode & stat.S_ISVTX:
        refuse(
            prefix,
            f"(an ancestor of the batch directory) is writable by others and not sticky "
            f"(mode {stat.S_IMODE(st.st_mode):04o}); fix: chmod g-w,o-w {prefix}, or choose "
            "a batch directory under a private parent",
        )
for directory in ("/",):
    st = os.lstat(directory)
    if st.st_uid not in (0, uid) or (st.st_mode & 0o022 and not st.st_mode & stat.S_ISVTX):
        refuse(directory, "(the root directory) is writable by others")
if phase == "after" and os.path.realpath(path) != path:
    refuse(path, "(the batch directory) does not resolve to itself")
PY
}
check_batch_dir before || exit 2
mkdir -p "$BATCH_DIR"
check_batch_dir after || exit 2
mkdir -p "$BATCH_DIR/logs"
check_private "$BATCH_DIR/logs" dir
PROGRESS="$BATCH_DIR/progress.jsonl"
export TMPDIR="$BATCH_DIR/tmp"
mkdir -p "$TMPDIR"
check_private "$TMPDIR" dir
for existing in "$BATCH_DIR"/logs/*; do
    [[ -e "$existing" || -L "$existing" ]] && check_private "$existing" file
done
# Reserve the progress record before any lane runs: created exclusively at
# 0600, or -- when a batch directory is reused -- an existing private file.
if [[ -e "$PROGRESS" || -L "$PROGRESS" ]]; then
    check_private "$PROGRESS" file
else
    python3 -c '
import os, sys
os.close(os.open(sys.argv[1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600))
' "$PROGRESS"
fi

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
# find and kill the lane anyway if the supervisor itself dies. lane-scope.py
# does the scope work; after creation it reads and kills through
# /sys/fs/cgroup directly, so cleanup and its verification never depend on
# the user manager answering.
#
# TEST SEAM. `--test-scope-tool <path>` (the first argument) substitutes a
# stand-in for lane-scope.py, so the containment contracts run in CI without
# a user manager. It needs GPO_STUDIO_REQUAL_ALLOW_TEST_SCOPE=1 as well -- a
# flag on the command line AND an opt-in in the environment, so neither an
# inherited environment nor a stray argument selects it alone. It is
# announced on stderr, and every progress row records test_scope_tool (true
# here); the batch-manifest gates refuse any row that is not explicitly false
# (tests/batch_provenance.py). Nothing outside tests/ may use either
# (tests/test_requal_batch_driver.py scans the repository).
SCOPE_TOOL=(python3 "$SCRIPT_DIR/lane-scope.py")
TEST_SCOPE=0
if [[ -n "$TEST_SCOPE_TOOL" ]]; then
    if [[ "${GPO_STUDIO_REQUAL_ALLOW_TEST_SCOPE:-}" != "1" ]]; then
        echo "refusing: --test-scope-tool is for tests and needs GPO_STUDIO_REQUAL_ALLOW_TEST_SCOPE=1" >&2
        exit 2
    fi
    SCOPE_TOOL=("$TEST_SCOPE_TOOL")
    TEST_SCOPE=1
    echo "WARNING: test scope tool in use ($TEST_SCOPE_TOOL); this batch is not evidence" >&2
fi
# Unit names carry a random nonce per batch, so they cannot collide with a
# unit this batch did not create -- and if creation fails anyway, nothing is
# cleaned up by name (see run_bounded).
SCOPE_NONCE="$(od -An -N8 -tx1 /dev/urandom | tr -d ' \n')"
SCOPE_SEQ=0
if ! "${SCOPE_TOOL[@]}" probe "$SCOPE_NONCE" >/dev/null 2>&1; then
    echo "refusing: lane containment needs 'systemd-run --user --scope' (a systemd user manager)" >&2
    exit 2
fi
# How many times an unanswerable cgroup query is retried before the lane's
# state is declared unknown (one second apart).
SCOPE_QUERY_ATTEMPTS=5
# Rounds of kill-then-verify before a cgroup is declared not verifiably empty.
SCOPE_CLEAR_ATTEMPTS=10
if [[ $TEST_SCOPE -eq 1 && -n "${GPO_STUDIO_REQUAL_TEST_SCOPE_ATTEMPTS:-}" ]]; then
    SCOPE_QUERY_ATTEMPTS=$GPO_STUDIO_REQUAL_TEST_SCOPE_ATTEMPTS
    SCOPE_CLEAR_ATTEMPTS=$GPO_STUDIO_REQUAL_TEST_SCOPE_ATTEMPTS
fi

# scope_state <unit> <proof>: prints "empty", "populated [pids...]" or
# "unknown". "empty" only on lane-scope's certified EMPTY (two consistent
# reads) or confirmed GONE; an UNKNOWN answer is retried, and still unknown
# after SCOPE_QUERY_ATTEMPTS it stays "unknown" -- never "empty".
scope_state() {
    local pids rc i
    for ((i = 1; i <= SCOPE_QUERY_ATTEMPTS; i++)); do
        pids="$("${SCOPE_TOOL[@]}" procs "$1" "$2" 2>/dev/null)"
        rc=$?
        case $rc in
            0 | 3) echo empty; return 0 ;;
            1) echo "populated $(tr '\n' ' ' <<<"$pids")"; return 0 ;;
        esac
        (( i < SCOPE_QUERY_ATTEMPTS )) && sleep 1
    done
    echo unknown
}

# clear_scope <unit> <proof> <log>: kill everything in the lane's cgroup and
# VERIFY it emptied. Every kill is checked; returns 0 only on verified empty.
clear_scope() {
    local unit=$1 proof=$2 log=$3 state="" i
    for ((i = 1; i <= SCOPE_CLEAR_ATTEMPTS; i++)); do
        if ! "${SCOPE_TOOL[@]}" kill "$unit" "$proof" >/dev/null 2>&1; then
            echo "=== watchdog: kill of the lane's cgroup failed (attempt $i)" >>"$log"
        fi
        sleep 1
        state="$(scope_state "$unit" "$proof")"
        [[ "$state" == empty ]] && return 0
    done
    echo "=== watchdog: the lane's cgroup could NOT be verified empty: $state" >>"$log"
    return 1
}

# Stopping the driver. TERM, INT or HUP to this process cancels the lane in
# flight. The trap only records the signal and creates the lane's CANCEL FILE,
# which the supervisor checks before it starts the lane and on every poll --
# so there is no window: a stop before a launch prevents it, a stop after the
# fork but before the supervisor is ready is seen the moment it is, and a
# stop while the lane runs makes the supervisor kill it and report a
# cancellation. run_bounded then checks the lane's scope with the usual rules
# and clears it if anything is left; the lane is recorded cancelled with
# 128 + the signal; no finalizer runs; the batch exits 5 (4 if containment
# was lost on the way). A second signal while stopping changes nothing: the
# driver never exits before the scope is verified empty or the loss of
# containment is recorded.
STOP_SIGNAL=0
ACTIVE_SUPERVISOR=""
ACTIVE_CANCEL=""
SUPERVISOR_STOP_GRACE=$((LANE_KILL_GRACE + 15))
on_stop() {
    if [[ $STOP_SIGNAL -ne 0 ]]; then
        echo "driver: already stopping (signal $1 ignored); finishing the lane's cleanup" >&2
        return 0
    fi
    STOP_SIGNAL=$1
    echo "driver: signal $1 received; cancelling the batch" >&2
    if [[ -n "$ACTIVE_CANCEL" ]]; then
        : >"$ACTIVE_CANCEL" 2>/dev/null || true
    fi
    return 0
}
# TEST HOOK, honoured only on the test scope stand-in: pause between the last
# check for a stop and the launch, so a test can land a signal exactly there.
TEST_PAUSE_BEFORE_LAUNCH=""
if [[ $TEST_SCOPE -eq 1 ]]; then
    TEST_PAUSE_BEFORE_LAUNCH="${GPO_STUDIO_REQUAL_TEST_PAUSE_BEFORE_LAUNCH:-}"
fi
trap 'on_stop 15' TERM
trap 'on_stop 2' INT
trap 'on_stop 1' HUP

# Remove an invocation's private directory -- only ever one mktemp made here.
drop_work() {
    [[ -n "$1" && "$1" == "$TMPDIR"/tmp.* ]] && rm -rf -- "$1"
    return 0
}

# run_bounded <deadline-epoch> <log> <command...>
# Run the command under lane-supervisor.py, inside its own scope: a new
# session, the log appended, and -- whether the command exits by itself or the
# deadline passes -- every process it started, detached or not, TERM'd,
# KILL'd after the grace, and reaped before this returns. Returns the
# command's own status (124 when the deadline killed it). Sets WATCHDOG_FIRED
# when the deadline did the killing (a lane may exit 124 by itself; psdirect
# does on its own deadline), CANCELLED when the supervisor was stopped by a
# signal, SCOPE_FAILED when no scope could be created (nothing ran),
# CONTAINMENT_LOST when the supervisor's report is missing or invalid or its
# scope is not verifiably empty afterwards (the scope is then killed and
# verified), and adds to PROCESSES_KILLED every process cleanup signalled.
run_bounded() {
    local deadline=$1 log=$2 work report cgroup_file status unit state parsed valid
    shift 2
    # A private (0700) directory per invocation for the report and the
    # ownership proof, which lane-scope.py publishes at 0600 and refuses to
    # read from anywhere others can write.
    work="$(mktemp -d)"
    report="$work/report.json"
    cgroup_file="$work/scope-proof"
    SCOPE_SEQ=$((SCOPE_SEQ + 1))
    unit="gpo-studio-lane-$SCOPE_NONCE-$SCOPE_SEQ"
    # The cancel file is armed BEFORE the last check: from here on, a stop
    # either prevents the launch or reaches the supervisor through the file.
    ACTIVE_CANCEL="$work/cancel"
    if [[ $STOP_SIGNAL -ne 0 ]]; then
        echo "=== watchdog: stopped before launch (signal $STOP_SIGNAL); not started: $*" >>"$log"
        ACTIVE_CANCEL=""
        drop_work "$work"
        CANCELLED=1
        return $((128 + STOP_SIGNAL))
    fi
    [[ -n "$TEST_PAUSE_BEFORE_LAUNCH" ]] && sleep "$TEST_PAUSE_BEFORE_LAUNCH"
    # In the background, so a signal to this driver runs its trap at once
    # (a foreground child would defer it); `wait` is then the place it lands.
    "${SCOPE_TOOL[@]}" start "$unit" "$SCOPE_NONCE" "$cgroup_file" -- \
        python3 "$SUPERVISOR" --deadline "$deadline" --grace "$LANE_KILL_GRACE" \
        --log "$log" --report "$report" --cancel-file "$ACTIVE_CANCEL" -- "$@" &
    ACTIVE_SUPERVISOR=$!
    local stop_seen_at=""
    while kill -0 "$ACTIVE_SUPERVISOR" 2>/dev/null; do
        if [[ $STOP_SIGNAL -eq 0 ]]; then
            # Blocks until the supervisor exits or a signal runs the trap.
            wait "$ACTIVE_SUPERVISOR" 2>/dev/null
            continue
        fi
        # Stopping: the supervisor was told (cancel file). Poll rather than wait, so
        # a supervisor that does not finish its own cleanup in time is
        # killed; the scope check below then treats the missing report as
        # lost containment and clears the scope itself.
        stop_seen_at=${stop_seen_at:-$(date +%s)}
        if (( $(date +%s) - stop_seen_at > SUPERVISOR_STOP_GRACE )); then
            echo "=== watchdog: supervisor did not stop within ${SUPERVISOR_STOP_GRACE}s; killing it" >>"$log"
            kill -KILL "$ACTIVE_SUPERVISOR" 2>/dev/null
        fi
        sleep 0.5
    done
    wait "$ACTIVE_SUPERVISOR" 2>/dev/null
    status=$?
    ACTIVE_SUPERVISOR=""
    ACTIVE_CANCEL=""
    if [[ ! -s "$cgroup_file" ]]; then
        # The scope was never established, so the supervisor -- and the lane
        # -- never started. Whatever already holds this unit name is not ours
        # to touch: nothing is killed or stopped by name.
        SCOPE_FAILED=1
        echo "=== watchdog: SCOPE NOT CREATED (status $status) for $unit; the lane never started and nothing was cleaned up by name" >>"$log"
        drop_work "$work"
        return "$CONTAINMENT_LOST_STATUS"
    fi
    state="$(scope_state "$unit" "$cgroup_file")"
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
    if [[ $valid -ne 0 || "$state" != empty ]]; then
        # The supervisor died, wrote nonsense, or left something running, or
        # the lane's cgroup cannot be read: nothing vouches for this lane's
        # containment any more. Kill the cgroup, verify it, record it, and
        # let the caller stop the batch.
        CONTAINMENT_LOST=1
        echo "=== watchdog: CONTAINMENT LOST (supervisor status $status, report $([[ $valid -eq 0 ]] && echo valid || echo missing or invalid), scope $unit $state); killing the scope" >>"$log"
        if [[ "$state" == populated* ]]; then
            read -ra leftover <<<"${state#populated }"
            PROCESSES_KILLED=$((PROCESSES_KILLED + ${#leftover[@]}))
        fi
        if [[ "$state" == empty ]] || clear_scope "$unit" "$cgroup_file" "$log"; then
            echo "=== watchdog: scope $unit verified empty" >>"$log"
        fi
        drop_work "$work"
        return "$CONTAINMENT_LOST_STATUS"
    fi
    read -r status sup_timed_out sup_cancelled sup_killed <<<"$parsed"
    drop_work "$work"
    [[ $sup_timed_out -eq 1 ]] && WATCHDOG_FIRED=1
    [[ $sup_cancelled -eq 1 ]] && CANCELLED=1
    PROCESSES_KILLED=$((PROCESSES_KILLED + sup_killed))
    return "$status"
}

for row in "${LANES[@]}"; do
    IFS='|' read -r name runner budget envs <<<"$row"
    selected "$name" || continue
    if [[ $STOP_SIGNAL -ne 0 ]]; then
        echo "batch STOPPED by signal $STOP_SIGNAL before $name; no further lane runs" >&2
        exit "$BATCH_CANCELLED"
    fi
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
    SCOPE_FAILED=0
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
    if [[ $status -eq 0 && $CANCELLED -eq 0 && $CONTAINMENT_LOST -eq 0 && $SCOPE_FAILED -eq 0 \
          && $STOP_SIGNAL -eq 0 && -n "$next" ]]; then
        run_bounded "$deadline" "$log" bash -c "$next"
        status=$?
    fi
    if [[ $STOP_SIGNAL -ne 0 ]]; then
        # The driver was stopped while this lane ran: whatever it exited with
        # is not its verdict.
        CANCELLED=1
        [[ $CONTAINMENT_LOST -eq 0 && $SCOPE_FAILED -eq 0 ]] && status=$((128 + STOP_SIGNAL))
    fi
    set -e
    timed_out=$WATCHDOG_FIRED
    completed="$(date -u +%Y-%m-%dT%H:%M:%S.%6N+00:00)"
    run_dir="$(sed -n 's/^LOCAL_RUN_DIR=//p' "$log" | tail -1)"
    python3 - "$PROGRESS" "$name" "$runner" "$COMMIT" "$started" "$completed" "$status" "$run_dir" \
        "$budget" "$timed_out" "$PROCESSES_KILLED" "$CANCELLED" "$CONTAINMENT_LOST" \
        "$SCOPE_FAILED" "$TEST_SCOPE" <<'PY'
import json, sys
(path, name, runner, commit, started, completed, status, run_dir, budget, timed_out, strays,
 cancelled, lost, scope_failed, test_scope) = sys.argv[1:]
import os, stat
# The record was reserved private before the first lane; append only to the
# file that is still exactly that (checked on the descriptor actually written).
fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW)
st = os.fstat(fd)
if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
    os.close(fd)
    print(f"refusing: {path} is no longer a private file of this user", file=sys.stderr)
    sys.exit(2)
with os.fdopen(fd, "a", encoding="utf-8") as fh:
    fh.write(json.dumps({
        "name": name, "runner": runner, "commit": commit,
        "started_utc": started, "completed_utc": completed,
        "exit_status": int(status), "local_run_dir": run_dir or None,
        "budget_seconds": int(budget), "timed_out": timed_out == "1",
        "processes_killed": int(strays),
        "cancelled": cancelled == "1", "containment_lost": lost == "1",
        "scope_failed": scope_failed == "1", "test_scope_tool": test_scope == "1",
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
    if [[ $SCOPE_FAILED -eq 1 ]]; then
        echo "batch STOPPED: no scope could be created for $name; no further lane runs" >&2
        exit "$BATCH_CONTAINMENT_LOST"
    fi
    if [[ $CANCELLED -eq 1 || $STOP_SIGNAL -ne 0 ]]; then
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
