# AirGradient pilot

SBOMs for the open-source firmware of the AirGradient air quality monitors,
[Open-Air-Foundation/firmware-one-openair](https://github.com/Open-Air-Foundation/firmware-one-openair),
built here from its release tags, so Affectra can track its vulnerabilities without a
change in that repository.

## What is in the firmware

It is an Arduino library with one sketch per device in `examples/`, built for the
ESP32-C3 (ONE and Open Air) and the ESP8266 (DIY kits). The code comes from:

| Source | Example | Version from |
|---|---|---|
| Arduino core | arduino-esp32 2.0.17 with ESP-IDF v4.4.7 precompiled; esp8266 core 3.1.2 | `check.yml` in the firmware |
| Libraries from the Arduino registry | NimBLE-Arduino, ArduinoJson, Sensirion | `check.yml` |
| Libraries copied into `src/Libraries` | WiFiManager, PubSubClient, U8g2, Adafruit | each `library.properties` |
| Git submodules in `src/Libraries` | airgradient-client, airgradient-ota | the submodule's commit |

## How the SBOM is made

[`sbom.py`](sbom.py) builds one sketch with `arduino-cli` and writes a CycloneDX 1.6
SBOM:

1. It reads the core, the library versions and the compile flags from the firmware's
   own CI (`.github/workflows/check.yml`) at the tag, and installs exactly those.
2. It compiles the sketch. `arduino-cli`'s JSON result names the core and every
   library linked in.
3. For an ESP32 core, it reads the versions of ESP-IDF, Mbed TLS, lwIP and FreeRTOS
   from the precompiled SDK the core ships (`tools/sdk/<chip>/`).
4. It lists every library in `src/Libraries`: they are compiled with the firmware.

Libraries get a `pkg:github` package URL; lwIP and FreeRTOS get a CPE, as
Espressif's `esp-idf-sbom` writes them. The file has no timestamp and a serial
number derived from its content, so the same tag always gives the same file.

Run it locally (needs `arduino-cli` and PyYAML):

```sh
git clone --recurse-submodules --branch 3.7.1 \
  https://github.com/Open-Air-Foundation/firmware-one-openair.git fw
python airgradient/sbom.py --firmware fw --sketch OneOpenAir \
  --fqbn esp32:esp32:esp32c3 --version 3.7.1 --out dist/one-openair.cdx.json
```

## CI

[`airgradient.yml`](../.github/workflows/airgradient.yml) builds four variants:

| Variant in Affectra | Sketch | Board |
|---|---|---|
| `one-openair` | `OneOpenAir` | ESP32-C3 |
| `diy-pro-v4.2` | `DiyProIndoorV4_2` | ESP8266 (D1 mini) |
| `diy-pro-v3.3` | `DiyProIndoorV3_3` | ESP8266 (D1 mini) |
| `diy-basic` | `BASIC` | ESP8266 (D1 mini) |

It runs daily for the latest release and by hand for any tag (**Run workflow**, enter
e.g. `3.6.6`). The SBOMs are kept as artifacts. On pull requests it only builds.

To upload to Affectra, into product **AirGradient**, version = the tag:

1. In Affectra, as an organization admin: **Trusted publishers**, trust this
   repository, restricted to the workflow `airgradient.yml`.
2. In this repository, set the variables `AFFECTRA_URL` (your instance) and
   `AIRGRADIENT_ORG` (the organization slug).

Without `AIRGRADIENT_ORG` the upload job is skipped.

## What Affectra can match

None of these packages is in an ecosystem that OSV covers, so they are matched by CPE
against the NVD:

- **ESP-IDF** and **Mbed TLS**: built-in mappings, matched.
- **arduino-esp32**: built-in mapping once affectra/affectra#725 is released. Some of
  its CVEs (e.g. CVE-2025-53007) are still awaiting NVD analysis and have no affected
  versions yet, so they match nothing until the NVD adds them.
- **lwIP** and **FreeRTOS**: matched by the CPE in the SBOM.
- **The Arduino libraries** (NimBLE-Arduino, WiFiManager, PubSubClient, ...): the NVD
  has no CPE names for them, so they are listed but cannot be checked. NimBLE-Arduino
  embeds Apache NimBLE, whose NVD versions follow Apache's numbering, not the port's.

## Limits

- The build follows the firmware's CI, which pins the libraries. The images AirGradient
  ships over the air may be built differently (the GitHub releases have no binaries);
  check with the maintainers before calling the SBOM exact.
- Everything in `src/Libraries` is listed for every variant, although a library used
  only on one board may be left out by the linker on the other.
- For the ESP8266 core, only the core itself is listed, not the SDK, lwIP and BearSSL
  it bundles.
- `EMC230x` (a fan controller driver in `src/Libraries`) records no version.
