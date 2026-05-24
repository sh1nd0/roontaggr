#!/bin/bash
# Publish a new Fly Me To The Roon release to GitHub.
#
# What it does:
#   1. Reads VERSION.
#   2. Bails out if a tag for that version already exists.
#   3. Runs build_app.sh to produce dist/Fly Me To The Roon.app.
#   4. Zips the .app to dist/Fly Me To The Roon.app.zip.
#   5. Extracts the matching section from CHANGELOG.md.
#   6. Creates a git tag vX.Y.Z and a GitHub Release with the zip attached.
#
# Prerequisites:
#   brew install gh
#   gh auth login
#
# Usage:
#   bash publish.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

if [ ! -f VERSION ]; then
    echo "ERROR: VERSION file not found." >&2
    exit 1
fi
VERSION="$(tr -d '[:space:]' < VERSION)"
TAG="v$VERSION"

if ! command -v gh >/dev/null 2>&1; then
    echo "ERROR: GitHub CLI (gh) not installed. Run: brew install gh && gh auth login" >&2
    exit 1
fi

# Bail if this tag already exists locally or on the remote
if git rev-parse "$TAG" >/dev/null 2>&1; then
    echo "ERROR: tag $TAG already exists locally. Bump VERSION first." >&2
    exit 1
fi
if gh release view "$TAG" >/dev/null 2>&1; then
    echo "ERROR: release $TAG already exists on GitHub. Bump VERSION first." >&2
    exit 1
fi

echo "==> Publishing Fly Me To The Roon $TAG"

# Fail early if working tree has unstaged changes — release should reflect committed code
if [ -n "$(git status --porcelain)" ]; then
    echo "ERROR: uncommitted changes in the working tree. Commit + push first." >&2
    git status --short >&2
    exit 1
fi

# 1. Build the app
bash build_app.sh

APP="$SCRIPT_DIR/dist/Fly Me To The Roon.app"
ZIP="$SCRIPT_DIR/dist/Fly Me To The Roon.app.zip"

if [ ! -d "$APP" ]; then
    echo "ERROR: $APP was not produced by build_app.sh" >&2
    exit 1
fi

# 2. Zip the .app. Use ditto so macOS metadata (resource forks, signing) is preserved.
echo "==> Zipping .app"
rm -f "$ZIP"
ditto -c -k --sequesterRsrc --keepParent "$APP" "$ZIP"

# 3. Extract the changelog entry for this version.
NOTES_FILE="$(mktemp)"
awk -v ver="$VERSION" '
    /^## / {
        if (in_section) exit
        # Match "## 1.3.0" or "## 1.3.0 — ..." etc.
        if ($2 == ver) { in_section = 1; next }
    }
    in_section { print }
' CHANGELOG.md > "$NOTES_FILE"

if [ ! -s "$NOTES_FILE" ]; then
    echo "Release $TAG" > "$NOTES_FILE"
fi

# 4. Tag + create GitHub release with the zip as an asset.
echo "==> Creating tag $TAG"
git tag "$TAG"
git push origin "$TAG"

echo "==> Creating GitHub release"
gh release create "$TAG" "$ZIP" \
    --title "Fly Me To The Roon $TAG" \
    --notes-file "$NOTES_FILE"

rm -f "$NOTES_FILE"

echo ""
echo "✓  Fly Me To The Roon $TAG published."
echo "   Other Macs will see the update on next launch."
echo "   Release: https://github.com/sh1nd0/roontaggr/releases/tag/$TAG"
