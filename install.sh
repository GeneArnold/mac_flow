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
APP="/Applications/MacFlow.app"
PLIST_PATH="$HOME/Library/LaunchAgents/com.genearnold.mac_flow.plist"
LABEL="com.genearnold.mac_flow"

cmd="${1:-install}"

case "$cmd" in
    install)
        if [ ! -d "$APP" ]; then
            echo "ERROR: $APP not found."
            echo "Build and install it first: bash build_app.sh"
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

    <!-- Launch the .app bundle via LaunchServices rather than running the venv
         Python directly. macOS attributes Accessibility and Input Monitoring
         grants to the bundle; a bare python process is a different identity and
         would need its own grants, which is not obvious and fails silently. -->
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/open</string>
        <string>-a</string>
        <string>$APP</string>
    </array>

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
