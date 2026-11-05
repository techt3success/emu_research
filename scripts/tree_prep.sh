#!/usr/bin/env bash
# tree_prep.sh — the COMPLETE tree-preparation sequence, shared by the GHA
# workflow and the Kaggle kernel (single source of truth — the Kaggle v6 run
# reached soong and died on qcom namespaces because this lived inline in the
# workflow only).
#
# Order matters: removals -> definer clones -> definer-bulk removals ->
# radio strip (keep VINTF xmls) -> kernel-glue sed -> cuttlefish slim ->
# key-attestation visibility overlay. repair_defaults.py runs AFTER this
# (caller's job) — post-removal state is what it expects to see.
#
# Env: TREE (default $HOME/aosp), SCRIPTS_DIR (dir containing
# overlay_keyattestation_Android.bp; default: this script's dir).
set -o pipefail
TREE="${TREE:-$HOME/aosp}"
SCRIPTS_DIR="${SCRIPTS_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
cd "$TREE" || exit 1

# soong scans ALL Android.bp: drop phone-SoC + known-broken test dirs
# (kaggle-v10 lesson: trusty-host_package references assemble_cvd as a host
# tool provider — assemble_cvd is deleted by the cuttlefish slim below;
# emulator product does not need trusty TEE host tools)
rm -rf hardware/qcom-caf hardware/qcom platform_testing/tools/automotive platform_testing/tests/automotive packages/apps/DeviceDiagnostics/TradeInModeTests build/bazel/examples system/netd/tests external/ltp external/openwrt-prebuilts platform/cts/tests/signature platform/cts/apps platform/cts/tests platform/cts/hostsidetests platform/cts/tools device/generic/trusty
rm -rf platform_testing_sts

# robust definer clones (cts/hal/vts/vndk usually pre-extracted from defs tar)
clone_definer() {
  local dir="$1"; shift
  [ -d "$dir" ] && return 0
  local attempt src
  for attempt in 1 2 3; do
    for src in "$@"; do
      if git clone -q --depth=1 $src "$dir" 2>/dev/null; then
        echo "cloned definer: $dir (attempt $attempt)"; return 0
      fi
      rm -rf "$dir"; sleep 10
    done
  done
  echo "WARN: all clone attempts failed for $dir"; return 1
}
clone_definer platform/cts             "-b lineage-22.2 https://github.com/LineageOS/android_cts"             "-b android-15.0.0_r1 https://mirrors.tuna.tsinghua.edu.cn/git/AOSP/platform/cts"             "-b android-15.0.0_r1 https://android.googlesource.com/platform/cts"
clone_definer test/vts-testcase/hal    "-b android-15.0.0_r1 https://mirrors.tuna.tsinghua.edu.cn/git/AOSP/platform/test/vts-testcase/hal"             "-b android-15.0.0_r1 https://android.googlesource.com/platform/test/vts-testcase/hal"
clone_definer test/vts                 "-b android-15.0.0_r1 https://mirrors.tuna.tsinghua.edu.cn/git/AOSP/platform/test/vts"             "-b android-15.0.0_r1 https://android.googlesource.com/platform/test/vts"
clone_definer test/vts-testcase/vndk   "-b android-15.0.0_r1 https://mirrors.tuna.tsinghua.edu.cn/git/AOSP/platform/test/vts-testcase/vndk"             "-b android-15.0.0_r1 https://android.googlesource.com/platform/test/vts-testcase/vndk"
clone_definer device/google/cuttlefish "-b android-15.0.0_r1 https://mirrors.tuna.tsinghua.edu.cn/git/AOSP/device/google/cuttlefish"             "-b android-15.0.0_r1 https://android.googlesource.com/device/google/cuttlefish"
# cuttlefish r1 apex dirs carry props unknown to 22.2 soong
rm -rf device/google/cuttlefish/system_image device/google/cuttlefish/guest/hals/vehicle/apex device/google/cuttlefish/tests
# cloned definer repos: strip their BULK test subtrees (soong scans
# every Android.bp — thousands of test modules blow up soong_ui's GO-heap).
# Keep libs/ that define the defaults we need.
rm -rf test/vts-testcase/hal/treble platform/cts/tests platform/cts/apps platform/cts/hostsidetests platform/cts/tools 2>/dev/null
find platform/cts -maxdepth 1 -type d -name "tests*" -exec rm -rf {} + 2>/dev/null
# strip the radio/telephony stack from the product: the goldfish ril
# pulls multi-version aidl conflicts (radio-V3/V4) that cannot be stubbed.
# Emulator runs without SIM — acceptable device state.
# radio stack removed but VINTF manifest fragments are product-required:
# delete only build files, keep manifest xmls
find device/generic/goldfish/radio device/generic/goldfish/phone -name "Android.bp" -delete 2>/dev/null
rm -rf device/generic/goldfish/phone device/google/cuttlefish/guest/hals/ril
find device/generic/goldfish -name "*.mk" -exec sed -i -E "/(rild|libgoldfish-rild|libril-modem-lib|libreference-ril|modem_simulator)/d" {} + 2>/dev/null
# neutralize Lineage kernel-build glue vars (undefined in sdk_phone builds)
sed -i 's/\$(PATH_OVERRIDE_SOONG)//g; s/\$(KERNEL_BUILD_OUT_PREFIX)//g' vendor/lineage/build/soong/Android.bp 2>/dev/null || true
# slim cuttlefish to ONLY what the emulator product needs:
# wpa/hostapd driver libs + reference ril (everything else is
# cuttlefish-infra that drags recovery/vehicle/graphics deps)
CF_KEEP="guest/hals/wpa_supplicant guest/hals/hostapd guest/libs/wpa_supplicant_8_lib"
rm -rf /tmp/cf_keep && mkdir -p /tmp/cf_keep
for k in $CF_KEEP; do
  [ -d "device/google/cuttlefish/$k" ] && { mkdir -p "/tmp/cf_keep/$(dirname $k)"; cp -r "device/google/cuttlefish/$k" "/tmp/cf_keep/$k"; }
done
rm -rf device/google/cuttlefish
mkdir -p device/google/cuttlefish
for k in $CF_KEEP; do
  [ -d "/tmp/cf_keep/$k" ] && { mkdir -p "device/google/cuttlefish/$(dirname $k)"; cp -r "/tmp/cf_keep/$k" "device/google/cuttlefish/$k"; }
done
# deterministic visibility fix: overwrite with pre-stripped overlay
if [ -f "$SCRIPTS_DIR/overlay_keyattestation_Android.bp" ] && [ -d external/android-key-attestation ]; then
  cp "$SCRIPTS_DIR/overlay_keyattestation_Android.bp" external/android-key-attestation/Android.bp
fi
echo "tree_prep done: $(du -sh --exclude=.repo . 2>/dev/null | cut -f1) tree"
