# Crave integration plan — wire in 5 minutes when credentials arrive

## What Crave gives
Remote build slots (32+ cores, 250GB+ disk, quota managed by them) driven by a CLI
(`crave`) or their GitHub-Actions runner pool via the community project
`sounddrill31/crave_aosp_builder`.

## Steps once you have the credentials
1. `pip install crave-cli` (or their binary) → `crave auth login` with the token
   they issue → store token as GitHub secret `CRAVE_TOKEN` on techt3success/emu_research
2. Copy `sounddrill31/crave_aosp_builder`'s workflow template into
   `.github/workflows/build-crave.yml`, adjusted for our build:
   - clone: `repo init -u https://android.googlesource.com/platform/manifest -b android-14.0.0_r1`
     (their machines handle googlesource quota — they run the fetch from their IPs)
   - our verified trim: remove cts, test/vts, linaro devices (already proven list)
   - product: `lunch sdk_phone64_x86_64-userdebug` (A14 r1 = 2-part lunch)
   - build: `m` → collect out/target/product/*/ images
   - artifact/upload: back to the GitHub Release (same as build-aosp.yml collect step)
3. Push → their runner picks it up (cron or workflow_dispatch), ~30 min per build
4. Images appear in Releases exactly like the GHA path intended — FortressLite bundle
   then swaps its images/ folder

## Backup: direct SSH build (if we get a raw crave slot instead of the GA runner)
- `crave ssh` into the slot → run the same sync+lunch+m script (kept in
  D:\aosp_cloud_build\crave\remote_build.sh) → pull images back over scp

## Status
- [x] Plan written
- [ ] User posts CRAVE_ACCESS_REQUEST.md to their community
- [ ] Credentials received → execute steps above
