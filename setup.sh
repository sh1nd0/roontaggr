#!/bin/bash
# Fly Me To The Roon setup script
# Run once: bash "$HOME/Documents/Claude/Projects/Fly Me To The Roon/setup.sh"

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$SCRIPT_DIR/venv"

# ── Choose Python ──────────────────────────────────────────────────────────
# Tcl/Tk 9.0 has a crash-on-startup bug on macOS 26+ (empty NSMenuItem title).
# Prefer python@3.13 or python@3.12 from Homebrew if available — these may be
# compiled against tcl-tk@8 (8.6), which doesn't have this issue.
# If you are on macOS 26+ and the app crashes immediately:
#   brew install tcl-tk@8   (then re-run this script)
PY=""
# Candidates: python.org framework installs first (have bundled Tk 8.6),
# then Homebrew pythons, then system python3.
CANDIDATES=(
    /Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12
    /Library/Frameworks/Python.framework/Versions/3.13/bin/python3.13
    /Library/Frameworks/Python.framework/Versions/3.11/bin/python3.11
    python3.12
    python3.13
    python3.11
    python3
)
for candidate in "${CANDIDATES[@]}"; do
    if command -v "$candidate" &>/dev/null 2>&1 || [ -x "$candidate" ]; then
        TK_VER=$("$candidate" -c "import tkinter; print(tkinter.TclVersion)" 2>/dev/null || echo "")
        echo "==> $candidate → Tcl/Tk ${TK_VER:-unavailable}"
        if [[ "$TK_VER" == 8.* ]]; then
            PY="$candidate"
            echo "    ✓ Using this — Tk 8.x is stable on all macOS versions"
            break
        elif [ -n "$TK_VER" ] && [ -z "$PY" ]; then
            PY="$candidate"   # fallback: first working python even if Tk 9
        fi
    fi
done
if [ -z "$PY" ]; then
    echo "ERROR: No suitable Python with tkinter found."
    echo "Install Python 3.12 from https://python.org and re-run this script."
    exit 1
fi
if [[ "$TK_VER" == 9.* ]]; then
    echo ""
    echo "WARNING: Using Python with Tcl/Tk 9.x — may crash on macOS 26+"
    echo "         Install Python 3.12 from https://python.org for Tk 8.6"
    echo ""
fi
echo "==> Creating virtual environment with $PY at $VENV"
"$PY" -m venv "$VENV"

echo "==> Installing dependencies"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install mutagen pillow requests tkinterdnd2

echo "==> Installing system tkdnd for drag-and-drop support"
if command -v brew &>/dev/null; then
    brew install tkdnd 2>&1 | grep -v "^==> Fetching\|^==> Downloading\|Already installed"
    # Find where Homebrew installed tkdnd and set env variable in launcher
    TKDND_PATH="$(brew --prefix tkdnd)/lib" 2>/dev/null || TKDND_PATH=""
else
    TKDND_PATH=""
fi

echo "==> Building Fly Me To The Roon.app in /Applications"

APP="/Applications/Fly Me To The Roon.app"
MACOS="$APP/Contents/MacOS"
RES="$APP/Contents/Resources"

mkdir -p "$MACOS" "$RES"

# Launcher shell script — name must match CFBundleExecutable in Info.plist
LAUNCHER_NAME="Fly Me To The Roon"
cat > "$MACOS/$LAUNCHER_NAME" << LAUNCHER
#!/bin/bash
# Set tkdnd library path if available (enables in-window drag-and-drop)
if [ -n "$TKDND_PATH" ]; then
    export TKDND_LIBRARY="$TKDND_PATH"
fi
# Override destination folder if ROONTAGGR_DEST is set in the environment.
# On a remote machine pointing at a network share, set this in ~/.zshenv:
#   export ROONTAGGR_DEST="/Volumes/Mark-Studio/PARA/5. ROON"
LOG="$HOME/Library/Logs/Fly Me To The Roon.log"
exec "$VENV/bin/python3" "$SCRIPT_DIR/app.py" "\$@" >>"\$LOG" 2>&1
LAUNCHER
chmod +x "$MACOS/$LAUNCHER_NAME"

# Info.plist
cat > "$APP/Contents/Info.plist" << PLIST
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
    <string>com.mark.roontaggr</string>
    <key>CFBundleVersion</key>
    <string>1.0</string>
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

echo ""
echo "✓ Fly Me To The Roon.app installed at /Applications/Fly Me To The Roon.app"
echo "✓ Dependencies installed in $VENV"
echo ""
echo "Launch with: open /Applications/Fly Me To The Roon.app"
echo "  or double-click it in Finder / Applications folder"
