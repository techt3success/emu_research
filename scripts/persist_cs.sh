#!/usr/bin/env bash
# persist_cs.sh — incremental compile-state (out/) persistence for the relay.
#
# Run 28 lesson: a post-build persist block NEVER fires on hard SIGTERM
# (exit 143 eviction kills the whole step instantly — 0 cs-part assets
# banked after a 4-minute death). The only way compile progress survives
# random evictions is checkpointing DURING the build: ninja is resumable,
# so build_selfheal.sh slices `m` with timeouts and calls persist_delta
# between slices. Every eviction loses at most one slice (~25 min).
#
# Delta scheme: generations. Each persist uploads ONLY files modified
# since the previous persist (marker file = last cutoff time), as
# cs<GEN>-part-aa.. assets. Restore applies all generations in ascending
# order — tar extract overlays, later generations win. Marker time comes
# from the newest generation's release-asset upload timestamp, so a fresh
# runner restoring 3 generations does NOT re-upload the restored files.
#
# 60-second safety cutoff: files written in the last 60s are EXCLUDED
# from a delta (a clang .o truncated by a slice kill has a fresh mtime;
# persisting it would wedge every future link — the relay would fail
# forever on one corrupt archive). Those files simply rebuild next slice.
#
# Env required: SNAP_TAG, GH_REPO, GH_TOKEN (set by the workflow);
# cwd must be $HOME/aosp. Can be sourced (functions) or run directly
# (runs one persist_delta and exits).
set -uo pipefail

MARKER="$HOME/aosp/.cs_marker"

# newest cs-generation number on the release (0 if none)
_gen_max() {
  gh api "repos/${GH_REPO}/releases/tags/${SNAP_TAG}" \
      --jq '[.assets[].name | select(test("^cs[0-9]+-part-")) | capture("^cs(?<g>[0-9]+)-part-").g | tonumber] | if length == 0 then 0 else max end' \
    2>/dev/null || echo 0
}

# newest cs-part upload timestamp (empty if none) — server-authoritative
_gen_last_ts() {
  gh api "repos/${GH_REPO}/releases/tags/${SNAP_TAG}" \
      --jq '[.assets[] | select(test("^cs[0-9]+-part-")) | .updated_at] | if length == 0 then "" else max end' \
    2>/dev/null || echo ""
}

# set the marker so the first delta on a fresh runner only covers NEW
# writes: restore-created files (extraction mtime = now) must NOT be
# re-uploaded, so the marker back-dates to the last generation's upload
init_marker() {
  [ -e "$MARKER" ] && return 0
  local TS
  TS=$(_gen_last_ts)
  if [ -n "$TS" ]; then
    touch -d "$TS" "$MARKER" 2>/dev/null || touch "$MARKER"
  else
    touch "$MARKER"
  fi
  echo "persist_cs: marker initialized at $(stat -c %y "$MARKER" 2>/dev/null || echo now)"
}

# upload one delta generation; safe to call repeatedly
persist_delta() {
  init_marker
  local M Epoch CUTOFF GEN NFILES
  M=$(stat -c %Y "$MARKER" 2>/dev/null || date +%s)
  CUTOFF=$(( $(date +%s) - 60 ))   # skip last 60s (truncation guard)

  find out -type f -newermt "@$M" ! -newermt "@$CUTOFF" -print0 > /tmp/cs.list 2>/dev/null
  NFILES=$(tr -cd '\0' < /tmp/cs.list | wc -c)
  if [ "${NFILES:-0}" -eq 0 ]; then
    echo "persist_cs: no delta since marker — nothing to bank"
    touch -d "@$CUTOFF" "$MARKER" 2>/dev/null || true
    return 0
  fi

  GEN=$(( $(_gen_max) + 1 ))
  echo "persist_cs: banking generation cs${GEN} (${NFILES} files)..."
  tar -C "$HOME/aosp" --null -T /tmp/cs.list \
      --exclude='out/.module_paths' \
      --exclude='*.d' \
      -cf - 2>/dev/null \
    | zstd -T0 -3 \
    | split -b 1900M \
        --filter='f="$FILE"; cat >"$f" && gh release upload "$SNAP_TAG" "$f" --clobber; rc=$?; rm -f "$f"; exit $rc' \
        - "cs${GEN}-part-"
  local RC=${PIPESTATUS[0]}
  rm -f /tmp/cs.list
  if [ "$RC" -eq 0 ]; then
    touch -d "@$CUTOFF" "$MARKER"   # advance to cutoff, NOT now: files
    echo "persist_cs: generation cs${GEN} banked"  # written after cutoff stay
  else                                               # eligible for the next delta
    echo "persist_cs: WARN generation cs${GEN} upload rc=$RC (delta lost, marker NOT advanced)"
  fi
  return "$RC"
}

# direct execution: one delta pass
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  cd "$HOME/aosp" || exit 1
  persist_delta
  exit $?
fi
