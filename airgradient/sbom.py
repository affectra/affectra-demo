#!/usr/bin/env python3
"""Builds one AirGradient firmware variant and writes its CycloneDX 1.6 SBOM.

The firmware (github.com/Open-Air-Foundation/firmware-one-openair) is an Arduino
library with sketches in examples/. It has no SBOM tooling of its own, and
Espressif's esp-idf-sbom needs a native ESP-IDF build, so this script makes the
SBOM from what the build actually used:

1. The Arduino core, the library versions and the compile flags come from the
   firmware's own CI (.github/workflows/check.yml) at the checked-out tag, so the
   SBOM follows whatever the maintainers pin.
2. arduino-cli compiles the sketch; its JSON result names the core and every
   library it linked.
3. For an ESP32 core, the versions of ESP-IDF, Mbed TLS, lwIP and FreeRTOS are
   read from the precompiled SDK the core ships (tools/sdk/<chip>/).
4. The libraries copied into the firmware (src/Libraries) are compiled with it;
   each is listed from its library.properties or, for a git submodule, its commit.

The output has no timestamp and a serial number derived from its content, so
building the same tag twice writes the same file and uploading it again is a no-op.

Usage: sbom.py --firmware fw --sketch OneOpenAir --fqbn esp32:esp32:esp32c3 \
               --version 3.7.1 --out dist/one-openair.cdx.json
"""

import argparse
import json
import os
import re
import subprocess
import sys
import uuid

import yaml

FIRMWARE_REPO = "open-air-foundation/firmware-one-openair"

# Libraries whose library.properties url is not their GitHub repository.
REPO_BY_NAME = {
    "ArduinoJson": "bblanchon/arduinojson",
    "PubSubClient": "knolleary/pubsubclient",
    "arduino-sht": "sensirion/arduino-sht",
}

# Arduino cores: package:architecture -> GitHub repository.
CORE_REPOS = {
    "esp32:esp32": "espressif/arduino-esp32",
    "esp8266:esp8266": "esp8266/arduino",
}

NS = uuid.UUID("6f1c3c1e-7a51-4f43-9a8e-2b8f0f6d4a10")


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def run(args, capture=False):
    log("+ " + " ".join(args))
    if capture:
        return subprocess.run(args, check=True, capture_output=True, text=True).stdout
    subprocess.run(args, check=True)
    return ""


def github_repo(url):
    """owner/repo from a GitHub URL, in lower case, or None."""
    m = re.match(r"^(?:https?://|git@)github\.com[/:]([^/]+)/([^/#?]+?)(?:\.git)?/?(?:[#?].*)?$", (url or "").strip())
    return f"{m.group(1)}/{m.group(2)}".lower() if m else None


def gh_purl(repo, version):
    return f"pkg:github/{repo}@{version}" if repo and version else None


def read_properties(path):
    props = {}
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                props[k.strip()] = v.strip()
    return props


# --- 1. The firmware's own CI configuration ---------------------------------


def ci_config(firmware, fqbn):
    """Core, libraries and compile flags that check.yml uses for this board."""
    with open(os.path.join(firmware, ".github/workflows/check.yml")) as f:
        wf = yaml.safe_load(f)
    job = wf["jobs"]["compile"]
    step = next(s for s in job["steps"] if str(s.get("uses", "")).startswith("arduino/compile-sketches"))
    w = step["with"]
    matrix = next((m for m in job["strategy"]["matrix"].get("include", []) if m.get("fqbn") == fqbn), None)
    if matrix is None:
        sys.exit(f"check.yml has no matrix entry for {fqbn}")

    def subst(s):
        return re.sub(r"\$\{\{\s*matrix\.(\w+)\s*\}\}", lambda m: str(matrix.get(m.group(1), "")), str(s))

    platforms = [{k: subst(v) for k, v in p.items()} for p in yaml.safe_load(w["platforms"])]
    libraries = yaml.safe_load(w["libraries"])
    flags = [subst(x) for x in yaml.safe_load(w.get("cli-compile-flags", "[]")) or []]
    # Drop an option whose value came out empty, e.g. --board-options for a board without any.
    clean = []
    for i, x in enumerate(flags):
        if x.startswith("--") and (i + 1 >= len(flags) or flags[i + 1] == ""):
            continue
        if x == "" and i > 0 and flags[i - 1].startswith("--"):
            continue
        clean.append(x)
    return platforms, libraries, clean


# --- 2. Build -----------------------------------------------------------------


def build(firmware, sketch, fqbn):
    platforms, libraries, flags = ci_config(firmware, fqbn)
    core = next(p for p in platforms if fqbn.startswith(p["name"] + ":"))
    urls = ["--additional-urls", core["source-url"]] if core.get("source-url") else []
    run(["arduino-cli", "core", "update-index", *urls])
    run(["arduino-cli", "core", "install", f"{core['name']}@{core['version']}", *urls])
    lib_args = []
    for lib in libraries:
        if "source-path" in lib:
            lib_args += ["--library", os.path.normpath(os.path.join(firmware, lib["source-path"]))]
        elif "name" in lib:
            spec = f"{lib['name']}@{lib['version']}" if lib.get("version") else lib["name"]
            run(["arduino-cli", "lib", "install", spec])
    out = run(
        ["arduino-cli", "compile", "--format", "json", "--fqbn", fqbn, *lib_args, *flags,
         os.path.join(firmware, "examples", sketch)],
        capture=True,
    )
    result = json.loads(out)
    if not result.get("success", True):
        sys.exit("compile failed:\n" + result.get("compiler_err", ""))
    br = result.get("builder_result") or result
    if "used_libraries" not in br:
        sys.exit(f"unexpected arduino-cli output, keys: {sorted(result)}")
    return br


# --- 3. Components ------------------------------------------------------------


def component(ref, name, version, purl=None, cpe=None, ctype="library", description=None, website=None):
    c = {"type": ctype, "bom-ref": ref, "name": name}
    if version:
        c["version"] = version
    if description:
        c["description"] = description
    if purl:
        c["purl"] = purl
    if cpe:
        c["cpe"] = cpe
    if website:
        c["externalReferences"] = [{"type": "website", "url": website}]
    return c


def sdk_components(core_dir, mcu):
    """ESP-IDF and what it bundles, from the SDK an ESP32 core ships precompiled."""
    sdk = os.path.join(core_dir, "tools", "sdk")
    versions = os.path.join(sdk, "versions.txt")
    if not os.path.exists(versions):
        return []
    idf = None
    with open(versions) as f:
        for line in f:
            m = re.match(r"^esp-idf:\s*(\S+)", line)
            if m:
                idf = m.group(1)
    if not idf:
        return []
    inc = os.path.join(sdk, mcu, "include")

    def grep(rel, pattern):
        try:
            with open(os.path.join(inc, rel), errors="replace") as f:
                m = re.search(pattern, f.read(), re.M)
                return m.groups() if m else None
        except FileNotFoundError:
            return None

    idf_v = idf.lstrip("v")
    comps = [component("esp-idf", "esp-idf", idf, gh_purl("espressif/esp-idf", idf),
                       f"cpe:2.3:a:espressif:esp-idf:{idf_v}:*:*:*:*:*:*:*", "framework",
                       f"Precompiled into the Arduino core for {mcu}")]
    m = grep("mbedtls/mbedtls/include/mbedtls/version.h", r'MBEDTLS_VERSION_STRING\s+"([^"]+)"')
    if m:
        comps.append(component("mbedtls", "mbedtls", m[0], gh_purl("mbed-tls/mbedtls", m[0]),
                               description="Bundled with ESP-IDF (Espressif's fork)"))
    parts = [grep("lwip/lwip/src/include/lwip/init.h", rf"^#define\s+LWIP_VERSION_{p}\s+(\d+)")
             for p in ("MAJOR", "MINOR", "REVISION")]
    if all(parts):
        v = ".".join(p[0] for p in parts)
        comps.append(component("lwip", "lwip", v, cpe=f"cpe:2.3:a:lwip_project:lwip:{v}:*:*:*:*:*:*:*",
                               description="Bundled with ESP-IDF"))
    m = grep("freertos/include/freertos/task.h", r'tskKERNEL_VERSION_NUMBER\s+"V?([^"]+)"')
    if m:
        comps.append(component("freertos", "freertos", m[0], cpe=f"cpe:2.3:o:amazon:freertos:{m[0]}:*:*:*:*:*:*:*",
                               description="Bundled with ESP-IDF"))
    return comps


def library_component(name, version, website, ref_prefix, description=None):
    repo = REPO_BY_NAME.get(name) or github_repo(website)
    return component(f"{ref_prefix}{name}", name, version, gh_purl(repo, version),
                     description=description, website=website or None)


def vendored_components(firmware):
    """Libraries copied into src/Libraries, compiled with the firmware."""
    base = os.path.join(firmware, "src", "Libraries")
    comps = []
    for d in sorted(os.listdir(base)):
        path = os.path.join(base, d)
        if not os.path.isdir(path):
            continue
        if os.path.exists(os.path.join(path, ".git")):  # a git submodule
            sha = run(["git", "-C", path, "rev-parse", "HEAD"], capture=True).strip()
            url = run(["git", "-C", path, "config", "--get", "remote.origin.url"], capture=True).strip()
            comps.append(component(f"vendored:{d}", d, sha, gh_purl(github_repo(url), sha),
                                   description="Git submodule in src/Libraries", website=url))
            continue
        props_path = os.path.join(path, "library.properties")
        if os.path.exists(props_path):
            p = read_properties(props_path)
            name = p.get("name", d)
            comps.append(library_component(name, p.get("version"), p.get("url"), "vendored:",
                                           f"Copied into src/Libraries/{d}"))
        else:
            comps.append(component(f"vendored:{d}", d, None,
                                   description=f"Copied into src/Libraries/{d}; no version recorded"))
    return comps


def sbom(firmware, sketch, fqbn, version, br):
    fw_dir = os.path.realpath(firmware)
    platform = br.get("build_platform") or br.get("board_platform") or {}
    core_id = platform.get("id") or ":".join(fqbn.split(":")[:2])
    core_dir = platform.get("install_dir", "")
    props = dict(p.split("=", 1) for p in br.get("build_properties", []) if "=" in p)
    mcu = props.get("build.mcu", "")

    core_version = platform.get("version")
    core = component("core", CORE_REPOS.get(core_id, core_id).split("/")[-1], core_version,
                     gh_purl(CORE_REPOS.get(core_id), core_version), ctype="framework",
                     description=f"Arduino core {core_id}")
    sdk = sdk_components(core_dir, mcu) if core_dir and mcu else []

    libs = []
    for lib in br["used_libraries"]:
        d = os.path.realpath(lib.get("install_dir", ""))
        loc = str(lib.get("location", "")).lower()
        if d == fw_dir:
            continue  # the AirGradient library is the firmware itself
        if "platform" in loc or (core_dir and d.startswith(os.path.realpath(core_dir))):
            continue  # part of the core (WiFi, HTTPClient, ...): covered by the core component
        libs.append(library_component(lib.get("name"), lib.get("version"), lib.get("website"), "lib:"))

    vendored = vendored_components(firmware)
    components = [core, *sdk, *libs, *vendored]
    root = {
        "type": "firmware",
        "bom-ref": "firmware",
        "name": "firmware-one-openair",
        "version": version,
        "description": f"Sketch {sketch} built for {fqbn}",
        "purl": gh_purl(FIRMWARE_REPO, version),
        "externalReferences": [{"type": "vcs", "url": f"https://github.com/{FIRMWARE_REPO}"}],
    }
    deps = [
        {"ref": "firmware", "dependsOn": [c["bom-ref"] for c in [core, *libs, *vendored]]},
        {"ref": "core", "dependsOn": [c["bom-ref"] for c in sdk[:1]]},
    ]
    if sdk:
        deps.append({"ref": "esp-idf", "dependsOn": [c["bom-ref"] for c in sdk[1:]]})
    doc = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "version": 1,
        "metadata": {
            "tools": {"components": [{"type": "application", "name": "affectra-demo airgradient/sbom.py"}]},
            "component": root,
        },
        "components": components,
        "dependencies": deps,
    }
    body = json.dumps(doc, sort_keys=True)
    doc = {"bomFormat": "CycloneDX", "specVersion": "1.6",
           "serialNumber": f"urn:uuid:{uuid.uuid5(NS, body)}", **doc}
    return doc


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--firmware", required=True, help="checkout of firmware-one-openair, with submodules")
    ap.add_argument("--sketch", required=True, help="folder in examples/, e.g. OneOpenAir")
    ap.add_argument("--fqbn", required=True, help="board, as in check.yml, e.g. esp32:esp32:esp32c3")
    ap.add_argument("--version", required=True, help="firmware version (the tag)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--builder-result", help="use this arduino-cli JSON instead of building (for testing)")
    a = ap.parse_args()

    if a.builder_result:
        with open(a.builder_result) as f:
            r = json.load(f)
        br = r.get("builder_result") or r
    else:
        br = build(a.firmware, a.sketch, a.fqbn)
    doc = sbom(a.firmware, a.sketch, a.fqbn, a.version, br)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w") as f:
        json.dump(doc, f, indent=2)
        f.write("\n")
    for c in doc["components"]:
        log(f"  {c['name']:<34} {c.get('version') or '-':<42} {c.get('purl') or c.get('cpe') or '(unmatchable)'}")
    log(f"wrote {a.out}: {len(doc['components'])} components")


if __name__ == "__main__":
    main()
