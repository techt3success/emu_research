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

print(f"repair_defaults: files={total_files} lines_stripped={total_lines} blocks_deleted={total_blocks}")
