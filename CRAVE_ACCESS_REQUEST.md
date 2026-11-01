# Crave.io access request — READY TO POST

## Where to post
Join the Crave community (Discord/Telegram linked from https://crave.io) and post the
text below in their access-request / new-member channel. Self-signup is disabled;
ROM maintainers and custom-build projects get free slots.

## The text (copy-paste)

Hi — I'm building a custom Android system image (AOSP 14, product
`sdk_phone64_x86_64-userdebug`, Android-x86-class emulator target) and need a build
slot. GitHub free runners can't host it (quota + 121GB disk vs ~130GB peak).

- Source: AOSP manifest `android-14.0.0_r1`, partial shallow clone, group-filtered
  (-darwin,-windows), synced via `repo` — standard flow, your infra handles it natively
- Product: emulator image (sdk_phone64_x86_64) for a security-testing emulator rig
  (personal research project — mobile app security testing / anti-detection research)
- Cadence: a few builds per week; each build is one `m` invocation, images only
- I can share the full build scripts + manifest if useful

Could I get a crave build slot? Thanks!

## After you're approved
1. You'll get a Crave username + key (or their `crave` CLI token)
2. Tell me the credentials → I wire up the crave_aosp_builder GitHub Action in
   D:\aosp_cloud_build (already researched the flow) and push → builds run on their
   32-core machines in ~30 min each, images land in the repo's Releases

## Alternative (if Crave is slow to respond)
Hetzner cloud CPX41/CCX23-class VM by the hour (~€0.05-0.15/hr; a full build ≈ €0.50-2
total) — you create the account, I drive everything over SSH.
