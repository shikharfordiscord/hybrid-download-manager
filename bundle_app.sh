#!/usr/bin/env bash
# bundle_app.sh — Builds a standalone macOS .app bundle for hydownload managers
set -e

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

echo "=============================================="
echo " Packaging hydownload managers for macOS      "
echo "=============================================="

# 1. Activate virtual environment if present
if [ -d "venv" ]; then
  source venv/bin/activate
elif [ -d ".venv" ]; then
  source .venv/bin/activate
fi

# 2. Clean previous build artifacts
rm -rf build_bin dist build
rm -rf "dist/Hybrid Download Manager.app" "dist/hydownload managers.app"
mkdir -p build_bin

# 3. Stage bundled binaries (aria2c and 7zz)
cp -f "$(which aria2c)" build_bin/aria2c
cp -f "$(which 7zz)" build_bin/7zz
chmod -R u+w build_bin
chmod +x build_bin/*

echo "✓ Staged aria2c and 7zz binaries"

# 4. Build standalone .app with PyInstaller
# Note: We exclude QtQuick and QtQml to avoid macOS QtQuickControls2 framework symlink conflicts
# since our UI is purely native QWidget-based (PyQt6.QtWidgets).
pyinstaller \
  --noconfirm \
  --onedir \
  --windowed \
  --name "hydownload managers" \
  --osx-bundle-identifier "com.hydownload.managers" \
  --add-data "build_bin/aria2c:bin" \
  --add-data "build_bin/7zz:bin" \
  --add-data "extension:extension" \
  --exclude-module PyQt6.QtQuick \
  --exclude-module PyQt6.QtQuickWidgets \
  --exclude-module PyQt6.QtQml \
  --exclude-module PyQt6.QtQmlModels \
  --collect-all aria2p \
  main_ui.py

APP_PATH="dist/hydownload managers.app"
RES_DIR="$APP_PATH/Contents/Resources"
RES_BIN="$RES_DIR/bin"
FW_BIN="$APP_PATH/Contents/Frameworks/bin"

# 5. Ensure binaries are accessible via Contents/Resources/bin and Contents/Frameworks/bin
mkdir -p "$RES_BIN" "$FW_BIN"
cp -f build_bin/aria2c "$RES_BIN/aria2c"
cp -f build_bin/7zz "$RES_BIN/7zz"
cp -f build_bin/aria2c "$FW_BIN/aria2c"
cp -f build_bin/7zz "$FW_BIN/7zz"
chmod +x "$RES_BIN"/* "$FW_BIN"/*

# 6. Ensure extension files are cleanly placed inside Contents/Resources/extension
mkdir -p "$RES_DIR/extension"
cp -R extension/* "$RES_DIR/extension/"

# 7. Set CFBundleName and CFBundleDisplayName in Info.plist to "hydownload managers"
PLIST="$APP_PATH/Contents/Info.plist"
if [ -f "$PLIST" ]; then
  plutil -replace CFBundleName -string "hydownload managers" "$PLIST"
  plutil -replace CFBundleDisplayName -string "hydownload managers" "$PLIST"
  echo "✓ Updated Info.plist CFBundleName and CFBundleDisplayName to 'hydownload managers'"
fi

# 8. Ad-hoc code signing & remove quarantine attributes
echo "Applying ad-hoc code signature and clearing quarantine..."
codesign --force --deep --sign - "$APP_PATH"
xattr -cr "$APP_PATH"

echo "=============================================="
echo " 🎉 Successfully built native macOS App Bundle: "
echo " $APP_PATH"
echo "=============================================="
