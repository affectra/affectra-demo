#!/bin/sh
# Builds climate-sensor.yaml and writes the firmware and its SBOM to dist/.
# Runs inside the ESPHome image (CI does this); locally use:
#   docker run --rm -v "$PWD":/src -w /src --entrypoint sh ghcr.io/esphome/esphome:2024.10.3 firmware/build.sh
set -eu

ESP_IDF_SBOM=1.5.0
name=climate-sensor
here=$(cd "$(dirname "$0")" && pwd)
dist=$here/../dist

# Build outside the checkout: PlatformIO cannot unpack the toolchain's
# symlinks into a folder shared from macOS.
work=$(mktemp -d)
cp "$here/$name.yaml" "$work/"
(cd "$work" && esphome compile "$name.yaml")
pioenv=$work/.esphome/build/$name/.pioenvs/$name

python3 -m venv /tmp/esp-idf-sbom
/tmp/esp-idf-sbom/bin/pip install -q "esp-idf-sbom==$ESP_IDF_SBOM"

mkdir -p "$dist"
# The app's project description, not the bootloader's. --rem-unused leaves out
# libraries that are compiled but not linked into the firmware.
/tmp/esp-idf-sbom/bin/python -m esp_idf_sbom -n --no-progress create \
  --format cyclonedx-json@1.6 --rem-unused \
  -o "$dist/$name.cdx.json" "$pioenv/project_description.json"
cp "$pioenv/firmware.factory.bin" "$dist/$name.factory.bin"  # flash over USB
cp "$pioenv/firmware.ota.bin" "$dist/$name.ota.bin"          # update over the air
ls -l "$dist"
