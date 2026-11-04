#!/bin/bash
# Builds a standalone "Video Downloader.app" for Apple Silicon Macs: Python, yt-dlp and Deno are
# inside, with their license texts, so it runs on a Mac with nothing else installed. ffmpeg is
# fetched by the app on first launch (its build is GPL, so it isn't shipped).
#
#   ./build_app.sh             build dist/Video Downloader.app
#   ./build_app.sh --install   ...and install it into ~/Applications (shortcut in /Applications)
#   ./build_app.sh --zip       ...and make dist/Video-Downloader-mac.zip to send to someone
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$PWD"
DENO_VERSION=2.9.0
APP="dist/Video Downloader.app"

[ "$(uname -m)" = arm64 ] || { echo "This builds for Apple Silicon; run it on an Apple Silicon Mac."; exit 1; }
for arg in "$@"; do
  case "$arg" in --install) INSTALL=1 ;; --zip) ZIP=1 ;; *) echo "unknown option: $arg"; exit 1 ;; esac
done

mkdir -p build/deps

# Deno, the official standalone release (Homebrew's build depends on other Homebrew libraries).
if ! build/deps/deno --version 2>/dev/null | grep -q "deno $DENO_VERSION"; then
  echo "Fetching Deno $DENO_VERSION…"
  base="https://github.com/denoland/deno/releases/download/v$DENO_VERSION"
  curl -fsSL -o build/deps/deno.zip "$base/deno-aarch64-apple-darwin.zip"
  expected=$(curl -fsSL "$base/deno-aarch64-apple-darwin.zip.sha256sum" | awk '{print $1}')
  actual=$(shasum -a 256 build/deps/deno.zip | awk '{print $1}')
  [ "$expected" = "$actual" ] || { echo "Deno download failed its checksum"; exit 1; }
  unzip -o -q build/deps/deno.zip -d build/deps && rm build/deps/deno.zip
fi

# Native (arm64) dependencies. The app ships yt-dlp; ffmpeg's build is GPL, so the app doesn't
# ship it but downloads this exact imageio-ffmpeg version from PyPI on first launch.
arch -arm64 uv sync --inexact --group build -q
YTDLP=$(arch -arm64 uv run python -c "import importlib.metadata as m; print(m.version('yt-dlp'))")
IMAGEIO_FFMPEG=$(arch -arm64 uv run python -c "import importlib.metadata as m; print(m.version('imageio-ffmpeg'))")
echo "{\"yt-dlp\": \"$YTDLP\", \"imageio-ffmpeg\": \"$IMAGEIO_FFMPEG\"}" > build/bundled_versions.json

# The license texts of everything inside the app.
arch -arm64 uv run python collect_licenses.py "$DENO_VERSION"

# Icon: every size macOS asks for, from assets/icon.png
ICONSET=build/AppIcon.iconset
rm -rf "$ICONSET" && mkdir -p "$ICONSET"
for s in 16 32 128 256 512; do
  sips -z $s $s assets/icon.png --out "$ICONSET/icon_${s}x${s}.png" >/dev/null
  sips -z $((s * 2)) $((s * 2)) assets/icon.png --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o build/AppIcon.icns

echo "Packaging (yt-dlp $YTDLP, Deno $DENO_VERSION)…"
arch -arm64 uv run --group build pyinstaller --noconfirm --clean --windowed --log-level WARN \
  --name "Video Downloader" --icon "$ROOT/build/AppIcon.icns" \
  --osx-bundle-identifier local.video-downloader --target-arch arm64 \
  --add-data "$ROOT/templates:templates" \
  --add-data "$ROOT/assets/icon.png:assets" \
  --add-data "$ROOT/build/bundled_versions.json:." \
  --add-data "$ROOT/build/licenses:licenses" \
  --add-binary "$ROOT/build/deps/deno:bin" \
  --collect-data yt_dlp_ejs \
  --hidden-import app --hidden-import engine --hidden-import downloader \
  --distpath "$ROOT/dist" --workpath "$ROOT/build/pyinstaller" --specpath "$ROOT/build" \
  desktop.py
# PyInstaller's imageio_ffmpeg hook would carry the GPL ffmpeg binary along; take it out.
find "$APP" -path "*imageio_ffmpeg/binaries/ffmpeg*" -exec rm -f {} +
if find "$APP" -iname "ffmpeg*" -type f | grep -q .; then echo "An ffmpeg binary is still inside the app"; exit 1; fi
plutil -replace CFBundleShortVersionString -string "1.0" "$APP/Contents/Info.plist"
plutil -replace LSMinimumSystemVersion -string "12.0" "$APP/Contents/Info.plist"
codesign --force --deep --sign - "$APP" 2>/dev/null   # ad-hoc signature, required on Apple Silicon
echo "Built: $APP ($(du -sh "$APP" | cut -f1))"

if [ -n "${INSTALL:-}" ]; then
  rm -rf "$HOME/Applications/Video Downloader.app"
  mkdir -p "$HOME/Applications"
  ditto "$APP" "$HOME/Applications/Video Downloader.app"
  LINK="/Applications/Video Downloader.app"
  if [ ! -e "$LINK" ] && [ ! -L "$LINK" ] && [ -w /Applications ]; then
    ln -s "$HOME/Applications/Video Downloader.app" "$LINK"
  fi
  echo "Installed: ~/Applications/Video Downloader.app"
fi

if [ -n "${ZIP:-}" ]; then
  SHARE=build/share/Video\ Downloader
  rm -rf build/share && mkdir -p "$SHARE"
  ditto "$APP" "$SHARE/Video Downloader.app"
  cp share/How\ to\ open.txt "$SHARE/"
  cp LICENSE "$SHARE/LICENSE.txt"
  rm -f dist/Video-Downloader-mac.zip
  (cd build/share && ditto -c -k --sequesterRsrc --keepParent "Video Downloader" "$ROOT/dist/Video-Downloader-mac.zip")
  echo "To share: dist/Video-Downloader-mac.zip ($(du -h dist/Video-Downloader-mac.zip | cut -f1))"
fi
