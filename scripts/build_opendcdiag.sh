#!/usr/bin/env bash
# Baut OpenDCDiag (github.com/opendcdiag/opendcdiag, Apache 2.0) reproduzierbar
# nach vendor/opendcdiag/:
#   opendcdiag          -march=haswell (AVX2, Intel ab Haswell / AMD ab Zen)
#   opendcdiag-generic  -march=x86-64-v2 für ältere CPUs ohne AVX2
# Kein sudo nötig: meson/ninja kommen bei Bedarf aus einem temporären venv,
# Eigen als geprüftes Header-Archiv, Boost-Header per "apt-get download".
# Auf Ubuntu 24.04 bauen (glibc 2.39), dann läuft das Binary auch auf Debian 13
# und Ubuntu 26.04. libstdc++ wird statisch gelinkt.
set -euo pipefail

COMMIT="b26f647e28a67f925c34a9d3e59681eabba3d545"
EIGEN_URL="https://gitlab.com/libeigen/eigen/-/archive/3.4.0/eigen-3.4.0.tar.gz"
EIGEN_SHA256="8586084f71f9bde545ee7fa6d00288b264a2b7ac3607b974e54d13e7162c1c72"

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="$REPO_DIR/vendor/opendcdiag"
WORK="${OPENDCDIAG_WORK:-$(mktemp -d /tmp/opendcdiag-build.XXXXXX)}"
mkdir -p "$WORK" "$DEST"
echo "Arbeitsverzeichnis: $WORK"

for tool in git g++ pkg-config curl strip; do
    command -v "$tool" >/dev/null || { echo "FEHLER: $tool fehlt" >&2; exit 1; }
done
for pc in zlib libzstd; do
    pkg-config --exists "$pc" || { echo "FEHLER: $pc-Entwicklungsdateien fehlen (zlib1g-dev, libzstd-dev)" >&2; exit 1; }
done

if ! command -v meson >/dev/null || ! command -v ninja >/dev/null; then
    python3 -m venv "$WORK/venv"
    "$WORK/venv/bin/pip" -q install meson ninja
    export PATH="$WORK/venv/bin:$PATH"
fi

if ! pkg-config --exists eigen3; then
    curl -fsSL -o "$WORK/eigen.tar.gz" "$EIGEN_URL"
    echo "$EIGEN_SHA256  $WORK/eigen.tar.gz" | sha256sum -c -
    tar -C "$WORK" -xzf "$WORK/eigen.tar.gz"
    mkdir -p "$WORK/pc"
    printf 'Name: Eigen3\nDescription: Eigen\nVersion: 3.4.0\nCflags: -I%s\n' "$WORK/eigen-3.4.0" > "$WORK/pc/eigen3.pc"
    export PKG_CONFIG_PATH="$WORK/pc${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
fi

CPP_ARGS=""
if [[ ! -f /usr/include/boost/algorithm/string.hpp ]]; then
    pkg="$(apt-cache depends libboost-dev 2>/dev/null | awk '/Depends: libboost[0-9.]*-dev/{print $2; exit}')"
    (cd "$WORK" && apt-get download "${pkg:-libboost1.83-dev}")
    dpkg-deb -x "$WORK"/libboost*-dev_*.deb "$WORK/boost"
    CPP_ARGS="-isystem $WORK/boost/usr/include"
fi

if [[ ! -d "$WORK/src/.git" ]]; then
    git clone -q https://github.com/opendcdiag/opendcdiag "$WORK/src"
fi
git -C "$WORK/src" checkout -q "$COMMIT"
# std::vector::append_range (C++23) fehlt in GCC 13 – gleichwertig ersetzen.
sed -i 's/app_cfg->exec_wrapper.append_range(\([a-z_]*\));/app_cfg->exec_wrapper.insert(app_cfg->exec_wrapper.end(), \1.begin(), \1.end());/' \
    "$WORK/src/framework/sandstone_opts.cpp"

build() {
    local name="$1" march="$2" dir="$WORK/src/build-$1"
    rm -rf "$dir"
    meson setup "$dir" "$WORK/src" --buildtype=release -Dstatic_libstdcpp=true -Dselftests=false \
        -Dmarch_base="$march" -Dexecutable_name="$name" ${CPP_ARGS:+-Dcpp_args="$CPP_ARGS"} >/dev/null
    ninja -C "$dir" >/dev/null
    strip -o "$DEST/$name" "$dir/$name"
    chmod 0755 "$DEST/$name"
    echo "gebaut: $DEST/$name ($("$DEST/$name" --version))"
}
build opendcdiag haswell
build opendcdiag-generic x86-64-v2

cp "$WORK/src/LICENSE" "$DEST/LICENSE"
cp "$WORK/src/LICENSE.3rdparty" "$DEST/LICENSE.3rdparty"
echo "$COMMIT" > "$DEST/COMMIT"
echo "Fertig. Temporäre Dateien: $WORK"
