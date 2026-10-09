#!/usr/bin/env bash
# Run a batch's post-batch directory check on the DC and capture its JSON.
#
#   acb exec cred:lab-hyperv-control cred:lab-guest-bootstrap -- \
#     env GPO_STUDIO_LAB_HOST=<hyper-v host> GPO_STUDIO_LAB_GUEST=LabDC01 \
#     bash scripts/plan-033/run-post-batch-collector.sh \
#       docs/plan-033/release110-cleanup/collector.ps1 \
#       docs/plan-033/release110-cleanup/directory.json
#
# Read-only: the collector queries AD and Group Policy and writes nothing on the
# guest. It throws (non-zero exit) when the directory differs from the lab
# baseline, AFTER printing the JSON, so the capture is kept either way and the
# exit status says whether it was clean. Run it only once the estate is idle:
# after the batch's last lane and before anything else touches the directory.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ORACLE_DIR="$(cd "$SCRIPT_DIR/../windows-oracle" && pwd)"
: "${GPO_STUDIO_LAB_HOST:?GPO_STUDIO_LAB_HOST not set}"
: "${GPO_STUDIO_LAB_GUEST:?GPO_STUDIO_LAB_GUEST not set (the DC)}"
: "${HYPERV_CONTROL_USERNAME:?composed hypervisor credential missing}"
: "${GUEST_BOOTSTRAP_USERNAME:?composed guest credential missing}"

collector="${1:?usage: run-post-batch-collector.sh <collector.ps1> <directory.json>}"
output="${2:?usage: run-post-batch-collector.sh <collector.ps1> <directory.json>}"
[[ -f "$collector" ]] || { echo "no collector at $collector" >&2; exit 2; }
[[ -e "$output" ]] && { echo "refusing to overwrite $output" >&2; exit 2; }

set +e
pwsh -NoProfile -File "$ORACLE_DIR/psdirect.ps1" \
    -LabHost "$GPO_STUDIO_LAB_HOST" -Guest "$GPO_STUDIO_LAB_GUEST" \
    -Action exec -TimeoutSeconds 300 -Command "$(cat "$collector")" >"$output"
status=$?
set -e
echo "collector exit=$status; captured $(wc -c <"$output") bytes in $output" >&2
exit "$status"
