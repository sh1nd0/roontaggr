#!/bin/bash
# Build / refresh the "Fly Me To The Roon.app" bundle (without PyInstaller).
# Generates AppIcon.icns from a Pillow design and installs the launcher bundle
# that points at app.py inside this checkout — useful while iterating in dev.
#
# Flags:
#   --icon-only   Generate AppIcon.icns and exit (used by build_app.sh)
#
# Usage: bash make_roontag_app.sh [--icon-only]
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$SCRIPT_DIR/venv"
PY="$VENV/bin/python3"

ICON_ONLY=0
if [ "${1:-}" = "--icon-only" ]; then
    ICON_ONLY=1
fi

APP_NAME="Fly Me To The Roon"
OLD_APPS=(
    "/Applications/RoonTaggr.app"
    "/Applications/RoonTag.app"
)
NEW_APP="/Applications/$APP_NAME.app"
ICNS_OUT="$SCRIPT_DIR/AppIcon.icns"

# ── 1. Generate icon from icon.png (source-of-truth artwork) ────────────────
ICON_SRC="$SCRIPT_DIR/icon.png"
if [ ! -f "$ICON_SRC" ]; then
    echo "ERROR: $ICON_SRC not found. Place a square PNG (≥1024×1024) named"
    echo "       icon.png in the project root."
    exit 1
fi
echo "==> Generating $APP_NAME icon from $ICON_SRC…"
SCRIPT_DIR_FOR_PY="$SCRIPT_DIR" "$PY" - <<'PYEOF'
import os
from pathlib import Path
from PIL import Image

SRC = Path(os.environ["SCRIPT_DIR_FOR_PY"]) / "icon.png"
ICONSET = Path("/tmp/FlyMeToTheRoon.iconset")
ICONSET.mkdir(exist_ok=True)

base = Image.open(SRC).convert("RGBA")
if base.width != base.height:
    # Center-crop to square so non-square sources still produce clean icons.
    side = min(base.size)
    left = (base.width  - side) // 2
    top  = (base.height - side) // 2
    base = base.crop((left, top, left + side, top + side))

SPECS = [
    ("icon_16x16.png",      16),
    ("icon_16x16@2x.png",   32),
    ("icon_32x32.png",      32),
    ("icon_32x32@2x.png",   64),
    ("icon_128x128.png",    128),
    ("icon_128x128@2x.png", 256),
    ("icon_256x256.png",    256),
    ("icon_256x256@2x.png", 512),
    ("icon_512x512.png",    512),
    ("icon_512x512@2x.png", 1024),
]

cache: dict[int, Image.Image] = {}
for fname, sz in SPECS:
    if sz not in cache:
        cache[sz] = base.resize((sz, sz), Image.LANCZOS)
    cache[sz].save(ICONSET / fname)

print(f"  Resampled {len(SPECS)} PNGs from {SRC.name} into {ICONSET}")
PYEOF

# ── 2. Convert iconset → .icns ───────────────────────────────────────────────
echo "==> Running iconutil…"
iconutil -c icns /tmp/FlyMeToTheRoon.iconset -o "$ICNS_OUT"
echo "  Icon: $ICNS_OUT"

# Legacy filename mirror so older specs still find an icon
cp "$ICNS_OUT" "$SCRIPT_DIR/RoonTag.icns" 2>/dev/null || true

if [ "$ICON_ONLY" = "1" ]; then
    exit 0
fi

# ── 3. Remove any legacy app bundles ────────────────────────────────────────
for OLD in "${OLD_APPS[@]}"; do
    if [ -d "$OLD" ] && [ "$OLD" != "$NEW_APP" ]; then
        echo "  Removing legacy $OLD…"
        rm -rf "$OLD"
    fi
done

# ── 4. Build dev launcher bundle (points at app.py in this checkout) ────────
echo "==> Building $APP_NAME.app (dev launcher)…"

MACOS="$NEW_APP/Contents/MacOS"
RES="$NEW_APP/Contents/Resources"
mkdir -p "$MACOS" "$RES"

cp "$ICNS_OUT" "$RES/AppIcon.icns"

LAUNCHER_NAME="$APP_NAME"
cat > "$MACOS/$LAUNCHER_NAME" << LAUNCHER
#!/bin/bash
# Set tkdnd library path if available (enables in-window drag-and-drop)
TKDND_PATH="\$(brew --prefix tkdnd 2>/dev/null)/lib"
if [ -d "\$TKDND_PATH" ]; then
    export TKDND_LIBRARY="\$TKDND_PATH"
fi
# Override destination folder if ROONTAGGR_DEST is set in the environment.
# On a remote machine pointing at a network share, set this in ~/.zshenv:
#   export ROONTAGGR_DEST="/Volumes/Mark-Studio/PARA/5. ROON"
LOG="\$HOME/Library/Logs/Fly Me To The Roon.log"
exec "$VENV/bin/python3" "$SCRIPT_DIR/app.py" "\$@" >>"\$LOG" 2>&1
LAUNCHER
chmod +x "$MACOS/$LAUNCHER_NAME"

cat > "$NEW_APP/Contents/Info.plist" << PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
    "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>Fly Me To The Roon</string>
    <key>CFBundleDisplayName</key>
    <string>Fly Me To The Roon</string>
    <key>CFBundleIdentifier</key>
    <string>com.mark.flymetotheroon</string>
    <key>CFBundleVersion</key>
    <string>dev</string>
    <key>CFBundleExecutable</key>
    <string>Fly Me To The Roon</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleIconFile</key>
    <string>AppIcon</string>
    <key>LSMinimumSystemVersion</key>
    <string>12.0</string>
    <key>NSHighResolutionCapable</key>
    <true/>
    <key>CFBundleDocumentTypes</key>
    <array>
        <dict>
            <key>CFBundleTypeName</key>
            <string>Audio File</string>
            <key>CFBundleTypeExtensions</key>
            <array>
                <string>mp3</string>
                <string>flac</string>
                <string>aif</string>
                <string>aiff</string>
                <string>wav</string>
                <string>m4a</string>
            </array>
            <key>CFBundleTypeRole</key>
            <string>Editor</string>
        </dict>
    </array>
    <key>NSAppleEventsUsageDescription</key>
    <string>Fly Me To The Roon needs to access files to tag music.</string>
</dict>
</plist>
PLIST

touch "$NEW_APP"
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister \
    -f "$NEW_APP" 2>/dev/null || true

echo ""
echo "✓ $APP_NAME.app installed at $NEW_APP"
echo ""
echo "Launch with:  open \"$NEW_APP\""
echo "Or double-click it in Finder / Applications."
