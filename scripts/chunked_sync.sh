#!/usr/bin/env bash
# chunked_sync.sh — free-runner AOSP sync with BOTH walls fixed:
#
#  WALL 1 (googlesource quota): `repo init` is pointed at a GENERATED
#    single-file manifest (scripts/gen_manifest.py) that rewrites every
#    android.googlesource.com remote to a full AOSP mirror (Tsinghua TUNA,
#    USTC fallback — picked by an on-runner probe) and keeps the ROM-org
#    projects on GitHub. No googlesource traffic at all.
#
#  WALL 2 (disk peak 130-147GB vs ~121GB free): sync in ~10 chunks
#    (build+toolchain prebuilts first, device/vendor last). After each
#    chunk's checkout is VERIFIED to exist, that chunk's git stores are
#    deleted (.repo/projects/<path>.git + .repo/project-objects/<name>.git);
#    the build never needs git metadata. Peak = tree + one chunk's store.
#
# Resumability: sentinels + per-chunk markers under .repo/.chunks; a run
# whose snapshot was restored skips straight to the build.
set -uo pipefail

AOSP_DIR="${AOSP_DIR:-$HOME/aosp}"
WS_DIR="${GITHUB_WORKSPACE:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
CHUNK_MARK_DIR="$AOSP_DIR/.repo/.chunks"
SYNC_JOBS="${SYNC_JOBS:-8}"
MIN_FREE_GB="${MIN_FREE_GB:-26}"
MANIFEST_SRC="${MANIFEST_SRC:-https://github.com/LineageOS/android}"
MANIFEST_BRANCH="${MANIFEST_BRANCH:-lineage-22.2}"
GEN_OUT="$HOME/manifest-mirror"
# Mirror probe order. Both are full AOSP mirrors (project names = AOSP paths,
# all googlesource tags mirrored). Verified reachable, no IP quota.
MIRRORS=(
  "https://mirrors.tuna.tsinghua.edu.cn/git/AOSP/"
  "https://mirrors.ustc.edu.cn/aosp/"
)

log()  { echo "[chunked-sync] $*"; }
die()  { echo "[chunked-sync] FATAL: $*" >&2; exit 1; }
free_gb() { df -BG / | awk 'NR==2 {gsub(/G/,"",$4); print $4}'; }

SENTINELS=(
  build/envsetup.sh
  prebuilts/go/linux-x86/bin/go
  prebuilts/clang/host/linux-x86/bin/clang
  frameworks/base/Android.bp
  device/generic/goldfish/AndroidProducts.mk
)

source_complete() {
  local p
  for p in "${SENTINELS[@]}"; do
    [ -e "$AOSP_DIR/$p" ] || return 1
  done
  return 0
}

pick_mirror() {
  local m t0 dt
  for m in "${MIRRORS[@]}"; do
    t0=$(date +%s)
    if timeout 25 git ls-remote "${m}platform/external/boringssl" HEAD >/dev/null 2>&1; then
      dt=$(( $(date +%s) - t0 ))
      log "mirror OK: $m (probe ${dt}s)"
      echo "$m"
      return 0
    fi
    log "mirror probe failed: $m"
  done
  return 1
}

# --- 0. already synced? (snapshot restored / previous run) -------------------
if source_complete; then
  log "source complete (all sentinels present) — skipping sync"
  echo "synced=false" >> "${GITHUB_OUTPUT:-/dev/null}"
  exit 0
fi
log "sentinels missing; sync required (disk now: $(free_gb)G free)"

# --- 1. generate rewritten manifest + chunk lists ----------------------------
MIRROR="${MIRROR:-$(pick_mirror)}" || die "no usable AOSP mirror found"
log "using mirror: $MIRROR"

rm -rf "$HOME/manifest-src" "$GEN_OUT"
git clone -q --depth=1 -b "$MANIFEST_BRANCH" "$MANIFEST_SRC" "$HOME/manifest-src" \
  || die "cloning manifest $MANIFEST_SRC failed"
python3 "$WS_DIR/scripts/gen_manifest.py" \
  --manifest-dir "$HOME/manifest-src" \
  --mirror "$MIRROR" \
  --out-dir "$GEN_OUT" || die "gen_manifest.py failed"

git init -q -b main "$GEN_OUT"
git -C "$GEN_OUT" add -A
git -C "$GEN_OUT" -c user.name=cloud-builder -c user.email=ci@local \
    commit -qm "generated manifest (mirror=$MIRROR)"
log "generated manifest: $(grep -c '<project' "$GEN_OUT/default.xml") projects, $(ls "$GEN_OUT/chunks"/*.tsv | wc -l) chunks"

# --- 2. repo init (local generated manifest; never touches googlesource) ------
mkdir -p "$AOSP_DIR"
if [ ! -d "$AOSP_DIR/.repo/manifests" ]; then
  log "repo init from generated local manifest"
  ( cd "$AOSP_DIR" && repo init -u "$GEN_OUT" -b main \
      --partial-clone --clone-filter=blob:limit=500k --depth=1 \
      --no-repo-verify --no-use-superproject ) > /tmp/repo-init.log 2>&1 \
    || { tail -20 /tmp/repo-init.log; die "repo init failed"; }
fi
# cross-check: no googlesource FETCH URL survived into the effective manifest
# (review=/review-host attributes may legitimately still mention googlesource)
if ( cd "$AOSP_DIR" && repo manifest ) 2>/dev/null | grep -qE 'fetch="[^"]*android\.googlesource\.com'; then
  die "effective manifest still fetches from android.googlesource.com"
fi
log "effective manifest is googlesource-free"
mkdir -p "$CHUNK_MARK_DIR"

# --- 3. chunked sync + per-chunk git-store deletion ---------------------------
CHUNKS=("$GEN_OUT"/chunks/*.tsv)
NCHUNKS=${#CHUNKS[@]}
i=0
for tsv in "${CHUNKS[@]}"; do
  i=$((i+1))
  id=$(basename "$tsv" .tsv)
  marker="$CHUNK_MARK_DIR/$id.done"
  first_path=$(head -1 "$tsv" | cut -f1)
  last_path=$(tail -1 "$tsv" | cut -f1)

  if [ -f "$marker" ] && [ -d "$AOSP_DIR/$first_path" ] && [ -d "$AOSP_DIR/$last_path" ]; then
    log "CHUNK $i/$NCHUNKS ($id): marker + worktrees verified, skipping"
    continue
  fi

  FREE=$(free_gb)
  if [ "$FREE" -lt "$MIN_FREE_GB" ]; then
    die "DISK BUDGET EXHAUSTED before chunk $i/$NCHUNKS: ${FREE}G free < ${MIN_FREE_GB}G required. Sync cannot continue safely."
  fi

  mapfile -t PROJECTS < <(cut -f1 "$tsv")
  ok=0
  for attempt in 1 2 3; do
    log "CHUNK $i/$NCHUNKS ($id, ${#PROJECTS[@]} projects): sync attempt $attempt (disk ${FREE}G free)"
    if ( cd "$AOSP_DIR" && repo sync -c --fail-fast -j"$SYNC_JOBS" "${PROJECTS[@]}" ) \
         > "$AOSP_DIR/sync-chunk-$id.log" 2>&1; then
      ok=1; break
    fi
    log "chunk $id attempt $attempt failed:"
    grep -m5 -E "error:|fatal:|failed" "$AOSP_DIR/sync-chunk-$id.log" | head -5 || tail -5 "$AOSP_DIR/sync-chunk-$id.log"
    [ "$attempt" -lt 3 ] && { log "backing off 60s"; sleep 60; }
  done
  [ "$ok" = 1 ] || { tail -30 "$AOSP_DIR/sync-chunk-$id.log"; die "chunk $id failed after 3 attempts"; }

  # verify EVERY project's worktree exists before deleting its git store
  missing=0
  for p in "${PROJECTS[@]}"; do
    if [ ! -d "$AOSP_DIR/$p" ]; then
      log "MISSING worktree after sync: $p"; missing=$((missing+1))
    fi
  done
  [ "$missing" = 0 ] || die "$missing worktrees missing in chunk $id — refusing to delete git stores"

  # delete this chunk's git stores (future re-sync sacrificed; build is fine)
  deleted=0
  while IFS=$'\t' read -r p name; do
    if [ -d "$AOSP_DIR/$p" ]; then
      rm -rf "${AOSP_DIR:?}/.repo/projects/${p}.git" \
             "${AOSP_DIR:?}/.repo/worktrees/${p}" \
             "${AOSP_DIR:?}/.repo/project-objects/${name}.git"
      deleted=$((deleted+1))
    fi
  done < "$tsv"
  touch "$marker"
  log "CHUNK $i/$NCHUNKS done: $id — $deleted git stores deleted, $(free_gb)G free, tree $(du -sh --exclude=.repo "$AOSP_DIR" 2>/dev/null | cut -f1)"
done

# --- 4. final completeness gate ----------------------------------------------
for p in "${SENTINELS[@]}"; do
  [ -e "$AOSP_DIR/$p" ] || die "sync finished but sentinel missing: $p"
done
touch "$CHUNK_MARK_DIR/.all_done"
log "ALL CHUNKS DONE: $(free_gb)G free, tree $(du -sh --exclude=.repo "$AOSP_DIR" 2>/dev/null | cut -f1), .repo $(du -sh "$AOSP_DIR/.repo" 2>/dev/null | cut -f1)"
echo "synced=true" >> "${GITHUB_OUTPUT:-/dev/null}"
