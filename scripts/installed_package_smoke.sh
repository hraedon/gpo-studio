#!/usr/bin/env bash
# Installed-package smoke test: build wheel, install in clean venv, exercise CLI+API.
# Run from the repository root: bash scripts/installed_package_smoke.sh
set -euo pipefail

if [ ! -f scripts/print_installed_version.py ]; then
  echo "FAIL: run from the repository root (the checks call scripts/*.py)"
  exit 1
fi

TMPDIR=$(mktemp -d)
SERVER_PID=""
trap 'kill "$SERVER_PID" 2>/dev/null || true; rm -rf "$TMPDIR"' EXIT

PYTHON=${PYTHON:-python3.13}
PORT=8799
DB="$TMPDIR/smoke.db"

if [ -n "${GPO_STUDIO_WHEEL:-}" ]; then
  WHEEL=$(realpath "$GPO_STUDIO_WHEEL")
else
  echo "=== Building wheel ==="
  # With the locked backend only: a throwaway environment gets uv.lock's
  # `build-backend` group, and `uv build` builds gpo-studio in it without
  # isolation ([tool.uv] in pyproject.toml), so nothing resolves a backend from
  # the index. No uv, no build: an isolated `python -I -m build` would.
  if ! command -v uv >/dev/null 2>&1; then
    echo "FAIL: building the wheel needs uv (or set GPO_STUDIO_WHEEL to a built wheel)"
    exit 1
  fi
  UV_PROJECT_ENVIRONMENT="$TMPDIR/build-env" uv sync --locked --only-group build-backend
  UV_PROJECT_ENVIRONMENT="$TMPDIR/build-env" uv build --wheel --out-dir "$TMPDIR/dist"
  WHEEL=$(find "$TMPDIR/dist" -maxdepth 1 -name 'gpo_studio-*.whl' -print -quit)
fi
echo "Wheel: $WHEEL"

# Dependencies come from the hash-pinned lock, never from version ranges: the
# release passes its requirements-lock.txt asset; otherwise it is exported from
# uv.lock here. The wheel itself goes in with --no-deps --no-index, and
# `pip check` proves the locked set satisfies its metadata.
if [ -n "${GPO_STUDIO_REQUIREMENTS:-}" ]; then
  REQUIREMENTS=$(realpath "$GPO_STUDIO_REQUIREMENTS")
elif command -v uv >/dev/null 2>&1; then
  REQUIREMENTS="$TMPDIR/requirements-lock.txt"
  uv export --locked --no-default-groups --no-emit-project --format requirements.txt \
    --output-file "$REQUIREMENTS" > /dev/null
else
  echo "FAIL: set GPO_STUDIO_REQUIREMENTS to a hash-pinned requirements file, or install uv"
  exit 1
fi
echo "Requirements: $REQUIREMENTS"

echo "=== Creating clean venv ==="
"$PYTHON" -I -m venv "$TMPDIR/venv" --clear
"$TMPDIR/venv/bin/pip" install --only-binary :all: --require-hashes -r "$REQUIREMENTS"
"$TMPDIR/venv/bin/pip" install --only-binary :all: --no-deps --no-index "$WHEEL"
"$TMPDIR/venv/bin/pip" check

echo "=== Checking version consistency ==="
VENV_VERSION=$("$TMPDIR/venv/bin/python" -I scripts/print_installed_version.py)
META_VERSION=$("$TMPDIR/venv/bin/python" -I scripts/print_installed_version.py --metadata)
if [ "$VENV_VERSION" != "$META_VERSION" ]; then
  echo "FAIL: __version__ ($VENV_VERSION) != package metadata ($META_VERSION)"
  exit 1
fi
echo "Version: $VENV_VERSION (consistent)"

echo "=== Checking CLI entry point ==="
"$TMPDIR/venv/bin/gpo-studio" --help | head -1

echo "=== Starting server ==="
"$TMPDIR/venv/bin/gpo-studio" run --database "$DB" --port "$PORT" &
SERVER_PID=$!
sleep 2

echo "=== Checking installed API version ==="
HEALTH=$(curl -sf "http://127.0.0.1:$PORT/api/health")
HEALTH_VERSION=$(echo "$HEALTH" | "$TMPDIR/venv/bin/python" -I scripts/read_json_field.py version)
if [ "$HEALTH_VERSION" != "$VENV_VERSION" ]; then
  echo "FAIL: API version ($HEALTH_VERSION) != installed version ($VENV_VERSION)"
  exit 1
fi

echo "=== Creating GPO ==="
# One line: a continuation inside $(...) is refused by tests/workflow_grammar.py.
RESPONSE=$(curl -sf -X POST "http://127.0.0.1:$PORT/api/gpos" -H "Content-Type: application/json" -d '{"name":"Smoke Test GPO","domain":"test.local","actor":"smoke","reason":"installed-package smoke test"}')
GPO_GUID=$(echo "$RESPONSE" | "$TMPDIR/venv/bin/python" -I scripts/read_json_field.py gpo.guid)
echo "Created GPO: $GPO_GUID"

echo "=== Adding registry setting ==="
curl -sf -X POST "http://127.0.0.1:$PORT/api/gpos/$GPO_GUID/settings" \
  -H "Content-Type: application/json" \
  -d "{\"setting\":{\"side\":\"computer\",\"hive\":\"HKLM\",\"key\":\"Software\\\\Test\",\"value_name\":\"Setting1\",\"registry_type\":\"REG_DWORD\",\"value\":\"1\"},\"actor\":\"smoke\",\"reason\":\"add test setting\",\"expected_revision\":1}" > /dev/null
echo "Setting added"

echo "=== Exporting bundle ==="
curl -sf -o "$TMPDIR/export.zip" "http://127.0.0.1:$PORT/api/gpos/$GPO_GUID/export.zip"
ZIP_SIZE=$(stat -c%s "$TMPDIR/export.zip")
if [ "$ZIP_SIZE" -lt 500 ]; then
  echo "FAIL: export.zip too small ($ZIP_SIZE bytes)"
  exit 1
fi
echo "Export: $ZIP_SIZE bytes"

echo "=== Getting PowerShell plan ==="
PLAN=$(curl -sf "http://127.0.0.1:$PORT/api/gpos/$GPO_GUID/plan.ps1")
if ! echo "$PLAN" | grep -q "Set-GPRegistryValue"; then
  echo "FAIL: plan.ps1 does not contain Set-GPRegistryValue"
  exit 1
fi
echo "Plan contains Set-GPRegistryValue"

echo "=== Static UI ==="
UI_STATUS=$(curl -sf -o /dev/null -w "%{http_code}" "http://127.0.0.1:$PORT/")
if [ "$UI_STATUS" != "200" ]; then
  echo "FAIL: static UI returned $UI_STATUS"
  exit 1
fi
echo "Static UI: 200"

echo "=== Workspace check ==="
kill "$SERVER_PID" 2>/dev/null
SERVER_PID=""
"$TMPDIR/venv/bin/gpo-studio" workspace check --database "$DB"

echo ""
echo "=== ALL SMOKE TESTS PASSED ==="
