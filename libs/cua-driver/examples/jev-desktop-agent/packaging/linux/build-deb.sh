#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VERSION="${1:-0.1.0}"
ARCH="${PORTER_DEB_ARCH:-$(dpkg --print-architecture)}"
BUILD="$ROOT/build/porter-deb"
BINARY_DIR="$BUILD/binary"
STAGE="$BUILD/stage"
DIST="$ROOT/dist"
APP_ID="io.github.shehabyahya.Porter"

command -v dpkg-deb >/dev/null || {
  echo "dpkg-deb is required" >&2
  exit 1
}
command -v python3 >/dev/null || {
  echo "python3 is required" >&2
  exit 1
}

APP_VERSION="$(
  ROOT="$ROOT" python3 - <<'PY'
import os
import sys
sys.path.insert(0, os.path.join(os.environ["ROOT"], "python"))
from version import __version__
print(__version__)
PY
)"
if [[ "$VERSION" != "$APP_VERSION" ]]; then
  echo "Requested package version $VERSION does not match Porter $APP_VERSION" >&2
  exit 1
fi

cd "$ROOT"
rm -rf "$BUILD"
mkdir -p "$BINARY_DIR" "$STAGE" "$DIST"

uv sync --locked --extra app --extra deploy

uv run --extra app --extra deploy python -m nuitka   --mode=onefile   --enable-plugin=pyside6   --assume-yes-for-downloads   --output-dir="$BINARY_DIR"   --output-filename=porter   --include-data-dir="$ROOT/ui=ui"   --include-data-dir="$ROOT/assets=assets"   --include-package=keyring.backends   --include-package=secretstorage   --include-package=dbus_next   --include-package=sounddevice   "$ROOT/python/porter_app.py"

BIN="$BINARY_DIR/porter"
if [[ ! -x "$BIN" ]]; then
  echo "Nuitka did not produce $BIN" >&2
  exit 1
fi

install -Dm755 "$BIN" "$STAGE/opt/porter/porter"
install -Dm644   "$ROOT/packaging/linux/$APP_ID.desktop"   "$STAGE/usr/share/applications/$APP_ID.desktop"
install -Dm644   "$ROOT/packaging/linux/$APP_ID.metainfo.xml"   "$STAGE/usr/share/metainfo/$APP_ID.metainfo.xml"
install -Dm644   "$ROOT/assets/porter-ring.svg"   "$STAGE/usr/share/icons/hicolor/scalable/apps/$APP_ID.svg"

install -d "$STAGE/usr/bin"
cat >"$STAGE/usr/bin/porter" <<'EOF'
#!/bin/sh
exec /opt/porter/porter "$@"
EOF
chmod 0755 "$STAGE/usr/bin/porter"

install -d "$STAGE/DEBIAN"
cat >"$STAGE/DEBIAN/control" <<EOF
Package: porter-desktop
Version: $VERSION
Section: utils
Priority: optional
Architecture: $ARCH
Maintainer: Porter contributors
Depends: libegl1, libgl1, libportaudio2, libxkbcommon-x11-0, xdg-desktop-portal, curl
Recommends: gnome-keyring
Description: Porter resident AI desktop assistant
 Native Aurora Dark desktop assistant using Cua Driver and Jev.
EOF

cat >"$STAGE/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database /usr/share/applications >/dev/null 2>&1 || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -q /usr/share/icons/hicolor >/dev/null 2>&1 || true
fi
exit 0
EOF
chmod 0755 "$STAGE/DEBIAN/postinst"

OUT="$DIST/porter_${VERSION}_${ARCH}.deb"
dpkg-deb --build --root-owner-group "$STAGE" "$OUT"
echo "$OUT"
