#!/usr/bin/env bash
# Build, install and sign MacFlow.app.
#
# The signing step is not optional. macOS pins Accessibility and Input
# Monitoring grants to the app's *designated requirement*. With ad-hoc signing
# that requirement is the code hash, which changes on every build — so every
# rebuild silently voids the permissions and the hotkey stops working with no
# error message. Signing with a stable certificate pins the requirement to the
# certificate instead, and the grants survive.
#
# One-time setup on a new machine — see packaging/make_signing_cert.sh.
#
# Usage: bash build_app.sh
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

IDENTITY="${MACFLOW_SIGN_IDENTITY:-MacFlow Local Signing}"
BUNDLE_ID="com.genearnold.macflow"
DEST="/Applications/MacFlow.app"

if ! security find-identity -v -p codesigning | grep -q "$IDENTITY"; then
    echo "ERROR: code-signing identity '$IDENTITY' not found."
    echo "Run: bash packaging/make_signing_cert.sh"
    exit 1
fi

[ -n "${VIRTUAL_ENV:-}" ] || { echo "ERROR: activate the venv first (source venv/bin/activate)"; exit 1; }

echo "==> Cleaning stale bytecode"
find . -name '__pycache__' -not -path './venv/*' -exec rm -rf {} + 2>/dev/null || true

echo "==> Building"
rm -rf build dist
python setup.py py2app >/dev/null

echo "==> Installing to $DEST"
pkill -f "MacFlow.app/Contents/MacOS" 2>/dev/null || true
sleep 1
rm -rf "$DEST"
cp -R dist/MacFlow.app "$DEST"

echo "==> Signing with '$IDENTITY'"
codesign --force --deep --sign "$IDENTITY" --identifier "$BUNDLE_ID" "$DEST"

echo "==> Designated requirement (must be identical across builds):"
codesign -d -r- "$DEST" 2>&1 | grep "^designated" | sed 's/^/    /'

codesign --verify --verbose "$DEST" 2>&1 | sed 's/^/    /'
echo "==> Done. Permissions are preserved across rebuilds."
