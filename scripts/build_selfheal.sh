#!/usr/bin/env bash
# build_selfheal.sh — run `m` with automatic stub-generation for undefined modules.
# Called from the workflow build step; expects cwd=$HOME/aosp and lunch done.
#
# Round loop:  m -> harvest "depends on undefined module" from out/error.log ->
#              generate empty stub defaults -> retry (up to ROUND_MAX).
# Slice loop (run-28 lesson): `m` runs under `timeout -s INT` in ~25-min
#   slices; after EVERY slice (and every failed round) persist_delta banks
#   new out/ objects to the release as a cs<GEN>-part- generation. Ninja is
#   resumable, so a hard eviction (exit 143 kills the whole step — a
#   post-build persist can NEVER fire) costs at most one slice instead of
#   the whole run's compile progress.
set -o pipefail

# shellcheck source=persist_cs.sh
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SCRIPT_DIR/persist_cs.sh"

ROUND_MAX=60
CS_SLICE="${CS_SLICE:-1500}"   # seconds per ninja slice (25 min)
STUBS="$HOME/aosp/fortress_stubs"
mkdir -p "$STUBS"
if [ ! -s "$STUBS/Android.bp" ]; then
  printf '// auto-generated stubs for release-config modules absent in Lineage\n' > "$STUBS/Android.bp"
fi

# one full build round: sliced m with a checkpoint after every slice.
# rc 0 = build complete; rc 124 handled inside (slice boundary); any other
# rc = real error, returned for the stub-harvest path.
run_round() {
  local RC=1
  : > build.log
  while true; do
    echo "=== ninja slice (up to $((CS_SLICE/60)) min) at $(date -u +%H:%M:%S) ==="
    # -s INT lets soong_ui/ninja shut down cleanly; -k 30 KILLs it if it
    # hangs past the interrupt (soong_ui occasionally ignores INT mid-rust)
    timeout -s INT -k 30 "$CS_SLICE" m -j2 >> build.log 2>&1
    RC=$?
    # bank whatever this slice produced — eviction insurance; failure to
    # upload must not kill the build (empty delta = cheap no-op)
    persist_delta || echo "WARN: checkpoint upload failed (continuing build)"
    if [ "$RC" -eq 0 ]; then
      return 0
    fi
    # 124 = clean slice timeout, 137 = post-INT KILL followup — both are
    # slice boundaries (ninja resumes from banked objects), NOT failures
    if [ "$RC" -eq 124 ] || [ "$RC" -eq 137 ]; then
      echo "slice boundary (rc=$RC) — ninja resumes from banked objects"
      continue
    fi
    return "$RC"
  done
}

BUILD_RC=1
round=1
while [ "$round" -le "$ROUND_MAX" ]; do
  echo "=== ninja round $round/${ROUND_MAX} ==="
  run_round
  BUILD_RC=$?
  if [ "$BUILD_RC" -eq 0 ]; then
    echo "=== BUILD COMPLETE (round $round) ==="
    break
  fi

  # harvest undefined-module names from soong's error log
  UNDEFS=$(grep -hoE 'depends on undefined module "[^"]+"' out/error.log 2>/dev/null \
           | sed 's/.*"\(.*\)"/\1/' | sort -u)
  if [ -z "$UNDEFS" ]; then
    echo "no undefined-module errors — real compile failure, stopping loop"
    tail -40 build.log
    break
  fi

  NEW=0
  while IFS= read -r M; do
    [ -z "$M" ] && continue
    grep -q "name: \"$M\"" "$STUBS/Android.bp" && continue
    case "$M" in
      *includes*|*headers*)
        # kernel glue consumers demand genrule providers ("is not a genrule")
        printf 'genrule { name: "%s", cmd: "echo stub > $(out)", out: ["%s.out"] }\n' "$M" "$M" >> "$STUBS/Android.bp"
        echo "stub added: genrule $M"
        NEW=1
        continue ;;
      *rust|*_rust)         KIND="rust_defaults" ;;
      *java*|*Test*|*tests*) KIND="java_defaults" ;;
      *)                    KIND="cc_defaults" ;;
    esac
    printf '%s { name: "%s" }\n' "$KIND" "$M" >> "$STUBS/Android.bp"
    echo "stub added: $KIND $M"
    NEW=1
  done <<< "$UNDEFS"

  # persist stubs across runs (runner VMs get evicted mid-parse)
  if [ "$NEW" -eq 1 ]; then
    cp "$STUBS/Android.bp" /tmp/fortress_stubs.bp
    gh release upload "$SNAP_TAG" /tmp/fortress_stubs.bp --clobber >/dev/null 2>&1 \
      && echo "stubs persisted to snapshot" || true
  fi

  if [ "$NEW" -eq 0 ]; then
    echo "no new stubs generated — stopping loop"
    tail -40 build.log
    break
  fi
  round=$((round+1))
done

echo "BUILD_RC=$BUILD_RC"
exit "$BUILD_RC"
