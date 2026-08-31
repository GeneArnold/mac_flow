#!/usr/bin/env bash
# install.sh — Install/uninstall the LaunchAgent that starts MacFlow at login.
#
# Usage:
#   bash install.sh install      # enable autostart
#   bash install.sh uninstall    # disable autostart
#
# MacFlow itself runs fine with `python main.py` — this script only wires up
# the "launch on login" behaviour via a LaunchAgent plist.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PYTHON="$SCRIPT_DIR/venv/bin/python"
PLIST_PATH="$HOME/Library/LaunchAgents/com.genearnold.mac_flow.plist"
LABEL="com.genearnold.mac_flow"

cmd="${1:-install}"

case "$cmd" in
    install)
        if [ ! -f "$VENV_PYTHON" ]; then
            echo "ERROR: venv not found at $VENV_PYTHON"
            echo "Run: python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt"
            exit 1
        fi

        mkdir -p "$(dirname "$PLIST_PATH")"

        cat > "$PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>

    <key>ProgramArguments</key>
    <array>
        <string>$VENV_PYTHON</string>
        <string>$SCRIPT_DIR/main.py</string>
    </array>

    <key>WorkingDirectory</key>
    <string>$SCRIPT_DIR</string>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <false/>

    <key>StandardOutPath</key>
    <string>$SCRIPT_DIR/mac_flow.log</string>

    <key>StandardErrorPath</key>
    <string>$SCRIPT_DIR/mac_flow.log</string>
</dict>
</plist>
EOF

        # Load (replace any previous registration)
        launchctl unload "$PLIST_PATH" 2>/dev/null || true
        launchctl load "$PLIST_PATH"
        echo "Installed LaunchAgent at $PLIST_PATH"
        echo "MacFlow will start automatically the next time you log in."
        echo "Logs: $SCRIPT_DIR/mac_flow.log"
        ;;

    uninstall)
        if [ -f "$PLIST_PATH" ]; then
            launchctl unload "$PLIST_PATH" 2>/dev/null || true
            rm "$PLIST_PATH"
            echo "Removed $PLIST_PATH"
        else
            echo "No LaunchAgent installed at $PLIST_PATH"
        fi
        ;;

    *)
        echo "Usage: bash install.sh [install|uninstall]"
        exit 1
        ;;
esac
