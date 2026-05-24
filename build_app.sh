#!/bin/bash
# Builds a self-contained "Fly Me To The Roon.app" using PyInstaller.
# Prerequisites: run setup.sh and build_tkdnd.sh first.
# Usage: bash build_app.sh
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$SCRIPT_DIR/venv"
PY="$VENV/bin/python3"
DIST="$SCRIPT_DIR/dist"
APP_NAME="Fly Me To The Roon"
APP_BUNDLE="$DIST/$APP_NAME.app"
SPEC_FILE="$SCRIPT_DIR/FlyMeToTheRoon.spec"
ICNS_FILE="$SCRIPT_DIR/AppIcon.icns"

# ── Read version (single source of truth) ───────────────────────────────────
if [ -f "$SCRIPT_DIR/VERSION" ]; then
    VERSION="$(tr -d '[:space:]' < "$SCRIPT_DIR/VERSION")"
else
    VERSION="dev"
fi
echo "==> Building $APP_NAME v$VERSION"

# ── 1. Install / upgrade PyInstaller ────────────────────────────────────────
echo "==> Installing PyInstaller…"
"$VENV/bin/pip" install --quiet --upgrade pyinstaller

# ── 2. Verify tkdnd_lib is present ──────────────────────────────────────────
if [ ! -d "$SCRIPT_DIR/tkdnd_lib/lib" ]; then
    echo "ERROR: tkdnd_lib not found. Run build_tkdnd.sh first."
    exit 1
fi

# ── 3. Patch tkdnd dylib to use @loader_path so it works inside the bundle ───
echo "==> Patching tkdnd library for bundle compatibility…"
TKDND_DYLIB=$(ls "$SCRIPT_DIR/tkdnd_lib/lib/"*.dylib 2>/dev/null | head -1)
if [ -z "$TKDND_DYLIB" ]; then
    echo "ERROR: no .dylib found in tkdnd_lib/lib/. Run build_tkdnd.sh first."
    exit 1
fi

while IFS= read -r dep; do
    [[ "$dep" == "$TKDND_DYLIB" ]] && continue
    [[ "$dep" == /usr/lib/* ]]      && continue
    [[ "$dep" == /System/* ]]       && continue
    [[ "$dep" == @* ]]              && continue
    depname=$(basename "$dep")
    destlib="$SCRIPT_DIR/tkdnd_lib/lib/$depname"
    if [ ! -f "$destlib" ]; then
        echo "  Copying $depname"
        cp "$dep" "$destlib"
        chmod u+w "$destlib"
    fi
    echo "  Patching reference: $dep → @loader_path/$depname"
    install_name_tool -change "$dep" "@loader_path/$depname" "$TKDND_DYLIB"
done < <(otool -L "$TKDND_DYLIB" | tail -n +2 | awk '{print $1}')

LIBNAME=$(basename "$TKDND_DYLIB")
install_name_tool -id "@loader_path/$LIBNAME" "$TKDND_DYLIB" 2>/dev/null || true

# ── 4. Ensure icon exists. Generate via make_roontag_app.sh --icon-only if missing.
if [ ! -f "$ICNS_FILE" ]; then
    echo "==> Icon missing — generating via make_roontag_app.sh --icon-only"
    bash "$SCRIPT_DIR/make_roontag_app.sh" --icon-only
fi
ICNS_ARG="None"
if [ -f "$ICNS_FILE" ]; then
    ICNS_ARG="'$ICNS_FILE'"
fi

# ── 5. Write .spec file ──────────────────────────────────────────────────────
echo "==> Writing $SPEC_FILE…"
# Remove legacy spec
rm -f "$SCRIPT_DIR/RoonTag.spec"

cat > "$SPEC_FILE" << SPECEOF
# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
SCRIPT_DIR = Path(r'$SCRIPT_DIR')

a = Analysis(
    [str(SCRIPT_DIR / 'app.py')],
    pathex=[str(SCRIPT_DIR)],
    binaries=[],
    datas=[
        (str(SCRIPT_DIR / 'tkdnd_lib'), 'tkdnd_lib'),
        (str(SCRIPT_DIR / 'VERSION'), '.'),
        (str(SCRIPT_DIR / 'CHANGELOG.md'), '.'),
    ],
    hiddenimports=[
        'mutagen', 'mutagen.mp3', 'mutagen.flac', 'mutagen.aiff',
        'mutagen.mp4', 'mutagen.wave', 'mutagen.id3', 'mutagen.id3._frames',
        'mutagen._vorbis', 'mutagen._tags',
        'PIL', 'PIL.Image', 'PIL.ImageTk', 'PIL.ImageGrab',
        'PIL.ImageDraw', 'PIL.ImageFilter', 'PIL._imaging',
        'tkinterdnd2',
        'requests', 'urllib3', 'charset_normalizer', 'certifi', 'idna',
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Fly Me To The Roon',
    debug=False,
    strip=False,
    upx=False,
    console=False,
    argv_emulation=True,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name='Fly Me To The Roon',
)

app = BUNDLE(
    coll,
    name='Fly Me To The Roon.app',
    icon=$ICNS_ARG,
    bundle_identifier='com.mark.flymetotheroon',
    info_plist={
        'CFBundleName': 'Fly Me To The Roon',
        'CFBundleDisplayName': 'Fly Me To The Roon',
        'CFBundleVersion': '$VERSION',
        'CFBundleShortVersionString': '$VERSION',
        'NSHighResolutionCapable': True,
        'LSMinimumSystemVersion': '12.0',
        'CFBundleDocumentTypes': [{
            'CFBundleTypeName': 'Audio File',
            'CFBundleTypeExtensions': ['mp3','flac','aif','aiff','wav','m4a'],
            'CFBundleTypeRole': 'Editor',
        }],
        'NSAppleEventsUsageDescription':
            'Fly Me To The Roon needs to access files to tag music.',
    },
)
SPECEOF

# ── 6. Build ─────────────────────────────────────────────────────────────────
echo "==> Building (this takes ~1 minute)…"
cd "$SCRIPT_DIR"
"$VENV/bin/pyinstaller" --clean --noconfirm "$SPEC_FILE" 2>&1 | grep -v "^INFO:"

# ── 7. Ad-hoc code sign (required on Apple Silicon) ─────────────────────────
echo "==> Signing…"
codesign --force --deep --sign - "$APP_BUNDLE" 2>/dev/null && echo "  Signed ok" || echo "  (signing skipped)"

# ── 8. Install to /Applications ─────────────────────────────────────────────
echo "==> Installing to /Applications…"
rm -rf "/Applications/$APP_NAME.app"
cp -R "$APP_BUNDLE" "/Applications/$APP_NAME.app"

# ── 9. Register with Launch Services ────────────────────────────────────────
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister \
    -f "/Applications/$APP_NAME.app" 2>/dev/null || true

echo ""
echo "✓  $APP_NAME v$VERSION installed at /Applications/$APP_NAME.app"
echo ""
echo "Launch:   open \"/Applications/$APP_NAME.app\""
echo "Log:      ~/Library/Logs/$APP_NAME.log"
echo ""
echo "If macOS shows a security warning, go to:"
echo "  System Settings → Privacy & Security → Open Anyway"
