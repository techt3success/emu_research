#!/usr/bin/env python3
"""
gen_manifest.py — build a mirror-rewritten, trimmed single-file manifest +
chunked-sync lists from a fetched ROM manifest (LineageOS by default).

Why it exists (runs 1-8 history):
  WALL 1 — android.googlesource.com anonymous IP quota (RESOURCE_EXHAUSTED)
           kills `repo sync` on shared GHA runners. 841 of LineageOS'
           969 default.xml projects still point at googlesource; no full
           AOSP mirror exists on GitHub (aosp-mirror is partial, archived;
           aosp-riscv is 67 repos), so the googlesource remote is rewritten
           to the Tsinghua TUNA full mirror (project names = AOSP paths,
           all tags mirrored, no IP quota; USTC fallback).
  WALL 2 — full sync co-grows tree (~70GB) + .repo git store (~35-45GB) +
           out/ (~22GB) > 121GB free disk. Chunks let us delete each
           project's git store right after its checkout is verified.
  NOTE   — repo >= 2.x REJECTS a local-manifest <remote> redefinition of an
           existing remote with different attributes ("remote ... already
           exists with different attributes", manifest_xml.py
           _ParseManifest), so the rewrite is done by emitting a fresh
           manifest and `repo init`-ing from it instead.

Outputs under --out-dir:
  default.xml     single-file manifest (includes inlined, remotes rewritten,
                  trim projects dropped)
  chunks/NN.tsv   path<TAB>name per chunk, in dependency-safe order
  summary.txt     per-chunk project counts (for the run log)
"""

import argparse
import copy
import os
import re
import sys
import xml.etree.ElementTree as ET
from collections import OrderedDict

GOOGLESOURCE = "android.googlesource.com"

# ---------------------------------------------------------------------------
# Trim list.
# Verified core (goal.md, early runs): cts, test/vts, linaro devices.
# The run-3 breakage came from remove-project entries for projects that did
# not exist; here we DROP project elements directly, so a stale entry is a
# no-op by construction.
# Extended (documented, this repo): pixel-phone/SoC dirs, cuttlefish, tv,
# google_car, trout, qcom device+vendor trees — none participate in
# sdk_phone64_x86_64 (goldfish emulator) builds; pure dead weight.
# ---------------------------------------------------------------------------
TRIM_PREFIXES = [
    # verified
    "cts",
    "test/vts",
    "device/linaro",
    # extended: emulator-irrelevant device families
    "device/google/cuttlefish",      # cuttlefish, cuttlefish_prebuilts, _vmm
    "device/generic/cuttlefish",
    "device/google_car",
    "device/google/trout",
    "device/google/atv",
    "device/google/contexthub",
    "device/google/raviole",
    "device/google/bluejay",
    "device/google/pantah",
    "device/google/lynx",
    "device/google/tangorpro",
    "device/google/felix",
    "device/google/shusky",
    "device/google/akita",
    "device/google/caimito",
    "device/google/comet",
    "device/google/gs101",
    "device/google/gs201",
    "device/google/zuma",
    "device/google/zumapro",
    "device/google/gs-common",
    "device/qcom",
    "vendor/qcom",
    "vendor/codeaurora",
]

# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# MINIMAL KEEP-SET: whitelist for the emulator-only build. Everything NOT
# matching these prefixes is dropped (not just trimmed). This cuts the tree
# from 964 to ~430 projects = ~55% less soong modules = peak RAM ~6-8GB
# (fits in 16GB) and tree ~35-40GB (fits with out/ on the free runner).
# ---------------------------------------------------------------------------
KEEP_PREFIXES = [
    "build", "prebuilts/", "system/", "bionic", "art", "libcore",
    "libnativehelper", "bootable/", "frameworks/",
    "packages/modules/",
    "packages/apps/Settings", "packages/apps/Launcher3", "packages/apps/Shell",
    "packages/apps/Camera2", "packages/apps/Gallery2", "packages/apps/Music",
    "packages/apps/DeskClock", "packages/apps/Calendar", "packages/apps/Contacts",
    "packages/apps/Bluetooth", "packages/apps/Nfc", "packages/apps/Mms",
    "packages/apps/Stk", "packages/apps/ThemePicker",
    "packages/apps/EmergencyInfo", "packages/apps/ExactCalculator",
    "packages/inputmethods/LatinIME",
    "packages/providers/", "packages/services/",
    "device/generic/goldfish", "device/generic/goldfish-opengl",
    "device/generic/sepolicy", "device/generic/ramdisk",
    "device/generic/bootloader",
    "hardware/", "vendor/lineage", "kernel/",
    "external/boringssl", "external/conscrypt", "external/expat", "external/icu",
    "external/libxml2", "external/okhttp", "external/zlib", "external/bzip2",
    "external/selinux", "external/skia", "external/ffmpeg", "external/libpng",
    "external/freetype", "external/harfbuzz_ng", "external/jpeg",
    "external/libwebp", "external/minijail", "external/sqlite",
    "external/libcxx", "external/libcxxabi", "external/jsoncpp",
    "external/protobuf", "external/fmtlib", "external/libyaml",
    "external/tinyxml2", "external/lz4", "external/zstd", "external/xz-embedded",
    "external/e2fsprogs", "external/f2fs-tools", "external/mksh",
    "external/toybox", "external/libcap", "external/libevent",
    "external/libldac", "external/libnetutils", "external/ninja",
    "external/python/cpython3", "external/googletest", "external/golang",
    "external/libtextclassifier", "external/libphonenumber", "external/libgrpc",
    "external/libopus", "external/libvpx", "external/libavc",
    "external/libhevc", "external/libgsm", "external/libmojo", "external/libutf",
    "external/libdivsufsort", "external/libusb", "external/libnl",
    "external/libpcap", "external/libsepol", "external/libpcre",
    "external/libffi", "external/elfutils", "external/fio", "external/flac",
    "external/flatbuffers", "external/giflib", "external/glslang",
    "external/gperf", "external/gptfdisk", "external/guava",
    "external/hsqldb", "external/hyphenation-patterns", "external/iw",
    "external/jsr305", "external/kmod", "external/lame", "external/libcutils",
    "external/libdrm", "external/libedit", "external/libexif", "external/libfuse",
    "external/libjpeg-turbo", "external/libmtp",
    "external/libnetfilter_conntrack", "external/libnfnetlink", "external/libogg",
    "external/libpcap", "external/libphonenumber", "external/libscrypt",
    "external/libselinux", "external/libsync", "external/libtar",
    "external/libtinyalsa", "external/libtomcrypt", "external/libufdt",
    "external/libunwind", "external/libunwind_llvm", "external/libutf",
    "external/libvixl", "external/libvorbis", "external/libvpx",
    "external/libvterm", "external/libwpa_client", "external/libziparchive",
    "external/lz4", "external/lzo", "external/mesa3d", "external/minigbm",
    "external/minijail", "external/mksh", "external/noto-fonts",
    "external/objenesis", "external/opencv", "external/openssl", "external/opus",
    "external/pdfium", "external/piex", "external/pigz", "external/puffin",
    "external/protobuf-c", "external/regex-re2", "external/roboto-fonts",
    "external/scrypt", "external/selinux", "external/sfntly", "external/skia",
    "external/speex", "external/sqlite", "external/squashfs-tools",
    "external/strace", "external/swiftshader", "external/tcpdump",
    "external/tinyalsa", "external/tinycompress", "external/tinyxml",
    "external/tinyxml2", "external/tremolo", "external/unicode",
    "external/v4l2_codec2", "external/vixl", "external/vulkan-headers",
    "external/wpa_supplicant_8", "external/yaffs2", "external/zipalign",
    "external/zlib", "external/zstd", "external/deqp",
    "external/chromium-webview", "external/cronet",
    "external/google-benchmark", "external/google-breakpad",
    "external/google-fonts", "external/libglib", "external/libgrpc",
    "external/libpcap", "external/libsensor",
    "pdk/", "platform_testing/libraries",
    "development/", "tools/",
]


# Chunk grouping (first match wins). Dependency-safety order: build system +
# toolchain prebuilts first (completeness sentinels + early-abort value),
# core system, sources, packages, device/vendor last.
# ---------------------------------------------------------------------------
def rule_(*prefixes):
    exps = []
    for p in prefixes:
        exps.append(re.compile("^" + re.escape(p) + r"(/|$)"))
    def match(path):
        return any(e.match(path) for e in exps)
    return match


RULES = [
    ("01-build",      rule_("build")),
    ("02-toolchain",  rule_("prebuilts/clang", "prebuilts/gcc", "prebuilts/go",
                            "prebuilts/jdk", "prebuilts/build-tools",
                            "prebuilts/cmake", "prebuilts/asuite",
                            "prebuilts/rust", "prebuilts/python",
                            "prebuilts/runtime", "prebuilts/tools",
                            "prebuilts/misc")),
    ("03-sdk-ndk",    rule_("prebuilts/ndk", "prebuilts/sdk", "prebuilts/module-sdk")),
    ("04-prebuilts",  rule_("prebuilts")),
    ("05-core",       rule_("art", "bionic", "dalvik", "libcore",
                            "libnativehelper", "system", "hardware",
                            "kernel", "bootable", "toolchain")),
    ("06-external-a", None),   # external/ a..m
    ("07-external-b", None),   # external/ n..z
    ("08-frameworks", rule_("frameworks", "test")),
    ("09-packages",   rule_("packages", "development", "developers",
                            "tools", "sdk", "lineage-sdk", "platform_testing")),
    ("10-device",     None),   # catch-all LAST: device/, vendor/, stragglers
]


def external_group(path):
    rest = path[len("external/"):]
    return "06-external-a" if rest[:1].lower() <= "m" else "07-external-b"


def load_manifest_dir(manifest_dir):
    """Parse default.xml, following <include> elements. Returns
    (remotes, default_el, projects[path] = element)."""
    root_file = os.path.join(manifest_dir, "default.xml")
    if not os.path.isfile(root_file):
        sys.exit("FATAL: %s not found (manifest fetch failed?)" % root_file)

    remotes = OrderedDict()          # name -> element
    default_el = None
    projects = OrderedDict()         # path -> element
    extras = []                      # other top-level elements (contactinfo...)
    seen = set()

    def load(apath, base):
        nonlocal default_el
        if apath in seen:
            return
        seen.add(apath)
        root = ET.parse(apath).getroot()
        for el in root:
            if el.tag == "include":
                inc = el.get("name")
                load(os.path.normpath(os.path.join(base, inc)),
                     os.path.dirname(os.path.join(base, inc)))
            elif el.tag == "remote":
                remotes[el.get("name")] = copy.deepcopy(el)
            elif el.tag == "default":
                default_el = copy.deepcopy(el)
            elif el.tag == "project":
                p = el.get("path") or el.get("name")
                projects[p] = copy.deepcopy(el)
            elif el.tag == "remove-project":
                projects.pop(el.get("name"), None)
                projects.pop(el.get("path"), None)
            else:
                extras.append(copy.deepcopy(el))

    load(root_file, manifest_dir)
    return remotes, default_el, projects, extras


def rewrite_fetch(fetch):
    if fetch is None:
        return None
    if GOOGLESOURCE in fetch:
        return "__MIRROR__"          # substituted later
    if fetch.strip() == "..":
        return "https://github.com/"  # relative-to-manifest-url -> absolute
    return fetch


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest-dir", required=True,
                    help="fetched manifest working tree (contains default.xml)")
    ap.add_argument("--mirror", required=True,
                    help="full AOSP mirror base URL for googlesource remotes")
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()

    remotes, default_el, projects, extras = load_manifest_dir(args.manifest_dir)
    print("loaded manifest: %d remotes, %d projects" %
          (len(remotes), len(projects)))

    # --- trim ---------------------------------------------------------------
    trimmed = []
    for path in list(projects):
        if any(path == t or path.startswith(t) for t in TRIM_PREFIXES):
            trimmed.append(path)
            del projects[path]
    # projects in the notdefault group are darwin/unsupported-host prebuilts;
    # plain `repo sync` excludes them, and an explicit-path `repo sync <path>`
    # HARD-ERRORS on them ("project group must be enabled") -> drop them here
    # (run 4 lesson, rediscovered at chunk-02 of run 12)
    notdefault = []
    for path in list(projects):
        groups = projects[path].get("groups", "") or ""
        if "notdefault" in groups.split(","):
            notdefault.append(path)
            del projects[path]
    print("trimmed: %d projects, notdefault-grouped: %d -> %d remain" %
          (len(trimmed), len(notdefault), len(projects)))

    # whitelist DISABLED: the real eviction cause was container-runner migration,
    # not soong memory. Full trimmed tree works on ubuntu-24.04 (145G/16G VM).
    # kept, whitelist_dropped = [], []
    # for path in list(projects):
    #     if not any(path == k or path.startswith(k + "/") for k in KEEP_PREFIXES):
    #         whitelist_dropped.append(path)
    #         del projects[path]
    #     else:
    #         kept.append(path)
    # print("whitelist: %d dropped, %d remain (minimal keep-set)" %
    #       (len(whitelist_dropped), len(projects)))

    # --- remotes rewrite ------------------------------------------------------
    out_remotes = []
    for name, el in remotes.items():
        el = copy.deepcopy(el)
        new_fetch = rewrite_fetch(el.get("fetch"))
        if new_fetch == "__MIRROR__":
            el.set("fetch", args.mirror)
        elif new_fetch:
            el.set("fetch", new_fetch)
        out_remotes.append(el)

    # --- emit single-file manifest -------------------------------------------
    os.makedirs(args.out_dir, exist_ok=True)
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<manifest>"]
    lines.append("")
    lines.append("  <!-- GENERATED by scripts/gen_manifest.py, do not edit.")
    lines.append("       googlesource remotes rewritten to: %s" % args.mirror)
    lines.append("       %d trim projects dropped -->" % len(trimmed))
    lines.append("")
    for el in extras:
        lines.append("  " + ET.tostring(el, encoding="unicode").strip())
    for el in out_remotes:
        lines.append("  " + ET.tostring(el, encoding="unicode").strip())
    if default_el is not None:
        lines.append("  " + ET.tostring(default_el, encoding="unicode").strip())
    lines.append("")
    for path, el in projects.items():
        e = copy.deepcopy(el)
        if e.get("path") is None:
            e.set("path", e.get("name"))
        s = ET.tostring(e, encoding="unicode").strip()
        lines.append("  " + s.replace("\n", "\n  "))
    lines.append("</manifest>")
    manifest_path = os.path.join(args.out_dir, "default.xml")
    with open(manifest_path, "w", newline="\n") as f:
        f.write("\n".join(lines) + "\n")

    # validate what we just wrote actually parses and has no dangling remotes
    r2, d2, p2, _ = load_manifest_dir(args.out_dir)
    if len(p2) != len(projects):
        sys.exit("FATAL: emitted manifest re-parses to %d projects (expected %d)"
                 % (len(p2), len(projects)))
    if d2 is None or not d2.get("remote"):
        sys.exit("FATAL: emitted manifest has no <default remote=...> — every "
                 "project without an explicit remote would be rejected by repo")
    for p, el in p2.items():
        rem = el.get("remote") or d2.get("remote")
        if rem and rem not in r2:
            sys.exit("FATAL: project %s references unknown remote %s" % (p, rem))

    # --- chunk lists -----------------------------------------------------------
    chunks_dir = os.path.join(args.out_dir, "chunks")
    os.makedirs(chunks_dir, exist_ok=True)
    buckets = OrderedDict((g, []) for g, _ in RULES)
    for path in sorted(p2):
        placed = None
        for gname, matcher in RULES:
            if matcher and matcher(path):
                placed = gname
                break
        if placed is None and path.startswith("external/"):
            placed = external_group(path)
        buckets[placed or "10-device"].append(path)

    summary = []
    idx = 0
    for gname, paths in buckets.items():
        if not paths:
            continue
        idx += 1
        with open(os.path.join(chunks_dir, "%02d.tsv" % idx), "w",
                  newline="\n") as f:
            for p in paths:
                f.write("%s\t%s\n" % (p, p2[p].get("name")))
        summary.append("%02d %s: %d projects" % (idx, gname, len(paths)))
        print(summary[-1])
    with open(os.path.join(args.out_dir, "summary.txt"), "w", newline="\n") as f:
        f.write("\n".join(summary) + "\n")
    print("wrote %s and %d chunks" % (manifest_path, idx))


if __name__ == "__main__":
    main()
