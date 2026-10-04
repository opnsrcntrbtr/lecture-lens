#!/usr/bin/env bash
# Build "Lecture Lens.app" from the SwiftPM package and install it to ~/Applications.
#
#   ./build_app.sh            release build + bundle + sign + install
#   ./build_app.sh --test     run the unit tests first
#   ./build_app.sh --open     launch it afterwards
#
# Signing and macOS privacy grants: TCC pins a grant to the app's code
# requirement. For an ad-hoc signature that requirement is the exact cdhash, so
# every rebuild silently invalidates the grant (Settings still shows it ON) and
# macOS re-prompts "would like to record this computer's screen and audio" on
# every capture attempt. Two guards:
#   1. an unchanged build is not re-signed or replaced (cdhash stays the same);
#   2. a stable identity is used when available, so grants survive real changes:
#      $SP_SIGN_IDENTITY, else an "Apple Development" certificate (sign into
#      Xcode), else a self-signed code-signing certificate named
#      "Lecture Lens Local" (Keychain Access > Certificate Assistant >
#      Create a Certificate, type Code Signing). Ad-hoc is the last resort.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PKG="$HERE/LectureLens"
NAME="Lecture Lens"
BUNDLE_ID="io.github.opnsrcntrbtr.lecture-lens"
DEST="${APP_DEST:-$HOME/Applications}"
APP="$DEST/$NAME.app"

run_tests=0; open_after=0
for a in "$@"; do
  case "$a" in
    --test) run_tests=1 ;;
    --open) open_after=1 ;;
    *) echo "unknown option: $a" >&2; exit 2 ;;
  esac
done

cd "$PKG"
if (( run_tests )); then
  echo "▸ swift test" >&2
  swift test 2>&1 | tail -25
fi

echo "▸ swift build -c release" >&2
swift build -c release --arch arm64 2>&1 | grep -Ev '^\[[0-9]+/[0-9]+\]' || true
BIN="$(swift build -c release --arch arm64 --show-bin-path)/LectureLens"
[[ -x "$BIN" ]] || { echo "✗ build produced no binary at $BIN" >&2; exit 1; }

echo "▸ bundling $APP" >&2
mkdir -p "$DEST"
STAGE="$(mktemp -d)/$NAME.app"
mkdir -p "$STAGE/Contents/MacOS" "$STAGE/Contents/Resources"
cp "$BIN" "$STAGE/Contents/MacOS/LectureLens"

VERSION="0.1.0"
BUILD="$(shasum -a 256 "$BIN" | cut -c1-12)"   # from the binary, so an unchanged build has an identical Info.plist
cat > "$STAGE/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>$NAME</string>
  <key>CFBundleDisplayName</key><string>$NAME</string>
  <key>CFBundleIdentifier</key><string>$BUNDLE_ID</string>
  <key>CFBundleExecutable</key><string>LectureLens</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleVersion</key><string>$BUILD</string>
  <key>LSMinimumSystemVersion</key><string>15.0</string>
  <key>LSApplicationCategoryType</key><string>public.app-category.education</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSPrincipalClass</key><string>NSApplication</string>
  <key>NSMicrophoneUsageDescription</key>
  <string>Lecture Lens transcribes your own voice only if you turn the microphone on. Everything stays on this Mac.</string>
  <key>NSAudioCaptureUsageDescription</key>
  <string>Lecture Lens transcribes lecture audio from Zoom, Meet and the course portal player so you can search and review it. Audio never leaves this Mac.</string>
  <key>NSScreenCaptureUsageDescription</key>
  <string>Lecture Lens reads lecture slides and on-screen text so you can search and review them. Nothing leaves this Mac.</string>
</dict>
</plist>
PLIST

IDENTITY="${SP_SIGN_IDENTITY:-}"
[[ -n "$IDENTITY" ]] || IDENTITY="$(security find-identity -v -p codesigning 2>/dev/null | awk -F'"' '/Apple Development/ {print $2; exit}')"
[[ -n "$IDENTITY" ]] || IDENTITY="$(security find-identity -p codesigning 2>/dev/null | awk -F'"' '/Lecture Lens Local/ {print $2; exit}')"

# Guard 1: nothing changed -> keep the installed, already-granted app as is.
# (compare the pre-signing build hash recorded at install; the installed binary itself carries our signature)
BUILD_HASH="$(cat "$BIN" "$STAGE/Contents/Info.plist" | shasum -a 256 | cut -d' ' -f1)"
echo "$BUILD_HASH" > "$STAGE/Contents/Resources/build.sha256"
if [[ -d "$APP" && "$(cat "$APP/Contents/Resources/build.sha256" 2>/dev/null)" == "$BUILD_HASH" ]]; then
  echo "✓ $APP unchanged — kept (signature and privacy grants untouched)" >&2
  rm -rf "$STAGE"
  if (( open_after )); then open "$APP"; fi
  exit 0
fi

# Hardened runtime blocks the microphone unless the app is entitled to it, and
# then macOS doesn't even prompt ("requires entitlement ...audio-input"). screenpipe
# (launched by this app) refuses to start with audio on until the mic is allowed.
ENT="$(mktemp -t lecture-lens-ent).plist"
cat > "$ENT" <<'ENTPLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>com.apple.security.device.audio-input</key><true/>
</dict></plist>
ENTPLIST

if [[ -n "$IDENTITY" ]]; then
  echo "▸ signing with: $IDENTITY (grants survive rebuilds)" >&2
  codesign --force --options runtime --entitlements "$ENT" --timestamp=none --sign "$IDENTITY" "$STAGE"
else
  echo "▸ no stable signing identity — signing ad-hoc." >&2
  echo "  macOS will treat this build as a NEW app: in System Settings > Privacy & Security >" >&2
  echo "  Screen & System Audio Recording, remove 'Lecture Lens' with (−), then start capture" >&2
  echo "  from the app and allow it again. See the header of this script for a permanent fix." >&2
  codesign --force --sign - "$STAGE"
fi
codesign --verify --strict "$STAGE"
codesign -d -r- "$STAGE" 2>&1 | sed -n 's/^designated => /  designated requirement: /p' >&2

# Replace the installed copy only after the new one is complete and verified.
pkill -x LectureLens 2>/dev/null || true
rm -rf "$APP"
mv "$STAGE" "$APP"
echo "✓ $APP" >&2

if (( open_after )); then open "$APP"; fi
