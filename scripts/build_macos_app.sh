#!/bin/bash
# Build ARIA.app — a menu-bar-only macOS app wrapper around main.py.
#
#   ./scripts/build_macos_app.sh            # build the bundle
#   ./scripts/build_macos_app.sh --login    # build + register as a Login Item
#
# The bundle is a thin launcher: it runs this repo's .venv python on main.py.
# Re-run after pulling new code only if you changed the icon or Info.plist —
# the app always runs the live source, so code changes need no rebuild.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="$ROOT/ARIA.app"
PY="$ROOT/.venv/bin/python"
NAME="ARIA"
BUNDLE_ID="com.aria.assistant"

[ -x "$PY" ] || { echo "✗ venv python not found at $PY"; exit 1; }

echo "▸ Building $APP"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

# --- icon: render PNG -> .iconset -> .icns -------------------------------
echo "▸ Rendering icon"
TMP="$(mktemp -d)"
ICONSET="$TMP/$NAME.iconset"
mkdir -p "$ICONSET"
QT_QPA_PLATFORM=offscreen "$PY" "$ROOT/scripts/_render_app_icon.py" "$TMP/icon_1024.png" 1024 >/dev/null
for s in 16 32 128 256 512; do
  sips -z "$s" "$s"        "$TMP/icon_1024.png" --out "$ICONSET/icon_${s}x${s}.png"     >/dev/null
  sips -z "$((s*2))" "$((s*2))" "$TMP/icon_1024.png" --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null
done
cp "$TMP/icon_1024.png" "$ICONSET/icon_512x512@2x.png"
iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/$NAME.icns"
rm -rf "$TMP"

# --- launcher executable -------------------------------------------------
echo "▸ Writing launcher"
cat > "$APP/Contents/MacOS/$NAME" <<EOF
#!/bin/bash
cd "$ROOT" || exit 1
# single instance — don't start a second ARIA
if /usr/bin/pgrep -f "$ROOT/.venv/bin/python main.py" >/dev/null 2>&1; then
  exit 0
fi
exec "$PY" main.py >> "$ROOT/aria.log" 2>&1
EOF
chmod +x "$APP/Contents/MacOS/$NAME"

# --- Info.plist ----------------------------------------------------------
echo "▸ Writing Info.plist"
cat > "$APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>            <string>$NAME</string>
  <key>CFBundleDisplayName</key>     <string>$NAME</string>
  <key>CFBundleIdentifier</key>      <string>$BUNDLE_ID</string>
  <key>CFBundleExecutable</key>      <string>$NAME</string>
  <key>CFBundleIconFile</key>        <string>$NAME</string>
  <key>CFBundlePackageType</key>     <string>APPL</string>
  <key>CFBundleShortVersionString</key> <string>1.0</string>
  <key>CFBundleVersion</key>         <string>1</string>
  <key>LSMinimumSystemVersion</key>  <string>11.0</string>
  <key>NSHighResolutionCapable</key> <true/>
  <!-- menu-bar-only: no Dock icon, no app-switcher entry -->
  <key>LSUIElement</key>             <true/>
  <!-- TCC permission prompts -->
  <key>NSMicrophoneUsageDescription</key>
  <string>ARIA listens for your voice commands.</string>
  <key>NSCameraUsageDescription</key>
  <string>ARIA uses the camera when you ask it to look at something.</string>
  <key>NSSpeechRecognitionUsageDescription</key>
  <string>ARIA transcribes your speech to understand commands.</string>
  <key>NSAppleEventsUsageDescription</key>
  <string>ARIA controls apps on your behalf when you ask.</string>
</dict>
</plist>
EOF

# refresh Finder/LaunchServices so the icon + name update immediately
touch "$APP"
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$APP" 2>/dev/null || true

echo "✓ Built $APP"

if [ "${1:-}" = "--login" ]; then
  echo "▸ Registering Login Item"
  osascript -e "tell application \"System Events\" to make login item at end with properties {path:\"$APP\", hidden:false}" >/dev/null
  echo "✓ ARIA will now start automatically at login"
fi

echo
echo "Launch now with:  open \"$APP\""
