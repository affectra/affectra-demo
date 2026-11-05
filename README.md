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
   | `climate-sensor.cdx.json.sigstore.json` | Signature of the SBOM (not on pull requests) |
   | `climate-sensor.factory.bin` | Full image, flash over USB at offset `0x0` |
   | `climate-sensor.ota.bin` | Over-the-air update |

### Signatures

On pushes to `main`, tags and manual runs (not pull requests) the build signs, keyless
with [Sigstore](https://www.sigstore.dev); the signatures name this repository,
workflow and commit, and are recorded in Sigstore's public transparency log:

- Two GitHub [artifact attestations](https://docs.github.com/actions/security-for-github-actions/using-artifact-attestations)
  for both firmware images: build provenance (this workflow built them) and the SBOM
  (this SBOM describes exactly these images). Check a downloaded image with:

  ```sh
  gh attestation verify climate-sensor.ota.bin --repo schmidtfx/exposa-demo
  gh attestation verify climate-sensor.ota.bin --repo schmidtfx/exposa-demo \
    --predicate-type https://cyclonedx.org/bom   # also shows the attested SBOM
  ```

- A cosign signature over the SBOM file, `climate-sensor.cdx.json.sigstore.json`.
  Exposa checks it on upload (below). Check it yourself with:

  ```sh
  cosign verify-blob climate-sensor.cdx.json \
    --bundle climate-sensor.cdx.json.sigstore.json \
    --certificate-oidc-issuer https://token.actions.githubusercontent.com \
    --certificate-identity-regexp '^https://github.com/schmidtfx/exposa-demo/\.github/workflows/firmware\.yml@'
  ```

Attestations need the repository to be public (or on GitHub Enterprise Cloud).

### Upload to Exposa

On a tag `v*` the SBOM goes to Exposa with its signature, into product
**Climate Sensor**, hardware revision **esp32dev**, version = the tag. Exposa marks it
**signed** because the same trusted repository signed it. To turn that on:

1. In Exposa, as an organization admin: **Trusted publishers**, trust this
   repository (optionally only `firmware.yml` and `refs/tags/v*`; leave the
   environment empty, since signatures carry none). Optionally switch on **Accept
   only SBOMs signed by a trusted repository**.
2. In this repository: **Settings → Secrets and variables → Actions → Variables**,
   set `EXPOSA_URL` (your instance) and `EXPOSA_ORG` (the organization slug).

Without `EXPOSA_URL` the upload job is skipped. The GitHub attestations are not sent;
Exposa does not read them yet.

ESP-IDF 4.4.8 has known vulnerabilities (e.g. CVE-2025-55297, CVE-2025-66409), so
the release in Exposa has findings to look at and assess.

## Build locally

Needs Docker or Podman; the first build takes a few minutes.

```sh
docker run --rm -v "$PWD":/src -w /src --entrypoint sh \
  ghcr.io/esphome/esphome:2024.10.3 firmware/build.sh
```

The firmware and SBOM land in `dist/`.
