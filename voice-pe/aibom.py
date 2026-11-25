"""AI BOM for the Home Assistant Voice PE firmware.

Writes a CycloneDX 1.6 ML-BOM of the microWakeWord models (wake words, "stop" and
voice activity detection) that ESPHome compiled into the firmware, and adds the XMOS
audio firmware to the firmware SBOM. Runs after `esphome compile`: it reads the files
ESPHome downloaded, so the digests are those of what is in the image.

ESPHome resolves a model the same way (esphome/components/micro_wake_word, 2026.6):
a bare name is a file in esphome/micro-wake-word-models, a URL is a manifest, and
the files land in <data dir>/micro_wake_word/<first 8 hex of sha256(url)>/.
"""

import argparse
import hashlib
import json
import os
import re
import sys
import uuid

import yaml

NS = uuid.UUID("0c7a2f8e-5d1b-4e7c-9a3f-6b2d8e4f1a90")
MODELS_REPO = "https://github.com/esphome/micro-wake-word-models/raw/main/models/v2/"
TOOL = {"type": "application", "name": "affectra-demo voice-pe/aibom.py"}

# What microWakeWord says about how its models are trained (its README); the exact
# training set of each model is not published.
DATASETS = [
    {
        "type": "data",
        "bom-ref": "dataset-piper-samples",
        "name": "microWakeWord synthetic wake word samples",
        "description": "Wake word clips generated with Piper sample generator and augmented. "
        "The clips used for each model are not published.",
        "externalReferences": [
            {"type": "website", "url": "https://github.com/rhasspy/piper-sample-generator"}
        ],
        "data": [{"type": "dataset", "name": "synthetic wake word samples", "classification": "public"}],
    },
    {
        "type": "data",
        "bom-ref": "dataset-negative",
        "name": "kahrendt/microwakeword",
        "description": "Pre-generated spectrogram features of negative audio "
        "(speech, music, ambient noise) that microWakeWord trains against.",
        "externalReferences": [
            {"type": "distribution", "url": "https://huggingface.co/datasets/kahrendt/microwakeword"}
        ],
        "data": [{"type": "dataset", "name": "kahrendt/microwakeword", "classification": "public"}],
    },
]


class TagLoader(yaml.SafeLoader):
    """Reads ESPHome YAML; !lambda, !secret and friends become None."""


TagLoader.add_multi_constructor("!", lambda loader, suffix, node: None)


def sha(path, alg="sha256"):
    h = hashlib.new(alg)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def cache_dir(data_dir, domain, url):
    return os.path.join(data_dir, domain, hashlib.sha256(url.encode()).hexdigest()[:8])


def model_url(value):
    if isinstance(value, dict):
        value = value.get("model") or value.get("url")
    if re.fullmatch(r"[A-Za-z0-9_]+", value):
        return MODELS_REPO + value + ".json"
    if value.startswith(("http://", "https://")):
        return value
    sys.exit(f"model source not supported here: {value!r}")


def model_sources(mww):
    """(id, manifest URL, internal) for each model, VAD last."""
    out = []
    for m in mww.get("models") or []:
        src = m if isinstance(m, str) else m["model"]
        mid = None if isinstance(m, str) else m.get("id")
        url = model_url(src)
        out.append((mid or os.path.basename(url)[:-5], url, bool(not isinstance(m, str) and m.get("internal"))))
    if "vad" in mww:
        vad = mww["vad"] or {}
        out.append(("vad", model_url(vad.get("model", "vad")), True))
    return out


def model_version(url):
    """The release a model comes from: okay_nabu_20241226.3 -> 20241226.3, models/v2 -> v2."""
    m = re.search(r"/releases/download/([^/]+)/([^/]+)\.json$", url)
    if m:
        tag, name = m.groups()
        return tag[len(name) + 1:] if tag.startswith(name + "_") else None
    m = re.search(r"/models/(v\d+)/", url)
    return m.group(1) if m else None


def model_component(mid, url, internal, data_dir):
    d = cache_dir(data_dir, "micro_wake_word", url)
    with open(os.path.join(d, "manifest.json")) as f:
        man = json.load(f)
    path = os.path.join(d, man["model"])
    micro = man.get("micro", {})
    vad = man["wake_word"].lower() == "vad"
    langs = ", ".join(man.get("trained_languages", []))
    limits = [
        f"Trained on {langs} speech." if langs else None,
        f"ESPHome reports a detection when the mean probability over {micro.get('sliding_window_size')} "
        f"inferences of 30 ms reaches {micro.get('probability_cutoff')}; the firmware changes the cutoff "
        "with its wake word sensitivity setting." if not vad else None,
        "Runs only while a timer rings or an announcement plays, to stop it." if mid == "stop" else None,
    ]
    c = {
        "type": "machine-learning-model",
        "bom-ref": f"model-{mid}",
        "name": os.path.splitext(man["model"])[0],
        "description": "Voice activity detection for micro_wake_word" if vad
        else f'Wake word "{man["wake_word"]}"' + (" (internal)" if internal else ""),
        "supplier": {"name": man["author"], **({"url": [man["website"]]} if man.get("website") else {})},
        "licenses": [{"license": {"id": "Apache-2.0"}}],
        "hashes": [{"alg": "SHA-256", "content": sha(path)}],
        "externalReferences": [
            {"type": "distribution", "url": url.rsplit("/", 1)[0] + "/" + man["model"]},
            {"type": "website", "url": "https://github.com/kahrendt/microWakeWord"},
        ],
        "properties": [
            {"name": "affectra:ml:format", "value": "tflite"},
            {"name": "affectra:ml:quantization", "value": "int8"},
            {"name": "affectra:ml:accelerator", "value": "esp32-s3"},
        ],
        "modelCard": {
            "modelParameters": {
                "approach": {"type": "supervised"},
                "task": "voice activity detection" if vad else "keyword spotting",
                "architectureFamily": "convolutional neural network",
                "modelArchitecture": "microWakeWord streaming MixConv",
                "datasets": [{"ref": d["bom-ref"]} for d in DATASETS] if not vad else [],
                "inputs": [{"format": "40 int8 spectrogram features per 10 ms of 16 kHz audio"}],
                "outputs": [{"format": "uint8 probability" + ("" if vad else " that the wake word was spoken")}],
            },
            "considerations": {"technicalLimitations": [x for x in limits if x]},
        },
    }
    version = model_version(url)
    if version:
        c["version"] = version
    if not c["modelCard"]["modelParameters"]["datasets"]:
        del c["modelCard"]["modelParameters"]["datasets"]
    return c


def xmos_component(vk, data_dir):
    fw = vk["firmware"]
    url = fw["url"]
    path = cache_dir(data_dir, "voice_kit", url)
    m = re.match(r"https://github\.com/([^/]+/[^/]+)/releases/download/([^/]+)/", url)
    c = {
        "type": "firmware",
        "bom-ref": "xmos-firmware",
        "name": "voice-kit-xmos-firmware",
        "version": str(fw["version"]),
        "description": "Audio front end for the XMOS XU316 (echo cancellation, noise suppression, "
        "gain control). Flashed onto the XU316 by the ESP32-S3.",
        "supplier": {"name": "ESPHome"},
        "hashes": [{"alg": "SHA-256", "content": sha(path)}, {"alg": "MD5", "content": sha(path, "md5")}],
        "externalReferences": [{"type": "distribution", "url": url}],
    }
    if m:
        c["purl"] = f"pkg:github/{m.group(1)}@{m.group(2)}"
    return c


def finish(doc):
    body = json.dumps(doc, sort_keys=True)
    return {"bomFormat": "CycloneDX", "specVersion": "1.6",
            "serialNumber": f"urn:uuid:{uuid.uuid5(NS, body)}", **doc}


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--config", required=True, help="the firmware's ESPHome YAML")
    p.add_argument("--data-dir", required=True, help="ESPHome's data dir (.esphome next to the YAML)")
    p.add_argument("--version", required=True, help="firmware version")
    p.add_argument("--out", required=True, help="AI BOM to write")
    p.add_argument("--sbom", help="firmware SBOM to add the XMOS firmware to, in place")
    a = p.parse_args()

    with open(a.config) as f:
        cfg = yaml.load(f, Loader=TagLoader)

    models = [model_component(*s, a.data_dir) for s in model_sources(cfg["micro_wake_word"])]
    doc = finish({
        "version": 1,
        "metadata": {
            "tools": {"components": [TOOL]},
            "component": {"type": "firmware", "bom-ref": "firmware", "name": "Home Assistant Voice PE",
                          "version": a.version},
        },
        "components": models + DATASETS,
        "dependencies": [{"ref": "firmware", "dependsOn": [m["bom-ref"] for m in models]}],
    })
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w") as f:
        json.dump(doc, f, indent=2)
        f.write("\n")
    print(f"{a.out}: {len(models)} models", file=sys.stderr)

    if a.sbom and cfg.get("voice_kit", {}).get("firmware"):
        with open(a.sbom) as f:
            sbom = json.load(f)
        sbom.setdefault("components", []).append(xmos_component(cfg["voice_kit"], a.data_dir))
        with open(a.sbom, "w") as f:
            json.dump(sbom, f, indent=2)
            f.write("\n")
        print(f"{a.sbom}: added the XMOS firmware", file=sys.stderr)


if __name__ == "__main__":
    main()
