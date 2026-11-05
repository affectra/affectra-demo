# exposa-demo

A small ESPHome device whose CI builds the firmware and writes its SBOM, for
trying out [Exposa](https://github.com/schmidtfx/exposa).

## The device

[`firmware/climate-sensor.yaml`](firmware/climate-sensor.yaml): an ESP32
(`esp32dev`) reading a BME280 temperature, humidity and pressure sensor over I²C,
built with ESPHome 2024.10.3 on the ESP-IDF framework (ESP-IDF 4.4.8).

## CI

[`.github/workflows/firmware.yml`](.github/workflows/firmware.yml) runs on every
push and pull request:

1. Builds the firmware in the pinned ESPHome image.
2. Writes a CycloneDX 1.6 SBOM with Espressif's
   [`esp-idf-sbom`](https://github.com/espressif/esp-idf-sbom). It lists ESP-IDF and
   what it bundles (FreeRTOS, lwIP, ...) with their CPEs; libraries compiled but not
   linked into the firmware are left out.
3. Uploads the `climate-sensor` artifact:

   | File | |
   |---|---|
   | `climate-sensor.cdx.json` | The SBOM |
   | `climate-sensor.factory.bin` | Full image, flash over USB at offset `0x0` |
   | `climate-sensor.ota.bin` | Over-the-air update |

On pushes to `main` and tags (not pull requests) it also signs two
[artifact attestations](https://docs.github.com/actions/security-for-github-actions/using-artifact-attestations)
for both firmware images: build provenance (this repository, workflow and commit
built them) and the SBOM (this SBOM describes them). Check a downloaded image with:

```sh
gh attestation verify climate-sensor.ota.bin --repo schmidtfx/exposa-demo
gh attestation verify climate-sensor.ota.bin --repo schmidtfx/exposa-demo \
  --predicate-type https://cyclonedx.org/bom
```

The second command shows the attested SBOM. Attestations need the repository to be
public (or on GitHub Enterprise Cloud).

On a tag `v*` it also uploads the SBOM to Exposa, into product **Climate Sensor**,
hardware revision **esp32dev**, version = the tag. To turn that on:

1. In Exposa, as an organization admin: **Trusted publishers**, trust this
   repository (optionally only `firmware.yml` and `refs/tags/v*`).
2. In this repository: **Settings → Secrets and variables → Actions → Variables**,
   set `EXPOSA_URL` (your instance) and `EXPOSA_ORG` (the organization slug).

Without `EXPOSA_URL` the upload job is skipped.

ESP-IDF 4.4.8 has known vulnerabilities (e.g. CVE-2025-55297, CVE-2025-66409), so
the release in Exposa has findings to look at and assess.

## Build locally

Needs Docker or Podman; the first build takes a few minutes.

```sh
docker run --rm -v "$PWD":/src -w /src --entrypoint sh \
  ghcr.io/esphome/esphome:2024.10.3 firmware/build.sh
```

The firmware and SBOM land in `dist/`.
