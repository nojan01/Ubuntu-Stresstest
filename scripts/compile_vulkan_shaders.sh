#!/usr/bin/env bash
# Übersetzt die GLSL-Shader des Vulkan-Lasttests nach SPIR-V und schreibt sie
# als Python-Modul (hardwaretest/tests/vulkan_spirv.py). Nur nach Änderungen an
# hardwaretest/tests/vulkan_shaders/*.comp nötig; benötigt glslang-tools und spirv-tools.
set -euo pipefail
cd "$(dirname "$0")/.."
SRC=hardwaretest/tests/vulkan_shaders
OUT=hardwaretest/tests/vulkan_spirv.py
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
{
    echo '"""SPIR-V der Vulkan-Lasttest-Shader (erzeugt von scripts/compile_vulkan_shaders.sh)."""'
    echo
    echo 'SPIRV = {'
    for name in fill verify compute; do
        glslangValidator -V --target-env vulkan1.0 -o "$TMP/$name.spv" "$SRC/$name.comp" >/dev/null
        spirv-val --target-env vulkan1.0 "$TMP/$name.spv"
        printf '    "%s": (\n' "$name"
        base64 -w 76 "$TMP/$name.spv" | sed 's/.*/        "&"/'
        echo '    ),'
    done
    echo '}'
} > "$OUT"
echo "geschrieben: $OUT"
