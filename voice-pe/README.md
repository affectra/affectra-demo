# Home Assistant Voice PE pilot

An SBOM and an AI BOM for the open-source firmware of the Home Assistant Voice
Preview Edition,
[esphome/home-assistant-voice-pe](https://github.com/esphome/home-assistant-voice-pe),
built here from its release tags. It shows a device that ships machine learning
models: Affectra lists them on the release's **AI models** tab and finds the
releases that ship a given model, digest or dataset.

## What is in the firmware

An ESPHome configuration for the ESP32-S3, built on ESP-IDF. Besides ESP-IDF and the
ESPHome components, the image carries:

| What | Where it comes from |
|---|---|
| Wake word models "Okay Nabu", "Hey Jarvis", "Hey Mycroft" | [microWakeWord](https://github.com/kahrendt/microWakeWord) releases and [esphome/micro-wake-word-models](https://github.com/esphome/micro-wake-word-models) |
| A "stop" model, on only while a timer rings or an announcement plays | microWakeWord releases |
| A voice activity detection (VAD) model | esphome/micro-wake-word-models |
| Firmware for the XMOS XU316 audio processor (echo cancellation, noise suppression) | [esphome/voice-kit-xmos-firmware](https://github.com/esphome/voice-kit-xmos-firmware) releases |

The models are int8-quantized TensorFlow Lite Micro models that run on the ESP32-S3.
Speech to text, intents and text to speech run in Home Assistant, not on the device.

## How the SBOMs are made

[`build.sh`](build.sh) runs in the ESPHome image that the firmware's own CI uses for
the tag (`esphome-version` in its `build.yml`):

1. It builds `home-assistant-voice.factory.yaml`, the image Nabu Casa ships. The YAML
   takes its `voice_kit` component from the `dev` branch; the build uses the one at
   the tag.
2. Espressif's [`esp-idf-sbom`](https://github.com/espressif/esp-idf-sbom) writes the
   firmware SBOM, `voice-pe.cdx.json` (CycloneDX 1.6): ESP-IDF and what it bundles,
   with CPEs, and the managed components (esp-tflite-micro, esp-nn, the ESPHome audio
   libraries). Libraries compiled but not linked in are left out.
3. [`aibom.py`](aibom.py) writes the AI BOM, `voice-pe.aibom.cdx.json`: one
   `machine-learning-model` component per model, with the SHA-256 of the `.tflite`
   file ESPHome downloaded and compiled in, the deployment facts (`tflite`, `int8`,
   `esp32-s3`), a model card from the model's manifest, and the datasets
   microWakeWord trains on. It also adds the XMOS firmware, with its SHA-256 and MD5,
   to the firmware SBOM.
4. The ESP-IDF `sdkconfig` is kept as `voice-pe.sdkconfig`, the release's build
   config.

The AI BOM has no timestamp and a serial number derived from its content, so the same
tag gives the same file as long as the models don't change.

Run it locally (Docker or Podman; the first build takes a while):

```sh
git clone --branch 26.9.0 https://github.com/esphome/home-assistant-voice-pe.git fw
docker run --rm -v "$PWD":/src -w /src --entrypoint sh \
  ghcr.io/esphome/esphome:2026.6.0 voice-pe/build.sh fw 26.9.0
```

## CI

[`voice-pe.yml`](../.github/workflows/voice-pe.yml) runs daily for the latest release
and by hand for any tag (**Run workflow**, enter e.g. `26.6.0`). The files are kept as
artifacts. On pull requests it only builds.

To upload to Affectra, into product **Home Assistant Voice PE**, variant `voice-pe`,
version = the tag (both SBOMs and the build config go to the same release):

1. In Affectra, as an organization admin: **Trusted publishers**, trust this
   repository, restricted to the workflow `voice-pe.yml`.
2. In this repository, set the variables `AFFECTRA_URL` (your instance) and
   `VOICE_PE_ORG` (the organization slug).

Without `VOICE_PE_ORG` the upload job is skipped.

## Limits

- The datasets are what microWakeWord's README says its models are trained on
  (Piper-generated wake word samples, and the negative sets on Hugging Face). Which
  clips went into each model is not published.
- "Hey Jarvis", "Hey Mycroft" and the VAD model come from the `main` branch of
  esphome/micro-wake-word-models, not from a release. Their version is the model
  format (`v2`); the digest tells which file it was. The "stop" model has no version.
- Only `home-assistant-voice.factory.yaml` is built, not the 8 MB variant.
- The XMOS firmware is listed by its release and digest only; what it is built from is
  not known here.
