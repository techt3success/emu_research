#!/usr/bin/env python3
"""Kaggle AOSP build cell — restore snapshot from GHA release, run build.

Kaggle session specs (user-verified live 2026-11-04, df output):
  overlay /        : 8.0T total, 967G avail   -> 106G peak fits trivially
  /kaggle/working  : 20G quota (the only PERSISTED output)
  RAM 30G (parse peak 14.8G fits — no watchdog race), 4 cores, 12h CPU session
  Repo techt3success/emu_research is PUBLIC -> anonymous asset download,
  NO token goes into the notebook.

Usage: paste this whole file into ONE notebook cell (internet ON) and run.
Idempotent: finished stages are skipped via a marker, so "Run all" after a
kernel restart resumes at the right stage.

NOTE on Kaggle quotas: free tier = 30 GPU-hours/week but CPU sessions are
unmetered; still, a full build ~6-9h at 4 cores. Keep the tab alive.
"""

import json, os, subprocess, urllib.request

HOME = "/kaggle/tmp"          # scratch on the 967G overlay
AOSP = f"{HOME}/aosp"
REL  = "https://api.github.com/repos/techt3success/emu_research/releases/tags/src-snap-v2-minimal"
REPO = "https://github.com/techt3success/emu_research.git"
WS   = f"{HOME}/aosp_cloud_build"
MARK = f"{AOSP}/.kaggle_stage"
SLICE = 1500                  # 25-min ninja slices (same as CI persist_cs)
TARGET = "sdk_phone64_x86_64-trunk_staging-userdebug"


def run(cmd, timeout=None):
    print(f"$ {cmd[:140]}")
    return subprocess.run(["bash", "-c", cmd], timeout=timeout).returncode


def marker(stage):
    try:
        return open(MARK).read().strip() == stage
    except FileNotFoundError:
        return False


def set_marker(stage):
    open(MARK, "w").write(stage)


def stage_no():
    try:
        return int(open(MARK).read().strip().split("-")[0])
    except FileNotFoundError:
        return 0


os.makedirs(HOME, exist_ok=True)
stage = stage_no()
print(f"=== resuming at stage {stage} ===")

# ---------- stage 0: deps --------------------------------------------------
if stage < 1:
    rc = run("apt-get -y update >/dev/null && apt-get -y install -y "
             "repo git curl zstd openjdk-17-jdk python3 >/dev/null", timeout=900)
    assert rc == 0, "apt deps failed"
    set_marker("1-deps")

# ---------- stage 0b: pipeline scripts (public repo, no auth) --------------
if stage < 2:
    if not os.path.isdir(WS):
        assert run(f"git clone -q --depth=1 {REPO} {WS}") == 0
    assert os.path.isfile(f"{WS}/scripts/build_selfheal.sh")
    set_marker("2-scripts")

# ---------- stage 1: restore 84G snapshot from GHA release -----------------
if stage < 3:
    assets = json.load(urllib.request.urlopen(REL, timeout=30))["assets"]
    parts = sorted((a for a in assets
                    if a["name"].startswith("snap.tar.zst.part-")),
                   key=lambda a: a["name"])
    os.makedirs(f"{HOME}/snap", exist_ok=True)
    for a in parts:
        dst = f"{HOME}/snap/{a['name']}"
        if not os.path.exists(dst):
            run(f"curl -sL -o {dst}.dl '{a['browser_download_url']}' && mv {dst}.dl {dst}",
                timeout=3600)
    os.makedirs(AOSP, exist_ok=True)
    cat = " ".join(f"{HOME}/snap/{a['name']}" for a in parts)
    rc = run(f"cat {cat} | zstd -dc | tar -xf - -C {AOSP}", timeout=7200)
    assert rc == 0, "snapshot restore failed"
    for name, dest in [("definers.tar.zst", AOSP),
                       ("fortress_stubs.bp", f"{AOSP}/fortress_stubs")]:
        url = next((a["browser_download_url"] for a in assets if a["name"] == name), None)
        if not url:
            continue
        if name.endswith(".zst"):
            run(f"curl -sL '{url}' | zstd -dc | tar -xf - -C {AOSP}", timeout=1800)
        else:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            run(f"curl -sL '{url}' > {dest}/Android.bp")
    set_marker("3-restore")

# ---------- stage 2: repair + lunch ----------------------------------------
if stage < 4:
    run(f"cd {AOSP} && AOSP_TREE={AOSP} python3 {WS}/scripts/repair_defaults.py",
        timeout=1800)
    rc = run(f"cd {AOSP} && source build/envsetup.sh >/dev/null 2>&1 && "
             f"lunch {TARGET}", timeout=600)
    assert rc == 0, "lunch failed"
    set_marker("4-lunch")

# ---------- stage 3: soong parse + ninja ------------------------------------
# ONE bash shell: envsetup + lunch + selfheal (stub loop + 25-min slices).
# persist_cs checkpoints need gh auth which Kaggle does NOT have — selfheal
# treats upload failures as WARN and continues; ninja still resumes across
# slices inside the session. GOMEMLIMIT unnecessary here (30G RAM) but the
# Go knob is harmless headroom; SOONG_UI_PARALLELISM=2 keeps the peak sane.
if stage < 5:
    env = (f"export GH_REPO=techt3success/emu_research SNAP_TAG=src-snap-v2-minimal "
           f"GH_TOKEN=absent CS_SLICE={SLICE} SOONG_UI_PARALLELISM=2 "
           f"GOMEMLIMIT=12GiB GOGC=50; ")
    rc = run(f"cd {AOSP} && source build/envsetup.sh >/dev/null 2>&1 && "
             f"lunch {TARGET} >/dev/null 2>&1 && {env} "
             f"bash {WS}/scripts/build_selfheal.sh", timeout=11 * 3600)
    if rc == 0:
        set_marker("5-build")
    else:
        print(f"selfheal rc={rc} — see {AOSP}/build.log + out/error.log")
        run(f"tail -60 {AOSP}/build.log; tail -30 {AOSP}/out/error.log 2>/dev/null")
        raise SystemExit(f"BUILD FAILED rc={rc} — fix, push to repo, re-run cell")

# ---------- stage 4: ship images into the 20G working quota -----------------
run(f"mkdir -p /kaggle/working/images && "
    f"cp {AOSP}/out/target/product/*/*.img /kaggle/working/images/ 2>/dev/null; "
    f"du -sh /kaggle/working/images; ls -la /kaggle/working/images/")
print("=== DONE — click Save Version to persist /kaggle/working/images/ ===")
