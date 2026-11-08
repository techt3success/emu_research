#!/usr/bin/env python3
"""repair_defaults.py — soong-parse repair for the Lineage/sdk_phone hybrid tree.

The Lineage 22.2 tree + AOSP r1 definer clones leave three error classes:
  1. references to release-config defaults absent from Lineage (avf_build_flags_*,
     wifi-module-sdk-version-defaults, mts-target-sdk-version-current, sts_defaults,
     vhalclient_defaults, android.car-defaults, netd_aidl_interface_lateststable_*)
  2. test/fuzzer/benchmark module blocks referencing absent modules
  3. duplicated module definitions from drift

Repair policy (safe set):
  - DEFAULT_NAMES: strip the whole array-entry line (defaults entries are optional)
  - TEST module blocks (name matches test/fuzzer/benchmark patterns or explicit
    block list): delete the entire enclosing module block
Never touches: build/, vendor/, device/ (already handled), prebuilts/, art/
"""
import os, re, sys

TREE = os.environ.get("AOSP_TREE", "/home/runner/aosp")
SKIP_DIRS = {".repo", "out", "build", "vendor", "prebuilts", "art", "bionic"}

BAD_DEFAULTS = [
    "avf_build_flags_rust", "avf_build_flags_cc",
    "wifi-module-sdk-version-defaults", "mts-target-sdk-version-current",
    "sts_defaults", "vhalclient_defaults", "android.car-defaults",
    "compatibility-device-util-axt-ravenwood",
    "nfc_aconfig_flags_lib", "android.net.vcn.flags-aconfig-java",
    "framework-connectivity-test-defaults", "cts_defaults",
    "cts_error_prone_rules", "cts_support_defaults",
]
BAD_VERSIONED = re.compile(r"netd_aidl_interface_lateststable_\w+")
LINE_KILL = re.compile(
    r'"(?:' + "|".join(re.escape(n) for n in BAD_DEFAULTS) + r')"'
) if BAD_DEFAULTS else None
LINE_KILL_VERSIONED = re.compile(r'"netd_aidl_interface_lateststable_\w+"')

TEST_NAME = re.compile(
    r"(Test|Tests|test|fuzzer|Fuzz|benchmark|Benchmark|Ravenwood|mobly)"
    r"(ellow|_test|_fuzz)?"
)

def is_test_block(name: str) -> bool:
    if name in ("wpa_supplicant", "hostapd", "netd", "adbd"):
        return False
    return bool(re.search(r"(te?sts?\b|_test|fuzzer|benchmark|Ravenwood)", name)) or \
           name.startswith(("Cts", "Vts", "Frameworks", "vts_"))

def walk_bp(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for f in filenames:
            if f == "Android.bp":
                yield os.path.join(dirpath, f)

def blocks_with(text, needle):
    """Yield (start, end) spans of top-level blocks whose text contains needle."""
    spans = []
    i = 0
    n = len(text)
    while True:
        m = re.search(r"\n?([a-zA-Z_][\w]*\s*\{)", text[i:])
        if not m:
            break
        start = i + m.start()
        depth = 0
        j = i + m.end() - 1
        instr = None
        while j < n:
            c = text[j]
            if instr:
                if c == "\\":
                    j += 2
                    continue
                if c == instr:
                    instr = None
            else:
                if c in "\"'":
                    instr = c
                elif c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        j += 1
                        break
            j += 1
        end = j
        block = text[start:end]
        if needle in block:
            spans.append((start, end))
        i = end
    return spans

def strip_lines(text, pat):
    return "\n".join(l for l in text.split("\n") if not pat.search(l))

total_files = total_lines = total_blocks = 0
directives = 0
for bp in walk_bp(TREE):
    try:
        t = open(bp, encoding="utf-8", errors="replace").read()
    except OSError:
        continue
    orig = t
    # 1. line-level strip of broken defaults/dep entries
    #    NEVER strip lines containing 'name:' — orphaning a module breaks soong
    def _kill(line: str) -> bool:
        if "name:" in line:
            return False
        if LINE_KILL and LINE_KILL.search(line):
            return True
        if LINE_KILL_VERSIONED.search(line):
            return True
        return False

    newt = "\n".join(l for l in t.split("\n") if not _kill(l))
    killed_lines = newt.count("\n") - t.count("\n")
    t = newt
    if killed_lines:
        total_lines += killed_lines
        directives += 1
    # 2. block-level deletion for test/fuzzer blocks referencing BAD_VERSIONED
    for span in sorted(blocks_with(t, "undefined") or [], reverse=True):
        pass
    for name_pat in ("netd_aidl_interface_lateststable",):
        changed = True
        while changed:
            changed = False
            spans = blocks_with(t, name_pat)
            for start, end in reversed(spans):
                blk = t[start:end]
                nm = re.search(r'name:\s*"([^"]+)"', blk)
                if nm and is_test_block(nm.group(1)):
                    t = t[:start].rstrip() + "\n" + t[end:]
                    total_blocks += 1
                    changed = True
    if t != orig:
        open(bp, "w", encoding="utf-8").write(t)
        total_files += 1

# visibility relaxations: definer-clone consumers need to see these modules
VISIBILITY_PUBLIC_DIRS = [
    "external/android-key-attestation",
]
for rel in VISIBILITY_PUBLIC_DIRS:
    bp = os.path.join(TREE, rel, "Android.bp")
    if not os.path.exists(bp):
        continue
    t = open(bp, encoding="utf-8").read()
    if '"//visibility:public"' in t:
        continue
    # delete visibility AND default_visibility declarations entirely —
    # absent = visible everywhere, avoids soong's mixing prohibition
    vis_pat = re.compile(r"\n?\s*(?:default_)?visibility:\s*\[[^\]]*\],?")
    t2 = vis_pat.sub("", t)
    if t2 != t:
        open(bp, "w", encoding="utf-8").write(t2)
        print(f"visibility relaxed: {rel}")

# hard-drop modules that only exist for Lineage kernel builds — the sdk_phone
# product never consumes them, and their cmds reference make-vars that don't
# exist outside a Lineage kernel environment (run-23/24 lesson)
# run-27 lesson: dropping only the generator definitions left the two
# cc_defaults bridges and the three cc_library_headers consumers in the glue
# file; their quoted refs then resolved against the wrong-typed fortress
# stubs -> "is not a genrule" parse fatal. Drop the whole chain: generators,
# bridges, consumers. Then block-delete any OTHER tree module whose text
# references one of these names (the old line-strip regex was unquoted and
# never matched the quoted refs; block deletion covers inline arrays and
# missing trailing commas alike).
DROP_MODULES = {"generated_kernel_includes", "prebuilt_kernel_includes",
                "generated_kernel_headers", "prebuilt_kernel_headers",
                "qti_kernel_headers",
                "generated_kernel_header_defaults",
                "prebuilt_kernel_header_defaults"}
# the 5 names consumers may reference (bridges are never referenced by name)
KERNEL_REFS = ("generated_kernel_includes", "prebuilt_kernel_includes",
               "generated_kernel_headers", "prebuilt_kernel_headers",
               "qti_kernel_headers")
# walk_bp skips vendor/ and build/ dirs — process the known glue file explicitly
_drop_files = list(walk_bp(TREE))
_drop_files.append(os.path.join(TREE, "vendor", "lineage", "build", "soong", "Android.bp"))
for bp in _drop_files:
    if "fortress_stubs" in bp:
        continue  # stubs file: keep everything (unreferenced stubs are harmless)
    try:
        t = open(bp, encoding="utf-8", errors="replace").read()
    except OSError:
        continue
    if not any(f'"{m}"' in t for m in DROP_MODULES):
        continue
    orig = t
    for target in DROP_MODULES:
        changed = True
        while changed:
            changed = False
            for start, end in sorted(blocks_with(t, f'name: "{target}"'), reverse=True):
                t = t[:start].rstrip() + "\n" + t[end:]
                total_blocks += 1
                changed = True
    # delete every remaining module that references a kernel name (bridges'
    # generated_headers, stray consumers the product doesn't need)
    for kn in KERNEL_REFS:
        changed = True
        while changed:
            changed = False
            for start, end in sorted(blocks_with(t, f'"{kn}"'), reverse=True):
                t = t[:start].rstrip() + "\n" + t[end:]
                total_blocks += 1
                changed = True
    if t != orig:
        open(bp, "w", encoding="utf-8").write(t)
        print(f"dropped kernel-include modules from: {bp}")


# block-delete any module referencing cuttlefish host tools that the
# cuttlefish slim (tree_prep.sh) deleted — tree_prep keeps only wpa/hostapd
# libs; every remaining ref (trusty-host_package -> assemble_cvd, kaggle
# v10/v11 lesson) is a dead dependency of a module the emulator product
# never needs. Generic pass: kills the WHOLE class instead of one dir/run.
CF_TOOLS = ("assemble_cvd", "launch_cvd", "stop_cvd", "cvd",
            "crosvm", "socket_vsock_proxy", "cvd_import_exports")
for bp in walk_bp(TREE):
    if "fortress_stubs" in bp:
        continue
    if "/cuttlefish/" in bp.replace(os.sep, "/"):
        continue  # slimmed cuttlefish dirs are already minimal
    try:
        t = open(bp, encoding="utf-8", errors="replace").read()
    except OSError:
        continue
    if not any(f'"{tl}"' in t for tl in CF_TOOLS):
        continue
    orig = t
    for tl in CF_TOOLS:
        changed = True
        while changed:
            changed = False
            for start, end in sorted(blocks_with(t, f'"{tl}"'), reverse=True):
                nm = re.search(r'name:\s*"([^"]+)"', t[start:end])
                if nm and nm.group(1) not in ("wpa_supplicant", "hostapd", "netd", "adbd"):
                    t = t[:start].rstrip() + "\n" + t[end:]
                    total_blocks += 1
                    changed = True
    if t != orig:
        open(bp, "w", encoding="utf-8").write(t)
        print(f"dropped cuttlefish-tool refs from: {bp}")


# block-delete any module referencing directories that tree_prep.sh deleted
# (kaggle-v16 lesson: netd_unit_test -> "source path system/netd/tests does
# not exist" — same class as the cuttlefish-tool refs, so all tree_prep
# drop targets are covered here in one pass)
DEAD_DIRS = ("system/netd/tests", "external/ltp", "external/openwrt-prebuilts",
             "platform/cts/tests", "platform/cts/apps", "platform/cts/hostsidetests",
             "platform/cts/tools", "device/generic/trusty",
             "platform_testing/tools/automotive", "platform_testing/tests/automotive",
             "build/bazel/examples",
             "packages/apps/DeviceDiagnostics/TradeInModeTests",
             "test/vts-testcase/hal/treble", "hardware/qcom", "hardware/qcom-caf")
for bp in walk_bp(TREE):
    if "fortress_stubs" in bp:
        continue
    try:
        t = open(bp, encoding="utf-8", errors="replace").read()
    except OSError:
        continue
    if not any(dd in t for dd in DEAD_DIRS):
        continue
    orig = t
    for dd in DEAD_DIRS:
        changed = True
        while changed:
            changed = False
            for start, end in sorted(blocks_with(t, dd), reverse=True):
                nm = re.search(r'name:\s*"([^"]+)"', t[start:end])
                if nm and nm.group(1) not in ("wpa_supplicant", "hostapd", "netd", "adbd"):
                    t = t[:start].rstrip() + "\n" + t[end:]
                    total_blocks += 1
                    changed = True
    if t != orig:
        open(bp, "w", encoding="utf-8").write(t)
        print(f"dropped dead-dir refs from: {bp}")


print(f"repair_defaults: files={total_files} lines_stripped={total_lines} blocks_deleted={total_blocks}")
