#!/bin/sh
# Builds the Home Assistant Voice PE firmware from a checkout of
# esphome/home-assistant-voice-pe and writes its SBOM and AI BOM to dist/.
# Builds home-assistant-voice.factory.yaml, the image Nabu Casa ships, which
# includes home-assistant-voice.yaml.
# Runs inside the ESPHome image the firmware's own CI uses (CI does this); locally:
#   docker run --rm -v "$PWD":/src -w /src --entrypoint sh \
#     ghcr.io/esphome/esphome:2026.6.0 voice-pe/build.sh fw 26.9.0
set -eu

fw=$(cd "$1" && pwd)
version=$2
ESP_IDF_SBOM=1.5.0
name=home-assistant-voice
here=$(cd "$(dirname "$0")" && pwd)
dist=$here/../dist

# Build outside the checkout: PlatformIO cannot unpack the toolchain's
# symlinks into a folder shared from macOS.
work=$(mktemp -d)
cp -R "$fw/." "$work/"
# The YAML takes its voice_kit component from the dev branch; build the one
# released with this tag instead.
sed -i "s|^\( *ref:\) dev\$|\1 $version|" "$work/$name.yaml"
(cd "$work" && esphome compile "$name.factory.yaml")

build=$work/.esphome/build/$name
desc=$(find "$build" -name project_description.json -not -path '*bootloader*' | head -n 1)
if [ -z "$desc" ]; then
  echo "no project_description.json under $build" >&2
  exit 1
fi
# --rem-unused reads the linker map as <project_name>.map; PlatformIO writes it as
# firmware.map.
pdir=$(dirname "$desc")
project=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["project_name"])' "$desc")
if [ ! -f "$pdir/$project.map" ] && [ -f "$pdir/firmware.map" ]; then
  ln -s firmware.map "$pdir/$project.map"
fi

python3 -m venv /tmp/esp-idf-sbom
/tmp/esp-idf-sbom/bin/pip install -q "esp-idf-sbom==$ESP_IDF_SBOM"

mkdir -p "$dist"
# The app's project description, not the bootloader's. --rem-unused leaves out
# libraries that are compiled but not linked into the firmware.
/tmp/esp-idf-sbom/bin/python -m esp_idf_sbom -n --no-progress create \
  --format cyclonedx-json@1.6 --rem-unused \
  -o "$dist/voice-pe.cdx.json" "$desc"

# The AI BOM: the wake word and VAD models the build downloaded and compiled in.
# Also adds the XMOS audio firmware, which the ESP32 flashes onto the XU316, to
# the SBOM above.
python3 -I "$here/aibom.py" --config "$work/$name.yaml" --data-dir "$work/.esphome" \
  --version "$version" --out "$dist/voice-pe.aibom.cdx.json" --sbom "$dist/voice-pe.cdx.json"

sdkconfig=$(find "$build" -maxdepth 2 -name "sdkconfig.*" -not -name '*.old' | head -n 1)
if [ -z "$sdkconfig" ]; then
  echo "no sdkconfig under $build" >&2
  exit 1
fi
cp "$sdkconfig" "$dist/voice-pe.sdkconfig"
ls -l "$dist"
