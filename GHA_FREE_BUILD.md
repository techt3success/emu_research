# GHA FREE BUILD — from-scratch Fortress-Lite image (runs 9+)

Status: **ACTIVE — mirror rework landed, run 9+ iterating.**
This replaces the goal.md Frontier conclusion ("GHA free runner permanently
infeasible, run 8") with a working design: the two walls had engineering
fixes that were never tried.

## WALL 1 — googlesource quota (RESOURCE_EXHAUSTED, runs 5/6/7/8)

### Mirror verification (2026-11-01, evidence collected before wiring)

Checked every candidate org for a **GitHub-hosted full AOSP-15**:

| Candidate | Verdict | Evidence |
|---|---|---|
| LineageOS `android` @ lineage-22.1/22.2 | **chosen tree, but NOT a full mirror** | manifest fetched: `raw.githubusercontent.com/LineageOS/android/lineage-22.2/default.xml` (branch `lineage-22.x` does not exist; real branches are 22.1/22.2). 969 projects in default.xml: **841 point at `android.googlesource.com`** (remote `aosp`, pinned `refs/tags/android-15.0.0_r32`), only 128 at `github.com/LineageOS`. Same shape for crDroid `15.0` (crdroidandroid) and PixelOS `manifest@fifteen` (default remote = aosp). |
| aosp-mirror (GitHub) | partial | read-only mirror of ~80 popular repos only; `platform_manifest` archived Nov 2023 ([android.github.io](https://android.github.io)) |
| aosp-riscv (GitHub) | partial | 67 repos (RISC-V port subset) |
| PixelOS-AOSP / crdroidandroid | mixed remotes | verified manifests: still googlesource for the bulk |
| **Tsinghua TUNA** | **full AOSP mirror, no IP quota** | `https://mirrors.tuna.tsinghua.edu.cn/git/AOSP/<project>` — `git ls-remote` on `platform/external/boringssl`, `platform/prebuilts/jdk/jdk21`, `platform/prebuilts/clang/host/linux-x86` all resolve, **including the exact pinned tag `refs/tags/android-15.0.0_r32`**. No-`.git`-suffix URLs work (repo builds `fetch + name` without appending `.git`). Throughput probe (shallow partial clone of frameworks/base): 153MB / 44s ≈ 3.5MB/s from host; on-runner probe picks the fastest mirror. |
| USTC | full mirror, fallback | `https://mirrors.ustc.edu.cn/aosp/<project>` — ls-remote OK (same HEAD hash as TUNA = consistent mirror) |

**Final choice**: LineageOS `lineage-22.2` tree (its `device/generic/goldfish`
@ lineage-22.2 ships the emulator products we build — verified
`AndroidProducts.mk` lists `sdk_phone64_x86_64.mk` et al; the tree is the
AOSP-15 base with 128 ROM repos on GitHub) with **every googlesource remote
rewritten to TUNA/USTC** in a generated single-file manifest.

### Why a generated manifest instead of local_manifests

`git-repo`'s `_ParseManifest` **raises** `ManifestParseError("remote ... already
exists with different attributes")` when a local_manifest redefines an existing
remote (verified in repo source `manifest_xml.py`, `_ParseManifest`). So
`scripts/gen_manifest.py` inlines `default.xml` + `snippets/{lineage,pixel}.xml`,
rewrites remotes (`..` → `https://github.com/`, googlesource → mirror), applies
the trim, and emits one `default.xml` into a local git repo that `repo init -u`
consumes. A runtime guard aborts if the effective manifest still has a
googlesource `fetch=` URL.

### Emulator product

LineageOS does not ship `lineage_*` emulator products (no gphone entries in
`vendor/lineage/products`); the goldfish tree keeps the plain AOSP products.
Lunch is resolved at runtime, first success wins:
`sdk_phone64_x86_64-trunk_staging-userdebug` → `sdk_phone64_x86_64-userdebug`
→ `sdk_phone64_x86_64-trunk_staging-eng`.

## WALL 2 — disk peak (~130-147GB vs ~121GB free)

`scripts/chunked_sync.sh`: sync ~10 chunks (generated from the manifest by
`gen_manifest.py`, dependency-safe order — build + toolchain prebuilts first,
`device/`/`vendor/` last). After each chunk's checkout is **verified present**,
that chunk's `.repo/projects/<path>.git`, `.repo/worktrees/<path>` and
`.repo/project-objects/<name>.git` are deleted (the build never needs git
metadata; re-sync capability is sacrificed by design). Per chunk: df guard
(abort if <26GB free), 3 retry attempts, `CHUNK i/n done: <GB> free` progress
lines so a failing run's log pinpoints the chunk. Peak = tree + one chunk's
store + out/ ≈ 95-110GB.

Chunk plan (dry-run validated against the real manifest, 1144 → 1098 projects
after trim): 01-build(7) 02-toolchain(19) 03-sdk-ndk(2) 04-prebuilts(50)
05-core(189) 06-external-a..m(325) 07-external-b..z(219) 08-frameworks+test(45)
09-packages(195) 10-device+vendor(47).

Trim (46 projects): verified core — `cts`, `test/vts`, `device/linaro/*`;
documented extension (nothing in an x86_64 goldfish build references them) —
cuttlefish family, google_car, trout, atv, pixel SoC dirs, `device/qcom`,
`vendor/qcom`, `vendor/codeaurora`. Removes are applied by dropping elements
while generating, so a stale entry can never break sync (the run-3 failure
mode).

## Persistence — release-asset snapshot, NOT actions/cache

`actions/cache` is capped at **10GB per repository**; a ~65GB tree can never be
cached (runs 1-8 never hit this because sync never completed). Instead, after a
successful sync the tree (git stores already stripped) is `tar | zstd | split`
into ~2GB parts and **stream-uploaded as assets of the rolling `src-snap-v1`
release** (GNU `split --filter` uploads each part as it is produced, so parts
never sit on disk). Later runs stream-restore: `gh api` each part in order
piped into `zstd -dc | tar -x` — no extra disk. Integrity via `snap.meta`
(part list) + zstd EOF checks; any failure falls back to a fresh chunked sync.

## Attempt log

| Run | What | Result / cause |
|---|---|---|
| 1-8 | googlesource `android-15.0.0_r1` direct sync | quota (RESOURCE_EXHAUSTED), disk peaks, group-filter and trim-manifest breakage — full history in goal.md Frontier |
| 9 | mirror rework: LineageOS 22.2 + TUNA rewrite, chunked sync w/ per-chunk store deletion, release-asset snapshot | *pending — filled in below as attempts land* |

## Success criterion

A run reaches "Build emulator product" and prints
`=== NINJA COMPILATION STARTED` (grep for `ninja: Entering directory` in
build.log). Compile (~4h on 4 cores) may span into the next run via the
snapshot; image release may lag one run behind the sync that fed it.
