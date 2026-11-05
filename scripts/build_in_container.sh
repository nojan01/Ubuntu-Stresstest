#!/usr/bin/env bash
# Baut das portable Bundle, OpenDCDiag, RPM und AppImage in einem AlmaLinux-9-
# Container (glibc 2.34). Damit laufen die Artefakte auf RHEL/AlmaLinux/Rocky 9+,
# Debian 12+ und Ubuntu 22.04+. Ein auf Ubuntu 24.04 gebautes Bundle verlangt
# glibc 2.38 und startet auf EL9 nicht.
#
# Aufruf (auf dem Host, braucht podman oder docker):
#   bash scripts/build_in_container.sh            # RPM + AppImage
#   bash scripts/build_in_container.sh rpm        # nur RPM
#   bash scripts/build_in_container.sh --shell    # interaktive Shell im Container
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="${HARDWARETEST_BUILD_IMAGE:-docker.io/library/almalinux:9}"
BUILD_ROOT_IN="/src/build/el9"
TARGETS="${*:-rpm appimage}"

if [[ "${IN_CONTAINER:-0}" != "1" ]]; then
    ENGINE="$(command -v podman || command -v docker || true)"
    [[ -n "$ENGINE" ]] || { echo "FEHLER: podman oder docker fehlt (sudo apt install podman)" >&2; exit 1; }
    mkdir -p "$REPO_DIR/build/el9"
    extra=()
    [[ "$TARGETS" == "--shell" ]] && { extra=(-it); TARGETS="bash"; }
    exec "$ENGINE" run --rm "${extra[@]}" \
        -v "$REPO_DIR:/src:Z" \
        -e IN_CONTAINER=1 \
        -e HARDWARETEST_BUILD_IMAGE="$IMAGE" \
        -w /src \
        "$IMAGE" bash /src/scripts/build_in_container.sh $TARGETS
fi

# ---- ab hier im Container -------------------------------------------------
dnf -q -y install epel-release >/dev/null
dnf -q -y --allowerasing install \
    python3.12 python3.12-pip python3.12-devel \
    gcc-toolset-13-gcc-c++ git pkgconf-pkg-config curl binutils \
    zlib-devel libzstd-devel boost-devel \
    perl-core rpm-build file desktop-file-utils fuse fuse-libs \
    mesa-libGL mesa-libEGL libxkbcommon-x11 xcb-util-cursor xcb-util-wm \
    xcb-util-keysyms xcb-util-renderutil fontconfig dbus-libs >/dev/null
[[ "$TARGETS" == "bash" ]] && exec scl enable gcc-toolset-13 -- bash

export HARDWARETEST_PORTABLE_BUILD_ROOT="$BUILD_ROOT_IN"
export PYINSTALLER_PYTHON="$BUILD_ROOT_IN/venv/bin/python"
export QT_QPA_PLATFORM=offscreen
export HOME=/tmp/build-home
mkdir -p "$HOME"

if [[ ! -x "$PYINSTALLER_PYTHON" ]]; then
    python3.12 -m venv "$BUILD_ROOT_IN/venv"
fi
"$PYINSTALLER_PYTHON" -m pip -q install --upgrade pip
"$PYINSTALLER_PYTHON" -m pip -q install -r /src/requirements-build.txt -e /src

# OpenDCDiag mit glibc 2.34 – läuft damit auch auf Debian/Ubuntu.
need_odcd=0
for b in opendcdiag opendcdiag-generic; do
    f=/src/vendor/opendcdiag/$b
    # grep ohne -q: sonst SIGPIPE für objdump und mit pipefail ein falsches Ergebnis
    if [[ ! -x "$f" ]] || objdump -T "$f" | grep 'GLIBC_2\.3[5-9]' >/dev/null; then need_odcd=1; fi
done
if [[ "$need_odcd" == 1 ]]; then
    # gcc-toolset nur hier aktivieren, sonst landet dessen libgcc_s (glibc 2.35) im Bundle
    OPENDCDIAG_WORK="$BUILD_ROOT_IN/opendcdiag-work" \
        scl enable gcc-toolset-13 -- bash /src/scripts/build_opendcdiag.sh
fi

bash /src/scripts/build_portable_bundle.sh
export HARDWARETEST_REUSE_BUNDLE=1
for t in $TARGETS; do
    case "$t" in
        rpm) bash /src/scripts/build_rpm.sh ;;
        appimage) bash /src/scripts/build_appimage.sh ;;
        deb) echo "DEB bitte auf dem Debian/Ubuntu-Host bauen (build_deb.sh)." ;;
        *) echo "Unbekanntes Ziel: $t" >&2; exit 1 ;;
    esac
done
