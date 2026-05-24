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

# ── 1. Generate icon ─────────────────────────────────────────────────────────
echo "==> Generating $APP_NAME icon…"
"$PY" - <<'PYEOF'
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter

ICONSET = Path("/tmp/FlyMeToTheRoon.iconset")
ICONSET.mkdir(exist_ok=True)

# ── Palette ──────────────────────────────────────────────────────────────────
# Roon-style violet diagonal gradient + white paper airplane carrying a music note.
TOP_LEFT     = (0x86, 0x6E, 0xFF)   # bright violet
BOTTOM_RIGHT = (0x47, 0x36, 0xC2)   # deep indigo
WHITE        = (0xFF, 0xFF, 0xFF, 255)
WHITE_DIM    = (0xE8, 0xE3, 0xFF, 255)   # subtle shadow on the lower wing
NOTE_COLOR   = (0x2A, 0x1E, 0x8E, 255)   # deep indigo for the note inside the plane


def _lerp(a, b, t):
    return tuple(int(a[i] * (1 - t) + b[i] * t) for i in range(3))


def make_icon(size: int) -> Image.Image:
    # ── Diagonal gradient background ─────────────────────────────────────
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    px = img.load()
    diag = (size - 1) * 2 or 1
    for y in range(size):
        for x in range(size):
            t = (x + y) / diag
            r, g, b = _lerp(TOP_LEFT, BOTTOM_RIGHT, t)
            px[x, y] = (r, g, b, 255)

    # Subtle radial highlight near the top-left
    highlight = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    hd = ImageDraw.Draw(highlight)
    rr = int(size * 0.55)
    cx_h, cy_h = int(size * 0.30), int(size * 0.22)
    for i in range(rr, 0, -1):
        alpha = int(38 * (i / rr) ** 2)
        hd.ellipse([cx_h - i, cy_h - i, cx_h + i, cy_h + i],
                   fill=(255, 255, 255, alpha))
    highlight = highlight.filter(ImageFilter.GaussianBlur(radius=size * 0.08))
    img = Image.alpha_composite(img, highlight)

    # ── Rounded-rectangle mask (macOS icon shape) ────────────────────────
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, size - 1, size - 1],
        radius=int(size * 0.225),
        fill=255,
    )
    img.putalpha(mask)

    draw = ImageDraw.Draw(img)

    # ── Trail of dots behind the plane ───────────────────────────────────
    trail_pts = [
        (size * 0.18, size * 0.78, size * 0.022, 90),
        (size * 0.27, size * 0.70, size * 0.030, 140),
        (size * 0.36, size * 0.62, size * 0.038, 190),
    ]
    for tx, ty, tr, a in trail_pts:
        draw.ellipse([tx - tr, ty - tr, tx + tr, ty + tr],
                     fill=(255, 255, 255, a))

    # ── Paper airplane ───────────────────────────────────────────────────
    def P(x, y):
        return (size * x, size * y)

    tip       = P(0.83, 0.20)
    back_top  = P(0.18, 0.74)
    notch     = P(0.36, 0.60)
    back_bot  = P(0.58, 0.85)

    draw.polygon([tip, back_top, notch], fill=WHITE)        # upper wing
    draw.polygon([tip, notch, back_bot], fill=WHITE_DIM)    # lower fold (shadow)

    fold_w = max(2, int(size * 0.006))
    draw.line([tip, notch], fill=(0xC9, 0xC0, 0xF5, 255), width=fold_w)

    # ── Tiny musical note tucked into the plane ──────────────────────────
    nc = P(0.58, 0.40)
    nh_w = size * 0.080
    nh_h = size * 0.060
    draw.ellipse(
        [nc[0] - nh_w / 2, nc[1] - nh_h / 2,
         nc[0] + nh_w / 2, nc[1] + nh_h / 2],
        fill=NOTE_COLOR,
    )
    stem_w = max(2, int(size * 0.012))
    sx = int(nc[0] + nh_w / 2 - stem_w * 0.6)
    sy_bot = int(nc[1] - nh_h * 0.10)
    sy_top = int(sy_bot - size * 0.115)
    draw.rectangle([sx, sy_top, sx + stem_w, sy_bot], fill=NOTE_COLOR)
    fw = size * 0.055
    fh = size * 0.065
    flag = [
        (sx + stem_w,             sy_top),
        (sx + stem_w + fw,        sy_top + fh * 0.30),
        (sx + stem_w + fw * 0.85, sy_top + fh * 0.62),
        (sx + stem_w,             sy_top + fh * 0.74),
    ]
    draw.polygon(flag, fill=NOTE_COLOR)

    return img


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
        cache[sz] = make_icon(sz)
    cache[sz].save(ICONSET / fname)

print(f"  Saved {len(SPECS)} PNGs to {ICONSET}")
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
