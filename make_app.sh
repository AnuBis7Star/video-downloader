#!/bin/bash
# Builds "Video Downloader.app" into ~/Applications.
# The app runs this project folder in place (its code and .venv), so after moving the folder,
# run this script again. Logs go to ~/Library/Logs/VideoDownloader.log.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
APP="$HOME/Applications/Video Downloader.app"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

# Icon: every size macOS asks for, from assets/icon.png
ICONSET="$(mktemp -d)/AppIcon.iconset"
mkdir -p "$ICONSET"
for s in 16 32 128 256 512; do
  sips -z $s $s "$ROOT/assets/icon.png" --out "$ICONSET/icon_${s}x${s}.png" >/dev/null
  sips -z $((s * 2)) $((s * 2)) "$ROOT/assets/icon.png" --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/AppIcon.icns"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Video Downloader</string>
  <key>CFBundleDisplayName</key><string>Video Downloader</string>
  <key>CFBundleIdentifier</key><string>local.video-downloader</string>
  <key>CFBundleExecutable</key><string>VideoDownloader</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
PLIST

cat > "$APP/Contents/MacOS/VideoDownloader" <<LAUNCHER
#!/bin/bash
# Apps opened from Finder get a bare PATH; yt-dlp needs deno, the updater needs uv.
export PATH="/opt/homebrew/bin:/usr/local/bin:\$HOME/.local/bin:\$PATH"
cd "$ROOT" || exit 1
exec >>"\$HOME/Library/Logs/VideoDownloader.log" 2>&1
echo "--- \$(date) starting"
uv sync --inexact -q || true
# exec, not a child process, so the window belongs to this app (its name and icon in the Dock)
exec "$ROOT/.venv/bin/python" "$ROOT/desktop.py"
LAUNCHER
chmod +x "$APP/Contents/MacOS/VideoDownloader"

touch "$APP"   # make Finder pick up the new icon
echo "Built: $APP"
