# emu_research — AOSP-from-source emulator image builder

Builds an Android 15 emulator system image **from AOSP source** (no prebuilt SDK
system image) on GitHub Actions' free runner, and publishes the images to Releases.

- Manifest: `android-15.0.0_r1`, partial shallow clone, darwin/windows prebuilts excluded
- Product: `sdk_phone64_x86_64-userdebug` (the AVD/emulator product)
- Output: `system.img`, `vendor.img`, `product.img`, `system_ext.img`, `ramdisk.img`,
  `boot.img` … published as gzipped Release assets
- Source is cached between runs so a timed-out build resumes without re-syncing

## Why
The Fortress-Lite stealth emulator project needs an image where the build fingerprint,
props, and package set are controlled at build time (user build, real-Pixel identity,
no goldfish metadata baked in). This repo is the from-scratch image supply line.

## Trigger
Every push that touches the workflow or a `TRIGGER` file starts a new build
(~4.5–6 h). Results appear under **Releases**.
