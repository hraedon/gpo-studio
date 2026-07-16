#!/usr/bin/env bash
# Installed-package smoke test: build wheel, install in clean venv, exercise CLI+API.
# Run: bash scripts/installed_package_smoke.sh
set -euo pipefail

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
  if command -v uv >/dev/null 2>&1; then
    uv build --wheel --out-dir "$TMPDIR/dist"
  else
    "$PYTHON" -m build --wheel --outdir "$TMPDIR/dist"
  fi
  WHEEL=$(find "$TMPDIR/dist" -maxdepth 1 -name 'gpo_studio-*.whl' -print -quit)
fi
echo "Wheel: $WHEEL"

echo "=== Creating clean venv ==="
"$PYTHON" -m venv "$TMPDIR/venv" --clear
"$TMPDIR/venv/bin/pip" install "$WHEEL"

echo "=== Checking version consistency ==="
VENV_VERSION=$("$TMPDIR/venv/bin/python" -c "from gpo_studio import __version__; print(__version__)")
META_VERSION=$("$TMPDIR/venv/bin/python" -c "import importlib.metadata as m; print(m.version('gpo-studio'))")
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
HEALTH_VERSION=$(echo "$HEALTH" | "$TMPDIR/venv/bin/python" -c "import sys,json; print(json.load(sys.stdin)['version'])")
if [ "$HEALTH_VERSION" != "$VENV_VERSION" ]; then
  echo "FAIL: API version ($HEALTH_VERSION) != installed version ($VENV_VERSION)"
  exit 1
fi

echo "=== Creating GPO ==="
RESPONSE=$(curl -sf -X POST "http://127.0.0.1:$PORT/api/gpos" \
  -H "Content-Type: application/json" \
  -d '{"name":"Smoke Test GPO","domain":"test.local","actor":"smoke","reason":"installed-package smoke test"}')
GPO_GUID=$(echo "$RESPONSE" | "$TMPDIR/venv/bin/python" -c "import sys,json; print(json.load(sys.stdin)['gpo']['guid'])")
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
